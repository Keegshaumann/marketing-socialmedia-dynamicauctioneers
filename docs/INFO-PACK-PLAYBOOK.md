# Dynamic Auctioneers — Info Pack Playbook

How a Dynamic Auctioneers information pack is built. **Derived from the team's own
packs**, not from a generic brochure idea: `designs/incoming/DP2674 - DIGITAL INFO
PACK.pdf`, `DP2777 INFO PACK.pdf`, `DP2948.1 INFO PACK COMPRESSED.pdf` (nine units
in one pack) and `DP2974 INFO PACK.pdf` (18 pages, mixed use). Those four are the
reference. When this document and a rendered pack disagree, the four packs win and
this document gets corrected.

The engine renders it from `engine/render/templates/info_pack.html.j2` (see §11 for
what the engine deliberately does differently).

---

## 1. What it is

A landscape PDF that markets one listing to buyers, issued **as Dynamic
Auctioneers' own document**. Never branded Cognexa. Legal entity for the closing
disclaimer: **Dynamic Solutions 1068 (Pty) LTD Trading As Dynamic Auctioneers**.

Length follows the property, not a template: 6 pages for a flat (DP2777), 7 for a
house (DP2674), 11 for a nine-unit portfolio (DP2948.1), 18 for a smallholding with
industrial improvements (DP2974). Gallery pages repeat until the photographs run
out; everything else appears once.

## 2. Format (locked)

- **A4 LANDSCAPE, 297mm × 210mm.** All four packs. Not portrait.
- **White paper** (`#ffffff`), **black ink** (`#000000`). Not ivory.
- **Gold, the two brand tones only:** `#ac874a` (mid: rules, frame, headings on
  white, pills) and `#ddc689` (light: highlights inside the gold, gradients, text
  on black). Sampled off the vector pack; the same pair as D54.
- **Grey swoosh** `#d5d5d5` with a pale gold band `#e8dece`, sweeping across the
  bottom-right corner of interior pages. It sits under the content, never over it.
- **Frame:** a 1px `#ac874a` hairline inset ~5mm from the sheet edge, with ~4mm
  rounded corners. Every interior page. The shield badge overlaps it.
- **Font:** Montserrat throughout. Headings **Black/ExtraBold, uppercase**; body
  Regular/Medium; small caps-style labels are just uppercase + letter-spacing.

## 3. Brand furniture

- **Shield badge** — black pennant (rectangle with a point at the bottom centre)
  carrying the white+gold lockup, hung off the **top-right** of interior pages,
  overlapping the frame. ~34mm wide.
- **Gallery badge** — on gallery pages the lockup sits **top-left** in a black
  rectangle, overlapping the first photo, with the page title beside it.
- **Cover lockup** — the full black+gold "DYNAMIC AUCTIONEERS" mark, centred in the
  white cover panel, on a faint chevron pattern.
- **Gold pill** — a rounded gold-filled bar with centred black text, used for
  standing statements ("All Outstanding Fees, if any, to be Settled by the
  Seller.", "Subject To 30 days Confirmation By Seller").
- **Outline pill** — the same shape, white fill with a gold outline, for warnings
  ("VACANT OCCUPATION CANNOT BE GUARANTEED. VIEWING NOT POSSIBLE.") and for the
  complex-name chip with a location pin.
- **Icon rows** — a black glyph (~9mm) with a bold uppercase label beside it, and
  an optional smaller sub-line under the label ("2 BATHROOMS" / "MAIN EN-SUITE").
- **Photo treatment** — every photograph carries a thin `#ac874a` border. The
  cover's inset photograph is a circle with a ~1.5mm gold ring.

## 4. Page order

1. **Cover.**
2. **Location details** — MUST be page 2.
3. **Property description / features** — one or more pages.
4. **Gallery** — repeats as needed.
5. **Investment highlights** — MUST be the **second-last** page.
6. **Closing** — MUST be last.

A page with no data for it is omitted, never printed empty.

## 5. Page anatomy

### 5.1 Cover

Split down the sheet: a **white panel** (~40% width, inset from the left edge so a
sliver of photograph shows beside it) against a **full-bleed photograph** filling
the rest. DP2674 mirrors it (panel right); panel-left is the majority and the
default.

- Panel: logo lockup, then the headline stacked in three weights — descriptor
  ("2 BEDROOM APARTMENT", "±1.2 HA MIXED USE SMALLHOLDING"), **locality in gold**,
  city/province in black. Then the **method badge**.
- Method badge: a black bar, first word gold, rest white — "LIQUIDATION AUCTION",
  "ONLINE AUCTION", "ON-SITE LIQUIDATION AUCTION" (that one puts LIQUIDATION in
  red). A non-auction listing reads "FOR SALE".
- Photograph half: a **circular inset** with a gold ring, holding either the hero
  photograph or an aerial with the property outlined in red and a map pin.

### 5.2 Location details

Title "LOCATION DETAILS". Two columns of labelled facts, label bold, value regular:

- Left: **Physical Address**, then Zoning, Extent of Unit / Extent of Mother Erf.
- Right: **Legal Address** (section/plan, "known as", situated at, erf, township),
  Municipality, Province.
- **Rates & Taxes** and **Levies** as icon chips (circled document glyph).
- The gold pill "All Outstanding Fees, if any, to be Settled by the Seller."
- A **bordered box** carrying the terms, centred, mixed weight: deposit %,
  commission % and VAT, guarantee days, occupation. Verbatim shape:
  > 10% deposit payable on the fall of the hammer by way of EFT.
  > 6% commission and VAT on the commission payable by the Seller.
  > Guarantee for balance within 45 days after confirmation.
  > Occupation on date of registration of transfer of the property.
- The gold pill "Subject To 30 days Confirmation By Seller".
- **Auction line or viewing line**, large and centred ("ONLINE AUCTION / 13 AUGUST
  2026 @ 10:00", or "VIEWING: 2 SEPTEMBER 2026 | 10:00-12:00").
- A **map/photo panel** on the right: satellite aerial with the boundary drawn in
  red and a pin, or a street photograph, gold-bordered.

### 5.3 Property description / features

Title "PROPERTY DESCRIPTION" or "PROPERTY FEATURES". Icon rows in two columns, with
optional **gold column headings** ("MAIN HOUSE:", "EXTERNAL FEATURES:",
"COTTAGE:", "INDUSTRIAL IMPROVEMENTS:"). Grouped subsets can be boxed in a gold
rounded outline ("OPEN PLAN:", "OUTDOORS:"). A page may end with a black uppercase
footnote line ("SEPARATE DETACHED COTTAGE - TENANT OCCUPIED - FAIR OVERALL
CONDITION") and/or a complex chip bottom-right.

**Building sizes** is a variant of this page: the same glyphs at ~18mm with the
extent above the label ("±450 m² / MAIN HOUSE"), split "RESIDENTIAL:" and
"INDUSTRIAL:" by a vertical gold rule.

### 5.4 Gallery

Title "GALLERY" beside the top-left badge. A **3 × 3 grid** of gold-bordered
photographs filling the sheet. Repeat pages until the photographs are used; the
last page may be short (DP2948.1 ends on a 2×3).

### 5.5 Investment highlights (second-last)

Title "INVESTMENT HIGHLIGHTS", centred. A **bordered table**: narrow left column
with the point in bold ("Established Brackenhurst Location"), wide right column
with one or two sentences. 8 to 10 rows. Thin `#000` hairlines, alternating rows
left plain.

### 5.6 Closing

A dark, dimmed photograph filling the page. On it:

- A short tagline in white bold uppercase with a gold left rule, then a lighter
  line under it ("WHERE SPACE, VERSATILITY & OPPORTUNITY COME TOGETHER." /
  "Don't miss this auction!").
- The shield badge top-right.
- Four contact rows, each a **circled white glyph** and white text: address,
  website, email, telephone —
  187 Gouws Avenue, Raslouw AH, Centurion · dynamicauctioneers.co.za ·
  administration@dynamicauctioneers.co.za · 0861 55 22 88.
- The **legal disclaimer verbatim**, small, white, centred across the foot (§8).

## 6. Multi-property packs

DP2948.1 sells nine sectional-title units in one pack, and the format handles it by
repeating blocks rather than by a different design:

- **One cover** for the development, headline naming the scheme.
- **A details page per group of units** that share terms ("UNIT 1,2,3 & 6
  DETAILS", "UNIT 5,7,8,9 & 10 DETAILS") — identical to §5.2 but titled by unit
  group. Commission differing between groups (6% vs 7.5%) is why they are split.
- **Description pages carrying three unit columns each**, divided by vertical gold
  rules. Each column: "UNIT n" + its own extent chip, then its own icon rows, with
  boxed subgroups ("OPEN PLAN:", "OUTDOORS:"). Three units per page, so nine units
  = three pages.
- **Shared gallery and shared highlights**, written about the portfolio ("Purchase
  Individual Units or Entire Portfolio").

## 7. Writing rules

- **Always hedge.** `±` or "approx." on every extent and figure. "TBC" is
  acceptable and used ("Levies: TBC"). Rates as "± R4 828,00".
- **Auction words only for an auction.** An auction pack says auction, bid, fall of
  the hammer. A normal listing says "FOR SALE", offers, conditions of sale.
- **ZAR only.** No dollars.
- **No em or en dashes.** Hyphens only. No AI tells ("nestled", "boasts").
- Highlights are written **about this property**, one point per row, factual and
  specific ("A double garage together with four covered carports provides secure
  parking for multiple vehicles"). No forecast, no yield, no promise.
- Standing statements are used verbatim (the two gold pills, the terms box, the
  disclaimer).

## 8. Disclaimer (closing page, verbatim)

> Whilst all reasonable care has been taken to provide accurate information, neither
> Dynamic Solutions 1068 (Pty) LTD Trading As Dynamic Auctioneers nor the Seller/s
> guarantee the correctness of the information provided herein and neither will be
> held liable for any direct or indirect damages or loss, of whatsoever nature,
> suffered by any person as a result of errors or omissions in the information
> provided, whether due to negligence or otherwise of Dynamic Solutions 1068 (Pty)
> LTD Trading As Dynamic Auctioneers or the Seller/s or any other person.

## 9. Contact block

**No personal name.** Address, website, email, telephone as printed in §5.6. The
packs use `administration@dynamicauctioneers.co.za`; the engine also carries
`properties@dynamicauctioneers.co.za` as the enquiries mailbox (SPEC hard rule 1).
Confirm per listing.

## 10. Technical build (engine)

- One self-contained HTML file, inline CSS, assets embedded as data URIs, printed
  to PDF through headless Chromium (`engine/render/rasterize.py`).
- `@page { size: A4 landscape; margin: 0 }`, `.page` = 297mm × 210mm,
  `print-color-adjust: exact`, `page-break-after` on every sheet.
- Glyphs are an **inline SVG library** authored to match the packs' flat black
  icon style (`engine/render/pack_icons.py`: the drawings, the keyword rules that
  choose one for a line of record wording, and the label/qualifier split). Drawn
  for this repo, not Canva assets.
- **Every page is filled.** Blocks are costed in tenths of a millimetre and a page
  that still runs short takes a photograph band that flexes into exactly the space
  left (D60). No sheet ends in a hand-sized hole.

## 11. What the engine does NOT copy

Deliberate divergences from the reference packs, each with a reason:

- **No valuation or comparable figures.** DP2777 prints "Offers from R750,000 will
  be considered" and a Lightstone comparable average. The engine strips municipal,
  professional and forced-sale values from `public_view` (D32/D54/D57, owner
  directive), so the pack carries the offers framing or the auction line only.
- **No red boundary outline on the aerial.** Drawn by hand in Canva against a
  cadastral diagram; the engine holds a GPS point, not a boundary. An aerial, when
  added, will carry a pin only.
- **No owner name, ID, bond or occupant contact**, ever (SPEC hard rule 1). The
  packs do not print these either.

## 12. Pre-send checklist

- [ ] Landscape A4; white paper; both golds only; frame + swoosh on interior pages.
- [ ] Location details on page 2; highlights second-last; closing last.
- [ ] Every figure hedged (± / approx. / TBC); ZAR only; no em or en dashes.
- [ ] Auction wording only if it is an auction.
- [ ] Both gold pills and the terms box present and verbatim.
- [ ] Disclaimer verbatim on the closing page; correct legal entity.
- [ ] Gallery grid full; no page ends in a hole.
- [ ] Open items flagged to the client: auction date, viewing window, rates and
      levies, commission percentage.
