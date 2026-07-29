# Ad-design drop folder

Drop the ad designs you want built into the gallery here, then tell me.

**What to put in:**
- One file per design: a **PNG** or **PDF** export (Canva → Share → Download → PNG/PDF). Highest resolution you can — the clearer the export, the closer the rebuild.
- Name them descriptively if easy (e.g. `modern-gold.png`, `dark-minimal.pdf`) so the design's label in the app reads nicely. If not, I'll name them.
- Optional: a note (a `.txt` next to it, or just tell me) for anything non-obvious — e.g. "the big box top-right is the price", "these 3 slots are photos".

**What happens:**
- I read each file directly off your disk (no upload/push needed) and rebuild it as an HTML template that fills with the property's real data (headline, price, photos, stats, features).
- Each finished design drops straight into the Gate-2 "Ad design" gallery with an auto-generated thumbnail.

**Note:** files in this folder are gitignored (not committed) — they're inputs, not code. Only the rebuilt `templates/ads/*.html.j2` templates get committed.
