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

    if args.pid is not None and args.prompt is not None:
        raise SystemExit("Cannot use --pid together with --prompt.")

    if args.pid is not None:
        prompt_path = find_prompt_by_pid(args.pid, config.prompts_dir)
        prompt_path, prompt_text = load_prompt(prompt_path, config.prompts_dir)
    else:
        prompt_path, prompt_text = load_prompt(args.prompt, config.prompts_dir)

    no_mask = args.no_mask or args.preserve_luminance
    run_pipeline(
        config,
        prompt_path=prompt_path,
        prompt_text=prompt_text,
        product=args.product,
        results=args.results,
        dry_run=args.dry_run,
        limit=args.limit,
        no_mask=no_mask,
        preserve_luminance=args.preserve_luminance,
    )


if __name__ == "__main__":
    main()
