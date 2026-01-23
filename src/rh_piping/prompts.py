"""Prompt selection helpers."""

from __future__ import annotations

from pathlib import Path


def pick_default_prompt(prompts_dir: Path) -> Path | None:
    candidates = sorted(prompts_dir.glob("*.md"))
    return candidates[0] if candidates else None


def load_prompt(prompt_path: Path | None, prompts_dir: Path) -> tuple[Path | None, str]:
    if prompt_path is not None:
        return prompt_path, prompt_path.read_text(encoding="utf-8")

    default_prompt = pick_default_prompt(prompts_dir)
    if default_prompt is None:
        return None, ""

    return default_prompt, default_prompt.read_text(encoding="utf-8")
