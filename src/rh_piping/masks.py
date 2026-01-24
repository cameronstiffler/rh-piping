"""Mask discovery and SAM2 mask generation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import io
import urllib.request

from PIL import Image, ImageChops, ImageFilter, ImageOps

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
BOX_DOWNSCALE = 4
BOX_MAX = 24
BOX_MIN_AREA = 20
BOX_MIN_ASPECT = 3.0
BOX_PADDING = 6


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
    target_mid = (MASK_MIN_COVERAGE + MASK_MAX_COVERAGE) / 2
    best: tuple[Image.Image, float, int, float, bool] | None = None
    for thresh in thresholds:
        base_mask = _mask_from_diff(diff_map, thresh)
        base_mask = ImageChops.multiply(base_mask, alpha)
        for percent in EDGE_TOP_PERCENTS:
            edge_mask = _edge_mask(donor_rgb, alpha, percent)
            candidate = ImageChops.multiply(base_mask, edge_mask)
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
    client = Client(space)
    with Image.open(image_path) as donor_img:
        donor_img = ImageOps.exif_transpose(donor_img)
        donor_rgb = donor_img.convert("RGB")
        if "A" in donor_img.getbands():
            alpha = _alpha_binary(donor_img.getchannel("A"))
        else:
            alpha = Image.new("L", donor_img.size, 255)
        boxes: list[dict[str, int]] = []
        if use_edge_boxes:
            boxes = _edge_boxes(donor_rgb, alpha, BOX_MAX)
        result = client.predict(
            model,
            {"image": handle_file(str(image_path)), "boxes": boxes},
            api_name="/predict",
        )
        overlay = _load_gradio_image(result)
        mask, info = _select_mask(donor_rgb, alpha, overlay, threshold)
        coverage = _coverage(mask, alpha)
        mask.save(output_path, format="PNG")
        print(
            "[mask] coverage={coverage:.4f} boxes={boxes} "
            "thresh={thresh} edge_p={edge_p:.3f} tuned={tuned} saved={name}".format(
                coverage=coverage,
                boxes=len(boxes),
                thresh=info["threshold"],
                edge_p=info["edge_percent"],
                tuned=info["tuned"],
                name=output_path.name,
            )
        )
    return output_path
