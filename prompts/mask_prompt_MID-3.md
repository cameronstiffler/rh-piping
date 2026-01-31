Task: High-fidelity image manipulation of the sofa in Donor Image. Change appearance of stitch seam piping material on sofa upholstery to match swatch in Color Reference Image. Only the color of the seam piping material is changed and the rest of the Result Image remains identical to the original Donor Image.

Core Subject: The pipes on the sofa upholstery from the Donor Image need to be found and surgically replaced with pipes whose color match the color of the swatch in Color Reference Image. 

Lighting and Atmosphere: There is no need to recalculate lighting. We only want the piping to appearance to match the color of the swatch. Outside of the pipes, the rest of the Result Image must remain the same as the original Donor Image. The sofa lighting and shadows must remain identical to the Donor Image. The background must be a solid, flat white (#FFFFFF), fully opaque — no gradients, textures, or transparency. Keep the exact donor contact shadow under the sofa; mimic its shape, position, and softness.

Strict Requirements: Everything about the sofa and background remains exactly like the Donor Image. Only the piping on the sofa upholstery may be changed. Only modify pixels where the mask is white; do not change anything outside the mask and do not infer seams beyond the mask.
Framing Requirement: Entire sofa fully visible in frame; no part of the sofa is cropped or cut off.
Scale Requirement: Sofa scale matches the Donor Image exactly; the sofa’s pixel width and height match the Donor Image.
Width Alignment: Sofa spans the same horizontal extent as the Donor Image; left and right margins match the Donor Image.
Canvas Flexibility: The output canvas may be taller than the Donor Image; any extra space is added only above and/or below the sofa.


Task: Find and map the areas occupied by the uphostery piping in the Sofa/Donor Image. Create binary Mask Image based on the pipe covered areas on the sofa. Pipes are typically follow the . Binary Mask image must be the same dimesions and aspect ratio as the Donor Image and sofa subject within. THe White sections of the Mask Image must occupy the same coordinates and dimensions as the donor image piping fseams only; black = everything else. Trace the piping seams exactly as they appear on the donor. Do not include fabric, cushions, frame, or background. No shading, no gradients, no text.

Techinical Requirements:
Image Dimensions: Same dimensions as the Donor Image.
Transparency: None. Output must be fully opaque with a solid white background (#FFFFFF).
