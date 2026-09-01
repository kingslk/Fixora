from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..models import FileChange, Repository, RepositoryRuntimeProfile
from .cache import RepositoryCache, normalize_repo_path


@dataclass(frozen=True)
class TestResult:
    status: str
    command: list[str]
    exit_code: int | None
    output: str
    duration_ms: int


class CommandRunner:
    """生产路径经 systemd-run + fixora-runner；测试可关 FIXORA_SYSTEMD_RUNNER_ENABLED。"""
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(
        self, argv: list[str], cwd: Path, *, writable_paths: list[Path]
    ) -> subprocess.CompletedProcess[str]:
        if not argv:
            raise ValueError("命令不能为空")
        env = {
            "HOME": str(cwd / ".fixora-home"),
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "CI": "1",
            "NO_COLOR": "1",
            "npm_config_cache": str(self.settings.dependency_root / "npm-cache"),
            "PNPM_HOME": str(self.settings.dependency_root / "pnpm-home"),
            "UV_CACHE_DIR": str(self.settings.dependency_root / "uv-cache"),
        }
        Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
        if not self.settings.systemd_runner_enabled:
            return subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.settings.runner_timeout_seconds,
            )
        properties = [
            "ProtectSystem=strict",
            "PrivateTmp=yes",
            "PrivateDevices=yes",
            "NoNewPrivileges=yes",
            "RestrictSUIDSGID=yes",
            "LockPersonality=yes",
            f"MemoryMax={self.settings.runner_memory_max}",
            f"CPUQuota={self.settings.runner_cpu_quota}",
            "TasksMax=256",
            f"RuntimeMaxSec={self.settings.runner_timeout_seconds}",
        ]
        command = [
            "systemd-run",
            "--wait",
            "--pipe",
            "--collect",
            "--quiet",
            f"--uid={self.settings.systemd_runner_user}",
            f"--working-directory={cwd}",
        ]
        for prop in properties:
            command.extend(["--property", prop])
        for path in writable_paths:
            command.extend(["--property", f"ReadWritePaths={path}"])
        command.extend(["env", "-i", *(f"{key}={value}" for key, value in env.items()), *argv])
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=self.settings.runner_timeout_seconds + 30,
        )


class LocalTestService:
    def __init__(self, settings: Settings, cache: RepositoryCache) -> None:
        self.settings = settings
        self.cache = cache
        self.command_runner = CommandRunner(settings)

    def run(
        self,
        task_id: int,
        repository: Repository,
        base_sha: str,
        runtime: RepositoryRuntimeProfile | None,
        changes: list[FileChange],
        temp_tests: dict[str, str],
        attempt_no: int = 1,
    ) -> TestResult:
        if not temp_tests:
            return TestResult("unverified", [], None, "未生成临时验证脚本", 0)
        language = runtime.language if runtime is not None else ""
        interpreter = {"node": "node", "python": "python3"}.get(language)
        if interpreter is None:
            return TestResult("unverified", [], None, "无法识别仓库语言，未执行临时脚本", 0)
        from ..paths import worktree_dir

        workspace = worktree_dir(task_id, attempt_no)
        start = time.monotonic()
        command: list[str] = []
        try:
            self.cache.create_worktree(repository.id, base_sha, workspace)
            for change in changes:
                path = workspace / normalize_repo_path(change.path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(change.new_content, encoding="utf-8")
            for path_value, content in temp_tests.items():
                path = workspace / normalize_repo_path(path_value)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            chunks: list[str] = []
            exit_code = 0
            for path_value in sorted(temp_tests):
                command = [interpreter, path_value]
                completed = self.command_runner.run(command, workspace, writable_paths=[workspace])
                chunk = (completed.stdout + "\n" + completed.stderr).strip()
                if chunk:
                    chunks.append(chunk)
                if completed.returncode != 0:
                    exit_code = completed.returncode
                    break
            output = "\n".join(chunks).strip()[-50_000:]
            status = "passed" if exit_code == 0 else "failed"
            return TestResult(
                status,
                command,
                exit_code,
                output,
                int((time.monotonic() - start) * 1000),
            )
        except subprocess.TimeoutExpired:
            return TestResult(
                "failed",
                command or [interpreter],
                None,
                f"测试超过 {self.settings.runner_timeout_seconds} 秒",
                int((time.monotonic() - start) * 1000),
            )
        except Exception as exc:
            return TestResult(
                "unverified",
                command,
                None,
                f"无法执行测试: {exc}",
                int((time.monotonic() - start) * 1000),
            )
        finally:
            self.cache.remove_worktree(repository.id, workspace)

    def _prepare_dependencies(
        self,
        repository: Repository,
        runtime: RepositoryRuntimeProfile,
        workspace: Path,
    ) -> list[str]:
        env_key = f"{runtime.lockfile_hash or 'unlocked'}-{runtime.runtime_version or 'default'}"
        dependency_env = self.settings.dependency_root / f"repository-{repository.id}" / env_key
        marker = dependency_env / ".ready"
        if not marker.exists() and runtime.install_argv:
            if dependency_env.exists():
                shutil.rmtree(dependency_env)
            dependency_env.mkdir(parents=True, exist_ok=True)
            source_root = workspace / runtime.working_directory
            for name in (
                "package.json",
                "package-lock.json",
                "pnpm-lock.yaml",
                "pnpm-workspace.yaml",
                "yarn.lock",
                ".npmrc",
                "pyproject.toml",
                "uv.lock",
                "requirements.txt",
            ):
                source = source_root / name
                if source.is_file():
                    shutil.copy2(source, dependency_env / name)
            install = list(runtime.install_argv)
            if runtime.language == "python" and install[:2] == ["uv", "sync"]:
                install.append("--no-install-project")
            if runtime.language == "python" and install[:3] == ["uv", "pip", "install"]:
                created = self.command_runner.run(
                    ["uv", "venv", ".venv"],
                    dependency_env,
                    writable_paths=[dependency_env, self.settings.dependency_root],
                )
                if created.returncode != 0:
                    raise RuntimeError((created.stdout + created.stderr)[-10_000:])
                install = [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(dependency_env / ".venv/bin/python"),
                    *install[3:],
                ]
            completed = self.command_runner.run(
                install,
                dependency_env,
                writable_paths=[dependency_env, self.settings.dependency_root],
            )
            if completed.returncode != 0:
                detail = (completed.stdout + "\n" + completed.stderr).strip()[-10_000:]
                raise RuntimeError(f"依赖安装失败: {detail}")
            marker.write_text("ready\n", encoding="utf-8")
        if runtime.language == "node":
            source = dependency_env / "node_modules"
            target = workspace / runtime.working_directory / "node_modules"
            if source.exists() and not target.exists():
                target.symlink_to(source, target_is_directory=True)
            return list(runtime.test_argv)
        python = dependency_env / ".venv/bin/python"
        if python.is_file():
            test = list(runtime.test_argv)
            pytest_index = test.index("pytest") if "pytest" in test else len(test)
            return [str(python), "-m", "pytest", *test[pytest_index + 1 :]]
        return list(runtime.test_argv)


def command_text(argv: list[str]) -> str:
    return shlex.join(argv)
