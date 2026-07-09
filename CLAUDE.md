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
  - **engine/** — `ingest` (M1/M2), `verify` (M3: deterministic cross-checks catch the garage conflict + flatlet finding unprompted, memo, sign-off gate), `store` (M4: 3-gate state machine per §4.3/D13), `render/` (M5: swappable backend — `html` default + removable `canva` scaffold D14 — renders all §12 formats from `public_view` only), `distribute/` (M6: channel matrix, GHL Social Planner scaffold D11, manual packs + per-channel status, WhatsApp stub), `crm.py` (M7 seed), `watch/graph.py` (Phase 6 placeholder).
  - **webapp/** — FastAPI + Jinja2 + HTMX platform (M8): job board, drag-drop intake, gates 1-3, approve-by-email tokens, artifact pack, settings; bcrypt auth (marketing/approver roles), SQLite jobs-table worker. "Auction-house ledger" UI per `docs/DESIGN-SYSTEM.md`. Run: `uvicorn webapp.main:app`.
  - **Tests:** `tests/` 84 passing + 1 skipped, offline/key-free. UI verified via screenshots (board, intake, gate 1).
  - **Open boxes (need credentials, not code):** live Claude extraction/verification/copy (`ANTHROPIC_API_KEY`), live social posting (GHL token, D11), Canva Enterprise (D12/D14), Prop Data feed, MS Graph watcher, platform hosting (open Qs 3,5,6,7,9-13). Everything degrades gracefully without them (template/pack fallbacks). See the placeholder list in the build handoff.
- **Next:** wire the `ANTHROPIC_API_KEY` and run the DP3060 golden extraction; then work the external-credential open questions (§10).

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

1. **POPIA / PII:** owner name, ID number, occupant contact, bond, arrears live only in the record's internal layer. Public renderers receive a `public_view` projection that does not contain these fields. Enquiries route to Dynamic (086 155 2288 / properties.admin@dynamicauctioneers.co.za), never the occupant's cell.
2. **Two human gates:** verification memo sign-off before drafting; artifact approval before distribution. Enforced in code (state machine), not convention.
3. **No hallucinated facts:** every ad claim traces to a record field; every record field traces to a source doc or cited research. Missing data = `null` + `confidence: "missing"`.
4. **Client-facing copy:** SA English, no em/en dashes, no AI-sounding constructions. Framing follows `sale_process.method` ("offers invited" vs auction).
5. **Brand:** DS gold `#B08D4A`, ink `#191613`, Montserrat, real letterhead asset (in `DP3060/photos/p1_img01_*.png`), footer: Dynamic Solutions 1068 (Pty) Ltd T/A Dynamic Auctioneers • Reg 2018/014769/07 • VAT 4050206442 • PPRA/SAIA/NAA.

## People

Ronnie (owner) · Gerrie Venter (prepares Property Reports, properties.admin@) · Nikki (marketing/admin, absorbs Abigail's role) · Brad (technical) · Keegan (builder, Cognexa).
