"""Rendering backend contract (M5, D14).

Every marketing artifact is rendered from a verified record through a swappable
backend. The default backend (``html``) renders the M5 brand-token templates;
the ``canva`` backend is a config-gated, one-move-removable scaffold (D14) that
only lights up if Dynamic Auctioneers ever moves to Canva Enterprise (D12).

Backends receive **only** the ``public_view`` projection of a record — never the
raw record — so owner PII cannot reach any artifact (SPEC 4.4). The poison-marker
PII test applies to every backend, Canva included (fields sent to Canva's cloud
are client-facing by definition).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# The full artifact set: the four channel copies + demo ad (M5), plus the
# post-client-approval set from the real marketing workflow (SPEC 12).
FORMATS: List[str] = [
    "portal_listing",   # Property24-ready copy (.md)
    "facebook_post",    # FB post + boost notes (.md)
    "email_blast",      # subject A/B + body (.md)
    "demo_ad",          # branded one-pager, print-ready A4 + web (.html)
    # Two alternative designs of the same advert (fix list 2.1). The team picks
    # one to post; having three to choose from is the point. Rendered with the
    # full pack only, so the pre-approval ad-only render stays a single design.
    "demo_ad_2",
    "demo_ad_3",
    "info_pack",        # buyer-facing Property Report variant (.html)
    "webapp_icon",      # upcoming-auction tile/icon (.svg or .png)
    "saia_banner",      # SAIA alert banner (.html)
    "alert_mailer",     # alert-mailer HTML + audience list (.html)
    "auction_board",    # print-ready auction board (.html; PDF export later)
    # One board for a whole block sold under one instruction (fix list 6.4).
    # Rendered ONLY when the property has siblings sharing a scheme.
    "estate_board",
]


@dataclass
class RenderRequest:
    """Everything a backend needs to render one format for one property.

    ``public_record`` is ``PropertyRecord.public_view()`` — a plain dict with the
    POPIA internal layer already stripped. ``copy`` is the channel-aware copy dict
    (from ``engine.render.copy``) or ``None`` to let the backend use record fields.
    """

    dp: str
    fmt: str
    public_record: dict
    photos: List[str] = field(default_factory=list)
    copy: Optional[dict] = None
    output_root: str = "."
    # The named design/template set to render with (the marketing team's pick,
    # stored on ``marketing.template_set``). None or an unknown name means the
    # backend's default set; backends without template sets (html) ignore it.
    template_set: Optional[str] = None


@dataclass
class Artifact:
    """A rendered artifact on disk, logged per DP per format per version."""

    dp: str
    fmt: str
    backend: str
    path: str
    mime: str
    version: int = 1
    # Backend-side design identity + a link to open/edit it (Canva design id and
    # edit URL). None for backends that render locally (html). Lets a human open
    # the exact source design behind a rendered artifact.
    design_id: Optional[str] = None
    edit_url: Optional[str] = None


class RenderBackend:
    """Abstract rendering backend. Subclasses live in ``*_backend.py`` modules."""

    name: str = "base"

    # Whether the backend produces the artifact bytes on this machine (html) or
    # via a remote service (Canva). Backends that render remotely enforce their
    # PII contract on the outbound *payload* (public_view only), not on locally
    # scannable output, so the offline byte-level poison test skips them.
    renders_locally: bool = True

    def available(self) -> "tuple[bool, str]":
        """Return ``(ok, reason)``. A misconfigured backend reports why it is
        unavailable instead of raising, so other engine commands keep working."""
        raise NotImplementedError

    def supports(self, fmt: str) -> bool:
        """Whether this backend can render ``fmt``."""
        raise NotImplementedError

    def render(self, request: RenderRequest) -> Artifact:
        """Render one format and return the on-disk artifact."""
        raise NotImplementedError
