#!/usr/bin/env python3
"""Score a result against the donor along a pipe-path overlay (QA-only)."""

from __future__ import annotations

import argparse
from pathlib import Path

from rh_piping.config import load_config
from rh_piping.quality import (
    parse_pipe_path_rgb_env,
    score_result_against_donor_along_pipe_path,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score a result image against the donor along a pipe-path overlay."
    )
    parser.add_argument("--donor", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--pipe-path", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, default=None)
    parser.add_argument("--out-mask", type=Path, default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config()

    if not args.donor.exists():
        raise SystemExit(f"Donor not found: {args.donor}")
    if not args.result.exists():
        raise SystemExit(f"Result not found: {args.result}")
    if not args.pipe_path.exists():
        raise SystemExit(f"Pipe path PNG not found: {args.pipe_path}")

    pipe_rgb = parse_pipe_path_rgb_env(config.pipe_path_rgb) or (255, 73, 73)
    score, pipe_region = score_result_against_donor_along_pipe_path(
        donor_png=args.donor.read_bytes(),
        result_png=args.result.read_bytes(),
        pipe_path_png=args.pipe_path.read_bytes(),
        path_rgb=pipe_rgb,
        color_tol=config.pipe_path_color_tol,
        expand_px=config.pipe_path_expand,
        change_threshold=config.pipe_path_change_threshold,
    )

    print("[pipe-path-score]")
    print(f" donor={args.donor}")
    print(f" result={args.result}")
    print(f" changed_pipe_pct={score.changed_pipe_pct:.6f}")
    print(f" changed_non_pipe_pct={score.changed_non_pipe_pct:.6f}")
    print(f" mean_diff_pipe={score.mean_diff_pipe:.3f}")
    print(f" mean_diff_non_pipe={score.mean_diff_non_pipe:.3f}")
    print("[/pipe-path-score]")

    if args.out_json is not None:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(score.to_json(), encoding="utf-8")
    if args.out_mask is not None:
        args.out_mask.parent.mkdir(parents=True, exist_ok=True)
        pipe_region.save(args.out_mask)


if __name__ == "__main__":
    main()
