Task: Return a binary mask for the existing piping on the sofa in the Donor Image. The mask must be almost entirely black with white lines only where the piping appear on the donor. The piping is subtle and close in color to the fabric, so focus on the seam/corded edges and their shadow/relief. Do not add or invent piping.
Important: trace the **inner edge** of the piping where it meets the cushion fabric, then include the full pipe width outward from that inner edge. If the outer edge is unclear, assume a typical pipe width and keep it consistent along the seam. Do not miss piping near the wicker/arm edges.

Core Subject: The existing piping on the donor sofa upholstery. Only those piping seams should be white. Everything else must be black. If unsure, keep black.

Strict Requirements:
- Only mark piping pixels that exist in the donor image.
- Keep the mask tightly confined to piping lines.
- All white pixels must stay within the edges of the overall cushion area.

Technical Requirements:
Image Dimensions: Must be exactly {{MASK_WIDTH}}x{{MASK_HEIGHT}} pixels (same as the donor image size for this run).
Binary Only: Pure white (#FFFFFF) for piping and pure black (#000000) for everything else.
