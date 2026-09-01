from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from .ignore import git_exclude_pathspecs, is_ignored_path


class RepositoryCacheError(RuntimeError):
    pass


def normalize_repo_path(value: str) -> str:
    """拒绝绝对路径和 ..，避免虚拟补丁打出仓库。"""
    path = PurePosixPath(value.strip().lstrip("/"))
    if not value.strip() or path.is_absolute() or ".." in path.parts:
        raise ValueError("非法仓库路径")
    return path.as_posix()


_SOURCE_DIR_NAMES = frozenset({"app", "components", "lib", "packages", "pages", "source", "src"})


def _source_path_rank(value: str) -> tuple[int, int, str]:
    normalized = f"/{value.casefold().strip('/')}/"
    if "/src/" in normalized:
        priority = 0
    elif "/test/" in normalized or "/tests/" in normalized:
        priority = 1
    elif "/public/" in normalized:
        priority = 3
    elif "/build/" in normalized:
        priority = 4
    else:
        priority = 2
    return priority, len(value), value.casefold()


def _tree_entry_rank(value: str) -> tuple[int, int, int, int, str]:
    name = value.rstrip("/").rsplit("/", 1)[-1].casefold()
    source_priority, _length, casefold_name = _source_path_rank(value)
    return (
        0 if value.endswith("/") else 1,
        0 if name in _SOURCE_DIR_NAMES else 1,
        source_priority,
        value.count("/"),
        casefold_name,
    )


def _keyword_path_group(value: str, lowered_query: str) -> str:
    parts = value.split("/")
    for index, part in enumerate(parts):
        if lowered_query in part.casefold():
            if index == len(parts) - 1:
                return value
            return "/".join(parts[: index + 1]) + "/"
    return value


class RepositoryCache:
    def __init__(
        self,
        root: Path,
        token: str,
        ca_bundle: str | None = None,
        ssl_verify: bool = False,
    ) -> None:
        self.root = root
        self.token = token
        self.ca_bundle = ca_bundle
        self.ssl_verify = ssl_verify
        self.root.mkdir(parents=True, exist_ok=True)
        self._file_lists: dict[tuple[int, str], list[str]] = {}

    def path_for(self, repository_id: int) -> Path:
        return self.root / f"repository-{repository_id}.git"

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_LFS_SKIP_SMUDGE"] = "1"
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraHeader"
        env["GIT_CONFIG_VALUE_0"] = f"PRIVATE-TOKEN: {self.token}"
        if not self.ssl_verify:
            env["GIT_SSL_NO_VERIFY"] = "1"
        elif self.ca_bundle:
            env["GIT_SSL_CAINFO"] = self.ca_bundle
        return env

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        check: bool = True,
    ) -> str:
        try:
            completed = subprocess.run(
                args,
                cwd=cwd,
                env=self._env(),
                check=check,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=600,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RepositoryCacheError(f"Git cache 操作失败: {exc}") from exc
        if check and completed.returncode != 0:
            raise RepositoryCacheError(completed.stderr[-500:])
        return completed.stdout.strip()

    @contextmanager
    def lock(self, repository_id: int) -> Iterator[None]:
        lock_path = self.root / f"repository-{repository_id}.lock"
        with lock_path.open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def sync(self, repository_id: int, clone_url: str, branch: str) -> str:
        cache = self.path_for(repository_id)
        ref = f"refs/heads/{branch}"
        with self.lock(repository_id):
            if not (cache / "HEAD").exists():
                if cache.exists():
                    shutil.rmtree(cache)
                self._run(
                    [
                        "git",
                        "clone",
                        "--bare",
                        "--single-branch",
                        "--branch",
                        branch,
                        "--depth",
                        "1",
                        "--no-tags",
                        clone_url,
                        str(cache),
                    ]
                )
            else:
                self._run(
                    [
                        "git",
                        "fetch",
                        "--prune",
                        "--no-tags",
                        "--depth",
                        "1",
                        "origin",
                        f"+{ref}:{ref}",
                    ],
                    cwd=cache,
                )
            return self._run(["git", "rev-parse", "--verify", ref], cwd=cache)

    def list_files(self, repository_id: int, sha: str, limit: int = 50_000) -> list[str]:
        key = (repository_id, sha)
        cached = self._file_lists.get(key)
        if cached is None:
            output = self._run(
                ["git", "-c", "core.quotePath=false", "ls-tree", "-r", "--name-only", sha],
                cwd=self.path_for(repository_id),
            )
            cached = [line for line in output.splitlines() if line and not is_ignored_path(line)]
            self._file_lists[key] = cached
        return cached[:limit]

    def list_tree(
        self,
        repository_id: int,
        sha: str,
        *,
        path: str = "",
        depth: int = 1,
        limit: int = 80,
    ) -> list[str]:
        prefix = normalize_repo_path(path).rstrip("/") if path.strip() else ""
        prefix_with_slash = f"{prefix}/" if prefix else ""
        depth = min(max(depth, 1), 2)
        entries: set[str] = set()
        for file_path in self.list_files(repository_id, sha):
            if prefix and not file_path.startswith(prefix_with_slash):
                continue
            relative = file_path[len(prefix_with_slash) :]
            parts = relative.split("/")
            if len(parts) > depth:
                entry = prefix_with_slash + "/".join(parts[:depth]) + "/"
            else:
                entry = file_path
            entries.add(entry)
        ranked = sorted(entries, key=_tree_entry_rank)
        return ranked[:limit]

    def search_code(
        self,
        repository_id: int,
        sha: str,
        query: str,
        *,
        path: str = "",
        limit: int = 100,
    ) -> list[dict[str, str | int]]:
        if not query.strip() or len(query) > 300:
            raise ValueError("搜索词为空或过长")
        completed = subprocess.run(
            [
                "git",
                "grep",
                "-n",
                "-i",
                "-I",
                "-F",
                "--",
                query,
                sha,
                "--",
                ".",
                *git_exclude_pathspecs(),
            ],
            cwd=self.path_for(repository_id),
            env=self._env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        if completed.returncode not in (0, 1):
            raise RepositoryCacheError(completed.stderr[-500:])
        prefix = normalize_repo_path(path).rstrip("/") if path.strip() else ""
        matches: list[dict[str, str | int]] = []
        seen: set[tuple[str, int]] = set()
        path_match_limit = min(20, max(1, limit // 2))
        lowered_query = query.casefold()
        path_matches = {
            _keyword_path_group(file_path, lowered_query)
            for file_path in self.list_files(repository_id, sha)
            if (not prefix or file_path.startswith(f"{prefix}/"))
            and lowered_query in file_path.casefold()
        }
        for file_path in sorted(path_matches, key=_source_path_rank):
            match_type = "(path group)" if file_path.endswith("/") else "(path match)"
            matches.append({"path": file_path, "line": 0, "text": match_type})
            seen.add((file_path, 0))
            if len(matches) >= path_match_limit:
                break
        content_matches: list[dict[str, str | int]] = []
        for line in completed.stdout.splitlines():
            parts = line.split(":", 3)
            if len(parts) != 4 or not parts[2].isdigit():
                continue
            _, file_path, line_no, text = parts
            if is_ignored_path(file_path) or (prefix and not file_path.startswith(f"{prefix}/")):
                continue
            key = (file_path, int(line_no))
            if key in seen:
                continue
            content_matches.append({"path": file_path, "line": int(line_no), "text": text[:500]})
        for item in sorted(
            content_matches,
            key=lambda item: (*_source_path_rank(str(item["path"])), int(item["line"])),
        ):
            key = (str(item["path"]), int(item["line"]))
            if key in seen:
                continue
            matches.append(item)
            seen.add(key)
            if len(matches) >= limit:
                break
        return matches

    def read_file(self, repository_id: int, sha: str, path: str) -> str:
        safe_path = normalize_repo_path(path)
        if is_ignored_path(safe_path):
            raise ValueError(f"构建产物或二进制文件不参与分析: {safe_path}")
        return self._run(["git", "show", f"{sha}:{safe_path}"], cwd=self.path_for(repository_id))

    def blob_sha(self, repository_id: int, sha: str, path: str) -> str:
        safe_path = normalize_repo_path(path)
        if is_ignored_path(safe_path):
            raise ValueError(f"构建产物不允许修改: {safe_path}")
        return self._run(
            ["git", "rev-parse", f"{sha}:{safe_path}"], cwd=self.path_for(repository_id)
        )

    def create_worktree(self, repository_id: int, sha: str, target: Path) -> None:
        cache = self.path_for(repository_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        self._run(["git", "worktree", "add", "--detach", str(target), sha], cwd=cache)

    def remove_worktree(self, repository_id: int, target: Path) -> None:
        cache = self.path_for(repository_id)
        self._run(["git", "worktree", "remove", "--force", str(target)], cwd=cache, check=False)
        if target.exists():
            shutil.rmtree(target)
        self._run(["git", "worktree", "prune"], cwd=cache, check=False)
