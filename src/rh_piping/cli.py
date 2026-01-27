"""Command-line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

from rh_piping.config import load_config
from rh_piping.pipeline import run_pipeline
from rh_piping.prompts import find_prompt_by_pid, load_prompt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare piping image jobs for AI processing.",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        help="Optional prompt file override.",
    )
    parser.add_argument(
        "--pid",
        type=int,
        help="Prompt PID number (matches prompts/*PID#.md).",
    )
    parser.add_argument(
        "--product",
        type=str,
        help="Only process the donor image matching this product name.",
    )
    parser.add_argument(
        "--results",
        type=int,
        default=1,
        help="Number of results to generate per donor image (max 100).",
    )
    parser.add_argument(
        "--assets-dir",
        type=Path,
        help="Override the assets root directory.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Override the output directory.",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Override the model name/id.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        help="Override generation temperature.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of donor images to process.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List discovered jobs without calling any APIs.",
    )
    parser.add_argument(
        "--no-mask",
        action="store_true",
        help="Disable diff/edge masking when compositing onto donor.",
    )
    parser.add_argument(
        "--preserve-luminance",
        action="store_true",
        help="Preserve donor luminance (shadows) in the output; implies --no-mask.",
    )
    parser.add_argument(
        "--bare",
        action="store_true",
        help="Disable masks and post-processing; save raw model output from the API.",
    )
    parser.add_argument(
        "--no-post",
        action="store_true",
        help="Disable all post-processing; save raw model output from the API.",
    )
    parser.add_argument(
        "--fit-only",
        action="store_true",
        help="Fit output to donor size and alpha only; skip masks and other post steps.",
    )
    parser.add_argument(
        "--raw-any-size",
        action="store_true",
        help="When used with --bare, accept any raw output size without retries.",
    )
    parser.add_argument(
        "--retry-until-fits",
        action="store_true",
        help="When used with --bare, retry until the full subject fits in frame.",
    )
    parser.add_argument(
        "--retry-until-scale",
        action="store_true",
        help="When used with --bare, retry until the subject matches donor scale.",
    )
    parser.add_argument(
        "--scale-to-donor",
        action="store_true",
        help="When used with --bare, scale output to match donor subject width.",
    )
    parser.add_argument(
        "--auto-aspect-ratio",
        action="store_true",
        help="Pick the closest supported aspect ratio based on the donor image.",
    )
    parser.add_argument(
        "--mask-dir",
        type=Path,
        help="Directory containing optional per-product mask files.",
    )
    parser.add_argument(
        "--generate-mask",
        action="store_true",
        help="Generate a piping mask via the SAM2 space when missing.",
    )
    parser.add_argument(
        "--regenerate-mask",
        action="store_true",
        help="Force regeneration of the piping mask even if one exists.",
    )
    parser.add_argument(
        "--sam2-space",
        type=str,
        help="Hugging Face Space ID for SAM2 mask generation.",
    )
    parser.add_argument(
        "--sam2-model",
        type=str,
        help="SAM2 model checkpoint (tiny, small, base_plus, large).",
    )
    parser.add_argument(
        "--sam2-mask-threshold",
        type=int,
        help="Threshold for converting SAM2 overlay to a binary mask.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    config = load_config()
    if args.assets_dir:
        config.assets_dir = args.assets_dir
        config.processed_dir = args.assets_dir / "processed"
    if args.output_dir:
        config.output_dir = args.output_dir
    if args.model:
        config.model = args.model
    if args.temperature is not None:
        config.temperature = args.temperature
    if args.mask_dir:
        config.masks_dir = args.mask_dir
    if args.sam2_space:
        config.sam2_space = args.sam2_space
    if args.sam2_model:
        config.sam2_model = args.sam2_model
    if args.sam2_mask_threshold is not None:
        config.sam2_mask_threshold = args.sam2_mask_threshold
    if args.auto_aspect_ratio:
        config.auto_aspect_ratio = True

    if args.pid is not None and args.prompt is not None:
        raise SystemExit("Cannot use --pid together with --prompt.")

    if args.pid is not None:
        prompt_path = find_prompt_by_pid(args.pid, config.prompts_dir)
        prompt_path, prompt_text = load_prompt(prompt_path, config.prompts_dir)
    else:
        prompt_path, prompt_text = load_prompt(args.prompt, config.prompts_dir)

    post_process = not args.no_post and not args.bare
    fit_only = args.fit_only
    no_mask = args.no_mask or args.preserve_luminance or args.bare
    preserve_luminance = args.preserve_luminance and post_process
    generate_mask = args.generate_mask and post_process
    regenerate_mask = args.regenerate_mask and post_process
    run_pipeline(
        config,
        prompt_path=prompt_path,
        prompt_text=prompt_text,
        product=args.product,
        results=args.results,
        dry_run=args.dry_run,
        limit=args.limit,
        no_mask=no_mask,
        preserve_luminance=preserve_luminance,
        post_process=post_process,
        fit_only=fit_only,
        enforce_raw_size=not args.raw_any_size,
        retry_until_fits=args.retry_until_fits,
        retry_until_scale=args.retry_until_scale,
        scale_to_donor=args.scale_to_donor,
        generate_mask=generate_mask,
        regenerate_mask=regenerate_mask,
        sam2_model=args.sam2_model,
        sam2_space=args.sam2_space,
        sam2_mask_threshold=args.sam2_mask_threshold,
    )


if __name__ == "__main__":
    main()
