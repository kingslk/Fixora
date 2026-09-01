from __future__ import annotations

import base64
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from agents import Agent, ModelSettings, RunConfig, Runner, function_tool, set_tracing_disabled
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError
from agents.items import ItemHelpers, MessageOutputItem
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI
from openai.types.shared import Reasoning
from pydantic import BaseModel, Field

from ..config import get_settings
from ..repo.cache import RepositoryCache, normalize_repo_path
from .workspace import VirtualWorkspace

# locate / patch / 验证失败重试各自新开会话，不再裁剪旧工具输出。
# 超限直接失败，禁止截掉证据后继续猜。
LOCATE_MAX_TURNS = 16
PATCH_MAX_TURNS = 20


class AgentSummary(BaseModel):
    title: str = Field(max_length=120)
    root_cause: str
    summary: str


class LocatePlanItem(BaseModel):
    path: str
    change: str = ""


class LocateSnippet(BaseModel):
    path: str
    start: int = 1
    end: int = 1
    text: str = ""


class LocateResult(BaseModel):
    title: str = Field(max_length=120)
    root_cause: str
    files: list[str]
    plan: list[LocatePlanItem] = Field(default_factory=list)
    snippets: list[LocateSnippet] = Field(default_factory=list)


class AgentTrace:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("# Agent 轨迹\n\n", encoding="utf-8")

    def add(self, heading: str, body: str = "") -> None:
        if self.path is None:
            return
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(f"## {heading}\n\n")
            if body.strip():
                handle.write(body.rstrip() + "\n\n")


class FixoraAgent:
    """locate 与 patch 分会话。重试带上已有虚拟 diff，不再 compact 旧工具输出。"""
    def __init__(
        self,
        *,
        cache: RepositoryCache,
        repository_id: int,
        base_sha: str,
        model_config: dict[str, Any],
        on_event: Callable[[str, dict[str, Any]], None],
        trace_path: Path | None = None,
        image_path: Path | None = None,
        image_mime: str | None = None,
    ) -> None:
        self.cache = cache
        self.repository_id = repository_id
        self.base_sha = base_sha
        self.model_config = model_config
        self.on_event = on_event
        self.trace = AgentTrace(trace_path)
        self.image_path = image_path
        self.image_mime = image_mime or "image/png"
        self._image_url: str | None = None
        self.workspace = VirtualWorkspace(cache, repository_id, base_sha)
        self.phase = "locate"
        self.allowed_paths: set[str] | None = None
        self.seen_reads: set[tuple[str, int, int]] = set()
        self.seen_searches: set[tuple[str, str]] = set()
        self._http: httpx.AsyncClient | None = None
        self._client: AsyncOpenAI | None = None

    async def __aenter__(self) -> FixoraAgent:
        set_tracing_disabled(not get_settings().model_tracing_enabled)
        self._http = httpx.AsyncClient(verify=get_settings().model_http_verify())
        self._client = AsyncOpenAI(
            api_key=str(self.model_config["api_key"]),
            base_url=str(self.model_config.get("base_url") or self.model_config["api_url"]),
            http_client=self._http,
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._http is not None:
            await self._http.aclose()
        self._http = None
        self._client = None

    async def run(self, description: str, source_context: str = "") -> AgentSummary:
        async with self:
            locate = await self.locate(description, source_context)
            return await self.patch(locate)

    async def locate(self, description: str, source_context: str = "") -> LocateResult:
        self.phase = "locate"
        self.allowed_paths = None
        self.seen_reads.clear()
        self.seen_searches.clear()
        self._emit("agent.phase", {"phase": "locate"})
        presearch = self._presearch(description)
        prompt = locate_prompt(description, presearch=presearch, source_context=source_context)
        self.trace.add("定位阶段", prompt[:8_000])
        messages = await self._run_agent(
            name="Fixora Locator",
            instructions=(
                "你是代码定位 Agent。只搜索和阅读，不要修改文件。"
                "思考用简短中文。先用用户标识符 search_code，再用 list_files 看命中目录，"
                "不要枚举全仓库，不要读 .claude、node_modules、dist、release。"
                "同一文件同一行范围只读一次；同一关键词不要原样再搜。"
                "读完 2–4 个相关文件后必须停止，输出一段 JSON："
                '{"title":"短标题","root_cause":"根因","files":["要改的文件"],'
                '"plan":[{"path":"文件","change":"改什么"}],'
                '"snippets":[{"path":"文件","start":1,"end":20,"text":"关键原文"}]}。'
                "files 必须是仓库里已有、准备修改的文本文件。不要输出 JSON 以外的收尾。"
            ),
            tools=self._locate_tools(),
            prompt=self._prompt_input(prompt),
        )
        result = locate_result_from_messages(messages)
        if result is None or not result.files:
            preview = next((text.strip() for text in reversed(messages) if text.strip()), "")
            raise RuntimeError("未能定位到修改文件" + (f"：{preview[:500]}" if preview else ""))
        normalized: list[str] = []
        for item in result.files:
            try:
                path = normalize_repo_path(item)
            except ValueError:
                continue
            if path not in normalized:
                normalized.append(path)
        if not normalized:
            raise RuntimeError("未能定位到修改文件")
        result.files = normalized
        self.trace.add(
            "定位结果", f"{result.title}\n{result.root_cause}\n" + "\n".join(result.files)
        )
        return result

    async def patch(
        self,
        locate: LocateResult,
        description: str = "",
        failure_context: str = "",
    ) -> AgentSummary:
        self.phase = "patch"
        self.allowed_paths = set(locate.files)
        self.seen_reads.clear()
        self.seen_searches.clear()
        self._emit("agent.phase", {"phase": "patch", "files": locate.files})
        prompt = patch_prompt(
            locate,
            description=description,
            failure_context=failure_context,
            virtual_diff=self.workspace.unified_diff_text(),
        )
        heading = "修改阶段" if not failure_context else "修改重试"
        self.trace.add(heading, prompt[:8_000])
        messages = await self._run_agent(
            name="Fixora Patcher",
            instructions=(
                "你是代码修改 Agent。只能改定位名单中的已有文件，不能搜索仓库。"
                "优先 replace_in_file 做最小替换；对不上再用 apply_virtual_patch 写全文件。"
                "改完后写一个可直接执行的临时脚本：JS 用 fixora_temp_*.mjs（node 运行），"
                "Python 用 fixora_temp_*.py。脚本用 fs/pathlib 读源文件做断言，"
                "不要写 jest/mocha/pytest，不要 import 未编译的 TS/TSX。"
                "修改和脚本完成后用一两句中文说明并停止。"
            ),
            tools=self._patch_tools(),
            prompt=self._prompt_input(prompt),
        )
        patches = [(item.path, item.reason) for item in self.workspace.files.values()]
        if not patches:
            raise RuntimeError("Agent 未产生任何文件修改")
        last_text = messages[-1] if messages else ""
        result = summary_from_result(patches, locate.title, last_text)
        if result is None:
            raise RuntimeError("Agent 未返回有效摘要")
        result = AgentSummary(
            title=locate.title or result.title,
            root_cause=locate.root_cause or result.root_cause,
            summary=result.summary,
        )
        self.trace.add("摘要", f"{result.title}\n{result.root_cause}\n{result.summary}")
        return result

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.on_event(event_type, {**payload, "phase": self.phase})

    def _prompt_input(self, prompt: str) -> str | list[dict[str, Any]]:
        image_url = self._image_data_url()
        if not image_url:
            return prompt
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt + "\n问题截图已附加，请结合截图识别用户可见现象。",
                    },
                    {"type": "input_image", "image_url": image_url, "detail": "high"},
                ],
            }
        ]

    def _image_data_url(self) -> str | None:
        if self.image_path is None:
            return None
        if self._image_url is None:
            if not self.image_path.is_file():
                raise RuntimeError("问题截图文件不存在")
            encoded = base64.b64encode(self.image_path.read_bytes()).decode("ascii")
            self._image_url = f"data:{self.image_mime};base64,{encoded}"
        return self._image_url

    def _model(self) -> OpenAIResponsesModel | OpenAIChatCompletionsModel:
        if self._client is None:
            raise RuntimeError("Agent 未连接模型客户端")
        model_name = str(self.model_config["model"])
        if self.model_config["api_mode"] == "responses":
            return OpenAIResponsesModel(model=model_name, openai_client=self._client)
        return OpenAIChatCompletionsModel(model=model_name, openai_client=self._client)

    async def _run_agent(
        self,
        *,
        name: str,
        instructions: str,
        tools: list[Any],
        prompt: str,
    ) -> list[str]:
        agent = Agent(
            name=name,
            instructions=instructions,
            model=self._model(),
            tools=tools,
            model_settings=build_model_settings(self.model_config),
        )
        max_turns = LOCATE_MAX_TURNS if self.phase == "locate" else PATCH_MAX_TURNS
        try:
            return await drain_round(
                agent,
                prompt,
                max_turns=max_turns,
                run_config=RunConfig(),
                on_event=lambda event_type, payload: self._emit(event_type, payload),
                trace=self.trace,
            )
        except MaxTurnsExceeded as exc:
            label = "定位未收敛" if self.phase == "locate" else "修改未收敛"
            self.trace.add(label, f"max_turns={max_turns}")
            raise RuntimeError(f"{label}：工具轮次已达上限（{max_turns}）") from exc
        except ModelBehaviorError as exc:
            snippet = _model_output_snippet(exc)
            self.trace.add("本轮输出未通过校验", snippet or str(exc))
            raise

    def _denied(self, path: str) -> str | None:
        if self.allowed_paths is None:
            return None
        try:
            safe = normalize_repo_path(path)
        except ValueError as exc:
            return str(exc)
        if safe not in self.allowed_paths:
            return f"不在定位名单，禁止访问: {safe}"
        return None

    def _locate_tools(self) -> list[Any]:
        cache = self.cache
        repository_id = self.repository_id
        base_sha = self.base_sha

        @function_tool(name_override="list_files")
        def list_files(path: str = "", depth: int = 1, limit: int = 80) -> dict[str, Any]:
            """列出 path 下的浅层文件和子目录。path 为空表示仓库根目录。隐藏目录和构建产物已过滤。"""
            capped_depth = min(max(depth, 1), 2)
            capped_limit = min(max(limit, 1), 80)
            result = cache.list_tree(
                repository_id,
                base_sha,
                path=path,
                depth=capped_depth,
                limit=capped_limit,
            )
            self._emit(
                "agent.tool",
                {
                    "tool": "list_files",
                    "path": path or ".",
                    "depth": capped_depth,
                    "count": len(result),
                    "preview": result[:16],
                },
            )
            self.trace.add(f"list_files {path or '.'}", "\n".join(result) or "(empty)")
            return {"path": path or ".", "count": len(result), "entries": result}

        @function_tool(name_override="search_code")
        def search_code(
            query: str, path: str = "", limit: int = 40
        ) -> list[dict[str, str | int]] | str:
            """搜索源码路径和内容，不区分大小写；path 可限定目录。"""
            key = (query, path)
            if key in self.seen_searches:
                return f"已搜索过 {query}，请根据已有结果继续，不要重复搜索。"
            self.seen_searches.add(key)
            result = cache.search_code(
                repository_id,
                base_sha,
                query,
                path=path,
                limit=min(max(limit, 1), 40),
            )
            preview = [f"{item['path']}:{item['line']} {item['text']}" for item in result[:8]]
            self._emit(
                "agent.tool",
                {
                    "tool": "search_code",
                    "query": query,
                    "path": path,
                    "count": len(result),
                    "preview": preview,
                },
            )
            self.trace.add(f"search_code {query}", "\n".join(preview) or "(no matches)")
            return [
                {
                    "path": item["path"],
                    "line": item["line"],
                    "text": str(item["text"])[:160],
                }
                for item in result[:12]
            ]

        @function_tool(name_override="read_file")
        def read_file(path: str, start_line: int = 1, end_line: int = 200) -> str:
            """读取文件指定行范围。"""
            return self._read_file(path, start_line, end_line)

        return [list_files, search_code, read_file]

    def _patch_tools(self) -> list[Any]:
        workspace = self.workspace

        @function_tool(name_override="read_file")
        def read_file(path: str, start_line: int = 1, end_line: int = 200) -> str:
            """读取定位名单中的文件。修改过的文件返回虚拟新内容。"""
            denied = self._denied(path)
            if denied:
                return denied
            return self._read_file(path, start_line, end_line)

        @function_tool(name_override="replace_in_file")
        def replace_in_file(path: str, old_text: str, new_text: str, reason: str) -> str:
            """在已有文件中替换一段唯一原文。优先于整文件覆盖。"""
            denied = self._denied(path)
            if denied:
                return denied
            try:
                item = workspace.replace(path, old_text, new_text, reason)
            except ValueError as exc:
                return f"替换失败: {exc}"
            self._emit(
                "agent.tool",
                {"tool": "replace_in_file", "path": item.path, "reason": reason[:300]},
            )
            self.trace.add(f"replace_in_file {item.path}", reason)
            return f"已修改 {item.path}，{len(item.hunks)} 个 hunk"

        @function_tool(name_override="apply_virtual_patch")
        def apply_virtual_patch(path: str, new_content: str, reason: str) -> str:
            """用完整新内容修改一个已存在文本文件；仅写虚拟变更，不写 GitLab。"""
            denied = self._denied(path)
            if denied:
                return denied
            try:
                item = workspace.apply(path, new_content, reason)
            except ValueError as exc:
                return f"修改失败: {exc}"
            self._emit(
                "agent.tool",
                {"tool": "apply_virtual_patch", "path": item.path, "reason": reason[:300]},
            )
            self.trace.add(f"apply_virtual_patch {item.path}", reason)
            return f"已修改 {item.path}，{len(item.hunks)} 个 hunk"

        @function_tool(name_override="write_temp_test")
        def write_temp_test(path: str, content: str) -> str:
            """生成仅用于验证的临时脚本，文件名以 fixora_temp_ 开头，扩展名为 .mjs/.js/.cjs/.py。"""
            try:
                workspace.add_temp_test(path, content)
            except ValueError as exc:
                return f"临时脚本无效: {exc}"
            self._emit("agent.tool", {"tool": "write_temp_test", "path": path})
            self.trace.add("write_temp_test", path)
            return f"临时测试已准备: {path}"

        return [read_file, replace_in_file, apply_virtual_patch, write_temp_test]

    def _read_file(self, path: str, start_line: int, end_line: int) -> str:
        content = self.workspace.read(path)
        lines = content.splitlines()
        start = max(start_line, 1)
        end = min(max(end_line, start), start + 399, len(lines))
        key = (path, start, end)
        if key in self.seen_reads:
            return already_read_message(path, start, end, phase=self.phase)
        if self.phase == "locate":
            covered = covered_read_range(self.seen_reads, path, start, end)
            if covered is not None:
                return already_read_message(path, start, end, phase=self.phase, covered=covered)
        self.seen_reads.add(key)
        self._emit(
            "agent.tool",
            {"tool": "read_file", "path": path, "start": start, "end": end},
        )
        self.trace.add(f"read_file {path}:{start}-{end}", f"{end - start + 1} lines")
        return "\n".join(f"{index}: {lines[index - 1]}" for index in range(start, end + 1))

    def _presearch(self, description: str) -> str:
        keywords = _extract_search_keywords(description)
        sections: list[str] = []
        for keyword in keywords[:8]:
            matches = self.cache.search_code(
                self.repository_id,
                self.base_sha,
                keyword,
                limit=8,
            )
            if not matches:
                continue
            lines = [f"{item['path']}:{item['line']} {item['text']}" for item in matches]
            sections.append(f"[{keyword}]\n" + "\n".join(lines))
        return "\n\n".join(sections)


async def drain_round(
    agent: Agent[Any],
    prompt: str | list[dict[str, Any]],
    *,
    max_turns: int,
    run_config: RunConfig,
    on_event: Callable[[str, dict[str, Any]], None],
    trace: AgentTrace,
) -> list[str]:
    messages: list[str] = []
    streamed = Runner.run_streamed(
        agent,
        prompt,
        max_turns=max_turns,
        run_config=run_config,
    )
    async for event in streamed.stream_events():
        text = _record_stream_event(event, on_event, trace)
        if text:
            messages.append(text)
    return messages


def locate_prompt(description: str, *, presearch: str = "", source_context: str = "") -> str:
    parts = [f"问题描述:\n{description.strip()}"]
    if presearch:
        parts.append(f"用户关键词预检（已过滤构建产物与隐藏目录）:\n{presearch}")
    if source_context.strip():
        parts.append(f"问题页面采集内容:\n{source_context[:20_000]}")
    parts.append("先读预检命中的目录和文件，定位根因后输出 JSON 并停止。")
    return "\n\n".join(parts)


def patch_prompt(
    locate: LocateResult,
    *,
    description: str = "",
    failure_context: str = "",
    virtual_diff: str = "",
) -> str:
    plan_lines = [f"- {item.path}: {item.change}" for item in locate.plan if item.path] or [
        f"- {path}" for path in locate.files
    ]
    parts: list[str] = []
    if description.strip():
        parts.append(f"问题描述:\n{description.strip()}")
    parts.extend(
        [
            f"标题: {locate.title}",
            f"根因:\n{locate.root_cause}",
            "允许修改的文件:\n" + "\n".join(plan_lines),
        ]
    )
    if locate.snippets:
        blocks = []
        for snippet in locate.snippets[:6]:
            blocks.append(f"{snippet.path}:{snippet.start}-{snippet.end}\n{snippet.text[:4_000]}")
        parts.append("已读片段:\n" + "\n\n".join(blocks))
    if virtual_diff.strip():
        parts.append(
            "当前虚拟工作区相对仓库原始内容的修改如下。"
            "这些不是仓库原文件；必须基于它们继续，不要当成未改过的代码。\n"
            + virtual_diff.strip()[-20_000:]
        )
    if failure_context:
        parts.append(f"上一次验证失败，请基于日志重新判断并修复:\n{failure_context[-20_000:]}")
    parts.append("只改名单内文件。用 replace_in_file 做最小修改，然后写 fixora_temp_ 脚本并停止。")
    return "\n\n".join(parts)


def locate_result_from_messages(messages: list[str]) -> LocateResult | None:
    for text in reversed(messages):
        parsed = parse_locate_result(text)
        if parsed is not None:
            return parsed
    return None


def covered_read_range(
    seen_reads: set[tuple[str, int, int]], path: str, start: int, end: int
) -> tuple[int, int] | None:
    for seen_path, seen_start, seen_end in seen_reads:
        if seen_path == path and seen_start <= start and end <= seen_end:
            return seen_start, seen_end
    return None


def already_read_message(
    path: str,
    start: int,
    end: int,
    *,
    phase: str,
    covered: tuple[int, int] | None = None,
) -> str:
    if phase == "locate":
        extra = f"（此前读过 {covered[0]}-{covered[1]}）" if covered else ""
        return (
            f"已读取过 {path}:{start}-{end}{extra}。"
            "不要再换行号重复读取，立即输出定位 JSON 并停止。"
        )
    return f"已读取过 {path}:{start}-{end}，请根据已有内容继续，不要重复读取。"


def parse_locate_result(text: str) -> LocateResult | None:
    if not text.strip():
        return None
    raw = extract_json_object(text)
    try:
        parsed = LocateResult.model_validate_json(raw)
    except Exception:
        try:
            data = json.loads(raw)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        files = [str(item).strip() for item in data.get("files") or [] if str(item).strip()]
        if not files:
            return None
        parsed = LocateResult(
            title=str(data.get("title") or "代码修改")[:120],
            root_cause=str(data.get("root_cause") or ""),
            files=files,
        )
    parsed.files = [item.strip() for item in parsed.files if item.strip()]
    return parsed if parsed.files else None


def extract_json_object(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


def _record_stream_event(
    event: Any,
    on_event: Callable[[str, dict[str, Any]], None],
    trace: AgentTrace,
) -> str:
    if getattr(event, "type", None) != "run_item_stream_event":
        return ""
    name = getattr(event, "name", "")
    item = getattr(event, "item", None)
    if name == "reasoning_item_created":
        text = _reasoning_text(item)
        if text:
            on_event("agent.thought", {"text": text[:2_000]})
            trace.add("思考", text)
        return ""
    if name == "message_output_created" and isinstance(item, MessageOutputItem):
        text = ItemHelpers.text_message_output(item)
        if text:
            on_event("agent.message", {"text": text[:2_000]})
            trace.add("模型输出", text)
            return text
    return ""


def _reasoning_text(item: Any) -> str:
    raw = getattr(item, "raw_item", item)
    if isinstance(raw, dict):
        blocks = raw.get("content") or raw.get("summary") or []
    else:
        blocks = getattr(raw, "content", None) or getattr(raw, "summary", None) or []
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, str):
            parts.append(block)
            continue
        if isinstance(block, dict):
            text = block.get("text")
        else:
            text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


def _model_output_snippet(exc: ModelBehaviorError) -> str:
    run_data = getattr(exc, "run_data", None)
    responses = getattr(run_data, "raw_responses", None) if run_data is not None else None
    if not responses:
        return str(exc)
    last = responses[-1]
    chunks: list[str] = []
    for item in getattr(last, "output", []) or []:
        text = ItemHelpers.extract_last_content(item)
        if text:
            chunks.append(text)
        arguments = getattr(item, "arguments", None)
        if arguments:
            chunks.append(str(arguments))
    return "\n".join(chunks)[:2_000] or str(exc)


def summary_from_patches(patches: list[tuple[str, str]], description: str) -> AgentSummary | None:
    if not patches:
        return None
    title = description.strip().splitlines()[0][:80] or "代码修改"
    summary = "；".join(f"{path}：{reason}" for path, reason in patches)
    return AgentSummary(
        title=title,
        root_cause="根因以已写入的修改原因为准。",
        summary=summary[:2_000],
    )


def summary_from_result(
    patches: list[tuple[str, str]],
    description: str,
    last_text: str = "",
) -> AgentSummary | None:
    if last_text.strip():
        try:
            parsed = AgentSummary.model_validate_json(extract_json_object(last_text))
            return parsed
        except Exception:
            pass
    base = summary_from_patches(patches, description)
    if base is None:
        return None
    if len(last_text.strip()) > 20:
        return AgentSummary(
            title=_title_from_text(last_text, base.title),
            root_cause=last_text.strip()[:800],
            summary=base.summary,
        )
    return base


def _title_from_text(text: str, fallback: str) -> str:
    for raw in text.splitlines():
        line = raw.strip().lstrip("#* ").strip()
        if not line.startswith(("标题", "title", "Title")):
            continue
        _, _, rest = line.replace("：", ":", 1).partition(":")
        rest = rest.strip().strip("*").strip()
        if rest:
            return rest[:120]
    return fallback[:120]


def _extract_search_keywords(description: str) -> list[str]:
    import re

    candidates = re.findall(r"[A-Za-z_][A-Za-z0-9_-]{3,}", description)
    ignored = {"button", "input", "page", "show", "display", "delete"}
    unique: list[str] = []
    for value in candidates:
        if value.casefold() in ignored or value.casefold() in {item.casefold() for item in unique}:
            continue
        unique.append(value)
    return sorted(
        unique,
        key=lambda value: (
            not any(char.isupper() for char in value[1:]),
            -len(value),
        ),
    )


def build_model_settings(config: dict[str, Any]) -> ModelSettings:
    parameters = dict(config.get("parameters") or {})
    max_tokens = parameters.pop("max_tokens", parameters.pop("max_output_tokens", None))
    known = {
        "temperature": parameters.pop("temperature", None),
        "top_p": parameters.pop("top_p", None),
        "frequency_penalty": parameters.pop("frequency_penalty", None),
        "presence_penalty": parameters.pop("presence_penalty", None),
        "parallel_tool_calls": parameters.pop("parallel_tool_calls", None),
        "verbosity": parameters.pop("verbosity", None),
        "timeout": parameters.pop("timeout", None),
        "max_tokens": max_tokens,
    }
    effort = config.get("reasoning_effort")
    reasoning = Reasoning(effort=effort) if effort and effort != "none" else None
    return ModelSettings(
        **known,
        reasoning=reasoning,
        extra_body=parameters or None,
    )
