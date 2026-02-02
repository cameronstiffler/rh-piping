Task: Return a binary mask image for the sofa cushions only. White = cushion bodies (back + seat). Black = everything else. The mask must stay strictly within the visible sofa silhouette; anything outside the sofa must be black.

Core Subject: Create a mask where the white section is defined by the cushion bodies only (no arms, frame/base, piping, fabric outside cushions, or background).

Technical Requirements:
Image Dimensions: Must be exactly {{MASK_WIDTH}}x{{MASK_HEIGHT}} pixels (same as the donor image size for this run).
Binary Only: Pure white (#FFFFFF) for cushions and pure black (#000000) for everything else.
