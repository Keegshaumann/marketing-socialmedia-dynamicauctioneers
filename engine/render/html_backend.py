"""Default rendering backend: HTML / Markdown / SVG from Jinja2 templates (M5).

``HtmlBackend`` renders every format in :data:`engine.render.base.FORMATS` from the
brand-token templates in ``engine/render/templates/``. It is the platform's default
renderer (SPEC M5, D14): no credentials, always available, deterministic, offline.

Design rules baked in here:
- Backends receive **only** ``public_record`` (``PropertyRecord.public_view()``),
  plus photo paths and an optional copy dict. The POPIA internal layer is already
  gone from ``public_record``, so no artifact this backend produces can leak owner
  or occupant PII (SPEC 4.4) — the poison-marker test relies on exactly this.
- Copy precedence: values on ``request.copy`` (generated or human-edited) override
  the deterministic defaults derived from record fields, so re-renders keep human
  edits (SPEC M5). With ``copy=None`` every artifact still renders from the record.
- Facts are only rendered when the record carries them; a missing field is omitted
  rather than invented (no hallucinated facts in a client-facing artifact, SPEC 8).
- SA English, no em or en dashes, no emojis in any rendered copy.

Text formats (portal_listing, facebook_post, email_blast) render to
``.md`` / ``.txt``; visual formats (demo_ad, info_pack, saia_banner, alert_mailer,
auction_board) to ``.html``; webapp_icon to an ``.svg`` tile. Artifacts are written
to ``<output_root>/DP<dp>/artifacts/<fmt>.<ext>``.
"""

from __future__ import annotations

import base64
import os
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from engine.render import ad_icons, pack_icons
from engine.render.base import FORMATS, Artifact, RenderBackend, RenderRequest


# Real Dynamic Auctioneers brand tokens (extracted Phase 0; see DESIGN-SYSTEM.md
# and the DP3060 letterhead). These are company-level facts, safe for any artifact.
BRAND: Dict[str, str] = {
    "name": "Dynamic Auctioneers",
    "phone": "086 155 2288",
    "email": "properties@dynamicauctioneers.co.za",
    # A second public mailbox shown alongside the first on the roomy document
    # surfaces (info pack, letterhead) so an enquirer can choose who to mail; the
    # tight ad contact bar keeps only the primary address.
    "email_admin": "properties.admin@dynamicauctioneers.co.za",
    "web": "dynamicauctioneers.co.za",
    "address": "187 Gouws Avenue, Raslouw AH, Centurion",
    "reg": (
        "Dynamic Solutions 1068 (Pty) Ltd T/A Dynamic Auctioneers"
        "  ·  Reg 2018/014769/07"
        "  ·  VAT 4050206442"
        "  ·  Registered with the PPRA"
        "  ·  Member: SAIA, National Auction Association"
    ),
}

# fmt -> (template file, extension, MIME type). Keys mirror base.FORMATS exactly.
_FORMAT_SPEC: Dict[str, Tuple[str, str, str]] = {
    "portal_listing": ("portal_listing.md.j2", "md", "text/markdown"),
    "facebook_post": ("facebook_post.md.j2", "md", "text/markdown"),
    "email_blast": ("email_blast.md.j2", "md", "text/markdown"),
    "demo_ad": ("ads/hero_overlay.html.j2", "html", "text/html"),
    # The template for these is chosen per record (see _resolve_ad_template):
    # the designs after the marketer's pick, so the three are always different.
    "demo_ad_2": ("ads/hero_overlay.html.j2", "html", "text/html"),
    "demo_ad_3": ("ads/hero_overlay.html.j2", "html", "text/html"),
    # Rendered to HTML first, then printed to PDF (see _PDF_FORMATS below): the
    # buyer receives a PDF, so the artifact this backend returns is the PDF.
    "info_pack": ("info_pack.html.j2", "html", "text/html"),
    "webapp_icon": ("webapp_icon.svg.j2", "svg", "image/svg+xml"),
    "saia_banner": ("saia_banner.html.j2", "html", "text/html"),
    "alert_mailer": ("alert_mailer.html.j2", "html", "text/html"),
    "auction_board": ("auction_board.html.j2", "html", "text/html"),
    "estate_board": ("estate_board.html.j2", "html", "text/html"),
}

# Formats delivered as a PDF. These are documents a buyer or client receives as
# an attachment, not web pages: the HTML is rendered first (and kept as the print
# source) and Chromium then prints it to A4. A host without Chromium keeps the
# HTML artifact, so the pack still renders everywhere.
#
# Every VISUAL artifact is a PDF: the team hands these to clients, attaches them
# to email and imports them into Canva, and an .html file is none of those things
# (the downloaded pack used to be a folder of web pages). The value is the root
# element to print at its own size - an advert is a 1080x1350 canvas, not an A4
# sheet - or "" for a document that sets its own @page rules.
#
# The copy formats (portal_listing, facebook_post, email_blast) stay MARKDOWN on
# purpose: they exist to be pasted into Property24, Facebook and the mailer, and
# text cannot be pasted out of a PDF without picking up its line breaks.
_PDF_FORMATS: Dict[str, str] = {
    "info_pack": "",            # multi-page A4 landscape, its own @page rules
    "demo_ad": ".ig",           # 1080x1350 social canvas
    "demo_ad_2": ".ig",
    "demo_ad_3": ".ig",
    "saia_banner": ".bn",
    # The mailer is an email: nested tables with a 640px wrapper, no canvas.
    "alert_mailer": ".wrap",
    "auction_board": ".board",
    "estate_board": ".board",
}


def _pdf_export_enabled() -> bool:
    """Whether to print the PDF formats. On by default; ``ENGINE_PDF_EXPORT=0``
    turns it off.

    Each PDF costs a Chromium launch (~2-3s). That is fine once per approval in
    production, but the test suite renders the pack in dozens of tests and went
    from 33s to nearly nine minutes, so the suite disables it and a couple of
    dedicated tests turn it back on to cover the real export.
    """
    return (os.getenv("ENGINE_PDF_EXPORT") or "1").strip() != "0"

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _should_autoescape(template_name: Optional[str]) -> bool:
    """Escape markup templates (HTML, SVG); leave Markdown/text unescaped."""
    if not template_name:
        return False
    return template_name.endswith((".html.j2", ".svg.j2"))


_ASSET_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml", ".webp": "image/webp",
}


@lru_cache(maxsize=64)
def _asset_data_uri(relative_path: str) -> str:
    """Read a template-relative asset (e.g. a logo) and return a base64 data URI.

    Templates embed assets this way so the rendered ad is self-contained and
    survives file:// rasterising (no external file resolution). Cached; returns
    "" if the asset is missing so a template never crashes on a typo'd path.
    """
    path = _TEMPLATE_DIR / relative_path
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    mime = _ASSET_MIME.get(path.suffix.lower(), "application/octet-stream")
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def configure_template_env(env) -> None:
    """Register every global and filter the render templates rely on (D97).

    TWO Jinja environments load these templates: this backend, and the
    gate-2 thumbnail renderer (``ad_thumbs``), which builds its own. They
    were configured separately, so D95's new ``ad_glyph``/``ad_icon_for``
    globals reached the backend and not the thumbnails - and three of the
    four design previews turned into broken images the moment they were
    next rendered. One function now configures both.
    """
        # Let templates embed a bundled asset (logo, etc.) as a data URI, so the
    # rendered ad is self-contained for rasterising (D41).
    env.globals["asset_uri"] = _asset_data_uri
    # The info pack prints a black glyph beside every feature line and splits
    # a line into label + qualifier (playbook 3). Both are pure functions of
    # record wording, so the template calls them directly rather than the
    # view model carrying a second, parallel copy of every feature list.
    env.globals["glyph"] = lambda name, size="9mm": Markup(pack_icons.svg(name, size))
    # The pack's glyph chooser, honouring the marketer's picks (D94). Only a
    # name the pack actually HAS is used: the advert's set has four glyphs
    # the pack does not (size, feature, bar, sofa), and an unknown name must
    # fall back to the rules rather than draw nothing.
    def _icon_for(text, picks=None):
        pick = (picks or {}).get(text)
        if pick and pick in pack_icons.ICONS:
            return pick
        return pack_icons.icon_for(text)

    env.globals["icon_for"] = _icon_for
    # The advert's own stroked set, shared with the gate-2 picker (D95).
    def _ad_glyph(name, style=None, custom=None, output_root=None, dp=None):
        """One advert glyph: a built-in drawing, or an uploaded file (D96).

        An uploaded glyph is embedded as a data URI inside an ``<img>``, so
        the artifact stays self-contained for printing AND a script inside
        an uploaded SVG cannot run (it does not, inside an <img>).
        """
        if isinstance(name, str) and name.startswith("custom:"):
            label = name[7:]
            filename = (custom or {}).get(label)
            if filename:
                path = Path(output_root or ".") / f"DP{dp}" / "icons" / Path(filename).name
                uri = _file_data_uri(path)
                if uri:
                    return Markup(f'<img src="{uri}" alt="" class="adglyph"/>')
            return Markup(ad_icons.svg("feature", style=style))
        return Markup(ad_icons.svg(name, style=style))

    env.globals["ad_glyph"] = _ad_glyph
    env.globals["ad_icon_for"] = ad_icons.icon_for
    env.globals["split_label"] = pack_icons.split_label
    env.filters["split3"] = _split3
    # "3 Bedroom Apartment" -> "Apartment", for surfaces that state the bed
    # count separately (the board's stacked headline).
    env.filters["regex_strip_beds"] = lambda t: re.sub(
        r"^\s*\d+\s*(?:-|\s)?bed(?:room)?s?\s+", "", str(t or ""), flags=re.I
    ).strip() or str(t or "")


def _file_data_uri(path: Path) -> str:
    """Read a file and return it as a base64 data URI, or "" when absent."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ""
    suffix = Path(path).suffix.lower()
    mime = _ASSET_MIME.get(suffix)
    if not mime:
        return ""
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def _split3(text: object) -> List[str]:
    """Split a headline into up to 3 roughly balanced lines (for the stacked,
    gold-middle-line headline style the DA ad designs use). One/two words stay on
    one line; longer headlines chunk into 3 near-equal lines by word count."""
    words = str(text or "").split()
    if len(words) <= 2:
        return [str(text or "")]
    lines = 3 if len(words) >= 3 else 2
    per = -(-len(words) // lines)  # ceil division
    return [" ".join(words[i : i + per]) for i in range(0, len(words), per)][:3]


_DATE_FORMATS = (
    "%d %B %Y", "%d %b %Y", "%-d %B %Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y",
    "%B %d %Y", "%b %d %Y", "%d %B", "%d %b",
)


# The three viewing states and the words each prints. The "none" wording is the
# client's own, verbatim from the fix list: a buyer who cannot view must be told
# so, and told why, before they bid.
VIEWING_WORDS = {
    "by_arrangement": ("Viewing:", "By arrangement with Dynamic Auctioneers"),
    "set_time": ("Viewing:", ""),          # filled with the window
    "none": ("Viewing not possible.", "Vacant occupation cannot be guaranteed."),
}


def _viewing(viewing: dict) -> dict:
    """The viewing block for every surface, from the record's mode.

    Falls back to the old boolean for records written before the mode existed,
    so nothing has to be migrated: by_appointment True reads as an arrangement.
    """
    mode = (viewing.get("mode") or "").strip()
    if mode not in VIEWING_WORDS:
        mode = "by_arrangement"
    lead, tail = VIEWING_WORDS[mode]
    if mode == "set_time":
        tail = str(viewing.get("viewing_at") or "").strip() or "To be confirmed"
    return {"mode": mode, "lead": lead, "detail": tail,
            "possible": mode != "none"}


def _board_phone(contact: object, fallback: str) -> str:
    """The one number a board prints, big.

    ``sale_process.viewing.contact_public`` is free text and has been seen
    holding a name, a number and an email in one string ("Dynamic Auctioneers
    086 155 2288 | properties@..."). A board carries a single number at 76px, so
    the NUMBER is extracted rather than the field printed: splitting on a
    separator that happens to be there put "Dynamic Auctioneers" where the phone
    should be, and printing the field whole ran off the edge of the board.
    """
    text = str(contact or "")
    match = re.search(r"(?:\+?27[\s-]?|0)\d[\d\s-]{7,12}\d", text)
    return " ".join(match.group(0).split()) if match else fallback


def _auction_weekday(text: object) -> Optional[str]:
    """The weekday of the auction, DERIVED from the date the marketer typed.

    The board's header reads "ONLINE | THURSDAY" over the date, and the day must
    be the actual auction day (owner's ruling) rather than a second thing to type
    and get wrong. ``auction_date`` is free display text (D42) - "28 May 2026",
    "2026-09-15", "7/5/2026" - so it is parsed rather than replaced by a date
    field, which would ripple into extraction, gate 2 and every artifact for a
    single derived word.

    An unparseable date yields None and the board prints no weekday: a wrong day
    on a printed board sends people to a property on the wrong morning.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    # Strip a weekday the marketer may already have typed, and any ordinal
    # suffix ("7th May 2026"), so both spellings parse.
    cleaned = re.sub(
        r"^(mon|tues|wednes|thurs|fri|satur|sun)day[,\s]+", "", raw, flags=re.I)
    cleaned = re.sub(r"(\d{1,2})(st|nd|rd|th)\b", r"\1", cleaned, flags=re.I)
    cleaned = cleaned.replace(",", " ").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    from datetime import datetime

    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        if parsed.year == 1900:          # a date with no year cannot name a day
            return None
        return parsed.strftime("%A").upper()
    return None


def _fmt_num(value: object) -> Optional[str]:
    """Render a number without a trailing ``.0`` (185.0 -> ``"185"``)."""
    if value is None:
        return None
    if isinstance(value, bool):  # guard: bools are ints in Python
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _fmt_size(value: object) -> Optional[str]:
    """An extent in m2 with space-grouped thousands (``1228995`` -> ``1 228 995``).

    A farm or land assembly runs to seven digits, and an ungrouped digit run
    cannot be read at a glance in a document a buyer studies. Grouped the same
    way the engine formats rands (``_rand`` in engine/render/service.py, SA
    style). A non-numeric value is passed through untouched.
    """
    text = _fmt_num(value)
    if text is None:
        return None
    try:
        whole = int(round(float(text)))
    except (TypeError, ValueError):
        return text
    return format(whole, ",").replace(",", " ")


# A free-text term that states one of the figures the OTP also states. These are
# the lines that go stale: somebody typed "10% deposit" onto the record months
# before the signed OTP said 20%.
_TERM_STATES_A_FIGURE = re.compile(
    r"\d+(?:[.,]\d+)?\s*%\s*(?:deposit|commission)"
    r"|\bdeposit\b[^.]{0,30}?\d+(?:[.,]\d+)?\s*%"
    r"|\bcommission\b[^.]{0,30}?\d+(?:[.,]\d+)?\s*%"
    r"|\d+\s*(?:\([a-z ]+\)\s*)?days?\b[^.]{0,40}?\bguarantee"
    r"|\bguarantee[^.]{0,40}?\d+\s*(?:\([a-z ]+\)\s*)?days?\b",
    re.I,
)


def _tagline(features: List[str], limit: int = 3) -> List[str]:
    """The short pipe-separated strip the reference ads carry (D84).

    Their taglines are three punchy phrases - "FAMILY RETREAT | ENTERTAINER'S
    PATIO | POOL & GARDEN" - while a record's features are full sentences
    ("3 bedrooms, main with en-suite (bath, toilet, basin)"). Taking the first
    three verbatim filled the strip with one long clause and wrapped it over two
    lines, which is not what the strip is.

    So: prefer the SHORT features, in the record's own order, and only fall back
    to longer ones when a property has nothing short to say. Nothing is invented
    or shortened - a phrase is either printed as written or not chosen.
    """
    clean = [f.strip().rstrip(".") for f in (features or []) if f and f.strip()]
    short = [f for f in clean if len(f) <= 30]
    picked = short[:limit]
    if len(picked) < limit:
        picked += [f for f in clean if f not in picked][: limit - len(picked)]
    return picked[:limit]


def _reconciled_terms(sale: dict) -> List[str]:
    """The terms strip, with the OTP owning every figure it states (D83).

    A property carries terms in two places: ``sale_process.terms``, free text a
    human typed, which the advert, the email and the portal listing print; and
    ``sale_process.otp``, read from the signed contract, which the information
    pack prints. Nothing reconciled them, so one property told a buyer a 10%
    deposit on the advert and a 20% deposit in the pack, at the same moment.

    Where the OTP states a figure, its own wording replaces any free-text line
    stating a deposit, commission or guarantee period; every other typed term
    (occupation, certificates, vacant occupation) is kept exactly as written,
    because the OTP says nothing about those and dropping them would lose real
    conditions. With no OTP the free text is returned untouched.
    """
    # Imported here rather than at module scope, like the other engine.otp uses
    # in this file (the module is only needed when a record carries an OTP).
    from engine.otp import has_terms, terms_lines

    typed = [str(t) for t in (sale.get("terms") or []) if str(t).strip()]
    otp = sale.get("otp") or {}
    if not has_terms(otp):
        return typed
    kept = [t for t in typed if not _TERM_STATES_A_FIGURE.search(t)]
    # terms_lines closes with a fixed occupation sentence; the strip only wants
    # the figures, and an occupation term the human typed is already in `kept`.
    contract = [
        line for line in terms_lines(otp)
        if not line.lower().startswith("occupation on date of registration")
    ]
    return contract + kept


# The order the owner reads an advert in (D92). Rank 0 first. Everything the
# stat rows already print (extent, beds, baths) is normally suppressed by
# _ad_features, but it is ranked here too so a property that prints them as
# bullets still runs in the right order.
_FEATURE_RANK: List[Tuple["re.Pattern[str]", int]] = [
    (re.compile(r"\bextent|\bstand\b|\berf\b|hectare|\bm2\b|\bm²|square met", re.I), 0),
    (re.compile(r"\bbedroom|\bbeds?\b", re.I), 1),
    (re.compile(r"\bbathroom|en.?suite|\bshower|\btoilet|ablution", re.I), 2),
    (re.compile(r"\bkitchen|scullery|pantry", re.I), 3),
    # Living space, dining included. An open-plan line already says both, so it
    # sits here as ONE row; a property with separate rooms keeps its two rows,
    # adjacent, because they share this rank.
    (re.compile(r"open.?plan|\blounge|\bliving|\bdining|family room|tv room", re.I), 4),
    # The nice-to-haves.
    (re.compile(r"entertain|\bbraai|\bbar\b|\bboma|\blapa|\bpool\b|\bpatio|balcon|veranda|"
                r"\bdeck\b|\bstoep|garden|\bview\b|jacuzzi|\bgym\b|playground", re.I), 5),
    # The practical tail, last by the owner's order.
    (re.compile(r"\bgarage|carport|\bparking|storeroom|storage|\bstaff\b|domestic|laundry", re.I), 7),
]
_FEATURE_RANK_OTHER = 6


def _feature_rank(text: str) -> int:
    """Where a feature line sits in the advert's running order (D92).

    Ranked on the line's SUBJECT - the opening words - not on any word in it.
    "Balcony leading from the lounge" is a balcony; matching anywhere put it with
    the living spaces because the sentence happens to end in "lounge".
    """
    head = " ".join(str(text or "").split()[:5])
    for pattern, rank in _FEATURE_RANK:
        if pattern.search(head):
            return rank
    # Nothing in the opening words: fall back to the whole line before giving up,
    # so a long clause still lands somewhere sensible.
    for pattern, rank in _FEATURE_RANK:
        if pattern.search(str(text or "")):
            return rank
    return _FEATURE_RANK_OTHER


def _drop_rooms_the_open_plan_line_covers(features: List[str]) -> List[str]:
    """"If open plan living and dining then one line" (owner's rule, D92).

    A record can carry "Open-plan living and dining room" AND a standalone
    "Dining room", which prints the same space twice on one advert. When an
    open-plan line already names a room, a bare line for that room is dropped.
    Only a BARE line goes: "Dining room with a built-in bar" says something the
    open-plan line does not, so it stays.
    """
    covered: set = set()
    for f in features:
        low = (f or "").lower()
        if "open-plan" in low or "open plan" in low:
            for room in ("living", "lounge", "dining", "kitchen"):
                if room in low:
                    covered.add(room)
    if not covered:
        return list(features)

    kept = []
    for f in features:
        low = (f or "").lower()
        if "open-plan" in low or "open plan" in low:
            kept.append(f)
            continue
        # a bare room line: the subject is the room and little else
        subject = re.split(r"[,(;]", low, maxsplit=1)[0].strip()
        words = [w for w in re.split(r"\W+", subject) if w and w not in ("room", "area", "the", "and")]
        if len(words) <= 2 and any(w in covered for w in words):
            continue
        kept.append(f)
    return kept


def _ordered_features(features: List[str]) -> List[str]:
    """The advert's rows in the owner's running order, de-duplicated (D92).

    ``sorted`` is stable, so lines sharing a rank keep the record's own order -
    which is what keeps a separate lounge and dining room adjacent and in the
    order the record states them.
    """
    trimmed = _drop_rooms_the_open_plan_line_covers([f for f in features if f])
    return sorted(trimmed, key=_feature_rank)


def _count(value: object) -> Optional[str]:
    """A countable quantity for display, or None when there is none of it.

    Zero is not a feature: "0 bathrooms" on an advert says less than nothing,
    and reads as a fault in the listing rather than a fact about the property.
    """
    text = _fmt_num(value)
    if text is None:
        return None
    try:
        if float(str(text).replace(" ", "")) == 0:
            return None
    except (TypeError, ValueError):
        pass
    return text


def _ad_features(
    features: List[str],
    *,
    beds: Optional[str] = None,
    baths: Optional[str] = None,
    garages: Optional[str] = None,
) -> List[str]:
    """Feature bullets for an ad, minus whatever the stat rows already say.

    The ad designs print a stat grid (extent, bedrooms, bathrooms, garages) and
    then a couple of feature bullets underneath. Both come off the same record,
    so a property whose features begin "3 bedrooms, main with en-suite" and
    "Full family bathroom plus separate toilet" printed **3 BEDROOMS** as a stat
    and then said it again as a bullet, on the same ad, twice over. The client's
    own ads never repeat a stat in the bullets.

    Only the line's SUBJECT is tested (everything before the first comma or
    bracket), so "Kitchen with a bathroom off the passage" survives while
    "2 bathrooms, both recently refitted" does not. A stat that is not being
    printed does not suppress anything.
    """
    skip: List[str] = []
    if beds:
        skip += ["bedroom", "bed "]
    if baths:
        skip += ["bathroom", "en-suite", "ensuite"]
    if garages and garages != "0":
        skip += ["garage", "carport"]
    if not skip:
        return _ordered_features(features)
    kept = []
    for feature in features:
        subject = re.split(r"[,(;]", feature, maxsplit=1)[0].lower()
        if any(word in subject for word in skip):
            continue
        kept.append(feature)
    return _ordered_features(kept)


def _fmt_ha(value: object) -> Optional[str]:
    """The same extent in hectares, one decimal, for land big enough to warrant
    it (over a hectare). ``None`` for an ordinary residential erf, so the pack
    simply says nothing rather than printing "0.0 ha"."""
    text = _fmt_num(value)
    if text is None:
        return None
    try:
        area = float(text)
    except (TypeError, ValueError):
        return None
    if area < 10000:
        return None
    ha = area / 10000
    # Two decimals under 100 ha, one above. The client's own boards print
    # "59.96 ha": rounding that to "60.0 ha" throws away 400 m2 of land on a
    # figure a buyer reads as exact, while a 1 200 ha farm does not need
    # centares. Trailing zeros are trimmed so 60 ha never prints as "60.00".
    text = f"{ha:.2f}" if ha < 100 else f"{ha:.1f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


class HtmlBackend(RenderBackend):
    """Render each format from bundled Jinja2 templates. Always available."""

    name = "html"

    def __init__(self) -> None:
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=_should_autoescape,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        configure_template_env(self._env)

    # --- backend contract ------------------------------------------------

    def available(self) -> "tuple[bool, str]":
        return (True, "html backend renders from bundled templates; no credentials required")

    def supports(self, fmt: str) -> bool:
        return fmt in _FORMAT_SPEC

    def render(self, request: RenderRequest) -> Artifact:
        """Render ``request.fmt`` for one property and write it to disk."""
        if not self.supports(request.fmt):
            raise ValueError(
                f"html backend cannot render {request.fmt!r}. "
                f"Known formats: {', '.join(sorted(_FORMAT_SPEC))}."
            )

        template_name, ext, mime = _FORMAT_SPEC[request.fmt]
        # The ad (demo_ad) design is chosen from the ad-template library (D41);
        # the marketer's pick rides request.template_set and degrades to Classic.
        # Every other format keeps its single fixed template.
        if request.fmt in ("demo_ad", "demo_ad_2", "demo_ad_3"):
            from engine.render import ad_templates

            if request.fmt in ("demo_ad_2", "demo_ad_3"):
                # Variation 2 and 3 (fix list 2.1): the next designs after the
                # pick, so a marketer choosing Hero-overlay gets Stats-first and
                # Collage beside it rather than three of the same advert.
                index = 0 if request.fmt == "demo_ad_2" else 1
                others = ad_templates.variation_ids(request.template_set)
                pick = others[index] if index < len(others) else request.template_set
                template_name = ad_templates.resolve(pick)
            else:
                template_name = ad_templates.resolve(request.template_set)
        template = self._env.get_template(template_name)
        context = self._view_model(request)
        rendered = template.render(vm=context)

        art_dir = Path(request.output_root) / f"DP{request.dp}" / "artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        path = art_dir / f"{request.fmt}.{ext}"
        path.write_text(rendered, encoding="utf-8")

        # The information pack is a DOCUMENT a buyer is emailed, so it ships as a
        # real PDF rather than a web page. The HTML above stays on disk as the
        # print source. If Chromium is unavailable the HTML artifact is served as
        # before - a missing browser must degrade, never fail the render.
        if request.fmt in _PDF_FORMATS and _pdf_export_enabled():
            from engine.render import rasterize

            pdf_path = art_dir / f"{request.fmt}.pdf"
            try:
                rasterize.html_to_pdf(
                    path, pdf_path, fit_selector=_PDF_FORMATS[request.fmt]
                )
                path, mime = pdf_path, "application/pdf"
            except Exception:
                pass

        return Artifact(
            dp=request.dp,
            fmt=request.fmt,
            backend=self.name,
            path=str(path),
            mime=mime,
        )

    # --- view model ------------------------------------------------------

    def _photo_groups(self, request, marketing: dict, photos: List[str]) -> List[tuple]:
        """The gallery split into named groups, in the marketer's order.

        The record stores group -> names; this resolves them to the same urls the
        flat list uses, drops any name no longer on the record, and appends
        whatever was never grouped so a photograph cannot vanish by being left
        out of a group.
        """
        groups = marketing.get("photo_groups") or {}
        gallery = photos[1:] if len(photos) > 1 else []
        if not groups:
            return [("", gallery)] if gallery else []

        by_name = {PurePosixPath(url).name: url for url in gallery}
        out, used = [], set()
        for name, members in groups.items():
            urls = [by_name[PurePosixPath(m).name] for m in (members or [])
                    if PurePosixPath(m).name in by_name]
            if urls:
                out.append((str(name), urls))
                used.update(urls)
        leftover = [url for url in gallery if url not in used]
        if leftover:
            out.append(("", leftover))
        return out

    def _view_model(self, request: RenderRequest) -> dict:
        """Build the template context from ``public_record`` (+ photos + copy).

        Everything here is derived from the POPIA-safe ``public_view`` projection,
        so PII is structurally absent. Any value supplied on ``request.copy`` wins
        over the derived default, which is how human copy edits survive re-renders.
        """
        rec = request.public_record or {}
        identity = rec.get("identity") or {}
        physical = rec.get("physical") or {}
        valuation = rec.get("valuation") or {}
        sale = rec.get("sale_process") or {}
        marketing = rec.get("marketing") or {}
        viewing = sale.get("viewing") or {}
        flatlet = physical.get("flatlet") or {}

        method = sale.get("method")
        badge_label = self._badge_label(method, marketing.get("price_display"))

        flatlet_present = bool(flatlet.get("present"))
        flatlet_beds = _fmt_num(flatlet.get("bedrooms")) if flatlet_present else None

        # Multi-portion property: the size shown is the SUM of the portions'
        # extents, added in code (never the model's arithmetic, hard rule 3).
        # A single-portion property has no portions and uses its unit size.
        portions = [p for p in (physical.get("portions") or []) if isinstance(p, dict)]
        portion_sizes = [p.get("size_m2") for p in portions if p.get("size_m2") is not None]
        total_extent = sum(portion_sizes) if portion_sizes else None
        size_value = total_extent if total_extent is not None else physical.get("unit_size_m2")

        photos = self._photo_refs(request, marketing)

        # OTP-derived sale terms (D68). Built here so the template stays free of
        # wording logic, and so a record with no OTP yields empty values that the
        # pack can fall back from.
        from engine.otp import confirmation_pill, has_terms, outstanding_pill, terms_lines

        otp = sale.get("otp") or {}
        # ANY term, not the deposit alone: a marketer typing the terms by hand
        # (D80) may know the commission and not the deposit, and the box has to
        # print what they entered rather than nothing.
        otp_lines = terms_lines(otp) if has_terms(otp) else []
        otp_confirmation = confirmation_pill(otp) if otp else None
        otp_outstanding = outstanding_pill(otp)

        vm: dict = {
            "dp": request.dp,
            # ``ref`` is the INTERNAL filing code (DP number) - used only on the
            # internal pack labels, never on public ad chrome. ``public_ref`` is
            # the DA mandate/MASTER REF, the reference a buyer may quote; it is
            # None until sourced, and public artifacts then show no reference at
            # all rather than leaking the internal DP (owner directive, D37).
            "ref": f"DP{request.dp}",
            "public_ref": identity.get("mandate_ref") or None,
            # PROPERTY REF on the ad chrome: the DP shown as "PROPERTY REF: DP<n>"
            # top-right on the branded ads, matching the team's real ads (D42
            # reverses the earlier D37 hide-the-DP directive). Ad templates render
            # it; the internal board tile still omits it.
            "property_ref": f"DP{request.dp}",
            "headline": marketing.get("headline") or "Property for sale",
            # Derived headline parts for the feature-list / stats-first ads, which
            # lead with the locality and a concise descriptor rather than the free
            # marketing headline (matches the real AD 2 / AD 3 designs).
            "place_line": self._place_line(identity),
            "descriptor_line": self._descriptor_line(physical, identity),
            "address": identity.get("street_address"),
            "suburb": identity.get("suburb"),
            "municipality": identity.get("municipality"),
            "province": identity.get("province"),
            "scheme": identity.get("scheme"),
            "unit": _fmt_num(identity.get("unit")),
            "erf": identity.get("erf"),
            "title_type": identity.get("title_type"),
            "title_type_label": self._title_type_label(identity.get("title_type")),
            # Deeds identity of the PROPERTY, for the info pack's title card.
            # Never a person: the owner's name and ID live in the POPIA internal
            # layer, which public_view() has already removed (SPEC 4.4).
            "legal_description": identity.get("legal_description"),
            "title_deed_no": identity.get("title_deed_no"),
            "gps_str": self._gps_str(identity.get("gps")),
            "location_line": self._location_line(identity),
            "method": method,
            # Auction specifics (D42), rendered on auction ads only.
            "auction_type": sale.get("auction_type"),
            "auction_channel": sale.get("auction_channel"),
            "auction_date": sale.get("auction_date"),
            # Derived from the date above, never typed (D71).
            "auction_weekday": _auction_weekday(sale.get("auction_date")),
            "auction_time": sale.get("auction_time"),
            "badge_label": badge_label,
            "price_display": marketing.get("price_display") or badge_label,
            "size_str": _fmt_num(size_value),
            # The same extent formatted for a DOCUMENT the buyer reads rather
            # than an ad tile: thousands grouped, plus a hectare equivalent once
            # the land is big enough for m2 to stop meaning anything.
            "size_display": _fmt_size(size_value),
            "size_ha": _fmt_ha(size_value),
            # Land portions of a multi-portion property, for templates that list
            # them; empty for an ordinary single-portion property.
            "portions": [
                {
                    "label": p.get("label"),
                    "erf": p.get("erf"),
                    "size_str": _fmt_num(p.get("size_m2")),
                    "size_display": _fmt_size(p.get("size_m2")),
                    # Deed number per portion, for the info pack's schedule table.
                    "deed": p.get("title_deed_no"),
                }
                for p in portions
            ],
            # A count of ZERO is not a feature (D94). `_fmt_num(0)` returns the
            # string "0", which is TRUTHY in Jinja, so `{% if vm.baths %}` passed
            # and a warehouse advertised "0 BATHROOMS". Fixed here rather than by
            # adding `!= '0'` to each template - that guard existed on garages
            # and had been forgotten on beds and baths, which is exactly how this
            # reached a client-facing advert.
            "beds": _count(physical.get("bedrooms")),
            "baths": _count(physical.get("bathrooms_main_unit")),
            "garages": _count(physical.get("garages")),
            "separate_toilet": bool(physical.get("separate_toilet")),
            "zoning": physical.get("zoning"),
            "flatlet_present": flatlet_present,
            "flatlet_beds": flatlet_beds,
            # The flatlet's own rooms, spelled out from the record's booleans so
            # the info pack can describe it without inventing anything.
            "flatlet_features": self._flatlet_features(flatlet) if flatlet_present else [],
            "features_main": list(physical.get("features_main") or []),
            # The same features minus anything the ad's stat grid already
            # states, so a bedroom count is never printed twice on one ad.
            # `_count` here too: a stat that is NOT printed must not suppress a
            # feature line about it. With `_fmt_num`, a property with zero
            # bedrooms printed no bed stat and still had its bedroom features
            # suppressed, so the advert said nothing about them at all.
            "features_ad": _ad_features(
                list(physical.get("features_main") or [])
                + list(physical.get("features_complex") or []),
                beds=_count(physical.get("bedrooms")),
                baths=_count(physical.get("bathrooms_main_unit")),
                garages=_count(physical.get("garages")),
            ),
            "features_complex": list(physical.get("features_complex") or []),
            # The marketer's per-line icon picks (D94). The advert reads them
            # directly; the pack reads them through icon_for below.
            "feature_icons": dict(marketing.get("feature_icons") or {}),
            "icon_style": marketing.get("icon_style") or "line",
            "custom_icons": dict(marketing.get("custom_icons") or {}),
            "output_root": request.output_root,
            # The short strip the reference ads carry, not the first three
            # feature sentences (D84).
            "tagline": _tagline(
                list(physical.get("features_main") or [])
                + list(physical.get("features_complex") or [])
            ),
            # The OTP is the contract, so where it states a figure it OWNS that
            # figure on every surface (D83). Without this the same property told
            # a buyer two different deposits at the same moment: the pack read
            # the OTP (20%) while the advert, the email and the portal listing
            # read a stale free-text line (10%). Same fault D64 fixed for price,
            # in the terms.
            "terms": _reconciled_terms(sale),
            # Sale terms read from the OTP's clauses (D68). Empty when no OTP has
            # been uploaded, and the pack then falls back to the record's own
            # free-text terms rather than printing a default that would be wrong.
            "otp_terms_lines": otp_lines,
            "otp_confirmation_pill": otp_confirmation,
            "otp_outstanding_pill": otp_outstanding,
            "municipal_valuation": _fmt_num(valuation.get("municipal_valuation")),
            # A running municipal cost to the buyer, not a valuation of the
            # property: the reference packs print it as a "Rates & Taxes" chip
            # and the no-valuation directive (D32/D54/D57) does not cover it.
            # The valuation figures themselves are already absent from this
            # projection, so there is nothing here to leak.
            "monthly_rates": _fmt_size(valuation.get("estimated_monthly_rates")),
            "monthly_levy": _fmt_size(valuation.get("monthly_levy")),
            "viewing_by_appt": bool(viewing.get("by_appointment")),
            "viewing": _viewing(viewing),
            "contact_public": viewing.get("contact_public"),
            # The single number the on-site board prints (D74).
            "board_phone": ((marketing.get("contact_phone") or "").strip()
                            or _board_phone(viewing.get("contact_public"), BRAND["phone"])),
            "photos": photos,
            # The gallery as NAMED groups (1.4, D78): [(name, [urls]), ...].
            # A property with no groups yields one untitled group holding
            # everything, so the pack's gallery pages are one code path.
            "photo_groups": self._photo_groups(request, marketing, photos),
            # The board's QR code, uploaded by the team (D69). Resolved the
            # same way as a photo so it embeds for print; None until they
            # add one, and the board then omits the block rather than
            # printing an empty white square.
            # ``next(..., None)`` rather than ``[0]``: since D81 a reference
            # whose file is missing is skipped, so this list can now come back
            # empty and indexing it would crash the whole render. Coming back
            # None is also the right answer - it is what makes the board omit
            # the block instead of printing the empty square.
            "qr_src": (
                next(iter(self._photo_refs(request, {"hero_photo": marketing.get("qr_code")})), None)
                if marketing.get("qr_code") else None
            ),
            # The advert's own photographs (D90): the marketer's pick when she
            # has made one, otherwise the lead plus the next three, which is
            # what the ads took before. Deliberately NOT `photos`, which the
            # information pack uses - the pack shows everything.
            "ad_photos": self._ad_photo_refs(request, marketing, photos),
            "hero_src": photos[0] if photos else None,
            "stack_photos": photos[1:3],
            "gallery_photos": photos[3:7],
            "brand_name": BRAND["name"],
            "brand_phone": (marketing.get("contact_phone") or "").strip() or BRAND["phone"],
            # A per-property mailbox when the marketer typed one (2.9, D78).
            "brand_email": (marketing.get("contact_email") or "").strip() or BRAND["email"],
            "brand_email_admin": BRAND["email_admin"],
            "brand_web": BRAND["web"],
            "brand_address": BRAND["address"],
            "brand_reg": BRAND["reg"],
            "generated_note": (
                "Generated automatically from the Lightstone EVM report and "
                "Property Report. E&OE."
            ),
        }

        # Copy overrides (generated or human-edited) win over derived defaults, so
        # re-renders preserve edits. Only non-null values override.
        if request.copy:
            vm.update({k: v for k, v in request.copy.items() if v is not None})

        # ...except the sale terms, where the OTP has the last word (D83). The
        # merge above can carry the record's stale free-text terms back in over
        # the reconciled ones, which is how the advert came to say 10% deposit
        # while the pack said 20%. Re-run it on whatever `terms` survived, so
        # the contract's figure wins whichever source supplied the list.
        vm["terms"] = _reconciled_terms({"terms": vm.get("terms"), "otp": sale.get("otp")})

        return vm

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _badge_label(method: Optional[str], price_display: Optional[str]) -> str:
        """Sale-method framing: offers vs auction (SPEC M5)."""
        if method == "offers_invited":
            return "Offers Invited"
        if method == "auction":
            return "On Auction"
        return price_display or "Enquire"

    @staticmethod
    def _title_type_label(title_type: Optional[str]) -> Optional[str]:
        return {
            "sectional": "Sectional title",
            "freehold": "Freehold",
        }.get(title_type or "", title_type)

    @staticmethod
    def _gps_str(gps: object) -> Optional[str]:
        """Coordinates as ``"lat, lon"`` for the info pack's title card.

        Deeds/public data, never PII. Returns None for anything that is not a
        clean numeric pair, so a malformed value simply drops the row.
        """
        if not isinstance(gps, (list, tuple)) or len(gps) != 2:
            return None
        try:
            lat, lon = float(gps[0]), float(gps[1])
        except (TypeError, ValueError):
            return None
        return f"{lat:.5f}, {lon:.5f}"

    @staticmethod
    def _flatlet_features(flatlet: dict) -> List[str]:
        """Name the flatlet's rooms from the record's booleans (no invention)."""
        labels = [
            ("ensuite", "En-suite bathroom"),
            ("kitchen", "Own kitchen"),
            ("lounge", "Lounge"),
            ("patio", "Patio"),
        ]
        return [label for key, label in labels if flatlet.get(key)]

    @staticmethod
    def _location_line(identity: dict) -> str:
        """Truthful location line from record fields only (no invented amenities)."""
        parts = [
            identity.get("suburb"),
            identity.get("municipality"),
            identity.get("province"),
        ]
        return ", ".join(p for p in parts if p)

    @staticmethod
    def _place_line(identity: dict) -> Optional[str]:
        """The prominent locality line for the feature-list / stats-first ads:
        suburb, plus the municipality when it adds a recognisable town/city."""
        # The AREA alone (owner's rule, D92): "Vorna Valley", not "Vorna Valley,
        # City of Johannesburg Metropolitan". The municipality is an
        # administrative fact, not how anyone says where a property is, and on a
        # 1080px advert it pushed the headline to three lines to say nothing a
        # buyer uses. It still stands in when there is no suburb - a farm often
        # has only a municipality - with the bureaucratic suffix dropped
        # ("Msunduzi Local Municipality" -> "Msunduzi").
        suburb = identity.get("suburb")
        if suburb:
            return suburb
        muni = identity.get("municipality")
        if muni:
            return muni.split(" Municipality")[0].split(" Local")[0].strip() or muni
        return identity.get("province")

    @staticmethod
    def _descriptor_line(physical: dict, identity: dict) -> Optional[str]:
        """A concise property descriptor for the feature-list / stats-first ads,
        e.g. "3 Bedroom Home" / "2 Bedroom Apartment" (matches the real ads)."""
        beds = _fmt_num(physical.get("bedrooms"))
        dwelling = {"sectional": "Apartment", "freehold": "Home"}.get(
            identity.get("title_type") or "", "Property"
        )
        if beds:
            return f"{beds} Bedroom {dwelling}"
        label = HtmlBackend._title_type_label(identity.get("title_type"))
        return label or "Property"

    @staticmethod
    def _ad_photo_refs(request: "RenderRequest", marketing: dict, photos: List[str]) -> List[str]:
        """The photographs the ADVERTS use, in the marketer's chosen order (D90).

        ``marketing.ad_photos`` names them; anything it names that is not on the
        record (renamed, removed) is skipped, and an empty or absent pick falls
        back to ``photos`` - the lead plus the gallery, which is what the adverts
        used before. The names are matched against the already-resolved refs, so
        a missing FILE is still excluded by ``_photo_refs`` (D81).
        """
        picked = [PurePosixPath(str(n)).name for n in (marketing.get("ad_photos") or [])]
        if not picked:
            return list(photos)
        by_name = {PurePosixPath(url).name: url for url in photos}
        chosen = [by_name[n] for n in picked if n in by_name]
        return chosen or list(photos)

    @staticmethod
    def _photo_refs(request: RenderRequest, marketing: dict) -> List[str]:
        """Resolve photo paths to references relative to the artifact directory.

        The record stores photo paths relative to the DP folder (``photos/x.png``);
        artifacts live one level deeper in ``DP<dp>/artifacts/``, so a record path
        resolves to ``../photos/x.png``. Absolute paths are honoured as given.
        Record picks (hero + gallery) are preferred; ``request.photos`` is the
        fallback when the record carries none.
        """
        picks: List[str] = []
        hero = marketing.get("hero_photo")
        if hero:
            picks.append(hero)
        picks.extend(marketing.get("gallery") or [])
        if not picks:
            picks = list(request.photos or [])

        art_dir = Path(request.output_root) / f"DP{request.dp}" / "artifacts"
        refs: List[str] = []
        for raw in picks:
            if not raw:
                continue
            if os.path.isabs(raw):
                candidate = Path(raw)
            else:
                # record paths are relative to the DP folder
                candidate = Path(request.output_root) / f"DP{request.dp}" / raw
            # A photograph the record lists but the disk does not have is SKIPPED,
            # never referenced (D81). An <img> pointing at a missing file prints
            # as an empty frame with its alt text showing, and the advert is a
            # client-facing document: one fewer photograph is a smaller fault
            # than a hole where a photograph should be. Found by driving the real
            # app - the unit suite only ever renders photos that exist.
            if not candidate.is_file():
                continue
            rel = os.path.relpath(str(candidate), str(art_dir))
            refs.append(str(PurePosixPath(*Path(rel).parts)))
        return refs
