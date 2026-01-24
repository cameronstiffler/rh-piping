Goal: Replace only the piping material on the donor furniture image with the material from the color reference image.
Inputs:
- Donor image: the product photo that must stay otherwise unchanged.
- Color reference image: the new piping material/color to apply.

Rules:
- Change only the piping material around seams/edges; leave the base upholstery, frame, legs, background, and lighting unchanged.
- Preserve the donor image crop, camera perspective, scale, and proportions exactly; do not zoom or crop.
- The entire furniture piece must remain fully visible within the original donor canvas.
- Do not add or remove any objects, logos, or text.
- Keep textures, stitching, and seams realistic; avoid halos or color bleed.
- Preserve the donor image transparency/alpha; keep the transparent background exactly as provided.

Output:
- Return a single 4K image with the same aspect ratio as the donor.
- Output in Adobe RGB (1998).
