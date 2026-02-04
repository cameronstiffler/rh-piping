#!/usr/bin/env python3
"""Run a short parameter sweep and capture pipe-path QA scores.

This script is for iteration/QA only. It does not change production behavior.
It relies on the pipeline writing `pipe_path_score_*.json` under:
  output/<product>/recent/returned/post/
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class SweepRow:
    iteration: int
    expand: int
    blur: float
    changed_pipe_pct: float
    changed_non_pipe_pct: float
    mean_diff_pipe: float
    mean_diff_non_pipe: float
    score_path: str


def _latest_score_json(output_dir: Path, product: str) -> Path | None:
    post_dir = output_dir / product / "recent" / "returned" / "post"
    if not post_dir.exists():
        return None
    candidates = sorted(post_dir.glob("*pipe_path_score*.json"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def _run_once(
    *,
    pid: int,
    product: str,
    expand: int,
    blur: float,
    skip_api_calls: bool,
    pipe_path_png: str,
) -> None:
    env = os.environ.copy()
    env["POST_MASK_PASS"] = "0"
    env["DONOR_PIPING_MASK_EXPAND"] = str(expand)
    env["DONOR_PIPING_MASK_BLUR"] = str(blur)
    # Composite edge controls (optional; sweep may override).
    env.setdefault("COMPOSITE_MASK_ERODE", "0")
    env.setdefault("COMPOSITE_MASK_FEATHER", "0.0")
    env["PIPE_PATH_PNG"] = pipe_path_png
    env.setdefault("PIPE_PATH_RGB", "255,73,73")
    env.setdefault("PIPE_PATH_COLOR_TOL", "40")
    env.setdefault("PIPE_PATH_EXPAND", "8")
    env.setdefault("PIPE_PATH_CHANGE_THRESHOLD", "8")
    if skip_api_calls:
        env["SKIP_API_CALLS"] = "1"

    cmd = ["python3", "-m", "rh_piping", "--pid", str(pid), "--product", product]
    subprocess.run(cmd, check=True, env=env)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="12-iteration sweep for pipe-path QA scoring.")
    parser.add_argument("--pid", type=int, default=7)
    parser.add_argument(
        "--product",
        type=str,
        default="Provence_Sofa112in_NaturalWeave_prod34270121_F_CC",
    )
    parser.add_argument("--iters", type=int, default=12)
    parser.add_argument("--skip-api-calls", action="store_true", default=True)
    parser.add_argument(
        "--pipe-path-png",
        type=str,
        default="assets/processed/donor_pipe_location/donor_pipe_path.png",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir: Path = args.output_dir

    schedule: list[tuple[int, float, int, float]] = []
    # 12-step default schedule:
    # - increase coverage (expand)
    # - introduce small feathering only after some coverage exists
    # - add slight erode when feathering to reduce bleed/halos
    expands = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 14]
    blurs = [0.0] * 6 + [0.8] * 6
    erodes = [0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 2, 2]
    feathers = [0.0] * 6 + [0.4, 0.4, 0.6, 0.6, 0.8, 1.0]
    for i in range(min(args.iters, len(expands))):
        schedule.append((expands[i], blurs[i], erodes[i], feathers[i]))
    while len(schedule) < args.iters:
        schedule.append((expands[-1], blurs[-1], erodes[-1], feathers[-1]))

    rows: list[SweepRow] = []
    start = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[sweep] start={start} pid={args.pid} product={args.product}")

    last_score = _latest_score_json(output_dir, args.product)
    if args.skip_api_calls and last_score is None:
        print("[sweep] No prior score found; warmup run will call the API once.")

    for i, (expand, blur, erode, feather) in enumerate(schedule, start=1):
        before = _latest_score_json(output_dir, args.product)
        print(
            f"[sweep] iter={i}/{args.iters} expand={expand} blur={blur} "
            f"erode={erode} feather={feather}"
        )
        os.environ["COMPOSITE_MASK_ERODE"] = str(erode)
        os.environ["COMPOSITE_MASK_FEATHER"] = str(feather)
        _run_once(
            pid=args.pid,
            product=args.product,
            expand=expand,
            blur=blur,
            skip_api_calls=(args.skip_api_calls and before is not None),
            pipe_path_png=args.pipe_path_png,
        )
        after = _latest_score_json(output_dir, args.product)
        if after is None or after == before:
            raise RuntimeError("Expected a new pipe_path_score JSON but did not find one.")
        data = json.loads(after.read_text(encoding="utf-8"))
        row = SweepRow(
            iteration=i,
            expand=expand,
            blur=blur,
            changed_pipe_pct=float(data["changed_pipe_pct"]),
            changed_non_pipe_pct=float(data["changed_non_pipe_pct"]),
            mean_diff_pipe=float(data["mean_diff_pipe"]),
            mean_diff_non_pipe=float(data["mean_diff_non_pipe"]),
            score_path=str(after),
        )
        rows.append(row)
        print(
            "  -> changed(pipe) "
            f"{row.changed_pipe_pct:.4f} | changed(non-pipe) {row.changed_non_pipe_pct:.6f} | "
            f"meanΔ(pipe) {row.mean_diff_pipe:.2f} | meanΔ(non-pipe) {row.mean_diff_non_pipe:.3f}"
        )

    # Summarize best rows (prioritize pipe change; keep non-pipe low).
    ranked = sorted(
        rows,
        key=lambda r: (
            -r.changed_pipe_pct,
            r.changed_non_pipe_pct,
            -r.mean_diff_pipe,
            r.mean_diff_non_pipe,
        ),
    )
    best = ranked[0]
    print("[sweep] best:")
    print(
        f"  expand={best.expand} blur={best.blur} changed_pipe_pct={best.changed_pipe_pct:.4f} "
        f"changed_non_pipe_pct={best.changed_non_pipe_pct:.6f} meanΔ(pipe)={best.mean_diff_pipe:.2f} "
        f"meanΔ(non-pipe)={best.mean_diff_non_pipe:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
