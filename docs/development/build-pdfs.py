#!/usr/bin/env python3.12
"""Render the development documentation to branded PDFs.

Markdown -> HTML (python-markdown) -> PDF (headless Chromium via Playwright),
styled to the Dynamic Auctioneers brand tokens in docs/DESIGN-SYSTEM.md.

Usage:
    python3.12 docs/development/build-pdfs.py            # all documents
    python3.12 docs/development/build-pdfs.py 03         # just the one matching "03"

Requires: markdown, playwright (+ `playwright install chromium`). Both are in the
project's requirements; Montserrat is used when installed on the machine and
falls back to a clean sans stack when it is not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE / "pdf"

# Brand tokens, mirroring docs/DESIGN-SYSTEM.md.
CSS = """
@page { size: A4; margin: 20mm 16mm 18mm 16mm; }

:root {
  --gold:#B08D4A; --gold-deep:#8C6D33; --gold-pale:#F1E8D6;
  --ink:#191613; --body:#2B2620; --muted:#877E70;
  --hairline:#E5DFD4; --ground:#EFEBE3; --sheet:#FFFFFF;
  --block:#9A3B2E; --note:#A8792E; --ok:#4F6B45;
}

* { box-sizing: border-box; }

body {
  font-family: Montserrat, "Helvetica Neue", Arial, sans-serif;
  font-size: 9.6pt; line-height: 1.55;
  color: var(--body); background: var(--sheet);
  margin: 0; -webkit-print-color-adjust: exact; print-color-adjust: exact;
}

/* Cover ------------------------------------------------------------------ */
.cover { page-break-after: always; padding-top: 52mm; }
.cover .rule { height: 5px; background: linear-gradient(90deg,#ac874a 0%,#ddc689 100%); margin-bottom: 14mm; }
.cover .eyebrow {
  font-size: 8pt; font-weight: 700; letter-spacing: .22em; text-transform: uppercase;
  color: var(--gold-deep); margin-bottom: 7mm;
}
.cover h1 { font-size: 30pt; font-weight: 900; line-height: 1.1; color: var(--ink); margin: 0 0 8mm 0; letter-spacing: -.01em; }
.cover .sub { font-size: 11pt; color: var(--muted); font-weight: 600; margin-bottom: 26mm; }
.cover .meta { border-top: 1px solid var(--hairline); padding-top: 6mm; font-size: 8.4pt; color: var(--muted); }
.cover .meta strong { color: var(--ink); font-weight: 700; }

/* Headings --------------------------------------------------------------- */
h1, h2, h3, h4 { color: var(--ink); font-weight: 800; line-height: 1.25; }
h1 { font-size: 19pt; font-weight: 900; margin: 0 0 6mm 0; padding-bottom: 3mm; border-bottom: 3px solid var(--gold); letter-spacing: -.01em; }
h2 { font-size: 12.6pt; margin: 9mm 0 3.5mm 0; padding-left: 3.6mm; border-left: 3px solid var(--gold); page-break-after: avoid; }
h3 { font-size: 10.4pt; margin: 6.5mm 0 2.5mm 0; color: var(--gold-deep); page-break-after: avoid; }
h4 { font-size: 9.6pt; margin: 5mm 0 2mm 0; page-break-after: avoid; }

p { margin: 0 0 3.2mm 0; }
strong { color: var(--ink); font-weight: 700; }

/* Tables ----------------------------------------------------------------- */
table { width: 100%; border-collapse: collapse; margin: 4mm 0 6mm 0; font-size: 8.5pt; page-break-inside: avoid; }
thead th {
  background: var(--ink); color: #F3EFE7; text-align: left; font-weight: 700;
  font-size: 7.6pt; letter-spacing: .09em; text-transform: uppercase;
  padding: 2.4mm 2.8mm; border: none;
}
tbody td { padding: 2.3mm 2.8mm; border-bottom: 1px solid var(--hairline); vertical-align: top; }
tbody tr:nth-child(even) { background: #FBF9F5; }
td:first-child { font-weight: 600; color: var(--ink); }

/* Code ------------------------------------------------------------------- */
code {
  font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 8.2pt;
  background: var(--gold-pale); color: var(--gold-deep);
  padding: .4mm 1.2mm; border-radius: 2px;
}
pre {
  background: var(--ink); color: #E8E2D6; padding: 4mm 4.5mm;
  font-size: 7.9pt; line-height: 1.45; overflow-x: auto;
  margin: 3.5mm 0 5mm 0; page-break-inside: avoid;
  border-left: 3px solid var(--gold);
}
pre code { background: none; color: inherit; padding: 0; font-size: inherit; }

/* Lists ------------------------------------------------------------------ */
ul, ol { margin: 0 0 3.5mm 0; padding-left: 5.5mm; }
li { margin-bottom: 1.4mm; }
li > ul, li > ol { margin-top: 1.4mm; }

/* Blockquote = the "worth knowing" callout ------------------------------- */
blockquote {
  margin: 4mm 0; padding: 3.2mm 4.5mm;
  background: var(--gold-pale); border-left: 3px solid var(--gold);
  color: var(--body); page-break-inside: avoid;
}
blockquote p:last-child { margin-bottom: 0; }

hr { border: none; border-top: 1px solid var(--hairline); margin: 7mm 0; }

/* Numbers read as numbers */
td, th { font-variant-numeric: tabular-nums; }
"""

HEADER = """
<div style="font-family:Montserrat,Arial,sans-serif;font-size:6.6pt;color:#877E70;
     width:100%;padding:0 16mm;display:flex;justify-content:space-between;
     border-bottom:1px solid #E5DFD4;padding-bottom:2mm;">
  <span style="letter-spacing:.12em;text-transform:uppercase;">Dynamic Auctioneers Marketing Platform</span>
  <span>__DOCTITLE__</span>
</div>
"""

FOOTER = """
<div style="font-family:Montserrat,Arial,sans-serif;font-size:6.6pt;color:#877E70;
     width:100%;padding:0 16mm;display:flex;justify-content:space-between;">
  <span>Development documentation &middot; 20 August 2026 &middot; Cognexa</span>
  <span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>
</div>
"""


def cover(title: str, subtitle: str) -> str:
    return f"""
<div class="cover">
  <div class="rule"></div>
  <div class="eyebrow">Development Documentation</div>
  <h1>{title}</h1>
  <div class="sub">{subtitle}</div>
  <div class="meta">
    <strong>Dynamic Auctioneers Marketing Platform</strong><br>
    Built by Keegan Haumann, Cognexa<br>
    Compiled 20 August 2026 &middot; code state <strong>9c9e0df</strong> on main
  </div>
</div>
"""


# Cover subtitles per document.
SUBTITLES = {
    "00": "Index and reading order",
    "01": "What the system is, who uses it, and what it produces",
    "02": "Modules, data model, lifecycle and technology",
    "03": "The pipeline, step by step, documents in to posts out",
    "04": "Server, deployment, users and troubleshooting",
    "05": "Local setup, tests, conventions and how to extend it",
    "06": "What is done, what is outstanding, and the known risks",
}


def build(md_path: Path) -> Path:
    raw = md_path.read_text(encoding="utf-8")

    # The first "# " line becomes the cover title and is dropped from the body,
    # so the title is not printed twice.
    lines = raw.splitlines()
    title = md_path.stem
    for i, line in enumerate(lines):
        if line.startswith("# "):
            title = line[2:].strip()
            lines = lines[i + 1:]
            break
    body_md = "\n".join(lines)

    html_body = markdown.markdown(
        body_md,
        extensions=["tables", "fenced_code", "attr_list", "sane_lists"],
    )

    prefix = md_path.stem.split("-")[0]
    doc = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{CSS}</style></head><body>"
        f"{cover(title, SUBTITLES.get(prefix, ''))}"
        f"{html_body}"
        "</body></html>"
    )

    out_path = OUT / f"{md_path.stem}.pdf"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(doc, wait_until="networkidle")
        page.pdf(
            path=str(out_path),
            format="A4",
            print_background=True,
            display_header_footer=True,
            header_template=HEADER.replace("__DOCTITLE__", title),
            footer_template=FOOTER,
            margin={"top": "22mm", "bottom": "18mm", "left": "16mm", "right": "16mm"},
        )
        browser.close()
    return out_path


def main() -> int:
    OUT.mkdir(exist_ok=True)
    wanted = sys.argv[1] if len(sys.argv) > 1 else ""
    sources = sorted(p for p in HERE.glob("*.md") if wanted in p.stem)
    if not sources:
        print(f"No documents matched {wanted!r}")
        return 1
    for md_path in sources:
        out = build(md_path)
        print(f"  {md_path.name:38s} -> {out.relative_to(HERE.parent.parent)}"
              f"  ({out.stat().st_size // 1024} KB)")
    print(f"\n{len(sources)} PDF(s) written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
