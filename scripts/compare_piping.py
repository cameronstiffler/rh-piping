#!/usr/bin/env python3
"""Compare piping positions between a reference image and a result image."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps

DEFAULT_SHIFT_MIN = -120
DEFAULT_SHIFT_MAX = 120
DEFAULT_SHIFT_STEP = 4
DEFAULT_EDGE_PERCENTILE = 0.90
DEFAULT_FABRIC_THRESHOLD = 150
DEFAULT_DILATE = 1
DEFAULT_IOU_TARGET = 0.80
DEFAULT_BOUNDARY_WIDTH = 6


@dataclass
class CompareResult:
    output_path: Path
    reference_path: Path
    iou: float
    best_shift_y: int
    output_size: tuple[int, int]
    reference_size: tuple[int, int]
    resized: bool
    edge_percentile: float
    fabric_threshold: int
    dilate: int
    boundary_width: int
    cushion_mask: Path | None
    shift_min: int
    shift_max: int
    shift_step: int


def _load_image(path: Path) -> Image.Image:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
    return img


def _resize_to(img: Image.Image, target_size: tuple[int, int]) -> tuple[Image.Image, bool]:
    if img.size == target_size:
        return img, False
    resized = ImageOps.fit(img, target_size, Image.LANCZOS, centering=(0.5, 0.5))
    return resized, True


def _threshold_from_percentile(img: Image.Image, percentile: float) -> int:
    hist = img.histogram()
    total = sum(hist)
    if total == 0:
        return 255
    target = total * percentile
    cumulative = 0
    for value, count in enumerate(hist):
        cumulative += count
        if cumulative >= target:
            return value
    return 255


def _binary_mask(img: Image.Image) -> Image.Image:
    return img.point(lambda p: 255 if p > 0 else 0)


def _boundary_from_mask(mask: Image.Image, width: int) -> Image.Image:
    if width <= 0:
        return _binary_mask(mask)
    kernel = max(1, int(width)) * 2 + 1
    dilated = mask.filter(ImageFilter.MaxFilter(kernel))
    eroded = mask.filter(ImageFilter.MinFilter(kernel))
    boundary = ImageChops.difference(dilated, eroded)
    return _binary_mask(boundary)


def _extract_pipe_edges(
    img: Image.Image,
    edge_percentile: float,
    fabric_threshold: int,
    dilate: int,
    boundary_mask: Image.Image | None,
) -> Image.Image:
    gray = img.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edges = ImageOps.autocontrast(edges)

    if fabric_threshold is not None:
        fabric_mask = gray.point(lambda p: 255 if p >= fabric_threshold else 0)
        edges = ImageChops.multiply(edges, fabric_mask)

    if boundary_mask is not None:
        edges = ImageChops.multiply(edges, boundary_mask)

    threshold = _threshold_from_percentile(edges, edge_percentile)
    mask = edges.point(lambda p: 255 if p >= threshold else 0)

    if dilate and dilate > 0:
        kernel = max(1, int(dilate)) * 2 + 1
        mask = mask.filter(ImageFilter.MaxFilter(kernel))
        mask = mask.point(lambda p: 255 if p > 0 else 0)

    return mask


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


def _find_latest_output(output_dir: Path, pid: int | None, product: str | None) -> Path:
    if product:
        search_dir = output_dir / product
        pattern = "*_PID-*_R*.png" if pid is None else f"*_PID-{pid}_*_R*.png"
        candidates = list(search_dir.glob(pattern))
    else:
        pattern = "**/*_PID-*_R*.png" if pid is None else f"**/*_PID-{pid}_*_R*.png"
        candidates = list(output_dir.glob(pattern))
    if not candidates:
        raise FileNotFoundError("No output images found matching the pattern.")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def compare_piping(
    reference_path: Path,
    output_path: Path,
    edge_percentile: float,
    fabric_threshold: int,
    dilate: int,
    boundary_width: int,
    cushion_mask_path: Path | None,
    shift_min: int,
    shift_max: int,
    shift_step: int,
) -> tuple[CompareResult, Image.Image, Image.Image]:
    ref_img = _load_image(reference_path)
    out_img = _load_image(output_path)

    out_img, resized = _resize_to(out_img, ref_img.size)

    boundary_mask = None
    if cushion_mask_path is not None:
        with Image.open(cushion_mask_path) as mask_img:
            mask_img = ImageOps.exif_transpose(mask_img).convert("L")
        if mask_img.size != ref_img.size:
            mask_img = ImageOps.fit(mask_img, ref_img.size, Image.NEAREST, centering=(0.5, 0.5))
        boundary_mask = _boundary_from_mask(mask_img, boundary_width)

    ref_edges = _extract_pipe_edges(
        ref_img,
        edge_percentile,
        fabric_threshold,
        dilate,
        boundary_mask,
    )
    out_edges = _extract_pipe_edges(
        out_img,
        edge_percentile,
        fabric_threshold,
        dilate,
        boundary_mask,
    )

    best_iou = -1.0
    best_shift = 0
    for dy in range(shift_min, shift_max + 1, shift_step):
        shifted = _shift_mask(out_edges, dy)
        iou = _mask_iou(ref_edges, shifted)
        if iou > best_iou:
            best_iou = iou
            best_shift = dy

    return CompareResult(
        output_path=output_path,
        reference_path=reference_path,
        iou=best_iou,
        best_shift_y=best_shift,
        output_size=out_img.size,
        reference_size=ref_img.size,
        resized=resized,
        edge_percentile=edge_percentile,
        fabric_threshold=fabric_threshold,
        dilate=dilate,
        boundary_width=boundary_width,
        cushion_mask=cushion_mask_path,
        shift_min=shift_min,
        shift_max=shift_max,
        shift_step=shift_step,
    ), ref_edges, out_edges


def _append_log(result: CompareResult, log_path: Path, iou_target: float) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    status = "PASS" if result.iou >= iou_target else "FAIL"
    size_match = result.output_size == result.reference_size
    size_delta = (
        (result.output_size[0] - result.reference_size[0]),
        (result.output_size[1] - result.reference_size[1]),
    )
    lines = [
        "---",
        f"## {timestamp}",
        f"- Reference: `{result.reference_path}`",
        f"- Output: `{result.output_path}`",
        f"- Similarity (IoU): {result.iou:.4f} ({status}, target {iou_target:.2f})",
        f"- Best shift_y: {result.best_shift_y}px",
        f"- Edge percentile: {result.edge_percentile:.2f}",
        f"- Fabric threshold: {result.fabric_threshold}",
        f"- Dilate: {result.dilate}",
        f"- Boundary width: {result.boundary_width}px",
        f"- Cushion mask: `{result.cushion_mask}`",
        f"- Shift search: {result.shift_min}..{result.shift_max} step {result.shift_step}",
        f"- Reference size: {result.reference_size[0]}x{result.reference_size[1]}",
        f"- Output size: {result.output_size[0]}x{result.output_size[1]} (resized={result.resized})",
        f"- Size match: {size_match} (delta {size_delta[0]}x{size_delta[1]})",
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
        handle.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare piping positions to a reference image.")
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--product", type=str, default=None)
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument("--edge-percentile", type=float, default=DEFAULT_EDGE_PERCENTILE)
    parser.add_argument("--fabric-threshold", type=int, default=DEFAULT_FABRIC_THRESHOLD)
    parser.add_argument("--dilate", type=int, default=DEFAULT_DILATE)
    parser.add_argument("--boundary-width", type=int, default=DEFAULT_BOUNDARY_WIDTH)
    parser.add_argument("--cushion-mask", type=Path, default=None)
    parser.add_argument("--shift-min", type=int, default=DEFAULT_SHIFT_MIN)
    parser.add_argument("--shift-max", type=int, default=DEFAULT_SHIFT_MAX)
    parser.add_argument("--shift-step", type=int, default=DEFAULT_SHIFT_STEP)
    parser.add_argument("--iou-target", type=float, default=DEFAULT_IOU_TARGET)
    parser.add_argument(
        "--log",
        type=Path,
        default=Path("documentation/piping_similarity_log.md"),
    )
    parser.add_argument(
        "--debug-dir",
        type=Path,
        default=None,
        help="Optional directory to save reference/output edge masks for inspection.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    reference_path = args.reference
    if not reference_path.exists():
        raise SystemExit(f"Reference image not found: {reference_path}")

    if args.output is None:
        output_path = _find_latest_output(args.output_dir, args.pid, args.product)
    else:
        output_path = args.output

    if not output_path.exists():
        raise SystemExit(f"Output image not found: {output_path}")

    result, ref_edges, out_edges = compare_piping(
        reference_path=reference_path,
        output_path=output_path,
        edge_percentile=args.edge_percentile,
        fabric_threshold=args.fabric_threshold,
        dilate=args.dilate,
        boundary_width=args.boundary_width,
        cushion_mask_path=args.cushion_mask,
        shift_min=args.shift_min,
        shift_max=args.shift_max,
        shift_step=args.shift_step,
    )

    _append_log(result, args.log, args.iou_target)
    if args.debug_dir is not None:
        args.debug_dir.mkdir(parents=True, exist_ok=True)
        ref_edges.save(args.debug_dir / "reference_edges.png")
        out_edges.save(args.debug_dir / "output_edges.png")

    print("[piping-compare]")
    print(f" reference={reference_path}")
    print(f" output={output_path}")
    print(f" iou={result.iou:.4f} (target {args.iou_target:.2f})")
    print(f" best_shift_y={result.best_shift_y}px")
    print(f" ref_size={result.reference_size[0]}x{result.reference_size[1]}")
    print(
        f" output_size={result.output_size[0]}x{result.output_size[1]} (resized={result.resized})"
    )
    print("[/piping-compare]")


if __name__ == "__main__":
    main()
