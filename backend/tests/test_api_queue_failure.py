from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fixora.db import Base
from fixora.http.api import _enqueue_or_fail
from fixora.models import Repository, Task, TaskEvent
from fixora.tasks.attempts import create_attempt


class BrokenActor:
    @staticmethod
    def send(_: int) -> None:
        raise ConnectionError("redis unavailable")


def test_enqueue_failure_marks_task_failed() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
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
        attempt = create_attempt(db, task, 1)
        db.commit()

        try:
            _enqueue_or_fail(db, task, BrokenActor, attempt.id)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 503
        else:
            raise AssertionError("queue failure must surface")

        db.refresh(task)
        assert task.status == "failed"
        event = db.scalar(select(TaskEvent).where(TaskEvent.task_id == task.id))
        assert event and event.type == "task.failed"
