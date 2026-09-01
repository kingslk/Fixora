from __future__ import annotations

from pathlib import Path
from typing import Any

from fixora.gitlab import GitLabClient, GitLabError, project_path_from_url
from fixora.models import ChangeSet, FileChange, Task
from fixora.repo.cache import RepositoryCache
from fixora.tasks.workflow import _branch_already_exists, _branch_name, _commit_comment_body


def test_gitlab_ssl_verify_controls_api_and_git(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("fixora.gitlab.client.httpx.Client", FakeClient)
    GitLabClient(
        "https://gitlab.example.com",
        "token",
        ssl_verify=False,
        ca_bundle="/unused/ca.pem",
    )
    assert captured["verify"] is False

    cache = RepositoryCache(tmp_path, "token", ca_bundle="/unused/ca.pem", ssl_verify=False)
    env = cache._env()
    assert env["GIT_SSL_NO_VERIFY"] == "1"
    assert "GIT_SSL_CAINFO" not in env


class _FakeResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 201) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


def test_create_commit_opens_new_branch_from_start_sha(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeHttp:
        def __init__(self, **kwargs: object) -> None:
            return None

        def request(self, method: str, path: str, **kwargs: object) -> _FakeResponse:
            captured["method"] = method
            captured["path"] = path
            captured["json"] = kwargs.get("json")
            return _FakeResponse({"id": "abc123"})

        def close(self) -> None:
            return None

    monkeypatch.setattr("fixora.gitlab.client.httpx.Client", FakeHttp)
    client = GitLabClient("https://gitlab.example.com", "token")
    result = client.create_commit(
        12,
        branch="fix/fixora-11-repair",
        message="fix: button",
        actions=[{"action": "update", "file_path": "a.tsx", "content": "x"}],
        start_sha="deadbeef",
    )
    assert result["id"] == "abc123"
    assert captured["method"] == "POST"
    assert captured["path"] == "/projects/12/repository/commits"
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["branch"] == "fix/fixora-11-repair"
    assert payload["start_sha"] == "deadbeef"
    assert payload["actions"][0]["file_path"] == "a.tsx"
    assert "last_commit_id" not in payload["actions"][0]


def test_gitlab_project_url_is_converted_to_encoded_project_path() -> None:
    assert (
        project_path_from_url(
            "https://gitlab.example.com/group/app",
            "https://gitlab.example.com",
        )
        == "group/app"
    )
    assert (
        project_path_from_url(
            "https://other.example.com/group/app",
            "https://gitlab.example.com",
        )
        is None
    )


def test_branch_name_from_chinese_title_and_already_exists() -> None:
    assert _branch_name(11, "身份验证输入框缺少删除按钮") == "fix/fixora-11-repair"
    assert _branch_name(11, "身份验证输入框缺少删除按钮", 2) == "fix/fixora-11-a2-repair"
    assert _branch_already_exists(GitLabError('GitLab 400: {"message":"Branch already exists"}'))
    assert not _branch_already_exists(GitLabError("GitLab 400: file has changed"))


def test_create_commit_comment_posts_note(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeHttp:
        def __init__(self, **kwargs: object) -> None:
            return None

        def request(self, method: str, path: str, **kwargs: object) -> _FakeResponse:
            captured["method"] = method
            captured["path"] = path
            captured["json"] = kwargs.get("json")
            return _FakeResponse({"note": "ok"})

        def close(self) -> None:
            return None

    monkeypatch.setattr("fixora.gitlab.client.httpx.Client", FakeHttp)
    client = GitLabClient("https://gitlab.example.com", "token")
    result = client.create_commit_comment(12, "abc123", "hello")
    assert result["note"] == "ok"
    assert captured["method"] == "POST"
    assert captured["path"] == "/projects/12/repository/commits/abc123/comments"
    assert captured["json"] == {"note": "hello"}


def test_commit_comment_body_includes_root_cause_and_files() -> None:
    task = Task(id=11, title="缺清除按钮", forced_reason=None, branch_name="fix/fixora-11-repair")
    change_set = ChangeSet(root_cause="idcard 没有 ic-clear", summary="补了清除按钮")
    change_set.files = [
        FileChange(path="src/Verify.tsx", reason="增加清除按钮"),
    ]
    body = _commit_comment_body(task, change_set)
    assert "Fixora Task #11 · Attempt 1" in body
    assert "idcard 没有 ic-clear" in body
    assert "src/Verify.tsx" in body
    assert "passed" in body
