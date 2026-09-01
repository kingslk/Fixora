from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .crypto import decrypt_json, encrypt_json
from .models import SystemSetting, utcnow


class SettingsStore:
    """只存 browser 这类可写项。GitLab / 模型配置不走这里。"""
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, key: str) -> dict[str, Any] | None:
        row = self.db.get(SystemSetting, key)
        if row is None:
            return None
        value = decrypt_json(row.encrypted_value)
        return value if isinstance(value, dict) else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        row = self.db.get(SystemSetting, key)
        if row is None:
            row = SystemSetting(key=key, encrypted_value="")
            self.db.add(row)
        row.encrypted_value = encrypt_json(value)
        row.updated_at = utcnow()
        self.db.flush()

    def merge_secret(
        self,
        key: str,
        value: dict[str, Any],
        *,
        secret_fields: tuple[str, ...],
    ) -> dict[str, Any]:
        current = self.get(key) or {}
        merged = {**current, **value}
        for field in secret_fields:
            if value.get(field) in (None, ""):
                if field in current:
                    merged[field] = current[field]
                else:
                    merged.pop(field, None)
        self.put(key, merged)
        return merged


def public_settings(value: dict[str, Any] | None, *, secrets: set[str]) -> dict[str, Any]:
    if not value:
        return {}
    return {key: ("••••••••" if key in secrets and item else item) for key, item in value.items()}
