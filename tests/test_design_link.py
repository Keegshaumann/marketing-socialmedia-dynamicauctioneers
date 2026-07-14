"""Canva design-link capture (design_id + edit_url on the Artifact).

Offline: the Canva network calls are monkeypatched. Covers ``_design_url`` shape
handling across API versions, propagation onto the rendered ``Artifact``, and
manifest persistence (html artifacts carry null links).
"""

from __future__ import annotations

import json

from engine.render.base import Artifact, RenderRequest
from engine.render.canva_backend import CanvaBackend
from engine.render.service import _write_manifest
from engine.schema import PropertyRecord


def test_design_url_prefers_edit_then_view_then_top_then_constructs():
    b = CanvaBackend()
    assert b._design_url({"id": "D1", "urls": {"edit_url": "https://e", "view_url": "https://v"}}) == "https://e"
    assert b._design_url({"id": "D1", "urls": {"view_url": "https://v"}}) == "https://v"
    assert b._design_url({"id": "D1", "url": "https://top"}) == "https://top"
    assert b._design_url({"id": "D9"}) == "https://www.canva.com/design/D9/edit"
    assert b._design_url({}) is None


def test_render_sets_design_id_and_edit_url(monkeypatch, tmp_path, golden_record_path):
    rec = PropertyRecord.model_validate_json(golden_record_path.read_text(encoding="utf-8"))
    b = CanvaBackend()
    monkeypatch.setattr(b, "available", lambda: (True, "ok"))
    monkeypatch.setattr(b, "_load_template_map", lambda: {"demo_ad": "TPL1"})
    monkeypatch.setattr(b, "_access_token", lambda: "tok")
    monkeypatch.setattr(b, "_get_dataset", lambda t, tpl: {"headline": "text", "photo1": "image"})
    monkeypatch.setattr(
        b, "_run_autofill",
        lambda t, tpl, data: {"id": "DAFxyz", "urls": {"edit_url": "https://www.canva.com/design/DAFxyz/edit"}},
    )
    monkeypatch.setattr(b, "_export_design", lambda t, did, fmt: ("https://export", "pdf", "application/pdf"))

    def _fake_download(url, request, ext):
        out = tmp_path / f"DP{request.dp}" / "artifacts"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{request.fmt}.{ext}"
        path.write_bytes(b"%PDF-1.4 fake")
        return path

    monkeypatch.setattr(b, "_download", _fake_download)

    req = RenderRequest(dp=rec.dp, fmt="demo_ad", public_record=rec.public_view(), photos=[], output_root=str(tmp_path))
    art = b.render(req)
    assert art.backend == "canva"
    assert art.design_id == "DAFxyz"
    assert art.edit_url == "https://www.canva.com/design/DAFxyz/edit"


def test_manifest_persists_canva_design_link(tmp_path):
    art = Artifact(
        dp="3060", fmt="demo_ad", backend="canva", path=str(tmp_path / "x.pdf"),
        mime="application/pdf", version=1, design_id="DAF1", edit_url="https://e",
    )
    _write_manifest(str(tmp_path), "3060", [art])
    man = json.loads((tmp_path / "DP3060" / "artifacts" / "manifest.json").read_text(encoding="utf-8"))
    assert man[0]["design_id"] == "DAF1"
    assert man[0]["edit_url"] == "https://e"


def test_html_artifact_has_null_design_link(tmp_path):
    art = Artifact(
        dp="3060", fmt="portal_listing", backend="html",
        path=str(tmp_path / "p.md"), mime="text/markdown",
    )
    _write_manifest(str(tmp_path), "3060", [art])
    man = json.loads((tmp_path / "DP3060" / "artifacts" / "manifest.json").read_text(encoding="utf-8"))
    assert man[0]["design_id"] is None and man[0]["edit_url"] is None
