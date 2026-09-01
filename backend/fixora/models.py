from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class SystemSetting(Base):
    """可写系统设置。GitLab / 模型密钥不进这张表。"""
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    encrypted_value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Repository(Base):
    """已接入的 GitLab 项目。clone_url 不含凭据。"""
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(primary_key=True)
    gitlab_project_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    path_with_namespace: Mapped[str] = mapped_column(String(512), unique=True)
    clone_url: Mapped[str] = mapped_column(String(2048))
    default_branch: Mapped[str] = mapped_column(String(255))
    cached_sha: Mapped[str | None] = mapped_column(String(64))
    cache_status: Mapped[str] = mapped_column(String(32), default="pending")
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    runtime_profile: Mapped[RepositoryRuntimeProfile | None] = relationship(
        back_populates="repository", cascade="all, delete-orphan", uselist=False
    )
    tasks: Mapped[list[Task]] = relationship(back_populates="repository")


class RepositoryRuntimeProfile(Base):
    """验证用语言/包管理器/命令。可自动探测，也可人工改。"""
    __tablename__ = "repository_runtime_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), unique=True)
    language: Mapped[str] = mapped_column(String(32))
    runtime_version: Mapped[str] = mapped_column(String(64), default="")
    package_manager: Mapped[str] = mapped_column(String(32), default="")
    working_directory: Mapped[str] = mapped_column(String(1024), default=".")
    install_argv: Mapped[list[str]] = mapped_column(JSON, default=list)
    test_argv: Mapped[list[str]] = mapped_column(JSON, default=list)
    lockfile_path: Mapped[str | None] = mapped_column(String(1024))
    lockfile_hash: Mapped[str | None] = mapped_column(String(64))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    repository: Mapped[Repository] = relationship(back_populates="runtime_profile")


class TaskAttempt(Base):
    """一次完整的定位/修改/验证运行。Task 是稳定需求容器，Attempt 才是可重跑单位。"""

    __tablename__ = "task_attempts"
    __table_args__ = (UniqueConstraint("task_id", "attempt_no", name="uq_task_attempts_task_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255), default="待分析问题")
    status: Mapped[str] = mapped_column(String(48), default="queued", index=True)
    base_sha: Mapped[str | None] = mapped_column(String(64))
    forced_reason: Mapped[str | None] = mapped_column(Text)
    branch_name: Mapped[str | None] = mapped_column(String(255))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    feedback_rating: Mapped[str | None] = mapped_column(String(32))
    feedback_reason: Mapped[str | None] = mapped_column(Text)
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_seq: Mapped[int] = mapped_column(Integer, default=0)
    execution_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 删除/重跑闸门：Worker 结束（含取消）必须写入，避免活动写入打到已删行。
    execution_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    task: Mapped[Task] = relationship(back_populates="attempts", foreign_keys=[task_id])
    steps: Mapped[list[TaskStep]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan", order_by="TaskStep.position"
    )
    events: Mapped[list[TaskEvent]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan", order_by="TaskEvent.seq"
    )
    source_capture: Mapped[SourceCapture | None] = relationship(
        back_populates="attempt", cascade="all, delete-orphan", uselist=False
    )
    change_sets: Mapped[list[ChangeSet]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )
    test_runs: Mapped[list[TestRun]] = relationship(
        back_populates="parent_attempt",
        cascade="all, delete-orphan",
        foreign_keys="TestRun.task_attempt_id",
    )


class Task(Base):
    """需求容器。执行态以 current_attempt 为准，下列执行列是投影。"""
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    # 以下执行字段是当前 Attempt 的投影，现有 /api/v1 TaskView 仍读这些列。
    title: Mapped[str] = mapped_column(String(255), default="待分析问题")
    description: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(4096))
    # ponytail: one screenshot per Task; add TaskAttachment when multi-image evidence is needed.
    image_path: Mapped[str | None] = mapped_column(String(4096))
    image_name: Mapped[str | None] = mapped_column(String(255))
    image_mime: Mapped[str | None] = mapped_column(String(64))
    image_size: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(48), default="queued", index=True)
    base_sha: Mapped[str | None] = mapped_column(String(64))
    run_attempt: Mapped[int] = mapped_column(Integer, default=0)
    event_seq: Mapped[int] = mapped_column(Integer, default=0)
    forced_reason: Mapped[str | None] = mapped_column(Text)
    branch_name: Mapped[str | None] = mapped_column(String(255))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    current_attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_attempts.id", use_alter=True, name="fk_tasks_current_attempt_id"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    repository: Mapped[Repository] = relationship(back_populates="tasks")
    current_attempt: Mapped[TaskAttempt | None] = relationship(
        foreign_keys=[current_attempt_id], post_update=True
    )
    attempts: Mapped[list[TaskAttempt]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        foreign_keys="TaskAttempt.task_id",
        order_by="TaskAttempt.attempt_no",
    )
    steps: Mapped[list[TaskStep]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TaskStep.position"
    )
    events: Mapped[list[TaskEvent]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TaskEvent.seq"
    )
    source_captures: Mapped[list[SourceCapture]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    change_sets: Mapped[list[ChangeSet]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    test_runs: Mapped[list[TestRun]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class TaskStep(Base):
    """Attempt 内阶段条。position 唯一约束挂在 Attempt 上，因为一轮 Task 可多次跑。"""
    __tablename__ = "task_steps"
    __table_args__ = (
        UniqueConstraint("task_attempt_id", "position", name="uq_task_steps_attempt_pos"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    task_attempt_id: Mapped[int | None] = mapped_column(ForeignKey("task_attempts.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    summary: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    task: Mapped[Task] = relationship(back_populates="steps")
    attempt: Mapped[TaskAttempt | None] = relationship(back_populates="steps")


class TaskEvent(Base):
    """短事件。完整思考在 artifacts/.../agent-trace.md。"""
    __tablename__ = "task_events"
    __table_args__ = (
        UniqueConstraint("task_attempt_id", "seq", name="uq_task_events_attempt_seq"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    task_attempt_id: Mapped[int | None] = mapped_column(ForeignKey("task_attempts.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[Task] = relationship(back_populates="events")
    attempt: Mapped[TaskAttempt | None] = relationship(back_populates="events")


class SourceCapture(Base):
    """问题页采集元数据。截图文件在 data root，不进库。"""
    __tablename__ = "source_captures"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    task_attempt_id: Mapped[int | None] = mapped_column(ForeignKey("task_attempts.id"), unique=True)
    requested_url: Mapped[str] = mapped_column(String(4096))
    final_url: Mapped[str | None] = mapped_column(String(4096))
    title: Mapped[str | None] = mapped_column(String(1024))
    text_content: Mapped[str | None] = mapped_column(Text)
    screenshot_path: Mapped[str | None] = mapped_column(String(4096))
    insecure_http: Mapped[bool] = mapped_column(Boolean, default=False)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[Task] = relationship(back_populates="source_captures")
    attempt: Mapped[TaskAttempt | None] = relationship(back_populates="source_capture")


class ChangeSet(Base):
    """一次虚拟修改。确认前只存在库里，不写 GitLab。"""
    __tablename__ = "change_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    task_attempt_id: Mapped[int | None] = mapped_column(ForeignKey("task_attempts.id"), index=True)
    base_sha: Mapped[str] = mapped_column(String(64))
    patch_hash: Mapped[str] = mapped_column(String(64), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    root_cause: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[Task] = relationship(back_populates="change_sets")
    attempt: Mapped[TaskAttempt | None] = relationship(back_populates="change_sets")
    files: Mapped[list[FileChange]] = relationship(
        back_populates="change_set", cascade="all, delete-orphan"
    )


class FileChange(Base):
    """单个文件的虚拟 diff。old/new 全文用于提交时组 GitLab commit。"""
    __tablename__ = "file_changes"
    __table_args__ = (UniqueConstraint("change_set_id", "path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    change_set_id: Mapped[int] = mapped_column(ForeignKey("change_sets.id"), index=True)
    path: Mapped[str] = mapped_column(String(2048))
    base_blob_sha: Mapped[str] = mapped_column(String(64))
    old_content: Mapped[str] = mapped_column(Text)
    new_content: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    unified_diff: Mapped[str] = mapped_column(Text)
    hunks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    change_set: Mapped[ChangeSet] = relationship(back_populates="files")


class TestRun(Base):
    """临时脚本执行记录。"""

    __tablename__ = "test_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    task_attempt_id: Mapped[int | None] = mapped_column(ForeignKey("task_attempts.id"), index=True)
    change_set_id: Mapped[int | None] = mapped_column(ForeignKey("change_sets.id"))
    # 同一次 Attempt 内的验证重试次数，不是 TaskAttempt.attempt_no。
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    command: Mapped[list[str]] = mapped_column(JSON, default=list)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    output: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[Task] = relationship(back_populates="test_runs")
    parent_attempt: Mapped[TaskAttempt | None] = relationship(
        back_populates="test_runs", foreign_keys=[task_attempt_id]
    )


class BrowserAuthProfile(Base):
    """按 origin 共享的页面登录态，Fernet 加密。解密密钥是 data_root/.secret-key。"""
    __tablename__ = "browser_auth_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    origin: Mapped[str] = mapped_column(String(2048), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    encrypted_state: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
