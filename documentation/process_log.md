# Piping Process Log

This log tracks the steps and artifacts used to produce each result image.
Append new runs at the bottom.

## Workflow (Model Mask Only)

1) **Prepare donor**
   - Original donor: `assets/original/donor_image/<product>.tif`
   - Processed donor (4k): `assets/processed/donor_image/<product>.png`

2) **Select prompt**
   - Edit prompt: `prompts/initial_prompt_PID-<PID>.md`

3) **Generate model cushion mask from the model (model mask pass)**
   - Prompt: `prompts/mask_pass/mask_prompt_MID-<PID>_cushion.md` (fallback: `mask_prompt_MID-<PID>.md`, then MID-3).
   - Saved mask:
     - `output/<product>/masks/<short>_PID-<PID>_mask.png`
     - `output/<product>/recent/returned/mask/model_cushion_mask_*.png`
   - Submitted to edit:
     - `output/<product>/recent/submitted/edit/edit_mask_*.png`

4) **Submit edit request**
   - Donor submitted: `output/<product>/recent/submitted/edit/edit_donor_*.png`
   - Color ref (processed): `assets/processed/color_reference/<name>.png`
   - Piping refs (processed): `assets/processed/piping_ref_highlighted/*.png`

5) **Model output (raw)**
   - `output/<product>/recent/returned/result/edit_raw_*.png`

6) **Optional: Post-mask piping mask (from output)**
   - Prompt: `prompts/mask_pass/mask_prompt_MID-<PID>_piping.md` (fallback: `_post`)
   - Saved mask: `output/<product>/recent/returned/mask/piping_mask_*.png`

7) **Post composite (final)**
   - Final output: `output/<product>/<short>_PID-<PID>_<model>_F*_R<idx>.png`
   - Optional calibration overlay (most recent mask painted red): `output/<product>/recent/returned/post/cal_mask_*.png`

## Run Log (Append Below)

### Template
- Date:
- Command:
- Product:
- Donor (processed):
- Prompt:
- Mask (model mask):
- Output:
- Notes:

### 2026-02-01 (Sun) — PID-7 R5
- Command: `python3 -m rh_piping --pid 7 --product "Provence_Sofa112in_NaturalWeave_prod34270121_F_CC" --no-model-mask-pass --mask-dir "output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/masks"`
- Product: `Provence_Sofa112in_NaturalWeave_prod34270121_F_CC`
- Donor (processed): `assets/processed/donor_image/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC.png`
- Prompt: `prompts/initial_prompt_PID-7.md`
- Mask (used): `output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/masks/ProvenceSo1bf4_PID-7_mask.png`
- Output: `output/Provence_Sofa112in_NaturalWeave_prod34270121_F_CC/ProvenceSo1bf4_PID-7_gemini849_FM_R5.png`
- Notes: Mask coverage was large (~0.57); wicker was not immutable under this mask.
