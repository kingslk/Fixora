from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fixora.agent.workspace import VirtualWorkspace
from fixora.repo.cache import RepositoryCache
from fixora.repo.runtime import detect_runtime


def run(args: list[str], cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def make_remote(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    run(["git", "init", "-b", "master"], source)
    run(["git", "config", "user.email", "test@example.com"], source)
    run(["git", "config", "user.name", "Test"], source)
    (source / "package.json").write_text(
        '{"scripts":{"test":"vitest run"},"devDependencies":{"vitest":"1.0.0"}}\n'
    )
    (source / "package-lock.json").write_text('{"lockfileVersion":3}\n')
    (source / "profile.ts").write_text("export const nickname = user.nickname;\n")
    (source / "src").mkdir()
    (source / "src" / "feature.ts").write_text("export const sourceMarker = 'source';\n")
    (source / "src" / "dist").mkdir()
    (source / "src" / "dist" / "bundle.js").write_text("const sourceMarker = 'bundle';\n")
    (source / "release").mkdir()
    (source / "release" / "feature.js").write_text("const sourceMarker = 'compiled';\n")
    (source / "build").mkdir()
    (source / "build" / "webpack.js").write_text("export const buildConfig = true;\n")
    (source / ".claude").mkdir()
    (source / ".claude" / "agents").mkdir(parents=True)
    (source / ".claude" / "agents" / "explorer.md").write_text(
        "nickname accountLogoff agent skill\n"
    )
    (source / ".env").write_text("SECRET=1\n")
    (source / "aaa").mkdir()
    for index in range(40):
        (source / "aaa" / f"file-{index:02d}.ts").write_text(f"export const n{index} = {index};\n")
    run(["git", "add", "-A", "-f"], source)
    run(["git", "commit", "-m", "initial"], source)
    return source, run(["git", "rev-parse", "HEAD"], source)


def test_bare_cache_search_runtime_and_virtual_diff(tmp_path: Path) -> None:
    source, sha = make_remote(tmp_path)
    cache = RepositoryCache(tmp_path / "cache", token="")
    cached_sha = cache.sync(1, source.as_uri(), "master")
    assert cached_sha == sha
    files = cache.list_files(1, sha)
    assert "profile.ts" in files
    assert "release/feature.js" not in files
    assert "src/dist/bundle.js" not in files
    assert "build/webpack.js" in files
    assert ".claude/agents/explorer.md" not in files
    assert ".env" not in files
    tree = cache.list_tree(1, sha, depth=1, limit=20)
    assert tree[0] == "src/"
    assert "aaa/" in tree
    assert "build/" in tree
    assert "src/" in tree
    assert all(not item.startswith(".claude") for item in tree)
    crowded = cache.list_tree(1, sha, depth=2, limit=12)
    assert "src/" in crowded or any(item.startswith("src/") for item in crowded)
    assert not any(item.startswith(".claude") for item in crowded)
    assert cache.search_code(1, sha, "nickname") == [
        {"path": "profile.ts", "line": 1, "text": "export const nickname = user.nickname;"}
    ]
    assert all(
        "claude" not in str(item["path"]).casefold()
        for item in cache.search_code(1, sha, "accountLogoff")
    )
    assert cache.search_code(1, sha, "sourcemarker") == [
        {"path": "src/feature.ts", "line": 1, "text": "export const sourceMarker = 'source';"}
    ]
    assert cache.search_code(1, sha, "FEATURE") == [
        {"path": "src/feature.ts", "line": 0, "text": "(path match)"}
    ]
    with pytest.raises(ValueError, match="构建产物"):
        cache.read_file(1, sha, "release/feature.js")

    runtime = detect_runtime(cache, 1, sha)
    assert runtime.language == "node"
    assert runtime.package_manager == "npm"
    assert runtime.install_argv == ["npm", "ci"]

    workspace = VirtualWorkspace(cache, 1, sha)
    item = workspace.apply(
        "profile.ts",
        "export const nickname = user.nickname?.trim() || '未设置';\n",
        "为空值添加兜底",
    )
    assert item.base_blob_sha
    assert any(row["type"] == "insert" for row in item.hunks[0]["rows"])
    assert len(workspace.patch_hash()) == 64

    replaced = workspace.replace(
        "profile.ts",
        "user.nickname?.trim() || '未设置'",
        "user.nickname?.trim() || '匿名'",
        "改兜底文案",
    )
    assert "匿名" in replaced.new_content
    with pytest.raises(ValueError, match="未找到"):
        workspace.replace("profile.ts", "不存在的片段", "x", "nope")
    with pytest.raises(ValueError, match="jest/tsx"):
        workspace.add_temp_test("src/fixora_temp_clear.test.tsx", "describe('x', () => {})")
    workspace.add_temp_test("fixora_temp_clear.mjs", "import fs from 'node:fs';\n")


def test_worktree_is_ephemeral(tmp_path: Path) -> None:
    source, sha = make_remote(tmp_path)
    cache = RepositoryCache(tmp_path / "cache", token="")
    cache.sync(1, source.as_uri(), "master")
    worktree = tmp_path / "task-1"
    cache.create_worktree(1, sha, worktree)
    assert (worktree / "profile.ts").is_file()
    cache.remove_worktree(1, worktree)
    assert not worktree.exists()
