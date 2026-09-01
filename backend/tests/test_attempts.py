from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fixora.db import Base
from fixora.gitlab.comment import commit_comment_body, file_blob_url, sanitize_agent_text
from fixora.http.schemas import task_view
from fixora.models import ChangeSet, FileChange, Repository, Task
from fixora.paths import default_data_root, resolve_artifact_file, worktree_dir
from fixora.tasks.attempts import (
    create_attempt,
    is_active_status,
    next_attempt_no,
    sync_task_projection,
)


def _repo_task(db: Session) -> tuple[Repository, Task]:
    repository = Repository(
        gitlab_project_id=1,
        name="repo",
        path_with_namespace="group/repo",
        clone_url="https://gitlab.example.com/group/repo.git",
        default_branch="master",
    )
    db.add(repository)
    db.flush()
    task = Task(repository_id=repository.id, description="bug")
    db.add(task)
    db.flush()
    return repository, task


def test_old_task_backfill_shape_and_view() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        _repository, task = _repo_task(db)
        attempt = create_attempt(db, task, 1, status="awaiting_approval")
        attempt.title = "补按钮"
        sync_task_projection(task, attempt)
        change = ChangeSet(
            task_id=task.id,
            task_attempt_id=attempt.id,
            base_sha="abc",
            patch_hash="b" * 64,
            summary="摘要",
            root_cause="根因",
            status="ready",
        )
        db.add(change)
        db.flush()
        db.add(
            FileChange(
                change_set_id=change.id,
                path="a.tsx",
                base_blob_sha="c" * 40,
                old_content="old",
                new_content="new",
                reason="reason",
                unified_diff="",
                hunks=[],
            )
        )
        db.commit()
        db.refresh(task)
        view = task_view(task)
        assert view.attempt_no == 1
        assert view.current_attempt_no == 1
        assert view.change_sets[0].root_cause == "根因"
        assert not is_active_status(view.status)
        assert next_attempt_no(task) == 2


def test_worktree_and_legacy_artifact_fallback(tmp_path: Path) -> None:
    assert worktree_dir(11, 2) == Path("/tmp/fixora/task-11-a2")
    root = tmp_path / "artifacts"
    legacy = root / "task-11"
    legacy.mkdir(parents=True)
    (legacy / "agent-trace.md").write_text("old", encoding="utf-8")
    assert resolve_artifact_file(root, 11, 1, "agent-trace.md") == legacy / "agent-trace.md"
    assert resolve_artifact_file(root, 11, 2, "agent-trace.md") is None
    newer = root / "task-11" / "attempt-2"
    newer.mkdir(parents=True)
    (newer / "agent-trace.md").write_text("new", encoding="utf-8")
    assert resolve_artifact_file(root, 11, 2, "agent-trace.md") == newer / "agent-trace.md"


def test_default_data_root_is_not_source_tree() -> None:
    root = default_data_root()
    assert "Application Support" in str(root) or root.name == "fixora"


def test_comment_escapes_mention_and_builds_file_link() -> None:
    assert "@\u200badmin" in sanitize_agent_text("ping @admin")
    evil = sanitize_agent_text("[click](http://evil)")
    assert "\\[" in evil
    url = file_blob_url(
        "https://gitlab.example.com/group/repo.git",
        "abc123",
        "src/pages/Identity Verify.jsx",
    )
    assert url.endswith("/-/blob/abc123/src/pages/Identity%20Verify.jsx")
    task = Task(id=11, title="缺清除按钮", branch_name="fix/fixora-11-repair", commit_sha="abc123")
    change = ChangeSet(root_cause="idcard 没有 ic-clear", summary="补了清除按钮")
    change.files = [FileChange(path="src/Verify.tsx", reason="增加清除按钮")]
    repository = Repository(
        gitlab_project_id=1,
        name="repo",
        path_with_namespace="group/repo",
        clone_url="https://gitlab.example.com/group/repo.git",
        default_branch="master",
    )
    body = commit_comment_body(task, change, repository=repository)
    assert "Attempt 1" in body
    assert "abc123/src/Verify.tsx" in body
    assert "idcard 没有 ic-clear" in body
