from __future__ import annotations

from pathlib import Path

import pytest

from fixora.task_image import TaskImageError, decode_image_data_url, save_task_image

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="


def test_decode_and_save_task_image(tmp_path: Path) -> None:
    image = decode_image_data_url(PNG, "clipboard screenshot.png")
    assert image.mime == "image/png"
    assert image.name == "clipboard-screenshot.png"
    path = save_task_image(tmp_path, 7, image)
    assert path == tmp_path / "task-7" / "input-image.png"
    assert path.read_bytes() == image.content


@pytest.mark.parametrize(
    "value",
    [
        "not-a-data-url",
        "data:image/png;base64,YWJj",
        "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yw=",
    ],
)
def test_rejects_invalid_task_image(value: str) -> None:
    with pytest.raises(TaskImageError):
        decode_image_data_url(value)
