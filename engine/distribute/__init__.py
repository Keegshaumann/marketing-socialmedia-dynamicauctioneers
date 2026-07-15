"""Distribution package (M6, SPEC 5.6 + section 12).

Distribution has two arms. API channels post automatically once their
credentials exist: GoHighLevel's Social Planner (``ghl``, D11) for the social
pages. Every other channel gets a ready-to-post pack (``packs``) that a human
works down. The channel routing
matrix (``routing``) decides which channels a property goes to, and the
per-channel status log (``packs.log_posted`` / ``list_status``) records what
actually went out per DP per channel per version (the Proof of Marketing trail).

This is the package's public surface. Every external call in the submodules is
config-gated: without a token, a line or a feed the modules never call out,
never hang and never raise; they park a pack or a ready-to-send payload and
report the missing credential instead. The routing matrix and the manual pack
run fully offline and are what the tests exercise.

Design rules baked in here:
- Renderers and packs are built from ``record.public_view()`` only, so no
  outbound artifact can carry owner or occupant PII (SPEC 4.4).
- SA English, no em or en dashes, no emojis (matches ``engine.schema`` style).
"""

from __future__ import annotations

from engine.distribute.ghl import (
    DELETE_CAVEAT,
    GHL_SOCIAL_CHANNELS,
    PlannerResult,
    build_planner_checklist,
    build_planner_request,
    post_to_planner,
)
from engine.distribute.packs import (
    build_manual_pack,
    list_status,
    log_posted,
    price_drop_burst,
)
from engine.distribute.routing import (
    JAMESEDITION_THRESHOLD_ZAR,
    channel_matrix,
    is_commercial,
    property_value,
)

__all__ = [
    # routing (SPEC 5.6)
    "channel_matrix",
    "property_value",
    "is_commercial",
    "JAMESEDITION_THRESHOLD_ZAR",
    # GHL Social Planner scaffold (D11)
    "post_to_planner",
    "build_planner_request",
    "build_planner_checklist",
    "PlannerResult",
    "GHL_SOCIAL_CHANNELS",
    "DELETE_CAVEAT",
    # manual packs + Proof of Marketing log
    "build_manual_pack",
    "log_posted",
    "list_status",
    "price_drop_burst",
]
