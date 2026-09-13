"""Fill the proposal Word template and join the property's own OTP (M9, D103).

The document is three parts, in the order the team's proposals have them:

1. ``templates/proposal-front.docx`` filled in: cover (one block per erf), the
   deeds page(s), the advert, the static marketing examples, the budget table
   and the approval deadline;
2. the property's OTP ``.docx``, inserted **verbatim** with docxcompose (its
   styles and clause numbering come across; not a word of it is touched);
3. ``templates/proposal-back.docx`` filled in: the introduction pages, whose
   deposit, confirmation period and commission come from the same OTP.

Tokens are ``{{name}}`` in the template text. Word splits text into runs on a
whim (spell-check, edit history), so a token may straddle several runs; the
filler joins a paragraph's runs, replaces, and writes each run's share back, so
the formatting of the run a token starts in is kept. A token with no value
raises rather than printing ``{{name}}`` into a client document.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Dict, Iterable, List

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Inches
from docx.text.paragraph import Paragraph
from docx.text.run import Run
from docxcompose.composer import Composer
from lxml import etree
from PIL import Image

from engine.proposal.model import Proposal, budget_rows, tokens

TEMPLATES = Path(__file__).resolve().parent / "templates"
FRONT = TEMPLATES / "proposal-front.docx"
BACK = TEMPLATES / "proposal-back.docx"

_TOKEN = re.compile(r"\{\{([a-z_:]+)\}\}")
_RUN_XPATH = "./w:r | ./w:hyperlink/w:r | ./w:ins/w:r | ./w:smartTag/w:r"

# Picture boxes, measured against the team's pages: A4 with 0.5 inch side
# margins and the tall letterhead header, so a picture wider than 6.9 inches or
# taller than 7.2 inches spills onto a second page.
_MAX_W_IN = 6.9
_MAX_H_IN = 7.2


class TemplateError(RuntimeError):
    """The template and the data disagree (a token with no value, a missing slot)."""


# --- text ---------------------------------------------------------------------

def _plain(el) -> str:
    return "".join(t.text or "" for t in el.iter(qn("w:t")))


def _fill_paragraph(p, values: Dict[str, str]) -> None:
    runs = [Run(r, None) for r in p.xpath(_RUN_XPATH)]
    if not runs:
        return
    texts = [r.text for r in runs]
    lengths = [len(t) for t in texts]
    full = "".join(texts)
    matches = list(_TOKEN.finditer(full))
    if not matches:
        return
    starts = []
    pos = 0
    for n in lengths:
        starts.append(pos)
        pos += n

    def locate(offset: int) -> int:
        for i, (s, n) in enumerate(zip(starts, lengths)):
            if n and s <= offset < s + n:
                return i
        raise TemplateError(f"token offset {offset} outside the paragraph")

    for m in reversed(matches):
        name = m.group(1)
        if name not in values:
            raise TemplateError(f"no value for {{{{{name}}}}}")
        s, e = m.span()
        i, j = locate(s), locate(e - 1)
        if i == j:
            t = texts[i]
            texts[i] = t[: s - starts[i]] + values[name] + t[e - starts[i]:]
        else:
            texts[i] = texts[i][: s - starts[i]] + values[name]
            for k in range(i + 1, j):
                texts[k] = ""
            texts[j] = texts[j][e - starts[j]:]
    for run, text in zip(runs, texts):
        if run.text != text:
            run.text = text  # "\n" becomes a line break, "\t" a tab


def _fill_all(root, values: Dict[str, str]) -> None:
    for p in list(root.iter(qn("w:p"))):
        _fill_paragraph(p, values)


def _leftover_tokens(doc) -> List[str]:
    found = set()
    for p in doc.element.body.iter(qn("w:p")):
        found.update(m.group(0) for m in _TOKEN.finditer(_plain(p)))
    return sorted(found)


def set_page_break_before(p) -> None:
    ppr = p.find(qn("w:pPr"))
    if ppr is None:
        ppr = etree.Element(qn("w:pPr"))
        p.insert(0, ppr)
    if ppr.find(qn("w:pageBreakBefore")) is not None:
        return
    el = etree.Element(qn("w:pageBreakBefore"))
    anchor = 0
    for i, child in enumerate(ppr):
        if child.tag in (qn("w:pStyle"), qn("w:keepNext"), qn("w:keepLines")):
            anchor = i + 1
    ppr.insert(anchor, el)


def _blocks(doc) -> list:
    return [el for el in doc.element.body.iterchildren() if el.tag != qn("w:sectPr")]


# --- cover: one block per erf ---------------------------------------------------

def _fill_erven(doc, proposal: Proposal) -> None:
    blocks = _blocks(doc)
    try:
        start = next(i for i, el in enumerate(blocks) if "{{erf_legal}}" in _plain(el))
        end = next(i for i, el in enumerate(blocks) if "{{auction_date_line}}" in _plain(el))
    except StopIteration:
        raise TemplateError("the cover has no erf block") from None
    group = blocks[start:end]
    anchor = blocks[end]
    known_label = next((el for el in group if "better known as" in _plain(el).lower()), group[0])

    for el in group:
        el.getparent().remove(el)

    for n, erf in enumerate(proposal.erven):
        if n:
            joiner = copy.deepcopy(known_label)
            runs = joiner.xpath(_RUN_XPATH)
            donor = next((r for r in runs if _plain(r).strip()), runs[0])
            for k in list(joiner):
                if k.tag != qn("w:pPr"):
                    joiner.remove(k)
            joiner.append(copy.deepcopy(donor))
            Run(joiner.xpath(_RUN_XPATH)[0], None).text = "\tAND"
            anchor.addprevious(joiner)
        values = {
            "erf_legal": erf.legal_description.strip().upper(),
            "erf_known_as": erf.known_as.strip().upper(),
            "erf_title_deed": erf.title_deed.strip().upper(),
        }
        for el in group:
            clone = copy.deepcopy(el)
            _fill_all(clone, values)
            anchor.addprevious(clone)


# --- pictures -----------------------------------------------------------------------

def _fit(path: Path) -> tuple:
    with Image.open(path) as im:
        w_px, h_px = im.size
    width = _MAX_W_IN
    height = width * h_px / w_px
    if height > _MAX_H_IN:
        height = _MAX_H_IN
        width = height * w_px / h_px
    return Inches(width), Inches(height)


def _place_pictures(doc, slot: str, paths: Iterable[Path]) -> None:
    token = "{{image:%s}}" % slot
    target = next(
        (p for p in doc.element.body.iter(qn("w:p")) if _plain(p).strip() == token), None
    )
    if target is None:
        raise TemplateError(f"the template has no {token} slot")
    paths = list(paths)
    if not paths:
        raise TemplateError(f"no picture supplied for {token}")
    for r in target.xpath(_RUN_XPATH):
        r.getparent().remove(r)
    blank = copy.deepcopy(target)
    current = target
    for n, path in enumerate(paths):
        if n:
            nxt = copy.deepcopy(blank)
            set_page_break_before(nxt)
            current.addnext(nxt)
            current = nxt
        width, height = _fit(path)
        Paragraph(current, doc._body).add_run().add_picture(str(path), width=width, height=height)


# --- budget ---------------------------------------------------------------------------

def _fill_budget(doc, rows: List[Dict[str, str]]) -> None:
    template_row = next(
        (tr for tr in doc.element.body.iter(qn("w:tr")) if "{{line_media}}" in _plain(tr)), None
    )
    if template_row is None:
        raise TemplateError("the budget table has no template row")
    for row in rows:
        clone = copy.deepcopy(template_row)
        # The team's row has a fixed height; a media name set on two lines
        # would print over the row rule, so let the row grow instead.
        for height in clone.iter(qn("w:trHeight")):
            if height.get(qn("w:hRule")) == "exact":
                height.set(qn("w:hRule"), "atLeast")
        _fill_all(clone, {
            "line_media": row["media"],
            "line_description": row["description"],
            "line_placement": row["placement"],
            "line_cost": row["cost"],
        })
        template_row.addprevious(clone)
    template_row.getparent().remove(template_row)


# --- the whole document -------------------------------------------------------------------

def build(proposal: Proposal, files_root: Path, out_path: Path) -> Path:
    """Write the proposal ``.docx`` to ``out_path`` and return it.

    ``files_root`` is the folder the proposal's stored file paths are relative
    to (``<output_root>/DP<dp>/proposal``). Callers run ``checks.run`` first; this
    function assumes the pictures and the OTP exist and raises if they do not.
    """
    values = tokens(proposal)

    front = Document(str(FRONT))
    _fill_erven(front, proposal)
    _place_pictures(front, "deeds", [files_root / p for p in proposal.deeds_images])
    _place_pictures(front, "advert", [files_root / proposal.advert_image])
    _fill_budget(front, budget_rows(proposal))
    _fill_all(front.element.body, values)

    back = Document(str(BACK))
    _fill_all(back.element.body, values)

    otp = Document(str(files_root / proposal.otp_file))

    composer = Composer(front)
    before = len(_blocks(composer.doc))
    composer.append(otp)
    blocks = _blocks(composer.doc)
    if len(blocks) > before and blocks[before].tag == qn("w:p"):
        # The contract starts on a fresh page. This is layout on the proposal,
        # not an edit to the OTP's wording.
        set_page_break_before(blocks[before])
    composer.append(back)

    leftover = _leftover_tokens(composer.doc)
    if leftover:
        raise TemplateError("unfilled tokens: " + ", ".join(leftover))

    core = composer.doc.core_properties
    core.title = f"Auction proposal {proposal.dp}"
    core.author = "Dynamic Auctioneers"
    core.last_modified_by = "Dynamic Auctioneers"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    composer.save(str(out_path))
    return out_path
