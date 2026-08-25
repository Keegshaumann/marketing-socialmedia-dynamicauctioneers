# 5. Development Guide

## 5.1 Local setup

**Python 3.12.** The dependencies are pinned against it, and a newer interpreter
on the machine will not have them.

```bash
git clone https://github.com/Keegshaumann/marketing-socialmedia-dynamicauctioneers.git
cd marketing-socialmedia-dynamicauctioneers

python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # needed for PNG and PDF export

cp .env.example .env                 # then fill in what you need
```

The application runs without any credentials at all. Everything degrades
gracefully: no `ANTHROPIC_API_KEY` means template copy and deterministic checks
instead of model calls; no GHL token means manual packs instead of posting; no
Canva configuration means the HTML backend. **Nothing crashes for want of a
key** — a missing backend reports why it is unavailable.

### Running it

```bash
uvicorn webapp.main:app --reload            # the platform, http://127.0.0.1:8000
ENGINE_ALLOW_INSECURE_COOKIE=1 uvicorn webapp.main:app --reload   # over plain HTTP
```

The admin temporary password is printed once to the console on first boot.

## 5.2 The CLI

The developer and plumbing surface. It is **not** the product — the team never
touches it (D15).

| Command | Does |
|---|---|
| `engine ingest <dir-or-pair>` | Intake plus extraction. `--dp` to restrict |
| `engine list` | Every stored record: DP, state, suburb, updated |
| `engine show <dp>` | Print the stored record |
| `engine status <dp>` | The lifecycle state |
| `engine verify <dp>` | Run verification and write the memo |
| `engine signoff <dp>` | Record gate 1 sign-off |
| `engine render <dp>` | Render artifacts. Optionally a subset of formats |
| `engine backends` | Which render backends are available, and why not if not |
| `engine set-price <dp>` | Change the price and re-render everything |
| `engine channels <dp>` | Show the computed channel matrix |
| `engine pack <dp>` | Build the manual distribution pack |
| `engine crm-add` / `engine crm-match` | Seed and query the buyer CRM |

All of them take `--db` (or read `ENGINE_DB`).

## 5.3 Tests

```bash
python3.12 -m pytest -q
```

**Current: 370 passed, 15 skipped, about 52 seconds** — run and confirmed on
20 August 2026.

The suite is **hermetic**: offline and key-free. An autouse fixture strips
credentials from the environment per test, so a developer's real `.env` cannot
leak into a test run or cause an accidental live post. The 15 skips are tests
gated on credentials that are deliberately absent.

Twenty-two test modules, covering extraction, schema, intake, store, verify,
render, advert templates, Canva, precedence, OTP, levies, photos, photo picker,
distribution, CRM, jobs, edits, design links, mixed rendering, pack icons and
the webapp.

**The PII poison-marker test is the one that matters most.** It puts a marker in
the internal fields, renders, and asserts the marker is absent — per backend and
on every screen that renders a public artifact.

### What the suite will not catch

Worth internalising, because it has bitten twice:

- **D64** — a price change failing to reach all nine artifacts was found by
  driving the real application, not by a unit test.
- **D77** — the OTP and levy readers shelling out to `pdftotext`, which does not
  exist on the server. The tests passed locally, and the features would have been
  silently dead in production.

The lesson: green tests on the development machine do not prove the deployed
system works. Meaningful changes get an end-to-end run against the real
application.

## 5.4 Conventions

**The specification is the source of truth.**

1. When the implementation diverges from `SPEC.md`, update the specification **in
   the same commit**.
2. New decisions go in the decision log. **Never re-argue a logged decision** —
   read why it was made first.
3. A module is done when its acceptance criteria tick, not before.
4. No phase starts until the previous phase's criteria pass.

**The hard rules that bind any change:**

1. **POPIA** — owner name, ID, occupant contact, bond and arrears live only in
   the internal layer. Renderers receive `public_view`. Enquiries route to
   Dynamic, never the occupant.
2. **Three gates**, enforced by the state machine, not by convention.
3. **No hallucinated facts** — every claim traces to a record field, every field
   to a source document or cited research. Missing means `null`.
4. **Client-facing copy** — South African English, no em or en dashes, no
   AI-sounding constructions. Framing follows `sale_process.method`.
5. **Brand** — gold `#B08D4A`, ink `#191613`, Montserrat, the real letterhead,
   and the full Dynamic Solutions footer with registration and VAT numbers.

**Code conventions:** Python 3, SQLite, secrets in `.env` and never committed.
The engine never imports the webapp. The webapp has no JavaScript build chain —
hand-written CSS with variables, HTMX for interactivity, minimal vanilla
JavaScript, and inline SVG icons from a single Jinja macro.

**UI conventions** are in `docs/DESIGN-SYSTEM.md`: sharp corners on panels and
tables with a 3px radius only on interactive pills, ink-tinted shadows never pure
black, tabular numerals for prices and measurements, easing
`cubic-bezier(0.23, 1, 0.32, 1)` at 140–240ms, only `transform` and `opacity`
animated, and everything behind `prefers-reduced-motion`. Build all interactive
states — loading, empty, error, drag-drop — not just the happy path.

## 5.5 How to extend it

### Add a new artifact format

1. Add the key to `FORMATS` in `engine/render/base.py`.
2. Add the template under `engine/render/templates/`.
3. Teach `html_backend.py` to render it, and `supports()` to claim it.
4. Add it to the artifacts screen and the pack.
5. Add a test, including the poison-marker assertion.

### Add a render backend

Subclass `RenderBackend` in a new `*_backend.py`, implementing `name`,
`available()`, `supports()` and `render()`. Register one lazy-import line in
`engine/render/__init__.py`. Nothing else may import it — the Canva backend
proves removability by being deletable in one move, and a test enforces it.

`available()` must **return a reason, never raise**, so a misconfigured backend
cannot break unrelated commands.

### Add a screen

Add a router module under `webapp/routes/`, expose `router`, and add its name to
`ROUTE_MODULES` in `webapp/main.py`. Modules are included defensively: one that
fails to import is logged and skipped so the application still boots.

### Add a background job kind

Add it to `JOB_KINDS` and write a handler in `webapp/jobs.py`. Handlers return a
status and a message, and park the record with the raw output on failure.

## 5.6 The AI calls, in practice

Two calls cost money, and both are content-addressed cached (D59), so a repeat
run on unchanged input is free.

**Extraction** (`engine/extract.py`) — `claude-opus-4-8`, adaptive thinking,
sectioned into one structured-output call per section, non-strict tool use, PDFs
as native document blocks. The identical six-tool list is sent on every call so
the cached prefix hits; a per-call difference had been silently invalidating it
and fixing it was about a threefold saving.

**Verification research** (`engine/verify.py`) — the server-side
`web_search_20260209` tool.

**Copy** (`engine/render/copy.py`) — channel-aware, and falls back to templates
without a key.

Roughly R20 per property all-in. Re-renders cost cents.

## 5.7 Working with the fix list

`docs/fixlist/` holds the client's 38-item review.

- **`TRIAGE.md`** — every item read against the actual code, with a status
  (`todo`, `partial`, `conflict`), an effort estimate, and the exact file and
  line where it lives. Start here: several items are already half-built, and at
  least one ("the server already appends") is a UI problem rather than the
  backend problem it looks like.
- **`BUILD-PLAN.md`** — the decisions that must be made before code (Q1–Q12),
  each with a recommendation and the blast radius, then the build order.

**Do not start an item whose decision is still open.** Several of them conflict
with logged decisions, and building the wrong reading costs more than asking.
