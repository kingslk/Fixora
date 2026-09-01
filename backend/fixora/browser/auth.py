from __future__ import annotations

import json
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlparse


class BrowserAuthError(ValueError):
    pass


def normalize_origin(value: str) -> str:
    raw = value.strip()
    if "://" not in raw:
        raw = "https://" + raw.lstrip(".")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BrowserAuthError("origin 必须是 HTTP(S) 地址")
    return f"{parsed.scheme}://{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "")


def normalize_auth_import(raw: str, origin: str | None = None) -> tuple[str, dict[str, Any]]:
    text = raw.strip()
    if not text:
        raise BrowserAuthError("导入内容为空")
    normalized_origin = normalize_origin(origin) if origin else ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict) and "storage_state" in parsed:
        return normalize_auth_import(json.dumps(parsed["storage_state"]), origin)
    if isinstance(parsed, dict) and ("cookies" in parsed or "origins" in parsed):
        cookies = parsed.get("cookies") or []
        origins = parsed.get("origins") or []
        if not isinstance(cookies, list) or not isinstance(origins, list):
            raise BrowserAuthError("storage_state 格式无效")
        for cookie in cookies:
            if isinstance(cookie, dict):
                cookie.setdefault("path", "/")
                cookie.setdefault("sameSite", "Lax")
        profile_origin = normalized_origin or _origin_from_state(cookies, origins)
        if not profile_origin:
            raise BrowserAuthError("storage_state 无法推断 origin，请显式填写")
        return "storage_state", {"cookies": cookies, "origins": origins, "origin": profile_origin}
    if isinstance(parsed, list):
        cookies = [dict(item) for item in parsed if isinstance(item, dict) and item.get("name")]
        if not cookies:
            raise BrowserAuthError("Cookie JSON 数组为空")
        for cookie in cookies:
            cookie.setdefault("path", "/")
            cookie.setdefault("sameSite", "Lax")
        profile_origin = normalized_origin or _origin_from_state(cookies, [])
        if not profile_origin:
            raise BrowserAuthError("Cookie JSON 无法推断 origin，请显式填写")
        return "cookie", {"cookies": cookies, "origins": [], "origin": profile_origin}
    if isinstance(parsed, dict):
        if not normalized_origin:
            raise BrowserAuthError("localStorage 导入必须填写 origin")
        entries = [{"name": str(key), "value": str(value)} for key, value in parsed.items()]
        return "localStorage", {
            "cookies": [],
            "origins": [{"origin": normalized_origin, "localStorage": entries}],
            "origin": normalized_origin,
        }
    if not normalized_origin:
        raise BrowserAuthError("Cookie 文本导入必须填写 origin")
    cookie_jar = SimpleCookie()
    cookie_jar.load(text)
    if not cookie_jar:
        raise BrowserAuthError("Cookie 文本格式无效")
    host = urlparse(normalized_origin).hostname or ""
    cookies = [
        {
            "name": name,
            "value": morsel.value,
            "domain": host,
            "path": "/",
            "httpOnly": False,
            "secure": normalized_origin.startswith("https://"),
            "sameSite": "Lax",
        }
        for name, morsel in cookie_jar.items()
    ]
    return "cookie", {"cookies": cookies, "origins": [], "origin": normalized_origin}


def _origin_from_state(cookies: list[Any], origins: list[Any]) -> str:
    for item in origins:
        if isinstance(item, dict) and item.get("origin"):
            return normalize_origin(str(item["origin"]))
    for cookie in cookies:
        if isinstance(cookie, dict) and cookie.get("domain"):
            scheme = "https" if cookie.get("secure", True) else "http"
            return normalize_origin(f"{scheme}://{str(cookie['domain']).lstrip('.')}")
    return ""


def state_for_url(profiles: list[dict[str, Any]], url: str) -> dict[str, Any] | None:
    target = urlparse(url)
    if not target.hostname:
        return None
    target_origin = normalize_origin(url)
    cookies: list[dict[str, Any]] = []
    origins: list[dict[str, Any]] = []
    for state in profiles:
        profile_origin = str(state.get("origin") or "")
        parsed = urlparse(profile_origin)
        if parsed.scheme != target.scheme:
            continue
        host = parsed.hostname or ""
        if target.hostname != host and not target.hostname.endswith("." + host):
            continue
        cookies.extend(item for item in state.get("cookies", []) if isinstance(item, dict))
        origins.extend(
            item
            for item in state.get("origins", [])
            if isinstance(item, dict) and item.get("origin") == target_origin
        )
    return {"cookies": cookies, "origins": origins} if cookies or origins else None
