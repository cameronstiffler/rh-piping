"""Mask discovery and SAM2 mask generation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import io
import os
import tempfile
import time
import urllib.request

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

from rh_piping.io import ensure_dir

try:  # Optional dependency used only when generating masks.
    from gradio_client import Client, handle_file
except ImportError:  # pragma: no cover - handled at runtime.
    Client = None
    handle_file = None

MASK_EXTENSIONS = [".png", ".tif", ".tiff", ".jpg", ".jpeg", ".webp"]
EDGE_TOP_PERCENTS = (0.01, 0.02, 0.04, 0.06)
DIFF_TOP_PERCENTS = (0.005, 0.01, 0.02, 0.04, 0.06)
EDGE_BAND_PX = 2
EDGE_DETECT_BLUR = 2
MASK_MIN_COVERAGE = 0.001
MASK_MAX_COVERAGE = 0.06
MASK_SCORE_EDGE_PERCENT = 0.04
MASK_SCORE_ACCEPT = 0.55
MASK_FORCE_MIN_COVERAGE = 0.003
LOW_TEXTURE_PERCENT = 0.15
LOW_TEXTURE_EXPAND_PERCENT = 0.4
TEXTURE_BLUR = 2
SMOOTH_EDGE_KEEP = 0.2
SMOOTH_EDGE_BAND = 2
CUSHION_BRIGHT_PERCENT = 0.25
CUSHION_CLOSE_KERNEL = 7
CUSHION_EDGE_BAND = 3
CUSHION_SEAM_EDGE_PERCENT = 0.02
CUSHION_SEAM_DILATE = 2
PIPING_LUMA_PERCENT = 0.2
PIPING_EDGE_PERCENT = 0.02
PIPING_EDGE_DILATE = 2
PIPING_EDGE_V2_PERCENT = 0.03
PIPING_EDGE_V2_KEEP = 0.25
PIPING_EDGE_V2_DILATE = 1
SILHOUETTE_EDGE_BAND = 4
SILHOUETTE_LUMA_PERCENT = 0.35
SAM2_MAX_LONG_EDGE = 1024
SAM2_RETRIES = 3
SAM2_RETRY_SLEEP = 2.0
SAM2_HTTP_TIMEOUT = 120.0
BOX_DOWNSCALE = 4
BOX_MAX = 24
BOX_MIN_AREA = 20
BOX_MIN_ASPECT = 3.0
BOX_PADDING = 6
MASK_CONTRAST_FACTOR = 1.8
MASK_AUTOCONTRAST_CUTOFF = 1


def find_mask_for_product(masks_dir: Path, product_name: str) -> Path | None:
    for ext in MASK_EXTENSIONS:
        candidate = masks_dir / f"{product_name}{ext}"
        if candidate.exists():
            return candidate
    return None


def _load_gradio_image(result: Any) -> Image.Image:
    if isinstance(result, dict):
        result = result.get("image") or result.get("value") or result.get("path")
    if isinstance(result, list) and result:
        result = result[0]
    if hasattr(result, "name"):
        result = result.name
    if isinstance(result, str):
        if result.startswith("http://") or result.startswith("https://"):
            with urllib.request.urlopen(result, timeout=30) as response:
                data = response.read()
            img = Image.open(io.BytesIO(data))
        else:
            img = Image.open(result)
        img.load()
        return img
    raise ValueError("Unexpected SAM2 response format for mask image.")


def _mask_from_overlay(
    donor: Image.Image,
    overlay: Image.Image,
    threshold: int,
) -> Image.Image:
    diff = _diff_map(donor, overlay)
    return _mask_from_diff(diff, threshold)


def _diff_map(donor: Image.Image, overlay: Image.Image) -> Image.Image:
    donor = donor.convert("RGB")
    overlay = overlay.convert("RGB")
    if overlay.size != donor.size:
        overlay = ImageOps.fit(overlay, donor.size, Image.LANCZOS, centering=(0.5, 0.5))
    return ImageChops.difference(overlay, donor).convert("L")


def _mask_from_diff(diff_map: Image.Image, threshold: int) -> Image.Image:
    return diff_map.point(lambda p: 255 if p > threshold else 0)


def _alpha_binary(alpha: Image.Image) -> Image.Image:
    return alpha.point(lambda p: 255 if p > 0 else 0)


def _pad_alpha_to_size(alpha: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    if alpha.size == target_size:
        return alpha
    src_w, src_h = alpha.size
    tgt_w, tgt_h = target_size
    if src_w == tgt_w and tgt_h >= src_h:
        padded = Image.new("L", target_size, 0)
        offset = (0, (tgt_h - src_h) // 2)
        padded.paste(alpha, offset)
        return padded
    return ImageOps.fit(alpha, target_size, Image.NEAREST, centering=(0.5, 0.5))


def _boost_contrast(image: Image.Image) -> Image.Image:
    boosted = ImageOps.autocontrast(image, cutoff=MASK_AUTOCONTRAST_CUTOFF)
    if MASK_CONTRAST_FACTOR != 1.0:
        boosted = ImageEnhance.Contrast(boosted).enhance(MASK_CONTRAST_FACTOR)
    return boosted


def _coverage(mask: Image.Image, alpha: Image.Image | None) -> float:
    if alpha is not None:
        mask = ImageChops.multiply(mask, alpha)
    hist = mask.histogram()
    total = sum(hist)
    covered = total - hist[0]
    return covered / total if total else 0.0


def _percentile_threshold(hist: list[int], top_percent: float) -> int:
    total = sum(hist)
    if total <= 0:
        return 0
    target = max(1, int(round(total * top_percent)))
    cumulative = 0
    for value in range(255, -1, -1):
        cumulative += hist[value]
        if cumulative >= target:
            return value
    return 0


def _percentile_threshold_nonzero(hist: list[int], top_percent: float) -> int:
    total = sum(hist[1:])
    if total <= 0:
        return 0
    target = max(1, int(round(total * top_percent)))
    cumulative = 0
    for value in range(255, 0, -1):
        cumulative += hist[value]
        if cumulative >= target:
            return value
    return 1


def _percentile_threshold_bottom(hist: list[int], bottom_percent: float) -> int:
    total = sum(hist)
    if total <= 0:
        return 0
    target = max(1, int(round(total * bottom_percent)))
    cumulative = 0
    for value in range(256):
        cumulative += hist[value]
        if cumulative >= target:
            return value
    return 255


def _threshold_candidates(center: int, hist: list[int]) -> list[int]:
    thresholds: set[int] = set()
    center = max(1, min(255, center))
    thresholds.add(center)
    for percent in DIFF_TOP_PERCENTS:
        thresholds.add(max(1, _percentile_threshold(hist, percent)))
    for step in (4, 8, 12, 16, 24, 32, 48, 64):
        thresholds.add(max(1, min(255, center + step)))
        thresholds.add(max(1, min(255, center - step)))
    return sorted(thresholds)


def _edge_mask(donor_rgb: Image.Image, alpha: Image.Image, top_percent: float) -> Image.Image:
    blurred = donor_rgb.filter(ImageFilter.GaussianBlur(EDGE_DETECT_BLUR))
    edges = blurred.filter(ImageFilter.FIND_EDGES).convert("L")
    edges = ImageChops.multiply(edges, alpha)
    thresh = _percentile_threshold(edges.histogram(), top_percent)
    mask = edges.point(lambda p: 255 if p > thresh else 0)
    if EDGE_BAND_PX > 0:
        mask = mask.filter(ImageFilter.MaxFilter(EDGE_BAND_PX * 2 + 1))
    return mask


def _low_texture_mask(
    donor_rgb: Image.Image,
    alpha: Image.Image,
    keep_percent: float,
) -> Image.Image:
    gray = donor_rgb.convert("L")
    blur = gray.filter(ImageFilter.GaussianBlur(TEXTURE_BLUR))
    texture = ImageChops.difference(gray, blur)
    texture = ImageChops.multiply(texture, alpha)
    thresh = _percentile_threshold_bottom(texture.histogram(), keep_percent)
    return texture.point(lambda p: 255 if p <= thresh else 0)


def _smooth_edge_mask(
    donor_rgb: Image.Image,
    alpha: Image.Image,
    keep_percent: float,
    band_px: int,
) -> Image.Image:
    smooth = _low_texture_mask(donor_rgb, alpha, keep_percent)
    if band_px < 1:
        band_px = 1
    kernel = band_px * 2 + 1
    dilated = smooth.filter(ImageFilter.MaxFilter(kernel))
    eroded = smooth.filter(ImageFilter.MinFilter(kernel))
    edges = ImageChops.subtract(dilated, eroded)
    edges = ImageChops.multiply(edges, alpha)
    edge_filter = _edge_mask(donor_rgb, alpha, EDGE_TOP_PERCENTS[0])
    edges = ImageChops.multiply(edges, smooth)
    return ImageChops.multiply(edges, edge_filter)


def _cushion_edge_mask(
    donor_rgb: Image.Image,
    alpha: Image.Image,
) -> Image.Image:
    gray = donor_rgb.convert("L")
    low_texture = _low_texture_mask(donor_rgb, alpha, LOW_TEXTURE_PERCENT)
    masked = ImageChops.multiply(gray, low_texture)
    bright_thresh = _percentile_threshold_nonzero(
        masked.histogram(),
        CUSHION_BRIGHT_PERCENT,
    )
    bright = gray.point(lambda p: 255 if p >= bright_thresh else 0)
    cushion = ImageChops.multiply(bright, low_texture)
    cushion = ImageChops.multiply(cushion, alpha)
    close_kernel = max(3, CUSHION_CLOSE_KERNEL)
    if close_kernel % 2 == 0:
        close_kernel += 1
    cushion = cushion.filter(ImageFilter.MaxFilter(close_kernel))
    cushion = cushion.filter(ImageFilter.MinFilter(close_kernel))
    band_px = max(1, CUSHION_EDGE_BAND)
    band_kernel = band_px * 2 + 1
    dilated = cushion.filter(ImageFilter.MaxFilter(band_kernel))
    eroded = cushion.filter(ImageFilter.MinFilter(band_kernel))
    edges = ImageChops.subtract(dilated, eroded)
    edges = ImageChops.multiply(edges, low_texture)
    seam_edges = _edge_mask(donor_rgb, alpha, CUSHION_SEAM_EDGE_PERCENT)
    seam_edges = ImageChops.multiply(seam_edges, cushion)
    seam_kernel = max(1, CUSHION_SEAM_DILATE)
    if seam_kernel > 0:
        seam_edges = seam_edges.filter(ImageFilter.MaxFilter(seam_kernel * 2 + 1))
    combined = ImageChops.lighter(edges, seam_edges)
    return ImageChops.multiply(combined, alpha)


def _piping_luma_edge_mask(
    donor_rgb: Image.Image,
    alpha: Image.Image,
) -> Image.Image:
    gray = donor_rgb.convert("L")
    low_texture = _low_texture_mask(donor_rgb, alpha, LOW_TEXTURE_PERCENT)
    masked = ImageChops.multiply(gray, low_texture)
    luma_thresh = _percentile_threshold_nonzero(
        masked.histogram(),
        PIPING_LUMA_PERCENT,
    )
    bright = gray.point(lambda p: 255 if p >= luma_thresh else 0)
    bright = ImageChops.multiply(bright, low_texture)
    edges = _edge_mask(donor_rgb, alpha, PIPING_EDGE_PERCENT)
    candidate = ImageChops.multiply(bright, edges)
    dilate = max(1, PIPING_EDGE_DILATE)
    if dilate > 0:
        candidate = candidate.filter(ImageFilter.MaxFilter(dilate * 2 + 1))
    return ImageChops.multiply(candidate, alpha)


def _piping_edge_mask_v2(
    donor_rgb: Image.Image,
    alpha: Image.Image,
) -> Image.Image:
    low_texture = _low_texture_mask(donor_rgb, alpha, PIPING_EDGE_V2_KEEP)
    edges = _edge_mask(donor_rgb, alpha, PIPING_EDGE_V2_PERCENT)
    edges = ImageChops.multiply(edges, low_texture)
    eroded = edges.filter(ImageFilter.MinFilter(3))
    thin = ImageChops.subtract(edges, eroded)
    dilate = max(1, PIPING_EDGE_V2_DILATE)
    if dilate > 0:
        thin = thin.filter(ImageFilter.MaxFilter(dilate * 2 + 1))
    return ImageChops.multiply(thin, alpha)


def _silhouette_edge_mask(
    donor_rgb: Image.Image,
    alpha: Image.Image,
) -> Image.Image:
    gray = donor_rgb.convert("L")
    low_texture = _low_texture_mask(donor_rgb, alpha, LOW_TEXTURE_PERCENT)
    masked = ImageChops.multiply(gray, low_texture)
    luma_thresh = _percentile_threshold_nonzero(
        masked.histogram(),
        SILHOUETTE_LUMA_PERCENT,
    )
    bright = gray.point(lambda p: 255 if p >= luma_thresh else 0)
    bright = ImageChops.multiply(bright, low_texture)
    band_px = max(1, SILHOUETTE_EDGE_BAND)
    band_kernel = band_px * 2 + 1
    dilated = alpha.filter(ImageFilter.MaxFilter(band_kernel))
    eroded = alpha.filter(ImageFilter.MinFilter(band_kernel))
    edge_band = ImageChops.subtract(dilated, eroded)
    edge_band = edge_band.filter(ImageFilter.MaxFilter(band_kernel))
    candidate = ImageChops.multiply(edge_band, bright)
    return ImageChops.multiply(candidate, alpha)


def _edge_boxes(
    donor_rgb: Image.Image,
    alpha: Image.Image,
    max_boxes: int,
) -> list[dict[str, int]]:
    base_w, base_h = donor_rgb.size
    scale = max(1, BOX_DOWNSCALE)
    small_size = (max(1, base_w // scale), max(1, base_h // scale))
    small_img = donor_rgb.resize(small_size, Image.LANCZOS)
    small_alpha = alpha.resize(small_size, Image.NEAREST)
    edge_small = _edge_mask(small_img, small_alpha, EDGE_TOP_PERCENTS[0])
    low_texture = _low_texture_mask(small_img, small_alpha, LOW_TEXTURE_PERCENT)
    edge_small = ImageChops.multiply(edge_small, low_texture)
    pixels = edge_small.load()
    width, height = edge_small.size
    visited = [bytearray(width) for _ in range(height)]
    boxes: list[tuple[int, int, int, int, int, int]] = []
    neighbors = [
        (-1, -1),
        (-1, 0),
        (-1, 1),
        (0, -1),
        (0, 1),
        (1, -1),
        (1, 0),
        (1, 1),
    ]
    for y in range(height):
        row = visited[y]
        for x in range(width):
            if pixels[x, y] == 0 or row[x]:
                continue
            stack = [(x, y)]
            row[x] = 1
            min_x = max_x = x
            min_y = max_y = y
            count = 0
            while stack:
                cx, cy = stack.pop()
                count += 1
                if cx < min_x:
                    min_x = cx
                if cx > max_x:
                    max_x = cx
                if cy < min_y:
                    min_y = cy
                if cy > max_y:
                    max_y = cy
                for dx, dy in neighbors:
                    nx = cx + dx
                    ny = cy + dy
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    if visited[ny][nx]:
                        continue
                    if pixels[nx, ny] == 0:
                        continue
                    visited[ny][nx] = 1
                    stack.append((nx, ny))
            box_w = max_x - min_x + 1
            box_h = max_y - min_y + 1
            if box_w * box_h < BOX_MIN_AREA:
                continue
            aspect = max(box_w, box_h) / max(1, min(box_w, box_h))
            if aspect < BOX_MIN_ASPECT:
                continue
            boxes.append((min_x, min_y, max_x, max_y, count, int(aspect * 100)))
    boxes.sort(key=lambda b: (b[4] * b[5]), reverse=True)
    boxes = boxes[:max_boxes]
    scaled_boxes: list[dict[str, int]] = []
    pad = max(1, BOX_PADDING // scale)
    for min_x, min_y, max_x, max_y, _, _ in boxes:
        xmin = max(0, (min_x - pad) * scale)
        ymin = max(0, (min_y - pad) * scale)
        xmax = min(base_w - 1, (max_x + pad) * scale)
        ymax = min(base_h - 1, (max_y + pad) * scale)
        scaled_boxes.append(
            {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}
        )
    return scaled_boxes


def _tune_coverage(
    mask: Image.Image,
    alpha: Image.Image,
    target_min: float,
    target_max: float,
    max_iter: int = 4,
) -> tuple[Image.Image, float, bool]:
    coverage = _coverage(mask, alpha)
    tuned = False
    if coverage > target_max:
        for _ in range(max_iter):
            mask = mask.filter(ImageFilter.MinFilter(3))
            coverage = _coverage(mask, alpha)
            tuned = True
            if coverage <= target_max:
                break
    elif coverage < target_min:
        for _ in range(max_iter):
            mask = mask.filter(ImageFilter.MaxFilter(3))
            coverage = _coverage(mask, alpha)
            tuned = True
            if coverage >= target_min:
                break
    return mask, coverage, tuned


def _select_mask(
    donor_rgb: Image.Image,
    alpha: Image.Image,
    overlay: Image.Image,
    threshold: int,
) -> tuple[Image.Image, dict[str, float | int | bool]]:
    diff_map = _diff_map(donor_rgb, overlay)
    diff_hist = ImageChops.multiply(diff_map, alpha).histogram()
    thresholds = _threshold_candidates(threshold, diff_hist)
    low_texture = _low_texture_mask(donor_rgb, alpha, LOW_TEXTURE_PERCENT)
    target_mid = (MASK_MIN_COVERAGE + MASK_MAX_COVERAGE) / 2
    best: tuple[Image.Image, float, int, float, bool] | None = None
    for thresh in thresholds:
        base_mask = _mask_from_diff(diff_map, thresh)
        base_mask = ImageChops.multiply(base_mask, alpha)
        for percent in EDGE_TOP_PERCENTS:
            edge_mask = _edge_mask(donor_rgb, alpha, percent)
            candidate = ImageChops.multiply(base_mask, edge_mask)
            candidate = ImageChops.multiply(candidate, low_texture)
            candidate, coverage, tuned = _tune_coverage(
                candidate, alpha, MASK_MIN_COVERAGE, MASK_MAX_COVERAGE
            )
            score = abs(coverage - target_mid)
            if coverage < MASK_MIN_COVERAGE or coverage > MASK_MAX_COVERAGE:
                score += 1.0
            if best is None or score < best[1]:
                best = (candidate, score, thresh, percent, tuned)
    if best is None:
        fallback = ImageChops.multiply(_mask_from_overlay(donor_rgb, overlay, threshold), alpha)
        return fallback, {"threshold": threshold, "edge_percent": 0.0, "tuned": False}
    mask, _, thresh, percent, tuned = best
    return mask, {"threshold": thresh, "edge_percent": percent, "tuned": tuned}


def _mask_score(
    mask: Image.Image,
    donor_rgb: Image.Image,
    alpha: Image.Image,
) -> tuple[float, dict[str, float]]:
    mask = mask.convert("L")
    coverage = _coverage(mask, alpha)
    if coverage <= 0:
        return 0.0, {"coverage": 0.0, "edge_overlap": 0.0, "thin_ratio": 0.0}
    edge_mask = _edge_mask(donor_rgb, alpha, MASK_SCORE_EDGE_PERCENT)
    overlap = ImageChops.multiply(mask, edge_mask)
    overlap_cov = _coverage(overlap, alpha)
    edge_overlap = overlap_cov / max(coverage, 1e-6)

    low_texture = _low_texture_mask(donor_rgb, alpha, LOW_TEXTURE_PERCENT)
    high_texture = ImageChops.subtract(Image.new("L", donor_rgb.size, 255), low_texture)
    high_overlap = _coverage(ImageChops.multiply(mask, high_texture), alpha)
    high_ratio = high_overlap / max(coverage, 1e-6)

    eroded = mask.filter(ImageFilter.MinFilter(3))
    border = ImageChops.subtract(mask, eroded)
    border_cov = _coverage(border, alpha)
    thin_ratio = min(1.0, border_cov / max(coverage, 1e-6))

    target_mid = (MASK_MIN_COVERAGE + MASK_MAX_COVERAGE) / 2
    if coverage < MASK_MIN_COVERAGE:
        coverage_score = coverage / max(MASK_MIN_COVERAGE, 1e-6)
    elif coverage > MASK_MAX_COVERAGE:
        coverage_score = max(0.0, 1 - (coverage - MASK_MAX_COVERAGE) / MASK_MAX_COVERAGE)
    else:
        coverage_score = 1 - abs(coverage - target_mid) / max(target_mid, 1e-6)

    score = edge_overlap * 0.45 + thin_ratio * 0.35 + coverage_score * 0.2 - high_ratio * 0.3
    if coverage < MASK_MIN_COVERAGE:
        score *= max(0.05, coverage_score)
    return score, {
        "coverage": coverage,
        "edge_overlap": edge_overlap,
        "thin_ratio": thin_ratio,
        "high_texture_ratio": high_ratio,
    }


def _seed_candidates(
    donor_rgb: Image.Image,
    alpha: Image.Image,
    threshold: int,
) -> tuple[
    Image.Image | None,
    float,
    dict[str, float | int | bool],
    str,
    list[dict[str, object]],
]:
    best_mask = None
    best_score = -1.0
    best_info: dict[str, float | int | bool] = {}
    best_label = "seed"
    candidate_masks: list[dict[str, object]] = []

    smooth_mask = _smooth_edge_mask(
        donor_rgb,
        alpha,
        SMOOTH_EDGE_KEEP,
        SMOOTH_EDGE_BAND,
    )
    smooth_score, smooth_metrics = _mask_score(smooth_mask, donor_rgb, alpha)
    candidate_masks.append(
        {
            "label": "smooth_edges",
            "mask": smooth_mask,
            "metrics": smooth_metrics,
            "score": smooth_score,
        }
    )
    if smooth_score > best_score:
        best_score = smooth_score
        best_mask = smooth_mask
        best_info = {
            "threshold": threshold,
            "edge_percent": EDGE_TOP_PERCENTS[0],
            "tuned": False,
            **smooth_metrics,
        }
        best_label = "smooth_edges"
    print(
        "[mask] candidate=smooth_edges score={score:.4f} coverage={coverage:.4f} "
        "edge_overlap={edge_overlap:.3f} thin={thin:.3f} high_tex={high_tex:.3f}".format(
            score=smooth_score,
            coverage=smooth_metrics["coverage"],
            edge_overlap=smooth_metrics["edge_overlap"],
            thin=smooth_metrics["thin_ratio"],
            high_tex=smooth_metrics["high_texture_ratio"],
        )
    )

    cushion_mask = _cushion_edge_mask(donor_rgb, alpha)
    cushion_score, cushion_metrics = _mask_score(cushion_mask, donor_rgb, alpha)
    candidate_masks.append(
        {
            "label": "cushion_edges",
            "mask": cushion_mask,
            "metrics": cushion_metrics,
            "score": cushion_score,
        }
    )
    if cushion_score > best_score:
        best_score = cushion_score
        best_mask = cushion_mask
        best_info = {
            "threshold": threshold,
            "edge_percent": CUSHION_SEAM_EDGE_PERCENT,
            "tuned": False,
            **cushion_metrics,
        }
        best_label = "cushion_edges"
    print(
        "[mask] candidate=cushion_edges score={score:.4f} coverage={coverage:.4f} "
        "edge_overlap={edge_overlap:.3f} thin={thin:.3f} high_tex={high_tex:.3f}".format(
            score=cushion_score,
            coverage=cushion_metrics["coverage"],
            edge_overlap=cushion_metrics["edge_overlap"],
            thin=cushion_metrics["thin_ratio"],
            high_tex=cushion_metrics["high_texture_ratio"],
        )
    )
    piping_mask = _piping_luma_edge_mask(donor_rgb, alpha)
    piping_score, piping_metrics = _mask_score(piping_mask, donor_rgb, alpha)
    candidate_masks.append(
        {
            "label": "piping_luma_edges",
            "mask": piping_mask,
            "metrics": piping_metrics,
            "score": piping_score,
        }
    )
    if piping_score > best_score:
        best_score = piping_score
        best_mask = piping_mask
        best_info = {
            "threshold": threshold,
            "edge_percent": PIPING_EDGE_PERCENT,
            "tuned": False,
            **piping_metrics,
        }
        best_label = "piping_luma_edges"
    print(
        "[mask] candidate=piping_luma_edges score={score:.4f} coverage={coverage:.4f} "
        "edge_overlap={edge_overlap:.3f} thin={thin:.3f} high_tex={high_tex:.3f}".format(
            score=piping_score,
            coverage=piping_metrics["coverage"],
            edge_overlap=piping_metrics["edge_overlap"],
            thin=piping_metrics["thin_ratio"],
            high_tex=piping_metrics["high_texture_ratio"],
        )
    )
    piping_v2 = _piping_edge_mask_v2(donor_rgb, alpha)
    piping_v2_score, piping_v2_metrics = _mask_score(piping_v2, donor_rgb, alpha)
    candidate_masks.append(
        {
            "label": "piping_edge_v2",
            "mask": piping_v2,
            "metrics": piping_v2_metrics,
            "score": piping_v2_score,
        }
    )
    if piping_v2_score > best_score:
        best_score = piping_v2_score
        best_mask = piping_v2
        best_info = {
            "threshold": threshold,
            "edge_percent": PIPING_EDGE_V2_PERCENT,
            "tuned": False,
            **piping_v2_metrics,
        }
        best_label = "piping_edge_v2"
    print(
        "[mask] candidate=piping_edge_v2 score={score:.4f} coverage={coverage:.4f} "
        "edge_overlap={edge_overlap:.3f} thin={thin:.3f} high_tex={high_tex:.3f}".format(
            score=piping_v2_score,
            coverage=piping_v2_metrics["coverage"],
            edge_overlap=piping_v2_metrics["edge_overlap"],
            thin=piping_v2_metrics["thin_ratio"],
            high_tex=piping_v2_metrics["high_texture_ratio"],
        )
    )
    silhouette_mask = _silhouette_edge_mask(donor_rgb, alpha)
    silhouette_score, silhouette_metrics = _mask_score(silhouette_mask, donor_rgb, alpha)
    candidate_masks.append(
        {
            "label": "silhouette_edges",
            "mask": silhouette_mask,
            "metrics": silhouette_metrics,
            "score": silhouette_score,
        }
    )
    if silhouette_score > best_score:
        best_score = silhouette_score
        best_mask = silhouette_mask
        best_info = {
            "threshold": threshold,
            "edge_percent": SILHOUETTE_EDGE_BAND,
            "tuned": False,
            **silhouette_metrics,
        }
        best_label = "silhouette_edges"
    print(
        "[mask] candidate=silhouette_edges score={score:.4f} coverage={coverage:.4f} "
        "edge_overlap={edge_overlap:.3f} thin={thin:.3f} high_tex={high_tex:.3f}".format(
            score=silhouette_score,
            coverage=silhouette_metrics["coverage"],
            edge_overlap=silhouette_metrics["edge_overlap"],
            thin=silhouette_metrics["thin_ratio"],
            high_tex=silhouette_metrics["high_texture_ratio"],
        )
    )
    if (
        smooth_metrics["coverage"] < MASK_MIN_COVERAGE
        and cushion_metrics["coverage"] >= MASK_MIN_COVERAGE
        and cushion_metrics["high_texture_ratio"] < 0.2
    ):
        best_score = cushion_score
        best_mask = cushion_mask
        best_info = {
            "threshold": threshold,
            "edge_percent": CUSHION_SEAM_EDGE_PERCENT,
            "tuned": False,
            **cushion_metrics,
        }
        best_label = "cushion_edges"
        print(
            "[mask] prefer=cushion_edges reason=low_smooth_coverage "
            "smooth={smooth:.4f} cushion={cushion:.4f}".format(
                smooth=smooth_metrics["coverage"],
                cushion=cushion_metrics["coverage"],
            )
        )
    if (
        smooth_metrics["coverage"] < MASK_MIN_COVERAGE
        and piping_metrics["coverage"] >= MASK_MIN_COVERAGE
        and piping_metrics["high_texture_ratio"] < 0.2
    ):
        best_score = piping_score
        best_mask = piping_mask
        best_info = {
            "threshold": threshold,
            "edge_percent": PIPING_EDGE_PERCENT,
            "tuned": False,
            **piping_metrics,
        }
        best_label = "piping_luma_edges"
        print(
            "[mask] prefer=piping_luma_edges reason=low_smooth_coverage "
            "smooth={smooth:.4f} piping={piping:.4f}".format(
                smooth=smooth_metrics["coverage"],
                piping=piping_metrics["coverage"],
            )
        )
    if (
        silhouette_metrics["coverage"] >= MASK_MIN_COVERAGE
        and silhouette_metrics["high_texture_ratio"] < 0.2
        and cushion_metrics["coverage"] > 0.02
    ):
        best_score = silhouette_score
        best_mask = silhouette_mask
        best_info = {
            "threshold": threshold,
            "edge_percent": SILHOUETTE_EDGE_BAND,
            "tuned": False,
            **silhouette_metrics,
        }
        best_label = "silhouette_edges"
        print(
            "[mask] prefer=silhouette_edges reason=avoid_interior_seams "
            "silhouette={silhouette:.4f} cushion={cushion:.4f}".format(
                silhouette=silhouette_metrics["coverage"],
                cushion=cushion_metrics["coverage"],
            )
        )

    return best_mask, best_score, best_info, best_label, candidate_masks


def _ensure_min_coverage(
    best_mask: Image.Image,
    best_label: str,
    best_info: dict[str, float | int | bool],
    candidate_masks: list[dict[str, object]],
    donor_rgb: Image.Image,
    alpha: Image.Image,
) -> tuple[Image.Image, str, dict[str, float | int | bool], float]:
    best_cov = _coverage(best_mask, alpha)
    if best_cov < MASK_FORCE_MIN_COVERAGE:
        low_texture = _low_texture_mask(donor_rgb, alpha, LOW_TEXTURE_EXPAND_PERCENT)
        expanded = best_mask
        expand_px = 0
        for kernel in (3, 5, 7, 9, 11, 13, 17):
            expanded = expanded.filter(ImageFilter.MaxFilter(kernel))
            expanded = ImageChops.multiply(expanded, low_texture)
            expanded = ImageChops.multiply(expanded, alpha)
            expanded_cov = _coverage(expanded, alpha)
            expand_px = kernel // 2
            if expanded_cov >= MASK_FORCE_MIN_COVERAGE:
                best_mask = expanded
                best_info["expanded_px"] = expand_px
                best_cov = expanded_cov
                break
    if best_cov < MASK_FORCE_MIN_COVERAGE and candidate_masks:
        best_by_cov = max(
            candidate_masks,
            key=lambda item: item["metrics"]["coverage"],  # type: ignore[index]
        )
        alt_mask = best_by_cov["mask"]  # type: ignore[index]
        alt_label = best_by_cov["label"]  # type: ignore[index]
        low_texture = _low_texture_mask(donor_rgb, alpha, LOW_TEXTURE_EXPAND_PERCENT)
        for kernel in (5, 7, 9, 11, 13, 17):
            alt_mask = alt_mask.filter(ImageFilter.MaxFilter(kernel))
            alt_mask = ImageChops.multiply(alt_mask, low_texture)
            alt_mask = ImageChops.multiply(alt_mask, alpha)
            alt_cov = _coverage(alt_mask, alpha)
            if alt_cov >= MASK_FORCE_MIN_COVERAGE:
                best_mask = alt_mask
                best_label = f"{alt_label}+expand"
                best_cov = alt_cov
                break
    return best_mask, best_label, best_info, best_cov


def _pick_piping_focus_candidate(
    candidate_masks: list[dict[str, object]],
) -> dict[str, object] | None:
    priority = ("piping_edge_v2", "piping_luma_edges", "cushion_edges", "smooth_edges")
    def _score(item: dict[str, object]) -> float:
        metrics = item["metrics"]  # type: ignore[assignment]
        coverage = float(metrics["coverage"])  # type: ignore[index]
        edge_overlap = float(metrics["edge_overlap"])  # type: ignore[index]
        thin_ratio = float(metrics["thin_ratio"])  # type: ignore[index]
        high_tex = float(metrics["high_texture_ratio"])  # type: ignore[index]
        score = edge_overlap * 0.5 + thin_ratio * 0.4 - high_tex * 0.3
        if coverage < MASK_MIN_COVERAGE:
            score *= 0.3
        if coverage > MASK_MAX_COVERAGE:
            score *= 0.5
        return score

    by_label: dict[str, list[dict[str, object]]] = {}
    for item in candidate_masks:
        label = str(item.get("label", ""))
        by_label.setdefault(label, []).append(item)

    for label in priority:
        items = by_label.get(label, [])
        if not items:
            continue
        best_item = max(items, key=_score)
        metrics = best_item["metrics"]  # type: ignore[assignment]
        coverage = float(metrics["coverage"])  # type: ignore[index]
        if coverage >= MASK_MIN_COVERAGE * 0.8:
            return best_item
    return None


def _resize_for_sam2(
    donor_rgb: Image.Image,
    alpha: Image.Image,
) -> tuple[Image.Image, Image.Image, float]:
    width, height = donor_rgb.size
    max_side = max(width, height)
    if max_side <= SAM2_MAX_LONG_EDGE:
        return donor_rgb, alpha, 1.0
    scale = SAM2_MAX_LONG_EDGE / max_side
    target_size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    resized_rgb = donor_rgb.resize(target_size, Image.LANCZOS)
    resized_alpha = alpha.resize(target_size, Image.NEAREST)
    return resized_rgb, resized_alpha, scale


def _predict_with_retries(
    client: Client,
    model: str,
    image_path: Path,
    boxes: list[dict[str, int]],
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, SAM2_RETRIES + 1):
        try:
            return client.predict(
                model,
                {"image": handle_file(str(image_path)), "boxes": boxes},
                api_name="/predict",
            )
        except Exception as exc:  # pragma: no cover - network dependent
            last_exc = exc
            if attempt < SAM2_RETRIES:
                time.sleep(SAM2_RETRY_SLEEP * attempt)
    if last_exc:
        raise last_exc
    raise RuntimeError("Failed to call SAM2 space.")


def _space_candidates(space: str | list[str]) -> list[str]:
    if isinstance(space, list):
        return [s.strip() for s in space if s.strip()]
    if "," in space:
        return [s.strip() for s in space.split(",") if s.strip()]
    return [space.strip()]


def generate_mask_from_space(
    *,
    image_path: Path,
    output_path: Path,
    space: str,
    model: str,
    threshold: int,
    use_edge_boxes: bool = True,
) -> Path:
    if Client is None or handle_file is None:
        raise RuntimeError("gradio_client is required to generate SAM2 masks.")
    ensure_dir(output_path.parent)
    if os.environ.get("SAM2_SKIP") == "1":
        with Image.open(image_path) as donor_img:
            donor_img = ImageOps.exif_transpose(donor_img)
            donor_rgb = donor_img.convert("RGB")
            mask_rgb = _boost_contrast(donor_rgb)
            if "A" in donor_img.getbands():
                alpha = _alpha_binary(donor_img.getchannel("A"))
            else:
                alpha = Image.new("L", donor_img.size, 255)
            best_mask, best_score, best_info, best_label, candidate_masks = _seed_candidates(
                mask_rgb,
                alpha,
                threshold,
            )
            piping_pick = _pick_piping_focus_candidate(candidate_masks)
            if piping_pick is not None:
                best_mask = piping_pick["mask"]  # type: ignore[index]
                best_label = str(piping_pick.get("label", best_label))
                best_info = {
                    "threshold": threshold,
                    "edge_percent": 0.0,
                    "tuned": False,
                    **piping_pick.get("metrics", {}),  # type: ignore[arg-type]
                }
                print(f"[mask] prefer={best_label} reason=piping_focus")
            if best_mask is None:
                raise RuntimeError("Failed to generate any deterministic mask candidates.")
            best_mask, best_label, best_info, best_cov = _ensure_min_coverage(
                best_mask,
                best_label,
                best_info,
                candidate_masks,
                mask_rgb,
                alpha,
            )
            alpha = _pad_alpha_to_size(alpha, best_mask.size)
            best_mask = ImageChops.multiply(best_mask.convert("L"), alpha)
            best_mask.save(output_path, format="PNG")
            print(
                "[mask] selected={label} score={score:.4f} coverage={coverage:.4f} "
                "thresh={thresh} edge_p={edge_p:.3f} tuned={tuned} saved={name}".format(
                    label=best_label,
                    score=best_score,
                    coverage=best_cov,
                    thresh=best_info.get("threshold", threshold),
                    edge_p=best_info.get("edge_percent", 0.0),
                    tuned=best_info.get("tuned", False),
                    name=output_path.name,
                )
            )
            return output_path
    spaces = _space_candidates(space)
    last_exc: Exception | None = None
    for space_id in spaces:
        try:
            client = Client(
                space_id,
                httpx_kwargs={"timeout": SAM2_HTTP_TIMEOUT},
                ssl_verify=False,
            )
        except Exception as exc:  # pragma: no cover - network dependent
            last_exc = exc
            print(f"[mask] space={space_id} init failed: {exc}")
            continue
        try:
            with Image.open(image_path) as donor_img:
                donor_img = ImageOps.exif_transpose(donor_img)
                donor_rgb = donor_img.convert("RGB")
                mask_rgb = _boost_contrast(donor_rgb)
                if "A" in donor_img.getbands():
                    alpha = _alpha_binary(donor_img.getchannel("A"))
                else:
                    alpha = Image.new("L", donor_img.size, 255)
                input_rgb, input_alpha, _ = _resize_for_sam2(mask_rgb, alpha)
                temp_path: Path | None = None
                if input_rgb.size != donor_rgb.size:
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                    temp_path = Path(temp_file.name)
                    temp_file.close()
                    input_rgb.save(temp_path, format="PNG")
                    sam2_image_path = temp_path
                else:
                    sam2_image_path = image_path

                candidates: list[tuple[str, list[dict[str, int]]]] = []
                if use_edge_boxes:
                    candidates.append(("edge_boxes", _edge_boxes(input_rgb, input_alpha, BOX_MAX)))
                candidates.append(("auto", []))

                best_mask, best_score, best_info, best_label, candidate_masks = _seed_candidates(
                    mask_rgb,
                    alpha,
                    threshold,
                )

                try:
                    for idx, (label, boxes) in enumerate(candidates):
                        try:
                            result = _predict_with_retries(
                                client,
                                model,
                                sam2_image_path,
                                boxes,
                            )
                        except Exception as exc:  # pragma: no cover - network dependent
                            print(f"[mask] candidate={label} failed: {exc}")
                            last_exc = exc
                            continue
                        overlay = _load_gradio_image(result)
                        mask, info = _select_mask(mask_rgb, alpha, overlay, threshold)
                        score, metrics = _mask_score(mask, mask_rgb, alpha)
                        candidate_masks.append(
                            {
                                "label": label,
                                "mask": mask,
                                "metrics": metrics,
                                "score": score,
                            }
                        )
                        if score > best_score:
                            best_score = score
                            best_mask = mask
                            best_info = {**info, **metrics}
                            best_label = label
                        print(
                            "[mask] candidate={label} score={score:.4f} coverage={coverage:.4f} "
                            "edge_overlap={edge_overlap:.3f} thin={thin:.3f} high_tex={high_tex:.3f} "
                            "boxes={boxes}".format(
                                label=label,
                                score=score,
                                coverage=metrics["coverage"],
                                edge_overlap=metrics["edge_overlap"],
                                thin=metrics["thin_ratio"],
                                high_tex=metrics["high_texture_ratio"],
                                boxes=len(boxes),
                            )
                        )
                        if idx == 0 and best_score >= MASK_SCORE_ACCEPT:
                            break
                finally:
                    if temp_path is not None:
                        try:
                            temp_path.unlink()
                        except OSError:
                            pass

                if best_mask is None:
                    raise RuntimeError("Failed to generate any SAM2 mask candidates.")

                best_mask, best_label, best_info, best_cov = _ensure_min_coverage(
                    best_mask,
                    best_label,
                    best_info,
                    candidate_masks,
                    mask_rgb,
                    alpha,
                )
                alpha = _pad_alpha_to_size(alpha, best_mask.size)
                best_mask = ImageChops.multiply(best_mask.convert("L"), alpha)
                best_mask.save(output_path, format="PNG")
                print(
                    "[mask] selected={label} score={score:.4f} coverage={coverage:.4f} "
                    "thresh={thresh} edge_p={edge_p:.3f} tuned={tuned} saved={name}".format(
                        label=best_label,
                        score=best_score,
                        coverage=best_cov,
                        thresh=best_info.get("threshold", threshold),
                        edge_p=best_info.get("edge_percent", 0.0),
                        tuned=best_info.get("tuned", False),
                        name=output_path.name,
                    )
                )
                return output_path
        except Exception as exc:  # pragma: no cover - network dependent
            last_exc = exc
            print(f"[mask] space={space_id} failed: {exc}")
            continue
    if last_exc:
        raise last_exc
    raise RuntimeError("Failed to generate SAM2 mask from any space.")
    return output_path
