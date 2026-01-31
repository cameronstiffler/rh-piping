"""Prompt selection helpers."""

from __future__ import annotations

from pathlib import Path
import re


PROMPT_PID_PATTERN = re.compile(r"PID-?\d+")


def pick_default_prompt(prompts_dir: Path) -> Path | None:
    candidates = sorted(prompts_dir.glob("*.md"))
    return candidates[0] if candidates else None


def find_prompt_by_pid(pid: int, prompts_dir: Path) -> Path:
    token = f"{pid}"
    pattern = re.compile(rf"PID-?{re.escape(token)}(?!\\d)")
    matches = [
        path
        for path in prompts_dir.glob("*.md")
        if pattern.search(path.name)
    ]
    if not matches:
        raise FileNotFoundError(f"No prompt matching PID{pid} in {prompts_dir}")
    matches.sort(key=lambda p: (len(p.name), p.name))
    return matches[0]


def prompt_id_from_path(prompt_path: Path | None) -> str:
    if prompt_path is None:
        return "PID-NA"
    match = PROMPT_PID_PATTERN.search(prompt_path.name)
    return match.group(0) if match else "PID-NA"


def load_prompt(prompt_path: Path | None, prompts_dir: Path) -> tuple[Path | None, str]:
    if prompt_path is not None:
        return prompt_path, prompt_path.read_text(encoding="utf-8")

    default_prompt = pick_default_prompt(prompts_dir)
    if default_prompt is None:
        return None, ""

    return default_prompt, default_prompt.read_text(encoding="utf-8")
