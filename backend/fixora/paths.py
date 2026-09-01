from __future__ import annotations

import os
import sys
from pathlib import Path


def default_data_root() -> Path:
    """本地默认放到系统应用数据目录，避免运行数据落在源码树。生产仍用 FIXORA_DATA_ROOT。"""
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Application Support" / "Fixora").expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "fixora"
    return (Path.home() / ".local" / "share" / "fixora").expanduser()


def worktree_dir(task_id: int, attempt_no: int) -> Path:
    return Path("/tmp/fixora") / f"task-{task_id}-a{attempt_no}"


def artifact_dir(artifact_root: Path, task_id: int, attempt_no: int) -> Path:
    return artifact_root / f"task-{task_id}" / f"attempt-{attempt_no}"


def resolve_artifact_file(
    artifact_root: Path, task_id: int, attempt_no: int, name: str
) -> Path | None:
    """新路径优先；Attempt 1 兼容迁移前的 task-<id>/ 布局，不搬 backend/data。"""
    primary = artifact_dir(artifact_root, task_id, attempt_no) / name
    if primary.is_file():
        return primary
    if attempt_no == 1:
        legacy = artifact_root / f"task-{task_id}" / name
        if legacy.is_file():
            return legacy
    return None
