"""Render backend registry (M5, D14).

Lazy-import registry: `get_backend()` resolves arg -> ENGINE_RENDERER env ->
default "html". Backends are imported on demand so a missing/optional backend
(Canva) never breaks other engine commands at import time.

Removing the Canva scaffold (D14): delete `canva_backend.py`, the one
`"canva": ...` line in `_REGISTRY` below, and `tests/test_canva.py`. Nothing
else imports it, so the suite stays green.
"""

from __future__ import annotations

import os
from typing import Dict, Tuple

from engine.render.base import FORMATS, Artifact, RenderBackend, RenderRequest

# name -> (module path, class name). Lazy so optional backends don't import
# their dependencies until actually selected.
_REGISTRY: Dict[str, Tuple[str, str]] = {
    "html": ("engine.render.html_backend", "HtmlBackend"),
    "canva": ("engine.render.canva_backend", "CanvaBackend"),  # D14 scaffold — see module docstring
}

DEFAULT_BACKEND = "html"

__all__ = [
    "FORMATS",
    "Artifact",
    "RenderBackend",
    "RenderRequest",
    "get_backend",
    "list_backends",
]


def get_backend(name: str | None = None) -> RenderBackend:
    """Resolve a SINGLE backend: explicit arg -> ENGINE_RENDERER env -> "html".

    ``"mixed"`` is a per-format render MODE (the render service routes each format
    to the best backend), not a single backend, so a caller asking for "a
    backend" while mixed is configured gets the universal default instead of a
    crash.
    """
    import importlib

    resolved = name or os.getenv("ENGINE_RENDERER") or DEFAULT_BACKEND
    if resolved == "mixed":
        resolved = DEFAULT_BACKEND
    if resolved not in _REGISTRY:
        raise ValueError(
            f"Unknown render backend {resolved!r}. Known: {', '.join(sorted(_REGISTRY))}."
        )
    module_path, class_name = _REGISTRY[resolved]
    module = importlib.import_module(module_path)
    backend_cls = getattr(module, class_name)
    return backend_cls()


def list_backends() -> Dict[str, Tuple[bool, str]]:
    """Return ``{name: (available, reason)}`` for every registered backend."""
    out: Dict[str, Tuple[bool, str]] = {}
    for name in _REGISTRY:
        try:
            out[name] = get_backend(name).available()
        except Exception as exc:  # a backend that cannot even import is unavailable
            out[name] = (False, f"import failed: {exc}")
    return out
