"""Intake (M1): pair the source PDFs and classify each by content.

A Dynamic Auctioneers property is described by two documents (SPEC 4.1):
the Lightstone EVM report (deeds, market and valuation truth) and the
Dynamic Property Report (physical inspection truth). This module pairs them
by DP number and decides which is which by reading the page text, not by
trusting the filename. Filenames are a weak, human-supplied hint used only to
break a genuine tie.

Design rules baked in here:
- Classification scores content keywords with PyMuPDF; the filename is a
  tiebreaker, never the sole signal (SPEC M1 criterion).
- A lone Lightstone document does not proceed. ``build_jobs`` returns an
  ``IntakeJob`` whose ``is_complete`` is False and whose ``missing`` flags the
  absent property report, so the caller waits and raises a flag rather than
  extracting half a record.
- The DP identifier is stored WITHOUT the "DP" prefix to match
  ``record.json`` (``dp: "3060"``). A sub-property "3035.1" carries its parent
  "3035" and lot number 1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Union

try:  # PyMuPDF is a hard dependency, but keep the import failure legible.
    import fitz  # type: ignore
except ImportError:  # pragma: no cover - environment guard
    fitz = None  # type: ignore

PathLike = Union[str, Path]

# Content markers, lower-cased, mapped to a weight. Distinctive brand and
# product terms weigh more than generic ones so a stray word cannot flip the
# classification on its own.
LIGHTSTONE_MARKERS = {
    "lightstone": 3,
    "evm": 3,
    "estimated value": 2,
    "avm": 2,
    "report id": 2,
    "comparable sales": 2,
    "suburb estimated": 2,
    "mother erf": 2,
    "comparable": 1,
    "deeds": 1,
    "scheme": 1,
    "sectional title": 1,
    "last sales": 1,
}

PROPERTY_REPORT_MARKERS = {
    "property report": 3,
    "dynamic auctioneers": 3,
    "dynamic solutions": 3,
    "terms of sale": 2,
    "offers invited": 2,
    "prepared by": 2,
    "viewing": 1,
    "inspection": 1,
    "auction": 1,
    "id number": 1,
}

# A registered valuer's report. The high-weight terms are ones the Lightstone
# EVM never carries (a "forced sale value", a registered valuer, the SACPVP
# regulator), so an EVM's stray "market value"/"valuation" cannot flip to here.
VALUATION_MARKERS = {
    "forced sale value": 3,
    "registered valuer": 3,
    "sacpvp": 3,
    "professional valuer": 2,
    "valuation report": 2,
    "open market value": 2,
    "date of valuation": 2,
    "market value": 1,
    "valuer": 1,
}

# A DP token is a leading run of digits, optionally prefixed by "DP" and
# optionally suffixed by ".<lot>". We anchor at the start of the name so an
# address number later in the string (for example "40 Topham Road") can never
# be mistaken for the DP number.
_DP_RE = re.compile(r"^\s*(?:DP)?(\d+)(?:\.(\d+))?", re.IGNORECASE)


def parse_dp(name: str) -> Tuple[str, Optional[str], Optional[int]]:
    """Extract the DP identifier from a filename or folder name.

    Returns ``(dp, parent_dp, lot)``. A plain property gives
    ``("3060", None, None)``; a sub-property gives ``("3035.1", "3035", 1)``.
    Raises ``ValueError`` when the name does not begin with a DP number.
    """
    stem = Path(str(name)).name
    match = _DP_RE.match(stem)
    if not match:
        raise ValueError(f"no DP number found in {name!r}")
    base, lot_str = match.group(1), match.group(2)
    if lot_str is None:
        return base, None, None
    dp = f"{base}.{lot_str}"
    return dp, base, int(lot_str)


def _read_text(path: Path) -> str:
    """Return the full lower-cased text of a PDF, or empty string on failure."""
    if fitz is None:
        return ""
    try:
        doc = fitz.open(path)
    except Exception:  # pragma: no cover - unreadable file
        return ""
    try:
        return " ".join(page.get_text() for page in doc).lower()
    finally:
        doc.close()


def _score(text: str, markers: dict) -> int:
    return sum(weight for marker, weight in markers.items() if marker in text)


def classify_pdf(path: PathLike) -> str:
    """Classify a PDF by its content.

    Returns ``"lightstone_evm"``, ``"property_report"``, ``"valuation_report"``
    or ``"unknown"``. Page text is scored against the marker tables; the
    filename is consulted only to break an exact tie, never as the sole signal.
    """
    path = Path(path)
    text = _read_text(path)
    scores = {
        "lightstone_evm": _score(text, LIGHTSTONE_MARKERS),
        "property_report": _score(text, PROPERTY_REPORT_MARKERS),
        "valuation_report": _score(text, VALUATION_MARKERS),
    }
    best = max(scores, key=lambda k: scores[k])
    # A clear winner (a positive score not tied with another kind) settles it.
    if scores[best] > 0 and list(scores.values()).count(scores[best]) == 1:
        return best

    # Tie or all-zero (commonly when the text layer is unreadable): fall back to
    # the filename hint only. EVM/Lightstone first so an EVM whose name also says
    # "valuation" is not misread as a valuer's report.
    hint = path.name.lower()
    if "evm" in hint or "lightstone" in hint:
        return "lightstone_evm"
    if "property report" in hint or "property_report" in hint:
        return "property_report"
    if "valuation" in hint or "valuer" in hint:
        return "valuation_report"
    return "unknown"


@dataclass
class IntakeJob:
    """One property's paired-document state, keyed by DP number."""

    dp: str
    parent_dp: Optional[str] = None
    lot: Optional[int] = None
    lightstone_evm: Optional[Path] = None
    property_report: Optional[Path] = None
    # Optional third source (a registered valuer's report). Not required for
    # completeness - marketing is not always given it (D35).
    valuation_report: Optional[Path] = None
    unknown: List[Path] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        """True when both required documents are present.

        The valuation report is optional and never gates completeness.
        """
        return self.lightstone_evm is not None and self.property_report is not None

    @property
    def missing(self) -> List[str]:
        """The required documents that are still absent, in a stable order."""
        gaps: List[str] = []
        if self.lightstone_evm is None:
            gaps.append("lightstone_evm")
        if self.property_report is None:
            gaps.append("property_report")
        return gaps


def build_jobs(paths: List[PathLike]) -> List[IntakeJob]:
    """Group PDFs by DP number, classify each, and build an ``IntakeJob`` per DP.

    Files whose name carries no DP number are skipped. A file that classifies
    as ``unknown``, or that would overwrite an already-filled slot, is parked
    in the job's ``unknown`` list rather than silently dropped. Jobs are
    returned in DP order.
    """
    jobs: dict[str, IntakeJob] = {}
    for raw in paths:
        path = Path(raw)
        try:
            dp, parent_dp, lot = parse_dp(path.name)
        except ValueError:
            continue

        job = jobs.get(dp)
        if job is None:
            job = IntakeJob(dp=dp, parent_dp=parent_dp, lot=lot)
            jobs[dp] = job

        kind = classify_pdf(path)
        if kind == "lightstone_evm" and job.lightstone_evm is None:
            job.lightstone_evm = path
        elif kind == "property_report" and job.property_report is None:
            job.property_report = path
        elif kind == "valuation_report" and job.valuation_report is None:
            job.valuation_report = path
        else:
            job.unknown.append(path)

    return [jobs[dp] for dp in sorted(jobs)]


def build_jobs_from_dir(directory: PathLike) -> List[IntakeJob]:
    """Build intake jobs from every PDF found under ``directory`` (recursive)."""
    root = Path(directory)
    pdfs = sorted(root.rglob("*.pdf"))
    return build_jobs(list(pdfs))
