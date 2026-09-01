from __future__ import annotations

from urllib.parse import quote, urlparse

from ..models import ChangeSet, Repository, Task, TaskAttempt, TestRun

_MARKDOWN_SPECIAL = str.maketrans(
    {
        "\\": "\\\\",
        "`": "\\`",
        "*": "\\*",
        "[": "\\[",
        "]": "\\]",
        "(": "\\(",
        ")": "\\)",
    }
)


def sanitize_agent_text(text: str) -> str:
    """转义 Markdown，并用零宽空格拆开 @，避免模型文本变成 GitLab mention 或链接。"""
    cleaned = (text or "").replace("\x00", "").replace("\r\n", "\n")
    escaped = cleaned.translate(_MARKDOWN_SPECIAL)
    return escaped.replace("@", "@\u200b")


def encode_blob_path(path: str) -> str:
    return "/".join(quote(part, safe="") for part in path.split("/"))


def repository_web_url(clone_url: str) -> str:
    parsed = urlparse(clone_url.strip())
    if parsed.username or parsed.password:
        raise ValueError("clone URL 含有凭据，拒绝生成 web URL")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("clone URL 必须是无凭据的 HTTP(S) 地址")
    path = parsed.path.rstrip("/").removesuffix(".git")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def file_blob_url(clone_url: str, commit_sha: str, path: str) -> str:
    return f"{repository_web_url(clone_url)}/-/blob/{commit_sha}/{encode_blob_path(path)}"


def commit_comment_body(
    task: Task,
    change_set: ChangeSet,
    *,
    attempt: TaskAttempt | None = None,
    repository: Repository | None = None,
    test_runs: list[TestRun] | None = None,
) -> str:
    attempt_no = attempt.attempt_no if attempt is not None else 1
    branch = (attempt.branch_name if attempt is not None else None) or task.branch_name or ""
    commit_sha = (attempt.commit_sha if attempt is not None else None) or task.commit_sha or ""
    forced = bool((attempt.forced_reason if attempt is not None else None) or task.forced_reason)
    validation = "forced" if forced else "passed"
    title = sanitize_agent_text((attempt.title if attempt is not None else None) or task.title)
    parts = [
        f"Fixora Task #{task.id} · Attempt {attempt_no}",
        f"- 标题：{title}",
        f"- 分支：`{branch}`" if branch else "- 分支：",
        f"- commit：`{commit_sha}`" if commit_sha else "- commit：",
        f"- 验证：{validation}",
    ]
    forced_reason = (attempt.forced_reason if attempt is not None else None) or task.forced_reason
    if forced_reason:
        parts.append(f"- 强制提交原因：{sanitize_agent_text(forced_reason)}")
    root_cause = change_set.root_cause.strip()
    if root_cause:
        parts.extend(["", "根因", sanitize_agent_text(root_cause)])
    summary = change_set.summary.strip()
    if summary:
        parts.extend(["", "修改摘要", sanitize_agent_text(summary)])
    parts.extend(["", "文件"])
    clone_url = (repository.clone_url if repository is not None else "") or ""
    if change_set.files:
        for item in change_set.files:
            reason = sanitize_agent_text(item.reason)
            label = sanitize_agent_text(item.path)
            if clone_url and commit_sha:
                try:
                    url = file_blob_url(clone_url, commit_sha, item.path)
                except ValueError:
                    parts.append(f"- `{item.path}`：{reason}")
                else:
                    parts.append(f"- [{label}]({url})：{reason}")
            else:
                parts.append(f"- `{item.path}`：{reason}")
    else:
        parts.append("- （无文件）")
    runs = test_runs if test_runs is not None else []
    if runs:
        parts.extend(["", "验证"])
        last = runs[-1]
        command = " ".join(str(item) for item in last.command) if last.command else ""
        status_text = {
            "passed": "通过",
            "failed": "未通过",
            "unverified": "未验证",
        }.get(last.status, last.status)
        line = f"- {status_text}"
        if command:
            line += f"：`{command}`"
        parts.append(line)
    return "\n".join(parts)
