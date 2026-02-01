# rh-piping

Python tooling for piping-focused furniture edits via AI image APIs.

## Layout

- `assets/original/donor_image/`: primary product images to edit
- `assets/original/color_reference/`: fabric/finish reference images
- `assets/original/piping_ref_highlighted/`: piping reference images with highlighted piping
- `assets/processed/`: generated 4K PNGs derived from original TIFFs
- `prompts/`: prompt templates
- `output/`: generated results in a subfolder named same as product name

## Setup

1. `python -m venv .venv`
2. `source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. `cp .env.example .env` and update the values

## Run (dry run)

`python -m rh_piping --dry-run`

## Run using Prompt found in prompts/ with PID #

`python -m rh_piping --pid 1`

## Run in bare mode (no masking; keep size/alpha safety rails)

`python -m rh_piping --pid 1 --bare`

## Run for product

`python -m rh_piping --product "Provence_Sofa112in_NaturalWeave_prod34270121_F_CC"`

## Run and specify number of results to output 

`python -m rh_piping --results 5`

## Output product image name format
[product name from donor image]_PID-[prompt pid used in geenration]_MOD-[ai model used in generation]_R-[result number]
