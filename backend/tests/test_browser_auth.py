from __future__ import annotations

import json

import pytest

from fixora.browser.auth import BrowserAuthError, normalize_auth_import, state_for_url


def test_local_storage_requires_origin_and_matches_exact_scheme() -> None:
    with pytest.raises(BrowserAuthError):
        normalize_auth_import(json.dumps({"token": "secret"}))
    kind, state = normalize_auth_import(
        json.dumps({"token": "secret"}), "https://issues.example.com/path"
    )
    assert kind == "localStorage"
    assert state["origin"] == "https://issues.example.com"
    assert state_for_url([state], "https://issues.example.com/123") is not None
    assert state_for_url([state], "http://issues.example.com/123") is None
    assert state_for_url([state], "https://evil.example.net/") is None


def test_cookie_text_is_normalized_without_echoing_other_domains() -> None:
    kind, state = normalize_auth_import("sid=abc; theme=dark", "https://sentry.example.com")
    assert kind == "cookie"
    matched = state_for_url([state], "https://sentry.example.com/issues/1")
    assert matched is not None
    assert {item["name"] for item in matched["cookies"]} == {"sid", "theme"}
