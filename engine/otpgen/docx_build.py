"""Fill the OTP Word template (M10, D114).

``templates/otp.docx`` is the team's master OTP with the per-property parts as
``{{tokens}}`` and its errors corrected (``scripts/build_otp_template.py``). This
fills it: one property block per erf joined by "AND" (the Rules of Auction's
own layout for several erven), the MEASURING and MASTER REF lines left out when
there is nothing to print, and the special conditions printed where the master
had its body corporate line, or that line removed when there are none. The
Word-filling helpers are the proposal's (M9), so both documents fill the same way.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import List

from docx import Document
from docx.oxml.ns import qn
from docx.text.run import Run

from engine.otpgen.model import Otp, conditions, tokens
from engine.proposal.docx_build import (
    _RUN_XPATH,
    TemplateError,
    _blocks,
    _fill_all,
    _leftover_tokens,
    _plain,
)

TEMPLATE = Path(__file__).resolve().parent / "templates" / "otp.docx"


def _is_blank(el) -> bool:
    return el is not None and el.tag == qn("w:p") and not _plain(el).strip() and not list(el.iter(qn("w:drawing")))


def _remove_with_spacer(p) -> None:
    spacer = p.getnext()
    p.getparent().remove(p)
    if _is_blank(spacer):
        spacer.getparent().remove(spacer)


def _fill_erven(doc, otp: Otp) -> None:
    blocks = _blocks(doc)
    try:
        start = next(i for i, el in enumerate(blocks) if "{{erf_legal}}" in _plain(el))
        end = next(i for i in range(start, len(blocks)) if _plain(blocks[i]).strip().startswith("Subject to the following conditions"))
    except StopIteration:
        raise TemplateError("the OTP template has no property block") from None
    group = blocks[start:end]
    anchor = blocks[end]
    label = next((el for el in group if _plain(el).strip().upper().startswith("BETTER KNOWN AS")), group[0])
    for el in group:
        el.getparent().remove(el)

    for n, erf in enumerate(otp.erven):
        if n:
            joiner = copy.deepcopy(label)
            for r in joiner.xpath(_RUN_XPATH)[1:]:
                r.getparent().remove(r)
            Run(joiner.xpath(_RUN_XPATH)[0], None).text = "AND"
            anchor.addprevious(joiner)
            anchor.addprevious(copy.deepcopy(group[-1]) if _is_blank(group[-1]) else joiner.makeelement(qn("w:p"), {}))
        values = {
            **tokens(otp),
            "erf_legal": erf.legal_description.strip().upper(),
            "erf_known_as": erf.known_as.strip().upper(),
            "erf_title_deed": erf.title_deed.strip().upper(),
            "erf_extent": erf.extent.strip(),
        }
        skipped = False
        for el in group:
            text = _plain(el)
            if skipped and _is_blank(el):  # the gap under a line left out goes with it
                skipped = False
                continue
            skipped = ("{{erf_extent}}" in text and not values["erf_extent"]) or (
                "{{masters_ref}}" in text and not values["masters_ref"]
            )
            if skipped:
                continue
            clone = copy.deepcopy(el)
            _fill_all(clone, values)
            anchor.addprevious(clone)


def _fill_conditions(doc, lines: List[str]) -> None:
    slot = next((p for p in doc.element.body.iter(qn("w:p")) if "{{special_condition}}" in _plain(p)), None)
    if slot is None:
        raise TemplateError("the OTP template has no special condition slot")
    if not lines:
        _remove_with_spacer(slot)
        return
    spacer = slot.getnext() if _is_blank(slot.getnext()) else None
    _fill_all(slot, {"special_condition": lines[0]})
    current = spacer if spacer is not None else slot
    for line in lines[1:]:
        extra = copy.deepcopy(slot)
        for r in extra.xpath(_RUN_XPATH)[1:]:
            r.getparent().remove(r)
        Run(extra.xpath(_RUN_XPATH)[0], None).text = line
        current.addnext(extra)
        current = extra
        if spacer is not None:
            gap = copy.deepcopy(spacer)
            current.addnext(gap)
            current = gap


def build(otp: Otp, out_path: Path) -> Path:
    doc = Document(str(TEMPLATE))
    _fill_erven(doc, otp)
    _fill_conditions(doc, conditions(otp))
    _fill_all(doc.element.body, tokens(otp))

    leftover = _leftover_tokens(doc)
    if leftover:
        raise TemplateError("unfilled tokens: " + ", ".join(leftover))

    core = doc.core_properties
    core.title = f"OTP {otp.dp}"
    core.author = "Dynamic Auctioneers"
    core.last_modified_by = "Dynamic Auctioneers"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path
