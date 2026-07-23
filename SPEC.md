# Dynamic Auctioneers Marketing Platform — System Specification

| | |
|---|---|
| **Version** | 0.2 (living document — update as we build; 0.2 = platform pivot, D15) |
| **Status** | Draft, Phases 0–1 complete; platform (M8) specced, not started |
| **Owner** | Keegan Haumann (Cognexa) for Dynamic Auctioneers (Ronnie) |
| **Repo** | `~/Documents/GitHub/marketing-socialmedia-dynamicauctioneers/` (github.com/Keegshaumann/marketing-socialmedia-dynamicauctioneers) |
| **Sample data** | `~/Documents/dynamicAuctioneers/` (Lightstone/ + Property Reports/) |

**How to use this document:** each module (M1–M8) is specced with inputs, outputs, behavior, and acceptance criteria. We build module by module in the phase order at the bottom. When reality diverges from the spec, update the spec in the same commit — the spec is the source of truth, not a suggestion. Decisions go in the Decision Log so we never re-argue them.

---

## 1. Purpose

Dynamic Auctioneers' marketing is a manual bottleneck: every property ad is a hand-made artifact (poster, portal listing, Facebook post, email), and each price/photo/date change means remaking and reposting everything **4+ times per property**. Abigail (marketer) is leaving and will not be backfilled; Nikki + this system absorb the workload.

**The core fix:** separate data from presentation. One structured property record per DP number; every marketing artifact is *rendered* from it. Change the record once → regenerate everything → push updates. A repost becomes a two-minute approval instead of an afternoon.

**The deliverable (v0.2, D15):** a **separate platform** — a small web app the Dynamic Solutions marketing team logs into and drives themselves. It runs their real workflow (§12) end to end: drop the document pair → verified record → generated ad → internal approval → client approval → full artifact set (icon, SAIA banner, info pack, boards, mailer) → posted to every channel → change requests and re-posts. The engine modules M1–M7 are the platform's backbone; M8 is the face the team uses. Keegan operates nothing day-to-day.

**Secondary wins:**
- The "demo ad" doubles as a mandate-pitch weapon (show liquidators the campaign before they sign)
- The verification step catches real data errors (proven: Lightstone said 3 garages on DP3060, inspection found none; said 106 m²/2-bed on DP3040, valuer measured 310 m²/4-bed)
- Every enquiry self-tags with a DP number → buyer CRM builds itself (feeds the later phases of the Dynamic engagement)

## 2. Design principles

1. **DP number is the primary key.** Everything — records, files, ads, leads — hangs off it. Sub-properties (`DP3035.1`) are children of the instruction (`DP3035`).
2. **Human-in-the-loop at three gates.** Nothing publishes without (a) verification memo sign-off, (b) internal ad approval, and (c) client approval logged (D13). The system drafts; humans approve.
3. **POPIA by architecture, not discipline.** Owner PII lives only in the record's internal layer. Public renderers physically cannot access it.
4. **Zero disruption first.** Phase 1 consumes the documents the team already produces (Lightstone EVM + Property Report). Their workflow doesn't change until the system has earned trust.
5. **Build one bite at a time.** Each phase ships something Ronnie can see working. No big-bang.
6. **Consolidation target.** This system replaces marketing functions of Van Studios/GoHighLevel over time, toward the ~R6k/mo total tool budget.
7. **The team drives, not the developer.** From Phase 4 the product is the platform UI (M8): Nikki and the approvers run properties end to end without Keegan in the loop. The CLI stays as the dev/plumbing surface, not the product (amends D6 — see D15).

## 3. System overview

```
                        ┌────────────────────────────────────────────────┐
                        │              INTAKE (M1)                       │
  Lightstone EVM  ──┐   │  paired upload, keyed by DP number             │
  Property Report ──┼──▶│  local drop folder → later OneDrive/Graph      │
  Media folder    ──┘   └───────────────┬────────────────────────────────┘
                                        ▼
                        ┌────────────────────────────────────────────────┐
                        │            EXTRACTION (M2)                     │
                        │  Claude parses both PDFs → record.json         │
                        │  photos extracted/ranked                       │
                        └───────────────┬────────────────────────────────┘
                                        ▼
                        ┌────────────────────────────────────────────────┐
                        │           VERIFICATION (M3)                    │
                        │  cross-check both sources + live web research  │
                        │  → verification-memo.md with flags             │
                        │  ⛔ HUMAN GATE 1: sign-off                     │
                        └───────────────┬────────────────────────────────┘
                                        ▼
                 ┌──────────────────────┴───────────────────────┐
                 ▼                                              ▼
  ┌───────────────────────────┐              ┌───────────────────────────────┐
  │      RECORD STORE (M4)    │              │        RENDERING (M5)         │
  │  SQLite, one row per DP   │─────────────▶│  templates → portal listing,  │
  │  lifecycle state machine  │   on change  │  FB, email, demo-ad           │
  │  PII layer separation     │  re-render   │  HTML, (later: Property       │
  └───────────────────────────┘              │  Report itself)               │
                                             │  ⛔ HUMAN GATE 2: approve     │
                                             └──────────────┬────────────────┘
                                                            ▼
                                             ┌───────────────────────────────┐
                                             │       DISTRIBUTION (M6)       │
                                             │  channel routing rules        │
                                             │  P24 (Prop Data feed), FB,    │
                                             │  email, site,                 │
                                             │  JamesEdition (R10m+)         │
                                             └──────────────┬────────────────┘
                                                            ▼
                                             ┌───────────────────────────────┐
                                             │       BUYER CRM (M7)          │
                                             │  enquiries tagged by DP +     │
                                             │  category → matched-buyer     │
                                             │  broadcasts on new listings   │
                                             └───────────────────────────────┘
```

Everything above sits behind the **Platform UI (M8)** — a web app where the marketing team uploads the pair, watches extraction, signs the gates, downloads the artifact pack, and triggers posting. The diagram's human gates are platform screens, and gate emails to admin@ carry one-click approve links back into it.

## 4. Data model

### 4.1 Identifiers

- Property: `DP<number>` (e.g. `DP3060`)
- Sub-property (lot under one instruction/liquidation): `DP<number>.<n>` (e.g. `DP3035.1`)
- OneDrive folder convention: `<number>- <name>` (e.g. `3040- KC Zuma`) — the intake parser reads the DP number from folder or file names
- Assumption pending confirmation: sub-properties share the parent's auction/sale event. Modelled as per-lot logistics with parent default so either answer works. *(Open question #2)*

### 4.2 Property record

Canonical shape: see [`DP3060/record.json`](DP3060/record.json) — that file **is** the schema reference until we formalize a JSON Schema in Phase 1. Top-level groups:

| Group | Source of truth | Notes |
|---|---|---|
| `identity` | Lightstone (deeds data) | erf/scheme/unit, address, GPS, title deed |
| `physical` | **Property Report (inspection)** | beds/baths/features/flatlet; Lightstone attributes only fill gaps |
| `valuation` | Lightstone (EVM, comps, municipal) | forced-sale value from valuer report when present |
| `financials_internal` | both | bond, arrears — **never rendered publicly** |
| `sale_process` | Property Report | terms, method (offers vs auction), viewing contact |
| `marketing` | system + human edits | headline, price display, channel routing, photo picks |
| `compliance` | system | PII redaction confirmations |
| `verification` | M3 | status, flags, sign-off |

**Merge rule:** Lightstone wins on deeds/market data; the inspection wins on physical reality; any conflict becomes a verification flag rather than a silent pick.

### 4.3 Lifecycle state machine

```
intake → extracted → flags_raised → verified(✍ gate 1) → drafted → approved(✍ gate 2)
       → client_approved(✍ gate 3 — client email out/in stays manual; reply logged in the platform)
       → assets_built → live → updated(price/photo/date change → re-render → re-approve fast-path)
       → sold | withdrawn → archived
```

Gate 3 and `assets_built` come from the real marketing workflow (§12, D13): only after the client says yes does the full artifact set (icon, SAIA banner, info pack, boards, mailer) get produced and posting happen. Change requests after gate 3 are internal-only — the fast-path re-approval never goes back to the client. *(Code catch-up: `store.py` currently implements the pre-gate-3 machine; extending it is a Phase 2 task.)*

A change to a `live` record puts it in `updated`: all artifacts regenerate, channels with APIs push automatically after fast-path approval, manual channels get a ready-to-post pack. A price *drop* additionally queues a "reduced" re-engagement burst (M6).

### 4.4 PII layers

- **Internal layer:** owner name/ID, occupant contact, bond, arrears. Stored, never rendered by public templates (templates receive a `public_view` projection that simply does not contain these fields).
- **Public layer:** everything an ad may say. Enquiries route to Dynamic's 086 155 2288 / properties.admin@, never the occupant's cell.
- Source PDFs retain PII — that's fine (they're internal documents), but the generated buyer-facing Property Report variant (Phase 4) strips it.

## 5. Module specifications

### M1 — Intake

**Status:** Phase 1 ✅ (2026-07-08) — `engine/intake.py`, driven by `engine ingest`.

- **Trigger:** new/changed files in a watched directory (Phase 1: local folder, passed to `engine ingest <dir-or-pair>`; Phase 5: OneDrive "Master Training Solutions" SharePoint library via Microsoft Graph delta queries).
- **Input:** the paired upload — Lightstone EVM PDF + Dynamic Property Report PDF — plus optional photos. Files matched to a DP number from filename/folder (`3060 - ...`, `3035.1 - ...`).
- **Behavior:** classify each PDF by content (PyMuPDF text + weighted keyword scoring; filename is only a tiebreaker), queue extraction when a pair is complete; a lone doc parks the job as incomplete. (>24h nag is a later phase.)
- **Output:** `IntakeJob{dp, parent_dp, lot, lightstone_evm, property_report, unknown}` → M2.
- **Acceptance criteria:**
  - [x] Dropping the DP3060 pair produces one complete job with both docs attached
  - [x] A lone Lightstone doc waits and flags "property report missing" rather than proceeding
  - [x] Sub-property numbering (`3035.1`) resolves to parent `DP3035` + lot 1

### M2 — Extraction

**Status:** Phase 1 ✅ code complete (2026-07-08) — `engine/extract.py` + `engine/photos.py`. Live golden-match run is pending an `ANTHROPIC_API_KEY` (the build env had none).

- **Input:** the PDF pair (base64 document blocks) + DP number.
- **Engine:** Claude API, model `claude-opus-4-8`, adaptive thinking (`{"type":"adaptive"}`, set explicitly — off by default on 4.8), **structured outputs** (`client.messages.parse(output_format=PropertyRecord)`; the Pydantic schema is now formalised in `engine/schema.py`, §4.2) so output is validated JSON, never free text. Both PDFs go in one request. Stable extraction brief carries a `cache_control` breakpoint (inert below the 4096-token Opus 4.8 minimum, but structurally in place).
- **Behavior:** apply the merge rule (§4.2) — conflicts land in the record's `*_conflict`/`*_note` fields rather than a silent pick; missing facts are `null` (never hallucinated); owner name/ID and the occupant cell are captured into the internal layer only. Handles both EVM variants (freehold `identity.erf` + sectional `scheme`/`unit`); commercial/industrial variant later *(open question #4)*. Photos extracted from the Property Report via PyMuPDF at source quality (26 for DP3060, matching Phase 0).
- **Output:** `PropertyRecord` (state `extracted`) stored in SQLite + `DP<dp>/record.json` + `DP<dp>/photos/`.
- **Acceptance criteria:**
  - [ ] DP3060 pair → record matching the hand-built Phase 0 record on all facts — *request shape verified offline; live run pending API key*
  - [ ] DP3040-style freehold EVM parses (erf, not scheme/unit) — *schema supports `identity.erf`; live run pending*
  - [x] Owner name/ID land in `financials_internal`/internal layer only — structural (`Owner` in `financials_internal`; `public_view()` strips it; poison-marker test passes)
  - [x] A field the docs don't contain is `null` — never hallucinated (every schema field defaults to `null`; `extra="forbid"` rejects invented fields). *Note: modelled as `null` rather than a separate `confidence: "missing"` flag — D8.*

### M3 — Verification

**Status:** Phase 2 ✅ code done (2026-07-09) — `engine/verify.py`. Deterministic gate-1 checks run key-free; the web-research half is key-gated.

- **Input:** `record.json` in state `extracted`.
- **Engine:** Claude API with the server-side web search tool (`web_search_20260209`) for live market research.
- **Behavior:**
  1. Deterministic cross-checks (code, not model): extent, title deed, municipal valuation, GPS, bed/bath/garage counts between sources.
  2. Research checks (model + web search): comparable live listings on Property24 for the suburb, recent sales sanity check, address existence; for industrial: zoning + R/m² in the node.
  3. Produce `verification-memo.md`: corroborated-facts table, numbered flags each with evidence + action, market-context section, POPIA checklist.
- **Gate:** a human marks sign-off (Phase 2: editing the memo/CLI flag; later: one-tap in a small web view). Record moves to `verified`.
- **Acceptance criteria:**
  - [x] Re-running on DP3060 flags the garage conflict and the flatlet-not-in-Lightstone finding without being told (deterministic, no key)
  - [x] Memo distinguishes "blocks publishing" flags from "internal awareness" flags (`severity: block|note`)
  - [x] No record reaches `drafted` without sign-off recorded (`sign_off` is the only path to `verified`; state-guarded, enforced in code)

### M4 — Record store

**Status:** Phase 1 ✅ (2026-07-08) — `engine/store.py` (`RecordStore`).

- **Engine:** SQLite (default `./engine.db`, gitignored; override via `--db` or `ENGINE_DB`) — single-user scale is fine for years; the `PropertyRecord` JSON is stored as a column with key fields (suburb, title_type, price_display) indexed. A `state_events` table is the append-only transition audit trail.
- **Behavior:** the §4.3 state machine is enforced in `RecordStore.transition` (illegal moves raise `IllegalTransition`). The re-render-on-change / diff event fires once renderers exist (Phase 3).
- **Acceptance criteria:**
  - [x] Illegal transitions rejected (e.g. `extracted → live` raises `IllegalTransition`)
  - [ ] Editing price on a live record produces a diff event and regenerated artifacts within one command — *store + state machine landed; the re-render half depends on M5 renderers (Phase 3)*

### M5 — Rendering

**Status:** Phase 0 ✅ proven (DP3060: 4 ad formats + branded demo-ad HTML with real DA letterhead, Montserrat, extracted photos) · productization in Phase 3

- **Input:** `public_view` projection of a verified record + photo picks.
- **Formats:** portal listing (P24 field mapping), Facebook post, email (subject A/B + body), branded demo-ad HTML (print-ready A4 + web), later the buyer-facing Property Report itself (replacing Gerrie's manual assembly — the current one literally embeds a Lightstone screenshot by hand), plus the post-client-approval artifact set — webapp icon/tile, SAIA banner, alert-mailer HTML, info pack, auction-board print PDF (§12).
- **Engine:** copy by Claude (channel-aware tone; SA English; **no em dashes or AI-tells in client-facing copy**; "offers invited" vs "auction" framing per `sale_process.method`), layout by HTML templates with brand tokens (extracted Phase 0: DS letterhead asset, gold `#B08D4A`, ink `#191613`, Montserrat, PPRA/SAIA/NAA footer, reg 2018/014769/07, VAT 4050206442).
- **Gate:** human approves the artifact set (gate 2) before distribution; edits made to copy are stored back on the record so re-renders keep them.
- **Backend interface (D14 — Canva Enterprise scaffolding, built as part of Phase 3):** rendering goes through a swappable backend contract so Canva can be plugged in later or deleted without touching the engine.
  - `engine/render/` package: `base.py` defines `RenderBackend` (`name`, `available() -> (bool, reason)`, `supports(fmt)`, `render(RenderRequest) -> Artifact`) plus the SPEC §12 format list; `__init__.py` is a lazy-import registry with `get_backend(name)` resolving arg → `ENGINE_RENDERER` env var → default `"html"`.
  - `html_backend.py` — the default (M5 templates themselves). No credentials, always available.
  - `canva_backend.py` — the **removable scaffold**: Canva Connect API autofill flow (OAuth refresh-token exchange with rotation persisted to a local state file → asset upload for photos → create autofill job on a brand template → poll → export PNG/PDF → download). Config-gated via env (`CANVA_CLIENT_ID/SECRET/REFRESH_TOKEN`, template-map JSON mapping fmt → brand_template_id + field paths); `available()` returns the missing-credential reason instead of crashing. Stdlib `urllib` only — no new dependency. **Proven end-to-end on their Canva Teams account (D16): fills all 13 template fields from `public_view`, blanks anything unknown; `demo_ad → EAHO9qkNlwE`.** The backend fetches each template's `dataset` and sends only the fields it declares (unknown fields 400), so one backend serves any DA template.
  - **Removal = delete `canva_backend.py` + its one registry line + its own test file.** Nothing else may import it.
  - Backends receive only the `public_view` payload — the poison-marker PII test applies to every backend, including Canva (fields sent to Canva's cloud are client-facing by definition).
- **Acceptance criteria:**
  - [ ] One command regenerates all formats for a DP in <2 min
  - [ ] Owner PII cannot appear in output (test: render a record with a poisoned marker in internal fields; assert absent — applies per backend)
  - [ ] Price change re-render preserves human copy edits
  - [ ] `get_backend()` defaults to html; `ENGINE_RENDERER=canva` selects the scaffold; unconfigured Canva reports its missing variables without breaking any other engine command
  - [ ] Deleting the Canva backend file + registry line + its test file leaves the suite green (proves it is not load-bearing)

### M6 — Distribution

**Status:** Phase 4 · not started · **depends on external answers**

Channel routing (hard-coded rules):

| Rule | Channels |
|---|---|
| Every property | Property24, own website, Facebook, email list |
| ≥ R10m | + JamesEdition |
| Industrial/commercial | + commercial portals (TBD) |
| Excluded | Private Property (policy) |

- **Mechanisms (in confidence order):**
  1. **Property24 via Prop Data vendor Feeds API** — the big prize: feed-driven listings mean *updates propagate automatically*. Blocker: confirm standalone access (email api-support@propdata.net). Fallback: Entegral/Fusion syndication, or P24 direct.
  2. **Social (FB, IG, LinkedIn, TikTok, X)** — GoHighLevel Social Planner API (D11): one call posts to every connected page after gate 3; price changes post a "REDUCED" update rather than editing silently (live posts cannot be API-deleted on IG/TikTok — §12 step 8).
  3. **Email** — TBD: current GHL vs. cheap ESP during migration *(open question #6)*.
  4. **Website** — TBD: platform unknown *(open question #5)*.
  5. **JamesEdition** — listing method unknown (manual/feed) *(open question #7)*; manual-with-generated-pack is an acceptable v1.
- **Update propagation:** API channels push automatically post-approval; manual channels get a "ready-to-post pack" (artifacts + checklist) and the system tracks posted/not-posted per channel per version.
- **Price-drop burst:** a price decrease queues a re-engagement blast (Facebook) as a *marketing event*, not silent maintenance.
- **Acceptance criteria:**
  - [ ] Channel matrix computed correctly from record value/type
  - [ ] A price change on a live record updates P24 (or produces the manual pack) without any artifact being rebuilt by hand
  - [ ] Every outbound artifact logged per DP per channel per version (this becomes "Proof of Marketing" for free)

### M7 — Buyer CRM

**Status:** Phase 6 · not started

- Every enquiry (a "reply 3060" message, email link `?dp=3060`, FB lead) creates/updates a contact tagged with DP + derived category (residential/industrial, area, budget band).
- New verified listing → query matched buyers → targeted broadcast ("new industrial property in Jet Park — 214 matched buyers").
- This is deliberately minimal — it seeds the full Buyer CRM phase of the wider Dynamic engagement rather than replacing it.

### M8 — Platform UI

**Status:** Phase 4 ✅ code done (2026-07-09) — `webapp/` (FastAPI + Jinja2 + HTMX). Runs with `uvicorn webapp.main:app`; UI verified against the design system by screenshot (board, intake, gate 1). Hosting is open Q12.

- **What it is:** the separate platform the Dynamic Solutions marketing team logs into to run the §12 workflow end to end — the product face over M1–M7. Keegan is not in the daily loop.
- **Screens:**
  1. **Job board** — every DP as a row with lifecycle state (§4.3), days-in-state, owner, next action; the department's whole pipeline at a glance.
  2. **Intake** — drag-drop the document pair (+ photos); live progress through extraction; an incomplete pair parks visibly ("property report missing") instead of proceeding.
  3. **Verification review (gate 1)** — the memo with its flags; sign-off button; blocking flags must be resolved or overridden with a written reason.
  4. **Ad review (gate 2)** — artifact gallery per format; approve, or request changes with a note that feeds the regeneration; human copy edits are stored back on the record so re-renders keep them.
  5. **Client approval (gate 3)** — pre-drafted client email to copy and send manually; "client approved" logged with date + user; after this, change requests stay internal (D13).
  6. **Artifact pack** — download-all per DP per version; per-channel posted/not-posted status (doubles as the Proof of Marketing view).
  7. **Post & change** — trigger GHL posting after gate 3; a change request runs the fast-path re-approval and re-post; shows the §12 step-8 reality (IG/TikTok live posts are deleted manually).
  8. **Settings** — GHL private-integration token, Canva credentials (D14), channel toggles, approver email list.
- **Approve-by-email:** gate emails to admin@dynamicauctioneers.co.za carry signed, single-use, expiring approve / request-changes links, so an approver can act without logging in.
- **Users & auth:** ~5 accounts, email + password (bcrypt), two roles — **marketing** (run jobs) and **approver** (sign gates). No SSO in v1.
- **Tech:** FastAPI + Jinja2 + HTMX (no JS build chain), the same SQLite DB as M4, background work via a jobs table + single worker loop — zero-ops on one small VPS. *(Hosting: open question #12.)*
- **Acceptance criteria:**
  - [~] Nikki takes DP3060 from upload to posted without ever touching a terminal — *whole UI flow built; the final "posted" step needs the GHL token (Q10/Q13), else a ready-to-post pack*
  - [x] Every state transition appears on the job board within seconds (HTMX poll)
  - [x] An approver actions gate 2 from the email link alone, no login (signed single-use expiring token; tested)
  - [x] Poison-marker PII test passes on every screen that renders public artifacts (review verdict: pass; test in `tests/test_webapp.py`)
  - [x] A change request regenerates artifacts and returns to gate 2 in one screen flow

## 6. Technology stack

| Concern | Choice | Rationale |
|---|---|---|
| Language | Python 3 | already on the machine with PyMuPDF, Pillow, fontTools; fastest path |
| AI | Anthropic API, `claude-opus-4-8`, adaptive thinking (`{"type": "adaptive"}`) | extraction accuracy on messy PDFs is the whole game; structured outputs (`messages.parse` + Pydantic) make output machine-safe |
| Web research | server-side `web_search_20260209` tool | no scraper infrastructure to maintain |
| PDF parsing | native PDF document blocks to Claude; PyMuPDF for photo extraction | proven in Phase 0 |
| Store | SQLite | single-operator scale; zero ops |
| Templates | HTML + brand tokens; Playwright/wkhtmltoimage for PNG/PDF export later | demo-ad already built this way |
| Watcher | Phase 1: `watchdog` on a local folder; Phase 5: Microsoft Graph delta API on the SharePoint library | files already live in OneDrive |
| Interface | Phase 1–3: CLI (`engine ingest DP3060`, `engine approve DP3060`) as the dev surface; Phase 4+: the M8 platform web app is the product | pipeline first, then the team-facing platform (D15) |
| Web UI | FastAPI + Jinja2 + HTMX (M8) | one Python stack, no JS build chain; HTMX covers what a job board needs |
| Auth | session cookies + bcrypt; roles: marketing / approver | ~5 users; SSO is not worth it in v1 |
| Background jobs | SQLite jobs table + single worker loop | extraction/render/post run off-request without Redis or Celery |
| Hosting | one small VPS with HTTPS (Caddy) | zero-ops; fits the R6k/mo consolidation budget *(open question #12)* |

**Cost per property (estimate, at $5/$25 per MTok):** extraction ~R5, verification with web search ~R10, copy generation ~R3 → **under R20/property**, noise against the R6k/mo tool budget. Re-renders on updates cost cents (prompt caching on the stable system prompt).

## 7. Phased build plan

Each phase ends with a demo moment — something Ronnie can watch happen.

| Phase | Deliverable | Demo moment | Status |
|---|---|---|---|
| **0** | Manual prototype: record, memo, 4 ad formats, branded demo-ad artifact for DP3060 | "This is what the machine will produce" | ✅ done 2026-07-08 |
| **1** | `engine ingest <pair>` CLI: intake + Claude extraction + SQLite + photo extraction | drop 2 PDFs, get a validated record in 3 min | ✅ code done 2026-07-08 (live extraction pending API key) |
| **2** | Verification agent + memo + sign-off gate; state machine extended with gate 3 + `assets_built` (D13) | the machine catches the garage lie on its own | ✅ fully live 2026-07-23 (D31): deterministic flags + live web research (11 searches) in one run; the memo's market section found an active listing at the same address (R995k, inside the EVM range) and the "Pelham vs Pelham North" portal-naming insight |
| **3** | Rendering engine: all formats incl. the §12 artifact set (icon, SAIA banner, info pack, mailer, boards), re-render on change; renderer backend interface with the Canva scaffold (D14) | change the price once, watch every artifact regenerate | ✅ code done 2026-07-09 (html backend live; copy uses template fallback without a key) |
| **4** | **Platform v1 (M8):** job board, pair upload, gates 1–3 in the UI, approve-by-email links, artifact pack download; deployed at a URL the team opens | Nikki runs a property end to end without Keegan | ✅ code done 2026-07-09 (`uvicorn webapp.main:app`; hosting is open Q12) |
| **5** | Distribution v1: GHL Social Planner posting + per-channel status (D11), alert mailer generation, manual packs; Prop Data feed if access confirmed; generated buyer-facing Property Report | a price drop goes from record edit to live channels in minutes | ✅ live posting verified 2026-07-22 (D27-D30): drafts/schedule/post-now to FB/IG/LinkedIn with hosted images through the real GHL API, `GHL_POST_STATUS=draft` guard rail on; manual packs + status log done. Outstanding: Prop Data feed access (Q10) |
| **6** | OneDrive/Graph watcher: system reacts to their real folders, zero new habits needed (**convenience only — superseded by D20:** the platform upload covers intake and the gate-2 Photos panel covers adding/fixing photos, so no MS Graph dependency remains) | Gerrie saves a Lightstone PDF to the normal folder; the demo ad appears | 🟡 optional nicety (`engine/watch/graph.py` placeholder) pending MS Graph creds (Q12); no longer on the critical path |
| **7** | Buyer CRM seed: DP-tagged enquiries + matched-buyer broadcasts | "214 buyers notified" on a new listing | ✅ code done 2026-07-09 (`engine/crm.py`) |

Rule: no phase starts until the previous phase's acceptance criteria are checked and Keegan has demoed it (to himself or to Dynamic).

## 8. Non-functional requirements

- **POPIA:** structural PII separation (§4.4); poisoned-marker test in CI for every renderer; flag to Ronnie that the current Property Report cover prints owner name + ID — the generated buyer variant must not.
- **Auditability:** every artifact version, approval, and outbound push logged per DP → doubles as Proof of Marketing.
- **Failure handling:** extraction/verification failures park the record in an error state with the raw model output attached; nothing silently proceeds. API calls retried per SDK defaults; anything still failing surfaces in the daily digest.
- **No hallucination tolerance:** any fact in an ad must trace to a record field; any record field must trace to a source doc or a verified research citation.
- **Secrets:** API keys in `.env`, never in the repo; channel tokens (Meta, Graph) likewise.

## 9. Decision log

| # | Date | Decision | Why |
|---|---|---|---|
| D32 | 2026-07-23 | **Second-property live test (Erf 2035, Somerset Park, Umhlanga) exposed a cache bug and two schema gaps; all three fixed.** The first extraction of a never-seen property ran live end to end in native PDF mode (the production default, previously only exercised in text mode): a 7-page Lightstone EVM + a 21-page registered valuer's report, six section calls, valid record first try, and it recorded the 1-vs-2 garages disagreement unprompted. Three findings. (1) **Prompt caching never hit** (six ~57k cache writes, zero reads, ~R42 instead of ~R16): each section call sent a different single tool, and tool definitions serialize into the cached prefix AHEAD of the system block, so every call invalidated the cache from position zero. Fix: per-section tool names (`record_<section>`), the **identical full six-tool list on every call**, the directive text names which tool to use, and the forced retry names that section's tool; a wrong-section tool call counts as no call. Cuts extraction cost ~3x. (2) **Cross-source conflicts beyond garages were silently resolved** (EVM said 2 bedrooms / 106 m2 under roof; the valuer's inspection said 4 / 310 m2 - the record kept the inspection values with no note, while noting the garage half of the same EVM line): new `physical.conflicts` list, extraction is instructed to record every disagreement, and each entry raises a blocking `PHYSICAL_CONFLICT` flag, same rationale as the garage block. (3) **A registered valuer's figures had nowhere to live** - the market value (R3.4m, above the EVM high of R2.91m) and the forced-sale value (R2.38m, the number a liquidation firm actually works from) were absent from the record: new `valuation.professional` block (market value, forced-sale value, date, valuer). It is **sale-strategy data, not ad material**: `public_view()` strips the whole block exactly like the POPIA layer, so no renderer or copy model can ever put "forced sale value" in an ad; it reaches the memo and gate screens only, and a `VALUATION_DIVERGENCE` note fires when the valuer's figure falls outside the EVM range. A 10-agent field-by-field audit of the extracted record against both PDFs scored 61/63 fields correct with zero hallucinations; the 4 confirmed findings were all schema gaps, not extraction errors. 4 new tests; suite 177 pass / 1 skip. | the second property is the first proof the engine generalizes beyond its golden case; the audit showed the extraction itself is sound and the failures were all "the record had no place to put what the documents said" - and a liquidation firm's core number (forced sale value) must be captured but must never leak into marketing |
| D31 | 2026-07-23 | **M3's web-research half ran live and passed; the memo carries findings, not workings.** First live `research_market` run on the golden DP3060 (the account's usage tier rose to 500k input tokens/min, so the D21-era rate ceiling is gone; native PDF + no pacing are viable defaults again): 11 web searches, ~52k in / 2.4k out (~R8/run). It corroborated the record - the address resolves, an **active listing exists at the same address ("1 Kyalami, 40 Topham Road", 2 bed flat, R995 000)** sitting inside the EVM range 890k-1.05m, and surfaced a real marketing insight: the portals label the suburb "Pelham", not "Pelham North", so ad wording should follow the portal convention. Full `verify()` produced the same three deterministic flags (garage BLOCK + flatlet + spelling) plus the market section; state -> `flags_raised`. One quality fix: a tool-using turn interleaves narration ("Let me search...") between searches and `research_market` had concatenated every text block into the memo - it now keeps only the text after the final tool block (the findings), with sources still collected from every search; a no-tools turn keeps all its text. 2 new offline tests; suite 173 pass / 1 skip. | the sign-off memo is a decision document for a human gatekeeper - it needs the model's conclusions and sources, not its thinking-out-loud; and the live run proves the last never-exercised pipeline piece on real data |
| D30 | 2026-07-22 | **Media hosting via the GHL Media Library; the Canva design previews as a PNG everywhere; legacy-record migration; extraction normalizers.** Four pieces, all live-verified. (1) **Posts carry images now:** `upload_media` uploads each postable raster file to the GHL Media Library (`/medias/upload-file`, scope `medias.write`) and the post body carries the returned CDN URLs - never local paths; selection is property photos (hero first) then raster image artifacts, capped at 4 (`_postable_images`), SVG explicitly excluded (it was previously matched by the naive `image/*` test and would have been attached); a failed upload drops that image with a note and the post still goes out. Verified live: a draft to FB/IG/LinkedIn carried 3 hosted photos (`assets.cdn.filesafe.space`), read back, deleted. (2) **The branded design is visible without opening Canva:** the Canva backend now exports **PNG** for every format except the info pack (PDF, a print document) - proven live, the DP3060 demo ad rendered through the real Canva template to `demo_ad.png`; the gate-2 gallery shows image artifacts as inline `<img>` (click = full size) and PDFs in an embedded viewer, and the artifacts page derives its tile kind from the manifest mime instead of a static per-format table (demo_ad was hardcoded "page" and tiled as an icon). Being PNG, the branded one-pager also qualifies as post media. (3) **Two live UI bugs fixed on a browser sweep of all 8 screens:** the preview iframes' `sandbox=""` gave them an opaque origin so the auth-gated photo route never received the session cookie (ads previewed with broken photos) - now `sandbox="allow-same-origin"` (still no scripts); plus the artifacts-tile mime fix above. (4) **Pre-D19 records were unloadable** (stored `whatsapp_broadcast` vs `extra="forbid"` - found when the live render refused the golden record): `RecordStore.get` now strips known-legacy paths (`_LEGACY_PATHS`) before validation, so old records stay readable and shed dead fields on next save. Plus the D23 follow-up **normalizers** (`normalize_record`, applied after assembly): `2026/07/03`->ISO dates, `Sectional Title`->`sectional`, ALL-CAPS zoning->title case; unknown shapes pass through untouched (never invent). An adversarial review (3 dimensions x find-then-verify, 11 agents) confirmed 7 findings, all fixed: upload-failure notes now survive the media_urls request rebuild (a partially-imaged post was reported as clean - the guard rail itself was verified to survive the rebuild); a missing raster artifact file is noted, not silently dropped; the manual-pack checklist now lists the postable images and labels non-image artifacts "copy/reference (not post media)" instead of "attach"; a multi-page Canva PNG export warns that only page 1 is used; and **generated render output is untracked + gitignored** (`DP*/artifacts/`, `DP*/packs/`) because the manifest carries a live Canva edit-capability URL - the fresh URL never reached the repo; an expiring July-10 one remains in git history only. Suite 171 pass / 1 skip. | closes the last posting gap (image posts), gives the client-facing "see the design without Canva" review path the owner asked for, and hardens the store + extraction output against exactly the failures the live runs surfaced |
| D29 | 2026-07-22 | **End-to-end verified; two fixes the E2E surfaced.** A live run (golden DP3060 -> `render_all` -> `post_to_planner` -> GHL) exposed two gaps, both fixed: (1) **routing** - `channel_matrix` only enabled `facebook` among socials, so IG/LinkedIn (connected in GHL, wanted per D26) never posted; now `facebook`/`instagram`/`linkedin` all route, and the webapp Settings default for LinkedIn flipped on; (2) **caption** - `_caption_for` returned the raw `facebook_post` artifact including its `# Facebook Post DP3060` Markdown title, which would show in the post; now `_strip_leading_heading` removes leading heading/blank lines (hashtags like `#Auction`, no space, are preserved). Re-run confirmed live: one GHL **draft** created across Facebook + Instagram + LinkedIn (3 accounts), caption starting at the real copy, status `draft` (guard rail), then deleted - no lingering test posts. 3 new tests; suite 161 pass / 1 skip. The record->render->GHL path is proven end to end (text-only until media hosting). | the pilot must actually reach all three of DA's pages with a clean caption; the E2E turned two silent gaps into fixed, tested behaviour |
| D28 | 2026-07-22 | **"When to post" in the app: Save-as-draft / Schedule / Post-now, with `GHL_POST_STATUS` as a hard guard rail.** The post screen gains a mode selector (+ a datetime for scheduling); the route maps it to a GHL `status` + `scheduleDate` through `post_to_planner`. `GHL_POST_STATUS`, when set, **overrides** the per-post choice (not just a default) so a `draft` lock cannot be crossed by accident; the UI shows a "Draft-only safeguard is on" banner and the result says when the choice was forced. Scheduling requires a datetime (else blocked). The webapp now `load_dotenv()`s at import so the guard rail + `GHL_USER_ID` actually reach it (the autouse hermetic `conftest` fixture keeps tests offline). An **adversarial review (3 dimensions x find-then-verify, 8 agents) caught 4 real defects, all fixed:** (1) [high] the guard rail was enforced case-sensitively (`== "draft"` on raw env) while the UI normalized with `.strip().lower()`, so `GHL_POST_STATUS=Draft` showed the lock on but let a scheduled/publish request out - now both sides normalize; (2) [high] the summary branched on the DB-Settings token while `post_to_planner` falls back to the env token, so a live post could fire under a "nothing posted" message - the route now resolves the same token and the summary is driven by `posted_social`, not token presence; (3) [med] a failed live call was reported as "posted live" - now gated on `posted_social`; (4) [med] the schedule time was sent zone-less (GHL reads it as UTC -> SA 17:00 fired at 19:00) - now stamped with the SAST `+02:00` offset, **confirmed live against GHL 2026-07-22**: an explicit `+02:00` and an explicit `Z`/UTC value are both accepted and GHL normalizes them to the same UTC instant, so the offset is honored (GHL needs no separate timezone param, per GHL support); a zone-less value is what would have mis-fired. Also confirmed: scheduled posts require the `media` key (empty array ok), which `build_planner_request` already always sends. 12 new/updated tests incl. case-insensitive lock, SAST stamping, honest not-posted summary; suite 159 pass / 1 skip. | gives DA the schedule/post-now control they asked for while keeping "never publish by accident" enforceable in code; the review turned two latent live-publish holes into fixed, tested guarantees before they reached production |
| D27 | 2026-07-22 | **GHL Social Planner posting verified live; `userId` + configurable `status` wired; test suite made hermetic.** First real call proved GHL's create-post **requires a `userId`** (the planner user that owns the post) - a 422 without it - which we deliberately have no `users` scope for, so it is harvested once from an existing post's `createdBy` and stored as `GHL_USER_ID` in `.env`. `build_planner_request`/`post_to_planner` now send `userId` (from `GHL_USER_ID`) and a `status` from `GHL_POST_STATUS` (default `published`, set `draft` to stage without publishing); a missing userId degrades to a ready-to-post pack (like a missing token), never a failed send. **Verified end to end:** a `draft` named "devtest" was created against the live API to Facebook/Instagram/LinkedIn (read-back confirmed `status=draft`, nothing published; a guard deletes anything not-a-draft). Account discovery works on the minimal scopes (`socialplanner/account.readonly` + `post.readonly/write` + `medias`); the `posts/list` endpoint wants `skip`/`limit` as number-strings (GHL quirk). **Safety fix:** `engine.extract`/`cli` call `load_dotenv()` at import, which leaked a developer's real `.env` (Anthropic + GHL creds) into the whole pytest session - a distribution test reaching the real-call path would have fired a LIVE post once `userId` was wired. Added an autouse `conftest` fixture stripping all external creds per-test, so the suite stays offline/credential-free as documented. Remaining gap: public media hosting (posts are text-only until then). 5 new tests (userId/status in body, defaults, pack-without-userId, draft-via-injected-client) + hermetic fixture; suite 148 pass / 1 skip. | makes the engine actually able to post through GHL (the create API is unusable without userId), keeps a draft-first safety switch for rollout, and closes a real hole where the offline suite could publish to DA's live pages |
| D26 | 2026-07-22 | **TikTok dropped as a posting channel** (owner decision: "we not gonna post to tiktok"). Resolves the D24 open item. Removed from `GHL_SOCIAL_CHANNELS` (`ghl.py`), the post-screen `_SOCIAL` set (`post.py`), the settings channel toggles (`settings.py`), and every delete-caveat / "deleted old posts" string (Instagram is now the only channel in our set with no delete API, so the wording is Instagram-only). Zero references remain; suite 144 pass. The GHL sub-account still has TikTok connected, but the engine never targets it, so `GHL_ACCOUNT_MAP` omits it. Posting channels are now Facebook, Instagram, LinkedIn, X. | consistent with D24 (static-image-only) since TikTok is video-first; the owner does not want DA's buyers reached there |
| D25 | 2026-07-22 | **Photos extracted from the Property Report PDF are low-res thumbnails; the uploader now warns (non-blocking).** Measured on DP3060: the report embeds photos small (mostly 276x207 ~0.06 MP; the chosen hero is 276x207), because an inspection report lays photos out tiny. We extract at native embedded resolution (`engine/photos.py`), so we cannot recover pixels the source lacks - a photo posted straight from the PDF looks soft (social feeds render ~1080px wide). The gate-2 Photos panel now flags any image whose shorter side is under `_MIN_PHOTO_PX = 1080` with a "Low-res {WxH}" badge + hint, computed via Pillow (`_image_dimensions`, graceful None on unreadable so no false alarm), and the upload hint asks for large photos. It is a **warning, not a block** (owner decision: "warn... but still let them continue if needed"). This reinforces D20: production-quality photos come from the property's Media folder via upload, not the report PDF. The branded graphics are HTML/SVG (resolution-independent when rasterised), so this concerns photos only. 3 new tests (warns-but-saves, full-res-not-flagged, dimensions-None-for-unreadable); suite 144 pass / 1 skip. | stops a pixelated photo going out by accident without forcing a hard gate, and records why PDF photos are not posting-quality |
| D24 | 2026-07-21 | **Automated posting is static-image-only; video is out of scope.** The distribution path (`engine/distribute/ghl.py` `_media_entries`) now attaches only `image/*` artifacts to a GHL Social Planner post; the former `video/*` branch is removed, so a video is never sent to the API. Any non-image artifact (the HTML/SVG demo ad, banners, boards, or a video) is not posted and is noted for a human to handle (rasterise graphics to an image; post any video natively). The pipeline currently generates no video anyway, so this locks in the reality rather than changing behaviour. Owner decision (2026-07-21): "we just gonna do static posts." Aligns cleanly with D-log's trending-sound limit — video/Reels are exactly the posts that must be published natively (to attach a licensed trending sound), so the automation boundary (static auto, video manual) matches the platform boundary. Only three references existed, all in one function; no tests/templates touched. Suite 141 pass / 1 skip. Open: TikTok is still listed in `GHL_SOCIAL_CHANNELS` though it is video-first (photo posts exist but are secondary) - left in pending a separate channel decision. | static images post cleanly through the GHL API with no format/sound caveats; removing the video path prevents ever auto-posting a video that would need a human for sound/native features |
| D23 | 2026-07-17 | **Extraction uses non-strict tool use, not strict `messages.parse`; golden DP3060 run passed.** The strict grammar path is unusable for these schemas: even the flat `identity` section is rejected `400 "Schema is too complex"` (every field optional -> unions, under `extra="forbid"`), and the full record is `400 "grammar too large"` (D21). So each section is now one **non-strict tool** whose `input_schema` is the section's Pydantic schema; the model's tool call carries the fields and `_extract_section` validates them with the same model (`model_validate`). Validation is preserved (hard rule 3) with no grammar ceiling. `tool_choice` stays `auto` (forced choice is disallowed while thinking is on); a forced-tool retry with thinking dropped guarantees the call if the model ever skips it. De-risked live (identity + physical schemas accepted, tool_use returned, validated). **First live golden run (text mode + 65s pacing, on ~$4.83 free credits, no top-up): passed end to end** - both sentinels caught unprompted (`garages_conflict` set, flatlet present), POPIA verified on real PII (owner name + ID captured into `financials_internal`, `public_view()` strips them, no leak), and the money facts match the golden exactly (EVM range 890k-1.05m, municipal 960k, rates 1288, bond 945k, 3 beds, garages null). Grade vs golden: 45/67 leaves exact; the ~17 "mismatches" are overwhelmingly formatting/normalization (dates `2026/07/03` vs ISO, `Sectional Title` vs enum `sectional`, `STANDARD` vs `Standard Bank`, `10 %` vs `10%`), plus a partial `street_address` (dropped "Unit 1 Kyalami"/"Pelham North"); the 3 "extra" are correct (owner PII internal - a POPIA win; `erf 281` harmless) and the 1 "missing" is a golden human meta-note. Follow-up (not blocking): small code-side normalizers (dates->ISO, `title_type`->enum, institution/zoning canonical case). 6 new/updated offline tests; suite 141 pass / 1 skip. | the API forced the move off strict grammar; tool use has no complexity ceiling, still validates, keeps thinking for conflict reasoning, and the golden run proves the extraction is accurate on the facts that matter |
| D22 | 2026-07-17 | **Extraction gains a text-input mode + call pacing, both opt-in, so it runs under a low rate-limit tier.** The entry usage tier caps `claude-opus-4-8` at 10k input tokens/min, and one native-PDF section call is ~26k (both docs); a single such call is rejected outright (429) regardless of pacing. `EXTRACT_PDF_MODE=text` instead sends each PDF's PyMuPDF-extracted text layer as a labelled text block — DP3060 measures native 12.2k + 13.7k vs text 4.3k + 1.4k, so a call drops from ~26k to ~6.4k, under the cap. `EXTRACT_PACE_SECONDS` sleeps between the six section calls so the per-minute budget is not exceeded (65s for the golden run). Both default off: native full-fidelity PDF blocks stay the default (SPEC tech conventions / D-log native-PDF choice unchanged for any account above the entry tier). The trade is real and bounded: text mode drops any fact that lives only in a page image — checked before adopting, the two sentinel findings survive (Lightstone text says "garage" 3x, the Property Report text says "parking" 1x; the flatlet and all valuation figures are in the text layers), but the Property Report is photo-heavy (8 pages, ~2.4k chars, 131 digit-chars) so numeric physical specifics are the most at-risk. The golden DP3060 run therefore doubles as the accuracy test for text mode; native is the answer for anything text mode grades poorly on (needs a tier top-up). 4 new offline tests (text blocks not PDF, env selects mode, pacing waits between-not-before, pace 0 never sleeps); suite 139 pass / 1 skip. | lets the pilot's golden run and first properties run on existing credits at the entry tier and ~5x cheaper per property, without discarding the native-PDF fidelity the spec chose for higher tiers |
| D1 | 2026-07-08 | DP number is the universal key; parent/child for sub-properties | matches their existing convention (`DP3035.1`) |
| D2 | 2026-07-08 | Intake = paired upload (Lightstone EVM + Property Report), both required | per Keegan; Property Report carries inspection ground truth |
| D3 | 2026-07-08 | Lightstone wins deeds/market; inspection wins physical; conflicts → flags | DP3040 + DP3060 both showed real conflicts |
| D4 | 2026-07-08 | Public enquiries route to Dynamic's numbers, occupant contact stays internal | POPIA + professionalism |
| D5 | 2026-07-08 | Private Property excluded; JamesEdition only ≥ R10m | per Ronnie's notes |
| D6 | 2026-07-08 | Python + SQLite + CLI-first; UI only after pipeline works | speed, zero ops |
| D7 | 2026-07-08 | Claude `claude-opus-4-8` for extraction/verification/copy | accuracy over pennies; <R20/property all-in |
| D8 | 2026-07-08 | Missing facts modelled as `null` (not a separate `confidence: "missing"` flag); source-attribution rides on the merge rule + `*_conflict`/`*_note` fields | keeps the schema identical to the Phase 0 `record.json` so extraction can be graded against the golden record; `extra="forbid"` blocks hallucinated fields |
| D9 | 2026-07-08 | Owner name/ID + occupant cell captured into `financials_internal.owner` / `sale_process.viewing.contact_internal_only`; `PropertyRecord.public_view()` physically strips the internal layer | POPIA by architecture (§4.4) — public renderers receive a projection that cannot contain PII; enforced by a poison-marker test |
| D10 | 2026-07-08 | `record.json` formalised as `engine/schema.py` (Pydantic v2); SQLite record store keyed by bare DP (`3060`), lifecycle in a `state` column + `state_events` audit table; DB default `./engine.db` | closes the §4.2 "formalise a JSON Schema in Phase 1" action; state machine enforced in code per §4.3 |
| D11 | 2026-07-08 | Social posting v1 goes through GoHighLevel's Social Planner API (Private Integration token in the Dynamic Solutions sub-account; scopes + setup in `dynamicAuctioneers/03-Requirements-and-Action-Plan.md`); the mass-delete feature is dropped | DA explicitly asked for GHL integration; one API posts FB/IG/LinkedIn/TikTok/X. GHL deletes are planner-only — they never remove live posts, and IG/TikTok have no delete API at all. Redo need is covered by the approval gates; optional later: 15–30 min grace-window recall |
| D12 | 2026-07-08 | The Canva ad templates are recreated once as M5 HTML brand-token templates; Canva is not a pipeline dependency | Canva's autofill/brand-template API requires Canva Enterprise for developer *and* users (canva.dev autofill guide) — **DA is on Canva Teams, which doesn't qualify** (paid plans only get a small dev-trial quota). HTML templates are free, versionable, re-render instantly on price changes, and are already proven in Phase 0. Fallback if ever needed: engine emits a CSV → human runs Canva's in-app Bulk Create (works on Teams) — **Enterprise-only premise superseded by D16 (autofill proven on Teams 2026-07-10); HTML-first still the default** |
| D13 | 2026-07-08 | Lifecycle gains **gate 3 — client approval**: `approved → client_approved → assets_built → live`; client email out/in stays manual, change requests after gate 3 are internal-only | matches the real marketing workflow (§12). Fold into §4.3 + `store.py` when M5 lands — code currently implements the pre-gate-3 machine |
| D14 | 2026-07-08 | Rendering goes through a swappable backend interface (`engine/render/`): html default, Canva Enterprise autofill as a config-gated, one-move-removable scaffold (spec in M5) | keeps the Canva Enterprise door open without making it load-bearing: if DA upgrades, flip `ENGINE_RENDERER=canva`; if not, delete one file + one registry line. DA is on Canva Teams today (D12), so the scaffold is only testable via the dev-trial quota — **D16: autofill since proven working end-to-end on Teams; the scaffold is a live, usable backend, not just Enterprise-gated** |
| D15 | 2026-07-08 | **Platform pivot:** the deliverable is a separate web platform (M8) the DA marketing team drives themselves; the CLI is demoted to a dev surface. Stack: FastAPI + Jinja2 + HTMX over the same SQLite, jobs-table worker, one VPS | per Keegan — DA must be able to automate their own work without him in the daily loop; zero-ops stack keeps the R6k/mo consolidation promise; amends D6 |
| D21 | 2026-07-16 | **Extraction is sectioned: one structured-output call per source-derived section, cached PDFs, code-side assembly.** The first live DP3060 run proved the one-shot design impossible: the full `PropertyRecord` schema is rejected by the API (HTTP 400, "The compiled grammar is too large"). `engine/extract.py` now runs one `messages.parse` per section — `sources`, `identity`, `physical`, `valuation`, `financials_internal`, `sale_process` — each against its own small schema, and assembles the `PropertyRecord` in code. Every call shares an identical prefix (system brief + both PDF document blocks, cache breakpoint on the second PDF, ~26.8k input tokens measured for DP3060), so the PDFs are paid once (1.25x write) and read from cache (0.1x) for the remaining five calls: ~1.75x single-pass input cost instead of 6x. Code stamps what the model must never answer: `dp`/`parent_dp`, `status`, `record_created`, the real source file paths, and `compliance.owner_pii_redacted` (structural — the schema confines owner PII to `financials_internal` and `public_view()` strips it); `marketing` and `verification` stay `None` for the later stages. Structured outputs still validate every section (hard rule 3 unchanged: validated JSON, never free text). 8 new offline tests (`test_extract.py`, incl. identical-cached-prefix + fake-client assembly); suite 135 pass / 1 skip. **Also found on the same run:** the account's entry usage tier caps `claude-opus-4-8` at 10k input tokens/minute, below a single 26.8k PDF-carrying call — the golden run needs the account topped up to the next tier (or a Batch-API rework later for bulk); tracked as the blocker for the M1 live acceptance box. | the API forced the split; the cached-prefix layout keeps cost near one pass, the smaller per-section schemas are also likelier to extract accurately, and code-owned stamps remove a class of model hallucination surface |
| D20 | 2026-07-15 | **Photos are managed in the gate-2 editor; that supersedes the OneDrive/Graph watcher as the photo path.** The gate-2 ad-review screen gains a Photos panel (upload / set-lead / remove) so a human adds or fixes photos directly on the record — the answer to "what if the Property Report PDF has no embedded images, or the wrong ones" (§M1 extracts photos from the PDF; production photos live in the property's Media folder, not always in the report). Uploads write the **canonical** `marketing.hero_photo` + `marketing.gallery` (relative `photos/<name>`), not `human_overrides`, so every backend — including Canva asset-upload — picks them up; files are saved under `DP<dp>/photos/` and served by an auth-gated route (`GET /gates/{dp}/ads/photos/{name}`, basename-only, path-traversal-safe) which also fixed the previously-broken gate-2 preview `<img src="../photos/…">`. Guards: image MIME/extension allowlist, 12 MB/file, 40 files/upload, duplicate names de-collided (`front.png`→`front_1.png`); a no-op (deleting a non-existent photo, re-setting the current lead) neither reopens `live → updated` nor re-renders nor writes a false audit line; a real photo edit on a `live` record reopens it and requires the same one-internal-approval repost as D17. This is the primary way photos reach a record, so **Phase 6's OneDrive/Graph watcher is now a convenience-only nicety, not a dependency** — the platform upload (intake) + this panel cover the whole photo lifecycle without MS Graph. 8 new tests, suite 131 pass / 1 skip, offline/key-free. | closes "human adds/fixes photos" without waiting on MS Graph creds (Q12); keeps photos canonical so Canva + html + mixed all render them; the user chose this over wiring up OneDrive |
| D19 | 2026-07-15 | **Broadcast messaging channel removed** (owner decision — not needed for DA's buyer base). The broadcast format, its routing/schema/settings/pack entries, the distribute module + CLI preview were all deleted; render output is now 9 formats and the price-drop re-engagement burst is Facebook-only. The CRM's free-text enquiry `source` is unchanged. Suite 119 pass. | avoided a paid messaging line (~R1,500/mo) + a live-send integration for reach DA does not want. Logged so the channel is not re-added later as a "missing" feature; supersedes the corresponding rows in D5 and M6. |
| D18 | 2026-07-15 | **Per-format `mixed` render mode.** `render_all`/`render_one` accept `backend="mixed"` (or `ENGINE_RENDERER=mixed`): each format is rendered by the best available backend that supports it — a premium backend (Canva, for the formats in `CANVA_TEMPLATE_MAP`, today `demo_ad`) where configured and available, and the always-available html default for everything else — in **one pass, one manifest**. Resolves the D14/D16 limitation where selecting `canva` rendered only the mapped format and dropped the html channel copies (portal/FB/info pack/…). A premium backend that errors at render time (e.g. Canva autofill quota / HTTP 429) **falls back to html** for that format (logged to stderr) so the pass still yields a complete set; an explicitly-named single backend is unchanged (renders only what it supports, and a failure propagates). html stays the default; `mixed` is opt-in via env or arg. Resolver + fallback live in `engine/render/service.py` (`_format_backends`, `_render_format`); tests in `test_mixed.py`. | delivers the branded Canva one-pager **and** the html channel copies together, without weakening html-first + graceful degradation (D12) |
| D17 | 2026-07-14 | **Small edits on live listings (`human_overrides` editor + one-approval repost).** Marketers may correct a bounded set of public fields on an already-posted listing (v1 UI: `headline`, `price_display`, `identity.street_address`, `identity.suburb`, `sale_process.method`, `sale_process.terms`) and repost, without redoing the listing. Fact edits persist as a top-level `human_overrides` map (dotted public-view path → value) applied **last** inside `PropertyRecord.public_view()`, never by overwriting the sourced field — so every claim still traces to a source (hard rule 3), the edit survives an M2 re-extraction, and it is reversible. `human_overrides` can never recreate a POPIA-stripped path (guarded in `schema._apply_overrides` **and** on write in `service.apply_edits`). `price_display`/`headline` keep their dedicated `marketing` homes + the copy overlay (price still formats via `_format_price`); every other public fact rides the overrides map. Editing a `live` record reopens it `live → updated`; the repost needs **one internal approval** — recorded as a `gate=repost` sign-off — because the client already approved this listing at first go-live, so **gate 3 is not re-run** (owner decision, 2026-07-14). Enforced by `store.internally_approved_since_last_edit` (any later edit invalidates the approval); distribution of an `updated` repost additionally requires an explicit "old IG/TikTok posts deleted" confirmation (no delete API, §12 step 8). Dates stay free-text in `sale_process.terms` (no structured date field yet — owner deferred). Board index columns now derive from `public_view()` so an overridden suburb/price shows on the board. Two bugs fixed in the same change: board gate-action hrefs (`/gates/{dp}/verify\|ads\|client`, were 404) and the gate-2 approve guard (a still-`live` record could be walked forward with no sign-off). Acceptance: `human_overrides` applied last and cannot re-add PII; edits logged old→new on `state_events`; typed price `900000`→`R900 000`; one render per multi-field save; `updated` repost blocked until internal approval **and** delete-ack; 14 new tests (`test_edits.py` + `test_webapp.py`), suite 98 pass / 1 skip, offline/key-free. | enacts the §M5/§M8 "edits stored back on the record so re-renders keep them" promise for **facts**, not just the two copy strings; closes the §12 change-loop for small edits without weakening the two-gate contract |
| D16 | 2026-07-10 | **Canva autofill works on DA's Teams plan** — the D12/D14 "Enterprise-only" premise is disproven. Proven by a live end-to-end DP3060 render through `canva_backend`: refresh-token exchange → 4 photo uploads → autofill on a 13-field brand template → PDF export → download, all green. The backend now fetches the template `dataset` and fills only the fields it declares, so one backend serves any DA template; every value comes from `public_view` and anything unknown is **blanked, never invented** (garages `null` → "GUEST PARKING" from the parking feature; `mandate_ref` `null` → MASTER REF blank; over-long headline → concise field-composed form). Added optional `identity.mandate_ref` (the "MASTER REF" line, shared by sub-lots). `CANVA_TEMPLATE_MAP` maps `demo_ad → EAHO9qkNlwE` ("NewKeeganTest TEMPLATE"). D12's HTML-first default stands (free, versionable, instant re-render); Canva is now a **proven-usable** alternative, not just a scaffold. `renders_locally=False` marks the backend remote so the offline poison test skips it (its PII contract is payload-level, in `test_canva.py`) | supersedes the Enterprise-only claim in D12 + D14 |

## 10. Open questions (owner → answer feeds spec)

1. **Garages on DP3060** — Gerrie: Lightstone says 3, inspection says none. Which? *(blocks that ad only)*
2. **Sub-property auction dates** — Ronnie/Gerrie: do `.1/.2` lots always share the parent's sale event?
3. **Prop Data standalone feed access** — email api-support@propdata.net *(blocks the biggest M6 win)*
4. **One industrial/commercial Lightstone sample** — needed for the M2 commercial parser path
5. **Website platform** — what runs dynamicauctioneers.co.za; is there an API/CMS?
6. **Email channel during GHL migration** — keep GHL sending, or stand up a cheap ESP?
7. **JamesEdition listing method** — manual portal, feed, or API?
8. **ze.NOTES file** — get one sample; may carry auction logistics the reports don't
9. **Artifact specs for the §12 set** — one sample each of webapp icon/tile, SAIA banner, and auction board (dimensions, where uploaded, which printer) from Nikki/Abigail before M5 templates are built
10. **Who is "admin@dynamicauctioneers.co.za"** — which humans sit behind the internal-approval inbox (the "THEM" in the workflow), and do they want email-button approval?
11. **Alert mailer scheduling** — can the mailer be created *and scheduled* via the GHL API, or is v1 "system generates banner + HTML + list, human clicks schedule"? (extends Q6)
12. **Platform hosting + domain** — which VPS/host, who pays, and a subdomain (e.g. marketing.dynamicauctioneers.co.za) needs whoever holds their DNS
13. **Platform user list** — names + emails for the ~5 accounts, and who holds the *approver* role (ties to Q10's "who is behind admin@")

## 11. Glossary

- **DP number** — Dynamic Auctioneers' property file number (`DP3060`); sub-properties `DP3060.1` etc.
- **EVM** — Lightstone's Estimated Value Model report (desktop valuation, deeds data, comps)
- **Property Report** — Dynamic's in-house branded document (inspection findings, terms, photos), prepared by Gerrie
- **Lightstone** — SA property data provider (deeds, valuations, comparables)
- **Prop Data** — SA property-portal syndication backbone (feeds to Property24 etc.)
- **POPIA** — Protection of Personal Information Act (SA privacy law)
- **Instruction** — the engagement/liquidation under which one or more properties are sold
- **Forced-sale value** — valuer's quick-disposal estimate (typically 20–40% below market)
- **SAIA** — SA Institute of Auctioneers (industry body; DA is a member — footer logo, and the alert-mailer audience)

## 12. As-is marketing workflow & automation coverage (Dynamic Solutions > Marketing)

Captured from Keegan (Jul 2026) — what the marketing team does today per property/auction, and the engine's automation verdict on each step.

**As-is steps:**

1. Lightstone doc + Property Report arrive (the M1 pair).
2. Ad is created from the templates sitting in Canva.
3. Ad emailed to **admin@dynamicauctioneers.co.za** for internal approval.
4. Approved ad emailed externally to the client — **stays manual by choice**.
5. Client replies happy. After this point change requests are **internal-only**; the client is not re-consulted.
6. Marketing then produces: **webapp icon/tile**, **SAIA banner** (+ schedules an **alert mailer**), **info pack** (upcoming-auction pages + Meta campaign), **auction boards**, and posts ads on all platforms.
7. Change request: regenerate → internal re-approval → redistribute.
8. If a posted ad changes: delete the social posts, change, repost.

**Automation verdicts:**

| # | Step | Verdict | Mechanism |
|---|---|---|---|
| 1 | Intake | ✅ automated | M1/M2 — built (Phase 1) |
| 2 | Ad creation | ✅ automated | M5 HTML brand-token templates recreating the Canva designs (D12) |
| 3 | Internal approval | ✅ routing automated; the click stays human | artifact set emailed to admin@ with approve / request-changes links (gate 2) |
| 4 | Client email out | ➖ manual by choice | system drafts the email; a human sends it |
| 5 | Client approval in | 🟡 semi | human logs the reply (gate 3); an inbox-watcher can pre-flag it later |
| 6a | Webapp icon/tile | ✅ automated | icon-size template render |
| 6b | SAIA banner + alert mailer | 🟡 mostly | banner + mailer HTML + list generated; scheduling via GHL API if it allows, else one click (Q11) |
| 6c | Info pack | ✅ automated | generated buyer-facing Property Report (M5), auto-attached to the auction page; Meta *paid* campaign stays a human boost in v1 |
| 6d | Auction boards | 🟡 design only | print-ready PDF generated; printing/erecting is physical |
| 6e | Post to all platforms | ✅ automated | GHL Social Planner API (D11), fires only after the gates |
| 7 | Change loop | ✅ automated | `updated` fast-path: re-render every artifact, re-approve, redistribute |
| 8 | Delete + repost | 🟡 partial | regenerate + repost fully automated; **deleting live posts** is manual for IG/TikTok (no delete API exists) and GHL deletes only clean its planner. Optional later: publish on a 15–30 min delay so a recall button works pre-live |

**Irreducible manual moments (everything else automates):** the approval clicks at the gates (by design), sending/receiving client emails (their choice), physically printing + erecting auction boards, and deleting already-live IG/TikTok posts.
