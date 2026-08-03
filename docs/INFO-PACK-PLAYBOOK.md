# Dynamic Auctioneers — Info Pack Playbook

Rules for building a buyer/auction information pack (PDF) from source data. Derived from the Katlego Sitrus citrus portfolio pack. Follow this unless a specific brief overrides it.

## 1. What it is
A short, premium PDF that markets a listing (property, business, assets) to buyers, issued **as Dynamic Auctioneers' own document**. Never brand it Cognexa. Legal entity for footers/disclaimers: **Dynamic Solutions 1068 (Pty) Ltd t/a Dynamic Auctioneers**.

## 2. Source the data (keep token use low)
- **Deeds / property reports (Lightstone PDFs):** extract text with `pdftotext -layout file.pdf -` rather than reading the PDF visually. Pull: portion, farm, registration division, municipality, GPS coords, registered + cadastral extent, title deed no., owner + company reg, bonds (amount/bank/date/no.), municipal valuation, prior sale price/date.
- **Asset list (xlsx):** read with Python `openpyxl` (`data_only=True, read_only=True`). Note company status (e.g. "in business rescue"), then group ~all line items into a few clean categories with approx counts. Watch for extra sheets belonging to a **different entity** — do not merge those in; flag them.
- **Market/context:** 1–2 targeted `WebSearch` calls for sector stats (e.g. citrus exports, area, jobs) and the current **exchange rate** if converting. Attribute sources.
- **Aerials:** see §7.

## 3. Brand system (locked)
- **Palette (light):** paper `#faf7f0`, paper-2 `#f4efe4`, lines `#e3dbc7`, ink `#211d15`, ink-2 `#574f42`, ink-3 `#8c8371`, gold `#9c7a24`, green accent `#465a30`. (A dark variant exists — `#141109` ground + `#c9a24b` gold — use only if the brief asks for dark.)
- **Font:** Montserrat throughout (installed at `/Library/Fonts`, so Chrome renders it). Headings 700, body 400/500, eyebrows 600 uppercase letter-spaced.
- **Logo:** full black+gold "DYNAMIC AUCTIONEERS" lockup on the cover; gold dragon monogram as a small footer mark; **dark (full) logo on the last/contact page**. Clean logo saved at `~/Downloads/Dynamic-Auctioneers-logo.png`. If a pasted logo isn't on disk, recover it from the session transcript (`~/.claude/projects/<proj>/<session>.jsonl` holds pasted images as base64) and make the white transparent + autocrop.
- **Look:** ivory background, gold accents, hairline borders, generous spacing, serif-free. Keep it easy to read (body ≈12.5px, not cramped).

## 4. Page order (rules — honour any "must" from the client)
1. **Cover** — logo, hero image, title, one-line subtitle, 4 stat cells, contact strip.
2. **Property Information** — MUST be page 2. Schedule table (all portions: size, deed, GPS) + Ownership/Title card + Financial Snapshot card.
3. **Parcel Aerials** — after Property Info. 2×3 grid, each labelled portion + size + farm.
4. **Loose Assets** — MUST come before Investment Highlights. Categorised cards.
5. **Investment Highlights** — MUST be the **second-last** page. Format as a **table: Highlight | Detail**. Add a market-stat strip under it.
6. **Contact + Disclaimers** — last page. Steps ("How the auction works"), contact block with dark logo, both disclaimers (§8).

## 5. Writing rules
- **Always hedge. Never state anything as definite.** Prefix figures/extents with `approx.` or `±`. Say "to be confirmed on due diligence". **Exception: loose-asset quantities** — state counts plainly (e.g. "10 units", "× 4"), no "approx." (they come from the owner's register); still add "to be confirmed on inspection".
- **Do not call it an auction unless told to.** Default the cover to "Information Pack" (not "Auction Information Pack") and use offer / expression-of-interest language ("How to Proceed", "Submit an offer", "conditions of sale"), never "auction / bid / fall of the hammer". The Dynamic Auctioneers brand name + logo still appear as the issuer.
- **No dollars unless the client asks.** Default ZAR only. If converting, state the rate + date and mark "indicative only"; keep ZAR as the source-of-truth.
- **No em/en dashes** in client copy. No AI-tell phrasing ("nestled", "boasts", "in today's world"). Crisp, factual, brochure tone.
- Historical figures are labelled as such — not a current valuation or reserve.
- Market stats describe the sector, not the property — say so.
- If the entity is in business rescue / liquidation, state it factually; don't sensationalise.

## 6. Investment Highlights — build from the actual asset story
Rework the highlights around what makes THIS listing strong (e.g. turnkey going concern, on-site packhouse, complete fleet, scale in one hand, proven region, clean title, value entry via business rescue). One row per point, `Highlight | Detail`.

## 7. Parcel aerials — matching to the deeds data
1. Extract the pasted outline images from the session transcript jsonl (base64 image blocks).
2. Fetch reference satellite tiles at each portion's **exact Lightstone GPS coord** — free, no key:
   `https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/export?bbox=<xmin,ymin,xmax,ymax>&bboxSR=4326&imageSR=4326&size=800,615&format=jpg&f=image` (box ≈ ±0.010 lat, ±0.013 lon for ~2.5 km).
3. Match each provided outline to a portion by **surroundings** (township, river/bush, pivot density), not by apparent size (zoom varies). Build a side-by-side verify sheet before labelling.
4. Compress aerials (~980px, JPEG q82) before embedding. Caption each with "indicative boundaries, not a survey, imagery © Google".
5. Flag any near-identical/adjacent parcels as best-guess for the client to confirm.

## 8. Disclaimers (last page, verbatim)
- **Marketing note:** pack is for marketing only; not an offer/warranty/advice; figures approximate and from public deeds/third-party data/owner's register, not independently verified; buyer must do own due diligence; subject to conditions of sale + seller confirmation.
- **Legal disclaimer (exact):** "Whilst all reasonable care has been taken to provide accurate information, neither Dynamic Solutions 1068 (Pty) LTD Trading As Dynamic Auctioneers nor the Seller/s guarantee the correctness of the information provided herein and neither will be held liable for any direct or indirect damages or loss, of whatsoever nature, suffered by any person as a result of errors or omissions in the information provided, whether due to negligence or otherwise of Dynamic Solutions 1068 (Pty) LTD Trading As Dynamic Auctioneers or the Seller/s or any other person."

## 9. Contact block
**No personal name** on the contact block — use "Dynamic Auctioneers" as the heading. **086 155 2288** · **properties@dynamicauctioneers.co.za** · dynamicauctioneers.co.za. (Confirm the phone/email per listing.)

## 10. Technical build
- Author as one self-contained HTML file (inline CSS, no external fonts/CDNs). A4 `@page`, `.page` = 210mm × 297mm, ~18mm margins, `page-break-after`, `print-color-adjust:exact`.
- Render: `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu --no-pdf-header-footer --print-to-pdf=out.pdf "file://…/pack.html"`.
- **Embed every image as a base64 data URI** before final render so the file is portable (fixes broken images in previews and email).
- Free hero image: Pollinations — `https://image.pollinations.ai/prompt/<desc>?width=1600&height=680&nologo=true&seed=<n>`.
- Verify by rasterising pages (`pdftoppm -png`) and eyeballing before delivering. Deliver the PDF to `~/Downloads` with a descriptive name.

## 11. Pre-send checklist
- [ ] Property info on p2; loose assets before highlights; highlights second-last.
- [ ] Every figure hedged (approx./±); ZAR (no $) unless asked.
- [ ] No em dashes / AI tells.
- [ ] Logo on cover + dark logo on last page; images embedded (self-contained).
- [ ] Aerials matched to correct portions (adjacent ones confirmed).
- [ ] Both disclaimers present; correct legal entity name.
- [ ] Open items flagged to client: **auction date**, framing, phone/email, any excluded/other-entity assets.
