from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .cache import RepositoryCache


@dataclass(frozen=True)
class DetectedRuntime:
    language: str
    runtime_version: str
    package_manager: str
    working_directory: str
    install_argv: list[str]
    test_argv: list[str]
    lockfile_path: str | None
    lockfile_hash: str | None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def detect_runtime(cache: RepositoryCache, repository_id: int, sha: str) -> DetectedRuntime:
    files = set(cache.list_files(repository_id, sha))
    if "package.json" in files:
        package = json.loads(cache.read_file(repository_id, sha, "package.json"))
        scripts = package.get("scripts") if isinstance(package, dict) else {}
        if "pnpm-lock.yaml" in files:
            manager = "pnpm"
            lock = "pnpm-lock.yaml"
            install = ["pnpm", "install", "--frozen-lockfile"]
            test = ["pnpm", "test"]
        elif "yarn.lock" in files:
            manager = "yarn"
            lock = "yarn.lock"
            install = ["yarn", "install", "--immutable"]
            test = ["yarn", "test"]
        else:
            manager = "npm"
            lock = "package-lock.json" if "package-lock.json" in files else None
            install = ["npm", "ci"] if lock else ["npm", "install"]
            test = ["npm", "test"]
        if not isinstance(scripts, dict) or "test" not in scripts:
            test = []
        lock_content = cache.read_file(repository_id, sha, lock) if lock else ""
        return DetectedRuntime(
            language="node",
            runtime_version="22",
            package_manager=manager,
            working_directory=".",
            install_argv=install,
            test_argv=test,
            lockfile_path=lock,
            lockfile_hash=_hash(lock_content) if lock else None,
        )

    if "pyproject.toml" in files or "requirements.txt" in files:
        if "uv.lock" in files:
            lock = "uv.lock"
            install = ["uv", "sync", "--frozen"]
        elif "requirements.txt" in files:
            lock = "requirements.txt"
            install = ["uv", "pip", "install", "-r", "requirements.txt"]
        else:
            lock = "pyproject.toml"
            install = ["uv", "sync"]
        lock_content = cache.read_file(repository_id, sha, lock)
        return DetectedRuntime(
            language="python",
            runtime_version="3.12",
            package_manager="uv",
            working_directory=".",
            install_argv=install,
            test_argv=["uv", "run", "pytest"],
            lockfile_path=lock,
            lockfile_hash=_hash(lock_content),
        )

    raise ValueError("仅支持根目录包含 package.json 或 Python 项目配置的仓库")
