from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


class SecretError(ValueError):
    pass


def _fernet() -> Fernet:
    raw = _local_key().encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


def _local_key() -> str:
    # 页面登录态的 Fernet 材料，不是环境变量里的 GitLab/模型密钥。禁止提交到 git。
    path = get_settings().data_root / ".secret-key"
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        value = secrets.token_urlsafe(32)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            value = path.read_text(encoding="utf-8").strip()
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(value + "\n")
    if not value:
        raise SecretError(f"本地加密密钥为空: {path}")
    return value


def encrypt_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    return _fernet().encrypt(payload).decode()


def decrypt_json(value: str) -> Any:
    try:
        payload = _fernet().decrypt(value.encode())
    except InvalidToken as exc:
        raise SecretError("无法解密配置；FIXORA_SECRET_KEY 可能已变更") from exc
    return json.loads(payload)
