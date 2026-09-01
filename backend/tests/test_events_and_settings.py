from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fixora.db import Base
from fixora.models import Repository, Task, TaskEvent
from fixora.settings_store import SettingsStore, public_settings
from fixora.tasks.attempts import create_attempt
from fixora.tasks.events import emit_event


def test_event_sequence_and_secret_masking() -> None:
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
        create_attempt(db, task, 1)
        emit_event(db, task, "task.started", {})
        emit_event(db, task, "step.running", {"kind": "analyze"})
        db.commit()
        assert [event.seq for event in db.scalars(select(TaskEvent).order_by(TaskEvent.seq))] == [
            1,
            2,
        ]

        store = SettingsStore(db)
        store.put("model", {"api_url": "https://api.example.com/v1", "api_key": "secret"})
        db.commit()
        assert store.get("model") == {"api_url": "https://api.example.com/v1", "api_key": "secret"}
        assert public_settings(store.get("model"), secrets={"api_key"})["api_key"] == "••••••••"
