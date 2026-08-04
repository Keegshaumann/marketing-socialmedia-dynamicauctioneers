"""Shared test fixtures and sample-data guards.

The offline suite must run without an API key and without a network. Tests
that genuinely need the real sample PDFs (classification, photo extraction and
the request-shape check) are skipped with a clear reason when those documents
are not present on this machine, so a clean checkout still goes green.

The sample PDFs live under ``~/Documents/dynamicAuctioneers/`` on the author's
machine (SPEC / build contract golden data); they are not part of the repo.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# The session cookie is Secure by default (webapp.main), which a real browser -
# and the httpx TestClient - will not send over http://testserver. Opt into the
# insecure cookie for the test transport. This MUST be set before webapp.main is
# imported (its middleware reads it at app-construction time); conftest is loaded
# before any test module, so a module-level setenv here is early enough.
os.environ.setdefault("ENGINE_ALLOW_INSECURE_COOKIE", "1")

# Printing the info pack to PDF costs a Chromium launch (~2-3s) per render, and
# dozens of tests render the pack: with it on the suite went from 33s to nearly
# nine minutes. Off by default here; the tests that specifically cover the PDF
# export re-enable it for themselves (see test_render.py).
os.environ.setdefault("ENGINE_PDF_EXPORT", "0")

# The extraction / research cache (engine.aicache) is content-addressed, so two
# tests that build the same request would share a cached result and the second
# would assert against the first one's data. Off by default here; the tests that
# cover the cache turn it on for themselves.
os.environ.setdefault("ENGINE_AI_CACHE", "0")

# Make the repo root importable so ``import engine`` works no matter where
# pytest is invoked from.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# --- hermetic environment (no live external calls in the suite) ----------

# engine.extract / engine.cli call load_dotenv() at import, which pulls a
# developer's real .env (Anthropic + GoHighLevel credentials) into os.environ
# for the whole pytest session. Left in place, a distribution test that reaches
# post_to_planner's real-call path would fire a LIVE social post. This autouse
# fixture strips every external credential before each test, so the suite stays
# offline and credential-free as documented; a test that needs one sets it
# explicitly via monkeypatch.
_EXTERNAL_CRED_VARS = (
    "ANTHROPIC_API_KEY",
    "GHL_API_TOKEN",
    "GHL_LOCATION_ID",
    "GHL_ACCOUNT_MAP",
    "GHL_USER_ID",
    "GHL_POST_STATUS",
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    for var in _EXTERNAL_CRED_VARS:
        monkeypatch.delenv(var, raising=False)
    # Reset the in-process login throttle so failed-login attempts in one test
    # never leak into another (all tests share one client IP / app instance).
    try:
        from webapp.ratelimit import throttle

        throttle.reset()
    except Exception:
        pass
    yield


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
