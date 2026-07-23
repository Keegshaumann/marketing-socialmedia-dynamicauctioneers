"""Per-format 'mixed' render mode (D18): Canva one-pager + html channel copies.

Offline: the Canva backend is monkeypatched to be 'available' and to support
only ``demo_ad``, so no live API is touched. Proves that one render pass uses
the premium backend where it fits and the always-available html for the rest,
and that a premium render error falls back to html instead of losing the set.
"""

from __future__ import annotations

import pathlib

from engine.render import canva_backend as cb
from engine.render.base import FORMATS, Artifact
from engine.render.service import render_all
from engine.schema import PropertyRecord
from engine.store import RecordStore


def _fake_canva(fmt_ok: str = "demo_ad"):
    """Return (supports, render) patches for a Canva backend that only does fmt_ok."""
    def supports(self, fmt):
        return fmt == fmt_ok

    def render(self, request):
        out = pathlib.Path(request.output_root) / f"DP{request.dp}" / "artifacts"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{request.fmt}.pdf"
        path.write_bytes(b"%PDF-1.4 canva")
        return Artifact(
            dp=request.dp, fmt=request.fmt, backend="canva", path=str(path),
            mime="application/pdf", design_id="DAF1", edit_url="https://www.canva.com/design/DAF1/edit",
        )

    return supports, render


def _seed(dp, golden_record_path):
    rec = PropertyRecord.model_validate_json(golden_record_path.read_text(encoding="utf-8"))
    rec.dp = dp
    store = RecordStore(db_path=":memory:")
    store.upsert(rec, state="extracted")
    return store


def test_mixed_uses_canva_for_demo_ad_and_html_for_the_rest(monkeypatch, tmp_path, golden_record_path):
    supports, render = _fake_canva()
    monkeypatch.setattr(cb.CanvaBackend, "available", lambda self: (True, "ok"))
    monkeypatch.setattr(cb.CanvaBackend, "supports", supports)
    monkeypatch.setattr(cb.CanvaBackend, "render", render)

    store = _seed("9500", golden_record_path)
    try:
        arts = render_all("9500", store, backend="mixed", output_root=str(tmp_path))
    finally:
        store.close()

    by_fmt = {a.fmt: a for a in arts}
    assert by_fmt["demo_ad"].backend == "canva"
    assert by_fmt["demo_ad"].edit_url == "https://www.canva.com/design/DAF1/edit"
    assert all(a.backend == "html" for f, a in by_fmt.items() if f != "demo_ad")
    # A complete set in one pass: the canva one-pager + every html format.
    assert len(arts) == len(FORMATS)


def test_mixed_falls_back_to_html_when_canva_errors(monkeypatch, tmp_path, golden_record_path):
    supports, _ = _fake_canva()
    monkeypatch.setattr(cb.CanvaBackend, "available", lambda self: (True, "ok"))
    monkeypatch.setattr(cb.CanvaBackend, "supports", supports)

    def _boom(self, request):
        raise RuntimeError("quota_exceeded")

    monkeypatch.setattr(cb.CanvaBackend, "render", _boom)

    store = _seed("9501", golden_record_path)
    try:
        arts = render_all("9501", store, backend="mixed", output_root=str(tmp_path))
    finally:
        store.close()

    by_fmt = {a.fmt: a for a in arts}
    # demo_ad's Canva render failed, so it fell back to html; the set is complete.
    assert by_fmt["demo_ad"].backend == "html"
    assert len(arts) == len(FORMATS)


def test_html_backend_unchanged_when_not_mixed(monkeypatch, tmp_path, golden_record_path):
    # An explicit backend still renders every format through that one backend.
    store = _seed("9502", golden_record_path)
    try:
        arts = render_all("9502", store, backend="html", output_root=str(tmp_path))
    finally:
        store.close()
    assert {a.backend for a in arts} == {"html"}
    assert len(arts) == len(FORMATS)


def test_record_template_set_reaches_the_backend(monkeypatch, tmp_path, golden_record_path):
    """The marketing team's design pick (D33) rides every RenderRequest."""
    seen: list = []
    supports, render = _fake_canva()

    def capture(self, request):
        seen.append(request.template_set)
        return render(self, request)

    monkeypatch.setattr(cb.CanvaBackend, "available", lambda self: (True, "ok"))
    monkeypatch.setattr(cb.CanvaBackend, "supports", supports)
    monkeypatch.setattr(cb.CanvaBackend, "render", capture)

    store = _seed("9503", golden_record_path)
    try:
        rec = store.get("9503")
        rec.marketing.template_set = "Modern dark"
        store.upsert(rec)
        render_all("9503", store, backend="mixed", output_root=str(tmp_path))
    finally:
        store.close()
    assert seen == ["Modern dark"]


def test_apply_edits_stores_the_template_pick(tmp_path, golden_record_path):
    from engine.render.service import apply_edits

    store = _seed("9504", golden_record_path)
    try:
        change = apply_edits(
            "9504",
            store,
            {"marketing.template_set": "Modern dark"},
            user="nikki@dynamicauctioneers.co.za",
            backend="html",
            output_root=str(tmp_path),
        )
        assert change.changes == {"marketing.template_set": "Modern dark"}
        assert store.get("9504").marketing.template_set == "Modern dark"

        # A blank pick clears back to the default set.
        apply_edits(
            "9504",
            store,
            {"marketing.template_set": ""},
            user="nikki@dynamicauctioneers.co.za",
            backend="html",
            output_root=str(tmp_path),
        )
        assert store.get("9504").marketing.template_set is None
    finally:
        store.close()
