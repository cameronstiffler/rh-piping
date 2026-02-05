# rh-piping

Python tooling for piping-focused furniture edits via AI image APIs.

## Layout

- `assets/original/donor_image/`: primary product images to edit
- `assets/original/color_reference/`: fabric/finish reference images
- `assets/original/piping_ref_images/`: piping reference images
- `assets/processed/`: generated 4K PNGs derived from original TIFFs
- `prompts/`: prompt templates
- `output/`: generated results in a subfolder named same as product name
- `documentation/codex_runbook.md`: consolidated iteration rules + QA intent

## Setup

1. `python -m venv .venv`
2. `source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. `cp .env.example .env` and update the values

## Run (dry run)

`python -m rh_piping --dry-run`

## Fast iteration (reuse last API result)

If you want to tweak post settings without re-calling the API, set `SKIP_API_CALLS=true`.

## Pipe-path QA scoring (optional)

If `PIPE_PATH_PNG` is set (see `.env.example`), each run writes:
- `output/<product>/recent/returned/post/*pipe_path_score*.json`
- `output/<product>/recent/returned/post/*cal_mask_pipe_path*.png`

This does not change masking/editing; it only helps quantify “non-piping pixels unchanged” vs “piping path changed”.

### 12-iteration sweep helper

`python3 scripts/pipe_path_sweep.py --pid 7 --product "Provence_Sofa112in_NaturalWeave_prod34270121_F_CC" --iters 12 --skip-api-calls`

## Run using Prompt found in prompts/ with PID #

`python -m rh_piping --pid 1`

## Run in bare mode (no masking; keep size/alpha safety rails)

`python -m rh_piping --pid 1 --bare`

## Run for product

`python -m rh_piping --product "Provence_Sofa112in_NaturalWeave_prod34270121_F_CC"`

## Results count

Exactly 1 result per run (don’t add `--results` unless explicitly requested).

## Output product image name format
[product name from donor image]_PID-[prompt pid used in geenration]_MOD-[ai model used in generation]_R-[result number]
