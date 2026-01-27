Task: High-fidelity piping color swap on the sofa in the Donor Image using the Color Reference Image.

Inputs:
- Donor Image: source sofa photo (ground truth).
- Color Reference Image: target piping material/color.

Core Rules:
- Piping only: update piping color/material to match the reference; piping shape, width, placement, stitching, and texture match the Donor Image.
- All non-piping pixels match the Donor Image exactly (sofa body, frame, background, lighting, shadows, texture, seams).

Framing and Scale:
- Entire sofa fully visible in frame.
- Sofa scale matches the Donor Image exactly; sofa pixel width and height match the Donor Image.
- Sofa spans the same horizontal extent as the Donor Image; left and right margins match the Donor Image.
- Output canvas may be taller than the Donor Image; any extra space is added only above and/or below the sofa.

Technical Requirements:
- Color Space: Adobe RGB (1998).
- Output image may be taller than the Donor Image while preserving the sofa’s original pixel size.
- Preserve transparency from the Donor Image if supported by the output format.
