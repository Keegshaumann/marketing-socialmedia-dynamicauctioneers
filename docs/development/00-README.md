# Marketing Platform — Development Documentation

**System:** Dynamic Auctioneers Marketing Platform
**Repository:** `github.com/Keegshaumann/marketing-socialmedia-dynamicauctioneers`
**Built by:** Keegan Haumann (Cognexa) for Dynamic Auctioneers
**Documentation compiled:** 20 August 2026
**Code state at compile:** `9c9e0df` on `main`, 115 commits, decision log through D77
**Published at:** [SharePoint → Development → Marketing](https://dynamicauctioneers.sharepoint.com/sites/Development/Shared%20Documents/Forms/AllItems.aspx)

---

## What this folder is

The development documentation for the marketing platform: what it is, how it is
built, how the pipeline runs end to end, how to operate the server, how to work
on the code, and what is still outstanding.

It is written for three readers:

- **Dynamic Auctioneers (Ronnie, Nikki, Brad)** — sections 1, 3 and 6 explain
  what the system does, what the team does at each step, and what is still owed.
- **A developer picking the project up** — sections 2, 4 and 5 are the technical
  handover.
- **Whoever operates the server** — section 4 is the runbook.

These documents describe the system **as built**, verified against the code on
20 August 2026, not as originally planned. Where the code and the older
specification disagree, the code wins and the difference is noted.

## The documents

| # | Document | What it covers |
|---|---|---|
| 1 | `01-System-Overview.md` | The problem, the fix, who uses it, what it produces, current status |
| 2 | `02-Architecture.md` | Modules M1–M8, data model, lifecycle, tech stack, repository layout |
| 3 | `03-Process-Walkthrough.md` | The full pipeline, step by step, from documents in to posts out |
| 4 | `04-Operations-Runbook.md` | Server, deployment, users, environment variables, logs, backups |
| 5 | `05-Development-Guide.md` | Local setup, tests, conventions, how to extend the system |
| 6 | `06-Status-Roadmap-and-Gaps.md` | What is done, what is outstanding, open decisions, known risks |

Each document exists as Markdown (source) and PDF (`pdf/`).

The published set lives on the Dynamic Auctioneers **Development** SharePoint
site, alongside the P24 Auto-Responder and Moveables documentation:

<https://dynamicauctioneers.sharepoint.com/sites/Development/Shared%20Documents/Forms/AllItems.aspx>

Markdown is the source of truth and lives in this repository; SharePoint carries
the compiled copy for the team. If you change a document here, re-run
`build-pdfs.py` and re-upload both halves.

## Relationship to the documents already in the repository

This folder does not replace the working documents; it sits above them.

| Existing document | Remains the authority on |
|---|---|
| `SPEC.md` | Module specifications, acceptance criteria and the **decision log (D1–D77)** — the record of why the system is the way it is |
| `CLAUDE.md` | The working brief and current-status ledger used during development |
| `docs/DESIGN-SYSTEM.md` | Platform UI design tokens, layout, motion, states |
| `docs/INFO-PACK-PLAYBOOK.md` | The information pack's page order, composition rules and content rules |
| `docs/deploy/DEPLOY.md` | The original deployment notes (section 4 here supersedes and extends them) |
| `docs/fixlist/` | The client fix list: `TRIAGE.md` (item-by-item state) and `BUILD-PLAN.md` (decisions needed and build order) |

**The rule that governs all of them:** the specification is the source of truth,
and when the implementation diverges the specification is updated in the same
commit. New decisions are logged in the decision log and never re-argued.

## Rebuilding the PDFs

The PDFs are generated from the Markdown by `build-pdfs.py` (Chromium via
Playwright, styled to the Dynamic Auctioneers brand).

```bash
python3.12 docs/development/build-pdfs.py
```

Edit the Markdown, rerun the command, and the PDFs in `pdf/` are replaced.
