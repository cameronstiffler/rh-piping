"""Gen AI client helpers for image generation."""

from __future__ import annotations

import os
from pathlib import Path
import io

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


def extract_text_from_response(response) -> str:
    texts: list[str] = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) if content else []:
            text = getattr(part, "text", None)
            if text:
                texts.append(text)
    parts = getattr(response, "parts", None)
    if parts:
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                texts.append(text)
    direct_text = getattr(response, "text", None)
    if direct_text:
        texts.append(direct_text)
    return "\n".join(texts).strip()


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
    piping_ref_pngs: list[bytes] | None,
    mask_png: bytes | None,
    temperature: float,
    image_size: str | None,
    aspect_ratio: str | None,
    response_mime_type: str | None,
    image_output_mime_type: str | None,
) -> bytes:
    model_id = normalize_model_id(model_name, use_vertex)
    parts = [
        image_part_from_bytes(donor_png),
        {"text": "Donor image (replace only the piping material)."},
        image_part_from_bytes(color_ref_png),
        {"text": "Color reference for the new piping material."},
    ]
    if piping_ref_pngs:
        parts.append(
            {
                "text": (
                    "Piping reference examples follow. The piping is outlined in cyan "
                    "(#00daee), 3px stroke. Use those outlines only as visual guidance."
                )
            }
        )
        for idx, ref_png in enumerate(piping_ref_pngs, start=1):
            parts.extend(
                [
                    image_part_from_bytes(ref_png),
                    {"text": f"Piping reference {idx}: outlined areas indicate piping."},
                ]
            )
    if mask_png is not None:
        parts.extend(
            [
                image_part_from_bytes(mask_png),
                {"text": "Mask image: white = piping to change; black = keep unchanged."},
            ]
        )
    parts.append(prompt)
    config_kwargs: dict[str, object] = {
        "temperature": temperature,
        "response_modalities": ["TEXT", "IMAGE"],
    }
    # response_mime_type is not supported for image generation on some models.
    image_config: dict[str, str] = {}
    if image_size:
        image_config["imageSize"] = image_size
    if aspect_ratio:
        image_config["aspectRatio"] = aspect_ratio
    if image_output_mime_type:
        image_config["outputMimeType"] = image_output_mime_type
    if image_config:
        config_kwargs["image_config"] = image_config
    response = client.models.generate_content(
        model=model_id,
        contents=parts,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    try:
        return extract_image_from_response(response)
    except RuntimeError as exc:
        debug_summary = summarize_response_for_debug(response)
        prompt_feedback = getattr(response, "prompt_feedback", None)
        raise RuntimeError(
            f"{exc} (response summary: {debug_summary}, prompt_feedback={prompt_feedback})"
        ) from exc


def generate_piping_mask(
    *,
    client: genai.Client,
    model_name: str,
    use_vertex: bool,
    prompt: str,
    donor_png: bytes,
    piping_ref_pngs: list[bytes] | None,
    image_size: str | None,
    aspect_ratio: str | None,
) -> bytes:
    model_id = normalize_model_id(model_name, use_vertex)
    parts = [image_part_from_bytes(donor_png)]
    if piping_ref_pngs:
        parts.append(
            {
                "text": (
                    "Piping reference examples follow. The piping is outlined in cyan "
                    "(#00daee), 3px stroke. Use those outlines only as visual guidance."
                )
            }
        )
        for idx, ref_png in enumerate(piping_ref_pngs, start=1):
            parts.extend(
                [
                    image_part_from_bytes(ref_png),
                    {"text": f"Piping reference {idx}: outlined areas indicate piping."},
                ]
            )
    if prompt.strip():
        parts.append({"text": prompt})
    config_kwargs: dict[str, object] = {
        "temperature": 0.0,
        "response_modalities": ["IMAGE"],
    }
    image_config: dict[str, str] = {}
    if image_size:
        image_config["imageSize"] = image_size
    if aspect_ratio:
        image_config["aspectRatio"] = aspect_ratio
    if image_config:
        config_kwargs["image_config"] = image_config
    response = client.models.generate_content(
        model=model_id,
        contents=parts,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    try:
        return extract_image_from_response(response)
    except RuntimeError as exc:
        debug_summary = summarize_response_for_debug(response)
        prompt_feedback = getattr(response, "prompt_feedback", None)
        raise RuntimeError(
            f"{exc} (response summary: {debug_summary}, prompt_feedback={prompt_feedback})"
        ) from exc


def generate_segmentation_mask_response(
    *,
    client: genai.Client,
    model_name: str,
    use_vertex: bool,
    prompt: str,
    donor_png: bytes,
    piping_ref_pngs: list[bytes] | None,
    response_mime_type: str | None = "application/json",
    max_output_tokens: int | None = None,
) -> tuple[str | None, bytes | None]:
    model_id = normalize_model_id(model_name, use_vertex)
    parts = [image_part_from_bytes(donor_png)]
    if piping_ref_pngs:
        parts.append(
            {
                "text": (
                    "Piping reference examples follow. The piping is outlined in cyan "
                    "(#00daee), 3px stroke. Use those outlines only as visual guidance."
                )
            }
        )
        for idx, ref_png in enumerate(piping_ref_pngs, start=1):
            parts.extend(
                [
                    image_part_from_bytes(ref_png),
                    {"text": f"Piping reference {idx}: outlined areas indicate piping."},
                ]
            )
    if prompt.strip():
        parts.append({"text": prompt})
    response_modalities = ["TEXT"]
    if response_mime_type and response_mime_type.startswith("image/"):
        response_modalities = ["IMAGE"]
    config_kwargs: dict[str, object] = {
        "temperature": 0.0,
        "response_modalities": response_modalities,
    }
    if response_mime_type:
        config_kwargs["response_mime_type"] = response_mime_type
    if max_output_tokens is not None:
        config_kwargs["max_output_tokens"] = max_output_tokens
    model_lower = model_name.lower()
    if "2.5-flash" in model_lower or "2.5-pro" in model_lower:
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    try:
        response = client.models.generate_content(
            model=model_id,
            contents=parts,
            config=types.GenerateContentConfig(**config_kwargs),
        )
    except Exception as exc:
        message = str(exc)
        retried = False
        if response_mime_type and "response_mime_type" in message and "not supported" in message:
            config_kwargs.pop("response_mime_type", None)
            retried = True
        if "thinking_budget" in message and "not support" in message:
            config_kwargs.pop("thinking_config", None)
            retried = True
        if retried:
            response = client.models.generate_content(
                model=model_id,
                contents=parts,
                config=types.GenerateContentConfig(**config_kwargs),
            )
        else:
            raise
    text = extract_text_from_response(response)
    image_bytes = None
    try:
        image_bytes = extract_image_from_response(response)
    except RuntimeError:
        image_bytes = None
    return text or None, image_bytes


def _extract_vertex_image_bytes(image_obj) -> bytes:
    for attr in ("image_bytes", "_image_bytes", "data", "_data"):
        value = getattr(image_obj, attr, None)
        if value:
            return value
    if hasattr(image_obj, "save"):
        import io as _io

        buf = _io.BytesIO()
        image_obj.save(buf, format="PNG")
        return buf.getvalue()
    raise RuntimeError("Unable to extract bytes from Vertex image object.")


def _png_bytes_from_image(image_obj) -> bytes:
    import io as _io

    buf = _io.BytesIO()
    image_obj.save(buf, format="PNG")
    return buf.getvalue()


def _downscale_png_bytes(
    image_bytes: bytes,
    *,
    max_bytes: int | None,
    max_edge: int | None = None,
) -> tuple[bytes, tuple[int, int], tuple[int, int]]:
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(image_bytes)) as img:
        img = ImageOps.exif_transpose(img).convert("RGBA")
        orig_size = img.size
        target = img
        if max_edge and max(orig_size) > max_edge:
            scale = max_edge / max(orig_size)
            target_size = (
                max(1, int(orig_size[0] * scale)),
                max(1, int(orig_size[1] * scale)),
            )
            target = target.resize(target_size, Image.LANCZOS)
        payload = _png_bytes_from_image(target)
        if max_bytes is None or len(payload) <= max_bytes:
            return payload, target.size, orig_size
        current = target
        current_bytes = payload
        for _ in range(6):
            ratio = (max_bytes / max(1, len(current_bytes))) ** 0.5 * 0.95
            if ratio >= 1:
                break
            new_size = (
                max(1, int(current.size[0] * ratio)),
                max(1, int(current.size[1] * ratio)),
            )
            if new_size == current.size:
                new_size = (
                    max(1, current.size[0] - 1),
                    max(1, current.size[1] - 1),
                )
            current = current.resize(new_size, Image.LANCZOS)
            current_bytes = _png_bytes_from_image(current)
            if len(current_bytes) <= max_bytes:
                break
        return current_bytes, current.size, orig_size


def remove_background_vertex(
    image_bytes: bytes,
    *,
    model_name: str | None = None,
    output_mime_type: str | None = None,
    prompt: str | None = None,
    project: str | None = None,
    location: str | None = None,
    max_bytes: int | None = None,
    max_edge: int | None = None,
) -> bytes:
    try:
        from vertexai import init as vertex_init
        from vertexai.preview.vision_models import Image as VertexImage
        from vertexai.preview.vision_models import ImageGenerationModel
        from vertexai.preview.vision_models import RawReferenceImage
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Vertex background removal requires google-cloud-aiplatform. "
            "Install it to use --vertex-bg-remove."
        ) from exc

    if project and location:
        vertex_init(project=project, location=location)
    model_id = model_name or "imagen-3.0-generate-001"
    out_mime = output_mime_type or "image/png"
    edit_prompt = prompt or "remove background"
    if max_bytes is None:
        max_bytes = 20_000_000
    if max_bytes is not None and max_bytes <= 0:
        max_bytes = None
    if max_edge is not None and max_edge <= 0:
        max_edge = None

    from PIL import Image, ImageOps, ImageChops
    with Image.open(io.BytesIO(image_bytes)) as base_img_in:
        base_img_in = ImageOps.exif_transpose(base_img_in).convert("RGBA")
        base_alpha = base_img_in.getchannel("A")
        base_size = base_img_in.size
        base_img = base_img_in.copy()

    edit_bytes, edit_size, orig_size = _downscale_png_bytes(
        image_bytes,
        max_bytes=max_bytes,
        max_edge=max_edge,
    )
    if edit_size != orig_size:
        print(
            " [post] vertex bg input downscaled "
            f"{orig_size[0]}x{orig_size[1]} -> {edit_size[0]}x{edit_size[1]} "
            f"(bytes {len(edit_bytes):,}/{max_bytes:,})"
        )

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
        tmp.write(edit_bytes)
        tmp.flush()
        base_image = VertexImage.load_from_file(tmp.name)
    model = ImageGenerationModel.from_pretrained(model_id)
    reference_images = None
    if "capability" in model_id:
        reference_images = [RawReferenceImage(reference_id=0, image=base_image)]
    result = model.edit_image(
        base_image=base_image,
        mask_mode="background",
        prompt=edit_prompt,
        output_mime_type=out_mime,
        reference_images=reference_images,
    )
    if isinstance(result, list):
        result = result[0]
    edited_bytes = _extract_vertex_image_bytes(result)
    with Image.open(io.BytesIO(edited_bytes)) as edited_img:
        edited_img = ImageOps.exif_transpose(edited_img)
        if "A" not in edited_img.getbands():
            return edited_bytes
        alpha = edited_img.getchannel("A")
        if edited_img.size != base_size:
            alpha = alpha.resize(base_size, Image.LANCZOS)
    if base_alpha.getextrema() != (255, 255):
        alpha = ImageChops.multiply(alpha, base_alpha)
    base_img.putalpha(alpha)
    return _png_bytes_from_image(base_img)
