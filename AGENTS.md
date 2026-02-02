# AGENTS.md

This file guides coding agents working in this repo. Keep it short and practical.

## Project overview
- Repo: piping-focused furniture image edits via AI image APIs.
- Primary flow: generate a model cushion mask, run edit, post-process composite.

## Key paths
- `assets/original/`: source TIFFs (donor, color ref, piping refs)
- `assets/processed/`: 4K PNGs derived from originals
- `prompts/`: edit prompts + mask prompts
- `output/<product>/`: run outputs and `recent/` artifacts
- Documentation: `documentation/masks_and_images.md`, `documentation/process_log.md`, `documentation/journal.md`

## Mask workflow rules (must follow)
- **Do not use SAM2** under any circumstance.
- **Do not use or generate the old cushion-mask feature** (removed; unrelated to the model cushion mask). Do not reintroduce it.
- **Do not use diff masks** or fall back to them.
- Model cushion mask is the primary mask path (defines the cushion area).
- Model cushion mask prompt is named `prompts/mask_pass/mask_prompt_MID-<PID>_cushion.md`.
- Model cushion mask auto-generates if the output mask file is missing; `--model-mask-pass` forces regeneration/overwrite.
- Donor piping mask is always generated from the donor using `prompts/mask_pass/mask_prompt_MID-<PID>_donor_piping.md` and saved as `output/<product>/masks/<short>_PID-<PID>_donor_piping_mask.png`.
- The donor piping mask is for QA/reference only; the **model cushion mask** is used for edit and composite.
- Do not generate or use the post-mask piping mask from model output; the donor piping mask is the only composite mask.
- Calibration overlays are saved as `cal_mask_*.png` under `output/<product>/recent/returned/post/`.

## Results count
- We always want exactly 1 result. Do **not** add `--results` to commands unless explicitly asked.

## Command hygiene
- Don’t add or modify `assets/masks/` unless explicitly requested.
- Prefer manual masks under `output/<product>/masks/` when provided.
- If you change any mask/image artifact names or locations, update `documentation/masks_and_images.md` and `documentation/process_log.md`.
- Append notable actions to `documentation/journal.md`.

## Testing
- No tests for now unless explicitly requested.

## When uncertain
- Ask for the exact donor, mask, and output files to validate alignment.
