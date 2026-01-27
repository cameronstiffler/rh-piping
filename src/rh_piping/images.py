"""Image preparation helpers."""

from __future__ import annotations

from dataclasses import dataclass
import io
from math import gcd
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

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
SHADOW_SEARCH_START = 0.4
SHADOW_SEARCH_END = 0.95
SHADOW_MIN_BAND_PX = 4
SHADOW_MAX_BAND_RATIO = 0.3
SHADOW_COVERAGE_MIN = 0.7
SHADOW_PERCENTILE = 0.1


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


def _parse_aspect_ratio(token: str) -> float | None:
    parts = token.split(":")
    if len(parts) != 2:
        return None
    try:
        num = float(parts[0])
        den = float(parts[1])
    except ValueError:
        return None
    if den <= 0 or num <= 0:
        return None
    return num / den


def build_model_input(
    source: Path,
    pad_aspect_ratio: str | None = None,
) -> tuple[bytes, tuple[int, int]]:
    source_bytes = source.read_bytes()
    with Image.open(io.BytesIO(source_bytes)) as source_img:
        source_img = ImageOps.exif_transpose(source_img)
        if pad_aspect_ratio:
            ratio = _parse_aspect_ratio(pad_aspect_ratio)
        else:
            ratio = None
        if ratio:
            width, height = source_img.size
            target_height = int((width / ratio) + 0.5)
            if target_height > height:
                if source_img.mode != "RGBA":
                    source_img = source_img.convert("RGBA")
                padded = Image.new("RGBA", (width, target_height), (0, 0, 0, 0))
                offset_y = (target_height - height) // 2
                padded.paste(source_img, (0, offset_y))
                buffer = io.BytesIO()
                padded.save(buffer, format="PNG")
                return buffer.getvalue(), padded.size
        target_size = source_img.size
    return source_bytes, target_size


def build_square_mask_input(
    mask_image: Image.Image,
    target_size: tuple[int, int],
    square_size: tuple[int, int],
    background: tuple[int, int, int] = (0, 0, 0),
) -> bytes:
    mask = mask_image.convert("L")
    if mask.size != target_size:
        mask = ImageOps.fit(mask, target_size, Image.LANCZOS, centering=(0.5, 0.5))
    mask_rgb = Image.new("RGB", target_size, background)
    white = Image.new("RGB", target_size, (255, 255, 255))
    mask_rgb.paste(white, mask=mask)
    square = Image.new("RGB", square_size, background)
    offset = (
        (square_size[0] - target_size[0]) // 2,
        (square_size[1] - target_size[1]) // 2,
    )
    square.paste(mask_rgb, offset)
    buffer = io.BytesIO()
    square.save(buffer, format="PNG")
    return buffer.getvalue()


def _preserve_donor_luminance(donor_rgb: Image.Image, output_rgb: Image.Image) -> Image.Image:
    donor_y, _, _ = donor_rgb.convert("YCbCr").split()
    _, out_cb, out_cr = output_rgb.convert("YCbCr").split()
    return Image.merge("YCbCr", (donor_y, out_cb, out_cr)).convert("RGB")


def _find_shadow_band(
    donor_rgb: Image.Image, alpha_channel: Image.Image
) -> tuple[int, int] | None:
    width, height = donor_rgb.size
    if width == 0 or height == 0:
        return None
    luma = donor_rgb.convert("L")
    luma_data = memoryview(luma.tobytes())
    alpha_data = memoryview(alpha_channel.tobytes())
    row_means: list[float] = []
    row_coverage: list[float] = []
    for y in range(height):
        row_start = y * width
        row_end = row_start + width
        luma_row = luma_data[row_start:row_end]
        alpha_row = alpha_data[row_start:row_end]
        total = 0
        count = 0
        for i in range(width):
            if alpha_row[i]:
                total += luma_row[i]
                count += 1
        if count:
            row_means.append(total / count)
            row_coverage.append(count / width)
        else:
            row_means.append(255.0)
            row_coverage.append(0.0)

    start = int(height * SHADOW_SEARCH_START)
    end = max(start + 1, int(height * SHADOW_SEARCH_END))
    candidates = [
        row_means[y]
        for y in range(start, min(end, height))
        if row_coverage[y] >= SHADOW_COVERAGE_MIN
    ]
    if not candidates:
        return None
    candidates.sort()
    percentile_index = min(
        len(candidates) - 1, max(0, int(len(candidates) * SHADOW_PERCENTILE))
    )
    threshold = candidates[percentile_index]

    best_start = None
    best_end = None
    current_start = None
    for y in range(start, min(end, height)):
        qualifies = (
            row_coverage[y] >= SHADOW_COVERAGE_MIN and row_means[y] <= threshold
        )
        if qualifies and current_start is None:
            current_start = y
        if not qualifies and current_start is not None:
            current_end = y
            if best_start is None or (current_end - current_start) >= (
                best_end - best_start  # type: ignore[operator]
            ):
                best_start, best_end = current_start, current_end
            current_start = None
    if current_start is not None:
        current_end = min(end, height)
        if best_start is None or (current_end - current_start) >= (
            best_end - best_start  # type: ignore[operator]
        ):
            best_start, best_end = current_start, current_end

    if best_start is None or best_end is None:
        return None
    band_height = best_end - best_start
    if band_height < SHADOW_MIN_BAND_PX:
        return None
    if band_height > int(height * SHADOW_MAX_BAND_RATIO):
        return None
    return best_start, best_end


def _apply_shadow_band(
    donor_rgb: Image.Image,
    output_rgb: Image.Image,
    band: tuple[int, int],
) -> Image.Image:
    start_y, end_y = band
    if end_y <= start_y:
        return output_rgb
    box = (0, start_y, donor_rgb.size[0], end_y)
    patch = donor_rgb.crop(box)
    output_rgb.paste(patch, box)
    return output_rgb


def _average_corner_color(image: Image.Image) -> tuple[int, int, int]:
    rgb = image.convert("RGB")
    w, h = rgb.size
    samples = [
        rgb.getpixel((0, 0)),
        rgb.getpixel((w - 1, 0)),
        rgb.getpixel((0, h - 1)),
        rgb.getpixel((w - 1, h - 1)),
    ]
    return (
        sum(c[0] for c in samples) // 4,
        sum(c[1] for c in samples) // 4,
        sum(c[2] for c in samples) // 4,
    )


def _content_bbox(image: Image.Image, threshold: int = 12) -> tuple[int, int, int, int] | None:
    bg = _average_corner_color(image)
    bg_img = Image.new("RGB", image.size, bg)
    diff = ImageChops.difference(image.convert("RGB"), bg_img).convert("L")
    mask = diff.point(lambda p: 255 if p > threshold else 0)
    return mask.getbbox()


def _content_bbox_downscale(
    image: Image.Image,
    threshold: int = 20,
    max_width: int = 512,
) -> tuple[int, int, int, int] | None:
    width, height = image.size
    if width <= 0 or height <= 0:
        return None
    scale = min(1.0, max_width / width)
    if scale < 1.0:
        small = image.resize(
            (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
            Image.BILINEAR,
        )
    else:
        small = image
    bbox = _content_bbox(small, threshold=threshold)
    if bbox is None:
        return None
    left, top, right, bottom = bbox
    if scale < 1.0:
        inv = 1.0 / scale
        left = int(round(left * inv))
        top = int(round(top * inv))
        right = int(round(right * inv))
        bottom = int(round(bottom * inv))
    return (left, top, right, bottom)


def _alpha_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    if "A" not in image.getbands():
        return None
    alpha = image.getchannel("A")
    mask = alpha.point(lambda p: 255 if p > 0 else 0)
    return mask.getbbox()


def content_bbox_from_bytes(output_bytes: bytes) -> tuple[tuple[int, int, int, int] | None, tuple[int, int]]:
    with Image.open(io.BytesIO(output_bytes)) as output_img:
        output_img = ImageOps.exif_transpose(output_img)
        bbox = _alpha_bbox(output_img)
        if bbox is None:
            bbox = _content_bbox(output_img)
        return bbox, output_img.size


def content_bbox_from_path(image_path: Path) -> tuple[tuple[int, int, int, int] | None, tuple[int, int]]:
    with Image.open(image_path) as output_img:
        output_img = ImageOps.exif_transpose(output_img)
        bbox = _alpha_bbox(output_img)
        if bbox is None:
            bbox = _content_bbox_downscale(output_img)
        return bbox, output_img.size


def scale_output_to_donor_width(
    output_bytes: bytes,
    donor_path: Path,
    tolerance: float = 0.02,
) -> tuple[bytes, dict]:
    with Image.open(donor_path) as donor_img:
        donor_img = ImageOps.exif_transpose(donor_img)
        donor_bbox = _alpha_bbox(donor_img)
        if donor_bbox is None:
            donor_bbox = _content_bbox_downscale(donor_img)
        if donor_bbox is None:
            return output_bytes, {"scaled": False, "reason": "donor_bbox_missing"}
        d_left, d_top, d_right, d_bottom = donor_bbox
        donor_width = max(1, d_right - d_left)

    with Image.open(io.BytesIO(output_bytes)) as output_img:
        output_img = ImageOps.exif_transpose(output_img)
        if output_img.mode != "RGB":
            output_img = output_img.convert("RGB")
        out_bbox = _content_bbox_downscale(output_img)
        if out_bbox is None:
            return output_bytes, {"scaled": False, "reason": "output_bbox_missing"}
        o_left, o_top, o_right, o_bottom = out_bbox
        output_width = max(1, o_right - o_left)
        scale = donor_width / output_width
        if abs(1.0 - scale) <= tolerance:
            return output_bytes, {"scaled": False, "scale": scale}
        target_w = max(1, int(round(output_img.size[0] * scale)))
        target_h = max(1, int(round(output_img.size[1] * scale)))
        resized = output_img.resize((target_w, target_h), Image.LANCZOS)
        background = _average_corner_color(output_img)
        canvas = Image.new("RGB", output_img.size, background)
        offset = (
            max(0, (canvas.size[0] - target_w) // 2),
            max(0, (canvas.size[1] - target_h) // 2),
        )
        canvas.paste(resized, offset)
        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG")
        return buffer.getvalue(), {"scaled": True, "scale": scale}


def fit_output_to_donor(output_bytes: bytes, alpha_source: Path) -> tuple[bytes, dict]:
    with Image.open(alpha_source) as source_img:
        source_img = ImageOps.exif_transpose(source_img)
        if source_img.mode != "RGBA":
            source_img = source_img.convert("RGBA")
        alpha_channel = source_img.getchannel("A")
        target_size = source_img.size
        has_alpha = alpha_channel.getextrema() != (255, 255)

    with Image.open(io.BytesIO(output_bytes)) as output_img:
        output_img = ImageOps.exif_transpose(output_img)
        if output_img.mode != "RGB":
            output_img = output_img.convert("RGB")
        resized = False
        original_size = output_img.size
        if output_img.size != target_size:
            target_w, target_h = target_size
            out_w, out_h = output_img.size
            scale = max(target_w / out_w, target_h / out_h)
            new_w = max(1, int(round(out_w * scale)))
            new_h = max(1, int(round(out_h * scale)))
            if (new_w, new_h) != output_img.size:
                output_img = output_img.resize((new_w, new_h), Image.LANCZOS)
                resized = True
            bbox = _content_bbox(output_img)
            left = max(0, (output_img.size[0] - target_w) // 2)
            top = max(0, (output_img.size[1] - target_h) // 2)
            if bbox is not None:
                b_left, b_top, b_right, b_bottom = bbox
                if output_img.size[0] >= target_w:
                    if b_right - b_left <= target_w:
                        left = min(max(b_left - (target_w - (b_right - b_left)) // 2, 0), output_img.size[0] - target_w)
                if output_img.size[1] >= target_h:
                    if b_bottom - b_top <= target_h:
                        top = min(max(b_top - (target_h - (b_bottom - b_top)) // 2, 0), output_img.size[1] - target_h)
            output_img = output_img.crop((left, top, left + target_w, top + target_h))
        composited = output_img.convert("RGBA")
        composited.putalpha(alpha_channel)
        buffer = io.BytesIO()
        composited.save(buffer, format="PNG", icc_profile=output_img.info.get("icc_profile"))
        stats = {
            "has_alpha": has_alpha,
            "resized": resized,
            "target_size": target_size,
            "original_size": original_size,
        }
        return buffer.getvalue(), stats




def load_mask_image(mask_path: Path, target_size: tuple[int, int]) -> Image.Image:
    with Image.open(mask_path) as mask_img:
        mask_img = ImageOps.exif_transpose(mask_img)
        if mask_img.size != target_size:
            mask_img = ImageOps.fit(mask_img, target_size, Image.LANCZOS, centering=(0.5, 0.5))
        if mask_img.mode == "RGBA":
            return mask_img.getchannel("A")
        return mask_img.convert("L")


def diff_coverage_in_mask(
    donor_rgb: Image.Image,
    output_rgb: Image.Image,
    mask: Image.Image,
    threshold: int = 10,
) -> float:
    if output_rgb.size != donor_rgb.size:
        output_rgb = ImageOps.fit(output_rgb, donor_rgb.size, Image.LANCZOS, centering=(0.5, 0.5))
    mask = mask.convert("L")
    if mask.size != donor_rgb.size:
        mask = ImageOps.fit(mask, donor_rgb.size, Image.LANCZOS, centering=(0.5, 0.5))
    diff = ImageChops.difference(donor_rgb, output_rgb).convert("L")
    diff_mask = ImageChops.multiply(diff, mask.point(lambda p: 255 if p > 0 else 0))
    bw = diff_mask.point(lambda p: 255 if p > threshold else 0)
    hist = bw.histogram()
    total = sum(hist)
    covered = total - hist[0]
    return covered / total if total else 0.0


def chroma_delta_in_mask(
    donor_rgb: Image.Image,
    output_rgb: Image.Image,
    mask: Image.Image,
) -> float:
    if output_rgb.size != donor_rgb.size:
        output_rgb = ImageOps.fit(output_rgb, donor_rgb.size, Image.LANCZOS, centering=(0.5, 0.5))
    mask = mask.convert("L")
    if mask.size != donor_rgb.size:
        mask = ImageOps.fit(mask, donor_rgb.size, Image.LANCZOS, centering=(0.5, 0.5))
    donor_ycc = donor_rgb.convert("YCbCr")
    output_ycc = output_rgb.convert("YCbCr")
    donor_cb, donor_cr = donor_ycc.split()[1:]
    out_cb, out_cr = output_ycc.split()[1:]
    diff_cb = ImageChops.difference(donor_cb, out_cb)
    diff_cr = ImageChops.difference(donor_cr, out_cr)
    cb_mean = ImageStat.Stat(diff_cb, mask=mask).mean[0]
    cr_mean = ImageStat.Stat(diff_cr, mask=mask).mean[0]
    return (cb_mean + cr_mean) / 2


def apply_masked_chroma_transfer(
    donor_rgb: Image.Image,
    reference_rgb: Image.Image,
    mask: Image.Image,
    gain: float = 1.0,
) -> Image.Image:
    if reference_rgb.size != donor_rgb.size:
        reference_rgb = ImageOps.fit(reference_rgb, donor_rgb.size, Image.LANCZOS, centering=(0.5, 0.5))
    donor_y, _, _ = donor_rgb.convert("YCbCr").split()
    _, ref_cb, ref_cr = reference_rgb.convert("YCbCr").split()
    if gain != 1.0:
        def boost(val: int) -> int:
            shifted = 128 + (val - 128) * gain
            return max(0, min(255, int(round(shifted))))
        ref_cb = ref_cb.point(boost)
        ref_cr = ref_cr.point(boost)
    recolored = Image.merge("YCbCr", (donor_y, ref_cb, ref_cr)).convert("RGB")
    mask = mask.convert("L")
    if mask.size != donor_rgb.size:
        mask = ImageOps.fit(mask, donor_rgb.size, Image.LANCZOS, centering=(0.5, 0.5))
    return Image.composite(recolored, donor_rgb, mask)


def _mean_chroma(reference_rgb: Image.Image) -> tuple[int, int]:
    ycc = reference_rgb.convert("YCbCr")
    _, cb, cr = ycc.split()
    cb_mean = int(round(ImageStat.Stat(cb).mean[0]))
    cr_mean = int(round(ImageStat.Stat(cr).mean[0]))
    return cb_mean, cr_mean


def apply_masked_swatch_color(
    donor_rgb: Image.Image,
    reference_rgb: Image.Image,
    mask: Image.Image,
    expand_px: int = 0,
    soften_px: float = 0.0,
    min_luma_gain: float = 0.7,
    max_luma_gain: float = 1.0,
) -> Image.Image:
    if reference_rgb.size != donor_rgb.size:
        reference_rgb = ImageOps.fit(reference_rgb, donor_rgb.size, Image.LANCZOS, centering=(0.5, 0.5))
    donor_y = donor_rgb.convert("YCbCr").split()[0]
    cb_mean, cr_mean = _mean_chroma(reference_rgb)
    cb_img = Image.new("L", donor_rgb.size, cb_mean)
    cr_img = Image.new("L", donor_rgb.size, cr_mean)
    mask = mask.convert("L")
    if mask.size != donor_rgb.size:
        mask = ImageOps.fit(mask, donor_rgb.size, Image.LANCZOS, centering=(0.5, 0.5))
    if expand_px > 0:
        kernel = expand_px * 2 + 1
        mask = mask.filter(ImageFilter.MaxFilter(kernel))
    if soften_px > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(soften_px))
    ref_y = reference_rgb.convert("YCbCr").split()[0]
    donor_mean = ImageStat.Stat(donor_y, mask=mask).mean[0]
    ref_mean = ImageStat.Stat(ref_y).mean[0]
    gain = 1.0
    if donor_mean > 0:
        gain = ref_mean / donor_mean
    gain = max(min_luma_gain, min(max_luma_gain, gain))
    if gain != 1.0:
        donor_y = donor_y.point(lambda v: max(0, min(255, int(round(v * gain)))))
    recolored = Image.merge("YCbCr", (donor_y, cb_img, cr_img)).convert("RGB")
    return Image.composite(recolored, donor_rgb, mask)


def _center_square(image: Image.Image, size: int) -> Image.Image:
    width, height = image.size
    if size > min(width, height):
        size = min(width, height)
    left = (width - size) // 2
    top = (height - size) // 2
    return image.crop((left, top, left + size, top + size))


def _tile_texture(texture: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    tile_w, tile_h = texture.size
    target_w, target_h = target_size
    tiled = Image.new("RGB", target_size)
    for y in range(0, target_h, tile_h):
        for x in range(0, target_w, tile_w):
            tiled.paste(texture, (x, y))
    return tiled


def apply_masked_texture_transfer(
    donor_rgb: Image.Image,
    reference_rgb: Image.Image,
    mask: Image.Image,
    tile_size: int = 256,
    texture_strength: float = 0.25,
    chroma_gain: float = 1.4,
) -> Image.Image:
    if reference_rgb.size != donor_rgb.size:
        reference_rgb = ImageOps.fit(reference_rgb, donor_rgb.size, Image.LANCZOS, centering=(0.5, 0.5))
    ref_square = _center_square(reference_rgb, tile_size)
    if ref_square.size != (tile_size, tile_size):
        ref_square = ref_square.resize((tile_size, tile_size), Image.LANCZOS)
    tiled = _tile_texture(ref_square, donor_rgb.size)

    donor_y = donor_rgb.convert("YCbCr").split()[0]
    ref_y, ref_cb, ref_cr = tiled.convert("YCbCr").split()
    blended_y = Image.blend(donor_y, ref_y, max(0.0, min(1.0, texture_strength)))

    if chroma_gain != 1.0:
        def boost(val: int) -> int:
            shifted = 128 + (val - 128) * chroma_gain
            return max(0, min(255, int(round(shifted))))
        ref_cb = ref_cb.point(boost)
        ref_cr = ref_cr.point(boost)

    recolored = Image.merge("YCbCr", (blended_y, ref_cb, ref_cr)).convert("RGB")
    mask = mask.convert("L")
    if mask.size != donor_rgb.size:
        mask = ImageOps.fit(mask, donor_rgb.size, Image.LANCZOS, centering=(0.5, 0.5))
    return Image.composite(recolored, donor_rgb, mask)


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
        shadow_band = _find_shadow_band(donor_rgb, alpha_channel)
        if shadow_band is not None:
            output_img = _apply_shadow_band(donor_rgb, output_img, shadow_band)
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
                "shadow_band": shadow_band,
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
            "shadow_band": shadow_band,
            "mask_coverage": coverage,
            "mask_relaxed": mask_relaxed,
            "mask_tightened": mask_tightened,
            "mask_clipped": mask_clipped,
            "edge_guard_px": guard_used,
        }
        return buffer.getvalue(), stats
