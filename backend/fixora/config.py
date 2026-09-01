from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .paths import default_data_root


def normalize_model_base_url(value: str) -> str:
    """接受网关根地址或完整 endpoint，统一成 OpenAI client base_url。"""
    raw = value.strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("FIXORA_MODEL_API_URL 必须是 HTTP(S) 地址")
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, "")).rstrip("/") + "/"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FIXORA_",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://fixora:fixora@127.0.0.1:5433/fixora"
    redis_url: str = "redis://127.0.0.1:6380/0"
    data_root: Path = Field(default_factory=default_data_root)
    web_origin: str = "http://127.0.0.1:5173"
    systemd_runner_enabled: bool = False
    systemd_runner_user: str = "fixora-runner"
    runner_timeout_seconds: int = 600
    runner_memory_max: str = "4G"
    runner_cpu_quota: str = "200%"
    browser_timeout_seconds: int = 30
    browser_scroll_limit_px: int = 20_000
    browser_headless: bool = True
    gitlab_base_url: str = ""
    gitlab_token: str = ""
    # 内网自签证书：false 不是漏做。同时作用于 GitLab API 和 git fetch。
    gitlab_ssl_verify: bool = False
    gitlab_ca_bundle: Path | None = None
    model_api_url: str = ""
    model_api_key: str = ""
    model_api_mode: Literal["responses", "chat_completions"] = "responses"
    model_name: str = ""
    model_reasoning_effort: Literal["none", "low", "medium", "high"] = "medium"
    model_parameters_json: str = "{}"
    model_ssl_verify: bool = False
    # 关 tracing，避免第三方网关的调用轨迹发往 OpenAI。不要为此填 OPENAI_API_KEY。
    model_tracing_enabled: bool = False

    @property
    def git_root(self) -> Path:
        return self.data_root / "git"

    @property
    def dependency_root(self) -> Path:
        return self.data_root / "dependencies"

    @property
    def artifact_root(self) -> Path:
        return self.data_root / "artifacts"

    def model_runtime_config(self) -> dict[str, Any]:
        try:
            parameters = json.loads(self.model_parameters_json)
        except json.JSONDecodeError as exc:
            raise ValueError("FIXORA_MODEL_PARAMETERS_JSON 不是有效 JSON") from exc
        if not isinstance(parameters, dict):
            raise ValueError("FIXORA_MODEL_PARAMETERS_JSON 必须是 JSON 对象")
        reserved = {"model", "api_key", "base_url", "tools", "messages", "input", "stream"}
        overlap = reserved.intersection(parameters)
        if overlap:
            raise ValueError(
                f"FIXORA_MODEL_PARAMETERS_JSON 不允许覆盖: {', '.join(sorted(overlap))}"
            )
        return {
            "api_url": self.model_api_url,
            "base_url": normalize_model_base_url(self.model_api_url) if self.model_api_url else "",
            "api_key": self.model_api_key,
            "api_mode": self.model_api_mode,
            "model": self.model_name,
            "reasoning_effort": self.model_reasoning_effort,
            "parameters": parameters,
            "ssl_verify": self.model_ssl_verify,
            "tracing_enabled": self.model_tracing_enabled,
        }

    def gitlab_runtime_config(self) -> dict[str, Any]:
        return {
            "base_url": self.gitlab_base_url,
            "token": self.gitlab_token,
            "ssl_verify": self.gitlab_ssl_verify,
            "ca_bundle": str(self.gitlab_ca_bundle) if self.gitlab_ca_bundle else None,
        }

    def model_http_verify(self) -> bool:
        return self.model_ssl_verify


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    for path in (
        settings.data_root,
        settings.git_root,
        settings.dependency_root,
        settings.artifact_root,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return settings
