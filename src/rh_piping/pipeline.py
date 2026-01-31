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

from PIL import Image, ImageOps

from rh_piping.config import AppConfig
from rh_piping.genai_client import (
    create_client,
    generate_piping_image,
    generate_piping_mask,
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
    normalize_mask_bytes,
    normalize_mask_bytes_exact,
    pad_alpha_to_size,
    pad_mask_bytes_to_size,
    scale_output_to_donor_width,
    load_mask_image,
    parse_hex_color,
    apply_donor_alpha,
    restore_rgb_under_alpha,
    build_output_diff_mask,
)
from rh_piping.io import ensure_dir, list_images
from rh_piping.masks import find_mask_for_product, generate_mask_from_space
from rh_piping.prompts import prompt_id_from_path

ORIGINAL_ROOT = "original"
DONOR_DIRNAME = "donor_image"
COLOR_REF_DIRNAME = "color_reference"

PROCESSED_DONOR_DIRNAME = "donor_image"
PROCESSED_COLOR_DIRNAME = "color_reference"

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
    model_mask_prompt: str | None = None,
    model_mask_threshold: int = 200,
    mask_from_output: bool = False,
    mask_from_output_threshold: int = 10,
    generate_mask: bool = False,
    regenerate_mask: bool = False,
    sam2_model: str | None = None,
    sam2_space: str | None = None,
    sam2_mask_threshold: int | None = None,
) -> list[PipingJob]:
    ensure_dir(config.output_dir)
    run_id = uuid.uuid4().hex
    run_started = _utc_iso_now()
    run_stamp = _utc_run_stamp()

    if fit_only:
        post_process = False
        no_mask = True
        preserve_luminance = False
        generate_mask = False
        regenerate_mask = False
    elif not post_process:
        no_mask = True
        preserve_luminance = False
        generate_mask = False
        regenerate_mask = False
    if chroma_key_hex:
        if post_process:
            print("[info] chroma key enabled; disabling composite post-processing.")
        post_process = False
        no_mask = True
        preserve_luminance = False
        generate_mask = False
        regenerate_mask = False
        if vertex_bg_remove:
            print("[info] chroma key enabled; skipping Vertex background removal.")
        vertex_bg_remove = False
    if mask_from_output and model_mask_pass:
        print("[info] mask-from-output enabled; disabling model mask pass.")
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

    color_dir = config.assets_dir / ORIGINAL_ROOT / COLOR_REF_DIRNAME
    color_refs = list_images(color_dir)
    if not color_refs:
        raise FileNotFoundError(f"No color references found in {color_dir}")

    prompt_id = prompt_id_from_path(prompt_path)
    pid_label = _pid_label(prompt_id)
    model_id = normalize_model_id(config.model, config.use_vertex)
    model_tag = _sanitize_model_tag(model_id)
    model_tag_short = _short_model_tag(model_tag)
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

    for job in jobs:
        donor_meta = convert_to_4k_png(job.donor_original, job.donor_processed)
        print(
            f"[convert] donor {job.donor_original.name} -> {job.donor_processed.name} "
            f"({donor_meta.width}x{donor_meta.height}, {donor_meta.mode})"
        )
        out_dir = config.output_dir / job.product_name
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
                "sam2_space": sam2_space or config.sam2_space,
                "sam2_model": sam2_model or config.sam2_model,
            },
        )
        mask_path = None
        if post_process and not no_mask:
            mask_path = find_mask_for_product(config.masks_dir, job.product_name)
            if regenerate_mask:
                mask_path = None
            if generate_mask and mask_path is None:
                requested_model = sam2_model or config.sam2_model
                requested_space = sam2_space or config.sam2_space
                requested_threshold = (
                    sam2_mask_threshold
                    if sam2_mask_threshold is not None
                    else config.sam2_mask_threshold
                )
                mask_output = config.masks_dir / f"{job.product_name}.png"
                print(f"[mask] generating via SAM2 ({requested_model}) -> {mask_output}")
                _write_recent_file(
                    out_dir,
                    RECENT_SUBMITTED_MASK_DIR,
                    _recent_filename("sam2_donor", ".png", recent_tag_base),
                    job.donor_processed.read_bytes(),
                )
                mask_path = generate_mask_from_space(
                    image_path=job.donor_processed,
                    output_path=mask_output,
                    space=requested_space,
                    model=requested_model,
                    threshold=requested_threshold,
                )
                if mask_path.exists():
                    _write_recent_file(
                        out_dir,
                        RECENT_RETURNED_MASK_DIR,
                        _recent_filename("sam2_mask", ".png", recent_tag_base),
                        mask_path.read_bytes(),
                    )

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
            mask_image = load_mask_image(mask_path, (donor_meta.width, donor_meta.height))

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
        model_mask_bytes = None
        if model_mask_pass:
            base_mask_prompt = model_mask_prompt or (
                "Create a binary mask image: piping = white, everything else = black."
            )
            strict_mask_size = not mask_from_output
            if strict_mask_size:
                target_w, target_h = donor_meta.width, donor_meta.height
            else:
                target_w, target_h = donor_model_size
            mask_prompt = (
                f"{base_mask_prompt}\n"
                "Image Dimensions: The mask must be exactly "
                f"{target_w}x{target_h} pixels.\n"
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
                    "model_mask",
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
                mask_out = masks_dir / f"{_short_product_tag(job.product_name)}_{pid_label}_mask.png"
                mask_out.write_bytes(mask_bytes_donor)
                print(f" [mask] saved -> {mask_out}")
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
                if mask_from_output:
                    with Image.open(job.donor_processed) as donor_img:
                        donor_img = ImageOps.exif_transpose(donor_img)
                        donor_rgb = donor_img.convert("RGB")
                        alpha_channel = (
                            donor_img.getchannel("A") if "A" in donor_img.getbands() else None
                        )
                    mask_image = build_output_diff_mask(
                        donor_rgb,
                        output_bytes,
                        threshold=mask_from_output_threshold,
                        alpha=alpha_channel,
                    )
                output_bytes, stats = composite_output_with_donor(
                    output_bytes,
                    job.donor_processed,
                    apply_mask=not no_mask and mask_image is not None,
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
