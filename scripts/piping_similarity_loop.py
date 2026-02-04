#!/usr/bin/env python3
"""Iterate pipeline runs and compare piping mask similarity to a reference image."""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import io

from PIL import Image, ImageOps, ImageChops

from rh_piping.config import load_config
from rh_piping.genai_client import create_client, generate_piping_mask
from rh_piping.images import normalize_mask_bytes, normalize_mask_bytes_exact
from rh_piping.pipeline import _format_mask_prompt, _load_post_mask_prompt


@dataclass
class IterationResult:
    iteration: int
    output_path: Path
    similarity: float
    best_shift_y: int
    output_size: tuple[int, int]
    reference_size: tuple[int, int]
    resized: bool
    threshold: int
    shift_min: int
    shift_max: int
    shift_step: int
    command: str
    post_mask_expand: int
    post_mask_threshold: int
    error: str | None = None


def _load_prompt(prompts_dir: Path, pid: int) -> str:
    prompt = _load_post_mask_prompt(prompts_dir, f"PID-{pid}")
    if not prompt:
        raise FileNotFoundError(
            f"No piping mask prompt found for PID-{pid}. Expected "
            f"mask_prompt_MID-{pid}_piping.md (or _post fallback)."
        )
    return prompt


def _list_piping_refs(assets_dir: Path, max_refs: int | None) -> list[bytes]:
    ref_dir = assets_dir / "processed" / "piping_ref_highlighted"
    if not ref_dir.exists():
        return []
    refs = sorted([p for p in ref_dir.iterdir() if p.is_file()])
    if max_refs is not None and max_refs > 0:
        refs = refs[:max_refs]
    return [p.read_bytes() for p in refs]


def _mask_from_model(
    client,
    model_name: str,
    use_vertex: bool,
    prompt: str,
    image_bytes: bytes,
    size: tuple[int, int],
    threshold: int,
    piping_refs: list[bytes],
) -> Image.Image:
    raw_mask = generate_piping_mask(
        client=client,
        model_name=model_name,
        use_vertex=use_vertex,
        prompt=prompt,
        donor_png=image_bytes,
        piping_ref_pngs=piping_refs,
        image_size=None,
        aspect_ratio=None,
    )
    try:
        mask_bytes = normalize_mask_bytes_exact(raw_mask, size, threshold=threshold)
    except ValueError:
        mask_bytes = normalize_mask_bytes(raw_mask, size, threshold=threshold)
    with Image.open(io.BytesIO(mask_bytes)) as mask_img:
        mask_img = ImageOps.exif_transpose(mask_img).convert("L")
    return mask_img


def _binary_mask(mask: Image.Image) -> Image.Image:
    return mask.point(lambda p: 255 if p > 0 else 0)


def _shift_mask(mask: Image.Image, dy: int) -> Image.Image:
    if dy == 0:
        return mask
    width, height = mask.size
    shifted = Image.new("L", (width, height), 0)
    if dy > 0:
        src = (0, 0, width, height - dy)
        shifted.paste(mask.crop(src), (0, dy))
    else:
        dy_abs = abs(dy)
        src = (0, dy_abs, width, height)
        shifted.paste(mask.crop(src), (0, 0))
    return shifted


def _mask_iou(mask_a: Image.Image, mask_b: Image.Image) -> float:
    a = mask_a.convert("L").point(lambda p: 255 if p > 0 else 0)
    b = mask_b.convert("L").point(lambda p: 255 if p > 0 else 0)
    inter = ImageChops.multiply(a, b).point(lambda p: 255 if p > 0 else 0)
    union = ImageChops.lighter(a, b)
    inter_hist = inter.histogram()
    union_hist = union.histogram()
    inter_count = inter_hist[255] if len(inter_hist) > 255 else 0
    union_count = union_hist[255] if len(union_hist) > 255 else 0
    if union_count == 0:
        return 0.0
    return inter_count / union_count


def _find_latest_output(output_dir: Path, product: str, pid: int) -> Path:
    pattern = f"*_PID-{pid}_*_R*.png"
    search_dir = output_dir / product
    candidates = list(search_dir.glob(pattern))
    if not candidates:
        raise FileNotFoundError(f"No outputs found under {search_dir} for PID-{pid}.")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _run_pipeline(
    product: str,
    pid: int,
    post_shift: int | None,
    post_expand: int,
    post_threshold: int | None,
) -> str:
    cmd = [
        "python3",
        "-m",
        "rh_piping",
        "--pid",
        str(pid),
        "--product",
        product,
    ]
    if post_shift is not None:
        cmd.extend(["--post-mask-shift-y", str(post_shift)])
    if post_expand:
        cmd.extend(["--post-mask-expand", str(post_expand)])
    if post_threshold is not None:
        cmd.extend(["--post-mask-threshold", str(post_threshold)])
    subprocess.run(cmd, check=True)
    return " ".join(cmd)


def _compare_masks(
    ref_mask: Image.Image,
    out_mask: Image.Image,
    shift_min: int,
    shift_max: int,
    shift_step: int,
) -> tuple[float, int]:
    best_iou = -1.0
    best_shift = 0
    for dy in range(shift_min, shift_max + 1, shift_step):
        shifted = _shift_mask(out_mask, dy)
        iou = _mask_iou(ref_mask, shifted)
        if iou > best_iou:
            best_iou = iou
            best_shift = dy
    return best_iou, best_shift


def _append_log(path: Path, result: IterationResult, target: float) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    status = "PASS" if result.similarity >= target else "FAIL"
    size_match = result.output_size == result.reference_size
    size_delta = (
        result.output_size[0] - result.reference_size[0],
        result.output_size[1] - result.reference_size[1],
    )
    lines = [
        "---",
        f"## {timestamp}",
        f"- Iteration: {result.iteration}",
        f"- Command: `{result.command}`",
        f"- Output: `{result.output_path}`",
        f"- Similarity (IoU): {result.similarity:.4f} ({status}, target {target:.2f})",
        f"- Best shift_y: {result.best_shift_y}px",
        f"- Post-mask expand: {result.post_mask_expand}px",
        f"- Post-mask threshold: {result.post_mask_threshold}",
        f"- Threshold: {result.threshold}",
        f"- Shift search: {result.shift_min}..{result.shift_max} step {result.shift_step}",
        f"- Reference size: {result.reference_size[0]}x{result.reference_size[1]}",
        f"- Output size: {result.output_size[0]}x{result.output_size[1]} (resized={result.resized})",
        f"- Size match: {size_match} (delta {size_delta[0]}x{size_delta[1]})",
    ]
    if result.error:
        lines.append(f"- Error: {result.error}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
        handle.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Iterate pipeline runs and compare piping positions.")
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--product", type=str, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--max-iters", type=int, default=3)
    parser.add_argument("--iou-target", type=float, default=0.80)
    parser.add_argument("--threshold", type=int, default=200)
    parser.add_argument("--shift-min", type=int, default=-40)
    parser.add_argument("--shift-max", type=int, default=40)
    parser.add_argument("--shift-step", type=int, default=2)
    parser.add_argument("--post-mask-expand", type=int, default=0)
    parser.add_argument("--initial-shift", type=int, default=0)
    parser.add_argument("--post-mask-threshold", type=int, default=None)
    parser.add_argument("--log", type=Path, default=Path("documentation/piping_similarity_log.md"))
    parser.add_argument(
        "--debug-dir",
        type=Path,
        default=None,
        help="Optional directory to save reference/output piping masks.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    config = load_config()
    client = create_client(config)

    reference_path = args.reference
    if not reference_path.exists():
        raise SystemExit(f"Reference image not found: {reference_path}")

    ref_img = ImageOps.exif_transpose(Image.open(reference_path)).convert("RGB")
    ref_size = ref_img.size

    prompt_template = _load_prompt(config.prompts_dir, args.pid)
    prompt = _format_mask_prompt(
        prompt_template,
        mask_width=ref_size[0],
        mask_height=ref_size[1],
        donor_width=ref_size[0],
        donor_height=ref_size[1],
    )
    piping_refs = _list_piping_refs(config.assets_dir, config.piping_ref_max)

    ref_mask = _mask_from_model(
        client,
        config.model,
        config.use_vertex,
        prompt,
        reference_path.read_bytes(),
        ref_size,
        args.threshold,
        piping_refs,
    )
    ref_mask = _binary_mask(ref_mask)
    if args.debug_dir is not None:
        args.debug_dir.mkdir(parents=True, exist_ok=True)
        ref_mask.save(args.debug_dir / "reference_piping_mask.png")

    best_shift: int | None = args.initial_shift if args.initial_shift else None
    for iteration in range(1, args.max_iters + 1):
        try:
            command = _run_pipeline(
                args.product,
                args.pid,
                best_shift,
                args.post_mask_expand,
                args.post_mask_threshold,
            )
        except subprocess.CalledProcessError as exc:
            result = IterationResult(
                iteration=iteration,
                output_path=Path(""),
                similarity=0.0,
                best_shift_y=best_shift or 0,
                output_size=(0, 0),
                reference_size=ref_size,
                resized=False,
                threshold=args.threshold,
                shift_min=args.shift_min,
                shift_max=args.shift_max,
                shift_step=args.shift_step,
                command=" ".join(exc.cmd) if exc.cmd else "python3 -m rh_piping",
                post_mask_expand=args.post_mask_expand,
                post_mask_threshold=args.post_mask_threshold
                if args.post_mask_threshold is not None
                else args.threshold,
                error=str(exc),
            )
            _append_log(args.log, result, args.iou_target)
            break
        output_path = _find_latest_output(config.output_dir, args.product, args.pid)
        out_img = ImageOps.exif_transpose(Image.open(output_path)).convert("RGB")
        resized = False
        if out_img.size != ref_size:
            out_img = ImageOps.fit(out_img, ref_size, Image.LANCZOS, centering=(0.5, 0.5))
            resized = True
        output_buffer = io.BytesIO()
        out_img.save(output_buffer, format="PNG")
        output_bytes = output_buffer.getvalue()

        out_prompt = _format_mask_prompt(
            prompt_template,
            mask_width=ref_size[0],
            mask_height=ref_size[1],
            donor_width=ref_size[0],
            donor_height=ref_size[1],
        )
        out_mask = _mask_from_model(
            client,
            config.model,
            config.use_vertex,
            out_prompt,
            output_bytes,
            ref_size,
            args.threshold,
            piping_refs,
        )
        out_mask = _binary_mask(out_mask)
        if args.debug_dir is not None:
            out_mask.save(args.debug_dir / f"output_piping_mask_iter{iteration}.png")

        similarity, best_shift = _compare_masks(
            ref_mask,
            out_mask,
            args.shift_min,
            args.shift_max,
            args.shift_step,
        )

        result = IterationResult(
            iteration=iteration,
            output_path=output_path,
            similarity=similarity,
            best_shift_y=best_shift,
            output_size=out_img.size,
            reference_size=ref_size,
            resized=resized,
            threshold=args.threshold,
            shift_min=args.shift_min,
            shift_max=args.shift_max,
            shift_step=args.shift_step,
            command=command,
            post_mask_expand=args.post_mask_expand,
            post_mask_threshold=args.post_mask_threshold
            if args.post_mask_threshold is not None
            else args.threshold,
        )
        _append_log(args.log, result, args.iou_target)

        if similarity >= args.iou_target:
            break


if __name__ == "__main__":
    main()
