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

## Info packs

Same folder, same idea, for the buyer information pack. The four packs dropped here
on 2026-08-06 — `DP2674 - DIGITAL INFO PACK.pdf`, `DP2777 INFO PACK.pdf`,
`DP2948.1 INFO PACK COMPRESSED.pdf` (nine units in one pack) and
`DP2974 INFO PACK.pdf` — are what `docs/INFO-PACK-PLAYBOOK.md` and
`templates/info_pack.html.j2` were rebuilt from (D61). They are the reference for
the pack's look and feel: **if the playbook and one of these packs disagree, the
pack wins.** Keep them here (or ask for them back) before changing the pack design.
