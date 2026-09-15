"""Derive the OTP Word template from the team's master OTP (M10, D114, D115).

Source: ``3. PROPERTIES 2026/1.AAA MASTERS_PROPERTIES - E+G+A/KONTRAKTE+COMM
AGMENT.MASTER/MASTER OTP-insolvensies + likwidasies.docx``, the master that 111
of the 120 most recent OTPs on the drive were filled in from. The deceased
estate master is the same clauses with blanks, so one template serves every
kind of seller: only the heading above the seller's name changes.

What the script does to the master:

* **Per-property parts become ``{{tokens}}``**: the seller heading and name, the
  property block (legal description, street address, "In favor of", title deed,
  extent, Master's reference), the deposit, guarantee days (both mentions),
  interest, commission and confirmation period, and the body corporate line.
  The master's leftover client, Vorna Village property and "MONTAGU body
  corporate" go with them.
* **Corrections (D115, Keegan: "correct them now")**: PROHIBITATION, STARUS,
  VARATION, the index's suretyship heading, FOURTY, CONVEYENCER, "cause of
  business", "surely for", "Gouws Avenue,Raslouw", "affected to", and the Legal
  Practice Act cited as Act 53 of 1979 (it is the Legal Practice Act, 2014, Act 28
  of 2014). The run-on commission clause ("5% (SIX PERCENT) Upon confirmation")
  becomes the two clauses the team's own correct OTPs use (3048).
* **Letterhead**: the properties letterhead replaces the header picture, as on
  the proposals (D106).

The script refuses to write a template that still contains the leftover client
or any of the typos, and it fails loudly if a correction no longer matches, so a
change to the master is noticed rather than silently skipped.

    python3.12 scripts/build_otp_template.py "<path to MASTER OTP-insolvensies + likwidasies.docx>" \
        --letterhead "<path to KONTRAKTE+COMM AGMENT.MASTER/Letter Head 2026.png>"
"""

from __future__ import annotations

import argparse
import copy
import sys
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.text.run import Run

from build_proposal_templates import _drop_unreferenced_images, _replace_all_text, _scrub_properties, apply_letterhead

OUT = Path(__file__).resolve().parent.parent / "engine" / "otpgen" / "templates" / "otp.docx"
_RUN_XPATH = "./w:r | ./w:hyperlink/w:r | ./w:ins/w:r | ./w:smartTag/w:r"

LEFTOVERS = ("JUST LETTING", "2008/011219/07", "VORNA", "BERGER", "ST71988", "T391/2024", "MONTAGU")
TYPOS = ("PROHIBITATION", "STARUS", "VARATION", "FOURTY", "Act 53 of 1979", "surely for",
         "cause of business", "CONVEYENCER", "Gouws Avenue,Raslouw", "affected to")

# (paragraph starts with, literal in that paragraph, token text). Every occurrence
# in the paragraph is replaced, which catches both guarantee mentions.
TERMS = (
    ("A cash deposit of", "10% (Ten Percent)", "{{deposit_pct}}% ({{deposit_words}})"),
    ("The balance of the purchase price shall be paid upon registration", "45 (FOURTY FIVE)",
     "{{guarantee_days}} ({{guarantee_words}})"),
    ("The PURCHASER will pay interest on the balance", "12% (TWELVE PERCENT)",
     "{{interest_pct}}% ({{interest_words}} PERCENT)"),
    ("This agreement is subject to the Acceptance", "30 (THIRTY) days",
     "{{confirmation_days}} ({{confirmation_words}}) days"),
)

# (wrong, right) wherever they occur. Each must match at least once.
CORRECTIONS = (
    ("PROHIBITATION", "PROHIBITION"),
    ("MARITAL STARUS OF PURCHASER", "MARITAL STATUS OF PURCHASER"),
    ("VARATION", "VARIATION"),
    ("PERSONAL SURETYSHIP AND SEVERAL LIABILITY", "PERSONAL SURETYSHIP JOINT AND SEVERAL LIABILITY"),
    ("normal cause of business", "normal course of business"),
    ("as surely for", "as surety for"),
    ("CONVEYENCER", "CONVEYANCER"),
    ("Gouws Avenue,Raslouw", "Gouws Avenue, Raslouw"),
    ("will be affected to the CONVEYANCER", "will be effected to the CONVEYANCER"),
    ("Section 86 (4) of the Legal Practice Act, Act 53 of 1979 (Act Number 53 of 1979)",
     "section 86(4) of the Legal Practice Act, 2014 (Act 28 of 2014)"),
)

COMMISSION_DUE = (
    "The commission calculated at {{commission_pct}}% ({{commission_words}} PERCENT) of the purchase "
    "price {{commission_vat}}, will be due and payable by the {{commission_payer}} to the AUCTIONEER."
)
COMMISSION_EARNED = (
    "Upon confirmation by the SELLER of this agreement, the commission shall be earned by, and payable to, "
    "the AUCTIONEER. The said commission shall be appropriated from the deposit paid by the purchaser and "
    "shall be paid to the AUCTIONEER upon confirmation of the sale by the seller."
)


def _text(el) -> str:
    return "".join(t.text or "" for t in el.iter(qn("w:t")))


def _blocks(doc):
    return [el for el in doc.element.body.iterchildren() if el.tag != qn("w:sectPr")]


def _find(blocks, predicate, start=0, what="anchor"):
    for i in range(start, len(blocks)):
        if predicate(blocks[i]):
            return i
    raise SystemExit(f"{what} not found after block {start}; has the master changed?")


def _starts(prefix):
    return lambda el: _text(el).strip().upper().startswith(prefix.upper())


def _nonempty(el) -> bool:
    return bool(_text(el).strip())


def replace_literal(p, old: str, new: str) -> int:
    """Replace every ``old`` in paragraph ``p``, even where Word split it across runs."""
    runs = [Run(r, None) for r in p.xpath(_RUN_XPATH)]
    texts = [r.text for r in runs]
    full = "".join(texts)
    spans = []
    i = full.find(old)
    while i != -1:
        spans.append((i, i + len(old)))
        i = full.find(old, i + len(old))
    if not spans:
        return 0
    lengths = [len(t) for t in texts]
    starts, pos = [], 0
    for n in lengths:
        starts.append(pos)
        pos += n

    def locate(offset: int) -> int:
        return next(k for k, (s, n) in enumerate(zip(starts, lengths)) if n and s <= offset < s + n)

    for s, e in reversed(spans):
        a, b = locate(s), locate(e - 1)
        if a == b:
            texts[a] = texts[a][: s - starts[a]] + new + texts[a][e - starts[a]:]
        else:
            texts[a] = texts[a][: s - starts[a]] + new
            for k in range(a + 1, b):
                texts[k] = ""
            texts[b] = texts[b][e - starts[b]:]
    for run, text in zip(runs, texts):
        if run.text != text:
            run.text = text
    return len(spans)


def _value_to_token(p, token: str) -> None:
    """``      HELD BY TITLE DEED: ST71988/2018`` -> ``      HELD BY TITLE DEED: {{token}}``."""
    label, _, value = _text(p).partition(":")
    if not value.strip():
        raise SystemExit(f"no value after the label in {label!r}")
    if replace_literal(p, value.strip(), token) != 1:
        raise SystemExit(f"could not isolate the value in {_text(p)!r}")


def build(src: Path, dest: Path, letterhead: Path) -> None:
    doc = Document(str(src))
    body = doc.element.body
    b = _blocks(doc)

    # Seller and property -----------------------------------------------------
    agreement = _find(b, lambda el: _text(el).strip() == "AGREEMENT AND CONDITIONS OF SALE", what="agreement title")
    instructions = _find(b, _starts("In which DYNAMIC AUCTIONEERS"), agreement)
    heading = _find(b, _nonempty, instructions + 1)
    _replace_all_text(b[heading], "{{seller_heading}}")
    seller = _find(b, _nonempty, heading + 1)
    _replace_all_text(b[seller], "{{seller_line}}")
    hereafter = _find(b, _starts("(Hereafter referred to as the SELLER)"), seller)
    legal = _find(b, _nonempty, hereafter + 1)
    _replace_all_text(b[legal], "{{erf_legal}}")
    known_label = _find(b, _starts("BETTER KNOWN AS"), legal)
    known = _find(b, _nonempty, known_label + 1)
    _replace_all_text(b[known], "{{erf_known_as}}")
    favor = _find(b, _starts("In favor of"), known)
    _replace_all_text(b[favor], "In favor of: {{seller_line}}")
    deed = _find(b, lambda el: "HELD BY TITLE DEED" in _text(el), favor)
    _value_to_token(b[deed], "{{erf_title_deed}}")
    measuring = _find(b, lambda el: "MEASURING" in _text(el), deed)
    _value_to_token(b[measuring], "{{erf_extent}}")
    master_ref = _find(b, lambda el: "MASTER REF" in _text(el), measuring)
    _value_to_token(b[master_ref], "{{masters_ref}}")
    _find(b, _starts("Subject to the following conditions"), master_ref)

    # Terms ------------------------------------------------------------------------
    paragraphs = list(body.iter(qn("w:p")))
    for anchor, old, token in TERMS:
        hits = sum(replace_literal(p, old, token) for p in paragraphs if _text(p).strip().startswith(anchor))
        if not hits:
            raise SystemExit(f"term {old!r} not found in the paragraph starting {anchor!r}")

    b = _blocks(doc)
    commission = _find(b, _starts("The commission calculated at"), what="commission clause")
    due = b[commission]
    _replace_all_text(due, COMMISSION_DUE)
    earned = copy.deepcopy(due)
    for r in earned.xpath(_RUN_XPATH)[1:]:
        r.getparent().remove(r)
    Run(earned.xpath(_RUN_XPATH)[0], None).text = COMMISSION_EARNED
    spacer = b[commission + 1]
    due.addnext(earned)
    if spacer.tag == qn("w:p") and not _nonempty(spacer):
        due.addnext(copy.deepcopy(spacer))

    body_corporate = _find(b, _starts("Sale is subject to the rules and regulations of"), what="body corporate line")
    _replace_all_text(b[body_corporate], "{{special_condition}}")

    # Corrections --------------------------------------------------------------------
    paragraphs = list(body.iter(qn("w:p")))
    for wrong, right in CORRECTIONS:
        if not sum(replace_literal(p, wrong, right) for p in paragraphs):
            raise SystemExit(f"correction {wrong!r} did not match; has the master changed?")
        print(f"corrected: {wrong!r} -> {right!r}")

    _drop_unreferenced_images(doc)
    _scrub_properties(doc)
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest))
    print(f"letterhead: {apply_letterhead(dest, letterhead)} header(s)")


def _assert_clean(path: Path) -> None:
    doc = Document(str(path))
    text = "\n".join(_text(p) for p in doc.element.body.iter(qn("w:p")))
    for needle in (*LEFTOVERS, *TYPOS):
        if needle in text:
            raise SystemExit(f"{path.name} still contains {needle!r}")
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            data = z.read(name)
            for needle in LEFTOVERS:
                if needle.encode("utf8") in data:
                    raise SystemExit(f"{path.name}:{name} still contains {needle!r}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("master", type=Path, help="MASTER OTP-insolvensies + likwidasies.docx")
    ap.add_argument("--letterhead", type=Path, required=True, help="Letter Head 2026.png (properties letterhead)")
    args = ap.parse_args(argv)
    build(args.master, OUT, args.letterhead)
    _assert_clean(OUT)
    print(f"{OUT.name}: no leftover client and no typos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
