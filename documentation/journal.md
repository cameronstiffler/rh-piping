# Project Journal

This log captures notable work, decisions, and approach changes. Append new entries as we go.

## 2026-01-29 to 2026-01-31 (Thu–Sat)

### Summary
- Built/iterated MID mask prompt and ran model-mask pass to generate piping masks and edits.
- Added “recent” artifact capture for submitted/returned files and post-processed outputs.
- Added run-identifying tags to recent filenames (PID, model tag, UTC stamp, short run id, result index).
- Allowed prompt filenames to include custom suffixes after PID.
- Organized outputs under product-specific `output/<product>/recent/...`.

### What we did
- Created/updated `prompts/mask_prompt_MID-3.md` for model-driven mask generation.
- Ran multiple pipeline passes to validate mask coverage and output behavior.
- Implemented recent artifact tracking:
  - `recent/submitted/edit/` and `recent/submitted/mask/` for donor/mask bytes sent to APIs.
  - `recent/returned/mask/`, `recent/returned/result/`, and `recent/returned/post/` for API outputs and post-processed results.
- Added filename tags to recent files for run traceability.
- Added prompt selection support for filenames like `initial_prompt_PID-7_myvariant.md`.

### Approaches and why we switched
- **Mask generation approach**
  - Started with model-mask pass for fast, prompt-driven masks.
  - Kept SAM2 as optional (external) path when needed.
  - Rationale: model-mask pass is faster and integrates directly with the prompt; SAM2 remains useful for certain mask types.
- **Submitted/returned artifact capture**
  - Initial idea: global `output/submitted/`.
  - Switched to `output/<product>/submitted/` to avoid cross-product collisions.
  - Finalized as `output/<product>/recent/` to align with “latest run” intent and simplify discovery.
- **Traceability**
  - Added run identifiers in filenames instead of overwriting fixed names to disambiguate runs.
  - Added a `returned/post/` bucket to capture final post-processing results.

### Notes / follow-ups
- If you want automatic cleanup of older `recent/` files, we can add a retention policy.
- If you want per-run metadata in a JSON or text summary, we can add that too.

---

## 2026-01-31 (Sat)

### Summary
- Removed swatch-color fallback fill during post-processing.

### What we did
- Deleted `apply_masked_swatch_color` and all fallback usage.
- Simplified pipeline to always use model output + mask composite without swatch fill.

### Approaches and why we switched
- Dropped fallback because you want the final image to reflect only the model output applied to donor.

---

## 2026-01-31 (Sat)

### Summary
- Post-process now composites only the masked area from the result onto the donor (no auto mask logic).

### What we did
- Simplified `composite_output_with_donor` to use the provided mask only.
- Disabled masked compositing when no mask is present.

### Approaches and why we switched
- You wanted post to apply only the visible mask area from the result to the donor.

---

## 2026-01-31 (Sat)

### Summary
- Clipped generated masks to the donor silhouette to prevent spill outside the sofa.

### What we did
- Model mask bytes are now multiplied by donor alpha before use/saving.
- SAM2 masks are clipped to donor alpha before save.

### Approaches and why we switched
- You observed masks leaking above the donor; we now hard‑clip to silhouette.

---

## 2026-01-31 (Sat)

### Summary
- Enforced exact donor-size model masks (no resize/crop/pad).

### What we did
- Mask generation now uses donor-sized input and requires exact-size output.
- Clipped mask to donor alpha after validation to prevent spill.

### Approaches and why we switched
- You saw mask height drift (too high/low); we now require exact donor-size output.

---

## 2026-01-31 (Sat)

### Summary
- Post now refuses to resize masks; masks must already match donor size.

### What we did
- `load_mask_image` and post composite now error on size mismatch instead of fitting.

### Approaches and why we switched
- You want the mask to align with the donor raw; no post resizing.

---

## 2026-01-31 (Sat)

### Summary
- Mask generation now demands exact donor-size output (with retries) via MID prompt.

### What we did
- Appended explicit donor dimensions to the mask prompt.
- Added retry loop if the model returns the wrong mask size.

### Approaches and why we switched
- You want the MID file to dictate size and avoid any post resizing.

---

## 2026-01-31 (Sat)

### Summary
- Added flag to derive post mask from output diff vs donor.

### What we did
- `--mask-from-output` builds a diff mask from the generated image for post.
- Added threshold option for tuning (`--mask-from-output-threshold`).

### Approaches and why we switched
- You want a flag-controlled fallback that avoids resizing and uses the output itself.

---

## 2026-01-31 (Sat)

### Summary
- `--mask-from-output` now auto-disables model mask pass to avoid size conflicts.

### What we did
- CLI/pipeline turn off model mask when output-diff mask is requested.

### Approaches and why we switched
- Model mask returns wrong dimensions; output-diff mask should run alone.

---

## 2026-01-31 (Sat)

### Summary
- Re-enabled model mask pass for edit step when using output-diff post mask.

### What we did
- Model mask used for edit input only; output-diff mask still controls post.
- Strict donor-size enforcement applies only when output-diff is off.

### Approaches and why we switched
- You asked to hand a mask to the edit step without resizing in post.

---

## 2026-01-31 (Sat)

### Summary
- Added `--no-model-mask-pass` to disable model mask even if set in env/config.

### What we did
- CLI override to force model mask pass off when needed.

### Approaches and why we switched
- Needed a clean way to run without model masks (strict sizing errors).

---

## 2026-01-31 (Sat)

### Summary
- Improved deterministic (no-SAM) piping mask selection.

### What we did
- Added a new piping-focused edge mask candidate and priority selection.
- Reduced silhouette preference so piping candidates win more often.

### Approaches and why we switched
- The old deterministic mask was too thin/shifted; now favor piping edges.

---

## 2026-01-31 (Sat)

### Summary
- Post-processing now requires a mask and always composites onto the donor.
- Model-mask prompt now explicitly constrains masks to the sofa silhouette.
- Model-mask pass now resizes to donor size only when aspect ratios match (last-resort fallback).

### What we did
- Post step errors if no mask is present; raw output is never saved as final.
- Updated default model-mask prompt to mention staying within the silhouette.
- Added fallback to resize model mask to donor size when the model returns a smaller but proportional mask.

### Approaches and why we switched
- Geometry must match donor exactly, so final is now always a masked composite.
- Model returns a smaller mask size; resizing keeps alignment while avoiding failure.

---

## 2026-01-31 (Sat)

### Summary
- Mask prompts can now be per-PID (MID file per PID), with MID-3 as fallback.

### What we did
- Model mask pass loads `prompts/mask_pass/mask_prompt_MID-<PID>.md` when present.
- Added `prompts/mask_pass/mask_prompt_MID-7.md` as the current PID copy.

### Approaches and why we switched
- You want mask prompts tailored per PID; defaulting to MID-3 only wasn’t flexible enough.

---

## 2026-01-31 (Sat)

### Summary
- Mask pass instructions now come only from MID files (no hardcoded instruction block).

### What we did
- Removed the fixed mask instruction text from the mask API call.
- Stopped appending dimension lines in code; MID files now include {{MASK_WIDTH}}/{{MASK_HEIGHT}} placeholders.
- CLI no longer uses `MODEL_MASK_PROMPT` from `.env` unless explicitly passed via `--model-mask-prompt`.

### Approaches and why we switched
- You want all mask instructions centralized in the MID prompt files.

---

## 2026-01-31 (Sat)

### Summary
- Ran PID-7 with MID-7 mask prompt only; output saved as R14.

### What we did
- Mask pass still returned 1904x560 and was resized to 4096x1204; mask coverage ~3.16%.
- Final output saved to `output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/ProvenceSo1bf4_PID-7_gemini849_FM_R14.png`.

---

## Next Entry Template

### Date
YYYY-MM-DD

### Summary
- ...

### What we did
- ...

### Approaches and why we switched
- ...

### Questions / decisions to revisit
- ...
