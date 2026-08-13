All paths relative to `/Users/keegshaumann/Documents/GitHub/marketing-socialmedia-dynamicauctioneers`.

# Build plan — client fix list (38 items)

## 0. Housekeeping, do this in the first commit

- `SPEC.md:321-322` has **two rows numbered D56**. D57 exists only as commit `dd12fb5` and is cited as binding by D61 and playbook §11. Renumber before any decision below is logged.
- `docs/INFO-PACK-PLAYBOOK.md` §11 currently reads as "no valuation or comparable figures" and sweeps up DP2777's "Offers from R750,000". The rule that actually holds, and that D64 enforces: **a typed asking/offers figure is allowed everywhere; a municipal, professional, forced-sale or comparable valuation is allowed nowhere.** Reword it.

---

## 1. Decide first

No code on these until answered. Ranked by blast radius.

### Q1 — Info pack: portrait like DP2987, or landscape as built? (blocks 4.1; governs 3.3, 3.5, 3.6, 3.7, 4.2–4.5, 5.1, 5.2)
`designs/incoming/DP2987 - INFO PACK.pdf` is **596×842pt portrait, 15pp**. D61 locked A4 **landscape** off four packs (DP2674/DP2777/DP2948.1/DP2974, all 842×595), playbook §2 says "Not portrait", and two tests enforce it (`tests/test_render.py:643`, `:1016`). The client's own note says "not portrait" while handing us a portrait reference.
**Recommendation:** stay landscape. Treat DP2987 as a *content and finish* reference (section list, highlights styling, grouped improvements), not a geometry reference. Going portrait discards the whole D60/D61/D63 measured composition system, which is orientation-specific — roughly the last two weeks of pack work.

### Q2 — Is DP2987's Dynamic **Real Estate** branding replacing our pack furniture, or is it layout-only? (blocks 4.1; shapes 4.5, 5.1)
DP2987 carries a black/gold header bar, gold edge gradient, page-number corner and `dynamicrealestate.co.za` on the closing page. Playbook §1/§8 fix the issuer as Dynamic Auctioneers (shield, swoosh, gold hairline frame, verbatim disclaimer entity).
**Recommendation:** layout-only. If they want the Real Estate identity, that is a second brand variant of the pack, priced separately.

### Q3 — May municipal / professional valuations and a projected rental ROI appear in buyer-facing packs? (owner-level; blocks 4.1's per-portion table, 5.3c)
DP2987 prints "MUNICIPAL VALUATION R4 700 000" per portion. `engine/schema.py::_strip_internal_strategy` (362-383) physically removes those from `public_view` before any renderer or the copy model sees them — the implementation of D54 and D57, written after the copy model was caught emitting "Municipal valuation R960 000 (2024)" onto a live ad. Playbook §7 forbids "forecast, yield, promise"; there is no rental field anywhere on the schema, so an ROI row would breach hard rule 3.
**Recommendation:** no. Keep the strip. If the owner reverses it, log it as a new decision with a named source for every figure.

### Q4 — New photo cap, per property and per gallery? (blocks 1.1, 1.4; shapes 1.2, 1.3)
`_MAX_PHOTOS_TOTAL = 8` (`webapp/routes/gates.py:731`, D52). Three named galleries cannot fit in 8, and multi-folder upload is pointless when batch 2 is rejected. One number unblocks two items.
**Recommendation:** 40 per property, no per-gallery cap. Re-log D52's rationale. Note the second-order effect: raising it makes the pack's multi-page gallery live for the first time (`gallery = photos[1:]` is currently ≤7 so `batch(9)` never yields page 2), so `tests/test_render.py:643` must be re-run against the new page counts.

### Q5 — Does the typed headline replace or supplement the locality + descriptor lines? (blocks 2.2, 2.3)
Confirmed: with a headline set, it renders only in Collage. `hero_overlay` (the **default**), `stats_first` and `feature_list` print `place_line` + `descriptor_line` instead — D43's deliberate choice, copied from their own AD 2/AD 3. So Auto-generate currently changes nothing on three of four designs.
**Recommendation:** headline **replaces** `descriptor_line` when the marketer has typed one, and `place_line` stays. Preserves D43's look on untouched properties, makes the field do something.

### Q6 — "Morningside, Sandton": where does the town come from? (2.3)
`_place_line()` uses `identity.municipality`, which yields "City of Johannesburg Metropolitan", not "Sandton". `Identity` has no town/city field.
**Recommendation:** an editable town field on the gate-2 form, defaulting to today's derivation. Deriving it from source docs is an extraction change.

### Q7 — Which email addresses may appear on an ad, and is it per-property or per-user? (2.9)
Hard rule 1 exists to keep a private individual's address off a public ad; a free-text box re-opens it. D51 already resolved the mailbox question (`properties@` and `properties.admin@`; the ad bar keeps `properties@` only for width). Their own reference ads use `properties.admin@`, which the code holds as `BRAND['email_admin']` and never prints.
**Recommendation:** a two-option picker (`properties@` / `properties.admin@`), per property, defaulting to `properties@`. Not free text.

### Q8 — "Drag to rearrange": photos, or ad elements? (2.4)
Reordering photos is small. Free layout editing gives up "every ad renders identically from one record" (M5) and undoes D41/D48/D49/D53.
**Recommendation:** photo reordering. If they genuinely want a canvas, that is a separate product decision.

### Q9 — Three ad variations: three different designs, or three variants of one? Which is the primary emailed PNG? (2.1)
Affects fmt keys, filenames, `manifest.json`, the gate-2 gallery, PNG export and the GHL attachment path. Also tripling every gate-2 render gives back D56's 20s→0.0s win.
**Recommendation:** three different designs, auto-picked, generated **only on an explicit "Generate variations" button** (not on every save), with the marketer nominating the primary.

### Q10 — What does "DOP banner" name? (2.10)
Nothing in the tree is called DOP. Most likely the white shield lockup: it exists on `feature_list`/`hero_overlay`/`stats_first` with **no shadow**, is absent from Collage entirely, and their reference ads show it with a soft shadow.
**Recommendation:** confirm it is the shield. Build note: `box-shadow` is clipped by `clip-path` — the shadow must be `filter: drop-shadow()` on a wrapper.

### Q11 — Does the pack ignore gate-2 address edits, or just stop repeating the address? (3.3)
Doubling is real on the golden record. But D17 applies `human_overrides` last inside `public_view()` so one edit reaches every artifact; making the pack ignore them produces an ad and a pack with different addresses — structurally the fault D64 fixed.
**Recommendation:** layout-only. Stop echoing the address block, drop the title-deed line, keep the override path.

### Q12 — Tenure/freehold: does D35's scope change? (3.4)
D35 is owner-confirmed and explicit: precedence arbitrates **physical facts only**; Lightstone owns erf, **title type**, ownership. This item reverses that.
**Recommendation:** don't reverse it. Ship the cheap fix instead — `identity.title_type` is not in `_EDIT_TEXT_FIELDS` and gets no gate-1 source picker, so a wrong "Freehold" is uncorrectable short of a DB edit. Also ask for the actual mis-sourced example; nobody has seen the failing case.

### Q13 — Viewing modes: exact standing wording, and is a set viewing a slot or a window? (4.6)
### Q14 — Multi-property board: sub-lots of one instruction (already linked by `parent_dp`), or any set of DPs on one auction date? (6.4)
### Q15 — Board weekday: does the marketer type it, or does `sale_process.auction_date` become a real date field? (6.7)
Today it is free display text (D42), so "THURSDAY" cannot be derived. **Recommendation:** marketer types it; a real date field ripples into extraction, gate 2 and every artifact.

### Q16 — GHL QR: does the sub-account's LeadConnector v2 API expose a QR endpoint, or is it the funnel widget / a pasted trigger link? And what does the QR resolve to? (6.3)
`engine/distribute/ghl.py` wires media upload and social posts only. **Recommendation:** if it resolves to a plain URL, generate locally with `segno` (error-correction H, logo punched into the centre, embedded as a data URI like the existing logos) and keep the board self-contained for rasterising.

### Documents to request in the same email
1. A sample **OTP / Conditions of Sale** (2-3, across versions, to confirm clauses 3.1/3.2/14/20/21 are stable) — blocks 3.1, 3.2.
2. A **Rates & Taxes / municipal statement** — blocks the second half of 3.1.
3. One **multi-dwelling property's full source pair** (EVM + Property Report, ideally + valuer's report) — blocks 2.6, 4.1.
4. One **multi-portion instruction whose sources carry per-portion detail** — blocks 3.7, 3.6. D50 shipped multi-portion intake without ever seeing one.
5. An existing pack showing grouped **"Photographic Overview"** pages — that phrase appears nowhere in the repo, so the page grammar is unknown (1.4).

### Environment blocker
`~/Documents/dynamicAuctioneers/` **does not exist on this machine.** 14-15 tests already skip for this. No live extraction, re-extraction or prompt work can start until the folder is restored or the repo is pointed at OneDrive. This compounds 2.6, 3.1, 3.2, 3.4, 3.7, 5.3.

---

## 2. Already true — do not rebuild

| Item | What already works | Evidence |
|---|---|---|
| **1.1 (append half)** | The server already appends; same-named files from different folders survive as `front.png` / `front_1.png` | `gates.py:856-920` loads the existing list and appends; `tests/test_webapp.py:1559` |
| **1.2 (grid half)** | Responsive tile grid with 120px cover thumbs, LEAD badge, low-res warning + pixel dims, Lead/Remove | `_gate2_photos.html:28-56`, `app.css:721-736`, `gates.py:784-806` |
| **2.3 (two-part line)** | A "Suburb, X" place line renders on 3 of 4 designs | `html_backend.py:461-472`, `:317` |
| **2.5 (speed half)** | Pre-approval renders only `demo_ad` (D39) with copy served from cache; D56 measured a design switch at **0.0s** | D39, D56 |
| **2.10 (shield half)** | The white shield lockup exists on `feature_list`, `hero_overlay`, `stats_first` via `clip-path` | `feature_list.html.j2:41-44`, `hero_overlay.html.j2:32-35`, `stats_first.html.j2:29-32` |
| **3.2 (terms half)** | The terms box is record-driven — DP3060 carries deposit %, commission %, guarantee days, occupation | `info_pack.html.j2:520-524` |
| **4.5 (editable-text half)** | Pack text is **real embedded Montserrat Type0 subsets, fully extractable**; frame, swoosh and feature glyphs are vector paths | verified in the rendered PDF |
| **4.5 (background half)** | Interior pages have **no background image** — flat white + vector swoosh + gold hairline frame. DP2987 is the document with full-bleed raster grounds | `info_pack.html.j2`, playbook §2 |
| **5.1 (table half)** | A bordered two-column highlights table already exists in the playbook §5.5 shape | `info_pack.html.j2:396-399`, `:657-678` |
| **6.2 (both pills)** | The board already prints MASTER REF and PROPERTY REF, wording matches | `auction_board.html.j2:126-129` |
| **6.5 (m² half)** | A size line renders on every board (`±111m² Property`) | `auction_board.html.j2:138` |
| **6.6 (beds render)** | Beds render twice already — a "3 Bedrooms" stat row and a "3 Bedroom Home" descriptor | `auction_board.html.j2:139`, `:134` |
| **1.4 / 6.4 (single case)** | Single-property, single-gallery is complete — that is all the render contract supports | `base.py:35-53` |

---

## 3. Do now

Ordered. Ship each batch as one commit.

### Batch 1 — trivial, zero blockers (half a day total)
| Item | What | Effort | Files |
|---|---|---|---|
| 2.11 | Phone left, email right: `justify-content: space-between` on `.ig-contact`, drop the 44px gap | S | `ads/collage.html.j2:59-64`, `feature_list:84-88`, `stats_first:60-65`, `hero_overlay:61-65`; same in `saia_banner.html.j2:157`, `auction_board.html.j2:161-168` |
| 2.8 | U+00A0 inside grouped numbers; wrap number+unit in one `white-space:nowrap` span; keep the wrap opportunity *before* the number | S | `render/service.py:83-85` `_rand`, `html_backend.py:150-165` `_fmt_size`, all four ad templates, `collage.html.j2:104` (currently two flex children with a visible gap) |
| 3.5 | Delete the coordinates line; optionally drop `gps_str` + `_gps_str`. **Keep** `verify.py:270-282` MISSING_GPS and the memo row — internal, not buyer-facing | S | `info_pack.html.j2:541-543`, `html_backend.py:332`, `:425-438` |
| 4.3 | Delete the `.footline` block **and** drop `FOOTNOTE = 96` from the first features page budget, or it over-reserves 9.6mm. No test asserts the string. Came from D61 (`88a7f64`), not D63 | S | `info_pack.html.j2:623-625`, CSS `:372-375`, `:91`, `:601`; playbook `:115-117` |
| 4.2 | Rename "Also included:" → "Additional Features:". Ask whether "External and shared:" (what the golden record actually prints) changes too | S | `info_pack.html.j2:618` |

D64's guard already accepts both `R1&#160;875&#160;000` and the UTF-8 NBSP form, so 2.8 is safe.

### Batch 2 — ad clipping and units (1 day)
| Item | What | Effort | Files |
|---|---|---|---|
| 2.12 | Long real values are forced onto one line then ellipsised — Collage's tagline box runs to x=1412 on a 1080 canvas, Stats-first reaches x=1099. Let these blocks **wrap or shrink-to-fit** (as `auction_board.html.j2:21` already does for long names) instead of `white-space:nowrap + overflow:hidden + text-overflow:ellipsis` | M | `collage.html.j2:49-53,97,108`, `stats_first.html.j2:49,90-94`, `feature_list.html.j2:70,118-122`; `.ig` overflow at each `:18-26` |
| 2.7 | Drop `text-transform:uppercase` from the stat rows so `m²` and `ha` stay lowercase; surface `size_ha` on the ads above a threshold. **Not buildable as stated:** extents are stored in m² only, so the *source* unit is not preserved — "ha stays ha" needs a schema field (see §4). `_fmt_ha` rounds to one decimal, so it prints "60.0 ha" where they want "59.96 ha" — fix the precision | M | `hero_overlay.html.j2:46,87`, `stats_first:48,90`, `feature_list:68-69,118`, `collage:58,104`; `html_backend.py:168-181` |

Guard both with the D63 pattern: measure text client rects in Chromium and assert no clipping — reuse `tests/test_render.py:566-610`. Log the lowercase change as a **deliberate divergence from D43/D49** (their reference ads print `±81M²` in caps) so nobody "fixes" it back.

### Batch 3 — photo panel UX (1-2 days; needs Q4 for the cap)
| Item | What | Effort | Files |
|---|---|---|---|
| 1.1 | (a) Attach the existing `[data-dropzone]` wiring to the photos panel — it works, it is just only mounted on intake. (b) The native input **replaces** its FileList on each browse, so picking folder A then folder B loses A before Upload is pressed — fix with an accumulating `DataTransfer` buffer so the pending banner shows the running total. (c) Raise the cap per Q4 | M | `app.js:100-131` `wireDropzones`, `:351-383` `wirePhotoPicker`; `intake.html:14-25` (markup to copy); `_gate2_photos.html:3-15`; `gates.py:731` |
| 1.2 | Click-to-enlarge lightbox + larger tiles | S | `_gate2_photos.html:28-56`, `app.css:721-736` |
| 1.3 | Checkbox multi-select + bulk remove. **Load-bearing constraint:** every photo action posts `hx-target="#photos" hx-swap="outerHTML"`, so the whole panel DOM is destroyed on each action — hold the selection in a JS `Set` keyed on photo name **outside** `#photos` and re-apply on `htmx:afterSwap`. That is exactly the bug the item describes | M | `_gate2_photos.html:39-52`, `gates.py:923-965` (single-name endpoints today), `:831-843` `_photo_result`, `app.js:346-384` |
| 2.5 | Per-tile **Replace** action; restrict the post-approval re-render to the ad (`_save_photos` passes `formats=None` today, so one photo swap rebuilds all nine artifacts including the Chromium-printed PDF — that is the slowness they mean). Build D56's already-owed **batch edits + explicit Regenerate**, not partial rendering | M | `gates.py:809-828` `_save_photos`, `:156-159` `_formats_for_state`, `:922-962` |

### Batch 4 — gate-2 editable fields (1 day; needs Q5, Q6, Q7, Q10, Q12)
One form panel, one pass over `_EDIT_TEXT_FIELDS` (`gates.py:965-979`), one pass over the view model.

| Item | Field | Effort |
|---|---|---|
| 2.3 | optional town/place field, defaulting to today's `municipality` derivation | M |
| 2.2 | headline slot on `hero_overlay`, `stats_first`, `feature_list` per Q5's ruling | S |
| 2.9 | mailbox picker (`properties@` / `properties.admin@`), honoured by `vm.brand_email` | S |
| 3.4 | `identity.title_type` editable — interim fix while Q12 settles | S |
| 6.2 | `identity.mandate_ref` typeable (`override_key_allowed` already returns True for it; it is extraction-only today) + a `ref_mode` on `marketing` (both / master / property). **Default "both"**, logged against D42 | M |
| 2.10 | shield shadow via `filter: drop-shadow()` on a wrapper; add the shield to Collage, which has none | S |

### Batch 5 — info pack finish (needs Q1 = landscape; 1-2 days)
| Item | What | Effort |
|---|---|---|
| 5.2 | Remove the "How it is offered" row **and relocate `price_display` in the same commit** — `info_pack.html.j2:138` is the pack's **only** price line, so a naive delete strips money from the whole buyer pack and fails `tests/test_render.py:523 test_a_price_reaches_every_artifact`, re-creating the exact fault D64 fixed two days ago. Move it to the Location details page (`:513-517` or `:529-539`). Drop the auction-branch row at `:136` too — it duplicates the date already in `ld-when`. Lower the `show_highlights = (hl|length) >= 5` gate at `:150`, or a sparse property falls from 5 rows to 4 and loses the whole page | S |
| 5.1 | Restyle highlights to the DP2987 finish: alternating cream row tint, warm gold hairlines instead of `#222`, `text-transform:uppercase` on the label cell, ~40% label column, centre the title (`.ptitle--mid` exists and is unused). `print-color-adjust: exact` is already set at `:222` so the tint will print. **Copy the finish, not the geometry.** Update `docs/INFO-PACK-PLAYBOOK.md:131-134` ("alternating rows left plain") in the same commit | S |
| 3.3 | Layout half only per Q11: stop echoing the address block, delete the title-deed line and the schedule's deed column | M |

Row padding and the fill-band maths at `:666-667` **are** the D60/D63 measured composition — change padding or font-size only with `test_info_pack_pages_are_full_a4_landscape_sheets` re-run.

### Batch 6 — auction board rebuild (3-4 days; needs Q15, Q16)
**Ship 6.1, 6.6, 6.7, 6.3 and the board half of 6.2/6.5 as one rebuild.** Doing them separately means rebuilding the same CSS four times.

The current board is a different design end to end: 1620×1080 landscape, hero photo in a gold-framed 600px column, auction line in a bordered money box near the foot, gold contact bar. The reference (`designs/incoming/DP2817.3 - AUCTION BOARD.png`) is **1200×1600 portrait, zero photography**: black header bar carrying channel/day then date@time in huge type, ref pill top-right, three stacked summary lines, a black/white boxed "LIQUIDATION AUCTION" banner (not a gold pill), centred QR, phone, black footer with the horizontal DS lockup.

Reusable as-is: `badge_text` already yields "LIQUIDATION AUCTION"; `money_text` yields "ONLINE AUCTION | 28 MAY 2026 @ 10:00" and only needs resplitting plus the weekday; `parts.brand_logo(vm,'dark')` already resolves the white+gold lockup, so no new asset is needed; `artifact_thumbs.py:164` captures `.board`, so the preview keeps working. The A3-landscape `@page` block and the zoom steps must be redone with it.

New in the rebuild: `beds_short` ("3-BED") in the view model as the top summary line (6.6 — note "always" is not true today, both forms are guarded on `vm.beds` and degrade to "Property"); a board-type picker at gate 2 for industrial/commercial/agricultural (6.5) with `size_ha` at real precision; `ref_mode`; the QR per Q16.

Files: `engine/render/templates/auction_board.html.j2` (whole file), `html_backend.py:298-398`, `webapp/templates/gate2_ads.html:119-145`.

---

## 4. Needs a bigger change

Schema, extraction, or a new integration. None of these is a template edit.

### The one build that serves five items — per-dwelling / per-portion detail on the record
Blocks **2.6, 3.6, 3.7, 4.1's per-portion pages, 1.4** (if galleries key off portions), **4.4**, and D61's already-owed multi-unit pack. **Do not scope it per item.**

Root cause: `Physical` (`engine/schema.py:142-165`) holds **one** bedroom/bathroom/garage count plus at most one `Flatlet`, so a third house has nowhere to live and is silently dropped — exactly the reported symptom. `Portion` (`:95-110`) carries `label/erf/size_m2/title_deed_no/note` — **land, not buildings**.

Work: extend `Portion` with a physical block and/or add a `dwellings` list to `Physical`; matching section brief in `engine/extract.py:238-250`; `resolve_physical_conflicts` handling; then per-portion/per-dwelling rendering in every ad design and the pack. Blocked on the real source pair (document request 3 and 4) and on the restored `~/Documents/dynamicAuctioneers/`.

Separate, cheap, and reproducible on **any** property: `feature_list`/`stats_first`/`hero_overlay` print "3 BEDROOM HOME" as the descriptor and then "3 BEDROOMS" as a stat row on the same ad. Fix that in Batch 2.

### Items with no foothold today
| Item | Why it is big | Blocked on |
|---|---|---|
| **1.4** named galleries | `Marketing.gallery` is one flat list; changing it to named groups ripples through `public_view`, `apply_photos`, the render view model, the info pack template, and back-compat (`RecordStore.get` already strips legacy fields, so a migration path exists) | Q4, Q14 (naming vocabulary), document request 5 |
| **2.1** three ad variations | Touches artifact identity: fmt keys, filenames, `manifest.json`, the gate-2 gallery, PNG export, the GHL attachment path. `FORMATS` has one `demo_ad` key | Q9 |
| **2.4** element drag-and-drop | Needs a persisted per-property slot model, a sortable UI, and every template rewritten to read slot assignments instead of fixed positional slices (`hero_src`, `stack_photos=[1:3]`, `gallery_photos=[3:7]`) | Q8 — confirm they don't just mean photos |
| **2.7** literal unit preservation | "ha stays ha" needs a source-unit field on the record. "Show ha above a threshold" is derivable today | Q — which one |
| **3.1** block pack on OTP + Rates | Intake knows exactly three document kinds and completeness is EVM + Property Report only; the web intake proceeds even when incomplete. Needs new classifier markers, a completeness rule, and a decision on whether the block sits at intake or only on the `info_pack` format | Document requests 1 and 2 |
| **3.2** OTP clause extraction | `SaleProcess` has only `terms: List[str]` free text — nothing can assert "deposit = 10%". The two pills bracketing the terms box are **literal strings** with "30 days" baked in, so a 45-day property prints the wrong terms | Document request 1 + confirmation that clause numbering is stable across OTP versions |
| **4.1** DP2987 layout | TOC, Property Overview, Property Description, Property Condition, Farming Operation and Highest & Best Use have **no record fields and no template blocks**. Schema + extraction + full template rebuild, not a restyle | Q1, Q2, Q3, plus "who writes Condition / Farming / Highest & Best Use" |
| **4.4** grouped improvement pages | Pagination splits arithmetically, never by theme; layout is glyph-left/label-right rows where DP2987 is centred-icon-above-label. `pack_icons.py` has 43 residential glyphs and **nothing** for greenhouse/tunnel/borehole/dam/grazing — a farm pack draws the fallback mark on most rows | Whether the group is extracted per feature or inferred from wording, and the group vocabulary |
| **4.6** viewing modes | Viewing is one boolean today and there is no viewing control anywhere in the web app — marketing cannot set it at all. Needs a schema enum + date/time fields, a gate-2 panel with allow-listed values (copy the auction-channel guard), and per D64 the fact must reach **all** artifacts that mention viewing, not just the pack | Q13 |
| **5.3** investor highlights | Split three ways. (a) Model-written highlight rows: add `highlights: List[{label, detail}]` to `CopyBundle`, brief it in `SYSTEM_PROMPT`, render when present, fall back to the derived list — **M and safe, do this one**. (b) Positive tenancy framing: no occupancy/tenancy/lease field exists at all — schema + extraction. (c) Projected ROI: see Q3 | Q3 for (c); a tenancy statement convention for (b) |
| **6.3** QR | No QR library in `pyproject.toml`/`requirements.txt`; `engine/distribute/ghl.py` wires only media upload and social posts, so "via the GHL API" cannot be taken at face value | Q16 |
| **6.4** multi-property boards | The whole render contract is one record in, one artifact out (`RenderRequest` carries a single `dp`). Needs a render entry point taking N `public_view`s, a new template, a property picker, and somewhere for the group to live. `store.py` has a `parent_dp` column it never queries | Q14 |