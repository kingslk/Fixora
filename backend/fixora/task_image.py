from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from pathlib import Path

MAX_TASK_IMAGE_BYTES = 8 * 1024 * 1024

_FORMATS: dict[str, tuple[str, bytes]] = {
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
    "image/webp": (".webp", b"RIFF"),
}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class TaskImageError(ValueError):
    pass


@dataclass(frozen=True)
class TaskImage:
    mime: str
    suffix: str
    name: str
    content: bytes


def decode_image_data_url(data_url: str, name: str | None = None) -> TaskImage:
    if not data_url.startswith("data:") or "," not in data_url:
        raise TaskImageError("图片必须是 data URL")
    header, encoded = data_url[5:].split(",", 1)
    parts = header.split(";")
    mime = parts[0].strip().lower()
    if mime not in _FORMATS or "base64" not in {item.strip().lower() for item in parts[1:]}:
        raise TaskImageError("仅支持 PNG、JPEG、WebP 图片")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise TaskImageError("图片数据无效") from exc
    if not content:
        raise TaskImageError("图片不能为空")
    if len(content) > MAX_TASK_IMAGE_BYTES:
        raise TaskImageError("图片不能超过 8 MB")
    suffix, magic = _FORMATS[mime]
    if not content.startswith(magic) or (mime == "image/webp" and content[8:12] != b"WEBP"):
        raise TaskImageError("图片内容与声明格式不一致")
    safe_name = _SAFE_NAME.sub("-", Path(name or "").name).strip(".-")
    return TaskImage(
        mime=mime,
        suffix=suffix,
        name=(safe_name or f"screenshot{suffix}"),
        content=content,
    )


def save_task_image(root: Path, task_id: int, image: TaskImage) -> Path:
    directory = root / f"task-{task_id}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"input-image{image.suffix}"
    temporary = directory / f".input-image{image.suffix}.tmp"
    temporary.write_bytes(image.content)
    temporary.replace(path)
    return path
