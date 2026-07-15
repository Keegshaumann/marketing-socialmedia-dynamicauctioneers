"""Command-line entry point for the marketing engine (Phases 1 to 7).

This wires the ingestion, verification, rendering, distribution and CRM modules
into one ``engine`` command. Ingestion: intake (M1) pairs and classifies the
source PDFs, photos (M2) pulls the inspection images, extract (M2) turns the
pair into a ``PropertyRecord`` via Claude, and store (M4) persists the record
and its lifecycle state. Verification (M3): ``verify`` runs the deterministic
gate-1 checks and writes the memo, ``sign-off`` records the human sign-off.
Rendering (M5): ``render`` produces the marketing artifacts through a swappable
backend, ``backends`` lists backend availability, and ``set-price`` records a
price change and re-renders. Distribution (M6): ``channels`` prints the channel
routing matrix and ``pack`` builds a ready-to-post pack for the manual channels.
Buyer CRM (M7): ``crm-add`` records an enquiry and tags the buyer, ``crm-match``
lists the buyers matched to a listing plus its broadcast line.

Design rules baked in here:
- A complete job (both source documents present) is ingested; an incomplete
  job prints the missing document and is skipped, satisfying the M1 rule that a
  lone document waits and flags rather than proceeding.
- Extraction is skipped, and a minimal intake record is stored instead, when
  ``--no-extract`` is given or no API key is available. The reason is always
  printed so the operator knows why Claude did not run.
- Errors surface as a plain message and a non-zero exit code, never a traceback.
- No emojis, South African English, no em or en dashes in any output.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

from engine import __version__
from engine.intake import IntakeJob, build_jobs, build_jobs_from_dir
from engine.photos import extract_photos, rank_photos
from engine.schema import (
    LightstoneSource,
    Marketing,
    PropertyRecord,
    PropertyReportSource,
    Sources,
)
from engine.store import RecordStore

load_dotenv()


# Friendly labels for the two required documents, used in operator messages.
_DOC_LABELS = {
    "lightstone_evm": "Lightstone EVM missing",
    "property_report": "property report missing",
}


def _has_api_key() -> bool:
    """True when an Anthropic key is available in the environment."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _normalise_dp(value: str) -> str:
    """Strip an optional ``DP`` prefix so ``DP3060`` and ``3060`` match."""
    return re.sub(r"^\s*DP", "", value, flags=re.IGNORECASE).strip()


def _relative_photo(path: Path) -> str:
    """Store a photo as ``photos/<name>`` to match the record.json convention."""
    return f"photos/{path.name}"


def _gallery_paths(photos: List[Path]) -> dict:
    """Rank the extracted photos into a hero pick and a gallery list."""
    picks = rank_photos(photos)
    hero = picks.get("hero")
    gallery = picks.get("gallery") or []
    return {
        "hero": _relative_photo(hero) if hero else None,
        "gallery": [_relative_photo(p) for p in gallery],
    }


def _build_minimal_record(job: IntakeJob, picks: dict) -> PropertyRecord:
    """Build the intake-stage record used when Claude extraction is skipped.

    It carries only what intake and photo extraction already know: the DP, the
    source file paths and the ranked gallery. Every other fact stays null so the
    record never claims something the documents were not read for.
    """
    return PropertyRecord(
        dp=job.dp,
        parent_dp=job.parent_dp,
        status="intake",
        sources=Sources(
            lightstone_evm=LightstoneSource(file=str(job.lightstone_evm))
            if job.lightstone_evm
            else None,
            property_report=PropertyReportSource(file=str(job.property_report))
            if job.property_report
            else None,
        ),
        marketing=Marketing(hero_photo=picks["hero"], gallery=picks["gallery"]),
    )


def _write_record(output_dir: Path, record: PropertyRecord) -> Path:
    """Write the record as pretty JSON to ``<output_dir>/record.json``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    record_path = output_dir / "record.json"
    record_path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return record_path


def _ingest_job(
    job: IntakeJob,
    store: RecordStore,
    output_root: Path,
    no_extract: bool,
) -> str:
    """Ingest one intake job.

    Returns ``"ok"`` when the job was stored, ``"incomplete"`` when a required
    document was missing (a soft flag, not a failure), or ``"error"`` when a
    step failed.
    """
    print(f"DP{job.dp}")

    if not job.is_complete:
        gaps = ", ".join(_DOC_LABELS.get(m, m) for m in job.missing)
        print(f"  incomplete: {gaps}. Skipping.")
        return "incomplete"

    if job.unknown:
        print(f"  note: {len(job.unknown)} file(s) could not be classified and were ignored.")

    output_dir = output_root / f"DP{job.dp}"
    photos_dir = output_dir / "photos"

    # Photo extraction runs for every complete job, extraction or not.
    try:
        photos = extract_photos(job.property_report, photos_dir)
    except Exception as exc:  # noqa: BLE001 - report, do not crash the run
        print(f"  error: could not extract photos: {exc}")
        return "error"
    picks = _gallery_paths(photos)

    use_extract = not no_extract and _has_api_key()

    if not use_extract:
        reason = (
            "--no-extract given"
            if no_extract
            else "ANTHROPIC_API_KEY not set"
        )
        record = _build_minimal_record(job, picks)
        try:
            record_path = _write_record(output_dir, record)
            store.upsert(record, state="intake")
        except Exception as exc:  # noqa: BLE001
            print(f"  error: could not store record: {exc}")
            return "error"
        print("  documents paired: Lightstone EVM + Property Report")
        print(f"  photos extracted: {len(photos)}")
        print(f"  Claude extraction skipped ({reason}). Stored minimal intake record.")
        print("  state: intake")
        print(f"  record: {record_path}")
        return "ok"

    # Full path: Claude extraction, then attach the photo picks.
    from engine.extract import extract_record  # deferred so --no-extract needs no key

    try:
        record = extract_record(
            job.lightstone_evm,
            job.property_report,
            job.dp,
            parent_dp=job.parent_dp,
        )
    except RuntimeError as exc:
        print(f"  error: {exc}")
        return "error"
    except Exception as exc:  # noqa: BLE001
        print(f"  error: extraction failed: {exc}")
        return "error"

    if record.marketing is None:
        record.marketing = Marketing()
    record.marketing.hero_photo = picks["hero"]
    record.marketing.gallery = picks["gallery"]

    try:
        record_path = _write_record(output_dir, record)
        store.upsert(record, state="extracted")
    except Exception as exc:  # noqa: BLE001
        print(f"  error: could not store record: {exc}")
        return "error"

    print("  documents paired: Lightstone EVM + Property Report")
    print(f"  photos extracted: {len(photos)}")
    print("  state: extracted")
    print(f"  record: {record_path}")
    return "ok"


def _cmd_ingest(args: argparse.Namespace) -> int:
    paths = [Path(p) for p in args.paths]
    missing = [p for p in paths if not p.exists()]
    if missing:
        for p in missing:
            print(f"Error: path does not exist: {p}", file=sys.stderr)
        return 1

    if len(paths) == 1 and paths[0].is_dir():
        jobs = build_jobs_from_dir(paths[0])
    else:
        jobs = build_jobs(paths)

    if args.dp:
        wanted = _normalise_dp(args.dp)
        jobs = [j for j in jobs if j.dp == wanted]

    if not jobs:
        target = args.dp if args.dp else "the given path(s)"
        print(f"No property jobs found for {target}.")
        return 1

    output_root = Path(args.output_root) if args.output_root else Path.cwd()

    store = RecordStore(args.db)
    results: List[str] = []
    try:
        for job in jobs:
            results.append(_ingest_job(job, store, output_root, args.no_extract))
    finally:
        store.close()

    # A hard error fails the run. Incomplete jobs are a soft flag: they only
    # fail the run when nothing else was ingested (so the operator sees a
    # non-zero code when the whole request produced no record).
    if "error" in results:
        return 1
    if "ok" not in results:
        return 1
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    dp = _normalise_dp(args.dp)
    store = RecordStore(args.db)
    try:
        record = store.get(dp)
    finally:
        store.close()
    if record is None:
        print(f"No record found for DP {dp}.")
        return 1
    print(record.model_dump_json(indent=2))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    store = RecordStore(args.db)
    try:
        rows = store.list_records()
    finally:
        store.close()
    if not rows:
        print("No records stored yet.")
        return 0

    header = f"{'DP':<10} {'STATE':<14} {'SUBURB':<22} {'UPDATED'}"
    print(header)
    print("-" * len(header))
    for row in rows:
        dp = row.get("dp") or ""
        state = row.get("state") or ""
        suburb = row.get("suburb") or "-"
        updated = row.get("updated_at") or "-"
        print(f"{dp:<10} {state:<14} {suburb:<22} {updated}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    dp = _normalise_dp(args.dp)
    store = RecordStore(args.db)
    try:
        state = store.get_state(dp)
    finally:
        store.close()
    if state is None:
        print(f"No record found for DP {dp}.")
        return 1
    print(f"DP{dp}: {state}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    """Run the gate-1 deterministic verification and write the memo."""
    from engine.verify import verify

    dp = _normalise_dp(args.dp)
    output_root = Path(args.output_root) if args.output_root else Path.cwd()

    store = RecordStore(args.db)
    try:
        state = store.get_state(dp)
        if state is None:
            print(f"No record found for DP {dp}.")
            return 1

        # Extraction may have been skipped (--no-extract or no API key), leaving
        # the record at 'intake'. The deterministic gate can still run on what
        # intake and photo extraction captured, so promote it first (a legal
        # move) and note why.
        if state == "intake":
            store.transition(
                dp, "extracted", note="promoted for verification (extraction skipped)"
            )
            print(f"  note: DP{dp} was at 'intake' (extraction skipped); promoted to 'extracted'.")
            state = "extracted"

        if state != "extracted":
            print(
                f"DP{dp} is '{state}'; verification runs from 'extracted'. "
                "Nothing to do."
            )
            return 1

        memo_path, flags = verify(dp, store, output_root=str(output_root))
        final_state = store.get_state(dp)
    finally:
        store.close()

    blocks = [f for f in flags if f.severity == "block"]
    notes = [f for f in flags if f.severity != "block"]
    print(f"DP{dp}: {len(blocks)} block flag(s), {len(notes)} note(s).")
    for flag in flags:
        tag = "BLOCK" if flag.severity == "block" else "NOTE"
        print(f"  [{tag}] {flag.code}: {flag.title}")
    print(f"  memo: {memo_path}")
    print(f"  state: {final_state}")
    if blocks:
        codes = ", ".join(f.code for f in blocks)
        print(
            f"  action: resolve or override the block flag(s) before sign-off: {codes}."
        )
    return 0


def _cmd_signoff(args: argparse.Namespace) -> int:
    """Record a human sign-off (gate 1), advancing the record to 'verified'."""
    from engine.verify import SignOffRefused, sign_off

    dp = _normalise_dp(args.dp)

    overrides: dict = {}
    for item in args.override or []:
        if "=" not in item:
            print(
                f"Error: --override expects CODE=reason, got {item!r}.",
                file=sys.stderr,
            )
            return 1
        code, reason = item.split("=", 1)
        code = code.strip()
        reason = reason.strip()
        if not code or not reason:
            print(
                f"Error: --override expects a non-empty CODE and reason, got {item!r}.",
                file=sys.stderr,
            )
            return 1
        overrides[code] = reason

    store = RecordStore(args.db)
    try:
        if store.get_state(dp) is None:
            print(f"No record found for DP {dp}.")
            return 1
        try:
            record = sign_off(
                dp, store, user=args.user, override_notes=overrides or None
            )
        except SignOffRefused as exc:
            print(f"Sign-off refused: {exc}", file=sys.stderr)
            return 1
        final_state = store.get_state(dp)
        signoff = record.verification.human_signoff if record.verification else None
    finally:
        store.close()

    print(f"DP{dp} signed off by {args.user}.")
    print(f"  state: {final_state}")
    if overrides:
        for code, reason in overrides.items():
            print(f"  override {code}: {reason}")
    if signoff:
        print(f"  audit: {signoff}")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    """Render one or every marketing artifact for a DP through a backend."""
    from engine.render.service import render_all, render_one

    dp = _normalise_dp(args.dp)
    output_root = Path(args.output_root) if args.output_root else Path.cwd()

    store = RecordStore(args.db)
    try:
        if store.get_state(dp) is None:
            print(f"No record found for DP {dp}.")
            return 1
        if args.fmt:
            artifacts = [
                render_one(
                    dp,
                    store,
                    args.fmt,
                    backend=args.backend,
                    output_root=str(output_root),
                )
            ]
        else:
            artifacts = render_all(
                dp, store, backend=args.backend, output_root=str(output_root)
            )
    finally:
        store.close()

    if not artifacts:
        print(f"DP{dp}: the selected backend rendered no formats.")
        return 1

    backend_name = artifacts[0].backend
    print(f"DP{dp}: rendered {len(artifacts)} artifact(s) via '{backend_name}'.")
    for art in artifacts:
        print(f"  {art.fmt:<16} {art.path}")
    return 0


def _cmd_backends(args: argparse.Namespace) -> int:
    """List registered render backends and whether each is available."""
    from engine.render import list_backends

    info = list_backends()
    header = f"{'BACKEND':<10} {'STATUS':<12} REASON"
    print(header)
    print("-" * len(header))
    for name, (ok, reason) in info.items():
        status = "available" if ok else "unavailable"
        print(f"{name:<10} {status:<12} {reason}")
    return 0


def _cmd_set_price(args: argparse.Namespace) -> int:
    """Change a record's price, emit the diff, and re-render its artifacts."""
    from engine.render.service import set_price

    dp = _normalise_dp(args.dp)
    output_root = Path(args.output_root) if args.output_root else Path.cwd()

    store = RecordStore(args.db)
    try:
        if store.get_state(dp) is None:
            print(f"No record found for DP {dp}.")
            return 1
        result = set_price(
            dp,
            store,
            args.amount,
            backend=args.backend,
            output_root=str(output_root),
        )
    finally:
        store.close()

    print(f"DP{dp} price: {result.old or 'unset'} -> {result.new}")
    print(f"  state: {result.state}")
    print(f"  re-rendered {len(result.artifacts)} artifact(s).")
    return 0


def _cmd_channels(args: argparse.Namespace) -> int:
    """Print the channel routing matrix for a DP (M6, SPEC 5.6)."""
    from engine.distribute import channel_matrix, property_value

    dp = _normalise_dp(args.dp)
    store = RecordStore(args.db)
    try:
        record = store.get(dp)
    finally:
        store.close()
    if record is None:
        print(f"No record found for DP {dp}.")
        return 1

    matrix = channel_matrix(record)
    value = property_value(record)
    routed = [ch for ch, on in matrix.items() if on]
    excluded = [ch for ch, on in matrix.items() if not on]

    value_line = f"R{int(value):,}".replace(",", " ") if value is not None else "unknown"
    print(f"DP{dp}: channel routing matrix (value {value_line}).")
    print(f"  routed to {len(routed)} channel(s):")
    for channel in routed:
        print(f"    [on]  {channel}")
    for channel in excluded:
        print(f"    [off] {channel}")
    return 0


def _cmd_pack(args: argparse.Namespace) -> int:
    """Build a ready-to-post pack for the manual channels of a DP (M6, 5A)."""
    from engine.distribute import build_manual_pack
    from engine.render.service import render_all

    dp = _normalise_dp(args.dp)
    output_root = Path(args.output_root) if args.output_root else Path.cwd()

    store = RecordStore(args.db)
    try:
        if store.get_state(dp) is None:
            print(f"No record found for DP {dp}.")
            return 1

        artifacts = _load_artifacts(output_root, dp)
        if not artifacts:
            # No manifest yet: render the artifacts first through the key-free
            # html backend so the pack has something to gather.
            try:
                artifacts = render_all(dp, store, output_root=str(output_root))
            except Exception as exc:  # noqa: BLE001 - report, do not crash
                print(f"  note: could not render artifacts ({exc}); building an empty pack.")
                artifacts = []
    finally:
        store.close()

    pack_dir = build_manual_pack(dp, artifacts, output_root=str(output_root))
    print(f"DP{dp}: ready-to-post pack built with {len(artifacts)} artifact(s).")
    print(f"  pack: {pack_dir}")
    print(f"  checklist: {Path(pack_dir) / 'checklist.md'}")
    return 0


def _load_artifacts(output_root: Path, dp: str) -> list:
    """Load the rendered-artifact manifest for a DP, or an empty list.

    Reads ``DP<dp>/artifacts/manifest.json`` (written by the render service) and
    returns the plain dicts, which ``build_manual_pack`` accepts alongside
    ``Artifact`` dataclasses. A missing or unreadable manifest yields an empty
    list rather than an error.
    """
    manifest = output_root / f"DP{dp}" / "artifacts" / "manifest.json"
    if not manifest.exists():
        return []
    import json

    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _cmd_crm_add(args: argparse.Namespace) -> int:
    """Record a buyer enquiry and tag the contact from its DP (M7)."""
    from engine.crm import record_enquiry

    dp = _normalise_dp(args.dp) if args.dp else None
    contact = record_enquiry(args.db, args.source, args.raw, dp=dp)

    print(f"Enquiry recorded from {args.source}.")
    print(f"  contact: {contact.handle or 'anonymous'} (id {contact.id})")
    print(f"  DP: {contact.dp or 'unknown'}")
    print(f"  category: {contact.category or 'unknown'}")
    print(f"  area: {contact.area or 'unknown'}")
    print(f"  budget band: {contact.budget_band or 'unknown'}")
    return 0


def _cmd_crm_match(args: argparse.Namespace) -> int:
    """List the buyers matched to a listing plus its broadcast line (M7)."""
    from engine.crm import broadcast_text, matched_buyers

    dp = _normalise_dp(args.dp)
    store = RecordStore(args.db)
    try:
        record = store.get(dp)
        if record is None:
            print(f"No record found for DP {dp}.")
            return 1
        matched = matched_buyers(args.db, record)
    finally:
        store.close()

    print(broadcast_text(record, matched))
    if matched:
        print(f"  matched buyers ({len(matched)}):")
        for contact in matched:
            print(
                f"    {contact.handle or 'anonymous'} "
                f"[{contact.category or '-'} / {contact.area or '-'} / "
                f"{contact.budget_band or '-'}]"
            )
    else:
        print("  no matched buyers yet.")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engine",
        description="Dynamic Auctioneers marketing engine (Phase 1: ingest).",
    )
    parser.add_argument("--version", action="version", version=f"engine {__version__}")
    sub = parser.add_subparsers(dest="command")

    ingest = sub.add_parser(
        "ingest",
        help="Pair and classify source PDFs, extract photos and a record, and store it.",
    )
    ingest.add_argument(
        "paths",
        nargs="+",
        help="A directory of PDFs, or explicit PDF file paths to pair.",
    )
    ingest.add_argument("--dp", help="Only ingest this DP number (for example 3060).")
    ingest.add_argument("--db", help="SQLite database path (default: env ENGINE_DB or ./engine.db).")
    ingest.add_argument(
        "--output-root",
        help="Root directory for DP<dp>/ working folders (default: current directory).",
    )
    ingest.add_argument(
        "--no-extract",
        action="store_true",
        help="Skip Claude extraction and store a minimal intake record.",
    )
    ingest.set_defaults(func=_cmd_ingest)

    show = sub.add_parser("show", help="Print the stored record for a DP number.")
    show.add_argument("dp", help="DP number, for example 3060.")
    show.add_argument("--db", help="SQLite database path.")
    show.set_defaults(func=_cmd_show)

    lst = sub.add_parser("list", help="List stored records: DP, state, suburb, updated.")
    lst.add_argument("--db", help="SQLite database path.")
    lst.set_defaults(func=_cmd_list)

    status = sub.add_parser("status", help="Print the lifecycle state for a DP number.")
    status.add_argument("dp", help="DP number, for example 3060.")
    status.add_argument("--db", help="SQLite database path.")
    status.set_defaults(func=_cmd_status)

    verify = sub.add_parser(
        "verify",
        help="Run the gate-1 deterministic verification and write the memo.",
    )
    verify.add_argument("dp", help="DP number, for example 3060.")
    verify.add_argument("--db", help="SQLite database path.")
    verify.add_argument(
        "--output-root",
        help="Root for DP<dp>/verification-memo.md (default: current directory).",
    )
    verify.set_defaults(func=_cmd_verify)

    signoff = sub.add_parser(
        "sign-off",
        help="Record a human sign-off (gate 1), advancing the record to 'verified'.",
    )
    signoff.add_argument("dp", help="DP number, for example 3060.")
    signoff.add_argument(
        "--user",
        default=os.environ.get("USER", "operator"),
        help="The person signing off (default: current OS user).",
    )
    signoff.add_argument(
        "--override",
        action="append",
        metavar="CODE=reason",
        help="Override a block flag with a written reason. Repeatable.",
    )
    signoff.add_argument("--db", help="SQLite database path.")
    signoff.set_defaults(func=_cmd_signoff)

    render = sub.add_parser(
        "render",
        help="Render one or every marketing artifact for a DP through a backend.",
    )
    render.add_argument("dp", help="DP number, for example 3060.")
    render.add_argument(
        "--backend",
        help="Render backend (default: env ENGINE_RENDERER or 'html').",
    )
    render.add_argument(
        "--fmt",
        help="Render only this format (default: every supported format).",
    )
    render.add_argument("--db", help="SQLite database path.")
    render.add_argument(
        "--output-root",
        help="Root for DP<dp>/artifacts/ (default: current directory).",
    )
    render.set_defaults(func=_cmd_render)

    backends = sub.add_parser(
        "backends",
        help="List registered render backends and whether each is available.",
    )
    backends.set_defaults(func=_cmd_backends)

    set_price = sub.add_parser(
        "set-price",
        help="Change a record's price, emit the diff, and re-render its artifacts.",
    )
    set_price.add_argument("dp", help="DP number, for example 3060.")
    set_price.add_argument(
        "amount",
        help="New price: a figure (2500000) or a framing label (Offers invited).",
    )
    set_price.add_argument(
        "--backend",
        help="Render backend for the re-render (default: env ENGINE_RENDERER or 'html').",
    )
    set_price.add_argument("--db", help="SQLite database path.")
    set_price.add_argument(
        "--output-root",
        help="Root for DP<dp>/artifacts/ (default: current directory).",
    )
    set_price.set_defaults(func=_cmd_set_price)

    channels = sub.add_parser(
        "channels",
        help="Print the channel routing matrix for a DP (M6).",
    )
    channels.add_argument("dp", help="DP number, for example 3060.")
    channels.add_argument("--db", help="SQLite database path.")
    channels.set_defaults(func=_cmd_channels)

    pack = sub.add_parser(
        "pack",
        help="Build a ready-to-post pack for a DP's manual channels (M6).",
    )
    pack.add_argument("dp", help="DP number, for example 3060.")
    pack.add_argument("--db", help="SQLite database path.")
    pack.add_argument(
        "--output-root",
        help="Root for DP<dp>/packs/ (default: current directory).",
    )
    pack.set_defaults(func=_cmd_pack)

    crm_add = sub.add_parser(
        "crm-add",
        help="Record a buyer enquiry and tag the contact from its DP (M7).",
    )
    crm_add.add_argument(
        "source",
        help="Enquiry source, for example email, facebook or sms.",
    )
    crm_add.add_argument(
        "raw",
        help='The raw enquiry text, for example "reply 3060" or a lead payload.',
    )
    crm_add.add_argument(
        "--dp",
        help="Force the DP number instead of parsing it from the raw text.",
    )
    crm_add.add_argument("--db", help="SQLite database path.")
    crm_add.set_defaults(func=_cmd_crm_add)

    crm_match = sub.add_parser(
        "crm-match",
        help="List the buyers matched to a listing plus its broadcast line (M7).",
    )
    crm_match.add_argument("dp", help="DP number, for example 3060.")
    crm_match.add_argument("--db", help="SQLite database path.")
    crm_match.set_defaults(func=_cmd_crm_match)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Parse arguments and dispatch to the requested subcommand."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return 1

    try:
        return args.func(args)
    except KeyboardInterrupt:  # pragma: no cover - operator interrupt
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - never surface a traceback
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
