"""Content-addressed cache for the paid Claude calls (D59).

Three things in this system cost Anthropic credits: extraction (six calls per
property, both source PDFs on every call), the verification market research
(one call that fans out to ~11 web searches) and copy generation (one call,
already cached on the record). The first two were re-paid in full every time a
property was re-intaked - which is exactly what testing does, and what a
marketer does after a mistake: delete the property, drop the same PDFs again.

Both are pure functions of their inputs: the same PDFs produce the same record,
the same facts produce the same research. So key the result on a hash of those
inputs and reuse it. A genuinely new property still pays once; a repeat costs
nothing.

The key deliberately includes a caller-supplied ``version`` (the model id plus a
fingerprint of the prompt), so changing the prompt or the model invalidates
every entry rather than silently serving output the current code would not
produce.

Disable with ``ENGINE_AI_CACHE=0``. Failures are always non-fatal: a cache miss
or an unreadable entry just means the call runs, which is the pre-cache
behaviour.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Optional

_DIR_NAME = ".ai-cache"


def enabled() -> bool:
    """Whether caching is on. ``ENGINE_AI_CACHE=0`` turns it off."""
    return (os.getenv("ENGINE_AI_CACHE") or "1").strip() != "0"


def cache_dir(output_root: Optional[str] = None) -> Path:
    """Where entries live: ``<output_root>/.ai-cache``.

    Deliberately OUTSIDE the per-property ``DP<dp>/`` folder, so deleting a
    property from the board (which removes that folder) does not throw away the
    cached extraction - deleting and re-intaking is the exact case this exists
    to make free.
    """
    return Path(output_root or ".") / _DIR_NAME


def file_digest(paths: Iterable) -> str:
    """Hash the CONTENT of the given files, order-independent.

    Content, not path or mtime: the intake flow copies uploads into a new
    per-property folder, so the same document arrives under a different path
    every time and a path-based key would never hit.
    """
    digests = []
    for p in paths:
        try:
            digests.append(hashlib.sha256(Path(p).read_bytes()).hexdigest())
        except OSError:
            digests.append("missing:" + str(p))
    return hashlib.sha256("|".join(sorted(digests)).encode()).hexdigest()


def key(kind: str, version: str, *parts: str) -> str:
    """Build a cache key from the call kind, a prompt/model version and inputs."""
    blob = "\x1f".join([kind, version, *parts])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:40]


def load(kind: str, cache_key: str, output_root: Optional[str] = None) -> Optional[Any]:
    """Return the cached JSON value, or ``None`` on miss/disabled/unreadable."""
    if not enabled():
        return None
    path = cache_dir(output_root) / kind / f"{cache_key}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(kind: str, cache_key: str, value: Any, output_root: Optional[str] = None) -> None:
    """Store a JSON-serialisable value. Never raises: caching is best effort."""
    if not enabled():
        return
    path = cache_dir(output_root) / kind / f"{cache_key}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write via a temp file so a crash mid-write cannot leave a truncated
        # entry that would later be served as a valid result.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except (OSError, TypeError, ValueError):
        pass
