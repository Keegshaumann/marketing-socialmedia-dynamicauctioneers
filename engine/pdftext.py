"""Turn a PDF into text that keeps its ROWS, using PyMuPDF only.

``pdftotext -layout`` does this well, and both document readers were written
against it - but the production server has no poppler, so on the box every OTP
and every levy statement would have read as "could not be read" and the features
would have been quietly dead. PyMuPDF is already a hard dependency of this
project, so the rows are rebuilt from word positions instead: one code path, the
same result everywhere, and nothing to install.

Plain ``page.get_text()`` is not enough - it puts each table cell on its own
line, which separates a charge from its description and is exactly what these
readers need joined.
"""

from __future__ import annotations

from pathlib import Path

try:
    import fitz  # type: ignore
except ImportError:  # pragma: no cover - environment guard
    fitz = None  # type: ignore


class PdfUnreadable(RuntimeError):
    """The document could not be turned into text (an image scan, or no PyMuPDF)."""


def layout_text(path: "str | Path", row_tolerance: float = 3.0, min_chars: int = 80) -> str:
    """The document as text, with words on the same visual line joined.

    ``row_tolerance`` is how far apart (in points) two words may sit vertically
    and still count as the same row; 3pt holds a table row together without
    merging two lines of a paragraph.
    """
    path = Path(path)
    if fitz is None:  # pragma: no cover
        raise PdfUnreadable("PyMuPDF is not installed")
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise PdfUnreadable(f"{path.name} could not be opened") from exc

    lines = []
    try:
        for page in doc:
            rows: dict = {}
            for x0, y0, _x1, _y1, word, *_rest in page.get_text("words"):
                rows.setdefault(round(y0 / row_tolerance), []).append((x0, word))
            for key in sorted(rows):
                lines.append(" ".join(word for _, word in sorted(rows[key])))
    finally:
        doc.close()

    text = "\n".join(lines)
    if len(text.strip()) < min_chars:
        raise PdfUnreadable(
            f"{path.name} yielded almost no text; it is probably a scan, so the "
            "figures must be entered by hand"
        )
    return text
