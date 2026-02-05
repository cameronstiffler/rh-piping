# Project Journal

This log captures notable work, decisions, and approach changes. Append new entries as we go.

## 2026-01-29 to 2026-01-31 (Thu–Sat)

### Summary
- Built/iterated MID mask prompt and ran model-mask pass to generate masks and edits.
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
  - Rationale: model-mask pass is faster and integrates directly with the prompt.
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

## 2026-02-04 (Wed)

### Summary
- Added a session handoff snapshot so new sessions can resume without re-briefing.
 - Added a QA process doc for post-render checks.

### What we did
- Recorded canonical workflow, current PID/product, best recent outputs, and tuning knobs in `documentation/session_handoff.md`.
 - Documented post-render QA steps in `documentation/qa_process.md`.

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
- Auto-generated masks are clipped to donor alpha before save.

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

## 2026-01-31 (Sat)

### Summary
- Tightened MID-7 wording to request full piping band (not hairline) and reran; output saved as R16.

### What we did
- Updated `prompts/mask_pass/mask_prompt_MID-7.md` to emphasize thicker piping bands and include arms/base piping.
- Ran PID-7; mask coverage ~4.36%, output saved to `output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/ProvenceSo1bf4_PID-7_gemini849_FM_R16.png`.

---

## 2026-01-31 (Sat)

### Summary
- Added console logging for model + location on each run.

---

## 2026-01-31 (Sat)

### Summary
- Added Gemini segmentation mask path (JSON masks) with per-PID `_seg` MID prompts.

---

## 2026-01-31 (Sat)

### Summary
- Segmentation mask parser now supports 0–1000 normalized boxes; `_seg` prompts updated accordingly.

---

## 2026-01-31 (Sat)

### Summary
- Added optional Vertex endpoint support for mask generation (later removed).

### What we did
- Added `SAM2_VERTEX_ENDPOINT`/`SAM2_VERTEX_LOCATION` config + CLI overrides.
- Implemented Vertex PredictionService calls and mask parsing/selection.

### Approaches and why we switched
- You asked to call the mask generator on Vertex instead of a hosted space.

---

## 2026-01-31 (Sat)

### Summary
- Added piping reference images to the model inputs as visual examples (outlined piping).

### What we did
- Loaded `assets/original/piping_ref_images/`, converted to processed PNGs, and injected them into edit + mask calls.
- Added `PIPING_REF_MAX` to cap how many reference images are included.

### Approaches and why we switched
- In‑context examples should help the model localize piping without fine‑tuning.

---

## 2026-01-31 (Sat)

### Summary
- Moved piping reference input folder to a dedicated highlighted‑image directory.

### What we did
- Switched to `assets/original/piping_ref_images/` (and matching processed folder) for piping refs.

### Approaches and why we switched
- You want refs that already have piping explicitly highlighted.

---

## 2026-01-31 (Sat)

### Summary
- Added optional local (Transformers) mask generation path (later removed).

### What we did
- Added local mask runner config/CLI support (later removed).

### Approaches and why we switched
- You want to try local mask generation with the large model.

---

## 2026-01-31 (Sat)

### Summary
- Disabled Gemini model-mask pass when external mask generation is enabled.

### What we did
- Pipeline now turns off model mask pass whenever `--generate-mask` is used.

### Approaches and why we switched
- You want the external mask generator to be the only mask source.

---

## 2026-01-31 (Sat)

### Summary
- Constrained local mask prompts to cushion regions and refined masks to avoid wicker.

### What we did
- Added a cushion-body mask to sample foreground points and to refine masks.

### Approaches and why we switched
- The auto mask was picking wicker/arms; we now bias toward upholstery.

---

## 2026-01-31 (Sat)

### Summary
- Added target mode to generate cushion masks (positive = cushions).

### What we did
- Added `SAM2_TARGET` (piping/cushions) and cushion‑focused prompting/scoring.

### Approaches and why we switched
- You want a cushion‑only positive mask from the auto masker.

---

## 2026-01-31 (Sat)

### Summary
- Implemented the new cushion‑mask workflow (cushion outline → edit → diff mask → composite).

### What we did
- Added `CUSHION_MASK_PASS` and cushion outline mask generation from donor.
- Edit step uses cushion outline mask; post uses diff mask clipped to cushion mask.

### Approaches and why we switched
- You specified a new workflow centered on cushion outlines and piping diff masks.

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

---

## 2026-02-01 (Sun)

### Summary
- Shifted segmentation mask pass to produce filled cushion masks as images (Gemini Flash).

### What we did
- Updated `prompts/mask_pass/mask_prompt_MID-7_seg.md` to request a PNG cushion mask.
- Allowed segmentation mask pass to run alongside mask-from-output for post compositing.
- Enabled segmentation image output via `SEGMENTATION_RESPONSE_MIME_TYPE=image/png` and set `SEGMENTATION_MASK_MODEL=gemini-2.5-flash`.

### Approaches and why we switched
- JSON masks were truncating; image masks avoid token limits and fit the new cushion-mask workflow.

### Update
- Vertex Gemini Flash does not support image-mask output; reverted segmentation masks to JSON RLE for cushion masks.
- Added COCO RLE string decoding for segmentation masks and bumped segmentation max output tokens to 16384.
- Added fallback parsing of `bits` masks from truncated segmentation text responses.
- Hardened `bits` mask parsing to handle wrapped/truncated outputs (whitespace + padding).
- Allowed segmentation mask pass to continue even when mask coverage is near-zero (inspection mode).

---

## 2026-02-01 (Sun)

### Final export notes (R27)
- Command: `python3 -m rh_piping --pid 7 --product "Provence_Sofa112in_NaturalWeave_prod34270121_F_CC" --segmentation-mask-pass --segmentation-mask-model gemini-2.5-pro --mask-from-output --results 1`
- Donor: `assets/processed/donor_image/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC.png` (4096x1204, RGBA)
- Prompt: `prompts/initial_prompt_PID-7.md`
- Segmentation prompt: `prompts/mask_pass/mask_prompt_MID-7_seg.md` (cushion mask, bits format)
- Masking flow:
  - Segmentation mask pass executed (Gemini 2.5 Pro); mask saved but coverage reported 0.0000.
  - Mask-from-output used for post composite to preserve donor geometry.
  - Post fit output to donor canvas 4096x1204; donor alpha applied.
- Output: `output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/ProvenceSo1bf4_PID-7_gemini849_FM_R27.png`
- Color/DPI: Converted to Adobe RGB (1998); DPI set to 300.
- Recent artifacts:
  - Segmentation response JSON saved under `output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/recent/returned/mask/`.
  - Submitted donor for segmentation saved under `output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/recent/submitted/mask/`.

### Detail: Final image source of cushion interiors
- Final composite uses donor as the base. The post step composites *only* pixels included by the diff mask.
- Diff mask is built from the pixel differences between donor and model output; only those changed areas are applied back onto the donor.
- Therefore, cushion interiors in the final output are sourced from the **donor image by default**.
- Cushion interior pixels will only come from the model output if the model changed those regions enough to be captured by the diff mask.
- This keeps geometry and most fabric texture identical to the donor while allowing selective piping changes to be transferred.
- Added filled cushion mask generation and fallback: when segmentation returns near-zero coverage, pipeline now builds a cushion body mask from the donor and uses it instead.
- Updated MID-7 segmentation prompt to request official Gemini segmentation format (base64 PNG mask) and set thinking_budget=0 for 2.5 Flash segmentation requests.
- Added base64-mask parsing fallback from truncated segmentation JSON and donor cushion-mask fallback when segmentation output is unusable.
- Updated MID-7 segmentation prompt to prefer 32x32 mask size to reduce base64 output and truncation risk.
- Segmentation (or fallback) cushion mask now also gates the post diff-mask, preventing edits outside cushions (e.g., wicker).
- Added `--mask-only` CLI option to generate masks and skip edit/post output.
- Applied `thinking_budget=0` for Gemini 2.5 Pro segmentation requests (previously Flash-only).
- Added retry logic to drop `thinking_config` when Vertex rejects `thinking_budget=0` for Gemini 2.5 Pro.
- Disabled donor-derived cushion-mask fallback for segmentation; segmentation must now succeed or the run stops.
- Made base64-mask extraction more tolerant of truncated JSON by scanning from the base64 marker or mask key when quotes are missing.
- Updated MID-7 segmentation prompt to prefer 16x16 mask size to reduce base64 truncation.
- Updated MID-7 segmentation prompt to request raw base64 PNG binary mask (no JSON) to reduce truncation.
- Made base64 mask extraction tolerant of JPEG (`/9j/`) and raw base64 headers to recover truncated non-JSON mask outputs.
- Replaced MID-7 model mask prompt with cushion-only binary mask instructions.
- Allowed model mask pass to run alongside mask-from-output; model mask now gates post diff-mask when enabled.
- Enabled MODEL_MASK_PASS in .env to include cushion mask in the full workflow by default.
- Stopped CLI from disabling model-mask pass when --mask-from-output is set so cushion masks can gate post composites.
- Forced model mask generation to always use donor-sized masks so the cushion mask can reliably gate post diff compositing.

---

## 2026-02-01 (Sun)

### Run notes: PID-7 R34 (final looked perfect)
- Product: `Provence_Sofa112in_NaturalWeave_prod34270121_F_CC`
- Donor (processed): `assets/processed/donor_image/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC.png` (4096x1204, RGBA)
- Prompt: `prompts/initial_prompt_PID-7.md`
- Model: `gemini-3-pro-image-preview` via Vertex (model_tag `gemini849`)
- Masking:
  - Model mask pass enabled (MID-7 cushion-only binary mask from `prompts/mask_pass/mask_prompt_MID-7.md`).
  - Segmentation mask pass **off**; cushion_mask_pass **off** (per `output/.../recent/run.json`).
  - Mask submitted to edit step recorded in `output/.../recent/submitted/edit/edit_mask_PID-7_gemini849_20260201T211815Z_run0b8601a6_R34.png`.
- References:
  - Piping reference images available under `assets/original/piping_ref_images/`; pipeline uses them when present (capped by `PIPING_REF_MAX`).
- Edit step artifacts:
  - Donor submitted to edit: `output/.../recent/submitted/edit/edit_donor_PID-7_gemini849_20260201T211815Z_run0b8601a6_R34.png`.
- Post-processing:
  - Post enabled; output composited back onto donor using the model-generated cushion mask (mask used flag appears in filename as `_FM_`).
  - Output written with donor alpha handling and config-driven Adobe RGB/DPI enforcement.
- Output file: `output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/ProvenceSo1bf4_PID-7_gemini849_FM_R34.png`.
- Run metadata: `output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/recent/run.json` (run_id `0b8601a6...`, run_stamp_utc `20260201T211815Z`).
- Added optional post-edit piping mask pass to generate a binary mask from the edited output and save it under `recent/returned/mask/` (filename `piping_mask_...png`). Enable with `--post-mask-pass` or `POST_MASK_PASS=true`.

---

## 2026-02-01 (Sun)

### Run notes: PID-7 R1 (cushion mask pass test)
- Command: `python3 -m rh_piping --pid 7 --product "Provence_Sofa112in_NaturalWeave_prod34270121_F_CC" --results 1 --cushion-mask-pass`
- Product: `Provence_Sofa112in_NaturalWeave_prod34270121_F_CC`
- Donor (processed): `assets/processed/donor_image/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC.png` (4096x1204, RGBA)
- Mask: `assets/masks/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC_cushion.png` (4096x1204)
- Output: `output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/ProvenceSo1bf4_PID-7_gemini849_FM_R1.png` (4096x1204)
- Notes:
  - Cushion mask pass disables model mask/segmentation/auto-mask paths.
  - Mask coverage: ~6.03% of pixels.
  - Mask pixels outside donor alpha: 0.00%.
  - Mask bbox: (37, 57)–(3655, 1085); donor bbox: (28, 50)–(4068, 1179).
  - Mask center delta vs donor alpha center: (-202px, -43.5px) ≈ (-4.93%, -3.61%).

---

## 2026-02-01 (Sun)

### Run notes: PID-7 R2 (model mask pass, full result)
- Command: `python3 -m rh_piping --pid 7 --product "Provence_Sofa112in_NaturalWeave_prod34270121_F_CC" --results 1 --model-mask-pass --no-cushion-mask-pass`
- Product: `Provence_Sofa112in_NaturalWeave_prod34270121_F_CC`
- Donor (processed): `assets/processed/donor_image/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC.png` (4096x1204, RGBA)
- Mask: `output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/masks/ProvenceSo1bf4_PID-7_mask.png` (generated from 1904x560 model mask resized to 4096x1204)
- Output: `output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/ProvenceSo1bf4_PID-7_gemini849_FM_R2.png` (4096x1204)
- Notes:
  - Model mask pass retried twice; final mask was resized to donor size (ratio match).
  - Model mask coverage reported: 0.5237.
  - Post fit output to donor canvas (original 6336x2688).

---

## 2026-02-01 (Sun)

### Run notes: PID-7 R3 (cushion+model combined, no shift)
- Command: `python3 -m rh_piping --pid 7 --product "Provence_Sofa112in_NaturalWeave_prod34270121_F_CC" --results 1 --cushion-mask-pass --model-mask-pass`
- Output: `output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/ProvenceSo1bf4_PID-7_gemini849_FM_R3.png`
- Notes:
  - Combined post mask (model ∩ cushion) coverage: 0.0603.
  - Post mask center delta vs donor: (-86.5px, -199px) ≈ (-2.11%, -16.53%).

---

## 2026-02-01 (Sun)

### Run notes: PID-7 R4 (cushion+model combined, shift +199)
- Command: `python3 -m rh_piping --pid 7 --product "Provence_Sofa112in_NaturalWeave_prod34270121_F_CC" --results 1 --cushion-mask-pass --model-mask-pass --post-mask-shift-y 199`
- Output: `output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/ProvenceSo1bf4_PID-7_gemini849_FM_R4.png`
- Notes:
  - Combined post mask (model ∩ cushion) shifted by +199px in Y.
  - Post mask center delta vs donor: (-86.5px, 0px) ≈ (-2.11%, 0%).
  - Calibration overlay: `output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/recent/returned/post/cal_pipe_mask_PID-7_gemini849_20260202T053346Z_run70958bb4.png`.

---

## 2026-02-02 (Mon)

### Summary
- Removed external auto-mask and cushion-mask feature usage; model mask is now the primary path.
- Added/updated mask and image reference documentation.

### What we did
- Deleted cushion-mask CLI/config options and removed cushion-mask logic from the pipeline.
- Removed external auto-mask CLI/config options and generation paths.
- Updated `.env.example` to remove external auto-mask/cushion settings.
- Updated `documentation/masks_and_images.md` to reflect current mask/image types and note disabled features.

---

## 2026-02-02 (Mon)

### Summary
- Removed diff-mask feature; pipeline no longer builds or saves output-diff masks.
- Removed external auto-mask and cushion-mask support; runs now fail if requested.
- Model mask is the primary mask path.

---

## 2026-02-02 (Mon)

### Summary
- Renamed the cushion-oriented model mask prompt and output naming.

### What we did
- Renamed `prompts/mask_pass/mask_prompt_MID-7.md` to `prompts/mask_pass/mask_prompt_MID-7_cushion.md`.
- Updated model mask outputs to `model_cushion_mask_*.png`.
- Renamed calibration overlays to `cal_mask_*.png`.

---

## 2026-02-02 (Mon)

### Summary
- Added a dedicated piping mask prompt for MID-7.

### What we did
- Added `prompts/mask_pass/mask_prompt_MID-7_piping.md` to generate a white-on-black piping-only mask.

---

## 2026-02-02 (Mon)

### Summary
- Enabled model cushion mask + post-mask pass by default with auto-regeneration rules.

### What we did
- Model cushion mask auto-runs when the mask file is missing; `--model-mask-pass` now forces overwrite.
- Defaulted POST_MASK_PASS to true in config/env example.

---

## 2026-02-02 (Mon)

### Summary
- Constrained post-mask composites to the model cushion mask.
- Fixed IoU calculation for piping mask similarity tests and continued iterations.

### What we did
- Intersected post-mask piping mask with the base cushion mask before compositing.
- Added retry logic to post-mask generation.
- Updated piping similarity scripts to count IoU correctly and log failures.

---

## 2026-02-02 (Mon)

### Summary
- Tightened the MID-7 piping mask prompt to keep the mask mostly black and constrained to cushion-area piping.

---

## 2026-02-02 (Mon)

### Summary
- Added an optional donor piping mask pass (pre-edit).

### What we did
- Added `prompts/mask_pass/mask_prompt_MID-7_donor_piping.md` for donor piping detection.
- Added a donor piping mask pass (CLI/env) that saves `donor_piping_mask_*.png` and `*_donor_piping_mask.png`.
- Intersects donor piping mask with the model cushion mask when available.

---

## 2026-02-02 (Mon)

### Summary
- Donor piping mask is now mandatory and used for editing/compositing.

### What we did
- Made the donor piping mask generation always-on (no pass toggle).
- The donor piping mask is now the edit mask and composite mask.
- Added a prompt note to constrain piping edits to the donor piping mask.

---

## 2026-02-02 (Mon)

### Summary
- Disabled post-mask piping generation; donor piping mask is the only composite mask.

### What we did
- Turned off the post-mask pass in the pipeline.
- Updated docs to reflect donor piping mask usage for compositing.

---

## 2026-02-02 (Mon)

### Summary
- Ensured model outputs are fit to donor proportions before saving.

### What we did
- Added a pre-save fit step so `edit_raw_*.png` matches the donor size/aspect ratio.

---

## 2026-02-02 (Mon)

### Summary
- Substantially dilated the donor piping mask to make top piping show.

### What we did
- Added a donor piping mask dilation step (`DONOR_PIPING_MASK_EXPAND = 8`) before saving.

---

## 2026-02-02 (Mon)

### Summary
- Switched edit/composite back to the model cushion mask (donor piping mask is QA-only).

### What we did
- Removed donor piping mask from edit/composite usage; cushion mask now constrains edits.
- Updated prompt language to reference the cushion mask.

---

## 2026-02-02 (Mon)

### Summary
- Guarded post-processing so the cushion mask is still applied even if --no-mask leaks in.

### What we did
- If a cushion mask exists during post, the pipeline now overrides no-mask and composites anyway.

---

## 2026-02-02 (Mon)

### Summary
- Forced edit inputs to use donor size (no 21:9 padding) to prevent raw model proportion drift.

### What we did
- Disabled aspect-ratio padding for edit inputs and cleared aspect_ratio in the edit request.

---

## 2026-02-02 (Mon)

### Summary
- Reverted the donor-size forced edit input change.

### What we did
- Restored aspect-ratio padding and aspect_ratio on the edit request.

## 2026-02-02 (Mon) — Color tweak support
- Summary: Added Pillow-based saturation/hue adjustments for final composites.
- What we did:
  - Added `FINAL_SATURATION` / `FINAL_HUE_SHIFT` config knobs with defaults 1.0 / 0.0 so we can nudge the final PNG’s color without touching the donor geometry.
  - Implemented `adjust_image_color(...)` (HSV channel math) and run it just before saving the final result plus the preview, so the donor piping mask still governs where edits land.
  - This keeps the cushions aligned with the donor while letting us dial in color saturation/hue as a post-processing safety net.

## 2026-02-02 (Mon) — Cushion mask blur tuning
- Summary: Added optional blur post-processing to model cushion masks before they are sent to the edit API.
- What we did:
  - Introduced `MASK_BLUR_RADIUS` (default 0) so the mask keeps #000/#FFF cores but gains a thin gray feather along the edges.
  - This softer boundary keeps the final composite aligned with the donor piping mask while making the transition between pipes and cushions more subtle.

## 2026-02-03 (Tue) — Preserve donor ICC profile in composites
- Summary: Keep the donor color profile through composite steps to prevent global washout.
- What we did:
  - Composite outputs now save with the donor image ICC profile (when present).
  - This applies to `composite_output_with_donor`, `composite_with_mask`, `overlay_donor_with_mask`, and preview background compositing.

## 2026-02-03 (Tue) — Donor piping mask expansion + composite cleanup
- Summary: Reduced donor piping mask expansion and removed donor-outside overlay.
- What we did:
  - Donor piping mask expansion reduced from 8px to 4px.
  - Removed the “overlay donor outside cushions” composite step (no longer needed for color recovery).
  - Composite now keeps soft mask grays instead of hard-thresholding during the piping composite.

## 2026-02-03 (Tue) — Remove donor snapshot captures
- Summary: Removed the `current_donor/` snapshot captures from the pipeline.
- What we did:
  - Deleted the snapshot writes taken before/after mask and composite steps.

## 2026-02-03 (Tue) — Donor piping mask feathering
- Summary: Added a light blur option for the donor piping mask to smooth jagged pipe edges.
- What we did:
  - Added `DONOR_PIPING_MASK_BLUR` (default 0.0). When >0, we apply a small Gaussian blur after expansion.

## 2026-02-03 (Tue) — Donor piping mask expansion control
- Summary: Made donor piping mask expansion configurable for iteration.
- What we did:
  - Added `DONOR_PIPING_MASK_EXPAND` (default 2). Increase to thicken pipes; reduce for tighter edges.

## 2026-02-03 (Tue) — Post-mask pass default
- Summary: Post-mask pass now defaults off to avoid unexpected output masks.
- What we did:
  - `POST_MASK_PASS` defaults to false; enable explicitly per run when you want a mask derived from the edit output.

## 2026-02-03 (Tue) — Latest result copy
- Summary: Auto-copy the final output image to a shared folder after each run.
- What we did:
  - Added `LATEST_RESULT_DIR` (optional). When set, the pipeline copies the final PNG to `latest.png` in that folder, overwriting any existing file.

---

## 2026-02-04 (Wed) — Manual pipeline run (PID-7 R133)

### Summary
- Ran a manual pipeline call for PID-7 using the canonical flow.

### What we did
- Executed `python3 -m rh_piping --pid 7 --product "Provence_Sofa112in_NaturalWeave_prod34270121_F_CC"`.
- Used existing model cushion mask and generated donor piping mask (intersected, expanded 2px).
- Saved output `ProvenceSo1bf4_PID-7_gemini849_FM_R133.png`; ensured DPI set to 300.

---

## 2026-02-04 (Wed) — QA helper + prompt tightening

### Summary
- Added a QA helper to score any result against the donor along the pipe-path overlay.
- Tightened the PID-7 prompt to avoid texture smoothing, halos, and color spill.

### What we did
- Added `scripts/pipe_path_score.py` for manual pipe-path scoring (JSON + mask output).
- Updated `prompts/initial_prompt_PID-7.md` with stricter realism constraints.

---

## 2026-02-04 (Wed) — Processed donor fallback

### Summary
- Allowed the pipeline to use processed donors directly when originals are missing.

### What we did
- `build_jobs` now falls back to `assets/processed/donor_image/` if no originals are found.

---

## 2026-02-04 (Wed) — Model mask ratio tolerance

### Summary
- Added configurable tolerance for model mask size ratio mismatches.

### What we did
- Introduced `MODEL_MASK_RATIO_TOL` (default 0.01) so near‑match aspect ratios can be resized on the last retry.

---

## 2026-02-04 (Wed) — Explicit placement map refs

### Summary
- When available, include explicit placement maps as extra references for pipe drawing.

### What we did
- Pipeline now looks in `assets/processed/explicite_placement_map/` for a product‑named PNG and appends it to the piping reference list.
