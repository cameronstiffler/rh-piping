Goal: Keep the donor image identical while swapping only the piping material to match the color reference image.
Inputs:
- Donor image: base product photo; keep every pixel outside the piping identical to this image.
- Color reference image: target piping material/color.

Rules:
- Piping only: update the piping material around seams/edges to match the reference color and luminance; keep piping shape, width, placement, stitching, and texture the same as the donor.
- Every pixel outside the piping is an exact match to the donor image (upholstery, frame, legs, background, lighting, shadows).
- Preserve the continuous dark shadow running along the full underside between the couch feet exactly as in the donor image.
- Match the donor canvas exactly: same crop, camera perspective, scale, proportions, dimensions, and resolution; full furniture stays in frame.
- Preserve the donor transparency/alpha exactly.
- Overall output is at least 97% identical to the donor image by pixel match, with the piping change as the only difference.

Output:
- One 4K image with the same aspect ratio as the donor, Adobe RGB (1998).
