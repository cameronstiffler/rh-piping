"""Command-line entry point."""

from __future__ import annotations

import argparse
import io
import os
import time
from pathlib import Path

from PIL import Image

from rh_piping.config import load_config
from rh_piping.genai_client import create_client, generate_piping_image
from rh_piping.pipeline import run_pipeline
from rh_piping.prompts import find_prompt_by_pid, load_prompt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare piping image jobs for AI processing.",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        help="Optional prompt file override.",
    )
    parser.add_argument(
        "--pid",
        type=int,
        help="Prompt PID number (matches prompts/*PID#.md).",
    )
    parser.add_argument(
        "--product",
        type=str,
        help="Only process the donor image matching this product name.",
    )
    parser.add_argument(
        "--results",
        type=int,
        default=1,
        help="Number of results to generate per donor image (max 100).",
    )
    parser.add_argument(
        "--assets-dir",
        type=Path,
        help="Override the assets root directory.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Override the output directory.",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Override the model name/id.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        help="Override generation temperature.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of donor images to process.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List discovered jobs without calling any APIs.",
    )
    parser.add_argument(
        "--no-mask",
        action="store_true",
        help="Disable diff/edge masking when compositing onto donor.",
    )
    parser.add_argument(
        "--preserve-luminance",
        action="store_true",
        help="Preserve donor luminance (shadows) in the output; implies --no-mask.",
    )
    parser.add_argument(
        "--bare",
        action="store_true",
        help="Disable masks and post-processing; save raw model output from the API.",
    )
    parser.add_argument(
        "--no-post",
        action="store_true",
        help="Disable all post-processing; save raw model output from the API.",
    )
    parser.add_argument(
        "--fit-only",
        action="store_true",
        help="Fit output to donor size and alpha only; skip masks and other post steps.",
    )
    parser.add_argument(
        "--raw-any-size",
        action="store_true",
        help="When used with --bare, accept any raw output size without retries.",
    )
    parser.add_argument(
        "--retry-until-fits",
        action="store_true",
        help="When used with --bare, retry until the full subject fits in frame.",
    )
    parser.add_argument(
        "--retry-until-scale",
        action="store_true",
        help="When used with --bare, retry until the subject matches donor scale.",
    )
    parser.add_argument(
        "--scale-to-donor",
        action="store_true",
        help="When used with --bare, scale output to match donor subject width.",
    )
    parser.add_argument(
        "--auto-aspect-ratio",
        action="store_true",
        help="Pick the closest supported aspect ratio based on the donor image.",
    )
    parser.add_argument(
        "--chroma-key",
        action="store_true",
        help="Use a chroma key background and convert it to transparency.",
    )
    parser.add_argument(
        "--chroma-color",
        type=str,
        help="Hex color for chroma key background (default: #FF00FF).",
    )
    parser.add_argument(
        "--chroma-tolerance",
        type=int,
        help="Tolerance for chroma key removal (0-255).",
    )
    parser.add_argument(
        "--chroma-softness",
        type=int,
        help="Soft edge width for chroma key removal (0 disables).",
    )
    parser.add_argument(
        "--chroma-edge-clip",
        type=int,
        help="Clip low alpha values to fully transparent (0 disables).",
    )
    parser.add_argument(
        "--chroma-restore-donor",
        action="store_true",
        help="Restore donor RGB under semi-transparent pixels after keying.",
    )
    parser.add_argument(
        "--chroma-restore-threshold",
        type=int,
        help="Alpha threshold for donor RGB restore (0-255).",
    )
    parser.add_argument(
        "--chroma-mask-donor",
        action="store_true",
        help="Use donor alpha as a mask to restrict chroma keying to background.",
    )
    parser.add_argument(
        "--chroma-mask-threshold",
        type=int,
        help="Alpha threshold for donor mask (0-255).",
    )
    parser.add_argument(
        "--chroma-mask-expand",
        type=int,
        help="Pixels to expand the donor background mask (0 disables).",
    )
    parser.add_argument(
        "--mask-dir",
        type=Path,
        help="Directory containing optional per-product mask files.",
    )
    parser.add_argument(
        "--generate-mask",
        action="store_true",
        help="Generate a piping mask via the SAM2 space when missing.",
    )
    parser.add_argument(
        "--regenerate-mask",
        action="store_true",
        help="Force regeneration of the piping mask even if one exists.",
    )
    parser.add_argument(
        "--sam2-space",
        type=str,
        help="Hugging Face Space ID for SAM2 mask generation.",
    )
    parser.add_argument(
        "--sam2-model",
        type=str,
        help="SAM2 model checkpoint (tiny, small, base_plus, large).",
    )
    parser.add_argument(
        "--sam2-mask-threshold",
        type=int,
        help="Threshold for converting SAM2 overlay to a binary mask.",
    )
    parser.add_argument(
        "--vertex-diagnose",
        action="store_true",
        help="Run a minimal Vertex image call and print resolved config.",
    )
    parser.add_argument(
        "--vertex-bg-remove",
        action="store_true",
        help="Use Vertex edit/background removal after generation.",
    )
    parser.add_argument(
        "--vertex-bg-model",
        type=str,
        help="Vertex model ID for background removal (e.g., imagen-3.0-generate-001).",
    )
    parser.add_argument(
        "--vertex-bg-prompt",
        type=str,
        help="Prompt for background removal edit call.",
    )
    parser.add_argument(
        "--vertex-bg-output-mime",
        type=str,
        help="Output MIME type for background removal (default image/png).",
    )
    parser.add_argument(
        "--vertex-bg-location",
        type=str,
        help="Vertex location for background removal edit call.",
    )
    parser.add_argument(
        "--vertex-bg-max-bytes",
        type=int,
        help="Max PNG byte size for Vertex background removal input (downscale if larger).",
    )
    parser.add_argument(
        "--vertex-bg-max-edge",
        type=int,
        help="Max pixel edge for Vertex background removal input (downscale if larger).",
    )
    parser.add_argument(
        "--model-mask-pass",
        action="store_true",
        help="Generate a piping mask with the model before editing.",
    )
    parser.add_argument(
        "--mask-from-output",
        action="store_true",
        help="Build the post-process mask from the model output (diff vs donor).",
    )
    parser.add_argument(
        "--mask-from-output-threshold",
        type=int,
        help="Threshold for output-diff mask (0-255).",
    )
    parser.add_argument(
        "--model-mask-prompt",
        type=str,
        help="Override prompt for model-generated piping mask.",
    )
    parser.add_argument(
        "--model-mask-threshold",
        type=int,
        help="Threshold for binarizing model-generated mask (0-255).",
    )
    return parser


def _run_vertex_diagnose(config) -> None:
    print("[vertex-diagnose]")
    print(f" use_vertex={config.use_vertex}")
    print(f" model={config.model}")
    print(f" project={config.project}")
    print(f" location={config.location}")
    print(f" GOOGLE_GENAI_USE_VERTEXAI={os.getenv('GOOGLE_GENAI_USE_VERTEXAI')}")
    print(f" GOOGLE_CLOUD_PROJECT={os.getenv('GOOGLE_CLOUD_PROJECT')}")
    print(f" GOOGLE_CLOUD_LOCATION={os.getenv('GOOGLE_CLOUD_LOCATION')}")
    creds_env = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_env:
        print(f" GOOGLE_APPLICATION_CREDENTIALS={creds_env}")
    else:
        print(" GOOGLE_APPLICATION_CREDENTIALS=<unset> (defaults to vertex.json)")
    creds_path = Path(creds_env) if creds_env else Path("vertex.json").resolve()
    print(f" vertex.json_exists={creds_path.exists()} ({creds_path})")

    if not config.use_vertex:
        print(" abort: GOOGLE_GENAI_USE_VERTEXAI is false (not using Vertex).")
        print("[/vertex-diagnose]")
        return

    client = create_client(config)

    donor = Image.new("RGBA", (64, 64), (255, 255, 255, 0))
    color = Image.new("RGB", (64, 64), (200, 100, 50))

    def _to_bytes(img: Image.Image) -> bytes:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    donor_bytes = _to_bytes(donor)
    color_bytes = _to_bytes(color)

    start = time.time()
    try:
        output = generate_piping_image(
            client=client,
            model_name=config.model,
            use_vertex=config.use_vertex,
            prompt="Diagnostic: change piping color only.",
            donor_png=donor_bytes,
            color_ref_png=color_bytes,
            mask_png=None,
            temperature=config.temperature,
            image_size=None,
            aspect_ratio=None,
            response_mime_type=None,
            image_output_mime_type=None,
        )
    except Exception as exc:  # pylint: disable=broad-except
        elapsed = time.time() - start
        print(f" image_call=failed elapsed={elapsed:.2f}s error={exc}")
        print("[/vertex-diagnose]")
        raise
    elapsed = time.time() - start
    print(f" image_call=ok bytes={len(output)} elapsed={elapsed:.2f}s")
    print("[/vertex-diagnose]")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    config = load_config()
    if args.assets_dir:
        config.assets_dir = args.assets_dir
        config.processed_dir = args.assets_dir / "processed"
    if args.output_dir:
        config.output_dir = args.output_dir
    if args.model:
        config.model = args.model
    if args.temperature is not None:
        config.temperature = args.temperature
    if args.mask_dir:
        config.masks_dir = args.mask_dir
    if args.sam2_space:
        config.sam2_space = args.sam2_space
    if args.sam2_model:
        config.sam2_model = args.sam2_model
    if args.sam2_mask_threshold is not None:
        config.sam2_mask_threshold = args.sam2_mask_threshold
    if args.auto_aspect_ratio:
        config.auto_aspect_ratio = True
    if args.vertex_diagnose:
        _run_vertex_diagnose(config)
        return
    if args.vertex_bg_remove:
        config.vertex_bg_remove = True
    if args.vertex_bg_model:
        config.vertex_bg_model = args.vertex_bg_model
    if args.vertex_bg_prompt:
        config.vertex_bg_prompt = args.vertex_bg_prompt
    if args.vertex_bg_output_mime:
        config.vertex_bg_output_mime = args.vertex_bg_output_mime
    if args.vertex_bg_location:
        config.vertex_bg_location = args.vertex_bg_location

    chroma_key = args.chroma_key
    chroma_color = (
        args.chroma_color or config.chroma_key_hex or "#FF00FF"
    )
    chroma_tolerance = (
        args.chroma_tolerance
        if args.chroma_tolerance is not None
        else config.chroma_key_tolerance
    )
    chroma_softness = (
        args.chroma_softness
        if args.chroma_softness is not None
        else config.chroma_key_softness
    )
    chroma_edge_clip = (
        args.chroma_edge_clip
        if args.chroma_edge_clip is not None
        else config.chroma_key_edge_clip
    )
    chroma_restore_donor = args.chroma_restore_donor or config.chroma_key_restore_donor
    chroma_restore_threshold = (
        args.chroma_restore_threshold
        if args.chroma_restore_threshold is not None
        else config.chroma_key_restore_threshold
    )
    chroma_mask_donor = args.chroma_mask_donor or config.chroma_key_mask_donor
    chroma_mask_threshold = (
        args.chroma_mask_threshold
        if args.chroma_mask_threshold is not None
        else config.chroma_key_mask_threshold
    )
    chroma_mask_expand = (
        args.chroma_mask_expand
        if args.chroma_mask_expand is not None
        else config.chroma_key_mask_expand
    )
    if not chroma_key:
        chroma_color = None

    if args.pid is not None and args.prompt is not None:
        raise SystemExit("Cannot use --pid together with --prompt.")

    if args.pid is not None:
        prompt_path = find_prompt_by_pid(args.pid, config.prompts_dir)
        prompt_path, prompt_text = load_prompt(prompt_path, config.prompts_dir)
    else:
        prompt_path, prompt_text = load_prompt(args.prompt, config.prompts_dir)

    post_process = not args.no_post and not args.bare
    fit_only = args.fit_only
    no_mask = args.no_mask or args.preserve_luminance or args.bare
    preserve_luminance = args.preserve_luminance and post_process
    generate_mask = args.generate_mask and post_process
    regenerate_mask = args.regenerate_mask and post_process
    vertex_bg_max_bytes = (
        args.vertex_bg_max_bytes
        if args.vertex_bg_max_bytes is not None
        else config.vertex_bg_max_bytes
    )
    vertex_bg_max_edge = (
        args.vertex_bg_max_edge
        if args.vertex_bg_max_edge is not None
        else config.vertex_bg_max_edge
    )
    model_mask_pass = args.model_mask_pass or config.model_mask_pass
    model_mask_prompt = args.model_mask_prompt or config.model_mask_prompt
    model_mask_threshold = (
        args.model_mask_threshold
        if args.model_mask_threshold is not None
        else config.model_mask_threshold
    )
    mask_from_output = args.mask_from_output
    mask_from_output_threshold = (
        args.mask_from_output_threshold
        if args.mask_from_output_threshold is not None
        else 10
    )
    if mask_from_output:
        model_mask_pass = False
    run_pipeline(
        config,
        prompt_path=prompt_path,
        prompt_text=prompt_text,
        product=args.product,
        results=args.results,
        dry_run=args.dry_run,
        limit=args.limit,
        no_mask=no_mask,
        preserve_luminance=preserve_luminance,
        post_process=post_process,
        fit_only=fit_only,
        enforce_raw_size=not args.raw_any_size,
        retry_until_fits=args.retry_until_fits,
        retry_until_scale=args.retry_until_scale,
        scale_to_donor=args.scale_to_donor,
        chroma_key_hex=chroma_color,
        chroma_key_tolerance=chroma_tolerance,
        chroma_key_softness=chroma_softness,
        chroma_key_edge_clip=chroma_edge_clip,
        chroma_key_restore_donor=chroma_restore_donor,
        chroma_key_restore_threshold=chroma_restore_threshold,
        chroma_key_mask_donor=chroma_mask_donor,
        chroma_key_mask_threshold=chroma_mask_threshold,
        chroma_key_mask_expand=chroma_mask_expand,
        vertex_bg_remove=config.vertex_bg_remove,
        vertex_bg_model=config.vertex_bg_model,
        vertex_bg_prompt=config.vertex_bg_prompt,
        vertex_bg_output_mime=config.vertex_bg_output_mime,
        vertex_bg_location=config.vertex_bg_location,
        vertex_bg_max_bytes=vertex_bg_max_bytes,
        vertex_bg_max_edge=vertex_bg_max_edge,
        model_mask_pass=model_mask_pass,
        model_mask_prompt=model_mask_prompt,
        model_mask_threshold=model_mask_threshold,
        mask_from_output=mask_from_output,
        mask_from_output_threshold=mask_from_output_threshold,
        generate_mask=generate_mask,
        regenerate_mask=regenerate_mask,
        sam2_model=args.sam2_model,
        sam2_space=args.sam2_space,
        sam2_mask_threshold=args.sam2_mask_threshold,
    )


if __name__ == "__main__":
    main()
