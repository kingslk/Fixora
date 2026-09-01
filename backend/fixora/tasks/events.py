from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Task, TaskAttempt, TaskEvent
from .attempts import sync_task_projection


def emit_event(
    db: Session,
    task: Task,
    event_type: str,
    payload: dict[str, Any],
    *,
    attempt: TaskAttempt | None = None,
) -> TaskEvent:
    # seq 按 Attempt 递增；锁 Attempt 行避免 Worker 与审批 API 并发抢号。
    target = attempt or task.current_attempt
    if target is None:
        raise LookupError(f"Task {task.id} 没有可写入的 Attempt")
    locked = db.scalar(select(TaskAttempt).where(TaskAttempt.id == target.id).with_for_update())
    if locked is None:
        raise LookupError(f"TaskAttempt {target.id} 不存在")
    locked.event_seq += 1
    if task.current_attempt_id == locked.id:
        sync_task_projection(task, locked)
    event = TaskEvent(
        task_id=task.id,
        task_attempt_id=locked.id,
        seq=locked.event_seq,
        type=event_type,
        payload=payload,
    )
    db.add(event)
    db.flush()
    return event
