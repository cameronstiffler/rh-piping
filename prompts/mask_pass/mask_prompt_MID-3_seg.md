Task: Generate segmentation masks for the piping seams only from the Donor Image.

Output Format: Return JSON only (no markdown). Provide a JSON array with exactly ONE mask object. The mask object must include:
- label: "piping"
- box_2d: [ymin, xmin, ymax, xmax] normalized to 0–1000 (as in Gemini segmentation docs)
- mask: { "rle": { "size": [64, 64], "counts": [ints], "order": "row-major" } }

Mask Rules: The mask must be binary (white = piping seams only; black = everything else). Trace the piping seams exactly as they appear on the donor. Do not include fabric panels, cushions, frame material, or background. The mask must stay strictly within the visible sofa silhouette; anything outside the sofa must be black. No shading, no gradients, no text.

RLE Rules: counts must start with background (0) and alternate background/foreground. Use row-major order. The mask size must be exactly [64, 64] and the counts must sum to 4096. Do not exceed 4096. Keep the JSON compact.
Size Constraint: Use a tight box around the combined piping region. Do NOT return a full-frame mask. Because the mask is low-res (64x64), make piping slightly thicker so it is still visible when upscaled.
