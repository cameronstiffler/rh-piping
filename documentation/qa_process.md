# QA Process (Post-Render)

This document describes the QA checks performed **after** a final image is rendered.

## Inputs
- Final output: `output/<product>/<short>_PID-<PID>_<model>_F*_R<idx>.png`
- Donor: `assets/processed/donor_image/<product>.png`
- QA reference pipe path: `assets/processed/QA_pass/donor_pipe_path.png`

## Checks
1) **Pipe-path accuracy (QA-only)**
   - Compare the final output to the donor along the reference pipe path.
   - When `PIPE_PATH_PNG` is set, the pipeline writes:
     - `output/<product>/recent/returned/post/*pipe_path_score*.json`
     - `output/<product>/recent/returned/post/*cal_mask_pipe_path*.png`

2) **Off-pipe integrity**
   - Ensure pixels outside the piping region remain identical to the donor.
   - Large off-pipe deltas indicate mask bleed or model overreach.

3) **Photorealism spot-check**
   - Look for halos, graphic/inked seams, double-lines, or texture smearing.
   - Verify lighting/shadows and wicker/fabric texture remain unchanged.

## Pass criteria (qualitative)
- Piping recolor is visible but subtle, following the exact donor piping path.
- No new seams, no outlines, no double lines.
- No visible changes outside the piping.

** Prepare for next pass but making changes to code and prompts to achieve general goals **
