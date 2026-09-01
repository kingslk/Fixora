from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from playwright.async_api import Browser, async_playwright
from playwright.async_api import Error as PlaywrightError

_TEXTBOX_VALUE_RE = re.compile(r'(?m)^(\s*-\s*textbox(?:\s+"[^"]*")?:)\s+.*$')
_SECRET_QUERY_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "authorization",
    "api_key",
    "cookie",
    "session",
    "sid",
    "jwt",
}


class CaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class CaptureResult:
    requested_url: str
    final_url: str
    title: str
    text: str
    screenshot_path: Path
    insecure_http: bool
    truncated: bool


def safe_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CaptureError("问题链接只允许 HTTP(S)")
    pairs = [
        (key, "***" if key.lower() in _SECRET_QUERY_KEYS else item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunparse(parsed._replace(query=urlencode(pairs)))


def validate_target(value: str) -> None:
    """禁止 localhost / 链路本地 / 元数据地址，避免 SSRF 打到内网管理面。"""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CaptureError("问题链接只允许 HTTP(S)")
    if parsed.hostname.lower() == "localhost" or parsed.hostname.lower().endswith(".localhost"):
        raise CaptureError("禁止访问 localhost")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
            )
        }
    except OSError as exc:
        raise CaptureError(f"无法解析问题链接域名: {exc}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise CaptureError("禁止访问 loopback、link-local 或 metadata 地址")


class PageCaptureService:
    def __init__(
        self,
        *,
        artifact_root: Path,
        timeout_seconds: int,
        scroll_limit_px: int,
        headless: bool = True,
    ) -> None:
        self.artifact_root = artifact_root
        self.timeout_seconds = timeout_seconds
        self.scroll_limit_px = scroll_limit_px
        self.headless = headless

    async def capture(
        self,
        task_id: int,
        url: str,
        storage_state: dict[str, object] | None,
        attempt_no: int = 1,
    ) -> CaptureResult:
        validate_target(url)
        from ..paths import artifact_dir

        directory = artifact_dir(self.artifact_root, task_id, attempt_no)
        directory.mkdir(parents=True, exist_ok=True)
        screenshot_path = directory / "source-page.png"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.headless)
            try:
                return await asyncio.wait_for(
                    self._capture(browser, url, storage_state, screenshot_path),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError as exc:
                raise CaptureError(f"页面采集超过 {self.timeout_seconds} 秒") from exc
            finally:
                await browser.close()

    async def _capture(
        self,
        browser: Browser,
        url: str,
        storage_state: dict[str, object] | None,
        screenshot_path: Path,
    ) -> CaptureResult:
        context = await browser.new_context(storage_state=storage_state or None)

        async def route_handler(route) -> None:
            try:
                await asyncio.to_thread(validate_target, route.request.url)
            except CaptureError:
                await route.abort("blockedbyclient")
                return
            await route.continue_()

        await context.route("**/*", route_handler)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded")
            validate_target(page.url)
            truncated = await self._scroll(page)
            body_text = (await page.locator("body").inner_text())[:200_000]
            try:
                aria = await page.locator("body").aria_snapshot()
            except (AttributeError, PlaywrightError):
                aria = ""
            aria = _TEXTBOX_VALUE_RE.sub(r"\1 [value redacted]", aria)[:100_000]
            await page.evaluate("window.scrollTo(0, 0)")
            if truncated:
                viewport = page.viewport_size or {"width": 1280, "height": 720}
                await page.screenshot(
                    path=str(screenshot_path),
                    clip={
                        "x": 0,
                        "y": 0,
                        "width": viewport["width"],
                        "height": self.scroll_limit_px,
                    },
                )
            else:
                await page.screenshot(path=str(screenshot_path), full_page=True)
            return CaptureResult(
                requested_url=safe_url(url),
                final_url=safe_url(page.url),
                title=await page.title(),
                text=f"{body_text}\n\n可访问性文本:\n{aria}".strip(),
                screenshot_path=screenshot_path,
                insecure_http=urlparse(page.url).scheme == "http",
                truncated=truncated or len(body_text) >= 200_000 or len(aria) >= 100_000,
            )
        finally:
            await context.close()

    async def _scroll(self, page) -> bool:
        travelled = 0
        while True:
            state = await page.evaluate(
                """() => {
                    const el = document.scrollingElement || document.documentElement;
                    return {top: el.scrollTop, height: el.clientHeight, total: el.scrollHeight};
                }"""
            )
            if state["top"] + state["height"] >= state["total"] - 1:
                return state["total"] > self.scroll_limit_px
            travelled += int(state["height"])
            if travelled >= self.scroll_limit_px:
                return True
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await page.wait_for_timeout(250)
