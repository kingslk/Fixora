from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Task, TaskAttempt, utcnow

# Worker 仍在跑或即将重入同一 Attempt（stale = 默认分支 SHA 变了，不新建 Attempt）。
ACTIVE_STATUSES = {
    "queued",
    "capturing_source",
    "syncing_repository",
    "analyzing",
    "validating",
    "committing",
    "stale",
}
WAITING_STATUSES = {"awaiting_approval", "awaiting_force_approval"}
TERMINAL_STATUSES = {"completed", "rejected", "failed", "cancelled", "superseded"}


def is_active_status(status: str) -> bool:
    return status in ACTIVE_STATUSES


def create_attempt(
    db: Session, task: Task, attempt_no: int, *, status: str = "queued"
) -> TaskAttempt:
    attempt = TaskAttempt(
        task_id=task.id,
        attempt_no=attempt_no,
        title="待分析问题",
        status=status,
        event_seq=0,
    )
    db.add(attempt)
    db.flush()
    task.current_attempt_id = attempt.id
    sync_task_projection(task, attempt)
    return attempt


def sync_task_projection(task: Task, attempt: TaskAttempt) -> None:
    """把当前 Attempt 写回 Task 行，保持旧 TaskView 字段可用。"""
    task.title = attempt.title
    task.status = attempt.status
    task.base_sha = attempt.base_sha
    task.run_attempt = attempt.attempt_no
    task.event_seq = attempt.event_seq
    task.forced_reason = attempt.forced_reason
    task.branch_name = attempt.branch_name
    task.commit_sha = attempt.commit_sha
    task.error = attempt.error
    task.current_attempt_id = attempt.id


def mark_attempt_finished(attempt: TaskAttempt) -> None:
    if attempt.execution_finished_at is None:
        attempt.execution_finished_at = utcnow()


def next_attempt_no(task: Task) -> int:
    numbers = [item.attempt_no for item in task.attempts]
    return max(numbers, default=0) + 1
