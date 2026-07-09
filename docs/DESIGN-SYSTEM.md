# Platform UI Design System (M8) — Dynamic Auctioneers Marketing Platform

**Design read:** internal marketing-ops web app (dense product UI, ~5 users), *not* a landing
page. Editorial/premium "auction-house ledger" aesthetic that extends the Phase 0 demo-ad brand
into an operational tool. Single locked light theme (branded internal tool — not consumer dual-mode).
Stack constraint: **FastAPI + Jinja2 + HTMX, no JS build chain** → hand-written CSS with variables,
HTMX for interactivity, minimal vanilla JS. Icons: inline SVG from a single set (Phosphor/Tabler
paths pasted into a Jinja macro — no icon npm). Montserrat is the brand font (self-host the woff
files if present in the repo/DP3060 assets, else a clean system fallback stack; document which).

## Tokens (reuse the demo-ad palette — `DP3060/ads/demo-ad.template.html`)
```
--gold:#B08D4A; --gold-deep:#8C6D33; --gold-pale:#F1E8D6;
--ink:#191613; --body:#2B2620; --muted:#877E70;
--hairline:#E5DFD4; --ground:#EFEBE3; --sheet:#FFFFFF;
/* state colours, desaturated to sit with the palette */
--block:#9A3B2E;   /* blocking flag / destructive */
--note:#A8792E;    /* awareness flag / warning */
--ok:#4F6B45;      /* verified / success / posted */
--info:#4A6274;    /* neutral status */
```
Shape lock: **sharp corners (radius 0)** on panels, tables, sheets (matches the demo-ad); a small
`3px` radius only on interactive pills/buttons/inputs. Never mix.
Shadows: tint to ink, never pure black — `0 2px 6px rgba(25,22,19,.08), 0 18px 44px rgba(25,22,19,.13)`.
Numbers (prices, m², counts, dates): `font-variant-numeric: tabular-nums`.

## Layout
- **App shell:** dark `--ink` left sidebar (fixed, ~230px) with gold wordmark + nav; warm `--ground`
  content area; a thin gold rule (`linear-gradient` gold sweep, 4px) under the top bar.
- **Job board:** a **ruled ledger table**, not cards — DP · property · state · days-in-state ·
  owner · next action. Hairline row dividers; gold left-border + row lift on hover.
- **Gate screens:** a centred `--sheet` panel with the demo-ad's letterhead/gold-rule chrome, the
  content (memo flags / artifact gallery), and a sticky action bar.
- Density ~5 (ops tool: dense but breathing). `max-width` the content at ~1200px.

## Motion (emilkowal rules; CSS + HTMX only)
- Default easing `cubic-bezier(0.23, 1, 0.32, 1)` (strong ease-out); durations 140–240ms, never >300ms.
- Buttons: `:active { transform: scale(0.97) translateY(1px); }`; asymmetric (press faster than release).
- Job-board rows: staggered fade+rise on load via `animation-delay: calc(var(--i) * 40ms)`.
- HTMX swaps: animate with `.htmx-swapping`/`.htmx-settling` + `@starting-style` (fade + 6px slide).
- Gate approval: press → brief gold sweep across the action bar + a blur-to-sharp "bridge" on the
  new state badge (`polish-blur-bridge`). Toasts (posted/approved) slide up, stack with 14px offset.
- Border/hover: rows & artifact tiles get a gold 1px border + tinted lift on hover
  (transform + box-shadow only — never animate width/height/top/left).
- Everything behind `@media (prefers-reduced-motion: no-preference)`; reduced-motion collapses to
  opacity-only/instant. Only `transform` + `opacity` animate. `will-change: transform` sparingly.

## Interactive states (build all, not just the happy path)
- **Loading:** skeleton rows matching the ledger shape while HTMX fetches (shimmer, not spinners).
- **Empty:** composed empty state ("Drop a document pair to begin") with the drop zone as the CTA.
- **Error:** inline on forms; a contextual banner for job failures (park the record, show raw output link).
- **Drag-drop:** dashed gold drop zone; on dragover, gold fill + scale(1.01); on drop, progress bar
  that advances through intake → extraction states via HTMX polling.

## Non-negotiables that bind the UI (SPEC §8, §4.4)
- **POPIA:** every screen that renders a public artifact renders it from `public_view()` only. The
  poison-marker PII test must pass against the UI's rendered output.
- No em dashes / en dashes anywhere in UI copy (SA English). No emojis in UI chrome.
- Accessible: WCAG AA contrast on all text/controls; visible focus rings; keyboard-operable gates.
