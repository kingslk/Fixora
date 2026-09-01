from __future__ import annotations

import re
import shutil
from pathlib import Path

from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..models import Task, TaskAttempt
from ..repo.cache import RepositoryCache
from .attempts import mark_attempt_finished, sync_task_projection
from .events import emit_event

_WORKTREE_DIR = re.compile(r"^task-(\d+)(?:-a\d+)?$")
_INTERRUPTED = {
    "queued",
    "capturing_source",
    "syncing_repository",
    "analyzing",
    "validating",
    "committing",
    "stale",
}


def recover_interrupted_tasks() -> None:
    """API 启动时把卡在活动态的 Attempt 标失败，并拆掉 /tmp/fixora 残留 worktree。"""
    settings = get_settings()
    with SessionLocal() as db:
        attempts = list(db.scalars(select(TaskAttempt).where(TaskAttempt.status.in_(_INTERRUPTED))))
        for attempt in attempts:
            attempt.status = "failed"
            attempt.error = "服务重启中断了任务；请重新创建任务"
            mark_attempt_finished(attempt)
            task = db.get(Task, attempt.task_id)
            if task is not None:
                sync_task_projection(task, attempt)
                emit_event(
                    db,
                    task,
                    "task.failed",
                    {"error": attempt.error, "recovered": True},
                    attempt=attempt,
                )
        db.commit()

        task_repositories = {
            task_id: repository_id
            for task_id, repository_id in db.execute(select(Task.id, Task.repository_id))
        }

    workspace_root = Path("/tmp/fixora")
    if not workspace_root.is_dir():
        return
    cache = RepositoryCache(settings.git_root, token="")
    for path in workspace_root.iterdir():
        match = _WORKTREE_DIR.fullmatch(path.name)
        if not match or not path.is_dir():
            continue
        repository_id = task_repositories.get(int(match.group(1)))
        if repository_id and cache.path_for(repository_id).is_dir():
            try:
                cache.remove_worktree(repository_id, path)
                continue
            except Exception:
                pass
        shutil.rmtree(path)
