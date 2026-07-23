"""Extraction request-shape and assembly tests (M2), fully offline.

``build_request`` is factored out precisely so the request handed to
``client.messages.parse`` can be asserted without an API key or a network call.
Extraction is sectioned (one small structured-output call per source-derived
section; the full record's grammar is too large for the API to compile), so the
tests also pin the two guarantees that design leans on:

- every section call shares the identical cached prefix (full tool list, which
  serializes ahead of everything else, then system brief + both PDF blocks,
  cache breakpoint on the second PDF), and
- ``extract_record`` assembles the sections into a ``PropertyRecord`` and
  stamps the code-owned fields (dp, status, record_created, source file paths,
  compliance) rather than asking the model for them.

Tests that read the real DP3060 PDFs are skipped when those documents are
absent; the assembly test builds tiny stand-in PDFs so it always runs.
"""

from __future__ import annotations

import base64
import re
from types import SimpleNamespace

import pytest

from engine import MODEL
from engine.extract import SECTIONS, _extract_section, build_request, extract_record
from engine.schema import (
    FinancialsInternal,
    Identity,
    LightstoneSource,
    Owner,
    Physical,
    SaleProcess,
    Sources,
    Valuation,
)


EXPECTED_SECTIONS = [
    "sources",
    "identity",
    "physical",
    "valuation",
    "financials_internal",
    "sale_process",
]


def test_sections_cover_exactly_the_source_derived_fields():
    names = [name for name, _model, _focus in SECTIONS]
    assert names == EXPECTED_SECTIONS
    # Later-stage fields must never be asked of the extraction model.
    for later in ("marketing", "verification", "compliance", "human_overrides"):
        assert later not in names


def test_build_request_top_level_shape(lightstone_3060, property_report_3060):
    for name, model, _focus in SECTIONS:
        req = build_request(lightstone_3060, property_report_3060, "3060", name)

        assert req["model"] == MODEL == "claude-opus-4-8"
        assert req["max_tokens"] == 16000
        assert req["thinking"] == {"type": "adaptive"}
        # The FULL tool list rides on every call (tools serialize ahead of the
        # system block in the cached prefix, so a per-call difference would
        # break caching); the directive text names this section's tool.
        assert [t["name"] for t in req["tools"]] == [
            "record_" + n for n, _m, _f in SECTIONS
        ]
        tool = next(t for t in req["tools"] if t["name"] == "record_" + name)
        assert tool["input_schema"]["title"] == model.__name__
        # non-strict: no grammar-complexity ceiling
        assert all("strict" not in t for t in req["tools"])
        assert f"`record_{name}`" in req["messages"][0]["content"][-1]["text"]
        # tool_choice must be auto while thinking is on (forced choice is
        # disallowed with extended thinking).
        assert req["tool_choice"] == {"type": "auto"}
        # We validate the tool input ourselves; no strict-grammar knobs.
        assert "output_format" not in req and "output_config" not in req


def test_build_request_system_block_is_cacheable(lightstone_3060, property_report_3060):
    req = build_request(lightstone_3060, property_report_3060, "3060", "identity")

    system = req["system"]
    assert isinstance(system, list) and len(system) == 1
    block = system[0]
    assert block["type"] == "text"
    assert block["cache_control"] == {"type": "ephemeral"}
    assert block["text"].strip()  # non-empty stable brief


def test_build_request_document_blocks_precede_text(
    lightstone_3060, property_report_3060
):
    req = build_request(lightstone_3060, property_report_3060, "3060", "identity")

    messages = req["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    content = messages[0]["content"]
    assert len(content) == 3

    doc_a, doc_b, text = content
    for block in (doc_a, doc_b):
        assert block["type"] == "document"
        source = block["source"]
        assert source["type"] == "base64"
        assert source["media_type"] == "application/pdf"
        # The data really is base64 of a PDF (starts with the %PDF magic) and
        # carries no newlines.
        assert "\n" not in source["data"]
        decoded = base64.b64decode(source["data"])
        assert decoded[:4] == b"%PDF"

    # The cached prefix ends after the PDFs: the second document block carries
    # the breakpoint, the first does not (one prefix, not two).
    assert doc_b["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in doc_a

    # The volatile text block comes last and names the DP and the section.
    assert text["type"] == "text"
    assert "3060" in text["text"]
    assert "`identity`" in text["text"]


def test_build_request_orders_lightstone_first(lightstone_3060, property_report_3060):
    """The Lightstone PDF is the first document block, the Property Report the second."""
    req = build_request(lightstone_3060, property_report_3060, "3060", "identity")
    doc_a, doc_b, _ = req["messages"][0]["content"]

    lightstone_bytes = lightstone_3060.read_bytes()
    report_bytes = property_report_3060.read_bytes()
    assert base64.b64decode(doc_a["source"]["data"]) == lightstone_bytes
    assert base64.b64decode(doc_b["source"]["data"]) == report_bytes


def test_section_requests_share_identical_cached_prefix(
    lightstone_3060, property_report_3060
):
    """The caching contract: system + PDFs identical, only the text block varies."""
    reqs = [
        build_request(lightstone_3060, property_report_3060, "3060", name)
        for name, _model, _focus in SECTIONS
    ]
    first = reqs[0]
    for req in reqs[1:]:
        # Tools are part of the cached prefix (they serialize ahead of system
        # and messages), so the list must be byte-identical across sections.
        assert req["tools"] == first["tools"]
        assert req["system"] == first["system"]
        assert req["messages"][0]["content"][:2] == first["messages"][0]["content"][:2]

    # Every per-section text block is distinct (each names its own section).
    texts = {req["messages"][0]["content"][2]["text"] for req in reqs}
    assert len(texts) == len(reqs)


# --- text mode (reads the real PDFs, no API) ------------------------------


def test_text_mode_sends_text_blocks_not_pdf(lightstone_3060, property_report_3060):
    req = build_request(
        lightstone_3060, property_report_3060, "3060", "identity", mode="text"
    )
    doc_a, doc_b, text = req["messages"][0]["content"]
    # Both source blocks are now text, labelled by document, carrying extracted
    # content; no base64 PDF document blocks remain.
    for block, label in ((doc_a, "LIGHTSTONE EVM REPORT"), (doc_b, "DYNAMIC PROPERTY REPORT")):
        assert block["type"] == "text"
        assert label in block["text"]
        assert "[page 1]" in block["text"]
    # The cache breakpoint still sits on the second source block.
    assert doc_b["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in doc_a
    assert text["type"] == "text" and "`identity`" in text["text"]


def test_env_selects_text_mode(monkeypatch, lightstone_3060, property_report_3060):
    monkeypatch.setenv("EXTRACT_PDF_MODE", "text")
    req = build_request(lightstone_3060, property_report_3060, "3060", "physical")
    assert req["messages"][0]["content"][0]["type"] == "text"
    # Default (no env) stays native.
    monkeypatch.delenv("EXTRACT_PDF_MODE", raising=False)
    req2 = build_request(lightstone_3060, property_report_3060, "3060", "physical")
    assert req2["messages"][0]["content"][0]["type"] == "document"


# --- assembly (fake client; no sample docs, no key, no network) -----------


class _FakeToolUse:
    type = "tool_use"

    def __init__(self, name: str, data: dict):
        self.name = name
        self.input = data


_SECTION_MODELS = {name: model for name, model, _focus in SECTIONS}


def _requested_section(kwargs: dict) -> str:
    """The section a request asks for, per the trailing directive text block.

    Every call now carries the identical full tool list (the cache contract),
    so the directive text is what distinguishes one section call from another.
    """
    directive = kwargs["messages"][0]["content"][-1]["text"]
    return re.search(r"calling the `record_(\w+)` tool", directive).group(1)


class _FakeClient:
    """Mocks ``messages.create``: emits the requested section's tool_use block.

    The requested section is read from the directive text block, and the reply
    block is named ``record_<section>``, the way ``_extract_section`` matches
    it before validating with that section's model.
    """

    def __init__(self, outputs: dict):
        self._by_name = {model.__name__: inst for model, inst in outputs.items()}
        self.requests: list = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        section = _requested_section(kwargs)
        inst = self._by_name[_SECTION_MODELS[section].__name__]
        return SimpleNamespace(
            content=[_FakeToolUse("record_" + section, inst.model_dump())],
            stop_reason="tool_use",
        )


def _fake_pdf(tmp_path, name: str):
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4 stand-in for the assembly test")
    return path


def test_extract_record_assembles_sections_and_stamps_code_owned_fields(tmp_path):
    lightstone = _fake_pdf(tmp_path, "lightstone.pdf")
    report = _fake_pdf(tmp_path, "report.pdf")

    fake = _FakeClient(
        {
            # The model was told to leave file fields null; even if it fills
            # them, the caller's real paths must win.
            Sources: Sources(lightstone_evm=LightstoneSource(report_id="LSR-1", file="model-guess.pdf")),
            Identity: Identity(suburb="Prestbury", title_type="sectional"),
            Physical: Physical(bedrooms=3),
            Valuation: Valuation(municipal_valuation=520000),
            FinancialsInternal: FinancialsInternal(owner=Owner(name="ZZOWNER")),
            SaleProcess: SaleProcess(method="offers_invited"),
        }
    )

    rec = extract_record(lightstone, report, "3060", parent_dp=None, client=fake)

    # One call per section, in the declared order (per the directive text; the
    # tool list itself is identical on every call by design).
    assert [_requested_section(r) for r in fake.requests] == [
        n for n, _m, _f in SECTIONS
    ]

    # Sections landed on the record.
    assert rec.identity.suburb == "Prestbury"
    assert rec.physical.bedrooms == 3
    assert rec.valuation.municipal_valuation == 520000
    assert rec.financials_internal.owner.name == "ZZOWNER"
    assert rec.sale_process.method == "offers_invited"
    assert rec.sources.lightstone_evm.report_id == "LSR-1"

    # Code-owned stamps: never the model's to answer.
    assert rec.dp == "3060"
    assert rec.status == "extracted"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", rec.record_created)
    assert rec.compliance.owner_pii_redacted is True
    assert rec.sources.lightstone_evm.file == str(lightstone)  # real path wins
    assert rec.sources.property_report.file == str(report)

    # Later-stage fields stay untouched for the pipeline to fill.
    assert rec.marketing is None
    assert rec.verification is None
    assert rec.human_overrides is None

    # POPIA: the owner PII the model placed internally survives assembly but
    # never reaches the public projection.
    pv = rec.public_view()
    assert "financials_internal" not in pv


def test_extract_record_survives_all_empty_sections(tmp_path):
    """A property the model finds nothing for still assembles a valid record."""
    lightstone = _fake_pdf(tmp_path, "l.pdf")
    report = _fake_pdf(tmp_path, "r.pdf")
    fake = _FakeClient(
        {
            Sources: Sources(),
            Identity: Identity(),
            Physical: Physical(),
            Valuation: Valuation(),
            FinancialsInternal: FinancialsInternal(),
            SaleProcess: SaleProcess(),
        }
    )

    rec = extract_record(lightstone, report, "3035.1", parent_dp="3035", client=fake)

    assert rec.dp == "3035.1"
    assert rec.parent_dp == "3035"
    assert rec.status == "extracted"
    # The file stamps exist even when the model returned an empty Sources.
    assert rec.sources.lightstone_evm.file == str(lightstone)
    assert rec.sources.property_report.file == str(report)


def _all_empty_client():
    return _FakeClient(
        {
            Sources: Sources(),
            Identity: Identity(),
            Physical: Physical(),
            Valuation: Valuation(),
            FinancialsInternal: FinancialsInternal(),
            SaleProcess: SaleProcess(),
        }
    )


def test_pace_seconds_waits_between_but_not_before_first_call(tmp_path, monkeypatch):
    lightstone = _fake_pdf(tmp_path, "l.pdf")
    report = _fake_pdf(tmp_path, "r.pdf")
    sleeps: list = []
    monkeypatch.setattr("engine.extract.time.sleep", lambda s: sleeps.append(s))

    extract_record(lightstone, report, "3060", client=_all_empty_client(), pace_seconds=62)

    # One wait between each pair of the six section calls: five waits, each 62s.
    assert sleeps == [62] * (len(SECTIONS) - 1)


def test_pace_seconds_zero_never_sleeps(tmp_path, monkeypatch):
    lightstone = _fake_pdf(tmp_path, "l.pdf")
    report = _fake_pdf(tmp_path, "r.pdf")
    sleeps: list = []
    monkeypatch.setattr("engine.extract.time.sleep", lambda s: sleeps.append(s))

    extract_record(lightstone, report, "3060", client=_all_empty_client(), pace_seconds=0)

    assert sleeps == []


# --- section retry: tool call is forced (thinking off) if the model skips it ---


def test_extract_section_forces_tool_on_retry(tmp_path):
    lightstone = _fake_pdf(tmp_path, "l.pdf")
    report = _fake_pdf(tmp_path, "r.pdf")
    req = build_request(lightstone, report, "3060", "identity", mode="native")

    class _SkipThenCall:
        def __init__(self):
            self.calls: list = []
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:  # first attempt: model answers without the tool
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="no tool")],
                    stop_reason="end_turn",
                )
            return SimpleNamespace(  # retry: a valid tool call
                content=[_FakeToolUse("record_identity", {"suburb": "Prestbury"})],
                stop_reason="tool_use",
            )

    fake = _SkipThenCall()
    out = _extract_section(fake, req, "identity", Identity)

    assert out.suburb == "Prestbury"
    assert len(fake.calls) == 2
    # First attempt: thinking on, tool_choice auto.
    assert fake.calls[0]["thinking"] == {"type": "adaptive"}
    assert fake.calls[0]["tool_choice"] == {"type": "auto"}
    # Retry: thinking dropped so this section's tool can be forced.
    assert "thinking" not in fake.calls[1]
    assert fake.calls[1]["tool_choice"] == {"type": "tool", "name": "record_identity"}


def test_extract_section_rejects_wrong_section_tool_and_retries_forced(tmp_path):
    """A call to a DIFFERENT section's tool counts as no call and is retried.

    All six section tools ride on every request (the cache contract), so the
    model could in principle answer with the wrong one; the forced retry names
    the right tool.
    """
    lightstone = _fake_pdf(tmp_path, "l.pdf")
    report = _fake_pdf(tmp_path, "r.pdf")
    req = build_request(lightstone, report, "3060", "identity", mode="native")

    class _WrongThenRight:
        def __init__(self):
            self.calls: list = []
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:  # first attempt: the wrong section's tool
                return SimpleNamespace(
                    content=[_FakeToolUse("record_physical", {"bedrooms": 3})],
                    stop_reason="tool_use",
                )
            return SimpleNamespace(
                content=[_FakeToolUse("record_identity", {"suburb": "Prestbury"})],
                stop_reason="tool_use",
            )

    fake = _WrongThenRight()
    out = _extract_section(fake, req, "identity", Identity)

    assert out.suburb == "Prestbury"
    assert len(fake.calls) == 2
    assert fake.calls[1]["tool_choice"] == {"type": "tool", "name": "record_identity"}


def test_normalize_record_canonicalizes_source_formats():
    from engine.extract import normalize_record
    from engine.schema import (
        LastSale,
        PropertyRecord as PR,
        SameSchemeSale,
    )

    rec = PR(
        dp="3060",
        sources={"lightstone_evm": {"report_date": "2026/07/03"},
                 "property_report": {"figures_as_at": "2026/07/06"}},
        identity={"title_type": "Sectional Title"},
        physical={"zoning": "RESIDENTIAL"},
        valuation={"same_scheme_sale": {"sale_date": "2025/07/04"},
                   "professional": {"valuation_date": "2026/06/22"}},
        financials_internal={"as_at": "2026/07/06", "last_sale": {"date": "2021/04/12"}},
    )
    out = normalize_record(rec)
    assert out.sources.lightstone_evm.report_date == "2026-07-03"
    assert out.sources.property_report.figures_as_at == "2026-07-06"
    assert out.identity.title_type == "sectional"
    assert out.physical.zoning == "Residential"
    assert out.valuation.same_scheme_sale.sale_date == "2025-07-04"
    assert out.valuation.professional.valuation_date == "2026-06-22"
    assert out.financials_internal.as_at == "2026-07-06"
    assert out.financials_internal.last_sale.date == "2021-04-12"


def test_normalize_record_passes_unknown_shapes_through():
    from engine.extract import normalize_record
    from engine.schema import PropertyRecord as PR

    rec = PR(
        dp="3061",
        sources={"lightstone_evm": {"report_date": "3 July 2026"}},  # not YYYY/MM/DD
        identity={"title_type": "Full Title Freehold"},
        physical={"zoning": "Mixed Use"},  # already mixed case: untouched
    )
    out = normalize_record(rec)
    assert out.sources.lightstone_evm.report_date == "3 July 2026"  # never invented
    assert out.identity.title_type == "freehold"
    assert out.physical.zoning == "Mixed Use"
    # A record with everything None normalizes without error.
    assert normalize_record(PR(dp="3062")).identity is None


def test_extract_section_raises_when_tool_never_called(tmp_path):
    lightstone = _fake_pdf(tmp_path, "l.pdf")
    report = _fake_pdf(tmp_path, "r.pdf")
    req = build_request(lightstone, report, "3060", "identity", mode="native")

    class _NeverCall:
        def __init__(self):
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **kwargs):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="x")], stop_reason="end_turn"
            )

    with pytest.raises(RuntimeError, match="identity"):
        _extract_section(_NeverCall(), req, "identity", Identity)
