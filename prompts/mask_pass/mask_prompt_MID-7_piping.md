Task: Return a binary mask image for the sofa piping only. White = piping where the seam piping material should appear. Black = everything else. The mask must stay strictly within the visible sofa silhouette; anything outside the sofa must be black.

Core Subject: The pipes on the sofa upholstery from the Donor Image must be found and isolated. Only the piping should be white. Everything else (cushions, arms, base, frame, background) must be black.

Strict Requirements: Only mark the piping seams/corded edges that match the donor image. Do not infer or invent new piping beyond what exists in the donor. Keep the mask tightly confined to the piping pixels.

Technical Requirements:
Image Dimensions: Must be exactly {{MASK_WIDTH}}x{{MASK_HEIGHT}} pixels (same as the donor image size for this run).
Binary Only: Pure white (#FFFFFF) for piping and pure black (#000000) for everything else.
