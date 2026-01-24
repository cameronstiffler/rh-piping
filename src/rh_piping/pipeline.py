"""Job discovery and orchestration for piping edits."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from rh_piping.config import AppConfig
from rh_piping.genai_client import create_client, generate_piping_image, normalize_model_id
from rh_piping.images import (
    aspect_ratio_for_size,
    build_square_model_input,
    composite_output_with_donor,
    convert_to_4k_png,
)
from rh_piping.io import ensure_dir, list_images
from rh_piping.prompts import prompt_id_from_path

ORIGINAL_ROOT = "original"
DONOR_DIRNAME = "donor_image"
COLOR_REF_DIRNAME = "color_reference"

PROCESSED_DONOR_DIRNAME = "donor_image"
PROCESSED_COLOR_DIRNAME = "color_reference"

MAX_RESULTS = 100
PROMPT_PID_VALUE = re.compile(r"PID(?P<value>-?\d+)")


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


def _normalize_results(requested: int) -> int:
    if requested == 3:
        return 5
    return requested


def _existing_result_indices(
    out_dir: Path,
    product_name: str,
    pid_label: str,
    model_tag: str,
    flag_tag: str,
) -> set[int]:
    if not out_dir.exists():
        return set()
    prefix = f"{product_name}_{pid_label}_MOD-{model_tag}{flag_tag}_R-"
    pattern = re.compile(rf"^{re.escape(prefix)}(?P<index>\d+)\.png$")
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


def _format_flag_tag(no_mask: bool, preserve_luminance: bool) -> str:
    flags: list[str] = []
    if no_mask:
        flags.append("nomask")
    if preserve_luminance:
        flags.append("lum")
    if not flags:
        return ""
    return f"_FX-{'-'.join(flags)}"


def _match_product(donor: Path, product_name: str) -> bool:
    return donor.stem == product_name or donor.name == product_name


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
) -> list[PipingJob]:
    ensure_dir(config.output_dir)

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
        aspect_ratio = aspect_ratio_for_size(donor_meta.width, donor_meta.height)

        print("[job]")
        print(f" product={job.product_name}")
        print(f" donor_original={job.donor_original}")
        print(f" donor_processed={job.donor_processed} ({donor_meta.width}x{donor_meta.height})")
        print(f" color_original={selected_color}")
        print(f" color_processed={selected_color_processed}")
        print(f" prompt_id={prompt_id}")
        print(f" model={model_id}")
        print(f" aspect_ratio={aspect_ratio}")
        print(f" results={results}")
        print("[/job]")

        donor_model_bytes, _, donor_model_size = build_square_model_input(
            job.donor_processed
        )
        color_bytes = selected_color_processed.read_bytes()

        print("[submission]")
        print(
            " donor_file="
            f"{job.donor_processed.name} size={_file_size(job.donor_processed)} "
            f"model_input={donor_model_size[0]}x{donor_model_size[1]}"
        )
        print(f" color_file={selected_color_processed.name} size={_file_size(selected_color_processed)}")
        print("[/submission]")

        out_dir = config.output_dir / job.product_name
        ensure_dir(out_dir)

        flag_tag = _format_flag_tag(no_mask, preserve_luminance)
        existing_indices = _existing_result_indices(
            out_dir,
            job.product_name,
            pid_label,
            model_tag,
            flag_tag,
        )
        result_index = _first_free_index(existing_indices)
        generated = 0
        while generated < results:
            out_name = (
                f"{job.product_name}_{pid_label}_MOD-{model_tag}{flag_tag}_R-{result_index}.png"
            )
            out_path = out_dir / out_name
            if out_path.exists():
                print(f" ↷ Skip R-{result_index}: {out_path.name} already exists")
                result_index += 1
                continue
            print(
                f" → Request {generated + 1}/{results} for {job.product_name} (R-{result_index})"
            )
            try:
                output_bytes = generate_piping_image(
                    client=client,
                    model_name=config.model,
                    use_vertex=config.use_vertex,
                    prompt=prompt_text,
                    donor_png=donor_model_bytes,
                    color_ref_png=color_bytes,
                    temperature=config.temperature,
                )
            except Exception as exc:  # pylint: disable=broad-except
                print(f" ✖ Failed: {exc}")
                continue

            output_bytes, stats = composite_output_with_donor(
                output_bytes,
                job.donor_processed,
                apply_mask=not no_mask,
                preserve_luminance=preserve_luminance,
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

            out_path.write_bytes(output_bytes)
            print(f" ✔ Saved -> {out_path}")
            generated += 1
            result_index += 1

    return jobs
