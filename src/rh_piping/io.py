"""Filesystem helpers for image discovery."""

from __future__ import annotations

from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}


def list_images(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return [
        path
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
