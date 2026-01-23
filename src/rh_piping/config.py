"""Configuration loading for the piping workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

ASSETS_DIR = Path("assets")
OUTPUT_DIR = Path("output")
PROMPTS_DIR = Path("prompts")


@dataclass
class AppConfig:
    model: str
    temperature: float
    seed: int | None
    use_vertex: bool
    project: str | None
    location: str | None
    assets_dir: Path = ASSETS_DIR
    output_dir: Path = OUTPUT_DIR
    prompts_dir: Path = PROMPTS_DIR


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, fallback: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return fallback
    try:
        return float(raw)
    except ValueError:
        return fallback


def _env_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def load_config() -> AppConfig:
    load_dotenv()

    model = os.getenv("GEMINI_MODEL", "gemini-3-pro-image-preview")
    temperature = _env_float("GEMINI_TEMPERATURE", _env_float("TEMPERATURE", 0.2))
    seed = _env_int("GEMINI_SEED")
    if seed is None:
        seed = _env_int("SEED")
    use_vertex = _env_flag("GOOGLE_GENAI_USE_VERTEXAI", default=True)
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "global")

    return AppConfig(
        model=model,
        temperature=temperature,
        seed=seed,
        use_vertex=use_vertex,
        project=project,
        location=location,
    )
