from __future__ import annotations

import subprocess
from pathlib import Path

from fixora.config import Settings
from fixora.models import Repository, RepositoryRuntimeProfile
from fixora.repo.cache import RepositoryCache
from fixora.repo.runner import LocalTestService


def run(args: list[str], cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def test_node_temp_script_runs_from_ephemeral_worktree(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    run(["git", "init", "-b", "master"], source)
    run(["git", "config", "user.email", "test@example.com"], source)
    run(["git", "config", "user.name", "Test"], source)
    (source / "module.mjs").write_text("export const value = 2;\n")
    run(["git", "add", "."], source)
    run(["git", "commit", "-m", "initial"], source)
    sha = run(["git", "rev-parse", "HEAD"], source)

    cache = RepositoryCache(tmp_path / "cache", token="")
    cache.sync(987654, source.as_uri(), "master")
    settings = Settings(
        secret_key="test",
        database_url="sqlite+pysqlite:///:memory:",
        data_root=tmp_path / "data",
        systemd_runner_enabled=False,
    )
    repository = Repository(
        id=987654,
        gitlab_project_id=1,
        name="repo",
        path_with_namespace="group/repo",
        clone_url=source.as_uri(),
        default_branch="master",
    )
    runtime = RepositoryRuntimeProfile(
        language="node",
        runtime_version="22",
        package_manager="npm",
        working_directory=".",
        install_argv=[],
        test_argv=["node", "fixora_temp_test.mjs"],
    )
    result = LocalTestService(settings, cache).run(
        987654,
        repository,
        sha,
        runtime,
        [],
        {
            "fixora_temp_test.mjs": (
                "import { value } from './module.mjs';\n"
                "if (value !== 2) throw new Error('unexpected');\n"
            )
        },
    )
    assert result.status == "passed"
    assert result.command[:1] == ["node"]
    assert not Path("/tmp/fixora/task-987654-a1").exists()


def test_missing_temp_script_is_unverified_without_running(tmp_path: Path) -> None:
    settings = Settings(
        secret_key="test",
        database_url="sqlite+pysqlite:///:memory:",
        data_root=tmp_path / "data",
        systemd_runner_enabled=False,
    )
    cache = RepositoryCache(tmp_path / "cache", token="")
    repository = Repository(
        id=1,
        gitlab_project_id=1,
        name="repo",
        path_with_namespace="group/repo",
        clone_url="file://unused",
        default_branch="master",
    )
    runtime = RepositoryRuntimeProfile(
        language="node",
        runtime_version="22",
        package_manager="npm",
        working_directory=".",
        install_argv=[],
        test_argv=[],
    )
    result = LocalTestService(settings, cache).run(1, repository, "abc", runtime, [], {})
    assert result.status == "unverified"
    assert result.command == []
    assert "未生成临时验证脚本" in result.output


def test_python_temp_script_runs_from_ephemeral_worktree(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    run(["git", "init", "-b", "master"], source)
    run(["git", "config", "user.email", "test@example.com"], source)
    run(["git", "config", "user.name", "Test"], source)
    (source / "value.py").write_text("VALUE = 2\n")
    run(["git", "add", "."], source)
    run(["git", "commit", "-m", "initial"], source)
    sha = run(["git", "rev-parse", "HEAD"], source)

    cache = RepositoryCache(tmp_path / "cache", token="")
    cache.sync(2, source.as_uri(), "master")
    settings = Settings(
        secret_key="test",
        database_url="sqlite+pysqlite:///:memory:",
        data_root=tmp_path / "data",
        systemd_runner_enabled=False,
    )
    repository = Repository(
        id=2,
        gitlab_project_id=1,
        name="repo",
        path_with_namespace="group/repo",
        clone_url=source.as_uri(),
        default_branch="master",
    )
    runtime = RepositoryRuntimeProfile(
        language="python",
        runtime_version="3.12",
        package_manager="uv",
        working_directory=".",
        install_argv=[],
        test_argv=[],
    )
    result = LocalTestService(settings, cache).run(
        2,
        repository,
        sha,
        runtime,
        [],
        {
            "fixora_temp_value.py": (
                "from pathlib import Path\n"
                "text = Path('value.py').read_text(encoding='utf-8')\n"
                "assert 'VALUE = 2' in text\n"
            )
        },
    )
    assert result.status == "passed"
    assert result.command[:1] == ["python3"]
