# rh-piping

Python tooling for piping-focused furniture edits via AI image APIs.

## Layout

- `assets/original/donor_image/`: primary product images to edit
- `assets/original/color_reference/`: fabric/finish reference images
- `assets/original/piping_ref_images/`: piping style reference images
- `prompts/`: prompt templates
- `output/`: generated results

## Setup

1. `python -m venv .venv`
2. `source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. `cp .env.example .env` and update the values

## Run (dry run)

`python -m rh_piping --dry-run`
