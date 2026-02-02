"""Job discovery and orchestration for piping edits."""

from __future__ import annotations

from dataclasses import dataclass
import io
import hashlib
import json
from pathlib import Path
import re
import uuid
from datetime import datetime, timezone

from PIL import Image, ImageOps, ImageFilter

from rh_piping.config import AppConfig
from rh_piping.genai_client import (
    create_client,
    generate_piping_image,
    generate_piping_mask,
    generate_segmentation_mask_response,
    normalize_model_id,
    remove_background_vertex,
)
from rh_piping.images import (
    aspect_ratio_for_size,
    apply_chroma_key,
    build_model_input,
    build_square_mask_input,
    content_bbox_from_bytes,
    content_bbox_from_path,
    composite_output_with_donor,
    composite_over_background,
    convert_to_4k_png,
    fit_output_to_donor,
    ensure_adobe_rgb,
    ensure_dpi,
    align_mask_bytes,
    clip_mask_bytes_to_alpha,
    composite_with_mask,
    segmentation_json_to_mask_image,
    normalize_mask_bytes,
    normalize_mask_bytes_exact,
    pad_alpha_to_size,
    pad_mask_bytes_to_size,
    scale_output_to_donor_width,
    load_mask_image,
    parse_hex_color,
    apply_donor_alpha,
    restore_rgb_under_alpha,
)
from rh_piping.io import ensure_dir, list_images
from rh_piping.masks import (
    find_mask_for_product,
)
from rh_piping.prompts import prompt_id_from_path

ORIGINAL_ROOT = "original"
DONOR_DIRNAME = "donor_image"
COLOR_REF_DIRNAME = "color_reference"
PIPING_REF_DIRNAME = "piping_ref_highlighted"

PROCESSED_DONOR_DIRNAME = "donor_image"
PROCESSED_COLOR_DIRNAME = "color_reference"
PROCESSED_PIPING_REF_DIRNAME = "piping_ref_highlighted"

MAX_RESULTS = 100
RAW_OUTPUT_MAX_ATTEMPTS = 3
RAW_FIT_MAX_ATTEMPTS = 12
RAW_SCALE_MAX_ATTEMPTS = 12
RAW_SCALE_TOLERANCE = 0.03
MODEL_MASK_MAX_ATTEMPTS = 3
MAX_FAILURES = 5
PROMPT_PID_VALUE = re.compile(r"PID(?P<value>-?\d+)")
RECENT_DIRNAME = "recent"
RECENT_SUBMITTED_EDIT_DIR = "submitted/edit"
RECENT_SUBMITTED_MASK_DIR = "submitted/mask"
RECENT_RETURNED_MASK_DIR = "returned/mask"
RECENT_RETURNED_RESULT_DIR = "returned/result"
RECENT_RETURNED_POST_DIR = "returned/post"
SUPPORTED_ASPECT_RATIOS = [
    "1:1",
    "2:3",
    "3:2",
    "3:4",
    "4:3",
    "9:16",
    "16:9",
    "21:9",
]


@dataclass
class PipingJob:
    product_name: str
    donor_original: Path
    donor_processed: Path


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _sanitize_model_tag(model_name: str) -> str:
    cleaned = model_name.strip()
    if cleaned.startswith("models/"):
        cleaned = cleaned[len("models/") :]
    if cleaned.startswith("models-"):
        cleaned = cleaned[len("models-") :]
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", cleaned)
    return clean.strip("-") or "model"


def _pid_label(prompt_id: str) -> str:
    match = PROMPT_PID_VALUE.search(prompt_id)
    if not match:
        return "PID-NA"
    value = match.group("value")
    return f"PID{value}" if value.startswith("-") else f"PID-{value}"


def _format_mask_prompt(
    prompt: str,
    mask_width: int,
    mask_height: int,
    donor_width: int,
    donor_height: int,
) -> str:
    return (
        prompt.replace("{{MASK_WIDTH}}", str(mask_width))
        .replace("{{MASK_HEIGHT}}", str(mask_height))
        .replace("{{MASK_SIZE}}", f"{mask_width}x{mask_height}")
        .replace("{{DONOR_WIDTH}}", str(donor_width))
        .replace("{{DONOR_HEIGHT}}", str(donor_height))
        .replace("{{DONOR_SIZE}}", f"{donor_width}x{donor_height}")
    )


def _load_mid_mask_prompt(
    prompts_dir: Path,
    prompt_id: str,
    suffix: str = "",
) -> str:
    mask_prompt_dir = prompts_dir / "mask_pass"
    mask_prompt_paths: list[Path] = []

    def _append_candidates(pid_value: str) -> None:
        candidates = [
            f"mask_prompt_MID-{pid_value}_cushion{suffix}.md",
            f"mask_prompt_MID-{pid_value}{suffix}.md",
        ]
        for name in candidates:
            mask_prompt_paths.append(mask_prompt_dir / name)

    pid_match = PROMPT_PID_VALUE.search(prompt_id)
    if pid_match:
        pid_value = pid_match.group("value").lstrip("-")
        _append_candidates(pid_value)
    _append_candidates("3")
    for mask_prompt_path in mask_prompt_paths:
        if mask_prompt_path.exists():
            return mask_prompt_path.read_text(encoding="utf-8").strip()
    return ""


def _load_post_mask_prompt(
    prompts_dir: Path,
    prompt_id: str,
) -> str:
    for suffix in ("_piping", "_post"):
        prompt = _load_mid_mask_prompt(prompts_dir, prompt_id, suffix=suffix)
        if prompt:
            return prompt
    return ""


def _nearest_supported_aspect_ratio(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return "1:1"
    target = width / height
    best = SUPPORTED_ASPECT_RATIOS[0]
    best_diff = float("inf")
    for token in SUPPORTED_ASPECT_RATIOS:
        num, den = token.split(":")
        ratio = int(num) / int(den)
        diff = abs(ratio - target)
        if diff < best_diff:
            best_diff = diff
            best = token
    return best


def _fits_full_frame(
    output_bytes: bytes,
    donor_path: Path,
    min_margin_frac: float = 0.005,
) -> bool:
    bbox, out_size = content_bbox_from_bytes(output_bytes)
    if bbox is None:
        return False
    out_w, out_h = out_size
    left, top, right, bottom = bbox
    out_margins = {
        "left": left / out_w,
        "right": (out_w - right) / out_w,
        "top": top / out_h,
        "bottom": (out_h - bottom) / out_h,
    }
    with Image.open(donor_path) as donor_img:
        donor_img = ImageOps.exif_transpose(donor_img)
        donor_bbox = (
            donor_img.getchannel("A").point(lambda p: 255 if p > 0 else 0).getbbox()
            if "A" in donor_img.getbands()
            else None
        )
        if donor_bbox is None:
            return all(m >= min_margin_frac for m in out_margins.values())
        d_w, d_h = donor_img.size
        d_left, d_top, d_right, d_bottom = donor_bbox
        donor_margins = {
            "left": d_left / d_w,
            "right": (d_w - d_right) / d_w,
            "top": d_top / d_h,
            "bottom": (d_h - d_bottom) / d_h,
        }
    for side in ("left", "right", "top", "bottom"):
        required = max(min_margin_frac, donor_margins[side] * 0.5)
        if out_margins[side] < required:
            return False
    return True


def _scale_matches_donor(
    output_bytes: bytes,
    donor_bbox: tuple[int, int, int, int] | None,
    tolerance: float = RAW_SCALE_TOLERANCE,
) -> bool:
    if donor_bbox is None:
        return True
    left, top, right, bottom = donor_bbox
    donor_w = max(1, right - left)
    donor_h = max(1, bottom - top)
    out_bbox, _ = content_bbox_from_bytes(output_bytes)
    if out_bbox is None:
        return False
    o_left, o_top, o_right, o_bottom = out_bbox
    out_w = max(1, o_right - o_left)
    out_h = max(1, o_bottom - o_top)
    width_ratio = out_w / donor_w
    height_ratio = out_h / donor_h
    return (
        abs(1.0 - width_ratio) <= tolerance
        and abs(1.0 - height_ratio) <= tolerance
    )


def _normalize_results(requested: int) -> int:
    if requested == 3:
        return 5
    return requested


def _existing_result_indices(
    out_dir: Path,
    stem_prefix: str,
) -> set[int]:
    if not out_dir.exists():
        return set()
    pattern = re.compile(rf"^{re.escape(stem_prefix)}(?P<index>\d+)\.(?:png|jpg|jpeg)$")
    indices: set[int] = set()
    for path in out_dir.iterdir():
        if not path.is_file():
            continue
        match = pattern.match(path.name)
        if not match:
            continue
        indices.add(int(match.group("index")))
    return indices


def _first_free_index(indices: set[int]) -> int:
    candidate = 1
    while candidate in indices:
        candidate += 1
    return candidate


def _output_size_from_bytes(output_bytes: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(output_bytes)) as out_img:
        out_img = ImageOps.exif_transpose(out_img)
        return out_img.size


def _sniff_image_extension(output_bytes: bytes) -> str:
    if output_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if output_bytes.startswith(b"\xff\xd8"):
        return ".jpg"
    return ".png"


def _format_flag_tag(
    no_mask: bool,
    preserve_luminance: bool,
    mask_used: bool,
    post_process: bool,
    fit_only: bool,
    chroma_key: bool,
) -> str:
    flags: list[str] = []
    if no_mask:
        flags.append("nomask")
    if preserve_luminance:
        flags.append("lum")
    if mask_used:
        flags.append("mask")
    if chroma_key:
        flags.append("ckey")
    if fit_only:
        flags.append("fit")
    elif not post_process:
        flags.append("nopost")
    if not flags:
        return ""
    return f"_FX-{'-'.join(flags)}"


def _short_product_tag(product_name: str, length: int = 10) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", product_name)
    if not cleaned:
        cleaned = "prod"
    digest = hashlib.sha1(product_name.encode("utf-8")).hexdigest()[:4]
    return f"{cleaned[:length]}{digest}"


def _short_model_tag(model_tag: str, length: int = 6) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", model_tag)
    if not cleaned:
        cleaned = "model"
    digest = hashlib.sha1(model_tag.encode("utf-8")).hexdigest()[:3]
    return f"{cleaned[:length]}{digest}"


def _format_flag_tag_short(
    no_mask: bool,
    preserve_luminance: bool,
    mask_used: bool,
    post_process: bool,
    fit_only: bool,
    chroma_key: bool,
    vertex_bg_remove: bool,
) -> str:
    flags: list[str] = []
    if no_mask:
        flags.append("N")
    if preserve_luminance:
        flags.append("L")
    if mask_used:
        flags.append("M")
    if chroma_key:
        flags.append("K")
    if vertex_bg_remove:
        flags.append("B")
    if fit_only:
        flags.append("F")
    elif not post_process:
        flags.append("P")
    if not flags:
        return ""
    return f"_F{''.join(flags)}"


def _match_product(donor: Path, product_name: str) -> bool:
    return donor.stem == product_name or donor.name == product_name


def _write_recent_file(
    root_dir: Path,
    bucket: str,
    filename: str,
    data: bytes,
) -> Path:
    dest_dir = root_dir / RECENT_DIRNAME / bucket
    ensure_dir(dest_dir)
    dest_path = dest_dir / filename
    dest_path.write_bytes(data)
    return dest_path


def _select_recent_mask(mask_dir: Path) -> Path | None:
    if not mask_dir.exists():
        return None
    candidates = [p for p in mask_dir.iterdir() if p.is_file()]
    if not candidates:
        return None
    priority_prefixes = (
        "piping_mask",
        "model_cushion_mask",
        "model_mask",
        "segmentation_mask",
    )
    for prefix in priority_prefixes:
        filtered = [p for p in candidates if p.name.startswith(prefix)]
        if filtered:
            return max(filtered, key=lambda p: p.stat().st_mtime)
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _build_mask_calibration_overlay(
    donor_path: Path,
    mask_path: Path,
) -> bytes:
    with Image.open(donor_path) as donor_img:
        donor_img = ImageOps.exif_transpose(donor_img).convert("RGBA")
        donor_rgb = donor_img.convert("RGB")
        donor_alpha = donor_img.getchannel("A")
    with Image.open(mask_path) as mask_img:
        mask_img = ImageOps.exif_transpose(mask_img).convert("L")
    if mask_img.size != donor_rgb.size:
        mask_img = ImageOps.fit(mask_img, donor_rgb.size, Image.NEAREST, centering=(0.5, 0.5))
    red_layer = Image.new("RGB", donor_rgb.size, (255, 0, 0))
    overlay = Image.composite(red_layer, donor_rgb, mask_img)
    overlay = overlay.convert("RGBA")
    overlay.putalpha(donor_alpha)
    buffer = io.BytesIO()
    overlay.save(buffer, format="PNG")
    return buffer.getvalue()


def _align_mask_to_bbox(
    mask_img: Image.Image,
    donor_bbox: tuple[int, int, int, int],
    output_size: tuple[int, int],
) -> Image.Image:
    mx1, my1, mx2, my2 = mask_img.getbbox() or (0, 0, *mask_img.size)
    dx1, dy1, dx2, dy2 = donor_bbox
    mw = max(1, mx2 - mx1)
    mh = max(1, my2 - my1)
    dw = max(1, dx2 - dx1)
    dh = max(1, dy2 - dy1)
    sx = dw / mw
    sy = dh / mh
    tx = dx1 - sx * mx1
    ty = dy1 - sy * my1
    inv_sx = 1.0 / sx if sx else 1.0
    inv_sy = 1.0 / sy if sy else 1.0
    matrix = (inv_sx, 0.0, -tx * inv_sx, 0.0, inv_sy, -ty * inv_sy)
    return mask_img.transform(
        output_size,
        Image.AFFINE,
        matrix,
        resample=Image.NEAREST,
        fillcolor=0,
    )


def _write_recent_json(
    root_dir: Path,
    filename: str,
    payload: dict[str, object],
) -> Path:
    dest_dir = root_dir / RECENT_DIRNAME
    ensure_dir(dest_dir)
    dest_path = dest_dir / filename
    dest_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return dest_path


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _utc_run_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _recent_tag(
    pid_label: str,
    model_tag: str,
    run_stamp: str,
    run_id: str,
    result_index: int | None = None,
) -> str:
    parts = [pid_label, model_tag, run_stamp, f"run{run_id[:8]}"]
    if result_index is not None:
        parts.append(f"R{result_index}")
    return "_".join(parts)


def _recent_filename(base: str, ext: str, tag: str) -> str:
    return f"{base}_{tag}{ext}"


def build_jobs(assets_dir: Path, processed_dir: Path, product: str | None) -> list[PipingJob]:
    donor_dir = assets_dir / ORIGINAL_ROOT / DONOR_DIRNAME
    donors = list_images(donor_dir)
    if product:
        donors = [donor for donor in donors if _match_product(donor, product)]

    processed_donor_dir = processed_dir / PROCESSED_DONOR_DIRNAME
    return [
        PipingJob(
            product_name=donor.stem,
            donor_original=donor,
            donor_processed=processed_donor_dir / f"{donor.stem}.png",
        )
        for donor in donors
    ]


def run_pipeline(
    config: AppConfig,
    prompt_path: Path | None,
    prompt_text: str,
    product: str | None,
    results: int,
    dry_run: bool = False,
    limit: int | None = None,
    no_mask: bool = False,
    preserve_luminance: bool = False,
    post_process: bool = True,
    fit_only: bool = False,
    enforce_raw_size: bool = True,
    retry_until_fits: bool = False,
    retry_until_scale: bool = False,
    scale_to_donor: bool = False,
    chroma_key_hex: str | None = None,
    chroma_key_tolerance: int = 8,
    chroma_key_softness: int = 0,
    chroma_key_edge_clip: int = 0,
    chroma_key_restore_donor: bool = False,
    chroma_key_restore_threshold: int = 254,
    chroma_key_mask_donor: bool = True,
    chroma_key_mask_threshold: int = 8,
    chroma_key_mask_expand: int = 2,
    vertex_bg_remove: bool = False,
    vertex_bg_model: str | None = None,
    vertex_bg_prompt: str | None = None,
    vertex_bg_output_mime: str | None = None,
    vertex_bg_location: str | None = None,
    vertex_bg_max_bytes: int | None = None,
    vertex_bg_max_edge: int | None = None,
    model_mask_pass: bool = False,
    force_model_mask: bool = False,
    model_mask_prompt: str | None = None,
    model_mask_threshold: int = 200,
    segmentation_mask_pass: bool = False,
    segmentation_mask_model: str | None = None,
    segmentation_mask_threshold: int = 1,
    segmentation_response_mime_type: str | None = None,
    segmentation_max_output_tokens: int | None = None,
    post_mask_pass: bool = False,
    post_mask_prompt: str | None = None,
    post_mask_threshold: int | None = None,
    post_mask_shift_y: int = 0,
    post_mask_expand: int = 0,
    mask_only: bool = False,
    calibrate_mask: bool = False,
    calibrate_mask_bbox: bool = False,
) -> list[PipingJob]:
    ensure_dir(config.output_dir)
    run_id = uuid.uuid4().hex
    run_started = _utc_iso_now()
    run_stamp = _utc_run_stamp()

    if fit_only:
        post_process = False
        no_mask = True
        preserve_luminance = False
        segmentation_mask_pass = False
    elif not post_process:
        no_mask = True
        preserve_luminance = False
        segmentation_mask_pass = False
    if chroma_key_hex:
        if post_process:
            print("[info] chroma key enabled; disabling composite post-processing.")
        post_process = False
        no_mask = True
        preserve_luminance = False
        segmentation_mask_pass = False
        if vertex_bg_remove:
            print("[info] chroma key enabled; skipping Vertex background removal.")
        vertex_bg_remove = False
    if post_mask_threshold is None:
        post_mask_threshold = model_mask_threshold
    if segmentation_mask_pass and model_mask_pass:
        print("[info] segmentation mask pass enabled; disabling model mask pass.")
        model_mask_pass = False
    if results < 1 or results > MAX_RESULTS:
        raise ValueError(f"--results must be between 1 and {MAX_RESULTS}")
    requested_results = results
    results = _normalize_results(results)
    if results != requested_results:
        print(
            f"[info] requested results={requested_results}; generating {results} per donor image"
        )

    jobs = build_jobs(config.assets_dir, config.processed_dir, product)
    if limit is not None:
        jobs = jobs[: max(limit, 0)]

    prompt_id = prompt_id_from_path(prompt_path)
    pid_label = _pid_label(prompt_id)
    model_id = normalize_model_id(config.model, config.use_vertex)
    model_tag = _sanitize_model_tag(model_id)
    model_tag_short = _short_model_tag(model_tag)
    if calibrate_mask:
        for job in jobs:
            donor_meta = convert_to_4k_png(job.donor_original, job.donor_processed)
            out_dir = config.output_dir / job.product_name
            recent_mask_dir = out_dir / RECENT_DIRNAME / RECENT_RETURNED_MASK_DIR
            mask_path = _select_recent_mask(recent_mask_dir)
            if mask_path is None:
                masks_dir = out_dir / "masks"
                mask_path = _select_recent_mask(masks_dir)
            if mask_path is None:
                mask_path = find_mask_for_product(config.masks_dir, job.product_name)
            if mask_path is None or not mask_path.exists():
                raise FileNotFoundError(
                    f"No recent mask found for {job.product_name}."
                )
            if calibrate_mask_bbox:
                with Image.open(mask_path) as mask_img:
                    mask_img = ImageOps.exif_transpose(mask_img).convert("L")
                    mask_img = mask_img.point(lambda p: 255 if p > 0 else 0)
                donor_bbox, _ = content_bbox_from_path(job.donor_processed)
                if donor_bbox is None:
                    donor_bbox = (0, 0, donor_meta.width, donor_meta.height)
                aligned = _align_mask_to_bbox(
                    mask_img,
                    donor_bbox,
                    (donor_meta.width, donor_meta.height),
                )
                buffer = io.BytesIO()
                aligned.save(buffer, format="PNG")
                aligned_path = _write_recent_file(
                    out_dir,
                    RECENT_RETURNED_MASK_DIR,
                    _recent_filename(
                        "piping_mask_bbox",
                        ".png",
                        _recent_tag(pid_label, model_tag_short, run_stamp, run_id),
                    ),
                    buffer.getvalue(),
                )
                mask_path = aligned_path
            recent_tag = _recent_tag(
                pid_label,
                model_tag_short,
                run_stamp,
                run_id,
            )
            overlay_bytes = _build_mask_calibration_overlay(
                job.donor_processed,
                mask_path,
            )
            filename = _recent_filename("cal_mask", ".png", recent_tag)
            cal_path = _write_recent_file(
                out_dir,
                RECENT_RETURNED_POST_DIR,
                filename,
                overlay_bytes,
            )
            print(
                f"[cal-mask] {job.product_name} "
                f"mask={mask_path.name} -> {cal_path}"
            )
        return jobs

    color_dir = config.assets_dir / ORIGINAL_ROOT / COLOR_REF_DIRNAME
    color_refs = list_images(color_dir)
    if not color_refs:
        raise FileNotFoundError(f"No color references found in {color_dir}")
    piping_ref_dir = config.assets_dir / ORIGINAL_ROOT / PIPING_REF_DIRNAME
    piping_refs = list_images(piping_ref_dir)
    if config.piping_ref_max is not None:
        if config.piping_ref_max <= 0:
            piping_refs = []
        else:
            piping_refs = piping_refs[: config.piping_ref_max]

    print("[connection]")
    print(f" model={model_id}")
    print(f" location={config.location}")
    print("[/connection]")
    chroma_key_color = None
    chroma_key_label = None
    if chroma_key_hex:
        chroma_key_color = parse_hex_color(chroma_key_hex)
        if chroma_key_color is None:
            raise ValueError(f"Invalid chroma key color: {chroma_key_hex}")
        if chroma_key_hex.startswith("#"):
            chroma_key_label = chroma_key_hex
        else:
            chroma_key_label = f"#{chroma_key_hex}"
        prompt_text = (
            f"{prompt_text.rstrip()}\n"
            "Background Requirement: The background outside the sofa is a solid, "
            f"flat color {chroma_key_label} (RGB {chroma_key_color[0]}, "
            f"{chroma_key_color[1]}, {chroma_key_color[2]}).\n"
        )
    preview_bg_color = None
    if config.preview_bg_hex:
        preview_bg_color = parse_hex_color(config.preview_bg_hex)
        if preview_bg_color is None:
            raise ValueError(f"Invalid preview background color: {config.preview_bg_hex}")

    if dry_run:
        print(f"Discovered {len(jobs)} job(s).")
        print(f"Color references found: {len(color_refs)}; using first: {color_refs[0].name}")
        if piping_refs:
            print(
                f"Piping references found: {len(piping_refs)}; using first: {piping_refs[0].name}"
            )
        else:
            print("Piping references found: 0")
        for job in jobs:
            print(f"- donor={job.donor_original.name}")
        return jobs

    if not jobs:
        print("No donor images found to process.")
        return jobs

    print(f"[prompt file] {prompt_path or 'none'}")
    print("[prompt text]")
    print(prompt_text)
    print("[/prompt text]")

    client = create_client(config)

    print(f"Color references found: {len(color_refs)}; using first: {color_refs[0].name}")
    if piping_refs:
        print(
            f"Piping references found: {len(piping_refs)}; using first: {piping_refs[0].name}"
        )
    else:
        print("Piping references found: 0")

    processed_color_dir = config.processed_dir / PROCESSED_COLOR_DIRNAME
    processed_color_map: dict[Path, Path] = {}
    for color_ref in color_refs:
        processed_path = processed_color_dir / f"{color_ref.stem}.png"
        meta = convert_to_4k_png(color_ref, processed_path)
        print(
            f"[convert] color_ref {color_ref.name} -> {processed_path.name} "
            f"({meta.width}x{meta.height}, {meta.mode})"
        )
        processed_color_map[color_ref] = processed_path

    selected_color = color_refs[0]
    selected_color_processed = processed_color_map[selected_color]

    piping_ref_bytes: list[bytes] = []
    if piping_refs:
        processed_piping_dir = config.processed_dir / PROCESSED_PIPING_REF_DIRNAME
        for piping_ref in piping_refs:
            processed_path = processed_piping_dir / f"{piping_ref.stem}.png"
            meta = convert_to_4k_png(piping_ref, processed_path)
            print(
                f"[convert] piping_ref {piping_ref.name} -> {processed_path.name} "
                f"({meta.width}x{meta.height}, {meta.mode})"
            )
            piping_ref_bytes.append(processed_path.read_bytes())

    for job in jobs:
        donor_meta = convert_to_4k_png(job.donor_original, job.donor_processed)
        print(
            f"[convert] donor {job.donor_original.name} -> {job.donor_processed.name} "
            f"({donor_meta.width}x{donor_meta.height}, {donor_meta.mode})"
        )
        out_dir = config.output_dir / job.product_name
        mask_short_tag = _short_product_tag(job.product_name)
        model_mask_out = out_dir / "masks" / f"{mask_short_tag}_{pid_label}_mask.png"
        existing_model_mask = model_mask_out if model_mask_out.exists() else None
        donor_bbox, _ = content_bbox_from_path(job.donor_processed)
        aspect_ratio = aspect_ratio_for_size(donor_meta.width, donor_meta.height)
        api_aspect_ratio = config.aspect_ratio
        if config.auto_aspect_ratio and not api_aspect_ratio:
            api_aspect_ratio = _nearest_supported_aspect_ratio(
                donor_meta.width, donor_meta.height
            )
        recent_tag_base = _recent_tag(
            pid_label,
            model_tag_short,
            run_stamp,
            run_id,
        )
        _write_recent_json(
            out_dir,
            "run.json",
            {
                "run_id": run_id,
                "run_stamp_utc": run_stamp,
                "run_started_utc": run_started,
                "product": job.product_name,
                "prompt_id": prompt_id,
                "model": model_id,
                "model_tag": model_tag_short,
                "use_vertex": config.use_vertex,
                "api_aspect_ratio": api_aspect_ratio,
                "requested_results": requested_results,
                "results_per_donor": results,
                "model_mask_pass": model_mask_pass,
                "model_mask_force": force_model_mask,
                "segmentation_mask_pass": segmentation_mask_pass,
                "segmentation_mask_model": segmentation_mask_model
                or config.segmentation_mask_model,
                "post_mask_pass": post_mask_pass,
            },
        )
        mask_path = None
        if post_process and not no_mask:
            if model_mask_pass and not force_model_mask and existing_model_mask is not None:
                mask_path = existing_model_mask
            else:
                mask_path = find_mask_for_product(config.masks_dir, job.product_name)
                if mask_path is None:
                    mask_path = _select_recent_mask(config.masks_dir)

        print("[job]")
        print(f" product={job.product_name}")
        print(f" donor_original={job.donor_original}")
        print(f" donor_processed={job.donor_processed} ({donor_meta.width}x{donor_meta.height})")
        print(f" color_original={selected_color}")
        print(f" color_processed={selected_color_processed}")
        print(f" prompt_id={prompt_id}")
        print(f" model={model_id}")
        print(f" aspect_ratio={aspect_ratio}")
        if config.auto_aspect_ratio and not config.aspect_ratio and api_aspect_ratio:
            print(f" api_aspect_ratio={api_aspect_ratio} (auto)")
        elif api_aspect_ratio:
            print(f" api_aspect_ratio={api_aspect_ratio}")
        print(f" results={results}")
        if mask_path and not no_mask:
            print(f" mask={mask_path}")
        print("[/job]")

        mask_image = None
        if mask_path and not no_mask:
            mask_image = load_mask_image(
                mask_path, (donor_meta.width, donor_meta.height)
            )

        donor_model_bytes, donor_model_size = build_model_input(
            job.donor_processed,
            pad_aspect_ratio=api_aspect_ratio,
            background_color=chroma_key_color,
        )
        with Image.open(job.donor_processed) as donor_alpha_img:
            donor_alpha_img = ImageOps.exif_transpose(donor_alpha_img)
            if "A" in donor_alpha_img.getbands():
                donor_alpha = donor_alpha_img.getchannel("A")
            else:
                donor_alpha = Image.new("L", donor_alpha_img.size, 255)
        donor_restore_bytes = None
        if chroma_key_color and chroma_key_restore_donor:
            donor_restore_bytes, _ = build_model_input(
                job.donor_processed,
                pad_aspect_ratio=api_aspect_ratio,
                background_color=None,
            )
        color_bytes = selected_color_processed.read_bytes()
        mask_bytes = None
        if mask_image is not None:
            mask_bytes = build_square_mask_input(
                mask_image,
                (donor_meta.width, donor_meta.height),
                donor_model_size,
            )
        if segmentation_mask_pass:
            seg_threshold = max(0, min(255, segmentation_mask_threshold))
            requested_seg_model = (
                segmentation_mask_model
                or config.segmentation_mask_model
                or config.model
            )
            response_mime = (
                segmentation_response_mime_type
                if segmentation_response_mime_type is not None
                else config.segmentation_response_mime_type
            )
            base_mask_prompt = _load_mid_mask_prompt(
                config.prompts_dir,
                prompt_id,
                suffix="_seg",
            )
            if not base_mask_prompt:
                raise RuntimeError(
                    "Segmentation mask prompt is empty. Create "
                    "`prompts/mask_pass/mask_prompt_MID-<PID>_seg.md`."
                )
            mask_prompt = _format_mask_prompt(
                base_mask_prompt,
                mask_width=donor_meta.width,
                mask_height=donor_meta.height,
                donor_width=donor_meta.width,
                donor_height=donor_meta.height,
            )
            print(f" [mask] generating via segmentation ({requested_seg_model})")
            donor_mask_bytes = job.donor_processed.read_bytes()
            donor_mask_size = (donor_meta.width, donor_meta.height)
            _write_recent_file(
                out_dir,
                RECENT_SUBMITTED_MASK_DIR,
                _recent_filename("segmentation_donor", ".png", recent_tag_base),
                donor_mask_bytes,
            )
            seg_text, seg_image = generate_segmentation_mask_response(
                client=client,
                model_name=requested_seg_model,
                use_vertex=config.use_vertex,
                prompt=mask_prompt,
                donor_png=donor_mask_bytes,
                piping_ref_pngs=piping_ref_bytes,
                response_mime_type=response_mime or "application/json",
                max_output_tokens=segmentation_max_output_tokens
                or config.segmentation_max_output_tokens,
            )
            if seg_text:
                _write_recent_file(
                    out_dir,
                    RECENT_RETURNED_MASK_DIR,
                    _recent_filename("segmentation_response", ".json", recent_tag_base),
                    seg_text.encode("utf-8", errors="ignore"),
                )
                preview = " ".join(seg_text.strip().split())
                if preview:
                    print(f" [mask] segmentation response preview: {preview[:400]}")
            seg_mask_image = None
            if seg_text:
                try:
                    seg_mask_image = segmentation_json_to_mask_image(
                        seg_text,
                        donor_mask_size,
                        threshold=seg_threshold,
                    )
                except Exception as exc:
                    tail = seg_text.strip()[-1:] if seg_text else ""
                    if tail not in {"]", "}"}:
                        print(" [mask] segmentation response may be truncated; consider increasing max output tokens.")
                    print(f" [mask] segmentation JSON parse failed: {exc}")
            if seg_mask_image is None and seg_image is not None:
                with Image.open(io.BytesIO(seg_image)) as mask_img:
                    mask_img = ImageOps.exif_transpose(mask_img).convert("L")
                if mask_img.size != donor_mask_size:
                    mask_img = mask_img.resize(donor_mask_size, Image.NEAREST)
                seg_mask_image = mask_img
            if seg_mask_image is None:
                raise RuntimeError("Segmentation mask did not return usable output.")
            seg_mask_image = seg_mask_image.point(
                lambda p: 255 if p >= seg_threshold else 0
            )
            seg_mask_bytes = io.BytesIO()
            seg_mask_image.save(seg_mask_bytes, format="PNG")
            seg_mask_bytes_value = clip_mask_bytes_to_alpha(
                seg_mask_bytes.getvalue(),
                donor_alpha,
                donor_mask_size,
            )
            _write_recent_file(
                out_dir,
                RECENT_RETURNED_MASK_DIR,
                _recent_filename("segmentation_mask", ".png", recent_tag_base),
                seg_mask_bytes_value,
            )
            masks_dir = out_dir / "masks"
            ensure_dir(masks_dir)
            mask_out = masks_dir / f"{_short_product_tag(job.product_name)}_{pid_label}_mask.png"
            mask_out.write_bytes(seg_mask_bytes_value)
            print(f" [mask] saved -> {mask_out}")
            with Image.open(io.BytesIO(seg_mask_bytes_value)) as seg_mask_img:
                seg_mask_img = ImageOps.exif_transpose(seg_mask_img).convert("L")
                mask_hist = seg_mask_img.histogram()
                mask_total = sum(mask_hist)
                mask_cov = (
                    (mask_total - mask_hist[0]) / mask_total if mask_total else 0.0
                )
                print(f" [mask] segmentation mask coverage {mask_cov:.4f}")
                if mask_cov <= 0.0001:
                    raise RuntimeError("Segmentation mask coverage too low.")
                mask_image = seg_mask_img.copy()
            mask_bytes = build_square_mask_input(
                mask_image,
                (donor_meta.width, donor_meta.height),
                donor_model_size,
            )
        model_mask_bytes = None
        if model_mask_pass:
            if existing_model_mask is not None and not force_model_mask:
                mask_image = load_mask_image(
                    existing_model_mask, (donor_meta.width, donor_meta.height)
                )
                mask_bytes = build_square_mask_input(
                    mask_image,
                    (donor_meta.width, donor_meta.height),
                    donor_model_size,
                )
                model_mask_bytes = existing_model_mask.read_bytes()
                print(f" [mask] using existing model cushion mask -> {existing_model_mask}")
            else:
                if model_mask_prompt:
                    base_mask_prompt = model_mask_prompt
                else:
                    base_mask_prompt = _load_mid_mask_prompt(config.prompts_dir, prompt_id)
                strict_mask_size = True
                if strict_mask_size:
                    target_w, target_h = donor_meta.width, donor_meta.height
                else:
                    target_w, target_h = donor_model_size
                mask_prompt = _format_mask_prompt(
                    base_mask_prompt,
                    mask_width=target_w,
                    mask_height=target_h,
                    donor_width=donor_meta.width,
                    donor_height=donor_meta.height,
                )
                print(" [mask] generating via model")
                if strict_mask_size:
                    donor_mask_bytes, donor_mask_size = build_model_input(
                        job.donor_processed,
                        pad_aspect_ratio=None,
                        background_color=None,
                    )
                else:
                    donor_mask_bytes = donor_model_bytes
                    donor_mask_size = donor_model_size
                _write_recent_file(
                    out_dir,
                    RECENT_SUBMITTED_MASK_DIR,
                    _recent_filename("model_donor", ".png", recent_tag_base),
                    donor_mask_bytes,
                )
                if strict_mask_size:
                    mask_bytes_donor = None
                    last_mask_exc: Exception | None = None
                    for attempt in range(1, MODEL_MASK_MAX_ATTEMPTS + 1):
                        raw_mask = generate_piping_mask(
                            client=client,
                            model_name=config.model,
                            use_vertex=config.use_vertex,
                            prompt=mask_prompt,
                            donor_png=donor_mask_bytes,
                            piping_ref_pngs=piping_ref_bytes,
                            image_size=None,
                            aspect_ratio=None,
                        )
                        try:
                            mask_bytes_donor = normalize_mask_bytes_exact(
                                raw_mask,
                                donor_mask_size,
                                threshold=model_mask_threshold,
                            )
                            last_mask_exc = None
                            break
                        except ValueError as exc:
                            last_mask_exc = exc
                            with Image.open(io.BytesIO(raw_mask)) as raw_mask_img:
                                raw_mask_img = ImageOps.exif_transpose(raw_mask_img)
                                raw_w, raw_h = raw_mask_img.size
                            tgt_w, tgt_h = donor_mask_size
                            raw_ratio = raw_w / raw_h if raw_h else 0.0
                            tgt_ratio = tgt_w / tgt_h if tgt_h else 0.0
                            ratio_delta = abs(raw_ratio - tgt_ratio)
                            if ratio_delta <= 0.01 and attempt == MODEL_MASK_MAX_ATTEMPTS:
                                print(
                                    " [mask] size mismatch; resizing to donor "
                                    f"({raw_w}x{raw_h} -> {tgt_w}x{tgt_h})"
                                )
                                mask_bytes_donor = normalize_mask_bytes(
                                    raw_mask,
                                    donor_mask_size,
                                    threshold=model_mask_threshold,
                                )
                                last_mask_exc = None
                                break
                            print(
                                " [mask] size mismatch; retry "
                                f"{attempt}/{MODEL_MASK_MAX_ATTEMPTS} ({exc})"
                            )
                    if mask_bytes_donor is None:
                        raise RuntimeError(
                            "Model mask did not return donor-sized output."
                        ) from last_mask_exc
                    mask_bytes_donor = clip_mask_bytes_to_alpha(
                        mask_bytes_donor,
                        donor_alpha,
                        donor_mask_size,
                    )
                else:
                    raw_mask = generate_piping_mask(
                        client=client,
                        model_name=config.model,
                        use_vertex=config.use_vertex,
                        prompt=mask_prompt,
                        donor_png=donor_mask_bytes,
                        piping_ref_pngs=piping_ref_bytes,
                        image_size=config.image_size,
                        aspect_ratio=api_aspect_ratio,
                    )
                    mask_bytes_donor = normalize_mask_bytes(
                        raw_mask,
                        donor_mask_size,
                        threshold=model_mask_threshold,
                    )
                    donor_alpha_model = pad_alpha_to_size(donor_alpha, donor_mask_size)
                    mask_bytes_donor = clip_mask_bytes_to_alpha(
                        mask_bytes_donor,
                        donor_alpha_model,
                        donor_mask_size,
                    )
                _write_recent_file(
                    out_dir,
                    RECENT_RETURNED_MASK_DIR,
                    _recent_filename(
                        "model_cushion_mask",
                        ".png",
                        recent_tag_base,
                    ),
                    mask_bytes_donor,
                )
                if strict_mask_size:
                    out_dir = config.output_dir / job.product_name
                    ensure_dir(out_dir)
                    masks_dir = out_dir / "masks"
                    ensure_dir(masks_dir)
                    model_mask_out.write_bytes(mask_bytes_donor)
                    print(f" [mask] saved -> {model_mask_out}")
                    model_mask_bytes = mask_bytes_donor
                    mask_bytes = pad_mask_bytes_to_size(mask_bytes_donor, donor_model_size)
                    with Image.open(io.BytesIO(model_mask_bytes)) as model_mask_img:
                        model_mask_img = ImageOps.exif_transpose(model_mask_img).convert("L")
                        mask_hist = model_mask_img.histogram()
                        mask_total = sum(mask_hist)
                        mask_cov = (
                            (mask_total - mask_hist[0]) / mask_total if mask_total else 0.0
                        )
                    if mask_cov > 0.0005:
                        mask_image = model_mask_img.copy()
                        print(
                            f" [mask] model mask coverage {mask_cov:.4f}; using for post-process"
                        )
                    else:
                        print(
                            f" [mask] model mask coverage {mask_cov:.4f}; keeping existing mask"
                        )
                else:
                    model_mask_bytes = mask_bytes_donor
                    mask_bytes = mask_bytes_donor

        if mask_only:
            print("[mask-only] mask generated; skipping edit/post generation.")
            continue

        print("[submission]")
        print(
            " donor_file="
            f"{job.donor_processed.name} size={_file_size(job.donor_processed)} "
            f"model_input={donor_model_size[0]}x{donor_model_size[1]}"
        )
        with Image.open(job.donor_processed) as donor_log_img:
            donor_log_img = ImageOps.exif_transpose(donor_log_img)
            donor_mode = donor_log_img.mode
            donor_w, donor_h = donor_log_img.size
            donor_has_alpha = "A" in donor_log_img.getbands()
            donor_alpha_opaque = None
            if donor_has_alpha:
                donor_alpha_opaque = donor_log_img.getchannel("A").getextrema() == (
                    255,
                    255,
                )
        print(
            " donor_props="
            f"{donor_w}x{donor_h} mode={donor_mode} "
            f"alpha={donor_has_alpha} alpha_opaque={donor_alpha_opaque}"
        )
        print(f" color_file={selected_color_processed.name} size={_file_size(selected_color_processed)}")
        if chroma_key_color:
            print(
                " chroma_key="
                f"{chroma_key_label} tolerance={chroma_key_tolerance} "
                f"softness={chroma_key_softness} edge_clip={chroma_key_edge_clip} "
                f"restore_donor={chroma_key_restore_donor} "
                f"restore_thr={chroma_key_restore_threshold} "
                f"mask_donor={chroma_key_mask_donor} "
                f"mask_thr={chroma_key_mask_threshold} "
                f"mask_expand={chroma_key_mask_expand}"
            )
        print("[/submission]")

        ensure_dir(out_dir)

        mask_used = mask_image is not None
        flag_tag = _format_flag_tag_short(
            no_mask,
            preserve_luminance,
            mask_used,
            post_process,
            fit_only,
            bool(chroma_key_color),
            vertex_bg_remove,
        )
        product_tag = _short_product_tag(job.product_name)
        stem_prefix = f"{product_tag}_{pid_label}_{model_tag_short}{flag_tag}_R"
        existing_indices = _existing_result_indices(out_dir, stem_prefix)
        result_index = _first_free_index(existing_indices)
        generated = 0
        failures = 0
        raw_attempts = 0
        fit_attempts = 0
        scale_attempts = 0
        def _maybe_generate_post_piping_mask(
            final_bytes: bytes,
            recent_tag: str,
        ) -> Image.Image | None:
            if not post_mask_pass:
                return None
            base_prompt = post_mask_prompt
            if base_prompt is None:
                base_prompt = _load_post_mask_prompt(config.prompts_dir, prompt_id)
            if not base_prompt:
                raise RuntimeError(
                    "Post-mask pass enabled but prompt is empty. "
                    "Provide --post-mask-prompt or add a MID _piping prompt file "
                    "(fallback: MID _post)."
                )
            mask_prompt = _format_mask_prompt(
                base_prompt,
                mask_width=donor_meta.width,
                mask_height=donor_meta.height,
                donor_width=donor_meta.width,
                donor_height=donor_meta.height,
            )
            try:
                aligned_bytes, _ = fit_output_to_donor(
                    final_bytes, job.donor_processed
                )
                with Image.open(io.BytesIO(aligned_bytes)) as aligned_img:
                    aligned_img = ImageOps.exif_transpose(aligned_img).convert("RGB")
                    buffer = io.BytesIO()
                    aligned_img.save(buffer, format="PNG")
                    aligned_png = buffer.getvalue()
                raw_mask = generate_piping_mask(
                    client=client,
                    model_name=config.model,
                    use_vertex=config.use_vertex,
                    prompt=mask_prompt,
                    donor_png=aligned_png,
                    piping_ref_pngs=piping_ref_bytes,
                    image_size=None,
                    aspect_ratio=None,
                )
                threshold = (
                    post_mask_threshold
                    if post_mask_threshold is not None
                    else model_mask_threshold
                )
                try:
                    mask_bytes_local = normalize_mask_bytes_exact(
                        raw_mask,
                        (donor_meta.width, donor_meta.height),
                        threshold=threshold,
                    )
                except ValueError:
                    mask_bytes_local = normalize_mask_bytes(
                        raw_mask,
                        (donor_meta.width, donor_meta.height),
                        threshold=threshold,
                    )
                with Image.open(io.BytesIO(mask_bytes_local)) as mask_img:
                    mask_img = ImageOps.exif_transpose(mask_img).convert("L")
                if post_mask_shift_y:
                    shifted = Image.new("L", mask_img.size, 0)
                    if post_mask_shift_y > 0:
                        src = (0, 0, mask_img.size[0], mask_img.size[1] - post_mask_shift_y)
                        shifted.paste(mask_img.crop(src), (0, post_mask_shift_y))
                    else:
                        dy = abs(post_mask_shift_y)
                        src = (0, dy, mask_img.size[0], mask_img.size[1])
                        shifted.paste(mask_img.crop(src), (0, 0))
                    mask_img = shifted
                    print(f" [post-mask] applied shift_y={post_mask_shift_y}")
                if post_mask_expand:
                    kernel = max(1, int(post_mask_expand)) * 2 + 1
                    mask_img = mask_img.filter(ImageFilter.MaxFilter(kernel))
                    mask_img = mask_img.point(lambda p: 255 if p > 0 else 0)
                    print(f" [post-mask] expanded by {post_mask_expand}px")
                buffer = io.BytesIO()
                mask_img.save(buffer, format="PNG")
                mask_dest = _write_recent_file(
                    out_dir,
                    RECENT_RETURNED_MASK_DIR,
                    _recent_filename("piping_mask", ".png", recent_tag),
                    buffer.getvalue(),
                )
                print(" [post-mask] saved piping mask from output")
                cal_bytes = _build_mask_calibration_overlay(
                    job.donor_processed,
                    mask_dest,
                )
                cal_name = _recent_filename("cal_mask", ".png", recent_tag)
                _write_recent_file(
                    out_dir,
                    RECENT_RETURNED_POST_DIR,
                    cal_name,
                    cal_bytes,
                )
                print(" [post-mask] saved cal_mask overlay")
                return mask_img
            except Exception as exc:  # pylint: disable=broad-except
                raise RuntimeError(f"Post-mask pass failed: {exc}") from exc
        while generated < results:
            out_stem = f"{stem_prefix}{result_index}"
            out_path = out_dir / f"{out_stem}.png"
            if out_path.exists():
                print(f" ↷ Skip R-{result_index}: {out_path.name} already exists")
                result_index += 1
                continue
            recent_tag_result = _recent_tag(
                pid_label,
                model_tag_short,
                run_stamp,
                run_id,
                result_index,
            )
            print(
                f" → Request {generated + 1}/{results} for {job.product_name} (R-{result_index})"
            )
            try:
                _write_recent_file(
                    out_dir,
                    RECENT_SUBMITTED_EDIT_DIR,
                    _recent_filename("edit_donor", ".png", recent_tag_result),
                    donor_model_bytes,
                )
                if mask_bytes is not None:
                    _write_recent_file(
                        out_dir,
                        RECENT_SUBMITTED_EDIT_DIR,
                        _recent_filename("edit_mask", ".png", recent_tag_result),
                        mask_bytes,
                    )
                output_bytes = generate_piping_image(
                    client=client,
                    model_name=config.model,
                    use_vertex=config.use_vertex,
                    prompt=prompt_text,
                    donor_png=donor_model_bytes,
                    color_ref_png=color_bytes,
                    piping_ref_pngs=piping_ref_bytes,
                    mask_png=mask_bytes,
                    temperature=config.temperature,
                    image_size=config.image_size,
                    aspect_ratio=api_aspect_ratio,
                    response_mime_type=config.response_mime_type,
                    image_output_mime_type=config.image_output_mime_type,
                )
                if vertex_bg_remove and config.use_vertex:
                    output_bytes = remove_background_vertex(
                        output_bytes,
                        model_name=vertex_bg_model,
                        output_mime_type=vertex_bg_output_mime or config.image_output_mime_type,
                        prompt=vertex_bg_prompt,
                        project=config.project,
                        location=vertex_bg_location or config.location,
                        max_bytes=vertex_bg_max_bytes,
                        max_edge=vertex_bg_max_edge,
                    )
                    print(" [post] vertex background removal applied")
                _write_recent_file(
                    out_dir,
                    RECENT_RETURNED_RESULT_DIR,
                    _recent_filename(
                        "edit_raw",
                        _sniff_image_extension(output_bytes),
                        recent_tag_result,
                    ),
                    output_bytes,
                )
            except Exception as exc:  # pylint: disable=broad-except
                failures += 1
                message = str(exc)
                if "output_mime_type parameter is not supported" in message:
                    print(" ✖ Failed: output_mime_type not supported; disabling and retrying")
                    config.image_output_mime_type = None
                elif "response_mime_type" in message and "not supported" in message:
                    print(" ✖ Failed: response_mime_type not supported; disabling and retrying")
                    config.response_mime_type = None
                else:
                    print(f" ✖ Failed: {exc}")
                if failures >= MAX_FAILURES:
                    raise RuntimeError(
                        f"Generation failed after {MAX_FAILURES} attempts."
                    ) from exc
                continue

            if fit_only:
                output_bytes, stats = fit_output_to_donor(
                    output_bytes, job.donor_processed
                )
                if stats["resized"]:
                    target_w, target_h = stats["target_size"]
                    orig_w, orig_h = stats["original_size"]
                    print(
                        " [post] fit output to donor canvas "
                        f"{target_w}x{target_h} (original {orig_w}x{orig_h})"
                    )
                if stats["has_alpha"]:
                    print(" [post] applied donor alpha channel to preserve transparency")
                else:
                    print(" [post] donor alpha was fully opaque; saved output with alpha channel")
                print(" [post] fit-only; saved output")
                _maybe_generate_post_piping_mask(output_bytes, recent_tag_result)
                out_path.write_bytes(output_bytes)
                _write_recent_file(
                    out_dir,
                    RECENT_RETURNED_POST_DIR,
                    _recent_filename(
                        "edit_post",
                        _sniff_image_extension(output_bytes),
                        recent_tag_result,
                    ),
                    output_bytes,
                )
                print(f" ✔ Saved -> {out_path}")
                generated += 1
                result_index += 1
                continue

            if not post_process:
                out_w, out_h = _output_size_from_bytes(output_bytes)
                if enforce_raw_size and (out_w, out_h) != (
                    donor_meta.width,
                    donor_meta.height,
                ):
                    raw_attempts += 1
                    print(
                        " [post] raw output size mismatch "
                        f"{out_w}x{out_h} (donor {donor_meta.width}x{donor_meta.height}); "
                        f"retry {raw_attempts}/{RAW_OUTPUT_MAX_ATTEMPTS}"
                    )
                    if raw_attempts >= RAW_OUTPUT_MAX_ATTEMPTS:
                        raise RuntimeError(
                            "Raw output size did not match donor after "
                            f"{RAW_OUTPUT_MAX_ATTEMPTS} attempts."
                        )
                    continue
                if retry_until_fits:
                    if not _fits_full_frame(output_bytes, job.donor_processed):
                        fit_attempts += 1
                        print(
                            " [post] raw output framing mismatch; "
                            f"retry {fit_attempts}/{RAW_FIT_MAX_ATTEMPTS}"
                        )
                        if fit_attempts >= RAW_FIT_MAX_ATTEMPTS:
                            raise RuntimeError(
                                "Raw output did not fit full frame after "
                                f"{RAW_FIT_MAX_ATTEMPTS} attempts."
                            )
                        continue
                    fit_attempts = 0
                if retry_until_scale:
                    if not _scale_matches_donor(output_bytes, donor_bbox):
                        scale_attempts += 1
                        print(
                            " [post] raw output scale mismatch; "
                            f"retry {scale_attempts}/{RAW_SCALE_MAX_ATTEMPTS}"
                        )
                        if scale_attempts >= RAW_SCALE_MAX_ATTEMPTS:
                            raise RuntimeError(
                                "Raw output scale did not match donor after "
                                f"{RAW_SCALE_MAX_ATTEMPTS} attempts."
                            )
                        continue
                    scale_attempts = 0
                if scale_to_donor:
                    output_bytes, scale_stats = scale_output_to_donor_width(
                        output_bytes, job.donor_processed
                    )
                    if scale_stats.get("scaled"):
                        print(f" [post] scaled output to donor width (scale={scale_stats['scale']:.4f})")
                if chroma_key_color:
                    mask_bytes = None
                    if chroma_key_mask_donor and donor_restore_bytes:
                        mask_bytes = donor_restore_bytes
                    output_bytes = apply_chroma_key(
                        output_bytes,
                        chroma_key_color,
                        tolerance=chroma_key_tolerance,
                        softness=chroma_key_softness,
                        edge_clip=chroma_key_edge_clip,
                        mask_bytes=mask_bytes,
                        mask_threshold=chroma_key_mask_threshold,
                        mask_expand=chroma_key_mask_expand,
                    )
                    print(
                        " [post] chroma key applied "
                        f"({chroma_key_label}, tol={chroma_key_tolerance}, "
                        f"soft={chroma_key_softness}, clip={chroma_key_edge_clip})"
                    )
                    if chroma_key_restore_donor and donor_restore_bytes:
                        output_bytes = apply_donor_alpha(output_bytes, donor_restore_bytes)
                        output_bytes = restore_rgb_under_alpha(
                            output_bytes,
                            donor_restore_bytes,
                            alpha_threshold=chroma_key_restore_threshold,
                        )
                        print(
                            " [post] donor alpha + RGB restored "
                            f"(thr={chroma_key_restore_threshold})"
                        )
                if model_mask_bytes is not None:
                    aligned_bytes, _ = fit_output_to_donor(
                        output_bytes, job.donor_processed
                    )
                    donor_mask_bytes = align_mask_bytes(
                        model_mask_bytes,
                        (donor_meta.width, donor_meta.height),
                        threshold=model_mask_threshold,
                    )
                    output_bytes = composite_with_mask(
                        aligned_bytes,
                        job.donor_processed,
                        donor_mask_bytes,
                    )
                    print(" [post] composited piping onto donor via model mask")
                raw_attempts = 0
                preview_bytes = None
                if preview_bg_color:
                    preview_bytes = composite_over_background(
                        output_bytes,
                        preview_bg_color,
                    )
                ext = _sniff_image_extension(output_bytes)
                if ext != ".png":
                    out_path = out_dir / f"{out_stem}{ext}"
                _maybe_generate_post_piping_mask(output_bytes, recent_tag_result)
                out_path.write_bytes(output_bytes)
                if enforce_raw_size:
                    print(" [post] disabled; saved raw model output (size matches donor)")
                else:
                    print(
                        " [post] disabled; saved raw model output "
                        f"({out_w}x{out_h})"
                    )
                print(f" ✔ Saved -> {out_path}")
                if config.enforce_adobe_rgb:
                    if ensure_adobe_rgb(out_path, config.adobe_rgb_icc):
                        print(" ✔ Converted to Adobe RGB (1998)")
                    else:
                        print(" ⚠ Adobe RGB conversion skipped/failed")
                if config.enforce_dpi:
                    changed, found = ensure_dpi(out_path, config.target_dpi)
                    if changed:
                        if found:
                            print(
                                f" ⚠ DPI was {found[0]:.1f}x{found[1]:.1f}; "
                                f"set to {config.target_dpi} DPI"
                            )
                        else:
                            print(f" ⚠ DPI missing; set to {config.target_dpi} DPI")
                    else:
                        if found:
                            print(
                                f" ✔ DPI already {found[0]:.1f}x{found[1]:.1f}"
                            )
                if preview_bytes is not None:
                    preview_path = out_dir / f"{out_stem}_preview.png"
                    preview_path.write_bytes(preview_bytes)
                    print(f" ✔ Saved preview -> {preview_path}")
                    if config.enforce_adobe_rgb:
                        if ensure_adobe_rgb(preview_path, config.adobe_rgb_icc):
                            print(" ✔ Converted preview to Adobe RGB (1998)")
                        else:
                            print(" ⚠ Adobe RGB conversion skipped/failed (preview)")
                    if config.enforce_dpi:
                        changed, found = ensure_dpi(preview_path, config.target_dpi)
                        if changed:
                            if found:
                                print(
                                    f" ⚠ Preview DPI was {found[0]:.1f}x{found[1]:.1f}; "
                                    f"set to {config.target_dpi} DPI"
                                )
                            else:
                                print(f" ⚠ Preview DPI missing; set to {config.target_dpi} DPI")
                        else:
                            if found:
                                print(
                                    f" ✔ Preview DPI already {found[0]:.1f}x{found[1]:.1f}"
                                )
                generated += 1
                result_index += 1
                continue

            if post_process:
                post_mask_image = _maybe_generate_post_piping_mask(
                    output_bytes, recent_tag_result
                )
                if post_mask_image is not None:
                    mask_image = post_mask_image
                    output_bytes, _ = fit_output_to_donor(
                        output_bytes, job.donor_processed
                    )
                if no_mask:
                    raise RuntimeError(
                        "Post-processing requires a mask to preserve donor geometry. "
                        "Disable --no-mask or use --no-post/--bare for raw output."
                    )
                if mask_image is None:
                    raise RuntimeError(
                        "Post-processing requires a mask image to composite pipes onto the donor. "
                        "Provide a mask file or enable mask generation."
                    )
                output_bytes, stats = composite_output_with_donor(
                    output_bytes,
                    job.donor_processed,
                    apply_mask=True,
                    preserve_luminance=preserve_luminance,
                    mask_image=mask_image,
                )
                if stats["resized"]:
                    target_w, target_h = stats["target_size"]
                    orig_w, orig_h = stats["original_size"]
                    print(
                        " [post] fit output to donor canvas "
                        f"{target_w}x{target_h} (original {orig_w}x{orig_h})"
                    )
                if stats.get("preserve_luminance"):
                    print(" [post] preserved donor luminance")
                shadow_band = stats.get("shadow_band")
                if shadow_band:
                    print(
                        " [post] shadow band copied from donor rows "
                        f"{shadow_band[0]}-{shadow_band[1]}"
                    )
                if stats["has_alpha"]:
                    print(" [post] applied donor alpha channel to preserve transparency")
                else:
                    print(" [post] donor alpha was fully opaque; saved output with alpha channel")
                if stats.get("mask_disabled"):
                    print(" [post] mask disabled; kept full output with donor alpha")
                else:
                    print(
                        " [post] mask coverage "
                        f"{stats['mask_coverage']:.4f} relaxed={stats['mask_relaxed']} "
                        f"tightened={stats['mask_tightened']} clipped={stats['mask_clipped']} "
                        f"edge_guard={stats['edge_guard_px']}px"
                    )

            preview_bytes = None
            if preview_bg_color:
                preview_bytes = composite_over_background(
                    output_bytes,
                    preview_bg_color,
                )
            out_path.write_bytes(output_bytes)
            print(f" ✔ Saved -> {out_path}")
            if config.enforce_adobe_rgb:
                if ensure_adobe_rgb(out_path, config.adobe_rgb_icc):
                    print(" ✔ Converted to Adobe RGB (1998)")
                else:
                    print(" ⚠ Adobe RGB conversion skipped/failed")
            if config.enforce_dpi:
                changed, found = ensure_dpi(out_path, config.target_dpi)
                if changed:
                    if found:
                        print(
                            f" ⚠ DPI was {found[0]:.1f}x{found[1]:.1f}; "
                            f"set to {config.target_dpi} DPI"
                        )
                    else:
                        print(f" ⚠ DPI missing; set to {config.target_dpi} DPI")
                else:
                    if found:
                        print(f" ✔ DPI already {found[0]:.1f}x{found[1]:.1f}")
            if preview_bytes is not None:
                preview_path = out_dir / f"{out_stem}_preview.png"
                preview_path.write_bytes(preview_bytes)
                print(f" ✔ Saved preview -> {preview_path}")
                if config.enforce_adobe_rgb:
                    if ensure_adobe_rgb(preview_path, config.adobe_rgb_icc):
                        print(" ✔ Converted preview to Adobe RGB (1998)")
                    else:
                        print(" ⚠ Adobe RGB conversion skipped/failed (preview)")
                if config.enforce_dpi:
                    changed, found = ensure_dpi(preview_path, config.target_dpi)
                    if changed:
                        if found:
                            print(
                                f" ⚠ Preview DPI was {found[0]:.1f}x{found[1]:.1f}; "
                                f"set to {config.target_dpi} DPI"
                            )
                        else:
                            print(f" ⚠ Preview DPI missing; set to {config.target_dpi} DPI")
                    else:
                        if found:
                            print(
                                f" ✔ Preview DPI already {found[0]:.1f}x{found[1]:.1f}"
                            )
            post_bytes = out_path.read_bytes()
            _write_recent_file(
                out_dir,
                RECENT_RETURNED_POST_DIR,
                _recent_filename(
                    "edit_post",
                    out_path.suffix or ".png",
                    recent_tag_result,
                ),
                post_bytes,
            )
            generated += 1
            result_index += 1

    return jobs
