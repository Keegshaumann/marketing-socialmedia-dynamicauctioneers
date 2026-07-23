"""Verification and human sign-off gate (M3).

Phase 1 merged two source documents (Lightstone EVM and the Dynamic Auctioneers
Property Report) into one ``PropertyRecord``. This module is the gate-1 check
that stands between an ``extracted`` record and anything client-facing: it
cross-examines the merged record for conflicts, writes a ``verification-memo.md``
a human can sign, and refuses to let a record advance without that signature.

Design rules baked in here (mirroring ``engine/schema.py`` and the contract):
- ``deterministic_checks`` is pure code. It runs offline, with no model, and is
  what the tests exercise. It surfaces the two findings the Phase 0 memo
  established by hand -- the garage conflict (a blocking flag) and the flatlet
  that only the physical inspection saw (an awareness note) -- unprompted, plus
  presence and sanity checks on the facts marketing relies on.
- ``research_market`` is the only path that touches a model, and it is
  key-gated: with no ``ANTHROPIC_API_KEY`` and no client it returns ``None`` and
  the memo simply omits the market-context section. It never crashes and never
  hangs. ``build_research_request`` factors out the ``messages.create`` kwargs so
  a test can assert the request shape offline.
- ``sign_off`` enforces the human gate: a block flag that is neither resolved nor
  overridden-with-a-reason refuses the sign-off, and ``verified`` (the only state
  from which the store permits ``drafted``) is reachable *only* through this
  function. That is how "no record is drafted without a recorded sign-off" is
  enforced in code rather than by convention.
- The memo is an internal document, but every public artifact is still built
  from ``public_view()`` downstream; ``research_market`` itself is handed only
  the public projection so no owner PII ever reaches a web-search prompt.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from engine import MODEL
from engine.schema import PropertyRecord
from engine.store import RecordStore


class Flag(BaseModel):
    """One verification finding.

    ``severity`` is ``"block"`` (must be resolved or overridden before sign-off)
    or ``"note"`` (an awareness item that does not gate the workflow). ``code`` is
    the stable identifier a human uses when writing an override reason.
    """

    model_config = ConfigDict(extra="forbid")

    severity: str  # "block" | "note"
    code: str
    title: str
    evidence: str
    action: str


class SignOffRefused(Exception):
    """Raised when sign-off is attempted while a block flag is unresolved and
    not overridden with a written reason (SPEC M8 gate 1)."""


def _now() -> str:
    """UTC timestamp, ISO 8601, seconds precision (matches engine.store)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _rand(amount: Optional[float]) -> str:
    """Format a rand figure as ``R960 000`` (thin-space thousands, SA style)."""
    if amount is None:
        return ""
    whole = int(round(amount))
    return "R" + format(whole, ",").replace(",", " ")


# --- deterministic checks (pure code, no model) --------------------------

def deterministic_checks(record: PropertyRecord) -> List[Flag]:
    """Cross-check the merged record and return flags, block flags first.

    Catches, unprompted from the golden record:
    - the garage conflict (Lightstone says garages, inspection says none) as a
      blocking flag, and
    - the flatlet that only the physical inspection found (absent from the
      Lightstone desktop data) as an awareness note.

    Also checks the facts marketing depends on: extent/unit size, title deed
    number, municipal valuation, GPS, and bed/bath sanity, plus any recorded
    municipality-spelling disagreement between the two sources.
    """
    flags: List[Flag] = []
    physical = record.physical
    identity = record.identity
    valuation = record.valuation

    # 1. Garage conflict -- BLOCK. The two sources disagree and the merge left
    #    garages null with a recorded conflict; do not advertise until resolved.
    if physical is not None and physical.garages_conflict:
        flags.append(
            Flag(
                severity="block",
                code="GARAGE_CONFLICT",
                title="Garages -- sources disagree, do not advertise until confirmed",
                evidence=physical.garages_conflict,
                action=(
                    "Confirm with the agent whether the unit has garages, "
                    "carports, or neither. Garages stay omitted from every "
                    "artifact until this is resolved or overridden with a reason."
                ),
            )
        )

    # 1b. Any other recorded cross-source physical conflict -- BLOCK, same
    #     rationale as the garage conflict: the inspection's value stands in
    #     the record per the merge rule, but a disputed fact must be confirmed
    #     by a human before it reaches an ad (D32).
    for conflict in (physical.conflicts or []) if physical is not None else []:
        flags.append(
            Flag(
                severity="block",
                code="PHYSICAL_CONFLICT",
                title="Physical fact -- sources disagree, confirm before advertising",
                evidence=conflict,
                action=(
                    "Confirm the disputed fact with the agent or a re-inspection. "
                    "The inspection's value stands in the record, but do not "
                    "advertise it until this is resolved or overridden with a "
                    "reason."
                ),
            )
        )

    # 2. Flatlet found only on inspection -- NOTE. Present in the record and the
    #    note says the desktop (Lightstone) data did not show it.
    flatlet = physical.flatlet if physical is not None else None
    if (
        flatlet is not None
        and flatlet.present
        and flatlet.note
        and "lightstone" in flatlet.note.lower()
    ):
        flags.append(
            Flag(
                severity="note",
                code="FLATLET_INSPECTION_ONLY",
                title="Flatlet -- inspection found value the desktop data cannot see",
                evidence=flatlet.note,
                action=(
                    "Lead the copy with the flatlet (income / extended-family "
                    "potential). It is verified by inspection, not by Lightstone."
                ),
            )
        )

    # 3. Municipality spelling disagreement -- NOTE (cross-source conflict).
    if identity is not None and identity.municipality_note:
        flags.append(
            Flag(
                severity="note",
                code="MUNICIPALITY_SPELLING",
                title="Municipality spelling differs between sources",
                evidence=identity.municipality_note,
                action="Use the official Lightstone/deeds spelling in every artifact.",
            )
        )

    # 3b. Professional valuation outside the Lightstone EVM range -- NOTE. The
    #     valuer's figure is internal sale-strategy data (never rendered, see
    #     schema D32), but a divergence from the desktop model belongs in the
    #     pricing conversation before drafting.
    professional = valuation.professional if valuation is not None else None
    evm_range = valuation.evm_range if valuation is not None else None
    if (
        professional is not None
        and professional.market_value is not None
        and evm_range
        and len(evm_range) == 2
        and evm_range[0] is not None
        and evm_range[1] is not None
        and not (evm_range[0] <= professional.market_value <= evm_range[1])
    ):
        flags.append(
            Flag(
                severity="note",
                code="VALUATION_DIVERGENCE",
                title="Professional valuation falls outside the Lightstone EVM range",
                evidence=(
                    f"Valuer's market value {_rand(professional.market_value)} vs "
                    f"EVM range {_rand(evm_range[0])} to {_rand(evm_range[1])}."
                ),
                action=(
                    "Weigh the valuer's inspection-based figure against the "
                    "desktop EVM in the pricing conversation. Internal only; "
                    "neither figure is ad copy."
                ),
            )
        )

    # 4. Presence checks on the facts marketing relies on -- each missing fact
    #    is a NOTE so a human decides whether to chase it before drafting.
    unit_size = physical.unit_size_m2 if physical is not None else None
    if unit_size is None:
        flags.append(
            Flag(
                severity="note",
                code="MISSING_EXTENT",
                title="Extent / unit size missing",
                evidence="Neither source supplied a registered extent (unit_size_m2).",
                action="Obtain the extent before quoting a rand-per-square-metre figure.",
            )
        )

    if identity is None or not identity.title_deed_no:
        flags.append(
            Flag(
                severity="note",
                code="MISSING_TITLE_DEED",
                title="Title deed number missing",
                evidence="No title_deed_no on the record.",
                action="Confirm the title deed number against the deeds data.",
            )
        )

    municipal_valuation = valuation.municipal_valuation if valuation is not None else None
    if municipal_valuation is None:
        flags.append(
            Flag(
                severity="note",
                code="MISSING_MUNICIPAL_VALUATION",
                title="Municipal valuation missing",
                evidence="No municipal_valuation on the record.",
                action="Pull the municipal valuation for the pricing conversation.",
            )
        )

    gps = identity.gps if identity is not None else None
    if not gps or len(gps) != 2:
        flags.append(
            Flag(
                severity="note",
                code="MISSING_GPS",
                title="GPS coordinates missing or malformed",
                evidence=f"gps = {gps!r}; expected [lat, lon].",
                action="Confirm the coordinates so the map pin is correct.",
            )
        )

    # 5. Bed / bath sanity -- NOTE. Missing counts, or an implausible ratio,
    #    warrant a human glance before the counts reach an ad.
    if physical is not None:
        bedrooms = physical.bedrooms
        bathrooms = physical.bathrooms_main_unit
        if bedrooms is None or bedrooms <= 0:
            flags.append(
                Flag(
                    severity="note",
                    code="BED_BATH_SANITY",
                    title="Bedroom count missing or non-positive",
                    evidence=f"bedrooms = {bedrooms!r}.",
                    action="Confirm the bedroom count from the inspection.",
                )
            )
        elif bathrooms is not None and bathrooms > bedrooms + 3:
            flags.append(
                Flag(
                    severity="note",
                    code="BED_BATH_SANITY",
                    title="Bathroom count looks implausibly high for the bedroom count",
                    evidence=f"bedrooms = {bedrooms}, bathrooms_main_unit = {bathrooms}.",
                    action="Re-check the bathroom count; a flatlet en-suite may be miscounted.",
                )
            )

    # Block flags first, notes after; stable order within each group.
    flags.sort(key=lambda f: 0 if f.severity == "block" else 1)
    return flags


# --- memo rendering ------------------------------------------------------

def _property_line(record: PropertyRecord) -> str:
    identity = record.identity
    if identity is None:
        return f"DP{record.dp}"
    parts = [
        identity.street_address or identity.legal_description,
        identity.suburb,
        identity.municipality,
    ]
    return ", ".join(p for p in parts if p) or f"DP{record.dp}"


def _sources_line(record: PropertyRecord) -> str:
    sources = record.sources
    if sources is None:
        return "Sources not recorded."
    bits: List[str] = []
    ls = sources.lightstone_evm
    if ls is not None:
        ref = f" (ref {ls.report_id})" if ls.report_id else ""
        date = f" {ls.report_date}" if ls.report_date else ""
        bits.append(f"Lightstone EVM{date}{ref}")
    pr = sources.property_report
    if pr is not None:
        asat = f" (figures as at {pr.figures_as_at})" if pr.figures_as_at else ""
        by = f", prepared by {pr.prepared_by}" if pr.prepared_by else ""
        bits.append(f"Dynamic Auctioneers Property Report{asat}{by}")
    return " vs ".join(bits) if bits else "Sources not recorded."


def _corroborated_rows(record: PropertyRecord) -> List[tuple]:
    """Facts that are present on the record and safe to treat as corroborated."""
    rows: List[tuple] = []
    physical = record.physical
    identity = record.identity
    valuation = record.valuation

    if physical is not None and physical.unit_size_m2 is not None:
        rows.append(("Extent / unit size", f"{physical.unit_size_m2:g} m2"))
    if identity is not None and identity.title_deed_no:
        rows.append(("Title deed", identity.title_deed_no))
    if valuation is not None and valuation.municipal_valuation is not None:
        year = valuation.municipal_valuation_year
        year_txt = f" ({year} roll)" if year else ""
        rows.append(("Municipal valuation", f"{_rand(valuation.municipal_valuation)}{year_txt}"))
    if identity is not None and identity.gps and len(identity.gps) == 2:
        rows.append(("GPS", f"{identity.gps[0]}, {identity.gps[1]}"))
    if physical is not None and physical.bedrooms is not None:
        rows.append(("Bedrooms (main unit)", str(physical.bedrooms)))
    if physical is not None and physical.zoning:
        rows.append(("Zoning", physical.zoning))
    return rows


def _popia_lines(record: PropertyRecord) -> List[str]:
    lines: List[str] = []
    compliance = record.compliance
    redacted = compliance.owner_pii_redacted if compliance is not None else None
    mark = "[x]" if redacted else "[ ]"
    lines.append(
        f"- {mark} Owner name and ID number are internal-only and stripped from "
        "every public artifact."
    )

    contact_internal = None
    if record.sale_process is not None and record.sale_process.viewing is not None:
        contact_internal = record.sale_process.viewing.contact_internal_only
    contact_mark = "[x]" if contact_internal else "[ ]"
    lines.append(
        f"- {contact_mark} Viewing enquiries route to Dynamic Auctioneers; the "
        "occupant's personal number stays internal (POPIA)."
    )

    fin_mark = "[x]" if record.financials_internal is not None else "[ ]"
    lines.append(
        f"- {fin_mark} Bond, arrears and last-sale figures are internal awareness "
        "only and never appear in marketing."
    )
    return lines


def build_memo(
    record: PropertyRecord,
    flags: List[Flag],
    research: Optional[dict] = None,
) -> str:
    """Render the verification memo as markdown (mirrors the Phase 0 memo).

    Block flags are numbered first, then notes; each carries its evidence and
    the action it demands. The market-context section appears only when
    ``research`` is supplied (it is ``None`` without an API key). A POPIA
    checklist closes the memo with a sign-off line for a human.
    """
    block_flags = [f for f in flags if f.severity == "block"]
    note_flags = [f for f in flags if f.severity != "block"]
    ordered = block_flags + note_flags

    status = "Flags raised" if block_flags else "Verified (no blocking flags)"

    lines: List[str] = []
    lines.append(f"# Verification Memo -- DP{record.dp}")
    lines.append("")
    lines.append(f"**Property:** {_property_line(record)}")
    lines.append(f"**Date compiled:** {_now()[:10]}")
    lines.append(f"**Sources compared:** {_sources_line(record)}")
    lines.append(f"**Status:** {status}")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## Corroborated facts")
    lines.append("")
    rows = _corroborated_rows(record)
    if rows:
        lines.append("| Fact | Value |")
        lines.append("|---|---|")
        for label, value in rows:
            lines.append(f"| {label} | {value} |")
    else:
        lines.append("No corroborated facts on record.")
    lines.append("")

    lines.append("## Flags")
    lines.append("")
    if ordered:
        for i, flag in enumerate(ordered, start=1):
            tag = "[BLOCK]" if flag.severity == "block" else "[NOTE]"
            lines.append(f"### {i}. {tag} {flag.title}")
            lines.append(flag.evidence)
            lines.append(f"**Action:** {flag.action}")
            lines.append("")
    else:
        lines.append("No flags raised.")
        lines.append("")

    if research:
        summary = research.get("summary")
        if summary:
            lines.append("## Market context (for pricing conversations, not for ads)")
            lines.append("")
            lines.append(str(summary))
            lines.append("")
            sources = research.get("sources")
            if sources:
                for src in sources:
                    lines.append(f"- {src}")
                lines.append("")

    lines.append("## POPIA check")
    lines.append("")
    lines.extend(_popia_lines(record))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("**Human sign-off:** ______________  **Date:** ______________")
    if block_flags:
        codes = ", ".join(f.code for f in block_flags)
        lines.append(
            f"*Block flags must be resolved or overridden with a written reason "
            f"before drafting: {codes}.*"
        )
    lines.append("")
    return "\n".join(lines)


# --- market research (key-gated model call) ------------------------------

def build_research_request(record: PropertyRecord) -> dict:
    """Return the ``messages.create`` kwargs for a market-context web search.

    Factored out so a test can assert the request shape offline. Uses the
    ``web_search_20260209`` server tool (no beta header on Opus 4.8) and is fed
    only the ``public_view`` projection, so owner PII never reaches the prompt.
    """
    public = record.public_view()
    identity = public.get("identity") or {}
    valuation = public.get("valuation") or {}
    address = identity.get("street_address") or identity.get("legal_description") or ""
    suburb = identity.get("suburb") or ""
    evm = valuation.get("evm_range")

    prompt = (
        "You are sanity-checking a South African property listing before it is "
        "marketed. Using web search, confirm three things and report concisely:\n"
        f"1. Does the address exist and resolve to {suburb}? Address: {address}\n"
        "2. Are there comparable listings or recent sales nearby, and roughly at "
        "what asking prices?\n"
        f"3. Does an estimated value range of {evm} look sane for the area?\n"
        "Keep the answer to a short paragraph of findings plus the sources used. "
        "Do not invent figures; if the web does not support a claim, say so."
    )

    return {
        "model": MODEL,
        "max_tokens": 2048,
        "tools": [{"type": "web_search_20260209", "name": "web_search"}],
        "messages": [{"role": "user", "content": prompt}],
    }


def research_market(record: PropertyRecord, client=None) -> Optional[dict]:
    """Return a market-context dict, or ``None`` when no key/client is available.

    Key-gated: with neither a passed ``client`` nor ``ANTHROPIC_API_KEY`` this
    returns ``None`` and the memo omits the market section. Any failure degrades
    to ``None`` rather than raising, so verification never crashes or hangs on
    the network.
    """
    if client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            return None
        try:  # pragma: no cover - requires a live key
            import anthropic

            client = anthropic.Anthropic()
        except Exception:
            return None

    try:  # pragma: no cover - requires a live key / network
        response = client.messages.create(**build_research_request(record))
    except Exception:
        return None

    blocks = list(getattr(response, "content", []) or [])

    sources: List[str] = []
    last_non_text = -1
    for index, block in enumerate(blocks):
        btype = getattr(block, "type", None)
        if btype != "text":
            last_non_text = index
        if btype == "web_search_tool_result":
            content = getattr(block, "content", None)
            if isinstance(content, list):
                for item in content:
                    url = getattr(item, "url", None)
                    title = getattr(item, "title", None)
                    if url:
                        sources.append(f"{title or url}: {url}")

    # A tool-using turn interleaves narration ("Let me search...") between the
    # searches; only the text AFTER the final tool block is the findings. The
    # memo is a sign-off document, so it carries the findings, not the workings.
    # A turn with no tool blocks (last_non_text == -1) keeps all its text.
    summary_parts = [
        getattr(block, "text", "")
        for block in blocks[last_non_text + 1:]
        if getattr(block, "type", None) == "text"
    ]

    summary = "\n\n".join(p for p in summary_parts if p).strip()
    if not summary and not sources:
        return None
    return {"summary": summary, "sources": sources}


# --- verify + sign-off (the gate) ----------------------------------------

def verify(
    dp: str,
    store: RecordStore,
    client=None,
    output_root: str = ".",
) -> "tuple[str, List[Flag]]":
    """Verify DP ``dp``: run checks, write the memo, transition to flags_raised.

    Loads the record (which must be in ``extracted``), runs
    ``deterministic_checks``, optionally attaches market research (key-gated),
    writes ``DP<dp>/verification-memo.md`` under ``output_root``, records the
    verification status on the record, and transitions the lifecycle state to
    ``flags_raised``. Returns ``(memo_path, flags)``.
    """
    record = store.get(dp)
    if record is None:
        raise KeyError(f"No record for DP {dp}")

    state = store.get_state(dp)
    if state != "extracted":
        raise ValueError(
            f"DP {dp} must be in 'extracted' to verify; current state is {state!r}."
        )

    flags = deterministic_checks(record)
    research = research_market(record, client)
    memo = build_memo(record, flags, research)

    memo_dir = Path(output_root) / f"DP{dp}"
    memo_dir.mkdir(parents=True, exist_ok=True)
    memo_path = memo_dir / "verification-memo.md"
    memo_path.write_text(memo, encoding="utf-8")

    has_block = any(f.severity == "block" for f in flags)
    if record.verification is None:
        from engine.schema import Verification

        record.verification = Verification()
    record.verification.status = "flags_raised" if has_block else "verified"
    record.verification.memo = "verification-memo.md"

    store.upsert(record)
    store.transition(dp, "flags_raised", note="deterministic verification complete")

    return str(memo_path), flags


def sign_off(
    dp: str,
    store: RecordStore,
    user: str,
    override_notes: Optional[Dict[str, str]] = None,
) -> PropertyRecord:
    """Record a human sign-off and transition the record to ``verified``.

    Refuses (``SignOffRefused``) if any current block flag is neither resolved
    (no longer raised by ``deterministic_checks`` against the current record)
    nor overridden with a written reason in ``override_notes[code]``. On success
    it stamps ``verification.human_signoff``, sets status ``verified``, upserts,
    and transitions the lifecycle state to ``verified``.

    ``verified`` is the only state from which the store permits ``drafted``, and
    this function is the only path to ``verified`` -- so no record is ever
    drafted without a recorded sign-off.
    """
    override_notes = override_notes or {}

    record = store.get(dp)
    if record is None:
        raise KeyError(f"No record for DP {dp}")

    # Guard the source state: sign-off applies only to a record awaiting gate 1.
    # Already-verified records return idempotently; anything past the gate is a
    # refusal so we never re-stamp sign-off onto a drafted/approved/live record.
    state = store.get_state(dp)
    if state == "verified":
        return record
    if state not in ("flags_raised", "extracted"):
        raise SignOffRefused(
            f"Cannot sign off DP {dp}: record is in state {state!r}, not awaiting "
            f"gate-1 sign-off."
        )

    block_flags = [f for f in deterministic_checks(record) if f.severity == "block"]
    unresolved = [
        f
        for f in block_flags
        if not (override_notes.get(f.code) and override_notes[f.code].strip())
    ]
    if unresolved:
        codes = ", ".join(f.code for f in unresolved)
        raise SignOffRefused(
            f"Cannot sign off DP {dp}: block flags need resolution or a written "
            f"override reason: {codes}."
        )

    if record.verification is None:
        from engine.schema import Verification

        record.verification = Verification()

    signoff_bits = [f"{user} @ {_now()}"]
    if override_notes:
        overrides = "; ".join(f"{code}: {reason}" for code, reason in override_notes.items())
        signoff_bits.append(f"overrides -- {overrides}")
    record.verification.human_signoff = " | ".join(signoff_bits)
    record.verification.status = "verified"

    store.upsert(record)
    store.transition(dp, "verified", note=f"signed off by {user}")

    return record


# --- offline self-check --------------------------------------------------

if __name__ == "__main__":
    import json
    import tempfile

    root = Path(__file__).resolve().parents[1]
    record_json = (root / "DP3060" / "record.json").read_text(encoding="utf-8")
    record = PropertyRecord.model_validate_json(record_json)

    # deterministic_checks surfaces the two golden findings unprompted.
    flags = deterministic_checks(record)
    codes = {f.code: f.severity for f in flags}
    assert codes.get("GARAGE_CONFLICT") == "block", "garage conflict must be a block flag"
    assert codes.get("FLATLET_INSPECTION_ONLY") == "note", "flatlet must be a note flag"
    print("deterministic_checks flags:")
    for f in flags:
        print(f"  [{f.severity.upper()}] {f.code}: {f.title}")

    # build_research_request has the right offline shape (no key needed).
    req = build_research_request(record)
    assert req["tools"][0]["type"] == "web_search_20260209"
    assert req["tools"][0]["name"] == "web_search"
    assert req["model"] == MODEL
    assert req["messages"][0]["role"] == "user"
    # public_view feeds the request -- owner PII must not appear in the prompt.
    assert "Tahera Kader" not in json.dumps(req)
    print("build_research_request shape OK (web_search_20260209, no PII in prompt)")

    # research_market is key-gated -> None offline.
    assert research_market(record, client=None) is None
    print("research_market returns None with no key")

    # Full gate over an in-memory store.
    with tempfile.TemporaryDirectory() as tmp:
        store = RecordStore(db_path=":memory:")
        store.upsert(record, state="extracted")

        memo_path, verify_flags = verify("3060", store, output_root=tmp)
        vcodes = {f.code for f in verify_flags}
        assert "GARAGE_CONFLICT" in vcodes and "FLATLET_INSPECTION_ONLY" in vcodes
        assert store.get_state("3060") == "flags_raised"
        memo_text = Path(memo_path).read_text(encoding="utf-8")
        assert "[BLOCK]" in memo_text and "[NOTE]" in memo_text
        assert "Tahera Kader" not in memo_text  # occupant PII never in the memo
        print(f"verify wrote memo -> {memo_path}; state now flags_raised")

        # sign-off refused while the garage block flag is unresolved.
        refused = False
        try:
            sign_off("3060", store, user="gerrie@dynamicauctioneers.co.za")
        except SignOffRefused as exc:
            refused = True
            print(f"sign_off refused as expected: {exc}")
        assert refused, "sign_off should refuse while a block flag is unresolved"
        assert store.get_state("3060") == "flags_raised"

        # sign-off allowed once the block flag is overridden with a reason.
        sign_off(
            "3060",
            store,
            user="gerrie@dynamicauctioneers.co.za",
            override_notes={
                "GARAGE_CONFLICT": "Agent confirmed no garages; ample guest parking only."
            },
        )
        assert store.get_state("3060") == "verified"
        signed = store.get("3060")
        assert signed.verification.human_signoff
        print(f"sign_off accepted with override; state now verified")
        print(f"  human_signoff: {signed.verification.human_signoff}")

        store.close()

    print("\nAll self-checks passed.")
