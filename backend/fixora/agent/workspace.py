from __future__ import annotations

import difflib
import hashlib
import json
from dataclasses import dataclass

from ..repo.cache import RepositoryCache, normalize_repo_path


@dataclass(frozen=True)
class VirtualFile:
    path: str
    base_blob_sha: str
    old_content: str
    new_content: str
    reason: str
    unified_diff: str
    hunks: list[dict[str, object]]


class VirtualWorkspace:
    """Agent 只改这里。确认后 workflow 才用这些内容组 GitLab commit。"""
    def __init__(self, cache: RepositoryCache, repository_id: int, base_sha: str) -> None:
        self.cache = cache
        self.repository_id = repository_id
        self.base_sha = base_sha
        self.files: dict[str, VirtualFile] = {}
        self.temp_tests: dict[str, str] = {}

    def read(self, path: str) -> str:
        safe = normalize_repo_path(path)
        if safe in self.files:
            return self.files[safe].new_content
        return self.cache.read_file(self.repository_id, self.base_sha, safe)

    def replace(self, path: str, old_text: str, new_text: str, reason: str) -> VirtualFile:
        if not old_text:
            raise ValueError("替换原文不能为空")
        current = self.read(path)
        count = current.count(old_text)
        if count == 0:
            raise ValueError("未找到要替换的原文，请提供更完整片段")
        if count > 1:
            raise ValueError("原文出现多次，请提供更长、能唯一匹配的片段")
        return self.apply(path, current.replace(old_text, new_text, 1), reason)

    def apply(self, path: str, new_content: str, reason: str) -> VirtualFile:
        safe = normalize_repo_path(path)
        old = self.cache.read_file(self.repository_id, self.base_sha, safe)
        if old == new_content:
            raise ValueError("修改前后内容相同")
        blob = self.cache.blob_sha(self.repository_id, self.base_sha, safe)
        diff_lines = list(
            difflib.unified_diff(
                old.splitlines(),
                new_content.splitlines(),
                fromfile=f"a/{safe}",
                tofile=f"b/{safe}",
                lineterm="",
            )
        )
        item = VirtualFile(
            path=safe,
            base_blob_sha=blob,
            old_content=old,
            new_content=new_content,
            reason=reason.strip(),
            unified_diff="\n".join(diff_lines) + "\n",
            hunks=_structured_diff(old, new_content),
        )
        self.files[safe] = item
        return item

    def add_temp_test(self, path: str, content: str) -> None:
        safe = normalize_repo_path(path)
        name = safe.rsplit("/", 1)[-1]
        if not name.startswith("fixora_temp_"):
            raise ValueError("临时测试文件名必须以 fixora_temp_ 开头")
        lowered = name.casefold()
        if lowered.endswith((".ts", ".tsx", ".jsx")):
            raise ValueError("临时脚本必须是可直接执行的 .mjs/.js/.cjs 或 .py，不要写 jest/tsx")
        if not lowered.endswith((".mjs", ".js", ".cjs", ".py")):
            raise ValueError("临时脚本只接受 .mjs/.js/.cjs/.py")
        self.temp_tests[safe] = content

    def unified_diff_text(self) -> str:
        """重试会话必须带上已有虚拟修改，否则 Agent 会把 diff 当成仓库原文。"""
        if not self.files:
            return ""
        return "\n".join(item.unified_diff for item in self.files.values())

    def patch_hash(self) -> str:
        payload = [
            {"path": item.path, "blob": item.base_blob_sha, "new": item.new_content}
            for item in sorted(self.files.values(), key=lambda item: item.path)
        ]
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()


def _structured_diff(old: str, new: str) -> list[dict[str, object]]:
    matcher = difflib.SequenceMatcher(a=old.splitlines(), b=new.splitlines())
    rows: list[dict[str, object]] = []
    old_no = new_no = 1
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for line in old.splitlines()[i1:i2]:
                rows.append({"type": "context", "old": old_no, "new": new_no, "text": line})
                old_no += 1
                new_no += 1
        else:
            if tag in {"replace", "delete"}:
                for line in old.splitlines()[i1:i2]:
                    rows.append({"type": "delete", "old": old_no, "new": None, "text": line})
                    old_no += 1
            if tag in {"replace", "insert"}:
                for line in new.splitlines()[j1:j2]:
                    rows.append({"type": "insert", "old": None, "new": new_no, "text": line})
                    new_no += 1
    # Keep UI payload bounded while retaining changed areas and nearby context.
    changed = [index for index, row in enumerate(rows) if row["type"] != "context"]
    if not changed:
        return []
    keep: set[int] = set()
    for index in changed:
        keep.update(range(max(0, index - 3), min(len(rows), index + 4)))
    selected = [rows[index] for index in sorted(keep)]
    old_numbers = [int(row["old"]) for row in selected if row["old"] is not None]
    new_numbers = [int(row["new"]) for row in selected if row["new"] is not None]
    old_start = min(old_numbers, default=0)
    new_start = min(new_numbers, default=0)
    old_count = max(old_numbers, default=old_start) - old_start + 1 if old_numbers else 0
    new_count = max(new_numbers, default=new_start) - new_start + 1 if new_numbers else 0
    return [
        {
            "header": f"@@ -{old_start},{old_count} +{new_start},{new_count} @@",
            "rows": selected,
        }
    ]
