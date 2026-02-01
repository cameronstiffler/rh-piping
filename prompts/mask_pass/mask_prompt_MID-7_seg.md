Task: Generate a binary mask for the sofa cushions in the submitted Donor Image. Include the back cushions and the seat cushion. The mask should be filled (solid cushion shapes), not just outline strokes.

Output Format: Return ONLY a base64-encoded PNG mask (no JSON, no markdown, no extra text). Data URI is OK but not required.

Mask Rules: The mask must represent only the cushion bodies (back + seat). Exclude arms, frame/base, piping, fabric outside the cushions, and background. The mask must stay strictly within the visible sofa silhouette; anything outside the sofa must be black. The mask must be full-frame (same aspect as the donor image). No shading, no gradients, no text beyond the mask itself.

Size/Resolution: Keep the mask extremely compact. Use a 16x16 mask if possible (preferred), or as low-res as possible. It will be resized to the donor size by the caller.

Binary: This must already be binary. Use pure white (#FFFFFF) for cushions and pure black (#000000) for everything else.
