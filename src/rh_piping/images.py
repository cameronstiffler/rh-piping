"""Image preparation helpers."""

from __future__ import annotations

from dataclasses import dataclass
import io
from math import gcd
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps

from rh_piping.io import ensure_dir

TARGET_LONG_EDGE = 4096
EDGE_GUARD_PX = 0
EDGE_DETECT_BLUR = 1
EDGE_BAND_PX = 6
EDGE_TOP_PERCENT = 0.35
DIFF_TOP_PERCENT = 0.12
MIN_EDGE_THRESHOLD = 1
MIN_DIFF_THRESHOLD = 1
MAX_MASK_COVERAGE = 0.5
MIN_MASK_COVERAGE = 0.005
MASK_FOLLOWS_DONOR = True


@dataclass
class ImageMeta:
    width: int
    height: int
    mode: str


def aspect_ratio_for_size(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return "1:1"
    divisor = gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def convert_to_4k_png(source: Path, destination: Path) -> ImageMeta:
    ensure_dir(destination.parent)
    with Image.open(source) as img:
        img = ImageOps.exif_transpose(img)
        width, height = img.size
        max_side = max(width, height)
        if max_side == 0:
            raise ValueError(f"Invalid image size for {source}")
        scale = TARGET_LONG_EDGE / max_side
        target_width = max(1, int(round(width * scale)))
        target_height = max(1, int(round(height * scale)))
        if (target_width, target_height) != img.size:
            img = img.resize((target_width, target_height), Image.LANCZOS)
        if img.mode not in {"RGB", "RGBA"}:
            img = img.convert("RGB")
        icc_profile = img.info.get("icc_profile")
        img.save(destination, format="PNG", icc_profile=icc_profile)
        return ImageMeta(width=target_width, height=target_height, mode=img.mode)


def build_square_model_input(
    source: Path,
    background: tuple[int, int, int] = (128, 128, 128),
) -> tuple[bytes, tuple[int, int], tuple[int, int]]:
    with Image.open(source) as source_img:
        source_img = ImageOps.exif_transpose(source_img)
        if source_img.mode != "RGBA":
            source_img = source_img.convert("RGBA")
        target_size = source_img.size
        square_size = max(target_size)
        flattened = Image.new("RGB", target_size, background)
        flattened.paste(source_img, mask=source_img.getchannel("A"))
        square = Image.new("RGB", (square_size, square_size), background)
        offset = (
            (square_size - target_size[0]) // 2,
            (square_size - target_size[1]) // 2,
        )
        square.paste(flattened, offset)
        buffer = io.BytesIO()
        square.save(buffer, format="PNG")
    return buffer.getvalue(), target_size, square.size


def _preserve_donor_luminance(donor_rgb: Image.Image, output_rgb: Image.Image) -> Image.Image:
    donor_y, _, _ = donor_rgb.convert("YCbCr").split()
    _, out_cb, out_cr = output_rgb.convert("YCbCr").split()
    return Image.merge("YCbCr", (donor_y, out_cb, out_cr)).convert("RGB")


def load_mask_image(mask_path: Path, target_size: tuple[int, int]) -> Image.Image:
    with Image.open(mask_path) as mask_img:
        mask_img = ImageOps.exif_transpose(mask_img)
        if mask_img.size != target_size:
            mask_img = ImageOps.fit(mask_img, target_size, Image.LANCZOS, centering=(0.5, 0.5))
        if mask_img.mode == "RGBA":
            return mask_img.getchannel("A")
        return mask_img.convert("L")


def composite_output_with_donor(
    output_bytes: bytes,
    alpha_source: Path,
    apply_mask: bool = True,
    preserve_luminance: bool = False,
    mask_image: Image.Image | None = None,
) -> tuple[bytes, dict]:
    with Image.open(alpha_source) as source_img:
        source_img = ImageOps.exif_transpose(source_img)
        if source_img.mode != "RGBA":
            source_img = source_img.convert("RGBA")
        alpha_channel = source_img.getchannel("A")
        target_size = source_img.size
        has_alpha = alpha_channel.getextrema() != (255, 255)
        donor_rgb = source_img.convert("RGB")

    with Image.open(io.BytesIO(output_bytes)) as output_img:
        output_img = ImageOps.exif_transpose(output_img)
        if output_img.mode != "RGB":
            output_img = output_img.convert("RGB")

        resized = False
        original_size = output_img.size
        if output_img.size != target_size:
            output_img = ImageOps.fit(output_img, target_size, Image.LANCZOS, centering=(0.5, 0.5))
            resized = True
        if preserve_luminance:
            output_img = _preserve_donor_luminance(donor_rgb, output_img)
        if not apply_mask:
            composited = output_img.convert("RGBA")
            composited.putalpha(alpha_channel)
            buffer = io.BytesIO()
            composited.save(buffer, format="PNG", icc_profile=output_img.info.get("icc_profile"))
            stats = {
                "has_alpha": has_alpha,
                "resized": resized,
                "target_size": target_size,
                "original_size": original_size,
                "preserve_luminance": preserve_luminance,
                "mask_disabled": True,
                "mask_coverage": 1.0,
                "mask_relaxed": False,
                "mask_tightened": False,
                "mask_clipped": False,
                "edge_guard_px": 0,
            }
            return buffer.getvalue(), stats

        total_pixels = target_size[0] * target_size[1]

        def coverage_for(mask: Image.Image) -> float:
            hist = mask.histogram()
            covered = total_pixels - hist[0]
            return covered / total_pixels if total_pixels else 0.0

        def build_edge_mask(edge_thresh: int, band_px: int) -> Image.Image:
            blurred = donor_rgb.filter(ImageFilter.GaussianBlur(EDGE_DETECT_BLUR))
            edges = blurred.filter(ImageFilter.FIND_EDGES).convert("L")
            edge_mask = edges.point(lambda p: 255 if p > edge_thresh else 0)
            if band_px > 0:
                edge_mask = edge_mask.filter(ImageFilter.MaxFilter(band_px * 2 + 1))
            return edge_mask

        def build_mask(diff_thresh: int, edge_thresh: int, band_px: int) -> Image.Image:
            diff = ImageChops.difference(donor_rgb, output_img).convert("L")
            diff_mask = diff.point(lambda p: 255 if p > diff_thresh else 0)
            edge_mask = build_edge_mask(edge_thresh, band_px)
            combined = ImageChops.multiply(diff_mask, edge_mask)
            return combined.filter(ImageFilter.GaussianBlur(1))

        mask_relaxed = False
        mask_tightened = False
        mask_clipped = False
        mask_already_multiplied = False

        alpha_binary = alpha_channel.point(lambda p: 255 if p > 0 else 0)
        edge_full = donor_rgb.filter(ImageFilter.GaussianBlur(EDGE_DETECT_BLUR)).filter(ImageFilter.FIND_EDGES).convert("L")
        edge_hist_image = ImageChops.multiply(edge_full, alpha_binary)
        edge_hist = edge_hist_image.histogram()

        def percentile_threshold(hist: list[int], top_percent: float) -> int:
            target = max(1, int(round(total_pixels * top_percent)))
            cumulative = 0
            for value in range(255, -1, -1):
                cumulative += hist[value]
                if cumulative >= target:
                    return value
            return 0

        edge_thresh = max(MIN_EDGE_THRESHOLD, percentile_threshold(edge_hist, EDGE_TOP_PERCENT))
        if mask_image is not None:
            mask = mask_image
            if mask.mode != "L":
                mask = mask.convert("L")
            if mask.size != target_size:
                mask = ImageOps.fit(mask, target_size, Image.LANCZOS, centering=(0.5, 0.5))
            mask = ImageChops.multiply(mask, alpha_binary)
            coverage = coverage_for(mask)
            mask_already_multiplied = True
        elif MASK_FOLLOWS_DONOR:
            mask = build_edge_mask(edge_thresh, EDGE_BAND_PX)
            coverage = coverage_for(mask)
            if coverage < MIN_MASK_COVERAGE:
                relaxed_mask = build_edge_mask(max(1, edge_thresh - 10), EDGE_BAND_PX + 2)
                relaxed_coverage = coverage_for(relaxed_mask)
                if relaxed_coverage > coverage:
                    mask = relaxed_mask
                    coverage = relaxed_coverage
                    mask_relaxed = True
            if coverage > MAX_MASK_COVERAGE:
                best_mask = mask
                best_coverage = coverage
                edge_iter = edge_thresh
                band_px = EDGE_BAND_PX
                for _ in range(3):
                    edge_iter += 10
                    band_px = max(1, band_px - 2)
                    candidate = build_edge_mask(edge_iter, band_px)
                    candidate_coverage = coverage_for(candidate)
                    if candidate_coverage < best_coverage:
                        best_mask = candidate
                        best_coverage = candidate_coverage
                        mask_tightened = True
                mask = best_mask
                coverage = best_coverage
                if coverage > MAX_MASK_COVERAGE:
                    mask = Image.new("L", target_size, 0)
                    coverage = 0.0
                    mask_clipped = True
        else:
            diff_full = ImageChops.difference(donor_rgb, output_img).convert("L")
            diff_hist_image = ImageChops.multiply(diff_full, alpha_binary)
            diff_hist = diff_hist_image.histogram()
            diff_thresh = max(MIN_DIFF_THRESHOLD, percentile_threshold(diff_hist, DIFF_TOP_PERCENT))

            mask = build_mask(diff_thresh, edge_thresh, EDGE_BAND_PX)
            coverage = coverage_for(mask)
            if coverage < MIN_MASK_COVERAGE:
                relaxed_mask = build_mask(
                    max(1, diff_thresh - 8),
                    max(1, edge_thresh - 10),
                    EDGE_BAND_PX + 1,
                )
                relaxed_coverage = coverage_for(relaxed_mask)
                if relaxed_coverage > coverage:
                    mask = relaxed_mask
                    coverage = relaxed_coverage
                    mask_relaxed = True
            if coverage > MAX_MASK_COVERAGE:
                best_mask = mask
                best_coverage = coverage
                diff_iter = diff_thresh
                edge_iter = edge_thresh
                band_px = EDGE_BAND_PX
                for _ in range(3):
                    diff_iter += 6
                    edge_iter += 8
                    band_px = max(1, band_px - 1)
                    candidate = build_mask(diff_iter, edge_iter, band_px)
                    candidate_coverage = coverage_for(candidate)
                    if candidate_coverage < best_coverage:
                        best_mask = candidate
                        best_coverage = candidate_coverage
                        mask_tightened = True
                mask = best_mask
                coverage = best_coverage
                if coverage > MAX_MASK_COVERAGE:
                    mask = Image.new("L", target_size, 0)
                    coverage = 0.0
                    mask_clipped = True

        if not mask_already_multiplied:
            mask = ImageChops.multiply(mask, alpha_binary)
        guard_px = EDGE_GUARD_PX
        guard_used = guard_px
        if guard_px > 0:
            guard_size = guard_px * 2 + 1
            interior = alpha_binary.filter(ImageFilter.MinFilter(guard_size))
            guarded = ImageChops.multiply(mask, interior)
            guarded_coverage = coverage_for(guarded)
            if guarded_coverage < 0.001 and guard_px > 2:
                guard_px = 2
                guard_size = guard_px * 2 + 1
                interior = alpha_binary.filter(ImageFilter.MinFilter(guard_size))
                guarded = ImageChops.multiply(mask, interior)
                guard_used = guard_px
                guarded_coverage = coverage_for(guarded)
            if guarded_coverage < 0.0005:
                guard_used = 0
                guarded = mask
            mask = guarded
            coverage = coverage_for(mask)

        composited = Image.composite(output_img, donor_rgb, mask)
        composited = composited.convert("RGBA")
        composited.putalpha(alpha_channel)
        buffer = io.BytesIO()
        composited.save(buffer, format="PNG", icc_profile=output_img.info.get("icc_profile"))
        stats = {
            "has_alpha": has_alpha,
            "resized": resized,
            "target_size": target_size,
            "original_size": original_size,
            "preserve_luminance": preserve_luminance,
            "mask_coverage": coverage,
            "mask_relaxed": mask_relaxed,
            "mask_tightened": mask_tightened,
            "mask_clipped": mask_clipped,
            "edge_guard_px": guard_used,
        }
        return buffer.getvalue(), stats
