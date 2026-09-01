"""HTTP 路由。路径、状态、事件契约见 `protocol.py`。"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from openai import AsyncOpenAI
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, selectinload

from ..browser.auth import BrowserAuthError, normalize_auth_import
from ..config import get_settings
from ..crypto import encrypt_json
from ..db import SessionLocal, get_db
from ..gitlab import GitLabError, project_path_from_url
from ..models import (
    BrowserAuthProfile,
    ChangeSet,
    Repository,
    RepositoryRuntimeProfile,
    Task,
    TaskAttempt,
    TaskEvent,
    utcnow,
)
from ..paths import resolve_artifact_file
from ..repo.runtime import detect_runtime
from ..settings_store import SettingsStore, public_settings
from ..task_image import TaskImageError, decode_image_data_url, save_task_image
from ..tasks.attempts import (
    WAITING_STATUSES,
    create_attempt,
    is_active_status,
    mark_attempt_finished,
    sync_task_projection,
)
from ..tasks.events import emit_event
from ..tasks.worker import commit_attempt_actor, run_attempt_actor
from ..tasks.workflow import gitlab_client, load_task, repository_cache, sync_repository
from .schemas import (
    ApprovalInput,
    BrowserAuthInput,
    BrowserAuthView,
    BrowserSettingsInput,
    FeedbackInput,
    FeedbackView,
    RepositoryCreate,
    RepositoryView,
    RuntimeProfileInput,
    SettingsStatus,
    TaskCreate,
    TaskEventView,
    TaskView,
    task_view,
)

router = APIRouter(prefix="/api/v1")


def _task_query():
    return select(Task).options(
        selectinload(Task.repository).selectinload(Repository.runtime_profile),
        selectinload(Task.attempts),
        selectinload(Task.current_attempt),
        selectinload(Task.change_sets).selectinload(ChangeSet.files),
        selectinload(Task.test_runs),
        selectinload(Task.source_captures),
    )


def _enqueue_or_fail(db: Session, task: Task, actor: Any, attempt_id: int) -> None:
    """入队失败必须把 Attempt 标失败并写 finished，否则删除闸门会一直卡住。"""
    try:
        actor.send(attempt_id)
    except Exception as exc:
        attempt = db.get(TaskAttempt, attempt_id)
        error = f"Redis 入队失败: {exc}"
        if attempt is not None:
            attempt.status = "failed"
            attempt.error = error
            mark_attempt_finished(attempt)
            sync_task_projection(task, attempt)
        else:
            task.status = "failed"
            task.error = error
        emit_event(db, task, "task.failed", {"error": error})
        db.commit()
        raise HTTPException(503, "任务队列暂不可用") from exc


def _attempt_by_no(task: Task, attempt_no: int) -> TaskAttempt:
    for item in task.attempts:
        if item.attempt_no == attempt_no:
            return item
    raise HTTPException(404, "Attempt 不存在")


def _current_attempt(task: Task) -> TaskAttempt:
    if task.current_attempt is None:
        raise HTTPException(409, "Task 没有当前 Attempt")
    return task.current_attempt


@router.get("/health")
def health() -> dict[str, str]:
    """存活探测。"""
    return {"status": "ok"}


@router.get("/settings/{section}", response_model=SettingsStatus)
def get_setting(section: str, db: Session = Depends(get_db)) -> SettingsStatus:
    """gitlab/model 只读环境变量；browser 读库。密钥字段脱敏。"""
    if section not in {"gitlab", "model", "browser"}:
        raise HTTPException(404)
    if section == "model":
        value = get_settings().model_runtime_config()
        return SettingsStatus(
            configured=bool(value.get("api_url") and value.get("api_key") and value.get("model")),
            values=public_settings(value, secrets={"api_key"}),
        )
    if section == "gitlab":
        value = get_settings().gitlab_runtime_config()
        return SettingsStatus(
            configured=bool(value.get("base_url") and value.get("token")),
            values=public_settings(value, secrets={"token"}),
        )
    secrets: set[str] = set()
    value = SettingsStore(db).get(section)
    return SettingsStatus(configured=bool(value), values=public_settings(value, secrets=secrets))


@router.post("/settings/gitlab/test")
def test_gitlab(db: Session = Depends(get_db)) -> dict[str, Any]:
    """用环境变量中的 GitLab token 打 /user。"""
    try:
        client = gitlab_client(db)
        try:
            user = client.current_user()
        finally:
            client.close()
    except (GitLabError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "username": user.get("username")}


@router.post("/settings/model/test")
async def test_model() -> dict[str, Any]:
    """用环境变量中的网关配置发一条最小请求。"""
    settings = get_settings()
    value = settings.model_runtime_config()
    if not value.get("api_url") or not value.get("api_key") or not value.get("model"):
        raise HTTPException(400, "模型环境变量尚未配置")
    http_client = httpx.AsyncClient(verify=settings.model_http_verify())
    client = AsyncOpenAI(
        api_key=value["api_key"],
        base_url=str(value.get("base_url") or value["api_url"]),
        http_client=http_client,
    )
    try:
        if value["api_mode"] == "responses":
            response = await client.responses.create(
                model=value["model"], input="Reply with OK only."
            )
            output = response.output_text
        else:
            response = await client.chat.completions.create(
                model=value["model"], messages=[{"role": "user", "content": "Reply with OK only."}]
            )
            output = response.choices[0].message.content
    except Exception as exc:
        raise HTTPException(400, f"模型连接失败: {exc}") from exc
    finally:
        await http_client.aclose()
    return {"ok": True, "output": output}


@router.patch("/settings/browser", response_model=SettingsStatus)
def patch_browser(payload: BrowserSettingsInput, db: Session = Depends(get_db)) -> SettingsStatus:
    """唯一可写设置。GitLab / 模型密钥不能从这里改。"""
    value = payload.model_dump()
    SettingsStore(db).put("browser", value)
    db.commit()
    return SettingsStatus(configured=True, values=value)


@router.get("/browser-auth-profiles", response_model=list[BrowserAuthView])
def list_browser_auth(db: Session = Depends(get_db)) -> list[BrowserAuthProfile]:
    """共享页面登录态列表，不含密文。"""
    return list(db.scalars(select(BrowserAuthProfile).order_by(BrowserAuthProfile.origin)))


@router.post("/browser-auth-profiles", response_model=BrowserAuthView)
def create_browser_auth(
    payload: BrowserAuthInput, db: Session = Depends(get_db)
) -> BrowserAuthProfile:
    """导入 Cookie / storage JSON，按 origin upsert。"""
    try:
        kind, state = normalize_auth_import(payload.raw, payload.origin)
    except BrowserAuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    origin = str(state["origin"])
    profile = db.scalar(select(BrowserAuthProfile).where(BrowserAuthProfile.origin == origin))
    if profile is None:
        profile = BrowserAuthProfile(origin=origin, kind=kind, encrypted_state="")
        db.add(profile)
    profile.kind = kind
    profile.encrypted_state = encrypt_json(state)
    db.commit()
    db.refresh(profile)
    return profile


@router.delete("/browser-auth-profiles/{profile_id}", status_code=204)
def delete_browser_auth(profile_id: int, db: Session = Depends(get_db)) -> Response:
    """删除一条共享登录态。"""
    profile = db.get(BrowserAuthProfile, profile_id)
    if profile is None:
        raise HTTPException(404)
    db.delete(profile)
    db.commit()
    return Response(status_code=204)


@router.get("/repositories/discover")
def discover_repositories(search: str = "", db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """GitLab 搜索，或粘贴项目 URL 精确解析。"""
    try:
        client = gitlab_client(db)
        try:
            project_path = project_path_from_url(search, get_settings().gitlab_base_url)
            projects = (
                [client.get_project(project_path)] if project_path else client.list_projects(search)
            )
        finally:
            client.close()
    except (GitLabError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    existing = set(db.scalars(select(Repository.gitlab_project_id)))
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "path_with_namespace": item["path_with_namespace"],
            "default_branch": item.get("default_branch"),
            "added": item["id"] in existing,
        }
        for item in projects
    ]


@router.get("/repositories", response_model=list[RepositoryView])
def list_repositories(db: Session = Depends(get_db)) -> list[Repository]:
    """已接入仓库。"""
    return list(
        db.scalars(
            select(Repository)
            .options(selectinload(Repository.runtime_profile))
            .order_by(Repository.path_with_namespace)
        )
    )


@router.post("/repositories", response_model=RepositoryView, status_code=201)
def create_repository(payload: RepositoryCreate, db: Session = Depends(get_db)) -> Repository:
    """按 GitLab project id 接入；已存在则返回原行。"""
    existing = db.scalar(
        select(Repository).where(Repository.gitlab_project_id == payload.gitlab_project_id)
    )
    if existing:
        return existing
    try:
        client = gitlab_client(db)
        try:
            project = client.get_project(payload.gitlab_project_id)
        finally:
            client.close()
    except (GitLabError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if not project.get("default_branch") or not project.get("http_url_to_repo"):
        raise HTTPException(400, "仓库缺少默认分支或 HTTP clone URL")
    clone_url = str(project["http_url_to_repo"])
    parsed_clone = urlparse(clone_url)
    if (
        parsed_clone.username
        or parsed_clone.password
        or parsed_clone.scheme not in {"http", "https"}
    ):
        raise HTTPException(400, "clone URL 必须是无内嵌凭据的 HTTP(S) 地址")
    repository = Repository(
        gitlab_project_id=project["id"],
        name=project["name"],
        path_with_namespace=project["path_with_namespace"],
        clone_url=clone_url,
        default_branch=project["default_branch"],
    )
    db.add(repository)
    db.commit()
    db.refresh(repository)
    return repository


@router.post("/repositories/{repository_id}/fetch", response_model=RepositoryView)
def fetch_repository(repository_id: int, db: Session = Depends(get_db)) -> Repository:
    """fetch 默认分支到本地 bare cache。"""
    repository = db.get(Repository, repository_id)
    if repository is None:
        raise HTTPException(404)
    try:
        sync_repository(db, repository)
    except Exception as exc:
        repository.cache_status = "failed"
        db.commit()
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    db.refresh(repository)
    return repository


@router.post("/repositories/{repository_id}/detect-runtime", response_model=RepositoryView)
def detect_repository_runtime(repository_id: int, db: Session = Depends(get_db)) -> Repository:
    """从 lockfile / package 元数据推断语言和测试命令。"""
    repository = db.get(Repository, repository_id)
    if repository is None:
        raise HTTPException(404)
    sha = repository.cached_sha or sync_repository(db, repository)
    try:
        detected = detect_runtime(repository_cache(db), repository.id, sha)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    runtime = repository.runtime_profile or RepositoryRuntimeProfile(repository_id=repository.id)
    runtime.language = detected.language
    runtime.runtime_version = detected.runtime_version
    runtime.package_manager = detected.package_manager
    runtime.working_directory = detected.working_directory
    runtime.install_argv = detected.install_argv
    runtime.test_argv = detected.test_argv
    runtime.lockfile_path = detected.lockfile_path
    runtime.lockfile_hash = detected.lockfile_hash
    db.add(runtime)
    db.commit()
    db.refresh(repository)
    return repository


@router.patch("/repositories/{repository_id}/runtime", response_model=RepositoryView)
def patch_repository_runtime(
    repository_id: int,
    payload: RuntimeProfileInput,
    db: Session = Depends(get_db),
) -> Repository:
    """人工覆盖自动探测结果。"""
    repository = db.get(Repository, repository_id)
    if repository is None:
        raise HTTPException(404)
    runtime = repository.runtime_profile or RepositoryRuntimeProfile(repository_id=repository.id)
    for key, value in payload.model_dump().items():
        setattr(runtime, key, value)
    db.add(runtime)
    db.commit()
    db.refresh(repository)
    return repository


@router.post("/tasks", response_model=TaskView, status_code=202)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)) -> TaskView:
    """建 Task 和 Attempt 1，入队分析。确认前不写 GitLab。"""
    repository = db.get(Repository, payload.repository_id)
    if repository is None:
        raise HTTPException(404, "仓库不存在")
    try:
        image = (
            decode_image_data_url(payload.image_data_url, payload.image_name)
            if payload.image_data_url
            else None
        )
    except TaskImageError as exc:
        raise HTTPException(422, str(exc)) from exc
    task = Task(
        repository_id=payload.repository_id,
        description=payload.description.strip() or "请根据附加截图定位并修复问题。",
        source_url=str(payload.source_url) if payload.source_url else None,
    )
    db.add(task)
    db.flush()
    if image is not None:
        image_path = save_task_image(get_settings().artifact_root, task.id, image)
        task.image_path = str(image_path)
        task.image_name = image.name
        task.image_mime = image.mime
        task.image_size = len(image.content)
    attempt = create_attempt(db, task, 1)
    db.commit()
    _enqueue_or_fail(db, task, run_attempt_actor, attempt.id)
    return task_view(load_task(db, task.id))


@router.get("/tasks/{task_id}/input-image")
def get_task_input_image(task_id: int, db: Session = Depends(get_db)) -> FileResponse:
    """返回 Task 级用户上传截图；同一张图跨 Attempt 复用。"""
    task = db.get(Task, task_id)
    if task is None or not task.image_path:
        raise HTTPException(404)
    path = Path(task.image_path).resolve()
    try:
        path.relative_to(get_settings().artifact_root.resolve())
    except ValueError:
        raise HTTPException(404) from None
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type=task.image_mime or "application/octet-stream")


@router.get("/tasks", response_model=list[TaskView])
def list_tasks(limit: int = 50, db: Session = Depends(get_db)) -> list[TaskView]:
    """最近任务，每条投影当前 Attempt。"""
    return [
        task_view(item)
        for item in db.scalars(_task_query().order_by(desc(Task.created_at)).limit(min(limit, 100)))
    ]


@router.get("/tasks/{task_id}", response_model=TaskView)
def get_task(task_id: int, db: Session = Depends(get_db)) -> TaskView:
    """当前 Attempt 的详情。历史 Attempt 走 /attempts/{n}。"""
    try:
        return task_view(load_task(db, task_id))
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc


def _screenshot_path(task: Task, attempt: TaskAttempt) -> Path | None:
    capture = next(
        (item for item in task.source_captures if item.task_attempt_id == attempt.id),
        None,
    )
    if capture and capture.screenshot_path:
        path = Path(capture.screenshot_path)
        if path.is_file():
            return path
    return resolve_artifact_file(
        get_settings().artifact_root, task.id, attempt.attempt_no, "source-page.png"
    )


def _trace_path(task: Task, attempt: TaskAttempt) -> Path | None:
    return resolve_artifact_file(
        get_settings().artifact_root, task.id, attempt.attempt_no, "agent-trace.md"
    )


@router.get("/tasks/{task_id}/source-screenshot")
def get_source_screenshot(task_id: int, db: Session = Depends(get_db)) -> FileResponse:
    """当前 Attempt 的问题页截图。"""
    try:
        task = load_task(db, task_id)
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    attempt = task.current_attempt
    if attempt is None:
        raise HTTPException(404)
    path = _screenshot_path(task, attempt)
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png")


@router.get("/tasks/{task_id}/agent-trace")
def get_agent_trace(task_id: int, db: Session = Depends(get_db)) -> FileResponse:
    """当前 Attempt 的完整轨迹；事件流里只有短预览。"""
    try:
        task = load_task(db, task_id)
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    attempt = task.current_attempt
    if attempt is None:
        raise HTTPException(404)
    path = _trace_path(task, attempt)
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, media_type="text/markdown; charset=utf-8")


@router.get("/tasks/{task_id}/attempts/{attempt_no}", response_model=TaskView)
def get_task_attempt(task_id: int, attempt_no: int, db: Session = Depends(get_db)) -> TaskView:
    """按编号查看某次 Attempt；历史只读。"""
    try:
        task = load_task(db, task_id)
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    return task_view(task, _attempt_by_no(task, attempt_no))


@router.get("/tasks/{task_id}/attempts/{attempt_no}/events", response_model=list[TaskEventView])
def get_task_attempt_events(
    task_id: int, attempt_no: int, db: Session = Depends(get_db)
) -> list[TaskEvent]:
    """该 Attempt 的全量事件（含过程）。"""
    try:
        task = load_task(db, task_id)
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    attempt = _attempt_by_no(task, attempt_no)
    return list(
        db.scalars(
            select(TaskEvent).where(TaskEvent.task_attempt_id == attempt.id).order_by(TaskEvent.seq)
        )
    )


@router.get("/tasks/{task_id}/attempts/{attempt_no}/source-screenshot")
def get_attempt_screenshot(
    task_id: int, attempt_no: int, db: Session = Depends(get_db)
) -> FileResponse:
    """指定 Attempt 的问题页截图。"""
    try:
        task = load_task(db, task_id)
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    path = _screenshot_path(task, _attempt_by_no(task, attempt_no))
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png")


@router.get("/tasks/{task_id}/attempts/{attempt_no}/agent-trace")
def get_attempt_trace(task_id: int, attempt_no: int, db: Session = Depends(get_db)) -> FileResponse:
    """指定 Attempt 的轨迹文件。"""
    try:
        task = load_task(db, task_id)
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    path = _trace_path(task, _attempt_by_no(task, attempt_no))
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, media_type="text/markdown; charset=utf-8")


@router.put(
    "/tasks/{task_id}/attempts/{attempt_no}/feedback",
    response_model=FeedbackView,
)
def submit_attempt_feedback(
    task_id: int,
    attempt_no: int,
    payload: FeedbackInput,
    db: Session = Depends(get_db),
) -> FeedbackView:
    """保存用户对一次修复结果的真实评价；活动 Attempt 不允许评价。"""
    try:
        task = load_task(db, task_id)
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    attempt = _attempt_by_no(task, attempt_no)
    if is_active_status(attempt.status):
        raise HTTPException(409, "修复仍在执行，完成后才能评价")
    attempt.feedback_rating = payload.rating
    attempt.feedback_reason = payload.reason.strip()
    attempt.feedback_at = utcnow()
    db.commit()
    return FeedbackView(
        rating=attempt.feedback_rating,
        reason=attempt.feedback_reason,
        submitted_at=attempt.feedback_at,
    )


def _validate_approval(task: Task, payload: ApprovalInput, forced: bool) -> ChangeSet:
    """审批必须钉住当前 Attempt 的 change_set_id + patch_hash，避免批到过期 diff。"""
    expected_status = "awaiting_force_approval" if forced else "awaiting_approval"
    if task.status != expected_status:
        raise HTTPException(409, f"任务状态不是 {expected_status}")
    attempt = _current_attempt(task)
    change_set = next(
        (
            item
            for item in task.change_sets
            if item.id == payload.change_set_id and item.task_attempt_id == attempt.id
        ),
        None,
    )
    if change_set is None or change_set.patch_hash != payload.patch_hash:
        raise HTTPException(409, "变更已过期，请刷新后重新确认")
    if forced and not (payload.reason or "").strip():
        raise HTTPException(422, "强制提交必须填写原因")
    return change_set


@router.post("/tasks/{task_id}/approve", status_code=202)
def approve_task(
    task_id: int, payload: ApprovalInput, db: Session = Depends(get_db)
) -> dict[str, str]:
    """通过虚拟变更并入队提交。SSE 在 committing 期间继续收事件。"""
    task = load_task(db, task_id)
    change_set = _validate_approval(task, payload, False)
    attempt = _current_attempt(task)
    change_set.status = "approved"
    attempt.status = "committing"
    attempt.execution_finished_at = None
    sync_task_projection(task, attempt)
    emit_event(db, task, "approval.approved", {"change_set_id": change_set.id}, attempt=attempt)
    db.commit()
    _enqueue_or_fail(db, task, commit_attempt_actor, attempt.id)
    return {"status": "accepted"}


@router.post("/tasks/{task_id}/force-approve", status_code=202)
def force_approve_task(
    task_id: int, payload: ApprovalInput, db: Session = Depends(get_db)
) -> dict[str, str]:
    """验证未通过时强制提交，reason 必填。"""
    task = load_task(db, task_id)
    change_set = _validate_approval(task, payload, True)
    attempt = _current_attempt(task)
    change_set.status = "approved"
    attempt.forced_reason = payload.reason.strip() if payload.reason else None
    attempt.status = "committing"
    attempt.execution_finished_at = None
    sync_task_projection(task, attempt)
    emit_event(
        db,
        task,
        "approval.force_approved",
        {"change_set_id": change_set.id, "reason": attempt.forced_reason},
        attempt=attempt,
    )
    db.commit()
    _enqueue_or_fail(db, task, commit_attempt_actor, attempt.id)
    return {"status": "accepted"}


@router.post("/tasks/{task_id}/reject")
def reject_task(task_id: int, db: Session = Depends(get_db)) -> dict[str, str]:
    """拒绝当前 Attempt 的变更，不建分支。"""
    try:
        task = load_task(db, task_id)
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    attempt = _current_attempt(task)
    if attempt.status not in WAITING_STATUSES:
        raise HTTPException(409, "当前状态不能拒绝")
    attempt.status = "rejected"
    mark_attempt_finished(attempt)
    sync_task_projection(task, attempt)
    emit_event(db, task, "approval.rejected", {}, attempt=attempt)
    db.commit()
    return {"status": "rejected"}


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: int, db: Session = Depends(get_db)) -> dict[str, str]:
    """请求取消。execution_finished_at 要等 Worker 退出后才写。"""
    try:
        task = load_task(db, task_id)
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    attempt = _current_attempt(task)
    if not is_active_status(attempt.status):
        raise HTTPException(409, "任务已结束")
    attempt.status = "cancelled"
    sync_task_projection(task, attempt)
    emit_event(db, task, "task.cancelled", {}, attempt=attempt)
    db.commit()
    return {"status": "cancelled"}


@router.post("/tasks/{task_id}/rerun", response_model=TaskView, status_code=202)
def rerun_task(task_id: int, db: Session = Depends(get_db)) -> TaskView:
    """新 Attempt。活动中 409；等待审批的旧 Attempt 标 superseded。"""
    task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise HTTPException(404)
    current = db.get(TaskAttempt, task.current_attempt_id) if task.current_attempt_id else None
    if current is None:
        raise HTTPException(409, "没有可重跑的 Attempt")
    if is_active_status(current.status):
        raise HTTPException(409, "当前 Attempt 仍在运行")
    if current.status in WAITING_STATUSES:
        current.status = "superseded"
        mark_attempt_finished(current)
    max_no = (
        db.scalar(select(func.max(TaskAttempt.attempt_no)).where(TaskAttempt.task_id == task.id))
        or 0
    )
    attempt = create_attempt(db, task, max_no + 1)
    db.commit()
    _enqueue_or_fail(db, task, run_attempt_actor, attempt.id)
    return task_view(load_task(db, task.id))


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db)) -> Response:
    """先删本地产物再删库行。失败返回 500 且库仍在。不碰 GitLab。"""
    task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise HTTPException(404)
    current = db.get(TaskAttempt, task.current_attempt_id) if task.current_attempt_id else None
    if current is None or current.execution_finished_at is None or is_active_status(current.status):
        raise HTTPException(409, "活动任务不能删除")
    artifact = get_settings().artifact_root / f"task-{task_id}"
    try:
        if artifact.exists():
            shutil.rmtree(artifact)
        workspace_root = Path("/tmp/fixora")
        if workspace_root.is_dir():
            for path in workspace_root.iterdir():
                if path.name == f"task-{task_id}" or path.name.startswith(f"task-{task_id}-a"):
                    shutil.rmtree(path, ignore_errors=True)
    except OSError as exc:
        raise HTTPException(500, f"删除本地产物失败: {exc}") from exc
    db.delete(task)
    db.commit()
    return Response(status_code=204)


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: int, request: Request) -> StreamingResponse:
    """当前 Attempt 的 SSE。awaiting_* 不是终点，批准后还要收 committing 事件。"""
    header = request.headers.get("last-event-id")
    try:
        after_seq = max(int(header or request.query_params.get("after", "0")), 0)
    except ValueError:
        after_seq = 0

    async def stream() -> AsyncIterator[str]:
        cursor = after_seq
        idle = 0
        while not await request.is_disconnected():
            with SessionLocal() as db:
                task = db.get(Task, task_id)
                if task is None:
                    yield 'event: error\ndata: {"error":"task not found"}\n\n'
                    return
                attempt_id = task.current_attempt_id
                events = (
                    list(
                        db.scalars(
                            select(TaskEvent)
                            .where(
                                TaskEvent.task_attempt_id == attempt_id,
                                TaskEvent.seq > cursor,
                            )
                            .order_by(TaskEvent.seq)
                            .limit(100)
                        )
                    )
                    if attempt_id
                    else []
                )
                terminal = task.status in {
                    "completed",
                    "rejected",
                    "failed",
                    "cancelled",
                    "superseded",
                }
            if events:
                idle = 0
                for event in events:
                    cursor = event.seq
                    yield (
                        f"id: {event.seq}\n"
                        f"event: {event.type}\n"
                        f"data: {json.dumps(event.payload, ensure_ascii=False)}\n\n"
                    )
            else:
                idle += 1
                if idle % 15 == 0:
                    yield ": keepalive\n\n"
                if terminal:
                    return
                await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")
