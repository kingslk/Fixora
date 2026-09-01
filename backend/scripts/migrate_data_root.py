from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fixora.paths import default_data_root  # noqa: E402


def _count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dest)
        os.chmod(dest, stat.S_IRUSR | stat.S_IWUSR)


def main() -> int:
    parser = argparse.ArgumentParser(description="把 Fixora data root 复制到新位置（默认 dry-run）")
    parser.add_argument("--from", dest="source", required=True, type=Path)
    parser.add_argument("--to", dest="target", type=Path, default=None)
    parser.add_argument("--apply", action="store_true", help="真正复制；默认只打印计划")
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    target = (args.target or default_data_root()).expanduser().resolve()
    if not source.exists():
        print(f"源目录不存在: {source}")
        return 1
    secret = source / ".secret-key"
    git_count = _count_files(source / "git")
    dep_count = _count_files(source / "dependencies")
    artifact_count = _count_files(source / "artifacts")
    print(f"from: {source}")
    print(f"to:   {target}")
    print(f".secret-key: {'存在' if secret.is_file() else '缺失'}")
    print(f"git files: {git_count}")
    print(f"dependency files: {dep_count}")
    print(f"artifact files: {artifact_count}")
    print("不会删除源目录。启动前请停止 API/Worker，复制后设置 FIXORA_DATA_ROOT。")
    if not args.apply:
        print("dry-run。加 --apply 才复制。")
        return 0
    target.mkdir(parents=True, exist_ok=True)
    for name in (".secret-key", "git", "dependencies", "artifacts"):
        src = source / name
        if src.exists():
            _copy(src, target / name)
    copied_secret = target / ".secret-key"
    copied_git = _count_files(target / "git")
    copied_dep = _count_files(target / "dependencies")
    copied_art = _count_files(target / "artifacts")
    if secret.is_file() and not copied_secret.is_file():
        print("校验失败：目标缺少 .secret-key")
        return 1
    if copied_git != git_count or copied_dep != dep_count or copied_art != artifact_count:
        print("校验失败：文件数量不一致")
        return 1
    print("复制完成。请设置 FIXORA_DATA_ROOT 后启动，并验证旧 Task / 截图 / trace / fetch。")
    print("删除旧目录是单独的不可逆操作，本脚本不会执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
