"""Shared test fixtures and sample-data guards.

The offline suite must run without an API key and without a network. Tests
that genuinely need the real sample PDFs (classification, photo extraction and
the request-shape check) are skipped with a clear reason when those documents
are not present on this machine, so a clean checkout still goes green.

The sample PDFs live under ``~/Documents/dynamicAuctioneers/`` on the author's
machine (SPEC / build contract golden data); they are not part of the repo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the repo root importable so ``import engine`` works no matter where
# pytest is invoked from.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# --- golden record (committed) -------------------------------------------

GOLDEN_RECORD = REPO_ROOT / "DP3060" / "record.json"


# --- sample PDFs (not committed; author's machine only) ------------------

SAMPLE_ROOT = Path.home() / "Documents" / "dynamicAuctioneers"
LIGHTSTONE_3060 = (
    SAMPLE_ROOT
    / "Lightstone"
    / "3060 - EVM_Report_40_Topham_Road__PIETERMARITZBURG__KwaZulu-Natal.pdf"
)
PROPERTY_REPORT_3060 = SAMPLE_ROOT / "Property Reports" / "3060 - PROPERTY REPORT.pdf"
LIGHTSTONE_3035_1 = (
    SAMPLE_ROOT / "Lightstone" / "3035.1 - EVM_Report_52_Fame_street__STRAND__Western_Cape.pdf"
)


def _require(path: Path) -> Path:
    """Skip the calling test unless ``path`` exists on disk."""
    if not path.exists():
        pytest.skip(f"sample document not present: {path}")
    return path


@pytest.fixture
def golden_record_path() -> Path:
    """Path to the committed golden ``DP3060/record.json``."""
    if not GOLDEN_RECORD.exists():
        pytest.skip(f"golden record not present: {GOLDEN_RECORD}")
    return GOLDEN_RECORD


@pytest.fixture
def lightstone_3060() -> Path:
    return _require(LIGHTSTONE_3060)


@pytest.fixture
def property_report_3060() -> Path:
    return _require(PROPERTY_REPORT_3060)


@pytest.fixture
def lightstone_3035_1() -> Path:
    return _require(LIGHTSTONE_3035_1)
