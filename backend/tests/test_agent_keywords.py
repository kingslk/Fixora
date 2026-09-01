from __future__ import annotations

from pathlib import Path

from fixora.agent.runtime import (
    AgentTrace,
    LocateResult,
    _extract_search_keywords,
    already_read_message,
    covered_read_range,
    extract_json_object,
    locate_prompt,
    locate_result_from_messages,
    parse_locate_result,
    patch_prompt,
    summary_from_patches,
    summary_from_result,
)


def test_camel_case_page_name_is_promoted_as_search_keyword() -> None:
    description = "账号注销：身份验证输入框删除按钮未展示，注销页面是accountLogoff"
    assert _extract_search_keywords(description)[0] == "accountLogoff"


def test_extract_json_object_strips_markdown_fences() -> None:
    raw = """思考完毕
```json
{"title": "修复注销", "root_cause": "按钮被删", "summary": "恢复按钮"}
```
"""
    assert extract_json_object(raw) == (
        '{"title": "修复注销", "root_cause": "按钮被删", "summary": "恢复按钮"}'
    )


def test_summary_from_patches_recovers_from_written_files() -> None:
    assert summary_from_patches([], "账号注销") is None
    summary = summary_from_patches(
        [("src/Verify.tsx", "给身份证输入补清除按钮")],
        "账号注销：身份验证输入框删除按钮未展示",
    )
    assert summary is not None
    assert "Verify.tsx" in summary.summary
    assert "清除按钮" in summary.summary


def test_summary_from_result_uses_last_markdown_when_json_missing() -> None:
    last = """## 修复说明
**标题**：身份验证输入框删除按钮不显示
根因是 SvgIcon 资源缺失。
"""
    summary = summary_from_result(
        [("src/Verify.tsx", "改为内联 SVG")],
        "账号注销：身份验证输入框删除按钮未展示",
        last,
    )
    assert summary is not None
    assert summary.title == "身份验证输入框删除按钮不显示"
    assert "SvgIcon" in summary.root_cause
    assert "Verify.tsx" in summary.summary


def test_parse_locate_result_reads_json_and_rejects_empty_files() -> None:
    parsed = parse_locate_result(
        """定位完成
```json
{"title": "补删除按钮", "root_cause": "idcard 没有 ic-clear", "files": ["src/Verify.tsx"]}
```
"""
    )
    assert parsed is not None
    assert parsed.files == ["src/Verify.tsx"]
    assert parse_locate_result('{"title": "x", "root_cause": "y", "files": []}') is None
    loose = parse_locate_result(
        '{"title": "补按钮", "root_cause": "缺清除", "files": ["a.tsx"], "plan": "自由文本"}'
    )
    assert loose is not None
    assert loose.files == ["a.tsx"]


def test_locate_result_from_messages_uses_latest_json() -> None:
    parsed = locate_result_from_messages(
        [
            "还在看文件",
            '{"title": "旧", "root_cause": "x", "files": ["old.tsx"]}',
            '{"title": "新", "root_cause": "y", "files": ["src/Verify.tsx"]}',
        ]
    )
    assert parsed is not None
    assert parsed.files == ["src/Verify.tsx"]
    assert parsed.title == "新"
    assert locate_result_from_messages(["没有 json", ""]) is None


def test_covered_locate_read_nudges_json_output() -> None:
    seen = {("src/Verify.tsx", 1, 200)}
    assert covered_read_range(seen, "src/Verify.tsx", 17, 100) == (1, 200)
    assert covered_read_range(seen, "src/Verify.tsx", 1, 200) == (1, 200)
    assert covered_read_range(seen, "src/Verify.tsx", 180, 260) is None
    assert covered_read_range(seen, "src/other.tsx", 17, 100) is None
    message = already_read_message("src/Verify.tsx", 17, 100, phase="locate", covered=(1, 200))
    assert "定位 JSON" in message
    assert "1-200" in message


def test_locate_and_patch_prompts_carry_presearch_and_whitelist() -> None:
    locate = locate_prompt("账号注销删除按钮", presearch="[accountLogoff]\napp/accountLogoff/")
    assert "accountLogoff" in locate
    assert "JSON" in locate
    patch = patch_prompt(
        LocateResult(
            title="补删除按钮",
            root_cause="idcard 没有清除按钮",
            files=["src/Verify.tsx"],
            plan=[],
            snippets=[],
        ),
        description="账号注销删除按钮",
        virtual_diff="--- a/src/Verify.tsx\n+++ b/src/Verify.tsx\n+clearable",
        failure_context="assert failed",
    )
    assert "src/Verify.tsx" in patch
    assert "replace_in_file" in patch
    assert "账号注销删除按钮" in patch
    assert "虚拟工作区" in patch
    assert "assert failed" in patch


def test_agent_trace_writes_markdown(tmp_path: Path) -> None:
    path = tmp_path / "agent-trace.md"
    trace = AgentTrace(path)
    trace.add("思考", "先搜索 accountLogoff")
    trace.add("list_files src", "src/pages/")
    text = path.read_text(encoding="utf-8")
    assert "# Agent 轨迹" in text
    assert "## 思考" in text
    assert "accountLogoff" in text
