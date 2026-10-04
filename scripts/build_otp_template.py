"""Derive the OTP Word templates from the team's master OTPs (M10, D114, D115, D123, D124).

Sources, both in ``3. PROPERTIES 2026/1.AAA MASTERS_PROPERTIES - E+G+A/KONTRAKTE+COMM
AGMENT.MASTER/``:

* ``MASTER OTP-insolvensies + likwidasies.docx`` -> ``otp.docx``, used for joint
  liquidators, joint trustees, executors and private sellers. 111 of the 120 most
  recent OTPs on the drive were filled in from it, and the deceased estate master
  is the same clauses with blanks, so only the heading above the seller changes.
* ``MASTER - OTP - BUSINESS RESCUE.docx`` -> ``otp-brp.docx`` (D124), used for
  business rescue practitioners. The team made it from the insolvency master on
  1 October 2026: no Master of the High Court consent clause, no Insolvency Act
  approval sentence, the practitioners in the definitions and the acceptance
  clauses, and "FROM the estate of" in the resolution.

What the script does to each master:

* **Per-property parts become ``{{tokens}}``**: the seller heading and name, the
  property block (legal description, street address, "In favor of", title deed,
  extent and, on the insolvency template only, the Master's reference), the
  deposit, guarantee days (both mentions), interest, commission and confirmation
  period. The business rescue master's property block was laid out by hand for one
  two-erf sale, so it is replaced by the insolvency template's block, which the
  generator repeats once per erf; a business rescue sale has no Master's reference,
  so that line is left out.
* **The special-condition slot** is a numbered clause after the last special
  condition, before RESTITUTION OF LAND RIGHTS (D123: the October master dropped the
  body corporate line the slot used to replace; a master that still has one gets
  the slot there).
* **Corrections (D115, Keegan: "correct them now")**: PROHIBITATION, STARUS,
  VARATION, the index's suretyship heading, FOURTY, CONVEYENCER, "cause of
  business", "surely for", "Gouws Avenue,Raslouw", "affected to", the Legal
  Practice Act citation written as "2014 (Act 28 of 2014)", and since D123 the
  October definitions' "the date the date" and the Restitution of Land Rights Act
  cited as 1998 (it is Act 22 of 1994). On the business rescue master also COMMISION
  and "BUSINESS PRACTITIONER/SELLER". The run-on commission clause ("5% (FIVE
  PERCENT) Upon confirmation") becomes the two clauses the team's own correct OTPs
  use (3048).
* **Letterhead**: the properties letterhead replaces the header picture, as on
  the proposals (D106).

The script refuses to write a template that still contains a master's leftover
client or any of the typos, and it fails loudly if a correction no longer matches,
so a change to a master is noticed rather than silently skipped.

    python3.12 scripts/build_otp_template.py "<path to MASTER OTP-insolvensies + likwidasies.docx>" \
        --brp "<path to MASTER - OTP - BUSINESS RESCUE.docx>" \
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

from build_proposal_templates import (
    _drop_unreferenced_images,
    _replace_all_text,
    _scrub_properties,
    apply_letterhead,
    replace_literal,
)

TEMPLATES = Path(__file__).resolve().parent.parent / "engine" / "otpgen" / "templates"
OUT = TEMPLATES / "otp.docx"
OUT_BRP = TEMPLATES / "otp-brp.docx"
_RUN_XPATH = "./w:r | ./w:hyperlink/w:r | ./w:ins/w:r | ./w:smartTag/w:r"

LEFTOVERS = ("JUST LETTING", "2008/011219/07", "VORNA", "BERGER", "ST71988", "T391/2024", "MONTAGU")
BRP_LEFTOVERS = ("MYSTICAL", "2004/094412/23", "KEMPTON", "LONG STREET", "T69455", "ERF 2664", "EKURHULENI")
TYPOS = ("PROHIBITATION", "STARUS", "VARATION", "FOURTY", "Act 53 of 1979", "surely for",
         "cause of business", "CONVEYENCER", "Gouws Avenue,Raslouw", "affected to", "Roukoop",
         "the date the date", "Rights Act 1998", "Act Number 28", "COMMISION", "BUSINESS PRACTITIONER")

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
    ("Section 86 (4) of the Legal Practice Act, Act 28 of 2014 (Act Number 28 of 2014)",
     "section 86(4) of the Legal Practice Act, 2014 (Act 28 of 2014)"),
    ("the date the date upon which", "the date upon which"),
    ("Restitution of Land Rights Act 1998", "Restitution of Land Rights Act 22 of 1994"),
)
BRP_CORRECTIONS = (
    ("COMMISION", "COMMISSION"),
    ("BUSINESS PRACTITIONER /SELLER", "BUSINESS RESCUE PRACTITIONER/SELLER"),
    ("BUSINESS PRACTITIONER/SELLER", "BUSINESS RESCUE PRACTITIONER/SELLER"),
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


def _blank(el) -> bool:
    return el.tag == qn("w:p") and not _nonempty(el) and not list(el.iter(qn("w:drawing")))


def _value_to_token(p, token: str) -> None:
    """``      HELD BY TITLE DEED: ST71988/2018`` -> ``      HELD BY TITLE DEED: {{token}}``."""
    label, _, value = _text(p).partition(":")
    if not value.strip():
        raise SystemExit(f"no value after the label in {label!r}")
    if replace_literal(p, value.strip(), token) != 1:
        raise SystemExit(f"could not isolate the value in {_text(p)!r}")


def _seller(b):
    """Tokenise the seller heading and line; return the index of "(Hereafter referred ...)"."""
    agreement = _find(b, lambda el: _text(el).strip() == "AGREEMENT AND CONDITIONS OF SALE", what="agreement title")
    instructions = _find(b, _starts("In which DYNAMIC AUCTIONEERS"), agreement)
    heading = _find(b, _nonempty, instructions + 1)
    _replace_all_text(b[heading], "{{seller_heading}}")
    seller = _find(b, _nonempty, heading + 1)
    _replace_all_text(b[seller], "{{seller_line}}")
    return _find(b, _starts("(Hereafter referred to as the SELLER)"), seller)


def _property_block(b, hereafter: int) -> None:
    """The insolvency master's property block, its values made tokens."""
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


def _swap_property_block(b, hereafter: int, donor_blocks) -> None:
    """Replace the business rescue master's property block with the insolvency one.

    The donor is the insolvency template's block between "(Hereafter referred ...)"
    and "Subject to the following conditions", already tokenised, without its
    MASTER REF line and the gap under it. Its paragraphs carry direct formatting
    only (no style or numbering ids), so they read the same in either file.
    """
    subject = _find(b, _starts("Subject to the following conditions"), hereafter)
    for el in b[hereafter + 1:subject]:
        el.getparent().remove(el)
    keep = []
    skip_gap = False
    for el in donor_blocks:
        if "{{masters_ref}}" in _text(el):
            skip_gap = True
            continue
        if skip_gap and _blank(el):
            skip_gap = False
            continue
        keep.append(copy.deepcopy(el))
    anchor = b[subject]
    for el in keep:
        anchor.addprevious(el)


def _terms(doc) -> None:
    body = doc.element.body
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


def _special_condition_slot(doc) -> None:
    """A numbered clause after the last special condition, with the gap the others have."""
    b = _blocks(doc)
    agreement = _find(b, lambda el: _text(el).strip() == "AGREEMENT AND CONDITIONS OF SALE", what="agreement title")
    special = _find(b, lambda el: _text(el).strip() == "SPECIAL CONDITIONS", agreement, what="special conditions")
    end = _find(b, lambda el: _text(el).strip().upper().startswith(("RESTITUTION OF LAND RIGHTS", "THUS, DONE AND SIGNED")),
                special + 1, what="end of the special conditions")
    old = next((i for i in range(special + 1, end) if _starts("Sale is subject to the rules and regulations of")(b[i])), None)
    if old is not None:  # a master that still carries a body corporate line
        _replace_all_text(b[old], "{{special_condition}}")
        return
    last = max((i for i in range(special + 1, end) if _nonempty(b[i])), default=None)
    if last is None:
        raise SystemExit("no special condition to put the slot after; has the master changed?")
    slot = copy.deepcopy(b[last])
    for r in slot.xpath(_RUN_XPATH)[1:]:
        r.getparent().remove(r)
    Run(slot.xpath(_RUN_XPATH)[0], None).text = "{{special_condition}}"
    after = b[last + 1] if _blank(b[last + 1]) else b[last]
    after.addnext(slot)
    if after is not b[last]:
        slot.addnext(copy.deepcopy(after))


def _correct(doc, corrections) -> None:
    paragraphs = list(doc.element.body.iter(qn("w:p")))
    for wrong, right in corrections:
        if not sum(replace_literal(p, wrong, right) for p in paragraphs):
            raise SystemExit(f"correction {wrong!r} did not match; has the master changed?")
        print(f"corrected: {wrong!r} -> {right!r}")


def _save(doc, dest: Path, letterhead: Path) -> None:
    _drop_unreferenced_images(doc)
    _scrub_properties(doc)
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest))
    print(f"{dest.name}: letterhead in {apply_letterhead(dest, letterhead)} header(s)")


def build(src: Path, dest: Path, letterhead: Path):
    """The insolvency template. Returns its tokenised property block for the business rescue one."""
    doc = Document(str(src))
    b = _blocks(doc)
    hereafter = _seller(b)
    _property_block(b, hereafter)
    b = _blocks(doc)
    subject = _find(b, _starts("Subject to the following conditions"), hereafter)
    donor = [copy.deepcopy(el) for el in b[hereafter + 1:subject]]
    _terms(doc)
    _special_condition_slot(doc)
    _correct(doc, CORRECTIONS)
    _save(doc, dest, letterhead)
    return donor


def build_brp(src: Path, dest: Path, letterhead: Path, donor_blocks) -> None:
    doc = Document(str(src))
    b = _blocks(doc)
    hereafter = _seller(b)
    _swap_property_block(b, hereafter, donor_blocks)
    _terms(doc)
    _special_condition_slot(doc)
    _correct(doc, CORRECTIONS + BRP_CORRECTIONS)
    _save(doc, dest, letterhead)


def _assert_clean(path: Path, leftovers) -> None:
    doc = Document(str(path))
    text = "\n".join(_text(p) for p in doc.element.body.iter(qn("w:p")))
    for needle in (*leftovers, *TYPOS):
        if needle in text:
            raise SystemExit(f"{path.name} still contains {needle!r}")
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            data = z.read(name)
            for needle in leftovers:
                if needle.encode("utf8") in data:
                    raise SystemExit(f"{path.name}:{name} still contains {needle!r}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("master", type=Path, help="MASTER OTP-insolvensies + likwidasies.docx")
    ap.add_argument("--brp", type=Path, required=True, help="MASTER - OTP - BUSINESS RESCUE.docx")
    ap.add_argument("--letterhead", type=Path, required=True, help="Letter Head 2026.png (properties letterhead)")
    args = ap.parse_args(argv)
    donor = build(args.master, OUT, args.letterhead)
    _assert_clean(OUT, LEFTOVERS)
    build_brp(args.brp, OUT_BRP, args.letterhead, donor)
    _assert_clean(OUT_BRP, LEFTOVERS + BRP_LEFTOVERS)
    print(f"{OUT.name}, {OUT_BRP.name}: no leftover client and no typos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
