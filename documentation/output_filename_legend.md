# Output Filename Legend

New short output filenames use this format:

```
<prodtag>_<PID>_<modeltag><flags>_R<index>.<ext>
```

Example:

```
ProvenceSo9f3a_PID-5_gemini2a1_FNP_R1.png
```

## Fields

- `prodtag`: Product name truncated to 10 alphanumeric characters plus a 4‑char hash.
  - Example: `ProvenceSo9f3a`
- `PID`: Prompt ID (from the prompt filename).
  - Example: `PID-5`
- `modeltag`: Model name truncated to 6 alphanumeric characters plus a 3‑char hash.
  - Example: `gemini2a1`
- `flags`: Short run flags (see below).
  - Example: `FNP`
- `R<index>`: Result index (incremented for each output).
  - Example: `R1`
- `ext`: File extension (`.png`, `.jpg`, `.jpeg`) depending on model output.

## Flags

Flags appear as a compact block prefixed with `F`:

```
F<letters>
```

Letter meanings:

- `N` = no mask (masking disabled)
- `L` = preserve luminance
- `M` = mask used
- `K` = chroma key enabled
- `B` = Vertex background removal (edit pass)
- `F` = fit‑only
- `P` = no post‑processing (raw output)

Examples:

- `FNP` = no mask + no post
- `FNK` = no mask + chroma key
- `FM` = mask used

If no flags apply, the `F...` block is omitted.
