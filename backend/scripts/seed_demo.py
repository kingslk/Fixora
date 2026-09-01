from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from fixora.db import Base, SessionLocal, engine
from fixora.models import (
    ChangeSet,
    FileChange,
    Repository,
    RepositoryRuntimeProfile,
    Task,
    TaskEvent,
    TestRun,
)
from fixora.tasks.attempts import create_attempt, mark_attempt_finished, sync_task_projection


def main() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        repository = db.scalar(
            select(Repository).where(Repository.path_with_namespace == "group/user-web")
        )
        if repository is None:
            repository = Repository(
                gitlab_project_id=1001,
                name="user-web",
                path_with_namespace="group/user-web",
                clone_url="https://gitlab.example.com/group/user-web.git",
                default_branch="master",
                cached_sha="a81c2d4f03b22c31d8dcee3d28af2e5bb2a516a7",
                cache_status="ready",
                last_fetch_at=datetime.now(UTC) - timedelta(minutes=8),
            )
            repository.runtime_profile = RepositoryRuntimeProfile(
                language="node",
                runtime_version="22",
                package_manager="pnpm",
                working_directory=".",
                install_argv=["pnpm", "install", "--frozen-lockfile"],
                test_argv=["pnpm", "test"],
                lockfile_path="pnpm-lock.yaml",
                lockfile_hash="a" * 64,
            )
            db.add(repository)
            db.flush()

        if db.scalar(select(Task).limit(1)) is not None:
            print("Demo data already exists")
            return
        now = datetime.now(UTC)
        detail = Task(
            repository_id=repository.id,
            title="用户详情页昵称为空异常",
            description="用户反馈详情页昵称为空时抛出异常，请定位根因并提供最小修复。",
            status="awaiting_approval",
            base_sha=repository.cached_sha,
            event_seq=6,
            created_at=now - timedelta(minutes=5),
            updated_at=now,
        )
        db.add(detail)
        db.flush()
        attempt = create_attempt(db, detail, 1, status="awaiting_approval")
        attempt.title = detail.title
        attempt.base_sha = detail.base_sha
        attempt.event_seq = 6
        mark_attempt_finished(attempt)
        sync_task_projection(detail, attempt)
        change = ChangeSet(
            task_id=detail.id,
            task_attempt_id=attempt.id,
            base_sha=repository.cached_sha or "",
            patch_hash="b" * 64,
            summary="为昵称渲染添加空值兜底，避免访问空值属性。",
            root_cause="页面直接渲染 user.nickname，未处理 null、undefined 与空白字符串。",
            status="ready",
        )
        db.add(change)
        db.flush()
        db.add(
            FileChange(
                change_set_id=change.id,
                path="src/user/Profile.tsx",
                base_blob_sha="c" * 40,
                old_content='<h1 className="nickname">{user.nickname}</h1>\n',
                new_content=(
                    '<h1 className="nickname">\n'
                    "  {user.nickname?.trim() ? user.nickname : '未设置昵称'}\n"
                    "</h1>\n"
                ),
                reason="昵称为空或仅含空白字符时显示稳定占位文案。",
                unified_diff="",
                hunks=[
                    {
                        "header": "@@ -130,1 +130,3 @@",
                        "rows": [
                            {
                                "type": "delete",
                                "old": 130,
                                "new": None,
                                "text": '<h1 className="nickname">{user.nickname}</h1>',
                            },
                            {
                                "type": "insert",
                                "old": None,
                                "new": 130,
                                "text": '<h1 className="nickname">',
                            },
                            {
                                "type": "insert",
                                "old": None,
                                "new": 131,
                                "text": "  {user.nickname?.trim() ? user.nickname : '未设置昵称'}",
                            },
                            {
                                "type": "insert",
                                "old": None,
                                "new": 132,
                                "text": "</h1>",
                            },
                        ],
                    }
                ],
            )
        )
        db.add(
            TestRun(
                task_id=detail.id,
                task_attempt_id=attempt.id,
                change_set_id=change.id,
                attempt=1,
                status="passed",
                command=["pnpm", "test", "--", "fixora_temp_profile.test.tsx"],
                exit_code=0,
                output="2 passed",
                duration_ms=1800,
            )
        )
        events = [
            (1, "task.started", {"attempt": 1}),
            (2, "agent.thought", {"text": "先按 nickname 搜索用户详情页渲染逻辑。"}),
            (
                3,
                "agent.tool",
                {
                    "tool": "search_code",
                    "query": "nickname",
                    "count": 4,
                    "preview": ["src/user/Profile.tsx:130"],
                },
            ),
            (
                4,
                "agent.tool",
                {"tool": "read_file", "path": "src/user/Profile.tsx", "start": 120, "end": 140},
            ),
            (
                5,
                "agent.tool",
                {
                    "tool": "apply_virtual_patch",
                    "path": "src/user/Profile.tsx",
                    "reason": "空昵称兜底",
                },
            ),
            (
                6,
                "approval.required",
                {"change_set_id": change.id, "patch_hash": change.patch_hash, "forced": False},
            ),
        ]
        for seq, event_type, payload in events:
            db.add(
                TaskEvent(
                    task_id=detail.id,
                    task_attempt_id=attempt.id,
                    seq=seq,
                    type=event_type,
                    payload=payload,
                )
            )

        samples = [
            ("修复用户登录后偶发的 500 错误", "analyzing", 2),
            ("修正支付回调处理的幂等性问题", "awaiting_approval", 18),
            ("修复商品详情页图片加载失败问题", "awaiting_force_approval", 62),
            ("优化搜索接口的超时与重试逻辑", "completed", 180),
            ("修复通知中心未读状态不同步问题", "rejected", 1440),
        ]
        for title, task_status, minutes in samples:
            sample = Task(
                repository_id=repository.id,
                title=title,
                description=title,
                status=task_status,
                base_sha=repository.cached_sha,
                created_at=now - timedelta(minutes=minutes + 5),
                updated_at=now - timedelta(minutes=minutes),
            )
            db.add(sample)
            db.flush()
            sample_attempt = create_attempt(db, sample, 1, status=task_status)
            sample_attempt.title = title
            sample_attempt.base_sha = repository.cached_sha
            if task_status not in {
                "queued",
                "capturing_source",
                "syncing_repository",
                "analyzing",
                "validating",
                "committing",
                "stale",
            }:
                mark_attempt_finished(sample_attempt)
            sync_task_projection(sample, sample_attempt)
        db.commit()
        print(f"Seeded demo task #{detail.id}")


if __name__ == "__main__":
    main()
