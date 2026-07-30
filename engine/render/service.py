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
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple, Union

from engine.render import DEFAULT_BACKEND, get_backend
from engine.render.base import FORMATS, Artifact, RenderRequest
from engine.render.copy import generate_copy
from engine.schema import Marketing, PropertyRecord, override_key_allowed
from engine.store import RecordStore

# The fields that keep dedicated homes on ``record.marketing`` (the copy
# channel) rather than riding the ``human_overrides`` facts channel: price
# carries its own audit/format handling, headline has a copy overlay the LLM
# would otherwise regenerate, and the template-set pick (D33) is a marketing
# decision, not a sourced fact. Everything else is a ``human_overrides`` key.
_PRICE_PATH = "marketing.price_display"
_HEADLINE_PATH = "marketing.headline"
_TEMPLATE_SET_PATH = "marketing.template_set"
_MARKETING_PATHS = (_PRICE_PATH, _HEADLINE_PATH, _TEMPLATE_SET_PATH)


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


def _resolve_template_set(record: PropertyRecord) -> Optional[str]:
    """The record's design/template-set pick (D33), or ``None`` for the default.

    Set by the marketing team on gate 2 and stored on ``marketing.template_set``.
    Backends resolve ``None`` (and any stale name) to their default set; the
    html backend ignores it entirely.
    """
    marketing = record.marketing
    return marketing.template_set if marketing is not None else None


# --- backend resolution (single backend, or per-format "mixed") -----------

# Backends preferred over the html default, in order, when they are configured
# and support a given format (the "mixed" render mode). html is the universal
# fallback: it renders every format and is always available (D14 / D16 / D18).
_PREMIUM_BACKENDS: Tuple[str, ...] = ("canva",)
_MIXED = "mixed"


def _effective_backend_name(backend: Optional[str]) -> str:
    return (backend or os.getenv("ENGINE_RENDERER") or DEFAULT_BACKEND).strip()


def _format_backends(backend: Optional[str]) -> Callable[[str], List]:
    """Return a resolver ``fmt -> [backend, ...]`` (in try order) for the pass.

    An explicit backend name renders only the formats it supports through that
    one backend (single-element list, or empty to skip) -- unchanged behaviour,
    and a render failure propagates. ``"mixed"`` returns, per format, the
    available premium backends that support it followed by the always-available
    html fallback, so the loop uses e.g. Canva for the branded one-pager and
    html for the channel copies in a single pass, and can fall back to html if a
    premium backend errors (e.g. Canva quota) rather than lose the whole set.
    """
    name = _effective_backend_name(backend)
    if name != _MIXED:
        be = get_backend(name)
        ok, reason = be.available()
        if not ok:
            raise RuntimeError(f"Render backend {be.name!r} is unavailable: {reason}")
        return lambda fmt: [be] if be.supports(fmt) else []

    html = get_backend("html")
    premium: List = []
    for pname in _PREMIUM_BACKENDS:
        try:
            candidate = get_backend(pname)
        except Exception:
            continue
        available, _reason = candidate.available()
        if available:
            premium.append(candidate)

    def resolve(fmt: str) -> List:
        chain = [be for be in premium if be.supports(fmt)]
        if html.supports(fmt):
            chain.append(html)
        return chain

    return resolve


def _render_format(candidates: List, request: RenderRequest) -> Optional[Artifact]:
    """Render one format through the first candidate backend that succeeds.

    Returns ``None`` when no candidate supports the format (skip). In mixed mode
    a premium backend that raises (e.g. Canva quota) falls back to the next
    candidate; the last candidate's failure re-raises, so a single-backend pass
    still surfaces its error unchanged.
    """
    if not candidates:
        return None
    last_exc: Optional[Exception] = None
    for i, be in enumerate(candidates):
        try:
            return be.render(request)
        except Exception as exc:
            last_exc = exc
            if i < len(candidates) - 1:
                nxt = candidates[i + 1].name
                print(
                    f"[render] {be.name} failed on {request.fmt} for DP{request.dp} "
                    f"({type(exc).__name__}); falling back to {nxt}.",
                    file=sys.stderr,
                )
    raise last_exc  # type: ignore[misc]


# --- public API ----------------------------------------------------------

def render_all(
    dp: str,
    store: RecordStore,
    backend: Optional[str] = None,
    output_root: str = ".",
    client=None,
    formats: Optional[List[str]] = None,
) -> List[Artifact]:
    """Render the supported formats for DP ``dp``, one manifest per pass.

    Resolves the backend (arg -> ``ENGINE_RENDERER`` env -> default ``html``);
    ``"mixed"`` selects the best backend per format (premium where configured,
    html otherwise). Loads the record, resolves copy + photos once, renders each
    format through its resolved backend(s), logs a manifest, returns the set.

    ``formats`` renders only that subset (validated against ``FORMATS``); default
    (None) renders the full set. The manifest is written for exactly the formats
    rendered, so an ad-only first pass (D39) lists only the ad, and the later
    full pass rewrites the manifest with everything.
    """
    targets = list(formats) if formats is not None else FORMATS
    unknown = [f for f in targets if f not in FORMATS]
    if unknown:
        raise ValueError(f"Unknown format(s): {', '.join(unknown)}. Known: {', '.join(FORMATS)}.")

    record = _load_record(dp, store)
    resolve = _format_backends(backend)
    public, copy, photos = _prepare(record, output_root, client=client)
    template_set = _resolve_template_set(record)

    artifacts: List[Artifact] = []
    for fmt in targets:
        request = RenderRequest(
            dp=dp,
            fmt=fmt,
            public_record=public,
            photos=photos,
            copy=copy,
            output_root=str(output_root),
            template_set=template_set,
        )
        artifact = _render_format(resolve(fmt), request)
        if artifact is not None:
            artifacts.append(artifact)

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
    """Render a single format for DP ``dp`` through the resolved backend(s).

    Honours ``"mixed"`` like ``render_all``: the format is rendered by the best
    available backend that supports it, falling back to html on a premium error.
    """
    if fmt not in FORMATS:
        raise ValueError(
            f"Unknown format {fmt!r}. Known formats: {', '.join(FORMATS)}."
        )

    record = _load_record(dp, store)
    candidates = _format_backends(backend)(fmt)
    if not candidates:
        raise ValueError(f"No configured render backend can render {fmt!r}.")

    public, copy, photos = _prepare(record, output_root, client=client)
    request = RenderRequest(
        dp=dp,
        fmt=fmt,
        public_record=public,
        photos=photos,
        copy=copy,
        output_root=str(output_root),
        template_set=_resolve_template_set(record),
    )
    artifact = _render_format(candidates, request)
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
    formats: Optional[List[str]] = None,
) -> EditChange:
    """Apply human edits to a record's public fields, then re-render once.

    ``changes`` maps a dotted public-view path (e.g. ``identity.street_address``,
    ``sale_process.method``) to its new value. Routing keeps one home per field:
    ``marketing.price_display`` is formatted and written to its own field (the
    single price path), ``marketing.headline`` rides the copy overlay,
    ``marketing.template_set`` records the design pick (D33), and every
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
        if path not in _MARKETING_PATHS and not override_key_allowed(path):
            raise ValueError(f"Field {path!r} cannot be edited: it is POPIA-protected.")

    applied: dict = {}
    for path, value in changes.items():
        old = _read_public_path(record, path)
        # Normalise the incoming value the same way it will be stored, so the
        # no-op check below compares like with like.
        if path == _PRICE_PATH:
            new_value = _format_price(value)
            gate = "price"
        elif path == _TEMPLATE_SET_PATH:
            # The design pick (D33): blank clears back to the default set.
            new_value = str(value).strip() or None
            gate = "edit"
        else:
            new_value = value
            gate = "edit"
        # Skip a no-op edit: the gate-2 form resubmits every prefilled field, so
        # without this each Save would re-pin unchanged sourced values into
        # human_overrides and write a "X -> X" audit row for each (D44 review).
        if new_value == old:
            continue
        if path == _PRICE_PATH:
            if record.marketing is None:
                record.marketing = Marketing()
            record.marketing.price_display = new_value
        elif path == _HEADLINE_PATH:
            if record.marketing is None:
                record.marketing = Marketing()
            record.marketing.headline = new_value
        elif path == _TEMPLATE_SET_PATH:
            if record.marketing is None:
                record.marketing = Marketing()
            record.marketing.template_set = new_value
        else:
            if record.human_overrides is None:
                record.human_overrides = {}
            record.human_overrides[path] = new_value
        applied[path] = new_value
        store.record_signoff(
            dp, gate=gate, user=user, note=f"{path}: {old} -> {new_value}"
        )

    store.upsert(record)
    try:
        artifacts = render_all(
            dp, store, backend=backend, output_root=output_root, client=client, formats=formats
        )
    except ValueError as exc:
        # By this point the edit IS saved; a render-time ValueError (e.g. no
        # brand template mapped) must not surface as this function's
        # "refused before anything was saved" ValueError contract, which the
        # webapp reports as "Edit refused".
        raise RuntimeError(f"edit saved, but the re-render failed: {exc}") from exc
    return EditChange(
        dp=dp, changes=applied, state=store.get_state(dp), artifacts=artifacts
    )


def apply_photos(
    dp: str,
    store: RecordStore,
    hero: Optional[str],
    gallery: List[str],
    user: str,
    backend: Optional[str] = None,
    output_root: str = ".",
    client=None,
) -> EditChange:
    """Set the record's hero + gallery photo picks (canonical) and re-render.

    Photos are written to the canonical ``marketing.hero_photo``/``gallery`` fields
    (not ``human_overrides``) so both the html backend and Canva (which uploads
    ``request.photos``) pick them up. Paths are stored relative to the DP folder
    (``photos/x.png``). The change is logged on the audit trail and every artifact
    re-rendered once. The ``live -> updated`` reopen a live change needs is owned
    by the caller, as for ``apply_edits``.
    """
    record = _load_record(dp, store)
    if record.marketing is None:
        record.marketing = Marketing()
    record.marketing.hero_photo = hero
    record.marketing.gallery = list(gallery)
    store.upsert(record)
    total = (1 if hero else 0) + len(gallery)
    store.record_signoff(
        dp, gate="edit", user=user, note=f"photos: {total} (hero={hero or 'none'})"
    )
    artifacts = render_all(
        dp, store, backend=backend, output_root=output_root, client=client
    )
    return EditChange(
        dp=dp,
        changes={"marketing.hero_photo": hero, "marketing.gallery": list(gallery)},
        state=store.get_state(dp),
        artifacts=artifacts,
    )
