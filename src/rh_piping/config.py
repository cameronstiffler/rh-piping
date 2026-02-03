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
    response_mime_type: str | None
    image_output_mime_type: str | None
    chroma_key_hex: str | None
    chroma_key_tolerance: int
    chroma_key_softness: int
    chroma_key_edge_clip: int
    chroma_key_restore_donor: bool
    chroma_key_restore_threshold: int
    chroma_key_mask_donor: bool
    chroma_key_mask_threshold: int
    chroma_key_mask_expand: int
    vertex_bg_remove: bool
    vertex_bg_model: str | None
    vertex_bg_prompt: str | None
    vertex_bg_output_mime: str | None
    vertex_bg_location: str | None
    vertex_bg_max_bytes: int | None
    vertex_bg_max_edge: int | None
    preview_bg_hex: str | None
    enforce_adobe_rgb: bool
    adobe_rgb_icc: str | None
    enforce_dpi: bool
    target_dpi: int
    model_mask_pass: bool
    model_mask_prompt: str | None
    model_mask_threshold: int
    segmentation_mask_model: str | None
    segmentation_mask_threshold: int
    segmentation_response_mime_type: str | None
    segmentation_max_output_tokens: int | None
    piping_ref_max: int
    post_mask_pass: bool
    post_mask_prompt: str | None
    post_mask_threshold: int
    post_mask_shift_y: int
    post_mask_expand: int
    donor_piping_mask_prompt: str | None
    donor_piping_mask_threshold: int
    assets_dir: Path = ASSETS_DIR
    output_dir: Path = OUTPUT_DIR
    prompts_dir: Path = PROMPTS_DIR
    processed_dir: Path = PROCESSED_DIR
    masks_dir: Path = MASKS_DIR
    mask_blur_radius: int = 0
    final_saturation: float = 1.0
    final_hue_shift: float = 0.0


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
    response_mime_type = _env_str("RESPONSE_MIME_TYPE")
    image_output_mime_type = _env_str("IMAGE_OUTPUT_MIME_TYPE")
    chroma_key_hex = _env_str("CHROMA_KEY_HEX")
    chroma_key_tolerance = _env_int("CHROMA_KEY_TOLERANCE") or 8
    chroma_key_softness = _env_int("CHROMA_KEY_SOFTNESS") or 0
    chroma_key_edge_clip = _env_int("CHROMA_KEY_EDGE_CLIP") or 0
    chroma_key_restore_donor = _env_flag("CHROMA_KEY_RESTORE_DONOR", default=False)
    chroma_key_restore_threshold = _env_int("CHROMA_KEY_RESTORE_THRESHOLD") or 254
    chroma_key_mask_donor = _env_flag("CHROMA_KEY_MASK_DONOR", default=True)
    chroma_key_mask_threshold = _env_int("CHROMA_KEY_MASK_THRESHOLD") or 8
    chroma_key_mask_expand = _env_int("CHROMA_KEY_MASK_EXPAND") or 2
    vertex_bg_remove = _env_flag("VERTEX_BG_REMOVE", default=False)
    vertex_bg_model = _env_str("VERTEX_BG_MODEL")
    vertex_bg_prompt = _env_str("VERTEX_BG_PROMPT")
    vertex_bg_output_mime = _env_str("VERTEX_BG_OUTPUT_MIME")
    vertex_bg_location = _env_str("VERTEX_BG_LOCATION")
    vertex_bg_max_bytes = _env_int("VERTEX_BG_MAX_BYTES")
    vertex_bg_max_edge = _env_int("VERTEX_BG_MAX_EDGE")
    preview_bg_hex = _env_str("PREVIEW_BG_HEX")
    enforce_adobe_rgb = _env_flag("ENFORCE_ADOBE_RGB", default=False)
    adobe_rgb_icc = _env_str("ADOBE_RGB_ICC")
    enforce_dpi = _env_flag("ENFORCE_DPI", default=False)
    target_dpi = _env_int("TARGET_DPI") or 300
    model_mask_pass = _env_flag("MODEL_MASK_PASS", default=True)
    model_mask_prompt = _env_str("MODEL_MASK_PROMPT")
    model_mask_threshold = _env_int("MODEL_MASK_THRESHOLD") or 200
    segmentation_mask_model = _env_str("SEGMENTATION_MASK_MODEL") or "gemini-2.5-flash"
    segmentation_mask_threshold = _env_int("SEGMENTATION_MASK_THRESHOLD") or 1
    segmentation_response_mime_type = _env_str("SEGMENTATION_RESPONSE_MIME_TYPE")
    segmentation_max_output_tokens = _env_int("SEGMENTATION_MAX_OUTPUT_TOKENS")
    piping_ref_max = _env_int("PIPING_REF_MAX")
    if piping_ref_max is None:
        piping_ref_max = 4
    post_mask_pass = _env_flag("POST_MASK_PASS", default=True)
    post_mask_prompt = _env_str("POST_MASK_PROMPT")
    post_mask_threshold = _env_int("POST_MASK_THRESHOLD")
    if post_mask_threshold is None:
        post_mask_threshold = model_mask_threshold
    post_mask_shift_y = _env_int("POST_MASK_SHIFT_Y")
    if post_mask_shift_y is None:
        post_mask_shift_y = 0
    post_mask_expand = _env_int("POST_MASK_EXPAND")
    if post_mask_expand is None:
        post_mask_expand = 0
    donor_piping_mask_prompt = _env_str("DONOR_PIPING_MASK_PROMPT")
    donor_piping_mask_threshold = _env_int("DONOR_PIPING_MASK_THRESHOLD")
    if donor_piping_mask_threshold is None:
        donor_piping_mask_threshold = model_mask_threshold
    mask_blur_radius = _env_int("MASK_BLUR_RADIUS")
    if mask_blur_radius is None:
        mask_blur_radius = 0

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
        response_mime_type=response_mime_type,
        image_output_mime_type=image_output_mime_type,
        chroma_key_hex=chroma_key_hex,
        chroma_key_tolerance=chroma_key_tolerance,
        chroma_key_softness=chroma_key_softness,
        chroma_key_edge_clip=chroma_key_edge_clip,
        chroma_key_restore_donor=chroma_key_restore_donor,
        chroma_key_restore_threshold=chroma_key_restore_threshold,
        chroma_key_mask_donor=chroma_key_mask_donor,
        chroma_key_mask_threshold=chroma_key_mask_threshold,
        chroma_key_mask_expand=chroma_key_mask_expand,
        vertex_bg_remove=vertex_bg_remove,
        vertex_bg_model=vertex_bg_model,
        vertex_bg_prompt=vertex_bg_prompt,
        vertex_bg_output_mime=vertex_bg_output_mime,
        vertex_bg_location=vertex_bg_location,
        vertex_bg_max_bytes=vertex_bg_max_bytes,
        vertex_bg_max_edge=vertex_bg_max_edge,
        preview_bg_hex=preview_bg_hex,
        enforce_adobe_rgb=enforce_adobe_rgb,
        adobe_rgb_icc=adobe_rgb_icc,
        enforce_dpi=enforce_dpi,
        target_dpi=target_dpi,
        model_mask_pass=model_mask_pass,
        model_mask_prompt=model_mask_prompt,
        model_mask_threshold=model_mask_threshold,
        segmentation_mask_model=segmentation_mask_model,
        segmentation_mask_threshold=segmentation_mask_threshold,
        segmentation_response_mime_type=segmentation_response_mime_type,
        segmentation_max_output_tokens=segmentation_max_output_tokens,
        piping_ref_max=piping_ref_max,
        post_mask_pass=post_mask_pass,
        post_mask_prompt=post_mask_prompt,
        post_mask_threshold=post_mask_threshold,
       post_mask_shift_y=post_mask_shift_y,
       post_mask_expand=post_mask_expand,
       donor_piping_mask_prompt=donor_piping_mask_prompt,
       donor_piping_mask_threshold=donor_piping_mask_threshold,
        mask_blur_radius=mask_blur_radius,
        final_saturation=_env_float("FINAL_SATURATION", 1.0),
        final_hue_shift=_env_float("FINAL_HUE_SHIFT", 0.0),
    )
