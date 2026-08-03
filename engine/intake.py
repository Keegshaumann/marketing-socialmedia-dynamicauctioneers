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
        # Reading the pages must be guarded too, not just the open: PyMuPDF opens
        # a mislabelled or truncated image happily and only raises when a page is
        # read (FzErrorFormat / FzErrorLibrary). A marketer dragging the property
        # photos in with the PDFs would otherwise 500 the intake screen.
        return " ".join(page.get_text() for page in doc).lower()
    except Exception:  # pragma: no cover - corrupt/mislabelled file
        return ""
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
    """One property's source-document state, keyed by DP number.

    A property may span several land portions, each with its own Lightstone EVM
    (multi-file intake), so every slot is a LIST. The singular ``lightstone_evm``
    / ``property_report`` / ``valuation_report`` properties return the first of
    each for the common single-portion case and for back-compatible callers.
    """

    dp: str
    parent_dp: Optional[str] = None
    lot: Optional[int] = None
    lightstone_evms: List[Path] = field(default_factory=list)
    property_reports: List[Path] = field(default_factory=list)
    # Optional third source (a registered valuer's report). Not required for
    # completeness - marketing is not always given it (D35).
    valuation_reports: List[Path] = field(default_factory=list)
    unknown: List[Path] = field(default_factory=list)

    # --- back-compat singular accessors (first of each list, or None) ------
    @property
    def lightstone_evm(self) -> Optional[Path]:
        return self.lightstone_evms[0] if self.lightstone_evms else None

    @property
    def property_report(self) -> Optional[Path]:
        return self.property_reports[0] if self.property_reports else None

    @property
    def valuation_report(self) -> Optional[Path]:
        return self.valuation_reports[0] if self.valuation_reports else None

    @property
    def all_sources(self) -> List[Path]:
        """Every classified source file, EVMs then reports then valuations."""
        return [*self.lightstone_evms, *self.property_reports, *self.valuation_reports]

    @property
    def is_complete(self) -> bool:
        """True when at least one EVM and one Property Report are present.

        The valuation report is optional and never gates completeness. A
        multi-portion property needs only one Property Report to proceed even if
        it carries several EVMs.
        """
        return bool(self.lightstone_evms) and bool(self.property_reports)

    @property
    def missing(self) -> List[str]:
        """The required document kinds still absent, in a stable order."""
        gaps: List[str] = []
        if not self.lightstone_evms:
            gaps.append("lightstone_evm")
        if not self.property_reports:
            gaps.append("property_report")
        return gaps


def _slot(job: "IntakeJob", path: Path) -> None:
    """Classify ``path`` and append it to the matching list slot on ``job``."""
    kind = classify_pdf(path)
    if kind == "lightstone_evm":
        job.lightstone_evms.append(path)
    elif kind == "property_report":
        job.property_reports.append(path)
    elif kind == "valuation_report":
        job.valuation_reports.append(path)
    else:
        job.unknown.append(path)


def build_jobs(paths: List[PathLike]) -> List[IntakeJob]:
    """Group PDFs by DP number, classify each, and build an ``IntakeJob`` per DP.

    Files whose name carries no DP number are skipped. Each classified file is
    appended to its list slot, so several EVMs under one DP all land (a
    multi-portion property); an ``unknown`` file is parked rather than dropped.
    Jobs are returned in DP order.
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
        _slot(job, path)

    return [jobs[dp] for dp in sorted(jobs)]


def dp_candidates(paths: List[PathLike]) -> List[str]:
    """Every distinct DP number the filenames carry, sorted.

    Empty when no name carries one (farm portions like "PTN 6 of Farm 7.pdf");
    more than one when the drop mixes properties. The caller needs the two cases
    kept apart: "no DP" is answered by asking the user to type one, but
    "several DPs" must be refused outright, because typing one DP would file the
    other property's documents under it and extraction would synthesise the two
    into a single chimera record.
    """
    dps: set[str] = set()
    for raw in paths:
        try:
            dp, _parent, _lot = parse_dp(Path(raw).name)
        except ValueError:
            continue
        dps.add(dp)
    return sorted(dps)


def find_dp(paths: List[PathLike]) -> Optional[str]:
    """The single DP number shared by every named file, or ``None`` if unclear.

    Returns a DP only when all parseable filenames agree on exactly one value.
    Multiple distinct DPs, or names with no DP at all (a farm portion like
    "PTN 6 of Farm 7.pdf"), return ``None`` so the caller prompts the user to
    type one rather than guessing. Erring toward a prompt is deliberate: a wrong
    key names the wrong folder and URL for the whole property. Use
    ``dp_candidates`` when the two "unclear" cases must be told apart.
    """
    found = dp_candidates(paths)
    return found[0] if len(found) == 1 else None


def build_combined_job(paths: List[PathLike], dp: Optional[str] = None) -> IntakeJob:
    """Build ONE ``IntakeJob`` from every file, for the multi-file intake screen.

    Unlike ``build_jobs`` (which groups by DP), this treats the whole drop as a
    single property that may span several portions and returns one job holding
    all of its EVMs, reports and valuations. ``dp`` is taken as given, else read
    from the filenames via ``find_dp``; when neither yields one the job's ``dp``
    is empty and the caller must ask the user for it.
    """
    dp = dp or find_dp(paths) or ""
    parent_dp: Optional[str] = None
    lot: Optional[int] = None
    if "." in dp:
        base, _, lot_str = dp.partition(".")
        parent_dp = base
        lot = int(lot_str) if lot_str.isdigit() else None
    job = IntakeJob(dp=dp, parent_dp=parent_dp, lot=lot)
    for raw in paths:
        _slot(job, Path(raw))
    return job


def build_jobs_from_dir(directory: PathLike) -> List[IntakeJob]:
    """Build intake jobs from every PDF found under ``directory`` (recursive)."""
    root = Path(directory)
    pdfs = sorted(root.rglob("*.pdf"))
    return build_jobs(list(pdfs))
