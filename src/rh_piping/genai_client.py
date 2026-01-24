"""Gen AI client helpers for image generation."""

from __future__ import annotations

import os
from pathlib import Path

from google import genai
from google.genai import types

from rh_piping.config import AppConfig


def ensure_credentials() -> str:
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds:
        creds = str(Path("vertex.json").resolve())
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds
    if not os.path.isfile(creds):
        raise FileNotFoundError(
            f"GOOGLE_APPLICATION_CREDENTIALS points to '{creds}', which does not exist."
        )
    return creds


def create_client(config: AppConfig) -> genai.Client:
    if config.use_vertex:
        if not config.project:
            raise RuntimeError("Missing GOOGLE_CLOUD_PROJECT for Vertex AI usage.")
        ensure_credentials()
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
        return genai.Client(vertexai=True, project=config.project, location=config.location)

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GOOGLE_API_KEY or GEMINI_API_KEY for Gemini API usage.")
    return genai.Client(api_key=api_key)


def normalize_model_id(model: str, use_vertex: bool) -> str:
    if use_vertex:
        return model[7:] if model.startswith("models/") else model
    return model if model.startswith("models/") else f"models/{model}"


def image_part_from_bytes(image_bytes: bytes, mime_type: str = "image/png") -> types.Part:
    return types.Part(inline_data=types.Blob(data=image_bytes, mime_type=mime_type))


def extract_image_from_response(response) -> bytes:
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) if content else []:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                return inline.data
    parts = getattr(response, "parts", None)
    if parts:
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                return inline.data
    raise RuntimeError("No image returned by model response.")


def summarize_response_for_debug(response) -> str:
    summaries = []
    parts = getattr(response, "parts", None)
    if parts:
        ptypes = []
        for part in parts:
            if getattr(part, "inline_data", None):
                ptypes.append("inline_data")
            elif getattr(part, "text", None):
                ptypes.append("text")
            else:
                ptypes.append(type(part).__name__)
        summaries.append(f"response.parts={','.join(ptypes) or 'none'}")
    for idx, candidate in enumerate(getattr(response, "candidates", [])):
        reason = getattr(candidate, "finish_reason", None) or "-"
        part_types = []
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) if content else []:
            if getattr(part, "inline_data", None):
                part_types.append("inline_data")
            elif getattr(part, "text", None):
                part_types.append("text")
            else:
                part_types.append(type(part).__name__)
        summaries.append(f"cand{idx}: reason={reason}, parts={','.join(part_types) or 'none'}")
    return "; ".join(summaries) if summaries else "no candidates"


def generate_piping_image(
    *,
    client: genai.Client,
    model_name: str,
    use_vertex: bool,
    prompt: str,
    donor_png: bytes,
    color_ref_png: bytes,
    temperature: float,
) -> bytes:
    model_id = normalize_model_id(model_name, use_vertex)
    parts = [
        image_part_from_bytes(donor_png),
        {"text": "Donor image (replace only the piping material)."},
        image_part_from_bytes(color_ref_png),
        {"text": "Color reference for the new piping material."},
        prompt,
    ]
    response = client.models.generate_content(
        model=model_id,
        contents=parts,
        config=types.GenerateContentConfig(
            temperature=temperature,
            response_modalities=["TEXT", "IMAGE"],
            image_config={"image_size": "4K"},
        ),
    )
    try:
        return extract_image_from_response(response)
    except RuntimeError as exc:
        debug_summary = summarize_response_for_debug(response)
        prompt_feedback = getattr(response, "prompt_feedback", None)
        raise RuntimeError(
            f"{exc} (response summary: {debug_summary}, prompt_feedback={prompt_feedback})"
        ) from exc
