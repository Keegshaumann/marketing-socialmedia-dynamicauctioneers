# 6. Status, Roadmap and Gaps

State of the system as at **20 August 2026**, verified against the code at
commit `9c9e0df`.

## 6.1 Headline

| | |
|---|---|
| **Deployment** | Live on the VPS, HTTP 200, TLS valid and auto-renewing |
| **Test suite** | 370 passed, 15 skipped, 52 seconds — run and confirmed |
| **Pipeline** | Every stage has run live end to end |
| **Build phases** | 0–5 and 7 complete. Phase 6 deliberately optional |
| **Decisions logged** | 77 |
| **Commits** | 115 |
| **Biggest operational gap** | **No backups** |
| **Biggest product gap** | The client fix list, part shipped, part awaiting owner decisions |

## 6.2 Phases

| Phase | Deliverable | Status |
|---|---|---|
| **0** | Manual prototype for DP3060 | Complete, 8 July 2026 |
| **1** | Intake, extraction, store, photo extraction | Complete, 8 July 2026. Live golden extraction verified 17 July |
| **2** | Verification, memo, sign-off gate | **Fully live 23 July 2026** — deterministic flags plus 11 live web searches in one run |
| **3** | Rendering: all formats, re-render on change, backend interface | Complete, 9 July 2026 |
| **4** | Platform v1: board, upload, gates 1–3, approve-by-email, artifact pack | Complete, 9 July 2026. Deployed and live |
| **5** | Distribution: GHL posting, per-channel status, mailers, manual packs | **Live posting verified 22 July 2026**. Prop Data feed still outstanding |
| **6** | OneDrive/Graph watcher | **Optional, off the critical path** — superseded by D20 |
| **7** | Buyer CRM seed | Complete, 9 July 2026 |

Phase 6 stopped being a dependency once photographs became manageable in the
gate-2 editor: the platform upload covers intake and humans fix photographs on
the record, so nothing needs Microsoft Graph. It remains a convenience if
credentials ever appear.

## 6.3 What has been proven live, with dates

| What | When | Result |
|---|---|---|
| **Golden extraction (DP3060)** | 17 July 2026 | Passed end to end. Both sentinels caught unprompted — the garage conflict and the flatlet. POPIA verified on real PII. Money facts match the golden record exactly |
| **Live distribution** | 22 July 2026 | Drafts, scheduling and post-now to Facebook, Instagram and LinkedIn against the real GHL API, with photographs and the advert PNG served as CDN URLs |
| **Live verification research** | 23 July 2026 | 11 searches. Found an active listing at the same address at R995,000, inside the EVM range, and the "portals say Pelham, not Pelham North" insight |
| **Second, never-seen property** | 23 July 2026 | Erf 2035, Somerset Park, Umhlanga. A 7-page EVM and a 21-page valuer's report. Valid record first try, **61 of 63 fields correct** on a field-by-field audit, **zero hallucinations** |
| **Live copywriting** | 23 July 2026 | South African English, correct offers framing, no dashes, no PII, every fact traceable |
| **Price change across the set** | 11 August 2026 | One change reaches all nine artifacts (D64) |
| **Pre-ship review** | 14 August 2026 | 46-check end-to-end run against the real application, on top of the unit suite (D77) |

**Every pipeline stage has now run live.** Nothing in the core flow is
theoretical.

## 6.4 The second property is the number that matters

The golden case can be tuned to. A property the system had never seen, with a
different document set, extracting at 61 of 63 fields with no invented facts, is
the evidence that this generalises.

It also forced three fixes worth knowing about:

- **Prompt caching had never hit.** A per-call tool difference was invalidating
  the prefix. One identical six-tool list made it about three times cheaper.
- **`physical.conflicts` was added.** Cross-source disagreements — two versus
  four bedrooms, 106 versus 310 m² — were being silently resolved. Now each is
  recorded and raises a blocking flag.
- **`valuation.professional` was added**, and `public_view()` strips it like the
  PII layer. Sale-strategy figures never reach a renderer or the copy model.

## 6.5 The client fix list

38 items from the client's handwritten review, triaged on 13 August 2026 by
reading every item against the actual code.

**At triage:** 21 `todo`, 13 `partial`, 4 `conflict`.

**Since triage,** batches shipped under D65–D77 resolving, among others:

| Area | Decision |
|---|---|
| Nothing cut off on an advert; units read as units; no stat printed twice | D66 |
| The pack ships as PDFs; the information pack loses its background | D67 |
| Information pack terms read from the OTP's clauses | D68 |
| Board QR code: the team supplies it, the platform asks for it | D69 |
| Photo cap raised to 40, and every drop adds instead of overwriting | D70 |
| Board weekday derived from the auction date, never typed | D71 |
| Gate 2 batches its edits; one explicit Regenerate renders | D72 |
| Monthly levy read off any statement shape | D73 |
| Auction board rebuilt to the team's own board | D74 |
| Intake classifies and reads the OTP and levy statement onto the record | D75 |
| Highlights lose the sale basis; three real viewing states; replace a photograph in place; lightbox | D76 |

> **Read `TRIAGE.md` with its date in mind.** Its statuses are from 13 August and
> predate those batches. Re-read it against the code before planning the next
> block of work, or you will rebuild something that already shipped.

**The four `conflict` items** are the ones where the client's request contradicts
a logged decision or a hard rule. They need an owner ruling, not a developer
decision:

- **1.4 Multiple named galleries per property** — a genuine schema change.
  Photographs are one ordered list today.
- **3.4 Tenure from a different source** — touches the D35 precedence order.
- **4.1 Follow the DP2987 layout** — the reference is portrait; D61 locked the
  pack to landscape off four of the team's own packs, and going portrait
  discards the measured composition system built in D60, D61 and D63.
- **5.3 Investor spin on highlights, including projected rental ROI** — there is
  no rental field on the schema, and a forecast breaches the no-hallucination
  rule.

## 6.6 Decisions waiting on the owner

`docs/fixlist/BUILD-PLAN.md` sets out 16 questions, each with a recommendation
and its blast radius. The ones with the widest reach:

| # | Question | Recommendation |
|---|---|---|
| **Q1** | Information pack portrait like the DP2987 reference, or landscape as built? | **Stay landscape.** Treat DP2987 as a content and finish reference, not a geometry one |
| **Q2** | Is the DP2987 Dynamic **Real Estate** branding replacing our pack furniture? | **Layout only.** A different brand identity is a second variant, priced separately |
| **Q3** | May municipal or professional valuations, or a projected rental ROI, appear in buyer-facing packs? | **No.** Keep the strip. This is the rule written after the copy model printed a municipal valuation onto a live advert |
| **Q9** | Three advert variations: three designs, or three variants of one? | **Three designs, on an explicit button** — not on every save, which would undo D56's rendering win |
| **Q7** | Which email addresses may appear on an advert? | **A two-option picker**, not free text — free text re-opens the POPIA hole hard rule 1 exists to close |
| **Q8** | "Drag to rearrange": photographs, or advert elements? | **Photographs.** Free layout editing gives up "every advert renders identically from one record" |

Q4, Q13, Q15 and Q16 have since been ruled on by the owner and shipped as D70,
D76, D71 and D69 respectively.

## 6.7 External blockers — credentials and answers, not code

| # | Blocked on | Impact |
|---|---|---|
| **Prop Data feed access** | Emailing `api-support@propdata.net` to confirm standalone access | **The biggest remaining distribution win.** A feed means Property24 updates propagate automatically. Fallbacks: Entegral/Fusion, or Property24 direct |
| **Real domain** | Whoever holds the DNS for `dynamicauctioneers.co.za` | The platform is on a nip.io stand-in hostname. Ten minutes of work once the A record exists |
| **Website platform** | What runs `dynamicauctioneers.co.za`, and whether it has an API or CMS | Blocks automated website publishing |
| **Email channel** | Keep GHL sending, or stand up a cheap ESP during the migration | Blocks the email channel decision |
| **JamesEdition** | Manual portal, feed, or API? | R10m+ properties. Manual-with-generated-pack is an acceptable v1 |
| **Alert mailer scheduling** | Whether GHL's API can create *and schedule* a mailer | Otherwise v1 is "system generates, human clicks schedule" |
| **Who sits behind `admin@`** | Which humans approve, and their emails | Ties to the platform user list |
| **Platform user list** | Names and emails for about five accounts, and who holds `approver` | Needed to hand over |
| **An industrial or commercial EVM sample** | One document | Blocks the commercial parser path |
| **A `ze.NOTES` sample** | One file | May carry auction logistics the reports do not |

## 6.8 Known risks and gaps

**Operational**

1. **No backups.** The most serious gap. A lost VPS is a lost system today. See
   section 4.10 — either option is an afternoon's work.
2. **Single VPS, no redundancy.** Appropriate for the scale, but it means a host
   outage is a platform outage.
3. **No CI.** Tests run on the developer's machine. A pre-deploy hook running the
   suite would have caught neither D64 nor D77 — but it would catch ordinary
   regressions.
4. **No forced first-login password change.** The admin temporary password is
   printed to the journal once and relies on a human changing it.
5. **No HSTS header.** Recommended in D44, not yet added at nginx.

**Documentation**

6. **`ENGINE_HTTPS` is vestigial.** `.env.example`, `DEPLOY.md` and D44 all refer
   to it; no code reads it. The behaviour is secure by default. Harmless but
   misleading — remove it or wire it.
7. **`X-Frame-Options` drift.** D44 records `DENY`; the code sets `SAMEORIGIN`,
   deliberately, so the gate-2 advert preview can be embedded in a same-origin
   iframe. The code is right and the decision text is stale.
8. **`SPEC.md` header is stale.** It still reads "Phases 0–1 complete; platform
   specced, not started". `CLAUDE.md` carries the current status. The header
   should be corrected.
9. **`README.md` is prototype-era**, describing the phase 0 DP3060 demonstration
   rather than the platform.

**Product**

10. **Deleting live Instagram posts stays manual.** No API exists. A 15–30 minute
    publish delay with a recall button is the only real mitigation.
11. **Multi-dwelling properties.** The record has no concept of a second or third
    dwelling — extraction folds every structure into one set of counts plus at
    most one flatlet. This is fix item 2.6 and a real schema change.
12. **Multi-portion packs.** Per-portion detail — a Property Report per portion,
    multi-property boards — has no foothold in a contract that is one record in,
    one artifact out. Fix items 3.7 and 6.4.

**Repository hygiene**

13. **`.env` sits in the repository working directory.** It is gitignored and
    correctly excluded, but it is worth confirming it has never been committed
    before the repository is shared more widely.
14. **`engine.db` in the repository root.** Untracked and correct, but it means a
    careless `rm -rf` of a clone destroys local data.

## 6.9 The next real milestone

Everything technical is in place. The next milestone is not a feature.

> **Nikki runs a property end to end, on the live platform, without the developer
> in the loop.**

That is the acceptance test the whole build was aimed at. What stands between
here and there:

1. Configure backups. Do not go further without this.
2. Cut over to the real domain.
3. Create the real user accounts and confirm who holds `approver`.
4. Get the owner's rulings on Q1, Q2, Q3, Q7, Q8 and Q9, then ship the unblocked
   half of the fix list.
5. Run the acceptance test.
6. Chase Prop Data feed access in parallel — it is the largest remaining win and
   it depends on an email, not on code.
