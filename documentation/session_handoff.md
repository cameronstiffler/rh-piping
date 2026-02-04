# Session Handoff (Auto-Resume)

This file is a snapshot of the current working context so a new session can resume without asking for setup.

## General goals + limits (apply to all sessions)
- Goal: photorealistic piping-only recolors; everything else must remain identical to the donor.
- Do **not** use SAM2 or any external auto-masking system.
- Do **not** reintroduce the old cushion-mask feature.
- Do **not** use diff masks.
- Always produce exactly 1 result per run (unless explicitly told otherwise).
- Don’t modify `assets/masks/` unless explicitly requested.
- Prefer manual masks under `output/<product>/masks/` when provided.
- If mask/image artifact names/locations change, update:
  - `documentation/masks_and_images.md`
  - `documentation/process_log.md`
- Append notable actions to `documentation/journal.md`.

## Canonical workflow
- Model cushion mask for **edit**.
- Donor piping mask **intersected with cushion mask** for **composite**.
- Post-mask generation from output is **disabled**.
- No SAM2 or external auto-masking.

## Current target
- Product: `Provence_Sofa112in_NaturalWeave_prod34270121_F_CC`
- PID: `7`
- Goal: photoreal piping recolor with **no “inked outline”** appearance.

## Current prompt adjustments (PID-7)
- `prompts/initial_prompt_PID-7.md` includes strict anti-outline rules and “preserve donor luminance / texture” constraints.

## Last-known good candidates (viewer PNGs)
- `output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/ProvenceSo1bf4_PID-7_gemini849_FM_R99_viewer_white.png`
- `output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/ProvenceSo1bf4_PID-7_gemini849_FM_R100_viewer_white.png`
- Close seconds: `R102`, `R105`.

## Current param baseline (env)
These are the knobs used during recent iterations:
- `DONOR_PIPING_MASK_EXPAND`: 4–5
- `DONOR_PIPING_MASK_BLUR`: 0.0
- `EDIT_MASK_ERODE`: 2 (testing 1–3)
- `EDIT_MASK_BLUR_RADIUS`: 0
- `MASK_BLUR_RADIUS`: 0
- `COMPOSITE_MASK_ERODE`: 1–2
- `COMPOSITE_MASK_FEATHER`: 0.4–0.6

## QA references (QA-only)
- Pipe-path overlay: `assets/processed/QA_pass/donor_pipe_path.png`
- QA outputs: `output/<product>/recent/returned/post/*pipe_path_score*.json`

## Resume checklist
1) Pick next knob tweak (prefer edit-mask erosion / composite erosion).
2) Run fresh API call (no caching).
3) Generate viewer-white PNG and compare to R99/R100.
