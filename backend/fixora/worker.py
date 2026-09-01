"""Dramatiq 进程入口。actor 定义在 `fixora.tasks.worker`。"""

from __future__ import annotations

from .tasks.worker import (  # noqa: F401
    commit_attempt_actor,
    commit_task_actor,
    run_attempt_actor,
    run_task_actor,
)
