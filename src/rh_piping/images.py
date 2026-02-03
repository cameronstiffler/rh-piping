"""Image preparation helpers."""

from __future__ import annotations

from dataclasses import dataclass
import base64
import io
import json
from math import gcd
from pathlib import Path
import re

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


@dataclass
class PaddingMetadata:
    original_size: tuple[int, int]
    padded_size: tuple[int, int]
    padding: tuple[int, int, int, int]


def pad_image_bytes(
    image_bytes: bytes,
    target_size: tuple[int, int] | None = None,
    background_color: tuple[int, ...] = (0, 0, 0, 0),
) -> tuple[bytes, PaddingMetadata]:
    with Image.open(io.BytesIO(image_bytes)) as img:
        img = ImageOps.exif_transpose(img)
        original_size = img.size
        if target_size is None:
            side = max(original_size)
            target_size = (side, side)
        target_w, target_h = target_size
        if target_w < original_size[0] or target_h < original_size[1]:
            raise ValueError(
                f"Target size {target_size} must be at least as large as {original_size}."
            )
        if original_size == target_size:
            padding = (0, 0, 0, 0)
            metadata = PaddingMetadata(original_size, target_size, padding)
            return image_bytes, metadata
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        bg_color = background_color
        if len(bg_color) == 3:
            bg_color = (*bg_color, 0)
        padded = Image.new("RGBA", target_size, bg_color)
        pad_left = (target_w - original_size[0]) // 2
        pad_top = (target_h - original_size[1]) // 2
        pad_right = target_w - original_size[0] - pad_left
        pad_bottom = target_h - original_size[1] - pad_top
        padded.paste(img, (pad_left, pad_top))
        buffer = io.BytesIO()
        padded.save(buffer, format="PNG")
        padding = (pad_left, pad_top, pad_right, pad_bottom)
        metadata = PaddingMetadata(original_size, target_size, padding)
        return buffer.getvalue(), metadata


def crop_padding_from_bytes(
    image_bytes: bytes, metadata: PaddingMetadata
) -> bytes:
    with Image.open(io.BytesIO(image_bytes)) as img:
        img = ImageOps.exif_transpose(img)
        padded_w, padded_h = metadata.padded_size
        if img.size != (padded_w, padded_h):
            img = img.resize((padded_w, padded_h), Image.LANCZOS)
        left, top, right, bottom = metadata.padding
        crop_box = (
            left,
            top,
            padded_w - right,
            padded_h - bottom,
        )
        cropped = img.crop(crop_box)
        buffer = io.BytesIO()
        cropped.save(buffer, format="PNG")
        return buffer.getvalue()


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


_JSON_FENCE_RE = re.compile(r"```(?:json)?(.*?)```", re.DOTALL | re.IGNORECASE)


def _strip_json_fences(text: str) -> str:
    match = _JSON_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _json_load_loose(payload: str):
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        try:
            return json.loads(payload, strict=False)
        except TypeError:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None


def _load_json_payload(text: str):
    cleaned = _strip_json_fences(text)
    if not cleaned:
        raise ValueError("Empty segmentation response.")
    cleaned = cleaned.strip()
    loaded = _json_load_loose(cleaned)
    if loaded is not None:
        return loaded
    start = min(
        (idx for idx in (cleaned.find("{"), cleaned.find("[")) if idx != -1),
        default=-1,
    )
    end = max(cleaned.rfind("}"), cleaned.rfind("]"))
    snippet = None
    if start != -1 and end != -1 and end > start:
        snippet = cleaned[start : end + 1]
        loaded = _json_load_loose(snippet)
        if loaded is not None:
            return loaded
    cleaned_sanitized = re.sub(r"[\x00-\x1F\x7F]", "", cleaned)
    loaded = _json_load_loose(cleaned_sanitized)
    if loaded is not None:
        return loaded
    if snippet:
        snippet_sanitized = re.sub(r"[\x00-\x1F\x7F]", "", snippet)
        loaded = _json_load_loose(snippet_sanitized)
        if loaded is not None:
            return loaded
    raise ValueError("No JSON object found in segmentation response.")


def _extract_segmentation_items(payload) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("masks", "objects", "segments", "annotations", "predictions", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if any(k in payload for k in ("mask", "segmentation", "box_2d", "bbox", "bounding_box")):
            return [payload]
    return []


def _decode_base64_blob(blob: str) -> bytes:
    cleaned = blob.strip()
    if cleaned.startswith("data:") and "," in cleaned:
        cleaned = cleaned.split(",", 1)[1]
    return base64.b64decode(cleaned)


def _bitstring_to_mask(bit_text: str, size: tuple[int, int]) -> Image.Image | None:
    if not bit_text:
        return None
    bits = [1 if ch == "1" else 0 for ch in bit_text if ch in "01"]
    if not bits:
        return None
    height, width = size
    total = height * width
    if total <= 0:
        return None
    if len(bits) < total:
        bits = bits + [0] * (total - len(bits))
    if len(bits) > total:
        bits = bits[:total]
    data = bytes(255 if b else 0 for b in bits)
    return Image.frombytes("L", (width, height), data)


def _coerce_mask_image(mask_info) -> Image.Image | None:
    if mask_info is None:
        return None
    width = height = None
    payload = None
    if isinstance(mask_info, dict):
        rle_mask = _coerce_rle_mask(mask_info)
        if rle_mask is not None:
            return rle_mask
        bit_payload = (
            mask_info.get("bits")
            or mask_info.get("mask_bits")
            or mask_info.get("bitmap")
            or mask_info.get("bitmask")
        )
        size_payload = mask_info.get("size") or mask_info.get("shape")
        if bit_payload and size_payload:
            size = _parse_rle_size(size_payload)
            if size:
                bit_mask = _bitstring_to_mask(str(bit_payload), size)
                if bit_mask is not None:
                    return bit_mask
    if isinstance(mask_info, dict):
        payload = (
            mask_info.get("data")
            or mask_info.get("image")
            or mask_info.get("png")
            or mask_info.get("mask")
            or mask_info.get("bytes")
        )
        width = mask_info.get("width") or mask_info.get("w")
        height = mask_info.get("height") or mask_info.get("h")
    elif isinstance(mask_info, str):
        payload = mask_info
    if not payload:
        return None
    raw = _decode_base64_blob(payload) if isinstance(payload, str) else payload
    try:
        with Image.open(io.BytesIO(raw)) as mask_img:
            return ImageOps.exif_transpose(mask_img).convert("L")
    except Exception:
        if width and height:
            w = int(width)
            h = int(height)
            if w > 0 and h > 0:
                if len(raw) == w * h:
                    return Image.frombytes("L", (w, h), raw)
                if len(raw) == w * h * 4:
                    return Image.frombytes("RGBA", (w, h), raw).convert("L")
    return None


def _parse_rle_size(size_info) -> tuple[int, int] | None:
    if isinstance(size_info, (list, tuple)) and len(size_info) == 2:
        h = int(size_info[0])
        w = int(size_info[1])
        if h > 0 and w > 0:
            return h, w
    if isinstance(size_info, dict):
        h = size_info.get("height") or size_info.get("h")
        w = size_info.get("width") or size_info.get("w")
        if h and w:
            return int(h), int(w)
    return None


def _decode_coco_rle(rle_text: str) -> list[int] | None:
    if not rle_text:
        return None
    counts: list[int] = []
    m = 0
    p = 0
    for ch in rle_text:
        x = ord(ch) - 48
        if x < 0:
            continue
        m |= (x & 0x1F) << (5 * p)
        if x & 0x20:
            p += 1
        else:
            if x & 0x10:
                m |= -1 << (5 * p)
            counts.append(m)
            m = 0
            p = 0
    if not counts:
        return None
    if any(v < 0 for v in counts):
        return None
    return counts


def _parse_rle_counts(counts_info) -> list[int] | None:
    if counts_info is None:
        return None
    if isinstance(counts_info, list):
        try:
            return [int(round(float(v))) for v in counts_info]
        except (TypeError, ValueError):
            return None
    if isinstance(counts_info, str):
        if any(ch.isalpha() for ch in counts_info):
            decoded = _decode_coco_rle(counts_info.strip())
            if decoded:
                return decoded
        nums = re.findall(r"\d+", counts_info)
        if nums:
            return [int(n) for n in nums]
    return None


def _rle_to_mask(
    counts: list[int],
    size: tuple[int, int],
    order: str = "row-major",
) -> Image.Image:
    height, width = size
    total = height * width
    flat = bytearray(total)
    idx = 0
    val = 0
    for run in counts:
        if run <= 0:
            val = 1 - val
            continue
        end = idx + run
        if end > total:
            end = total
        if val == 1:
            flat[idx:end] = b"\xff" * (end - idx)
        idx = end
        if idx >= total:
            break
        val = 1 - val
    if order.lower().startswith("col") or order.lower().startswith("fortran"):
        reordered = bytearray(total)
        for i in range(total):
            y = i % height
            x = i // height
            if x >= width:
                break
            reordered[y * width + x] = flat[i]
        flat = reordered
    return Image.frombytes("L", (width, height), bytes(flat))


def _coerce_rle_mask(mask_info) -> Image.Image | None:
    if not isinstance(mask_info, dict):
        return None
    rle = None
    order = "row-major"
    if "rle" in mask_info and isinstance(mask_info["rle"], dict):
        rle = mask_info["rle"]
        order = (
            rle.get("order")
            or rle.get("layout")
            or rle.get("encoding")
            or "row-major"
        )
    elif "counts" in mask_info and "size" in mask_info:
        rle = mask_info
    if rle is None:
        return None
    counts = _parse_rle_counts(rle.get("counts"))
    size = _parse_rle_size(rle.get("size"))
    if counts is None or size is None:
        return None
    return _rle_to_mask(counts, size, order=order)


def _scale_coord(value: float, max_value: int, normalized: bool, normalized_max: float = 1.0) -> int:
    if normalized:
        return int(round((value / normalized_max) * max_value))
    return int(round(value))


def _parse_box_list(values: list[float], target_size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    if len(values) != 4:
        return None
    width, height = target_size
    normalized = False
    normalized_max = 1.0
    if all(0.0 <= v <= 1.0 for v in values):
        normalized = True
        normalized_max = 1.0
    elif all(0.0 <= v <= 1000.0 for v in values):
        normalized = True
        normalized_max = 1000.0

    def _to_box(order: str) -> tuple[int, int, int, int]:
        if order == "yx":
            y1, x1, y2, x2 = values
        else:
            x1, y1, x2, y2 = values
        x1_i = _scale_coord(float(x1), width, normalized, normalized_max)
        x2_i = _scale_coord(float(x2), width, normalized, normalized_max)
        y1_i = _scale_coord(float(y1), height, normalized, normalized_max)
        y2_i = _scale_coord(float(y2), height, normalized, normalized_max)
        return x1_i, y1_i, x2_i, y2_i

    for order in ("yx", "xy"):
        x1_i, y1_i, x2_i, y2_i = _to_box(order)
        if x2_i > x1_i and y2_i > y1_i:
            return x1_i, y1_i, x2_i, y2_i
    return None


def _parse_box_mapping(box: dict, target_size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    width, height = target_size

    def _normalized(values: list[float]) -> tuple[bool, float]:
        if all(0.0 <= v <= 1.0 for v in values):
            return True, 1.0
        if all(0.0 <= v <= 1000.0 for v in values):
            return True, 1000.0
        return False, 1.0

    for keys in (("x1", "y1", "x2", "y2"), ("xmin", "ymin", "xmax", "ymax"), ("left", "top", "right", "bottom")):
        if all(k in box for k in keys):
            vals = [float(box[k]) for k in keys]
            normalized, normalized_max = _normalized(vals)
            x1_i = _scale_coord(vals[0], width, normalized, normalized_max)
            y1_i = _scale_coord(vals[1], height, normalized, normalized_max)
            x2_i = _scale_coord(vals[2], width, normalized, normalized_max)
            y2_i = _scale_coord(vals[3], height, normalized, normalized_max)
            if x2_i > x1_i and y2_i > y1_i:
                return x1_i, y1_i, x2_i, y2_i

    if all(k in box for k in ("x", "y", "width", "height")):
        x = float(box["x"])
        y = float(box["y"])
        w = float(box["width"])
        h = float(box["height"])
        normalized, normalized_max = _normalized([x, y, w, h])
        x1_i = _scale_coord(x, width, normalized, normalized_max)
        y1_i = _scale_coord(y, height, normalized, normalized_max)
        x2_i = _scale_coord(x + w, width, normalized, normalized_max)
        y2_i = _scale_coord(y + h, height, normalized, normalized_max)
        if x2_i > x1_i and y2_i > y1_i:
            return x1_i, y1_i, x2_i, y2_i

    if "top_left" in box and "bottom_right" in box:
        tl = box["top_left"]
        br = box["bottom_right"]
        if isinstance(tl, dict) and isinstance(br, dict):
            return _parse_box_mapping(
                {
                    "x1": tl.get("x"),
                    "y1": tl.get("y"),
                    "x2": br.get("x"),
                    "y2": br.get("y"),
                },
                target_size,
            )
    return None


def _extract_box(item: dict, target_size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    for key in ("box_2d", "bbox", "bounding_box", "boundingBox", "box"):
        if key in item:
            value = item.get(key)
            if isinstance(value, dict):
                return _parse_box_mapping(value, target_size)
            if isinstance(value, list):
                return _parse_box_list([float(v) for v in value], target_size)
    mask = item.get("mask")
    if isinstance(mask, dict):
        for key in ("box_2d", "bbox", "bounding_box", "boundingBox", "box"):
            if key in mask:
                value = mask.get(key)
                if isinstance(value, dict):
                    return _parse_box_mapping(value, target_size)
                if isinstance(value, list):
                    return _parse_box_list([float(v) for v in value], target_size)
    return None


def _parse_first_box(text: str) -> list[float] | None:
    match = re.search(r"box_2d\s*:\s*\[([^\]]+)", text)
    if not match:
        return None
    nums = re.findall(r"-?\d+\.?\d*", match.group(1))
    if len(nums) < 4:
        return None
    return [float(n) for n in nums[:4]]


def _parse_rle_from_text(text: str) -> tuple[list[int] | None, tuple[int, int] | None]:
    size_match = re.search(r"size\s*:\s*\[([^\]]+)", text)
    size_vals = None
    if size_match:
        size_nums = re.findall(r"\d+", size_match.group(1))
        if len(size_nums) >= 2:
            size_vals = (int(size_nums[0]), int(size_nums[1]))
    counts_match = re.search(r"counts\s*:\s*\[([^\]]*)", text)
    counts_vals: list[int] | None = None
    if counts_match:
        counts_nums = re.findall(r"\d+", counts_match.group(1))
        counts_vals = [int(n) for n in counts_nums] if counts_nums else None
    return counts_vals, size_vals


def _parse_bits_from_text(text: str) -> str | None:
    match = re.search(r"\"?bits\"?\s*:\s*\"?([01\s]+)", text, re.S)
    if match:
        return "".join(ch for ch in match.group(1) if ch in "01")
    return None


def _parse_size_from_text(text: str) -> tuple[int, int] | None:
    size_match = re.search(r"\"?size\"?\s*:\s*\[([^\]]+)", text)
    if not size_match:
        return None
    nums = re.findall(r"\d+", size_match.group(1))
    if len(nums) >= 2:
        return int(nums[0]), int(nums[1])
    return None


def _extract_base64_mask_from_text(text: str) -> bytes | None:
    match = re.search(r"data:image/(?:png|jpeg|jpg);base64,([A-Za-z0-9+/=\s]+)", text)
    if not match:
        match = re.search(r"\"mask\"\\s*:\\s*\"([A-Za-z0-9+/=\s]+)\"", text, re.S)
    if match:
        raw = "".join(ch for ch in match.group(1) if ch.isalnum() or ch in "+/=")
    else:
        marker = None
        for candidate in (
            "data:image/png;base64,",
            "data:image/jpeg;base64,",
            "data:image/jpg;base64,",
            "iVBOR",
            "/9j/",
        ):
            idx = text.find(candidate)
            if idx != -1:
                marker = candidate
                raw = text[idx + (len(candidate) if candidate.startswith("data:image") else 0) :]
                break
        else:
            mask_idx = text.find("\"mask\"")
            raw = text[mask_idx:] if mask_idx != -1 else text
        raw = "".join(ch for ch in raw if ch.isalnum() or ch in "+/=")
    if not raw:
        return None
    pad = (-len(raw)) % 4
    if pad:
        raw = raw + ("=" * pad)
    try:
        return base64.b64decode(raw, validate=False)
    except Exception:
        return None


def segmentation_json_to_mask_image(
    json_text: str,
    target_size: tuple[int, int],
    threshold: int = 1,
) -> Image.Image:
    if threshold < 0:
        threshold = 0
    if threshold > 255:
        threshold = 255
    items: list[dict] = []
    try:
        payload = _load_json_payload(json_text)
        items = _extract_segmentation_items(payload)
    except Exception:
        items = []
    if not items:
        box_vals = _parse_first_box(json_text)
        counts_vals, size_vals = _parse_rle_from_text(json_text)
        if counts_vals and size_vals:
            mask_img = _rle_to_mask(counts_vals, size_vals, order="row-major")
            mask_img = mask_img.point(lambda p: 255 if p >= threshold else 0)
            box = _parse_box_list(box_vals, target_size) if box_vals else None
            if box:
                x1, y1, x2, y2 = box
                box_w = max(1, x2 - x1)
                box_h = max(1, y2 - y1)
                if mask_img.size != (box_w, box_h):
                    mask_img = mask_img.resize((box_w, box_h), Image.NEAREST)
                placed = Image.new("L", target_size, 0)
                placed.paste(mask_img, (x1, y1))
                return placed
            if mask_img.size != target_size:
                mask_img = ImageOps.fit(mask_img, target_size, Image.NEAREST, centering=(0.5, 0.5))
            return mask_img
        bits_text = _parse_bits_from_text(json_text)
        size_vals = _parse_size_from_text(json_text)
        if bits_text and size_vals:
            mask_img = _bitstring_to_mask(bits_text, size_vals)
            if mask_img is not None:
                mask_img = mask_img.point(lambda p: 255 if p >= threshold else 0)
                box = _parse_box_list(box_vals, target_size) if box_vals else None
                if box:
                    x1, y1, x2, y2 = box
                    box_w = max(1, x2 - x1)
                    box_h = max(1, y2 - y1)
                    if mask_img.size != (box_w, box_h):
                        mask_img = mask_img.resize((box_w, box_h), Image.NEAREST)
                    placed = Image.new("L", target_size, 0)
                    placed.paste(mask_img, (x1, y1))
                    return placed
                if mask_img.size != target_size:
                    mask_img = ImageOps.fit(
                        mask_img, target_size, Image.NEAREST, centering=(0.5, 0.5)
                    )
                return mask_img
        raw_mask = _extract_base64_mask_from_text(json_text)
        if raw_mask:
            try:
                with Image.open(io.BytesIO(raw_mask)) as mask_img:
                    mask_img = ImageOps.exif_transpose(mask_img).convert("L")
            except Exception:
                mask_img = None
            if mask_img is not None:
                mask_img = mask_img.point(lambda p: 255 if p >= threshold else 0)
                box = _parse_box_list(box_vals, target_size) if box_vals else None
                if box:
                    x1, y1, x2, y2 = box
                    box_w = max(1, x2 - x1)
                    box_h = max(1, y2 - y1)
                    if mask_img.size != (box_w, box_h):
                        mask_img = mask_img.resize((box_w, box_h), Image.NEAREST)
                    placed = Image.new("L", target_size, 0)
                    placed.paste(mask_img, (x1, y1))
                    return placed
                if mask_img.size != target_size:
                    mask_img = ImageOps.fit(
                        mask_img, target_size, Image.NEAREST, centering=(0.5, 0.5)
                    )
                return mask_img
        raise ValueError("No mask items found in segmentation response.")
    full_mask = Image.new("L", target_size, 0)
    for item in items:
        mask_info = item.get("mask") or item.get("segmentation") or item.get("mask_data")
        mask_img = _coerce_mask_image(mask_info)
        if mask_img is None:
            mask_img = _coerce_rle_mask(item)
        if mask_img is None:
            continue
        mask_img = mask_img.convert("L")
        mask_img = mask_img.point(lambda p: 255 if p >= threshold else 0)
        box = _extract_box(item, target_size)
        if box:
            x1, y1, x2, y2 = box
            x1 = max(0, min(target_size[0], x1))
            x2 = max(0, min(target_size[0], x2))
            y1 = max(0, min(target_size[1], y1))
            y2 = max(0, min(target_size[1], y2))
            if x2 <= x1 or y2 <= y1:
                continue
            box_w = x2 - x1
            box_h = y2 - y1
            if mask_img.size != (box_w, box_h):
                mask_img = mask_img.resize((box_w, box_h), Image.NEAREST)
            placed = Image.new("L", target_size, 0)
            placed.paste(mask_img, (x1, y1))
            full_mask = ImageChops.lighter(full_mask, placed)
        else:
            if mask_img.size != target_size:
                mask_img = ImageOps.fit(
                    mask_img, target_size, Image.NEAREST, centering=(0.5, 0.5)
                )
            full_mask = ImageChops.lighter(full_mask, mask_img)
    if full_mask.getextrema() == (0, 0):
        raise ValueError("Segmentation mask was empty after parsing.")
    return full_mask


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


def adjust_image_color(
    image_bytes: bytes,
    saturation_scale: float = 1.0,
    hue_shift: float = 0.0,
) -> bytes:
    if saturation_scale == 1.0 and hue_shift == 0.0:
        return image_bytes
    with Image.open(io.BytesIO(image_bytes)) as img:
        img = ImageOps.exif_transpose(img)
        has_alpha = "A" in img.getbands()
        alpha = img.getchannel("A") if has_alpha else None
        rgb = img.convert("RGB")
        hsv = rgb.convert("HSV")
        h, s, v = hsv.split()
        if hue_shift:
            shift = int(round((hue_shift / 360.0) * 255)) % 256
            h = h.point(lambda x: (x + shift) % 256)
        if saturation_scale != 1.0:
            def scale_channel(val: int) -> int:
                scaled = int(round(val * saturation_scale))
                if scaled < 0:
                    return 0
                if scaled > 255:
                    return 255
                return scaled

            s = s.point(scale_channel)
        adjusted = Image.merge("HSV", (h, s, v)).convert("RGB")
        if alpha is not None:
            adjusted.putalpha(alpha)
        buffer = io.BytesIO()
        adjusted.save(buffer, format="PNG")
        return buffer.getvalue()




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


def overlay_donor_with_mask(
    output_bytes: bytes,
    donor_path: Path,
    mask_image: Image.Image,
    invert: bool = False,
) -> bytes:
    with Image.open(io.BytesIO(output_bytes)) as out_img:
        out_img = ImageOps.exif_transpose(out_img).convert("RGBA")
    with Image.open(donor_path) as donor_img:
        donor_img = ImageOps.exif_transpose(donor_img).convert("RGBA")
    mask = mask_image.convert("L")
    if mask.size != donor_img.size:
        mask = ImageOps.fit(mask, donor_img.size, Image.NEAREST, centering=(0.5, 0.5))
    if invert:
        mask = ImageOps.invert(mask)
    if out_img.size != donor_img.size:
        out_img = ImageOps.fit(out_img, donor_img.size, Image.LANCZOS, centering=(0.5, 0.5))
    merged = Image.composite(donor_img, out_img, mask)
    buffer = io.BytesIO()
    merged.save(buffer, format="PNG")
    return buffer.getvalue()
