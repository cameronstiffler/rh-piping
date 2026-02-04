# Codex Runbook (Piping QA + Iteration)

This document consolidates the operating constraints and iteration goals for this repo so you don’t have to restate them.

## Primary goal

Produce **photorealistic** furniture edits where **only the seam piping material changes** to match the color reference. Everything else (upholstery, wicker/frame, legs, lighting, shadows, background, alpha) should remain an exact match to the donor.

## Workflow rules (non‑negotiable)

- **Do not use any external auto-masking system** under any circumstance.
- **Do not reintroduce the old cushion-mask feature** (removed; unrelated to the model cushion mask).
- **Do not use diff masks** or fall back to them.
- **Exactly 1 result** per run (don’t add `--results` unless explicitly requested).
- **Don’t add/modify `assets/masks/`** unless explicitly requested.
- Prefer manual masks under `output/<product>/masks/` when provided.
- If mask/image artifact names/locations change, update:
  - `documentation/masks_and_images.md`
  - `documentation/process_log.md`
- Append notable actions to `documentation/journal.md`.

## Masking model (current)

- **Model cushion mask** defines the cushion/piping area and is used whenever building edit inputs; it may be regenerated via `--model-mask-pass`.
  - Prompt: `prompts/mask_pass/mask_prompt_MID-<PID>_cushion.md`
  - Fallbacks: `prompts/mask_pass/mask_prompt_MID-<PID>.md`, then MID-3.
- **Donor piping mask** comes from:
  - Prompt: `prompts/mask_pass/mask_prompt_MID-<PID>_donor_piping.md`
  - Saved to: `output/<product>/masks/<short>_PID-<PID>_donor_piping_mask.png`
- The donor piping mask is the **default composite mask**, aligned and **intersected with the cushion mask** before compositing.
- **Do not generate or use a post-edit piping mask from model output.**

## Iteration + QA

### Pipe-path overlay (QA-only)

There is a *QA-only* reference path image:

- `assets/processed/QA_pass/donor_pipe_path.png`

Use it only to judge whether the edited piping “flows” along the expected path. It must **never** be required in production because real subjects differ.

The pipeline can emit QA metrics and overlays when `PIPE_PATH_PNG` is set (see `.env.example`):
- `output/<product>/recent/returned/post/*pipe_path_score*.json`
- `output/<product>/recent/returned/post/*cal_mask_pipe_path*.png`

Interpretation:
- Prefer **higher on-path change** (`changed_pipe_pct`, `mean_diff_pipe`) so piping actually updates.
- Prefer **near-zero off-path change** (`changed_non_pipe_pct`, `mean_diff_non_pipe`) to keep donor unchanged.

### Photorealism reference dataset

Use multiple images from this dataset to generalize “good piping” characteristics:
- `assets/original/piping_ref_images/`

Use these refs for QA scoring/heuristics (edge realism, avoid halos/double-lines/over-blur), not for hard constraints tied to a single subject.

### Fast iteration

- Use the existing cached-output feature (`SKIP_API_CALLS=true`) whenever you’re only changing *post/composite* parameters and you can reuse a prior API result.
- When prompt or mask generation changes require new model outputs, run with API calls enabled and keep the iteration loop tight.

### Commit/push cadence

- Push after **every 12 iterations** (parameter sweep batch or prompt batch), or sooner if a clear quality win is found.

## When to ask first

Ask before adding any new fundamental masks, changing mask types, or introducing new external dependencies.
