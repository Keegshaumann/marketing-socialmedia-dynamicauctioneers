"""Derive the property report Word template from the team's master (M11, D116, D117).

Source: ``3. PROPERTIES 2026/1.AAA MASTERS_PROPERTIES - E+G+A/PROPERTY REPORT
TEMPLATE/3060- PROPERTY REPORT.docx``. That "template" is the live DP3060 report
copied, so everything about that property and its owner is cut out and replaced
by ``{{tokens}}`` or by slots the builder (``engine.propertyreport.docx_build``)
fills:

* the cover: legal description, address and the owner line; the Lightstone
  picture and owner strip become one ``{{image:cover}}`` slot; the Prepared By,
  Cell No and E-Mail values become tokens, and an Inspection Date line is added
  (as on 3076);
* the eight values of the PROPERTY DETAIL table;
* the description heading ("Residential Dwelling:") and its summary line;
* the IMPROVEMENTS, EXTERNAL FEATURES and TERMS AND CONDITIONS bullet lists, each
  cut to one slot line (``{{improvement}}``, ``{{feature}}``, ``{{term}}``) that
  the builder repeats;
* SECURITY, a new OCCUPATION AND CONDITION section (from the viewing checklist),
  RATES AND TAXES and OUTSTANDING LEVIES, filled or removed by the builder;
* the viewing heading and contact, the conclusion and the signature;
* the four photo paragraphs, which become one ``{{image:photos}}`` slot.

Corrected on the way: "PROPERTY DECRIPTION", the "L egal Description" label that
Word split across two type sizes, and "Arear Rates & Levies" (the terms lines are
generated now). The blank lines that left PROPERTY DETAIL alone at the foot of
page 1 go; the heading starts page 2 with its table. The conclusion, "Kind
Regards" and the name are kept with the next paragraph, so the signature never
sits alone on a page.

The properties letterhead replaces the header picture (D106). The script refuses
to write a template that still carries anything of DP3060 or its owner.

    python3.12 scripts/build_report_template.py "<path to 3060- PROPERTY REPORT.docx>" \\
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
from lxml import etree

from build_proposal_templates import (
    _RUN_XPATH,
    _XML_SPACE,
    _drop_unreferenced_images,
    _new_run_like,
    _replace_all_text,
    _scrub_properties,
    _set_page_break_before,
    apply_letterhead,
)

OUT = Path(__file__).resolve().parent.parent / "engine" / "propertyreport" / "templates" / "property-report.docx"

# Everything identifying DP3060, its owner and its viewing contact. Checked in
# upper case against the body text and byte for byte inside the package.
LEFTOVERS = ("KADER", "TARHERA", "TAHERA", "650302", "0674105758", "KYALAMI", "KAYALAMI", "TOPHAM",
             "ST35834", "MSUNDUZI", "MUSINDUZI", "51/1978", "PORTION 77", "VENTER", "PIETMARITZBURG",
             "29.62401", "30.383523", "960 000")
TYPOS = ("DECRIPTION", "AREAR")

TABLE = (
    ("Legal Description:", "{{legal_description}}"),
    ("Physical Address:", "{{known_as}}"),
    ("Property Extent:", "{{extent}}"),
    ("Zoning/Usage:", "{{zoning}}"),
    ("Local Authority:", "{{local_authority}}"),
    ("Municipal Valuation:", "{{municipal_valuation}}"),
    ("Title Deed No:", "{{title_deed}}"),
    ("GPS:", "{{gps}}"),
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


def _has_drawing(el) -> bool:
    return any(True for _ in el.iter(qn("w:drawing")))


def _nonempty(el) -> bool:
    return bool(_text(el).strip())


def _blank(el) -> bool:
    return el.tag == qn("w:p") and not _nonempty(el) and not _has_drawing(el)


def _is_list(el) -> bool:
    ppr = el.find(qn("w:pPr"))
    return el.tag == qn("w:p") and ppr is not None and ppr.find(qn("w:numPr")) is not None


def _remove(el) -> None:
    el.getparent().remove(el)


def _keep_with_next(p) -> None:
    ppr = p.find(qn("w:pPr"))
    if ppr is None:
        ppr = etree.Element(qn("w:pPr"))
        p.insert(0, ppr)
    if ppr.find(qn("w:keepNext")) is None:
        keep = etree.Element(qn("w:keepNext"))
        style = ppr.find(qn("w:pStyle"))  # pPr children have a schema order: pStyle, then keepNext
        if style is not None:
            style.addnext(keep)
        else:
            ppr.insert(0, keep)


def _drop_runs(p) -> None:
    for k in list(p):
        if k.tag != qn("w:pPr"):
            p.remove(k)


def _value_after_tabs(p, token: str) -> None:
    """``Label:<tab><tab>VALUE`` -> ``Label:<tab><tab>{{token}}``.

    The master puts the start of a value in the same run as the last tab
    ("<tab>GERRIE"), so text after that tab is cut from its run as well. The label
    run lends its look to the token.
    """
    runs = p.xpath(_RUN_XPATH)
    tabbed = [r for r in runs if r.find(qn("w:tab")) is not None]
    if not tabbed:
        raise SystemExit(f"no tab in {_text(p)!r}")
    last = tabbed[-1]
    kids = list(last)
    last_tab = max(i for i, child in enumerate(kids) if child.tag == qn("w:tab"))
    for child in kids[last_tab + 1:]:
        if child.tag == qn("w:t"):
            last.remove(child)
    donor = next(r for r in runs if _text(r).strip())
    after = False
    for k in list(p):
        if k is last:
            after = True
        elif after and k.tag != qn("w:pPr"):
            p.remove(k)
    p.append(_new_run_like(donor, token))


def _list_slot(blocks, heading: int, token: str) -> int:
    """Cut the bullet list under ``heading`` to one slot line. Returns the index after the list."""
    first = _find(blocks, _nonempty, heading + 1)
    end = first
    while end < len(blocks) and _is_list(blocks[end]):
        end += 1
    if end == first:
        raise SystemExit(f"no bullet list under {_text(blocks[heading])!r}")
    _replace_all_text(blocks[first], token)
    for el in blocks[first + 1:end]:
        _remove(el)
    return end


def _one_gap(blocks, start: int, stop: int) -> None:
    """Keep at most one blank paragraph between two blocks."""
    blanks = [el for el in blocks[start:stop] if _blank(el)]
    for el in blanks[1:]:
        _remove(el)


def build(src: Path, dest: Path, letterhead: Path) -> None:
    doc = Document(str(src))
    b = _blocks(doc)

    # Cover -------------------------------------------------------------------------
    title = _find(b, lambda el: _text(el).strip() == "PROPERTY REPORT", what="title")
    legal = _find(b, _nonempty, title + 1)
    _replace_all_text(b[legal], "{{legal_description}}")
    known_label = _find(b, _starts("Better known as"), legal)
    known = _find(b, _nonempty, known_label + 1)
    _replace_all_text(b[known], "{{known_as}}")
    respect = _find(b, _starts("IN RESPECT OF"), known)
    owner = _find(b, _nonempty, respect + 1)
    for drawing in list(b[owner].iter(qn("w:drawing"))):
        run = drawing.getparent()
        _remove(run)
    _replace_all_text(b[owner], "{{owner_line}}")
    strip = _find(b, _has_drawing, owner + 1, "Lightstone owner strip")
    donor = b[owner].xpath(_RUN_XPATH)[0]
    _drop_runs(b[strip])
    b[strip].append(_new_run_like(donor, "{{image:cover}}"))

    prepared = _find(b, _starts("Prepared By"), strip)
    cell = _find(b, _starts("Cell No"), prepared)
    email = _find(b, _starts("E-Mail"), cell)
    _value_after_tabs(b[prepared], "{{prepared_by}}")
    _value_after_tabs(b[cell], "{{prepared_cell}}")
    _value_after_tabs(b[email], "{{prepared_email}}")
    inspection = copy.deepcopy(b[prepared])
    label_run, token_run = inspection.xpath(_RUN_XPATH)[0], inspection.xpath(_RUN_XPATH)[-1]
    label_run.find(qn("w:t")).text = "Inspection Date"
    token_run.find(qn("w:t")).text = "{{inspection_date}}"
    b[email].addnext(inspection)

    detail = _find(b, _starts("PROPERTY DETAIL"), email)
    for el in b[email + 1:detail]:
        if _blank(el):
            _remove(el)
    _set_page_break_before(b[detail])

    # Property detail table ------------------------------------------------------------
    table = _find(b, lambda el: el.tag == qn("w:tbl"), detail, "PROPERTY DETAIL table")
    rows = b[table].findall(qn("w:tr"))
    if len(rows) != len(TABLE):
        raise SystemExit(f"the PROPERTY DETAIL table has {len(rows)} rows, not {len(TABLE)}")
    for row, (label, token) in zip(rows, TABLE):
        cells = row.findall(qn("w:tc"))
        found = _text(cells[0]).replace(" ", "").rstrip(":").lower()
        if found != label.replace(" ", "").rstrip(":").lower():
            raise SystemExit(f"table row {_text(cells[0])!r} is not {label!r}")
        _replace_all_text(next(p for p in cells[0].iter(qn("w:p")) if _nonempty(p)), label)
        _replace_all_text(next(p for p in cells[1].iter(qn("w:p")) if _nonempty(p)), token)

    # Description and lists ----------------------------------------------------------------
    desc = _find(b, _starts("PROPERTY DECRIPTION"), table, "PROPERTY DECRIPTION heading")
    _replace_all_text(b[desc], "PROPERTY DESCRIPTION")
    kind = _find(b, _nonempty, desc + 1)
    _replace_all_text(b[kind], "{{property_heading}}")
    summary = _find(b, _nonempty, kind + 1)
    _replace_all_text(b[summary], "{{description}}")

    improvements = _find(b, _starts("IMPROVEMENTS"), summary)
    after = _list_slot(b, improvements, "{{improvement}}")
    features = _find(b, _starts("EXTERNAL FEATURES"), after)
    _one_gap(b, after, features)
    _replace_all_text(b[features], "EXTERNAL FEATURES:")
    after = _list_slot(b, features, "{{feature}}")

    security = _find(b, lambda el: _text(el).strip() == "SECURITY", after, "SECURITY heading")
    security_text = _find(b, _nonempty, security + 1)
    _replace_all_text(b[security_text], "{{security}}")
    condition_heading = copy.deepcopy(b[security])
    _replace_all_text(condition_heading, "OCCUPATION AND CONDITION")
    condition = copy.deepcopy(b[security_text])
    _replace_all_text(condition, "{{condition}}")
    b[security_text].addnext(condition_heading)
    condition_heading.addnext(condition)

    rates = _find(b, _starts("RATES AND TAXES"), security_text)
    rates_line = _find(b, _nonempty, rates + 1)
    _replace_all_text(b[rates_line], "{{rates_line}}")
    levies = _find(b, _starts("OUTSTANDING LEVIES"), rates_line)
    levies_line = _find(b, _nonempty, levies + 1)
    _replace_all_text(b[levies_line], "{{levies_line}}")

    terms = _find(b, _starts("TERMS AND CONDITIONS"), levies_line)
    after = _list_slot(b, terms, "{{term}}")

    # Viewing, conclusion, signature ----------------------------------------------------------
    viewing = _find(b, _starts("VIEWING BY APPOINTMENT"), after)
    runs = b[viewing].findall(qn("w:r"))
    heading_run = next(r for r in runs if _text(r).strip().upper().startswith("VIEWING"))
    heading_run.find(qn("w:t")).text = "{{viewing_heading}}"
    contact = runs[runs.index(heading_run) + 1:]
    if not contact:
        raise SystemExit("the viewing paragraph has no contact after its heading")
    for t in contact[0].findall(qn("w:t")):
        contact[0].remove(t)
    t = etree.SubElement(contact[0], qn("w:t"))
    t.text = "{{viewing_contact}}"
    t.set(_XML_SPACE, "preserve")
    for r in contact[1:]:
        _remove(r)

    conclusion = _find(b, _starts("CONCLUSION"), viewing)
    conclusion_text = _find(b, _nonempty, conclusion + 1)
    _replace_all_text(b[conclusion_text], "{{conclusion}}")
    regards = _find(b, _starts("Kind Regards"), conclusion_text)
    signed = _find(b, _nonempty, regards + 1)
    _replace_all_text(b[signed], "{{signed_by}}")
    company = _find(b, _starts("Dynamic Auctioneers"), signed)
    # The conclusion and signature move as one block: a long report otherwise
    # leaves the name alone at the top of a page.
    for el in (b[conclusion], b[conclusion_text], b[regards], b[signed]):
        _keep_with_next(el)

    # Photos ------------------------------------------------------------------------------------
    photos = [i for i in range(company + 1, len(b)) if _has_drawing(b[i])]
    if not photos:
        raise SystemExit("no photo paragraphs after the signature")
    for el in b[company + 1:photos[0]]:
        if _blank(el):
            _remove(el)
    slot = b[photos[0]]
    _drop_runs(slot)
    slot.append(_new_run_like(b[company].xpath(_RUN_XPATH)[0], "{{image:photos}}"))
    _set_page_break_before(slot)
    for i in photos[1:]:
        _remove(b[i])
    for el in b[photos[-1] + 1:]:
        if el.getparent() is not None and _blank(el):
            _remove(el)

    for mark in list(doc.element.body.iter(qn("w:lastRenderedPageBreak"))):
        _remove(mark)
    _drop_unreferenced_images(doc)
    _scrub_properties(doc)
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest))
    print(f"letterhead: {apply_letterhead(dest, letterhead)} header(s)")


def _assert_clean(path: Path) -> None:
    doc = Document(str(path))
    text = "\n".join(_text(p) for p in doc.element.body.iter(qn("w:p"))).upper()
    for needle in (*LEFTOVERS, *TYPOS):
        if needle in text:
            raise SystemExit(f"{path.name} still contains {needle!r}")
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            data = z.read(name).upper()
            for needle in LEFTOVERS:
                if needle.encode("utf8") in data:
                    raise SystemExit(f"{path.name}:{name} still contains {needle!r}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("master", type=Path, help="3060- PROPERTY REPORT.docx")
    ap.add_argument("--letterhead", type=Path, required=True, help="Letter Head 2026.png (properties letterhead)")
    args = ap.parse_args(argv)
    build(args.master, OUT, args.letterhead)
    _assert_clean(OUT)
    print(f"{OUT.name}: nothing of DP3060 or its owner is left, and the typos are gone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
