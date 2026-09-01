from __future__ import annotations

from pathlib import PurePosixPath

# 隐藏目录会排在 git ls-tree 最前，进 list_files 会淹没源码。一律过滤，不单拦 node_modules。

IGNORED_DIRECTORIES = frozenset(
    {
        ".cache",
        ".claude",
        ".codex",
        ".cursor",
        ".git",
        ".grok",
        ".idea",
        ".next",
        ".nuxt",
        ".output",
        ".pnpm-store",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".turbo",
        ".venv",
        ".vs",
        ".vscode",
        ".yarn",
        "__pycache__",
        "coverage",
        "dist",
        "node_modules",
        "out",
        "release",
        "target",
        "vendor",
        "venv",
    }
)

IGNORED_NAMES = frozenset({".ds_store", ".envrc"})

IGNORED_SUFFIXES = (
    ".avif",
    ".bmp",
    ".eot",
    ".gif",
    ".heic",
    ".ico",
    ".jpeg",
    ".jpg",
    ".map",
    ".min.js",
    ".otf",
    ".png",
    ".svg",
    ".tif",
    ".tiff",
    ".ttf",
    ".webp",
    ".woff",
    ".woff2",
)


def is_ignored_path(value: str) -> bool:
    path = PurePosixPath(value.strip().lstrip("/"))
    name = path.name.lower()
    if name in IGNORED_NAMES or name.startswith(".env"):
        return True
    parents = path.parts[:-1]
    directories = {part.lower() for part in parents}
    if directories.intersection(IGNORED_DIRECTORIES):
        return True
    if any(_is_hidden_directory(part) for part in parents):
        return True
    return name.endswith(IGNORED_SUFFIXES)


def _is_hidden_directory(part: str) -> bool:
    return part.startswith(".") and part not in {".", ".."}


def git_exclude_pathspecs() -> list[str]:
    pathspecs: list[str] = [
        ":(exclude).*/**",
        ":(exclude)**/.*/**",
    ]
    for directory in sorted(IGNORED_DIRECTORIES):
        pathspecs.extend(
            [
                f":(exclude){directory}/**",
                f":(exclude)**/{directory}/**",
            ]
        )
    pathspecs.extend(f":(exclude)**/*{suffix}" for suffix in IGNORED_SUFFIXES)
    pathspecs.extend(f":(exclude)**/{name}" for name in sorted(IGNORED_NAMES))
    pathspecs.append(":(exclude)**/.env*")
    return pathspecs
