# 1. System Overview

## 1.1 The problem it was built to solve

Dynamic Auctioneers' property marketing was a manual bottleneck. Every ad was a
hand-made artifact — a Canva poster, a portal listing, a Facebook post, an email
— and every price, photo or date change meant remaking and reposting all of them,
**four or more times per property**. The marketer who carried that work was
leaving and was not being backfilled.

The bottleneck is structural, not a staffing problem: the data and the
presentation were welded together. Every artifact held its own private copy of
the price, the address and the photos, so a single change had to be applied by
hand in every place it had been duplicated.

## 1.2 The fix

**Separate the data from the presentation.** One structured property record per
DP number; every marketing artifact is *rendered* from that record. Change the
record once, regenerate everything, push the updates. A repost becomes a
two-minute approval instead of an afternoon.

A price change today reaches all nine artifacts in one pass (D64).

## 1.3 What was actually delivered

A **web platform** the marketing team logs into and drives themselves, at
`https://46.202.175.127.nip.io` (a temporary hostname; the intended address is
`marketing.dynamicauctioneers.co.za`). It runs the team's real workflow end to
end:

> drop the documents → verified record → generated advert → internal approval →
> client approval → full artifact set → posted to the channels → change requests
> and re-posts

The engine that does the work sits behind it as modules M1–M7; the platform (M8)
is the face the team uses. The developer operates nothing day to day.

**Secondary wins, all of them realised:**

- The generated advert doubles as a mandate-pitch tool — a liquidator can be
  shown the campaign before signing.
- The verification step catches real data errors. Proven twice: Lightstone
  claimed three garages on DP3060 where the inspection found none; Lightstone
  said 106 m² and two bedrooms on another property where the valuer measured
  310 m² and four.
- Every artifact version, approval and outbound push is logged per property,
  which produces "Proof of Marketing" as a by-product.

## 1.4 Who uses it

| Person | Role in the system | What they do |
|---|---|---|
| **Nikki** | marketing | Uploads the documents, adds photos, edits copy, drives every property through the platform |
| **Approvers** (behind `admin@dynamicauctioneers.co.za`) | approver | Sign gate 1 (verification) and gate 2 (advert). Can act straight from the approval email without logging in |
| **Gerrie Venter** | source | Prepares the Property Reports the system consumes (`properties.admin@`) |
| **Ronnie** | owner | Sets policy and rules on the open decisions |
| **Brad** | technical | Technical contact on the Dynamic side |
| **Keegan (Cognexa)** | builder | Builds and deploys. Not in the daily loop by design |

Roles in the code are `admin`, `marketing` and `approver` (`webapp/auth.py`).
The split exists so operational staff can run everything without ever touching
credentials (D34) — the Settings screens that hold API tokens are admin-only.

## 1.5 What the system produces

Nine artifacts per property, all rendered from the one record
(`engine/render/base.py`):

| Format | Output | What it is |
|---|---|---|
| `demo_ad` | HTML + PNG + PDF | The branded advert. Rendered at exactly the Instagram post canvas, 1080×1350, rasterised 2× to 2160×2700 (D48) |
| `info_pack` | PDF | The buyer-facing information pack: a real paginated A4 **landscape** document rebuilt to the team's own packs (D58, D61, D67) |
| `auction_board` | HTML → PDF | The on-site board, rebuilt to the team's own board (D74) |
| `portal_listing` | Markdown | Property24-ready copy |
| `facebook_post` | Markdown | Facebook post plus boost notes |
| `email_blast` | Markdown | Subject line A/B plus body |
| `alert_mailer` | HTML | The alert mailer, plus the audience |
| `saia_banner` | HTML | SAIA alert banner |
| `webapp_icon` | SVG | Upcoming-auction tile for the website |

Four advert designs are selectable per property — `hero_overlay` (the default),
`stats_first`, `feature_list` and `collage` — which between them cover every
distinct layout in the team's own 55 adverts (D41–D45, D49).

## 1.6 The four principles the build never bends

**1. The DP number is the primary key.** Records, files, artifacts and leads all
hang off it. Sub-properties (`DP3035.1`) are children of the instruction
(`DP3035`). The DP number is *internal* — it never appears on a public artifact,
where the public reference is the mandate MASTER REF (D37). The one exception is
auction adverts, which restored `PROPERTY REF: DP` on the chrome (D42).

**2. Privacy by architecture, not by discipline (POPIA).** Owner name and ID
number, occupant contact, bond and arrears live only in the record's internal
layer. Public renderers receive a `public_view` projection that *does not
contain those fields* — it is not a matter of remembering to omit them. The same
projection also strips sale-strategy figures: municipal, professional and
forced-sale valuations never reach a renderer or the copy model (D54, D57). A
poison-marker test asserts this on every backend and every screen. Enquiries
route to Dynamic on 086 155 2288 / `properties@dynamicauctioneers.co.za`, never
to the occupant.

**3. Three human gates, enforced in code.** Nothing publishes without
verification sign-off (gate 1), internal advert approval (gate 2) and logged
client approval (gate 3). These are states in a machine that rejects illegal
transitions, not a convention people are asked to follow.

**4. No hallucinated facts.** Every claim in an advert traces to a record field;
every record field traces to a source document or a cited piece of research.
Missing data is `null`, never invented — the schema forbids fields it does not
know about.

Two further rules bind the output: client-facing copy is South African English
with no em or en dashes and no AI-sounding constructions, and the brand is fixed
(gold `#B08D4A`, ink `#191613`, Montserrat, the real letterhead, and the
Dynamic Solutions 1068 (Pty) Ltd footer with its registration and VAT numbers).

## 1.7 Status as at 20 August 2026

| | |
|---|---|
| **Deployment** | Live on a Hostinger VPS behind nginx and Let's Encrypt TLS. Responding HTTP 200 |
| **Test suite** | **370 passed, 15 skipped** in 52s — run and confirmed on 20 August 2026 |
| **Pipeline** | Every stage has run live end to end: extraction, verification, rendering, distribution |
| **Properties run live** | The DP3060 golden case, plus a never-seen second property (Erf 2035, Somerset Park, Umhlanga) extracted at 61 of 63 fields correct on a field-by-field audit, with zero hallucinations |
| **Build phases** | 0–5 and 7 complete; phase 6 (the OneDrive watcher) deliberately optional |
| **Decision log** | 77 decisions logged |
| **Outstanding** | The client fix list (38 items, partially shipped), 12 decisions awaiting the owner, backups not yet configured, and the real domain not yet cut over |

Detail on all of it is in section 6.

## 1.8 What it costs to run

Roughly **R20 per property** in AI spend — extraction about R5, verification with
web research about R10, copy generation about R3. Re-renders on updates cost
cents, because the two paid calls are content-addressed cached (D59) and the
stable prompt prefix is cached (D32). That is noise against the roughly R6,000
per month tool budget the system is meant to consolidate into. The VPS is the
only fixed cost.

## 1.9 Glossary

| Term | Meaning |
|---|---|
| **DP number** | Dynamic Auctioneers' property file number (`DP3060`); sub-properties `DP3060.1` |
| **EVM** | Lightstone's Estimated Value Model report — desktop valuation, deeds data, comparables |
| **Property Report** | Dynamic's in-house branded inspection document, prepared by Gerrie |
| **OTP** | Offer to Purchase / Conditions of Sale — the document the sale terms are read from |
| **Lightstone** | South African property data provider |
| **Prop Data** | South African property-portal syndication backbone, feeds Property24 |
| **GHL** | GoHighLevel — the Social Planner used for posting to Facebook, Instagram and LinkedIn |
| **POPIA** | Protection of Personal Information Act, South African privacy law |
| **Instruction** | The engagement or liquidation under which one or more properties are sold |
| **Forced-sale value** | A valuer's quick-disposal estimate, typically 20–40% below market. Internal only |
| **SAIA** | South African Institute of Auctioneers — industry body, and the alert-mailer audience |
| **Gate** | A point where a human must approve before the system proceeds |
| **`public_view`** | The projection of a record with private and strategy fields physically removed |
