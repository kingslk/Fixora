from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..agent.runtime import AgentSummary, FixoraAgent
from ..browser.auth import state_for_url
from ..browser.capture import CaptureError, PageCaptureService
from ..config import get_settings
from ..crypto import decrypt_json
from ..gitlab import GitLabClient, GitLabError
from ..gitlab.comment import commit_comment_body
from ..models import (
    BrowserAuthProfile,
    ChangeSet,
    FileChange,
    Repository,
    RepositoryRuntimeProfile,
    SourceCapture,
    Task,
    TaskAttempt,
    TaskStep,
    TestRun,
    utcnow,
)
from ..paths import artifact_dir
from ..repo.cache import RepositoryCache
from ..repo.runner import LocalTestService, TestResult
from ..repo.runtime import detect_runtime
from ..settings_store import SettingsStore
from .attempts import is_active_status, mark_attempt_finished, sync_task_projection
from .events import emit_event

STEP_KINDS = ("capture", "sync", "analyze", "test", "approval", "commit")


class WorkflowError(RuntimeError):
    pass


def require_attempt(task: Task, attempt_id: int | None = None) -> TaskAttempt:
    if attempt_id is not None:
        for item in task.attempts:
            if item.id == attempt_id:
                return item
        raise WorkflowError(f"TaskAttempt {attempt_id} 不存在")
    if task.current_attempt is None:
        raise WorkflowError(f"Task {task.id} 没有当前 Attempt")
    return task.current_attempt


def load_task(db: Session, task_id: int) -> Task:
    task = db.scalar(
        select(Task)
        .where(Task.id == task_id)
        .options(
            selectinload(Task.repository).selectinload(Repository.runtime_profile),
            selectinload(Task.attempts),
            selectinload(Task.current_attempt),
            selectinload(Task.change_sets).selectinload(ChangeSet.files),
            selectinload(Task.test_runs),
            selectinload(Task.source_captures),
        )
    )
    if task is None:
        raise WorkflowError(f"Task {task_id} 不存在")
    return task


def create_steps(db: Session, task: Task, attempt: TaskAttempt) -> None:
    exists = db.scalar(select(TaskStep.id).where(TaskStep.task_attempt_id == attempt.id).limit(1))
    if exists:
        return
    for position, kind in enumerate(STEP_KINDS, start=1):
        db.add(
            TaskStep(
                task_id=task.id,
                task_attempt_id=attempt.id,
                position=position,
                kind=kind,
            )
        )
    db.flush()


def ensure_not_cancelled(db: Session, attempt_id: int) -> None:
    status = db.scalar(select(TaskAttempt.status).where(TaskAttempt.id == attempt_id))
    if status == "cancelled":
        raise WorkflowError("任务已取消")


def set_step(
    db: Session,
    task: Task,
    kind: str,
    status: str,
    summary: str,
    detail: dict[str, Any] | None = None,
    *,
    attempt: TaskAttempt | None = None,
) -> None:
    target = attempt or task.current_attempt
    if target is None:
        return
    step = db.scalar(
        select(TaskStep).where(TaskStep.task_attempt_id == target.id, TaskStep.kind == kind)
    )
    if step is None:
        return
    step.status = status
    step.summary = summary
    step.detail = detail or {}
    if status == "running" and step.started_at is None:
        step.started_at = utcnow()
    if status in {"completed", "failed", "waiting"}:
        step.finished_at = utcnow() if status != "waiting" else None
    emit_event(
        db,
        task,
        f"step.{status}",
        {"kind": kind, "summary": summary, **(detail or {})},
        attempt=target,
    )


def gitlab_client(db: Session) -> GitLabClient:
    value = get_settings().gitlab_runtime_config()
    if not value or not value.get("base_url") or not value.get("token"):
        raise WorkflowError("GitLab 环境变量尚未配置")
    return GitLabClient(
        str(value["base_url"]),
        str(value["token"]),
        ssl_verify=bool(value.get("ssl_verify", False)),
        ca_bundle=str(value["ca_bundle"]) if value.get("ca_bundle") else None,
    )


def repository_cache(db: Session) -> RepositoryCache:
    value = get_settings().gitlab_runtime_config()
    if not value or not value.get("token"):
        raise WorkflowError("GitLab 环境变量尚未配置")
    return RepositoryCache(
        get_settings().git_root,
        str(value["token"]),
        str(value["ca_bundle"]) if value.get("ca_bundle") else None,
        ssl_verify=bool(value.get("ssl_verify", False)),
    )


def sync_repository(db: Session, repository: Repository) -> str:
    client = gitlab_client(db)
    cache = repository_cache(db)
    try:
        api_sha = client.get_branch_sha(repository.gitlab_project_id, repository.default_branch)
        cached_sha = cache.sync(
            repository.id,
            repository.clone_url,
            repository.default_branch,
        )
    finally:
        client.close()
    if api_sha != cached_sha:
        raise WorkflowError("GitLab API SHA 与 bare cache SHA 不一致")
    repository.cached_sha = cached_sha
    repository.cache_status = "ready"
    repository.last_fetch_at = datetime.now(UTC)
    db.flush()
    return cached_sha


def ensure_runtime(db: Session, repository: Repository, sha: str) -> RepositoryRuntimeProfile:
    if repository.runtime_profile is not None:
        return repository.runtime_profile
    detected = detect_runtime(repository_cache(db), repository.id, sha)
    runtime = RepositoryRuntimeProfile(
        repository_id=repository.id,
        language=detected.language,
        runtime_version=detected.runtime_version,
        package_manager=detected.package_manager,
        working_directory=detected.working_directory,
        install_argv=detected.install_argv,
        test_argv=detected.test_argv,
        lockfile_path=detected.lockfile_path,
        lockfile_hash=detected.lockfile_hash,
    )
    db.add(runtime)
    db.flush()
    repository.runtime_profile = runtime
    return runtime


async def capture_source(db: Session, task: Task, attempt: TaskAttempt) -> str:
    if not task.source_url:
        return ""
    profiles = [decrypt_json(row.encrypted_state) for row in db.scalars(select(BrowserAuthProfile))]
    storage_state = state_for_url(profiles, task.source_url)
    browser_config = SettingsStore(db).get("browser") or {}
    settings = get_settings()
    service = PageCaptureService(
        artifact_root=settings.artifact_root,
        timeout_seconds=int(
            browser_config.get("timeout_seconds", settings.browser_timeout_seconds)
        ),
        scroll_limit_px=int(
            browser_config.get("scroll_limit_px", settings.browser_scroll_limit_px)
        ),
        headless=settings.browser_headless,
    )
    try:
        result = await service.capture(
            task.id, task.source_url, storage_state, attempt_no=attempt.attempt_no
        )
        capture = SourceCapture(
            task_id=task.id,
            task_attempt_id=attempt.id,
            requested_url=result.requested_url,
            final_url=result.final_url,
            title=result.title,
            text_content=result.text,
            screenshot_path=str(result.screenshot_path),
            insecure_http=result.insecure_http,
            truncated=result.truncated,
        )
        db.add(capture)
        db.flush()
        emit_event(
            db,
            task,
            "source.captured",
            {
                "title": result.title,
                "final_url": result.final_url,
                "screenshot": f"/api/v1/tasks/{task.id}/source-screenshot",
                "truncated": result.truncated,
                "insecure_http": result.insecure_http,
            },
            attempt=attempt,
        )
        return result.text
    except CaptureError as exc:
        capture = SourceCapture(
            task_id=task.id,
            task_attempt_id=attempt.id,
            requested_url=task.source_url,
            error=str(exc),
            insecure_http=task.source_url.startswith("http://"),
        )
        db.add(capture)
        db.flush()
        emit_event(db, task, "source.failed", {"error": str(exc)}, attempt=attempt)
        return ""


async def run_task(db: Session, attempt_id: int) -> None:
    """采集 → 同步仓库 → locate/patch → 临时脚本。成功则停在等待审批，不建分支。"""
    attempt = db.scalar(select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update())
    if attempt is None or attempt.status not in {"queued", "stale"}:
        return
    task = db.get(Task, attempt.task_id)
    if task is None:
        return
    attempt.status = "capturing_source" if task.source_url else "syncing_repository"
    attempt.execution_started_at = attempt.execution_started_at or utcnow()
    attempt.error = None
    sync_task_projection(task, attempt)
    create_steps(db, task, attempt)
    emit_event(db, task, "task.started", {"attempt": attempt.attempt_no}, attempt=attempt)
    db.commit()
    try:
        task = load_task(db, attempt.task_id)
        attempt = require_attempt(task, attempt_id)
        source_context = ""
        if task.source_url:
            set_step(db, task, "capture", "running", "正在读取问题页面", attempt=attempt)
            db.commit()
            source_context = await capture_source(db, task, attempt)
            set_step(db, task, "capture", "completed", "问题页面采集完成", attempt=attempt)
            db.commit()
        else:
            set_step(db, task, "capture", "completed", "未提供问题链接", attempt=attempt)
            db.commit()

        ensure_not_cancelled(db, attempt_id)

        attempt.status = "syncing_repository"
        sync_task_projection(task, attempt)
        set_step(db, task, "sync", "running", "正在 fetch 默认分支", attempt=attempt)
        db.commit()
        base_sha = sync_repository(db, task.repository)
        attempt.base_sha = base_sha
        sync_task_projection(task, attempt)
        runtime = ensure_runtime(db, task.repository, base_sha)
        set_step(
            db,
            task,
            "sync",
            "completed",
            f"已锁定 {task.repository.default_branch}@{base_sha[:8]}",
            attempt=attempt,
        )
        db.commit()

        ensure_not_cancelled(db, attempt_id)

        model_config = get_settings().model_runtime_config()
        if (
            not model_config.get("api_url")
            or not model_config.get("api_key")
            or not model_config.get("model")
        ):
            raise WorkflowError("模型环境变量尚未配置")

        def on_agent_event(event_type: str, payload: dict[str, Any]) -> None:
            live_task = db.get(Task, task.id)
            live_attempt = db.get(TaskAttempt, attempt_id)
            if live_task is not None and live_attempt is not None:
                emit_event(db, live_task, event_type, payload, attempt=live_attempt)
                db.commit()

        agent = FixoraAgent(
            cache=repository_cache(db),
            repository_id=task.repository_id,
            base_sha=base_sha,
            model_config=model_config,
            on_event=on_agent_event,
            image_path=Path(task.image_path) if task.image_path else None,
            image_mime=task.image_mime,
            trace_path=artifact_dir(get_settings().artifact_root, task.id, attempt.attempt_no)
            / "agent-trace.md",
        )
        async with agent:
            attempt.status = "analyzing"
            sync_task_projection(task, attempt)
            set_step(db, task, "analyze", "running", "Agent 正在定位问题", attempt=attempt)
            db.commit()
            locate = await agent.locate(task.description, source_context)
            ensure_not_cancelled(db, attempt_id)
            attempt.title = locate.title[:255] or attempt.title
            sync_task_projection(task, attempt)
            set_step(
                db,
                task,
                "analyze",
                "running",
                "Agent 正在生成修改",
                {"files": locate.files},
                attempt=attempt,
            )
            db.commit()
            summary = await agent.patch(locate, description=task.description)
            ensure_not_cancelled(db, attempt_id)
            change_set = _store_change_set(db, task, attempt, base_sha, agent, summary)
            attempt.title = summary.title[:255] or attempt.title
            sync_task_projection(task, attempt)
            set_step(db, task, "analyze", "completed", summary.summary, attempt=attempt)
            db.commit()

            result, change_set = _run_temp_scripts(
                db, task, attempt, runtime, change_set, agent, retry=1
            )
            if result.status == "failed":
                change_set.status = "superseded"
                db.commit()
                summary = await agent.patch(
                    locate,
                    description=task.description,
                    failure_context=result.output or result.status,
                )
                ensure_not_cancelled(db, attempt_id)
                change_set = _store_change_set(db, task, attempt, base_sha, agent, summary)
                set_step(db, task, "analyze", "completed", summary.summary, attempt=attempt)
                db.commit()
                result, change_set = _run_temp_scripts(
                    db, task, attempt, runtime, change_set, agent, retry=2
                )

        passed = result.status == "passed"
        change_set.status = "ready" if passed else "unverified"
        attempt.status = "awaiting_approval" if passed else "awaiting_force_approval"
        mark_attempt_finished(attempt)
        sync_task_projection(task, attempt)
        test_summary = (
            "临时脚本通过"
            if passed
            else ("未执行临时脚本" if result.status == "unverified" else "临时脚本未通过")
        )
        set_step(
            db,
            task,
            "test",
            "completed" if result.status != "failed" else "failed",
            test_summary,
            {"status": result.status, "duration_ms": result.duration_ms},
            attempt=attempt,
        )
        set_step(
            db,
            task,
            "approval",
            "waiting",
            "修改已验证，等待确认" if passed else "修改未验证，可人工强制确认",
            {"change_set_id": change_set.id, "patch_hash": change_set.patch_hash},
            attempt=attempt,
        )
        emit_event(
            db,
            task,
            "approval.required",
            {
                "forced": not passed,
                "change_set_id": change_set.id,
                "patch_hash": change_set.patch_hash,
            },
            attempt=attempt,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        attempt = db.get(TaskAttempt, attempt_id)
        task = db.get(Task, attempt.task_id) if attempt is not None else None
        if attempt is not None:
            if attempt.status == "cancelled" or (task is not None and task.status == "cancelled"):
                attempt.status = "cancelled"
                mark_attempt_finished(attempt)
                if task is not None:
                    sync_task_projection(task, attempt)
                db.commit()
                return
            attempt.status = "failed"
            attempt.error = f"{exc.__class__.__name__}: {str(exc)[:2_000]}"
            mark_attempt_finished(attempt)
            if task is not None:
                sync_task_projection(task, attempt)
                emit_event(db, task, "task.failed", {"error": attempt.error}, attempt=attempt)
            db.commit()
    finally:
        live = db.get(TaskAttempt, attempt_id)
        live_task = db.get(Task, live.task_id) if live is not None else None
        if (
            live is not None
            and live.execution_finished_at is None
            and not is_active_status(live.status)
        ):
            mark_attempt_finished(live)
            if live_task is not None:
                sync_task_projection(live_task, live)
            db.commit()


def commit_task(db: Session, attempt_id: int) -> bool:
    """把已批准的虚拟变更推到 GitLab。返回 True 表示 SHA 变了，调用方应重跑同一 Attempt。"""
    attempt = db.scalar(select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update())
    if attempt is None or attempt.status != "committing":
        return False
    db.commit()
    task = load_task(db, attempt.task_id)
    attempt = require_attempt(task, attempt_id)
    current_sha = sync_repository(db, task.repository)
    if current_sha != attempt.base_sha:
        attempt.status = "stale"
        attempt.base_sha = None
        sync_task_projection(task, attempt)
        emit_event(
            db,
            task,
            "task.stale",
            {
                "old_sha": task.base_sha,
                "new_sha": current_sha,
                "message": "默认分支已更新，重新分析",
            },
            attempt=attempt,
        )
        db.commit()
        return True
    change_set = next(
        (
            item
            for item in reversed(
                [row for row in task.change_sets if row.task_attempt_id == attempt.id]
            )
            if item.status in {"ready", "unverified", "approved"}
        ),
        None,
    )
    if change_set is None:
        raise WorkflowError("没有可提交的 change set")
    branch = _branch_name(task.id, attempt.title, attempt.attempt_no)
    client = gitlab_client(db)
    try:
        validation = "forced" if attempt.forced_reason else "passed"
        message = (
            f"fix: {attempt.title}\n\nFixora Task: #{task.id}\n"
            f"Attempt: {attempt.attempt_no}\nValidation: {validation}"
        )
        actions = [
            {
                "action": "update",
                "file_path": file.path,
                "content": file.new_content,
            }
            for file in change_set.files
        ]
        try:
            try:
                commit = client.create_commit(
                    task.repository.gitlab_project_id,
                    branch=branch,
                    message=message,
                    actions=actions,
                    start_sha=current_sha,
                )
            except GitLabError as exc:
                if not _branch_already_exists(exc):
                    try:
                        client.create_branch(task.repository.gitlab_project_id, branch, current_sha)
                    except GitLabError as branch_exc:
                        if not _branch_already_exists(branch_exc):
                            raise exc from branch_exc
                commit = client.create_commit(
                    task.repository.gitlab_project_id,
                    branch=branch,
                    message=message,
                    actions=actions,
                )
        except Exception as exc:
            db.rollback()
            attempt = db.get(TaskAttempt, attempt_id)
            task = db.get(Task, attempt.task_id) if attempt is not None else None
            if attempt is not None and attempt.status == "committing":
                attempt.status = "failed"
                attempt.error = f"{exc.__class__.__name__}: {str(exc)[:2_000]}"
                mark_attempt_finished(attempt)
                if task is not None:
                    sync_task_projection(task, attempt)
                    emit_event(db, task, "task.failed", {"error": attempt.error}, attempt=attempt)
                db.commit()
            return False
        attempt.branch_name = branch
        attempt.commit_sha = str(commit["id"])
        attempt.status = "completed"
        mark_attempt_finished(attempt)
        change_set.status = "committed"
        sync_task_projection(task, attempt)
        set_step(db, task, "commit", "completed", f"已创建 {branch}", attempt=attempt)
        emit_event(
            db,
            task,
            "task.completed",
            {"branch": branch, "commit_sha": attempt.commit_sha},
            attempt=attempt,
        )
        db.commit()
        try:
            client.create_commit_comment(
                task.repository.gitlab_project_id,
                attempt.commit_sha,
                commit_comment_body(
                    task,
                    change_set,
                    attempt=attempt,
                    repository=task.repository,
                    test_runs=[
                        item for item in task.test_runs if item.task_attempt_id == attempt.id
                    ],
                ),
            )
        except GitLabError as exc:
            emit_event(
                db, task, "commit.comment_failed", {"error": str(exc)[:2_000]}, attempt=attempt
            )
            db.commit()
    finally:
        client.close()
    return False


def _branch_name(task_id: int, title: str, attempt_no: int = 1) -> str:
    """Attempt 1 保持旧分支名，避免已推过的仓库对不上。"""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "repair"
    if attempt_no <= 1:
        return f"fix/fixora-{task_id}-{slug}"
    return f"fix/fixora-{task_id}-a{attempt_no}-{slug}"


def _branch_already_exists(exc: GitLabError) -> bool:
    text = str(exc).casefold()
    return "already exists" in text or "branch already exists" in text


def _commit_comment_body(
    task: Task,
    change_set: ChangeSet,
    *,
    attempt: TaskAttempt | None = None,
    repository: Repository | None = None,
    test_runs: list[TestRun] | None = None,
) -> str:
    return commit_comment_body(
        task, change_set, attempt=attempt, repository=repository, test_runs=test_runs
    )


def _store_change_set(
    db: Session,
    task: Task,
    attempt: TaskAttempt,
    base_sha: str,
    agent: FixoraAgent,
    summary: AgentSummary,
) -> ChangeSet:
    change_set = ChangeSet(
        task_id=task.id,
        task_attempt_id=attempt.id,
        base_sha=base_sha,
        patch_hash=agent.workspace.patch_hash(),
        summary=summary.summary,
        root_cause=summary.root_cause,
        status="validating",
    )
    db.add(change_set)
    db.flush()
    for item in agent.workspace.files.values():
        db.add(
            FileChange(
                change_set_id=change_set.id,
                path=item.path,
                base_blob_sha=item.base_blob_sha,
                old_content=item.old_content,
                new_content=item.new_content,
                reason=item.reason,
                unified_diff=item.unified_diff,
                hunks=item.hunks,
            )
        )
    db.flush()
    loaded = db.scalar(
        select(ChangeSet)
        .where(ChangeSet.id == change_set.id)
        .options(selectinload(ChangeSet.files))
    )
    assert loaded is not None
    return loaded


def _run_temp_scripts(
    db: Session,
    task: Task,
    attempt: TaskAttempt,
    runtime: RepositoryRuntimeProfile | None,
    change_set: ChangeSet,
    agent: FixoraAgent,
    *,
    retry: int,
) -> tuple[TestResult, ChangeSet]:
    attempt.status = "validating"
    sync_task_projection(task, attempt)
    set_step(
        db, task, "test", "running", "正在执行临时验证脚本", {"attempt": retry}, attempt=attempt
    )
    db.commit()
    result = LocalTestService(get_settings(), repository_cache(db)).run(
        task.id,
        task.repository,
        change_set.base_sha,
        runtime,
        change_set.files,
        agent.workspace.temp_tests,
        attempt_no=attempt.attempt_no,
    )
    db.add(
        TestRun(
            task_id=task.id,
            task_attempt_id=attempt.id,
            change_set_id=change_set.id,
            attempt=retry,
            status=result.status,
            command=result.command,
            exit_code=result.exit_code,
            output=result.output,
            duration_ms=result.duration_ms,
        )
    )
    db.commit()
    return result, change_set
