PROMPT: Color Replacement of Seam Piping Only (Strict No-Redesign Policy)

Task Objective:
High-fidelity image manipulation of the sofa in the Donor Image. Replace the color and surface material of the seam piping (stitched cylindrical trim) with the appearance of the swatch in the Color Reference Image. Only the piping is to be altered. All other aspects of the image must remain completely unchanged.

⸻

🧠 Core Behavior (What to Change):
	•	Detect the seam piping across all cushions and sofa areas.
	•	Change its color and surface texture to match the swatch.
	•	Integrate changes naturally under existing lighting — do not recalculate global shadows or lighting.
	•	Piping appearance must reflect the material quality (e.g. matte, glossy, textured) of the swatch.

⸻

❌ Strict No-Change Zones (What Must Stay Identical):
	•	The sofa model, shape, dimensions, frame, silhouette, legs, and cushions must remain pixel-exact to the Donor Image.
	•	No cropping: The entire sofa must remain fully in frame, same horizontal span and alignment as the Donor Image.
	•	No resizing: The sofa’s pixel width and height must be identical to the Donor Image.
	•	The sofa lighting and shadows must remain unchanged.
	•	The background must be a solid, flat white (#FFFFFF), fully opaque — no gradients, textures, or transparency.
	•	No stylistic enhancements, re-interpretations, or structural edits are allowed.

⸻

📐 Framing and Canvas Rules:
	•	Output image must maintain the same horizontal alignment and pixel dimensions of the sofa.
	•	The canvas may be taller than the donor image only if needed, and extra space must only be added above or below the sofa — never crop or truncate it.
	•	The output may be cropped in post, so slight vertical padding is acceptable — but horizontal positioning must match exactly.

⸻

🎨 Technical Specifications:
	•	Color Space: Adobe RGB (1998)
	•	Transparency: None. Output must be fully opaque with a solid white background (#FFFFFF).
	•	Output Integrity: Ensure the output is a drop-in replacement for downstream use where sofa alignment and scale are critical.

⸻
