"""Mask discovery helpers.

This repo's masking workflow uses:
- model cushion mask (model-mask pass), and
- donor piping mask (pre-edit prompt),
plus their intersection for edit/composite.

No external auto-masking generation exists in this codebase.
"""

from __future__ import annotations

from pathlib import Path

MASK_EXTENSIONS = (".png", ".tif", ".tiff", ".jpg", ".jpeg", ".webp")


def find_mask_for_product(masks_dir: Path, product_name: str) -> Path | None:
    """Find a mask file in `masks_dir` that matches `product_name`.

    This is a last-resort discovery helper used mainly for calibration tooling.
    """
    if not masks_dir.exists():
        return None

    # Prefer direct exact matches.
    for ext in MASK_EXTENSIONS:
        candidate = masks_dir / f"{product_name}{ext}"
        if candidate.exists():
            return candidate

    # Fallback: any file containing product_name (case-insensitive).
    needles = {product_name.lower(), product_name.replace(" ", "_").lower()}
    best: Path | None = None
    best_mtime = -1.0
    for path in masks_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in MASK_EXTENSIONS:
            continue
        name = path.name.lower()
        if any(n in name for n in needles):
            mtime = path.stat().st_mtime
            if mtime > best_mtime:
                best = path
                best_mtime = mtime
    return best
