"""Render orchestration service (M5, 3A).

The backends in this package render one format at a time. This module is the
layer above them: it loads a record, resolves the channel-aware copy once,
resolves the photo set once, then drives the selected backend across every
supported format and logs the artifacts it produced.

Public API:
- ``render_all(dp, store, backend=None, output_root=".")`` -- render every
  format the resolved backend supports for one property.
- ``render_one(dp, store, fmt, backend=None, output_root=".")`` -- render a
  single format (used by ``engine render <dp> --fmt <f>``).
- ``set_price(dp, store, amount, ...)`` -- record a price change (a diff event,
  ``live -> updated``) and re-render.

Design rules baked in here:
- Backends receive **only** ``record.public_view()`` (SPEC 4.4), so no artifact
  produced through this service can leak owner or occupant PII.
- Copy precedence: generated copy (Claude when a key is present, deterministic
  template offline) is the base; human edits stored on ``record.marketing``
  (``headline``, ``price_display``) are overlaid on top of it, so a re-render
  never discards a human's wording. This is how a price change keeps the human
  headline (SPEC M5).
- The service never crashes on a missing API key: ``generate_copy`` is key-gated
  and falls back to the template path, so ``render_all`` runs fully offline.
- ``set_price`` records the diff on the audit trail. The lifecycle move to
  ``updated`` is emitted only when the record is ``live`` (the one state from
  which that transition is legal); for any other state the new price is recorded
  and re-rendered without an illegal transition.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Union

from engine.render import get_backend
from engine.render.base import FORMATS, Artifact, RenderRequest
from engine.render.copy import generate_copy
from engine.schema import Marketing, PropertyRecord, override_key_allowed
from engine.store import RecordStore

# The two fields that keep dedicated homes on ``record.marketing`` (the copy
# channel) rather than riding the ``human_overrides`` facts channel: price
# carries its own audit/format handling and headline has a copy overlay the LLM
# would otherwise regenerate. Everything else is a ``human_overrides`` key.
_PRICE_PATH = "marketing.price_display"
_HEADLINE_PATH = "marketing.headline"


@dataclass
class PriceChange:
    """The outcome of a ``set_price`` call: the diff plus the re-rendered set."""

    dp: str
    old: Optional[str]
    new: str
    state: Optional[str]
    artifacts: List[Artifact] = field(default_factory=list)


@dataclass
class EditChange:
    """The outcome of ``apply_edits``: the fields changed plus the re-rendered set."""

    dp: str
    changes: dict  # public-view path -> value actually applied
    state: Optional[str]
    artifacts: List[Artifact] = field(default_factory=list)


# --- price formatting ----------------------------------------------------

def _rand(amount: float) -> str:
    """Format a rand figure as ``R2 500 000`` (thin-space thousands, SA style)."""
    return "R" + format(int(round(amount)), ",").replace(",", " ")


def _format_price(amount: Union[int, float, str]) -> str:
    """Format ``amount`` as a public price line.

    A number (or a numeric string like ``2500000`` / ``R2 500 000``) is rendered
    as ``R2 500 000``. A non-numeric phrase (for example ``Offers invited``) is
    kept verbatim, so a caller can set a framing label rather than a figure.
    """
    if isinstance(amount, bool):  # guard: bools are ints in Python
        raise ValueError("amount must be a number or a price string, not a bool.")
    if isinstance(amount, (int, float)):
        return _rand(amount)
    text = str(amount).strip()
    # Drop a decimal fraction first, so "900000.50" formats as R900 000, not the
    # cents-concatenated R90 000 050.
    whole = re.split(r"\.\d+", text, maxsplit=1)[0]
    digits = re.sub(r"[^\d]", "", whole)
    if digits:
        return _rand(int(digits))
    return text


# --- copy + photo resolution ---------------------------------------------

def _resolve_copy(record: PropertyRecord, client=None) -> dict:
    """Generate copy, then overlay human edits stored on ``record.marketing``.

    ``generate_copy`` is key-gated and returns the deterministic template dict
    offline. Any human-authored ``headline`` or ``price_display`` on
    ``record.marketing`` then wins, so re-rendering preserves the edit.
    """
    copy = generate_copy(record, client=client)

    marketing = record.marketing
    if marketing is not None:
        if marketing.headline:
            copy["headline"] = marketing.headline
        if marketing.price_display:
            copy["price_display"] = marketing.price_display
    return copy


def _resolve_photos(record: PropertyRecord, output_root: str) -> List[str]:
    """Resolve the record's hero + gallery picks to on-disk photo paths.

    Backends that upload photos (Canva) read these paths directly; the html
    backend prefers the record's own picks and treats this list as a fallback.
    Record paths are stored relative to the ``DP<dp>`` folder; they are joined to
    ``output_root`` here so a backend gets a path it can open.
    """
    marketing = record.marketing
    picks: List[str] = []
    if marketing is not None:
        if marketing.hero_photo:
            picks.append(marketing.hero_photo)
        picks.extend(marketing.gallery or [])

    base = Path(output_root) / f"DP{record.dp}"
    resolved: List[str] = []
    for raw in picks:
        if not raw:
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = base / raw
        resolved.append(str(candidate))
    return resolved


def _write_manifest(output_root: str, dp: str, artifacts: List[Artifact]) -> None:
    """Log the rendered artifacts to ``DP<dp>/artifacts/manifest.json``.

    A plain-file log of what was produced (per DP, per format, per backend, per
    version) that downstream distribution can read without a database round-trip.
    """
    if not artifacts:
        return
    art_dir = Path(output_root) / f"DP{dp}" / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "dp": art.dp,
            "fmt": art.fmt,
            "backend": art.backend,
            "path": art.path,
            "mime": art.mime,
            "version": art.version,
            "design_id": art.design_id,
            "edit_url": art.edit_url,
        }
        for art in artifacts
    ]
    (art_dir / "manifest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _load_record(dp: str, store: RecordStore) -> PropertyRecord:
    record = store.get(dp)
    if record is None:
        raise KeyError(f"No record for DP {dp}")
    return record


def _read_public_path(record: PropertyRecord, path: str):
    """Read the current value at a dotted ``public_view()`` path (the effective,
    override-aware value), or ``None`` if any segment is absent. Used to capture
    the before-value for the audit trail."""
    node = record.public_view()
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _prepare(record: PropertyRecord, output_root: str, client=None) -> Tuple[dict, dict, List[str]]:
    """Resolve the public view, the copy and the photo set for a render pass."""
    public = record.public_view()
    copy = _resolve_copy(record, client=client)
    photos = _resolve_photos(record, output_root)
    return public, copy, photos


# --- public API ----------------------------------------------------------

def render_all(
    dp: str,
    store: RecordStore,
    backend: Optional[str] = None,
    output_root: str = ".",
    client=None,
) -> List[Artifact]:
    """Render every supported format for DP ``dp`` through the resolved backend.

    Resolves the backend (arg -> ``ENGINE_RENDERER`` env -> default ``html``),
    checks it is available (raising a clear ``RuntimeError`` with the reason if
    not), loads the record, resolves copy once (human edits on
    ``record.marketing`` overlaid on generated copy), renders each format the
    backend supports, logs a manifest, and returns the artifacts.
    """
    record = _load_record(dp, store)

    be = get_backend(backend)
    ok, reason = be.available()
    if not ok:
        raise RuntimeError(f"Render backend {be.name!r} is unavailable: {reason}")

    public, copy, photos = _prepare(record, output_root, client=client)

    artifacts: List[Artifact] = []
    for fmt in FORMATS:
        if not be.supports(fmt):
            continue
        request = RenderRequest(
            dp=dp,
            fmt=fmt,
            public_record=public,
            photos=photos,
            copy=copy,
            output_root=str(output_root),
        )
        artifacts.append(be.render(request))

    _write_manifest(output_root, dp, artifacts)
    return artifacts


def render_one(
    dp: str,
    store: RecordStore,
    fmt: str,
    backend: Optional[str] = None,
    output_root: str = ".",
    client=None,
) -> Artifact:
    """Render a single format for DP ``dp`` through the resolved backend."""
    if fmt not in FORMATS:
        raise ValueError(
            f"Unknown format {fmt!r}. Known formats: {', '.join(FORMATS)}."
        )

    record = _load_record(dp, store)

    be = get_backend(backend)
    ok, reason = be.available()
    if not ok:
        raise RuntimeError(f"Render backend {be.name!r} is unavailable: {reason}")
    if not be.supports(fmt):
        raise ValueError(f"Backend {be.name!r} cannot render {fmt!r}.")

    public, copy, photos = _prepare(record, output_root, client=client)
    request = RenderRequest(
        dp=dp,
        fmt=fmt,
        public_record=public,
        photos=photos,
        copy=copy,
        output_root=str(output_root),
    )
    artifact = be.render(request)
    _write_manifest(output_root, dp, [artifact])
    return artifact


def set_price(
    dp: str,
    store: RecordStore,
    amount: Union[int, float, str],
    backend: Optional[str] = None,
    output_root: str = ".",
    client=None,
) -> PriceChange:
    """Record a price change and re-render every artifact.

    Writes the new ``price_display`` to ``record.marketing``, emits the diff on
    the audit trail, and re-renders. When the record is ``live`` the lifecycle
    moves ``live -> updated`` (the price-drop re-engagement event, SPEC M6); for
    any other state the price is recorded and re-rendered without an illegal
    transition. The re-render preserves any human copy edits (see
    ``_resolve_copy``), so only the price line changes.
    """
    record = _load_record(dp, store)

    old = record.marketing.price_display if record.marketing is not None else None
    new_display = _format_price(amount)

    if record.marketing is None:
        record.marketing = Marketing()
    record.marketing.price_display = new_display
    store.upsert(record)

    note = f"price change {old or 'unset'} -> {new_display}"
    if store.get_state(dp) == "live":
        store.transition(dp, "updated", note=note)
    else:
        # Not live: record the diff on the audit trail without an illegal move.
        store.record_signoff(dp, gate="price", user="system", note=note)

    artifacts = render_all(
        dp, store, backend=backend, output_root=output_root, client=client
    )
    return PriceChange(
        dp=dp,
        old=old,
        new=new_display,
        state=store.get_state(dp),
        artifacts=artifacts,
    )


def apply_edits(
    dp: str,
    store: RecordStore,
    changes: dict,
    user: str,
    backend: Optional[str] = None,
    output_root: str = ".",
    client=None,
) -> EditChange:
    """Apply human edits to a record's public fields, then re-render once.

    ``changes`` maps a dotted public-view path (e.g. ``identity.street_address``,
    ``sale_process.method``) to its new value. Routing keeps one home per field:
    ``marketing.price_display`` is formatted and written to its own field (the
    single price path), ``marketing.headline`` rides the copy overlay, and every
    other public fact is written to ``human_overrides`` so the sourced value stays
    pristine and survives a re-extraction (SPEC hard rule 3). A POPIA-protected
    path is refused. Each field is logged to the audit trail (old -> new); the
    record is upserted once and all artifacts are re-rendered once, so a
    multi-field save costs a single render. The lifecycle transition that a live
    edit needs (``live -> updated``) is owned by the reopen step, not here.
    """
    record = _load_record(dp, store)
    # Validate every field up front, so a POPIA-protected key raises before any
    # field is mutated or any audit row written (no partial edit / stray audit).
    for path in changes:
        if path not in (_PRICE_PATH, _HEADLINE_PATH) and not override_key_allowed(path):
            raise ValueError(f"Field {path!r} cannot be edited: it is POPIA-protected.")

    applied: dict = {}
    for path, value in changes.items():
        old = _read_public_path(record, path)
        if path == _PRICE_PATH:
            new_value = _format_price(value)
            if record.marketing is None:
                record.marketing = Marketing()
            record.marketing.price_display = new_value
            gate = "price"
        elif path == _HEADLINE_PATH:
            new_value = value
            if record.marketing is None:
                record.marketing = Marketing()
            record.marketing.headline = new_value
            gate = "edit"
        else:
            new_value = value
            if record.human_overrides is None:
                record.human_overrides = {}
            record.human_overrides[path] = new_value
            gate = "edit"
        applied[path] = new_value
        store.record_signoff(
            dp, gate=gate, user=user, note=f"{path}: {old} -> {new_value}"
        )

    store.upsert(record)
    artifacts = render_all(
        dp, store, backend=backend, output_root=output_root, client=client
    )
    return EditChange(
        dp=dp, changes=applied, state=store.get_state(dp), artifacts=artifacts
    )
