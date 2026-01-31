Task: Return a binary mask image. White = piping seams only; black = everything else. Trace the piping seams exactly as they appear on the donor. Do not include fabric, cushions, frame, or background. No shading, no gradients, no text. The mask must stay strictly within the visible sofa silhouette; anything outside the sofa must be black.

Core Subject: Create a mask where the white section is defined by the pipe locations on the sofa within the donor image.

Technical Requirements:
Image Dimensions: Must be exactly {{MASK_WIDTH}}x{{MASK_HEIGHT}} pixels (same as the donor image size for this run).
