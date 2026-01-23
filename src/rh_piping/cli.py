"""Command-line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

from rh_piping.config import load_config
from rh_piping.pipeline import run_pipeline
from rh_piping.prompts import load_prompt


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
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    config = load_config()
    if args.assets_dir:
        config.assets_dir = args.assets_dir
    if args.output_dir:
        config.output_dir = args.output_dir

    prompt_path, _ = load_prompt(args.prompt, config.prompts_dir)
    run_pipeline(
        config,
        prompt_path=prompt_path,
        dry_run=args.dry_run,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
