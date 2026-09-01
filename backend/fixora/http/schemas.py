from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from ..models import Task, TaskAttempt


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class GitLabSettingsInput(BaseModel):
    base_url: HttpUrl
    token: str | None = Field(default=None, min_length=1)
    ca_bundle: str | None = None


class ModelSettingsInput(BaseModel):
    api_url: HttpUrl
    api_key: str | None = Field(default=None, min_length=1)
    api_mode: Literal["responses", "chat_completions"]
    model: str = Field(min_length=1, max_length=255)
    reasoning_effort: Literal["none", "low", "medium", "high"] = "medium"
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_reserved_parameters(self) -> ModelSettingsInput:
        reserved = {"model", "api_key", "base_url", "tools", "messages", "input", "stream"}
        overlap = reserved.intersection(self.parameters)
        if overlap:
            raise ValueError(f"parameters 不允许覆盖: {', '.join(sorted(overlap))}")
        return self


class BrowserSettingsInput(BaseModel):
    timeout_seconds: int = Field(default=30, ge=5, le=180)
    scroll_limit_px: int = Field(default=20_000, ge=1_000, le=100_000)


class SettingsStatus(BaseModel):
    configured: bool
    values: dict[str, Any] = Field(default_factory=dict)


class RepositoryCreate(BaseModel):
    gitlab_project_id: int


class RuntimeProfileInput(BaseModel):
    language: Literal["node", "python"]
    runtime_version: str = ""
    package_manager: str = ""
    working_directory: str = "."
    install_argv: list[str]
    test_argv: list[str]


class RuntimeProfileView(RuntimeProfileInput, ORMModel):
    lockfile_path: str | None = None
    lockfile_hash: str | None = None


class RepositoryView(ORMModel):
    id: int
    gitlab_project_id: int
    name: str
    path_with_namespace: str
    default_branch: str
    cached_sha: str | None
    cache_status: str
    last_fetch_at: datetime | None
    runtime_profile: RuntimeProfileView | None = None


class TaskCreate(BaseModel):
    repository_id: int
    description: str = Field(default="", max_length=30_000)
    source_url: HttpUrl | None = None
    image_data_url: str | None = Field(default=None, max_length=12_000_000)
    image_name: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def require_description_or_image(self) -> TaskCreate:
        if len(self.description.strip()) < 3 and not self.image_data_url:
            raise ValueError("请填写至少 3 个字符的问题描述，或上传问题截图")
        return self


class ApprovalInput(BaseModel):
    change_set_id: int
    patch_hash: str = Field(min_length=64, max_length=64)
    reason: str | None = Field(default=None, max_length=2_000)


class FeedbackInput(BaseModel):
    rating: Literal["perfect", "partial", "incorrect"]
    reason: str = Field(default="", max_length=4_000)

    @model_validator(mode="after")
    def require_incorrect_reason(self) -> FeedbackInput:
        if self.rating == "incorrect" and len(self.reason.strip()) < 3:
            raise ValueError("选择“修复错误”时必须填写具体原因")
        return self


class FeedbackView(ORMModel):
    rating: Literal["perfect", "partial", "incorrect"]
    reason: str
    submitted_at: datetime


class BrowserAuthInput(BaseModel):
    raw: str = Field(min_length=1)
    origin: str | None = None


class BrowserAuthView(ORMModel):
    id: int
    origin: str
    kind: str
    updated_at: datetime


class TaskEventView(ORMModel):
    seq: int
    type: str
    payload: dict[str, Any]
    created_at: datetime


class FileChangeView(ORMModel):
    id: int
    path: str
    reason: str
    hunks: list[dict[str, Any]]


class ChangeSetView(ORMModel):
    id: int
    base_sha: str
    patch_hash: str
    summary: str
    root_cause: str
    status: str
    files: list[FileChangeView]


class TestRunView(ORMModel):
    id: int
    attempt: int
    status: str
    command: list[str]
    exit_code: int | None
    output: str
    duration_ms: int | None


class TaskAttemptSummary(ORMModel):
    attempt_no: int
    title: str
    status: str
    branch_name: str | None = None
    commit_sha: str | None = None
    error: str | None = None
    created_at: datetime
    execution_finished_at: datetime | None = None


class TaskView(ORMModel):
    id: int
    repository_id: int
    title: str
    description: str
    source_url: str | None
    image_name: str | None = None
    image_mime: str | None = None
    image_size: int | None = None
    image_url: str | None = None
    status: str
    base_sha: str | None
    branch_name: str | None
    commit_sha: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    repository: RepositoryView
    change_sets: list[ChangeSetView] = Field(default_factory=list)
    test_runs: list[TestRunView] = Field(default_factory=list)
    attempt_no: int = 1  # 本响应所查看的 Attempt
    current_attempt_no: int = 1  # Task 当前指向的 Attempt
    attempts: list[TaskAttemptSummary] = Field(default_factory=list)
    feedback: FeedbackView | None = None


def task_view(task: Task, attempt: TaskAttempt | None = None) -> TaskView:
    """默认投影当前 Attempt；传入 attempt 则查看历史。"""
    viewed = attempt or task.current_attempt
    current = task.current_attempt
    viewed_id = viewed.id if viewed else None
    change_sets = [
        item for item in task.change_sets if viewed_id is None or item.task_attempt_id == viewed_id
    ]
    test_runs = [
        item for item in task.test_runs if viewed_id is None or item.task_attempt_id == viewed_id
    ]
    source = viewed or task
    return TaskView(
        id=task.id,
        repository_id=task.repository_id,
        title=source.title,
        description=task.description,
        source_url=task.source_url,
        image_name=task.image_name,
        image_mime=task.image_mime,
        image_size=task.image_size,
        image_url=f"/api/v1/tasks/{task.id}/input-image" if task.image_path else None,
        status=source.status,
        base_sha=source.base_sha,
        branch_name=source.branch_name,
        commit_sha=source.commit_sha,
        error=source.error,
        created_at=task.created_at,
        updated_at=task.updated_at,
        repository=task.repository,
        change_sets=change_sets,
        test_runs=test_runs,
        attempt_no=viewed.attempt_no if viewed else 1,
        current_attempt_no=current.attempt_no if current else 1,
        attempts=[
            TaskAttemptSummary.model_validate(item)
            for item in sorted(task.attempts, key=lambda item: item.attempt_no)
        ],
        feedback=(
            FeedbackView(
                rating=viewed.feedback_rating,
                reason=viewed.feedback_reason or "",
                submitted_at=viewed.feedback_at,
            )
            if viewed.feedback_rating and viewed.feedback_at
            else None
        ),
    )
