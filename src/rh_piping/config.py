"""Configuration loading for the piping workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

ASSETS_DIR = Path("assets")
OUTPUT_DIR = Path("output")
PROMPTS_DIR = Path("prompts")
PROCESSED_DIR = ASSETS_DIR / "processed"
MASKS_DIR = ASSETS_DIR / "masks"


@dataclass
class AppConfig:
    model: str
    temperature: float
    seed: int | None
    use_vertex: bool
    project: str | None
    location: str | None
    image_size: str | None
    aspect_ratio: str | None
    auto_aspect_ratio: bool
    assets_dir: Path = ASSETS_DIR
    output_dir: Path = OUTPUT_DIR
    prompts_dir: Path = PROMPTS_DIR
    processed_dir: Path = PROCESSED_DIR
    masks_dir: Path = MASKS_DIR
    sam2_space: str = "lightly-ai/SAMv2-Mask-Generator"
    sam2_model: str = "tiny"
    sam2_mask_threshold: int = 10


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


def _env_str(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw if raw else None


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
    image_size = _env_str("GEMINI_IMAGE_SIZE") or _env_str("IMAGE_SIZE")
    aspect_ratio = _env_str("GEMINI_ASPECT_RATIO") or _env_str("ASPECT_RATIO")
    auto_aspect_ratio = _env_flag("AUTO_ASPECT_RATIO", default=False)
    sam2_space = os.getenv("SAM2_SPACE", "lightly-ai/SAMv2-Mask-Generator")
    sam2_model = os.getenv("SAM2_MODEL", "tiny")
    sam2_mask_threshold = _env_int("SAM2_MASK_THRESHOLD") or 10

    return AppConfig(
        model=model,
        temperature=temperature,
        seed=seed,
        use_vertex=use_vertex,
        project=project,
        location=location,
        image_size=image_size,
        aspect_ratio=aspect_ratio,
        auto_aspect_ratio=auto_aspect_ratio,
        sam2_space=sam2_space,
        sam2_model=sam2_model,
        sam2_mask_threshold=sam2_mask_threshold,
    )
