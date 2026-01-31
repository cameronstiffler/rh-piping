"""Image preparation helpers."""

from __future__ import annotations

from dataclasses import dataclass
import io
from math import gcd
from pathlib import Path

import os

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat, ImageCms

from rh_piping.io import ensure_dir

TARGET_LONG_EDGE = 4096
EDGE_GUARD_PX = 0
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


def parse_hex_color(value: str) -> tuple[int, int, int] | None:
    raw = value.strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6:
        return None
    try:
        r = int(raw[0:2], 16)
        g = int(raw[2:4], 16)
        b = int(raw[4:6], 16)
    except ValueError:
        return None
    return (r, g, b)


def build_model_input(
    source: Path,
    pad_aspect_ratio: str | None = None,
    background_color: tuple[int, int, int] | None = None,
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
                if background_color:
                    if source_img.mode != "RGBA":
                        source_img = source_img.convert("RGBA")
                    flattened = Image.new("RGB", source_img.size, background_color)
                    flattened.paste(source_img, mask=source_img.getchannel("A"))
                    padded = Image.new("RGB", (width, target_height), background_color)
                    offset_y = (target_height - height) // 2
                    padded.paste(flattened, (0, offset_y))
                    buffer = io.BytesIO()
                    padded.save(buffer, format="PNG")
                    return buffer.getvalue(), padded.size
                if source_img.mode != "RGBA":
                    source_img = source_img.convert("RGBA")
                padded = Image.new("RGBA", (width, target_height), (0, 0, 0, 0))
                offset_y = (target_height - height) // 2
                padded.paste(source_img, (0, offset_y))
                buffer = io.BytesIO()
                padded.save(buffer, format="PNG")
                return buffer.getvalue(), padded.size
        if background_color:
            if source_img.mode != "RGBA":
                source_img = source_img.convert("RGBA")
            flattened = Image.new("RGB", source_img.size, background_color)
            flattened.paste(source_img, mask=source_img.getchannel("A"))
            buffer = io.BytesIO()
            flattened.save(buffer, format="PNG")
            return buffer.getvalue(), source_img.size
        target_size = source_img.size
    return source_bytes, target_size


def apply_chroma_key(
    image_bytes: bytes,
    key_color: tuple[int, int, int],
    tolerance: int = 8,
    softness: int = 0,
    edge_clip: int = 0,
    mask_bytes: bytes | None = None,
    mask_threshold: int = 1,
    mask_expand: int = 0,
) -> bytes:
    if tolerance < 0:
        tolerance = 0
    if softness < 0:
        softness = 0
    if edge_clip < 0:
        edge_clip = 0
    if mask_threshold < 0:
        mask_threshold = 0
    if mask_threshold > 255:
        mask_threshold = 255
    if mask_expand < 0:
        mask_expand = 0
    kr, kg, kb = key_color
    with Image.open(io.BytesIO(image_bytes)) as img:
        img = ImageOps.exif_transpose(img).convert("RGBA")
        mask_data = None
        if mask_bytes is not None:
            with Image.open(io.BytesIO(mask_bytes)) as mask_img:
                mask_img = ImageOps.exif_transpose(mask_img)
                if "A" in mask_img.getbands():
                    mask = mask_img.getchannel("A")
                else:
                    mask = mask_img.convert("L")
                if mask.size != img.size:
                    mask = mask.resize(img.size, Image.NEAREST)
                mask = mask.point(lambda p: 255 if p <= mask_threshold else 0)
                if mask_expand:
                    mask = mask.filter(ImageFilter.MaxFilter(mask_expand * 2 + 1))
                mask_data = mask.tobytes()
        pixels = list(img.getdata())
        new_pixels = []
        for idx, (r, g, b, a) in enumerate(pixels):
            if mask_data is not None and mask_data[idx] == 0:
                new_pixels.append((r, g, b, a))
                continue
            delta = max(abs(r - kr), abs(g - kg), abs(b - kb))
            if delta <= tolerance:
                alpha = 0
            elif softness > 0 and delta <= tolerance + softness:
                alpha = int(255 * (delta - tolerance) / softness)
            else:
                alpha = 255
            if edge_clip and alpha <= edge_clip:
                alpha = 0
            if 0 < alpha < 255:
                alpha_f = alpha / 255.0
                r = int(round((r - kr * (1.0 - alpha_f)) / alpha_f))
                g = int(round((g - kg * (1.0 - alpha_f)) / alpha_f))
                b = int(round((b - kb * (1.0 - alpha_f)) / alpha_f))
                r = max(0, min(255, r))
                g = max(0, min(255, g))
                b = max(0, min(255, b))
            if a < alpha:
                alpha = a
            new_pixels.append((r, g, b, alpha))
        img.putdata(new_pixels)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()


def restore_rgb_under_alpha(
    output_bytes: bytes,
    donor_bytes: bytes,
    alpha_threshold: int = 254,
) -> bytes:
    if alpha_threshold < 0:
        alpha_threshold = 0
    if alpha_threshold > 255:
        alpha_threshold = 255
    with Image.open(io.BytesIO(output_bytes)) as out_img:
        out_img = ImageOps.exif_transpose(out_img).convert("RGBA")
        alpha = out_img.getchannel("A")
        with Image.open(io.BytesIO(donor_bytes)) as donor_img:
            donor_img = ImageOps.exif_transpose(donor_img).convert("RGB")
            if donor_img.size != out_img.size:
                donor_img = donor_img.resize(out_img.size, Image.LANCZOS)
        mask = alpha.point(lambda p: 255 if p < alpha_threshold else 0)
        out_rgb = out_img.convert("RGB")
        composited = Image.composite(donor_img, out_rgb, mask)
        result = composited.convert("RGBA")
        result.putalpha(alpha)
        buffer = io.BytesIO()
        result.save(buffer, format="PNG")
        return buffer.getvalue()


def apply_donor_alpha(
    output_bytes: bytes,
    donor_bytes: bytes,
) -> bytes:
    with Image.open(io.BytesIO(output_bytes)) as out_img:
        out_img = ImageOps.exif_transpose(out_img).convert("RGBA")
        with Image.open(io.BytesIO(donor_bytes)) as donor_img:
            donor_img = ImageOps.exif_transpose(donor_img)
            if "A" not in donor_img.getbands():
                return output_bytes
            if donor_img.size != out_img.size:
                donor_img = donor_img.resize(out_img.size, Image.LANCZOS)
            alpha = donor_img.getchannel("A")
        out_img.putalpha(alpha)
        buffer = io.BytesIO()
        out_img.save(buffer, format="PNG")
        return buffer.getvalue()


def composite_over_background(
    output_bytes: bytes,
    background_color: tuple[int, int, int],
) -> bytes:
    with Image.open(io.BytesIO(output_bytes)) as out_img:
        out_img = ImageOps.exif_transpose(out_img).convert("RGBA")
        bg = Image.new("RGB", out_img.size, background_color)
        bg.paste(out_img, mask=out_img.getchannel("A"))
        buffer = io.BytesIO()
        bg.save(buffer, format="PNG")
        return buffer.getvalue()


def ensure_adobe_rgb(
    image_path: Path,
    adobe_icc_path: str | None,
) -> bool:
    if not adobe_icc_path:
        return False
    if not os.path.isfile(adobe_icc_path):
        return False
    try:
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            icc_bytes = img.info.get("icc_profile")
            if icc_bytes:
                try:
                    src_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc_bytes))
                    desc = ImageCms.getProfileDescription(src_profile)
                    if desc and "Adobe RGB" in desc:
                        return False
                except Exception:
                    src_profile = ImageCms.createProfile("sRGB")
            else:
                src_profile = ImageCms.createProfile("sRGB")
            dst_profile = ImageCms.ImageCmsProfile(adobe_icc_path)
            alpha = None
            if "A" in img.getbands():
                alpha = img.getchannel("A")
            rgb = img.convert("RGB")
            converted = ImageCms.profileToProfile(
                rgb,
                src_profile,
                dst_profile,
                outputMode="RGB",
            )
            if alpha is not None:
                converted = converted.convert("RGBA")
                converted.putalpha(alpha)
            with open(adobe_icc_path, "rb") as profile_file:
                adobe_bytes = profile_file.read()
            converted.save(image_path, format="PNG", icc_profile=adobe_bytes)
            return True
    except Exception:
        return False


def ensure_dpi(
    image_path: Path,
    target_dpi: int,
) -> tuple[bool, tuple[float, float] | None]:
    try:
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            icc_bytes = img.info.get("icc_profile")
            dpi = img.info.get("dpi")
            if dpi and len(dpi) >= 2:
                dpi_x, dpi_y = float(dpi[0]), float(dpi[1])
            else:
                dpi_x = dpi_y = None
            if dpi_x is not None and dpi_y is not None:
                if abs(dpi_x - target_dpi) < 0.5 and abs(dpi_y - target_dpi) < 0.5:
                    return False, (dpi_x, dpi_y)
            fmt = img.format or "PNG"
            save_kwargs = {"dpi": (target_dpi, target_dpi)}
            if icc_bytes:
                save_kwargs["icc_profile"] = icc_bytes
            img.save(image_path, format=fmt, **save_kwargs)
            if dpi_x is not None and dpi_y is not None:
                return True, (dpi_x, dpi_y)
            return True, None
    except Exception:
        return False, None


def normalize_mask_bytes(
    mask_bytes: bytes,
    target_size: tuple[int, int],
    threshold: int = 200,
) -> bytes:
    if threshold < 0:
        threshold = 0
    if threshold > 255:
        threshold = 255
    with Image.open(io.BytesIO(mask_bytes)) as mask_img:
        mask_img = ImageOps.exif_transpose(mask_img).convert("L")
        if mask_img.size != target_size:
            mask_img = mask_img.resize(target_size, Image.NEAREST)
        mask_img = mask_img.point(lambda p: 255 if p >= threshold else 0)
        mask_rgb = Image.new("RGB", target_size, (0, 0, 0))
        white = Image.new("RGB", target_size, (255, 255, 255))
        mask_rgb.paste(white, mask=mask_img)
        buffer = io.BytesIO()
        mask_rgb.save(buffer, format="PNG")
        return buffer.getvalue()


def clip_mask_bytes_to_alpha(
    mask_bytes: bytes,
    alpha: Image.Image,
    target_size: tuple[int, int],
) -> bytes:
    with Image.open(io.BytesIO(mask_bytes)) as mask_img:
        mask_img = ImageOps.exif_transpose(mask_img).convert("L")
    if mask_img.size != target_size:
        raise ValueError(
            f"Mask size {mask_img.size} does not match target {target_size}."
        )
    if alpha.size != target_size:
        raise ValueError(
            f"Alpha size {alpha.size} does not match target {target_size}."
        )
    alpha = alpha.point(lambda p: 255 if p > 0 else 0)
    clipped = ImageChops.multiply(mask_img, alpha)
    mask_rgb = Image.new("RGB", target_size, (0, 0, 0))
    white = Image.new("RGB", target_size, (255, 255, 255))
    mask_rgb.paste(white, mask=clipped)
    buffer = io.BytesIO()
    mask_rgb.save(buffer, format="PNG")
    return buffer.getvalue()


def pad_alpha_to_size(
    alpha: Image.Image,
    target_size: tuple[int, int],
) -> Image.Image:
    if alpha.size == target_size:
        return alpha
    src_w, src_h = alpha.size
    tgt_w, tgt_h = target_size
    if src_w != tgt_w or tgt_h < src_h:
        raise ValueError(
            f"Alpha size {alpha.size} cannot be padded to target {target_size}."
        )
    padded = Image.new("L", target_size, 0)
    offset = (0, (tgt_h - src_h) // 2)
    padded.paste(alpha, offset)
    return padded


def align_mask_bytes(
    mask_bytes: bytes,
    target_size: tuple[int, int],
    threshold: int = 200,
) -> bytes:
    if threshold < 0:
        threshold = 0
    if threshold > 255:
        threshold = 255
    with Image.open(io.BytesIO(mask_bytes)) as mask_img:
        mask_img = ImageOps.exif_transpose(mask_img).convert("L")
        src_w, src_h = mask_img.size
        tgt_w, tgt_h = target_size
        if (src_w, src_h) != (tgt_w, tgt_h):
            if src_w == tgt_w and src_h >= tgt_h:
                top = max(0, (src_h - tgt_h) // 2)
                mask_img = mask_img.crop((0, top, tgt_w, top + tgt_h))
            else:
                mask_img = ImageOps.fit(
                    mask_img, target_size, Image.NEAREST, centering=(0.5, 0.5)
                )
        mask_img = mask_img.point(lambda p: 255 if p >= threshold else 0)
        mask_rgb = Image.new("RGB", target_size, (0, 0, 0))
        white = Image.new("RGB", target_size, (255, 255, 255))
        mask_rgb.paste(white, mask=mask_img)
        buffer = io.BytesIO()
        mask_rgb.save(buffer, format="PNG")
        return buffer.getvalue()


def normalize_mask_bytes_exact(
    mask_bytes: bytes,
    target_size: tuple[int, int],
    threshold: int = 200,
) -> bytes:
    if threshold < 0:
        threshold = 0
    if threshold > 255:
        threshold = 255
    with Image.open(io.BytesIO(mask_bytes)) as mask_img:
        mask_img = ImageOps.exif_transpose(mask_img).convert("L")
    if mask_img.size != target_size:
        raise ValueError(
            f"Mask size {mask_img.size} does not match target {target_size}."
        )
    mask_img = mask_img.point(lambda p: 255 if p >= threshold else 0)
    mask_rgb = Image.new("RGB", target_size, (0, 0, 0))
    white = Image.new("RGB", target_size, (255, 255, 255))
    mask_rgb.paste(white, mask=mask_img)
    buffer = io.BytesIO()
    mask_rgb.save(buffer, format="PNG")
    return buffer.getvalue()


def pad_mask_bytes_to_size(
    mask_bytes: bytes,
    target_size: tuple[int, int],
) -> bytes:
    with Image.open(io.BytesIO(mask_bytes)) as mask_img:
        mask_img = ImageOps.exif_transpose(mask_img).convert("L")
    src_w, src_h = mask_img.size
    tgt_w, tgt_h = target_size
    if (src_w, src_h) != (tgt_w, tgt_h):
        if src_w >= tgt_w and src_h >= tgt_h:
            left = max(0, (src_w - tgt_w) // 2)
            top = max(0, (src_h - tgt_h) // 2)
            mask_img = mask_img.crop((left, top, left + tgt_w, top + tgt_h))
        else:
            padded = Image.new("L", target_size, 0)
            offset = (
                max(0, (tgt_w - src_w) // 2),
                max(0, (tgt_h - src_h) // 2),
            )
            padded.paste(mask_img, offset)
            mask_img = padded
    mask_rgb = Image.new("RGB", target_size, (0, 0, 0))
    white = Image.new("RGB", target_size, (255, 255, 255))
    mask_rgb.paste(white, mask=mask_img)
    buffer = io.BytesIO()
    mask_rgb.save(buffer, format="PNG")
    return buffer.getvalue()


def composite_with_mask(
    output_bytes: bytes,
    donor_path: Path,
    mask_bytes: bytes,
) -> bytes:
    with Image.open(io.BytesIO(output_bytes)) as out_img:
        out_img = ImageOps.exif_transpose(out_img).convert("RGBA")
        out_rgb = out_img.convert("RGB")
    with Image.open(donor_path) as donor_img:
        donor_img = ImageOps.exif_transpose(donor_img).convert("RGBA")
        donor_rgb = donor_img.convert("RGB")
        donor_alpha = donor_img.getchannel("A")
    with Image.open(io.BytesIO(mask_bytes)) as mask_img:
        mask_img = ImageOps.exif_transpose(mask_img).convert("L")
    if mask_img.size != donor_rgb.size:
        mask_img = ImageOps.fit(mask_img, donor_rgb.size, Image.NEAREST, centering=(0.5, 0.5))
    if out_rgb.size != donor_rgb.size:
        out_rgb = ImageOps.fit(out_rgb, donor_rgb.size, Image.LANCZOS, centering=(0.5, 0.5))
    merged = Image.composite(out_rgb, donor_rgb, mask_img)
    merged = merged.convert("RGBA")
    merged.putalpha(donor_alpha)
    buffer = io.BytesIO()
    merged.save(buffer, format="PNG")
    return buffer.getvalue()
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
            raise ValueError(
                f"Mask size {mask_img.size} does not match target {target_size}."
            )
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


def build_output_diff_mask(
    donor_rgb: Image.Image,
    output_bytes: bytes,
    threshold: int = 10,
    alpha: Image.Image | None = None,
) -> Image.Image:
    with Image.open(io.BytesIO(output_bytes)) as out_img:
        out_img = ImageOps.exif_transpose(out_img).convert("RGB")
    if out_img.size != donor_rgb.size:
        out_img = ImageOps.fit(out_img, donor_rgb.size, Image.LANCZOS, centering=(0.5, 0.5))
    diff = ImageChops.difference(donor_rgb, out_img).convert("L")
    mask = diff.point(lambda p: 255 if p > threshold else 0)
    if alpha is not None:
        if alpha.size != donor_rgb.size:
            raise ValueError(
                f"Alpha size {alpha.size} does not match donor {donor_rgb.size}."
            )
        alpha_bin = alpha.point(lambda p: 255 if p > 0 else 0)
        mask = ImageChops.multiply(mask, alpha_bin)
    return mask


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
        if not apply_mask:
            if preserve_luminance:
                output_img = _preserve_donor_luminance(donor_rgb, output_img)
            shadow_band = _find_shadow_band(donor_rgb, alpha_channel)
            if shadow_band is not None:
                output_img = _apply_shadow_band(donor_rgb, output_img, shadow_band)
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
        if mask_image is None:
            raise ValueError("Mask image is required for masked composite.")

        mask = mask_image
        if mask.mode != "L":
            mask = mask.convert("L")
        if mask.size != target_size:
            raise ValueError(
                f"Mask size {mask.size} does not match target {target_size}."
            )

        alpha_binary = alpha_channel.point(lambda p: 255 if p > 0 else 0)
        mask = ImageChops.multiply(mask, alpha_binary)
        total_pixels = target_size[0] * target_size[1]
        hist = mask.histogram()
        coverage = (total_pixels - hist[0]) / total_pixels if total_pixels else 0.0

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
            "preserve_luminance": False,
            "shadow_band": None,
            "mask_disabled": False,
            "mask_coverage": coverage,
            "mask_relaxed": False,
            "mask_tightened": False,
            "mask_clipped": False,
            "edge_guard_px": EDGE_GUARD_PX,
        }
        return buffer.getvalue(), stats
