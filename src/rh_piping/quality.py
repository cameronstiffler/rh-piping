"""Lightweight image-quality scoring helpers (no numpy).

These utilities are intended to speed up iteration by quantifying:
- how unchanged the non-piping region stayed vs the donor, and
- whether changes actually occurred along the expected piping path.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from pathlib import Path
import io

from PIL import Image, ImageChops, ImageFilter, ImageOps


@dataclass(frozen=True)
class PipePathScore:
    pipe_region_pixels: int
    non_pipe_region_pixels: int
    changed_pipe_pixels: int
    changed_non_pipe_pixels: int
    changed_pipe_pct: float
    changed_non_pipe_pct: float
    mean_diff_pipe: float
    mean_diff_non_pipe: float

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def _parse_rgb_triplet(value: str) -> tuple[int, int, int]:
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 3:
        raise ValueError("Expected 'R,G,B'.")
    r, g, b = (int(parts[0]), int(parts[1]), int(parts[2]))
    for c in (r, g, b):
        if c < 0 or c > 255:
            raise ValueError("RGB values must be 0..255.")
    return r, g, b


def build_pipe_path_region_mask(
    pipe_path_png: bytes,
    size: tuple[int, int],
    *,
    path_rgb: tuple[int, int, int] = (255, 73, 73),
    color_tol: int = 40,
    expand_px: int = 8,
) -> Image.Image:
    """Return an L-mode mask (0/255) for the expected piping region.

    `pipe_path_png` is an RGBA image that contains a colored path overlay; we
    detect the path by RGB proximity and then expand it to a thicker region.
    """
    with Image.open(io.BytesIO(pipe_path_png)) as im:
        im = ImageOps.exif_transpose(im).convert("RGBA")
    if im.size != size:
        im = im.resize(size, Image.Resampling.NEAREST)

    r0, g0, b0 = path_rgb

    # Use a tolerance band around the annotated path RGB, requiring visible alpha.
    r, g, b, a = im.split()
    r = r.point(lambda p: 255 if abs(p - r0) <= color_tol else 0)
    g = g.point(lambda p: 255 if abs(p - g0) <= color_tol else 0)
    b = b.point(lambda p: 255 if abs(p - b0) <= color_tol else 0)
    rgb_match = ImageChops.multiply(ImageChops.multiply(r, g), b)
    a_on = a.point(lambda p: 255 if p > 0 else 0)
    path_mask = ImageChops.multiply(rgb_match, a_on).point(lambda p: 255 if p > 0 else 0)

    if expand_px > 0:
        # MaxFilter grows white regions. Radius is kernel = 2*expand + 1.
        kernel = expand_px * 2 + 1
        path_mask = path_mask.filter(ImageFilter.MaxFilter(size=kernel))
    return path_mask


def score_result_against_donor_along_pipe_path(
    *,
    donor_png: bytes,
    result_png: bytes,
    pipe_path_png: bytes,
    path_rgb: tuple[int, int, int] = (255, 73, 73),
    color_tol: int = 40,
    expand_px: int = 8,
    change_threshold: int = 8,
) -> tuple[PipePathScore, Image.Image]:
    """Score result vs donor, splitting inside/outside expected piping region.

    Returns (score, pipe_region_mask_L).
    """
    with Image.open(io.BytesIO(donor_png)) as donor_im:
        donor_im = ImageOps.exif_transpose(donor_im).convert("RGBA")
    with Image.open(io.BytesIO(result_png)) as result_im:
        result_im = ImageOps.exif_transpose(result_im).convert("RGBA")

    if donor_im.size != result_im.size:
        raise ValueError(
            f"Donor/result size mismatch: donor={donor_im.size} result={result_im.size}"
        )

    width, height = donor_im.size
    donor_alpha = donor_im.getchannel("A").point(lambda p: 255 if p > 0 else 0)
    pipe_region = build_pipe_path_region_mask(
        pipe_path_png,
        (width, height),
        path_rgb=path_rgb,
        color_tol=color_tol,
        expand_px=expand_px,
    )
    pipe_region = ImageChops.multiply(pipe_region, donor_alpha).point(lambda p: 255 if p > 0 else 0)
    non_pipe_region = ImageChops.subtract(donor_alpha, pipe_region)

    donor_rgb = donor_im.convert("RGB")
    result_rgb = result_im.convert("RGB")
    diff_rgb = ImageChops.difference(donor_rgb, result_rgb)
    diff_l = diff_rgb.convert("L")

    # Per-region mean diff (0..255).
    pipe_diff = ImageChops.multiply(diff_l, pipe_region)
    non_pipe_diff = ImageChops.multiply(diff_l, non_pipe_region)

    pipe_hist = pipe_diff.histogram()
    non_pipe_hist = non_pipe_diff.histogram()

    def _mean_from_hist(hist: list[int], denom: int) -> float:
        if denom <= 0:
            return 0.0
        total = 0
        for v, n in enumerate(hist):
            total += v * n
        return total / denom

    pipe_region_pixels = pipe_region.histogram()[255]
    non_pipe_region_pixels = non_pipe_region.histogram()[255]

    # Per-region changed pixels above threshold.
    diff_bin = diff_l.point(lambda p: 255 if p >= change_threshold else 0)
    pipe_changed = ImageChops.multiply(diff_bin, pipe_region).histogram()[255]
    non_pipe_changed = ImageChops.multiply(diff_bin, non_pipe_region).histogram()[255]

    score = PipePathScore(
        pipe_region_pixels=pipe_region_pixels,
        non_pipe_region_pixels=non_pipe_region_pixels,
        changed_pipe_pixels=pipe_changed,
        changed_non_pipe_pixels=non_pipe_changed,
        changed_pipe_pct=(pipe_changed / pipe_region_pixels) if pipe_region_pixels else 0.0,
        changed_non_pipe_pct=(non_pipe_changed / non_pipe_region_pixels)
        if non_pipe_region_pixels
        else 0.0,
        mean_diff_pipe=_mean_from_hist(pipe_hist, pipe_region_pixels),
        mean_diff_non_pipe=_mean_from_hist(non_pipe_hist, non_pipe_region_pixels),
    )
    return score, pipe_region


def parse_pipe_path_rgb_env(value: str | None) -> tuple[int, int, int] | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return _parse_rgb_triplet(value)

