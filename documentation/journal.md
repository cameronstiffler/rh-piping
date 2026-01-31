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
