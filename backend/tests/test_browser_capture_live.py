from __future__ import annotations

import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from fixora.browser.capture import PageCaptureService


@pytest.mark.asyncio
async def test_full_page_capture_scrolls_and_redacts(monkeypatch, tmp_path: Path) -> None:
    fixture_dir = Path(__file__).parent / "fixtures"
    handler = lambda *args, **kwargs: SimpleHTTPRequestHandler(  # noqa: E731
        *args, directory=fixture_dir, **kwargs
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr("fixora.browser.capture.validate_target", lambda _: None)
    try:
        service = PageCaptureService(
            artifact_root=tmp_path,
            timeout_seconds=15,
            scroll_limit_px=5_000,
        )
        result = await service.capture(
            1,
            f"http://127.0.0.1:{server.server_port}/scroll.html",
            None,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert result.title == "Fixora 页面采集测试"
    assert "页面底部证据" in result.text
    assert "不得进入快照的表单值" not in result.text
    assert result.screenshot_path.stat().st_size > 1_000
    assert result.insecure_http is True
    assert result.truncated is False
