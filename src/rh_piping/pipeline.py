"""Job discovery and orchestration for piping edits."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rh_piping.config import AppConfig
from rh_piping.io import ensure_dir, list_images

ORIGINAL_ROOT = "original"
DONOR_DIRNAME = "donor_image"
COLOR_REF_DIRNAME = "color_reference"
PIPING_REF_DIRNAME = "piping_ref_images"


@dataclass
class PipingJob:
    donor_image: Path
    color_references: list[Path]
    piping_references: list[Path]
    prompt_path: Path | None


def build_jobs(assets_dir: Path, prompt_path: Path | None) -> list[PipingJob]:
    original_dir = assets_dir / ORIGINAL_ROOT
    donor_dir = original_dir / DONOR_DIRNAME
    color_dir = original_dir / COLOR_REF_DIRNAME
    piping_dir = original_dir / PIPING_REF_DIRNAME

    donor_images = list_images(donor_dir)
    color_refs = list_images(color_dir)
    piping_refs = list_images(piping_dir)

    return [
        PipingJob(
            donor_image=donor,
            color_references=color_refs,
            piping_references=piping_refs,
            prompt_path=prompt_path,
        )
        for donor in donor_images
    ]


def run_pipeline(
    config: AppConfig,
    prompt_path: Path | None,
    dry_run: bool = False,
    limit: int | None = None,
) -> list[PipingJob]:
    ensure_dir(config.output_dir)

    jobs = build_jobs(config.assets_dir, prompt_path)
    if limit is not None:
        jobs = jobs[: max(limit, 0)]

    if dry_run:
        print(f"Discovered {len(jobs)} job(s).")
        for job in jobs:
            print(f"- donor={job.donor_image.name} color_refs={len(job.color_references)} piping_refs={len(job.piping_references)}")
        return jobs

    # TODO: Replace this stub with API calls to generate edited images.
    for job in jobs:
        _ = job

    return jobs
