# Marketing Engine — Dynamic Auctioneers

Property-marketing automation for Dynamic Auctioneers (SA liquidation-auction firm, owner Ronnie; built by Keegan/Cognexa). One structured record per property; every ad rendered from it. Kills the "repost the ad 4+ times on every price change" problem.

## Read this first

**`SPEC.md` is the source of truth.** Modules M1–M7, phased build plan, acceptance criteria, decision log, open questions. Rules:

- When implementation diverges from the spec, update the spec in the same commit.
- New decisions go in the spec's Decision Log — never re-argue logged decisions.
- A module is done when its acceptance-criteria boxes tick, not before.
- No phase starts until the previous phase's criteria pass.

## Current status

- **Phase 0 ✅ (2026-07-08):** manual prototype for DP3060 — `DP3060/record.json` (this file doubles as the record schema reference), `DP3060/verification-memo.md`, `DP3060/ads/*` (4 channel formats + branded demo-ad HTML), 26 photos extracted from the source PDF.
- **Phases 1-7 ✅ code done (2026-07-08/09):** the full engine + the M8 platform web app.
  - **engine/** — `ingest` (M1/M2), `verify` (M3: deterministic cross-checks catch the garage conflict + flatlet finding unprompted, memo, sign-off gate), `store` (M4: 3-gate state machine per §4.3/D13), `render/` (M5: swappable backend — `html` default + removable `canva` scaffold D14 — renders all §12 formats from `public_view` only), `distribute/` (M6: channel matrix, GHL Social Planner scaffold D11, manual packs + per-channel status), `crm.py` (M7 seed), `watch/graph.py` (Phase 6 placeholder).
  - **webapp/** — FastAPI + Jinja2 + HTMX platform (M8): job board, drag-drop intake, gates 1-3, approve-by-email tokens, artifact pack, settings; bcrypt auth (marketing/approver roles), SQLite jobs-table worker. "Auction-house ledger" UI per `docs/DESIGN-SYSTEM.md`. Run: `uvicorn webapp.main:app`. Gate 2 also carries the small-edits editor (D17) and the **Photos panel** (D20: upload/set-lead/remove, writes canonical `marketing.hero_photo`/`gallery`, auth-gated serve route) — humans add or fix photos on the record, so **the OneDrive/Graph watcher is now optional, not a dependency**.
  - **Tests:** `tests/` 131 passing + 1 skipped, offline/key-free. UI verified via screenshots (board, intake, gate 1).
  - **Open boxes (need credentials, not code):** live Claude extraction/verification/copy (`ANTHROPIC_API_KEY`), live social posting (GHL token, D11), Canva Enterprise (D12/D14), Prop Data feed, MS Graph watcher (convenience-only per D20), platform hosting (open Qs 3,5,6,7,9-13). Everything degrades gracefully without them (template/pack fallbacks). See the placeholder list in the build handoff.
- **Live golden extraction ✅ (2026-07-17):** the DP3060 golden run passed end to end on free credits. Extraction is sectioned (D21), non-strict tool use (D23: strict grammar rejects even a flat section as "too complex"), with opt-in text mode + pacing (D22) to run under the entry-tier 10k-input-tokens/min cap. Both sentinels caught unprompted (garage conflict + flatlet), POPIA verified on real PII, money facts match the golden exactly. Code-side `normalize_record` (D30) canonicalizes dates/title_type/zoning after assembly. Native PDF + strict-off tool use stay the defaults.
- **Live distribution ✅ (2026-07-22, D27-D30):** posting to GHL verified against the real API - drafts/schedule/post-now to Facebook/Instagram/LinkedIn (TikTok/broadcast removed D26/D19; static images only D24), property photos + the Canva demo-ad PNG hosted via the GHL Media Library and attached as CDN URLs, `GHL_POST_STATUS=draft` in `.env` is a hard guard rail (env overrides any per-post choice). The Canva backend exports PNG (info pack stays PDF), so the branded design previews inline on gate 2 and the artifacts page - no Canva login needed to review it. `RecordStore.get` strips known-legacy fields so pre-D19 records load.
- **Live verification ✅ (2026-07-23, D31):** M3's web-research half ran live on DP3060 - 11 searches, found an active listing at the same address (R995k, inside the EVM range) and the "portals say Pelham, not Pelham North" wording insight; memo now carries findings only (narration stripped). The usage tier is 500k input tokens/min now, so the old 10k/min ceiling (D21/D22 workarounds) no longer binds; native PDF + no pacing are the working defaults. **Every pipeline stage has now run live end to end.**
- **Second property ✅ (2026-07-23, D32):** first never-seen property extracted live (Erf 2035, 22 Taunton Way, Somerset Park, Umhlanga: 7-page EVM + a 21-page registered valuer's report) in native PDF mode, valid record first try, 61/63 fields correct on a field-by-field audit, zero hallucinations. Three fixes it forced: **prompt caching never hit** (per-call tool difference invalidated the prefix; now one identical six-tool list `record_<section>` per call, ~3x cheaper); new `physical.conflicts` (every cross-source disagreement recorded, each a blocking `PHYSICAL_CONFLICT` flag - the EVM-vs-inspection 2-vs-4 bedrooms and 106-vs-310 m2 were being silently resolved); new `valuation.professional` (valuer's market + forced-sale values, date, valuer) which `public_view()` strips like the POPIA layer - **sale-strategy figures never reach a renderer or the copy model** - plus a `VALUATION_DIVERGENCE` note when the valuer's figure falls outside the EVM range. Live copywriting also verified on the golden record (SA English, offers framing, no dashes, no PII, traceable facts).
- **Template sets ✅ (2026-07-23, D33):** `CANVA_TEMPLATE_MAP` supports named design sets (first set = default and defines which formats go through Canva; later sets overlay it); marketing picks the design per property via a gate-2 "Design template" dropdown (shown only when >1 set AND the renderer actually routes through Canva), stored on `marketing.template_set` ("Follow the default" = blank = tracks the first set), stale picks degrade to the default set. An adversarial review caught 4 defects in the first cut (render-pass crash via union `supports()`, silent pick-pinning on every save, inert picker, malformed-map silent dark) - all fixed pre-commit.
- **Source precedence + platform polish ✅ (2026-07-27, D35/D36):** physical-fact trust order **valuation > property report > lightstone** built end to end - structured `PhysicalConflict` (per-source values) replaces the free-text conflicts + `garages_conflict`, `resolve_physical_conflicts` writes the precedence winner in code, one unified `PHYSICAL_CONFLICT` flag, extraction takes an optional 3rd valuer's PDF, intake classifies + slots it (dropzone accepts up to 3), and gate 1 shows a **source picker** (default = precedence winner, pick to override, swaps the value). Precedence covers physical facts only; Lightstone still owns deeds/legal/market. Plus board **delete** (per-row, gated) and **Back to board** buttons on every gate/detail page. Suite 207 pass / 1 skip.
- **Next:** external-credential open questions (§10): platform hosting (the big one), Prop Data feed access; then Nikki running a property end to end as the real acceptance test. (LinkedIn page-vs-profile: resolved 2026-07-24 - post to whatever account is currently connected in the GHL Social Planner; no code change, the pipeline already targets the connected LinkedIn account.)

## Data locations

- **Sample source docs:** `~/Documents/dynamicAuctioneers/` — `Lightstone/` (EVM reports incl. `3060 - EVM_Report_40_Topham_Road...pdf`) and `Property Reports/` (`3060 - PROPERTY REPORT.pdf`). The DP3060 pair is the golden test case.
- **Production files live on OneDrive/SharePoint** ("Master Training Solutions" library > Marketing > Properties), one folder per property named `<DP>- <name>` with standard subfolders (Advertisements, Auction Prep, Lightstone, Media, Leads, Proof of Marketing…). Graph API watcher is Phase 5.
- **Naming:** properties are `DP3060`; sub-properties (lots under one instruction) `DP3035.1`, `DP3035.2`.

## Tech conventions

- Python 3, SQLite, CLI-first (no UI until Phase 3+). Secrets in `.env` (see `.env.example`), never committed.
- Claude API: model `claude-opus-4-8`, adaptive thinking (`{"type": "adaptive"}`), structured outputs via `client.messages.parse()` + Pydantic for all extraction (validated JSON, never free text). PDFs go in as base64 document blocks. Verification uses the server-side `web_search_20260209` tool.
- Photo extraction: PyMuPDF (`fitz`) pulls embedded images at source quality.
- Merge rule: Lightstone wins deeds/market data; the Property Report (physical inspection) wins physical reality; conflicts become verification flags, never silent picks.

## Hard rules (non-negotiable)

1. **POPIA / PII:** owner name, ID number, occupant contact, bond, arrears live only in the record's internal layer. Public renderers receive a `public_view` projection that does not contain these fields. Enquiries route to Dynamic (086 155 2288 / properties@dynamicauctioneers.co.za), never the occupant's cell.
2. **Two human gates:** verification memo sign-off before drafting; artifact approval before distribution. Enforced in code (state machine), not convention.
3. **No hallucinated facts:** every ad claim traces to a record field; every record field traces to a source doc or cited research. Missing data = `null` + `confidence: "missing"`.
4. **Client-facing copy:** SA English, no em/en dashes, no AI-sounding constructions. Framing follows `sale_process.method` ("offers invited" vs auction).
5. **Brand:** DS gold `#B08D4A`, ink `#191613`, Montserrat, real letterhead asset (in `DP3060/photos/p1_img01_*.png`), footer: Dynamic Solutions 1068 (Pty) Ltd T/A Dynamic Auctioneers • Reg 2018/014769/07 • VAT 4050206442 • PPRA/SAIA/NAA.

## People

Ronnie (owner) · Gerrie Venter (prepares Property Reports, properties.admin@) · Nikki (marketing/admin, absorbs Abigail's role) · Brad (technical) · Keegan (builder, Cognexa).
