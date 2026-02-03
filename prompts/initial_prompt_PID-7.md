Task: High-fidelity image manipulation of the sofa in Donor Image. Change appearance of stitch seam piping material on sofa upholstery to match swatch in Color Reference Image. Only the color of the seam piping material is changed and the rest of the Result Image remains identical to the original Donor Image.

Core Subject: The pipes on the sofa upholstery from the Donor Image need to be found and surgically replaced with pipes whose color match the color of the swatch in Color Reference Image. 

Reference Example: Use the unmarked example at `assets/original/piping_ref_images/Antibes_AluminumDiningSideChair_White_prod39010446_E25624663178_TQ_CC.png` (this is the first file in that folder). Do not rely on the “highlighted” folder or any markup overlays; the new piping should follow the donor geometry as shown in this clean reference.

Lighting and Atmosphere: There is no need to recalculate lighting. We only want the piping to appearance to match the color of the swatch. Outside of the pipes, the rest of the Result Image must remain the same as the original Donor Image. The sofa lighting and shadows must remain identical to the Donor Image. The background must be a solid, flat white (#FFFFFF), fully opaque — no gradients, textures, or transparency. Keep the exact donor contact shadow under the sofa; mimic its shape, position, and softness.

Strict Requirements: Everything about the sofa and background remains exactly like the Donor Image. Only the piping on the sofa upholstery may be changed. Only modify pixels where the cushion mask is white; do not change anything outside the mask and do not infer seams beyond the piping. The donor piping mask is applied during the final composite so donor cushions stay untouched, and the model cushion mask remains active while generating the edit.
Framing Requirement: Entire sofa fully visible in frame; no part of the sofa is cropped or cut off.
Scale Requirement: Sofa scale matches the Sofa in the Donor Image exactly; the sofa’s pixel width and height match the sofa in the Donor Image.
Width Alignment: Sofa spans the same horizontal extent as the Donor Image; left and right margins match the Donor Image.
Canvas Flexibility: The output canvas may be taller than the Donor Image; any extra space is added only above and/or below the sofa.

Techinical Requirements:
Color Space: Use Adobe RGB (1998).
Resolution: 300 DPI.
Image Dimensions: Overall output image may be taller than the Donor Image while preserving the sofa’s original pixel size.
Transparency: Transparencies in donor image must all be preserved.
