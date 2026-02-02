Task: Return a binary mask image for the sofa piping only. The mask should be almost entirely black, with thin white lines only where the seam piping material should appear (including the top back-cushion piping). Black = everything else. The mask must stay strictly within the visible sofa silhouette; anything outside the sofa must be black.

Core Subject: The pipes on the sofa upholstery from the Donor Image must be found and isolated. Ensure the top edge piping on the back cushions is visible and continuous, along with the seat front/back piping and the vertical seam piping between cushions. Only the piping should be white. Everything else (cushions, arms, base, frame, background) must be black. If a pixel is not clearly piping, it must be black.

Strict Requirements: Only mark the piping seams/corded edges that match the donor image. Do not infer or invent new piping beyond what exists in the donor. Keep the mask tightly confined to the piping pixels. All white pixels must lie within the cushion area (if unsure, keep black).

Technical Requirements:
Image Dimensions: Must be exactly {{MASK_WIDTH}}x{{MASK_HEIGHT}} pixels (same as the donor image size for this run).
Binary Only: Pure white (#FFFFFF) for piping and pure black (#000000) for everything else.
