"""Derive the auction proposal Word templates from a real proposal (M9, D103).

The team's own proposals are the reference: Century Gothic, letterhead in the
header and footer, the same static marketing-example screenshots in every one.
Rebuilding that in HTML would drift from what the liquidators are used to, so the
template IS one of their proposals with the per-property content cut out and
``{{tokens}}`` put in its place. ``engine.proposal.docx_build`` fills it.

Two files come out:

* ``proposal-front.docx`` - cover, deeds page, advert page, marketing examples,
  budget table and deadline. Ends where the contract used to start.
* ``proposal-back.docx`` - the introduction / functionality plan / conduct /
  commission pages that followed the contract, with the three sale terms they
  quote turned into tokens so they are filled from the property's own OTP.

The contract in between is never templated: the builder inserts that property's
own OTP word for word (D103).

Run once against a proposal whose layout is current, then commit the output:

    python3.12 scripts/build_proposal_templates.py "<path to 2821 - Auction proposal.docx>"

The script refuses to write a template that still carries any of the source
property's identifying strings (pass them with --forbid), and it drops every
image the cut-out content referenced, so the owner-details screenshot of the
source property does not ride along inside the package.
"""

from __future__ import annotations

import argparse
import copy
import sys
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

OUT_DIR = Path(__file__).resolve().parent.parent / "engine" / "proposal" / "templates"

_RUN_XPATH = "./w:r | ./w:hyperlink/w:r | ./w:ins/w:r | ./w:smartTag/w:r"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


# --- small XML helpers ------------------------------------------------------

def _text(el) -> str:
    return "".join(t.text or "" for t in el.iter(qn("w:t")))


def _has_image(el) -> bool:
    return any(True for _ in el.iter(qn("w:drawing"))) or any(
        True for _ in el.iter("{urn:schemas-microsoft-com:vml}imagedata")
    )


def _blocks(doc):
    """Top-level body blocks (paragraphs, tables, content controls), in order."""
    return [el for el in doc.element.body.iterchildren() if el.tag != qn("w:sectPr")]


def _find(blocks, predicate, start=0):
    for i in range(start, len(blocks)):
        if predicate(blocks[i]):
            return i
    raise SystemExit(f"anchor not found after block {start}")


def _starts(prefix):
    return lambda el: _text(el).strip().upper().startswith(prefix.upper())


def _new_run_like(run, text: str):
    """A run with ``run``'s formatting and ``text`` as its only content."""
    new = copy.deepcopy(run)
    for child in list(new):
        if child.tag != qn("w:rPr"):
            new.remove(child)
    t = etree.SubElement(new, qn("w:t"))
    t.text = text
    t.set(_XML_SPACE, "preserve")
    return new


def _value_after_last_tab(p, token: str) -> None:
    """Keep the label and its tabs, replace everything after the last tab.

    Cover lines read ``Label:<tab><tab>VALUE``; the value is often split across
    many runs (Word's spell-check and edit history) and sometimes wrapped in a
    hyperlink. The first non-blank value run donates its formatting.
    """
    kids = list(p)
    tab_idx = [
        i for i, k in enumerate(kids)
        if k.tag == qn("w:r") and k.find(qn("w:tab")) is not None
    ]
    if not tab_idx:
        raise SystemExit(f"no tab in cover line {_text(p)!r}")
    value = kids[tab_idx[-1] + 1:]
    donor = None
    for k in value:
        runs = [k] if k.tag == qn("w:r") else list(k.iter(qn("w:r")))
        for r in runs:
            if _text(r).strip():
                donor = r
                break
        if donor is not None:
            break
    if donor is None:
        raise SystemExit(f"no value run in cover line {_text(p)!r}")
    new = _new_run_like(donor, token)
    for k in value:
        if k.tag != qn("w:pPr"):
            p.remove(k)
    p.append(new)


def _replace_all_text(p, text: str) -> None:
    """Replace a paragraph's text with ``text``, keeping the first text run's look."""
    runs = p.xpath(_RUN_XPATH)
    donor = next((r for r in runs if _text(r).strip()), None)
    if donor is None:
        raise SystemExit(f"no text run in {_text(p)!r}")
    new = _new_run_like(donor, text)
    for k in list(p):
        if k.tag != qn("w:pPr"):
            p.remove(k)
    p.append(new)


def _swap_run_text(p, old: str, new: str) -> None:
    """Replace one run whose text is exactly ``old`` (whitespace-trimmed)."""
    for r in p.xpath(_RUN_XPATH):
        if _text(r).strip() == old:
            for t in list(r.iter(qn("w:t")))[1:]:
                t.getparent().remove(t)
            t = r.find(qn("w:t"))
            keep_trailing = _text(r).endswith(" ")
            t.text = new + (" " if keep_trailing else "")
            t.set(_XML_SPACE, "preserve")
            return
    raise SystemExit(f"run {old!r} not found in {_text(p)!r}")


def _set_page_break_before(p) -> None:
    ppr = p.find(qn("w:pPr"))
    if ppr is None:
        ppr = etree.SubElement(p, qn("w:pPr"))
        p.remove(ppr)
        p.insert(0, ppr)
    if ppr.find(qn("w:pageBreakBefore")) is None:
        el = etree.SubElement(ppr, qn("w:pageBreakBefore"))
        # pPr children have a schema order; pStyle/keepNext come first, so put
        # pageBreakBefore right after any of those rather than at the end.
        ppr.remove(el)
        anchor = 0
        for i, child in enumerate(ppr):
            if child.tag in (qn("w:pStyle"), qn("w:keepNext"), qn("w:keepLines")):
                anchor = i + 1
        ppr.insert(anchor, el)


def _strip_explicit_page_breaks(p) -> None:
    for br in list(p.iter(qn("w:br"))):
        if br.get(qn("w:type")) == "page":
            br.getparent().remove(br)
    for br in list(p.iter(qn("w:lastRenderedPageBreak"))):
        br.getparent().remove(br)


def _drop_unreferenced_images(doc) -> int:
    """Remove relationships to images the body/headers no longer reference."""
    xml = etree.tostring(doc.element).decode("utf8")
    dropped = 0
    for rid, rel in list(doc.part.rels.items()):
        if rel.reltype.endswith("/image") and f'"{rid}"' not in xml:
            del doc.part.rels[rid]
            dropped += 1
    return dropped


def _scrub_properties(doc) -> None:
    cp = doc.core_properties
    for field in ("author", "last_modified_by", "title", "subject", "keywords",
                  "comments", "category", "identifier", "content_status"):
        setattr(cp, field, "")


# --- front ----------------------------------------------------------------

def build_front(src: Path, dest: Path) -> None:
    doc = Document(str(src))
    body = doc.element.body
    b = _blocks(doc)

    deeds_h = _find(b, _starts("Subject Property Deeds Enquiry"))
    advert_h = _find(b, _starts("Draft Proposed Advert"), deeds_h)
    budget_h = _find(b, _starts("Proposed Budget and Schedule"), advert_h)
    index_h = _find(b, _starts("INDEX TO DEED OF SALE"), budget_h)

    # Cover -----------------------------------------------------------------
    seller = _find(b, lambda el: "estate" in _text(el).lower() or "matter of" in _text(el).lower())
    if seller >= deeds_h:
        raise SystemExit("seller line not found on the cover")
    p = b[seller]
    label_runs = [r for r in p.xpath(_RUN_XPATH) if r.find(qn("w:tab")) is None]
    first_label = next(r for r in label_runs if _text(r).strip())
    tab_run = next(r for r in p.xpath(_RUN_XPATH) if r.find(qn("w:tab")) is not None)
    for r in p.xpath(_RUN_XPATH):
        if r is tab_run:
            break
        if r is not first_label:
            r.getparent().remove(r)
    _swap_run_text(p, _text(first_label).strip(), "{{seller_label}}:")
    _value_after_last_tab(p, "{{seller_line}}")

    subject = _find(b, _starts("Subject Property:"), seller)
    _value_after_last_tab(b[subject], "{{erf_legal}}")
    known_label = _find(b, lambda el: "better known as" in _text(el).lower(), subject)
    known_value = _find(b, lambda el: _text(el).strip() != "", known_label + 1)
    p = b[known_value]
    runs = p.xpath(_RUN_XPATH)
    lead = runs[0] if not _text(runs[0]).strip() else None
    donor = next(r for r in runs if _text(r).strip())
    for k in list(p):
        if k.tag != qn("w:pPr") and k is not lead:
            p.remove(k)
    p.append(_new_run_like(donor, "{{erf_known_as}}"))
    deed = _find(b, _starts("Title"), known_value)
    _value_after_last_tab(b[deed], "{{erf_title_deed}}")

    date = _find(b, _starts("Auction Date:"), deed)
    _value_after_last_tab(b[date], "{{auction_date_line}}")
    venue = _find(b, _starts("Auction Venue:"), date)
    _value_after_last_tab(b[venue], "{{auction_venue_line}}")
    admin = _find(b, _starts("Administrator:"), venue)
    _value_after_last_tab(b[admin], "{{administrator}}")

    # Page headings carry their own break, so removing filler lines cannot let
    # a heading ride up onto the page before it.
    for i in (deeds_h, advert_h, budget_h):
        _strip_explicit_page_breaks(b[i])
        _set_page_break_before(b[i])

    # The blank lines that padded the cover down the page go: the deeds heading
    # now breaks on its own, and a second erf on the cover needs the room.
    for el in b[admin + 1:deeds_h]:
        if el.tag == qn("w:p") and not _text(el).strip() and not _has_image(el):
            body.remove(el)

    # Deeds page: first image paragraph becomes the slot, the rest go -------
    image_paras = [i for i in range(deeds_h + 1, advert_h) if _has_image(b[i])]
    if not image_paras:
        raise SystemExit("no deeds image found")
    slot = b[image_paras[0]]
    for k in list(slot):
        if k.tag != qn("w:pPr"):
            slot.remove(k)
    slot.append(_new_run_like(next(iter(b[deeds_h].xpath(_RUN_XPATH))), "{{image:deeds}}"))
    for i in range(deeds_h + 1, advert_h):
        if b[i] is not slot and b[i].tag == qn("w:p") and (not _text(b[i]).strip()):
            body.remove(b[i])

    # Advert page ------------------------------------------------------------
    b = _blocks(doc)
    advert_h = _find(b, _starts("Draft Proposed Advert"))
    examples_h = _find(b, _starts("Digital Marketing Examples"), advert_h)
    advert_imgs = [i for i in range(advert_h + 1, examples_h) if _has_image(b[i])]
    if not advert_imgs:
        raise SystemExit("no advert image found")
    slot = b[advert_imgs[0]]
    donor_run = next(iter(b[advert_h].xpath(_RUN_XPATH)))
    for k in list(slot):
        if k.tag != qn("w:pPr"):
            slot.remove(k)
    slot.append(_new_run_like(donor_run, "{{image:advert}}"))
    for i in range(advert_h + 1, examples_h):
        if b[i] is not slot and b[i].tag == qn("w:p") and (_has_image(b[i]) or not _text(b[i]).strip()):
            body.remove(b[i])

    # Budget -------------------------------------------------------------------
    b = _blocks(doc)
    budget_h = _find(b, _starts("Proposed Budget and Schedule"))
    starting_label = _find(b, _starts("AUCTION STARTING DATE"), budget_h)
    starting_value = _find(b, lambda el: _text(el).strip() != "", starting_label + 1)
    _replace_all_text(b[starting_value], "{{auction_starting_line}}")
    table_i = _find(b, lambda el: el.tag == qn("w:tbl"), starting_value)
    tbl = b[table_i]
    rows = tbl.findall(qn("w:tr"))
    header, template_row, totals = rows[0], rows[1], rows[-3:]
    for row in rows[2:-3]:
        tbl.remove(row)
    for cell, token in zip(template_row.findall(qn("w:tc")),
                           ("{{line_media}}", "{{line_description}}", "{{line_placement}}", "{{line_cost}}")):
        paras = cell.findall(qn("w:p"))
        for extra in paras[1:]:
            cell.remove(extra)
        first = paras[0]
        runs = first.xpath(_RUN_XPATH)
        if runs:
            donor = next((r for r in runs if _text(r).strip()), runs[0])
            new = _new_run_like(donor, token)
            for k in list(first):
                if k.tag != qn("w:pPr"):
                    first.remove(k)
            first.append(new)
        else:
            first.append(_new_run_like(etree.Element(qn("w:r")), token))
    for row, token in zip(totals, ("{{total}}", "{{vat}}", "{{total_incl}}")):
        last_cell = row.findall(qn("w:tc"))[-1]
        _replace_all_text(last_cell.find(qn("w:p")), token)
    deadline = _find(b, _starts("DEADLINE"), table_i)
    _replace_all_text(b[deadline], "DEADLINE ({{deadline}})")

    # Everything from the contract onwards is cut ----------------------------
    b = _blocks(doc)
    index_h = _find(b, _starts("INDEX TO DEED OF SALE"))
    for el in b[index_h:]:
        body.remove(el)
    # Trailing blank paragraphs would push an empty page ahead of the contract.
    b = _blocks(doc)
    while b and b[-1].tag == qn("w:p") and not _text(b[-1]).strip() and not _has_image(b[-1]):
        body.remove(b[-1])
        b = _blocks(doc)

    dropped = _drop_unreferenced_images(doc)
    _scrub_properties(doc)
    doc.save(str(dest))
    print(f"front: {dest.name} ({dropped} unreferenced images dropped)")


# --- back -----------------------------------------------------------------

def build_back(src: Path, dest: Path) -> None:
    doc = Document(str(src))
    body = doc.element.body
    b = _blocks(doc)
    intro = _find(b, lambda el: _text(el).strip().upper() == "INTRODUCTION")
    for el in b[:intro]:
        body.remove(el)
    b = _blocks(doc)
    _strip_explicit_page_breaks(b[0])
    _set_page_break_before(b[0])

    deposit = _find(b, lambda el: _text(el).strip().startswith("A deposit equal to"))
    _swap_run_text(b[deposit], "10", "{{deposit_pct}}")
    confirm = _find(b, lambda el: "for confirmation within a" in _text(el))
    _swap_run_text(b[confirm], "30", "{{confirmation_days}}")
    public = _find(b, lambda el: "conducted publicly" in _text(el))
    _replace_all_text(
        b[public],
        "The auction will be public and will be conducted {{conduct_channel}}, as to ensure transparency.",
    )
    heading = _find(b, lambda el: _text(el).strip().upper() in ("AUCTIONEERS COMMISION", "AUCTIONEERS COMMISSION"))
    _replace_all_text(b[heading], "AUCTIONEER'S COMMISSION")
    commission = _find(b, lambda el: "commission will be earned" in _text(el))
    _swap_run_text(b[commission], "SELLER", "{{commission_payer}}")
    _swap_run_text(b[commission], "7,5", "{{commission_pct}}")
    _swap_run_text(b[commission], "SEVEN AND A HALF", "{{commission_words}}")
    _swap_run_text(b[commission], "PLUS VAT", "{{commission_vat}}")
    typo = _find(b, lambda el: _text(el).strip() == "CWNTURION")
    _replace_all_text(b[typo], "CENTURION")

    dropped = _drop_unreferenced_images(doc)
    _scrub_properties(doc)
    doc.save(str(dest))
    print(f"back: {dest.name} ({dropped} unreferenced images dropped)")


# --- guard ----------------------------------------------------------------

def _assert_clean(path: Path, forbidden) -> None:
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            data = z.read(name)
            for needle in forbidden:
                if needle.encode("utf8") in data:
                    raise SystemExit(f"{path.name}:{name} still contains {needle!r}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", type=Path, help="a real proposal .docx in the current layout")
    ap.add_argument("--forbid", action="append", default=[],
                    help="a string from the source property that must not survive (repeatable)")
    args = ap.parse_args(argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    front = OUT_DIR / "proposal-front.docx"
    back = OUT_DIR / "proposal-back.docx"
    build_front(args.source, front)
    build_back(args.source, back)
    for path in (front, back):
        _assert_clean(path, args.forbid)
    print("no forbidden strings in either template")
    return 0


if __name__ == "__main__":
    sys.exit(main())
