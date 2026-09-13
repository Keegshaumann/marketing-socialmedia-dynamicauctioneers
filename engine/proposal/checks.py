"""What must be true before a proposal is generated (M9, D103).

Every proposal checked on the shared drive in September 2026 had a mistake a
rule would have caught: a Word file and its PDF quoting different auction dates
and venues (2887), a 2025 approval deadline on a 2026 auction (3018), a plan
number that differed between the cover and the contract (2821), and a contract
in the proposal that did not match the property's own OTP (2887: 20% against
10% deposit, 7 against 30 days). These checks are plain rules, no model.

``block`` issues stop generation. ``warn`` issues are shown and allowed: they
are the ones where the documents may legitimately differ in form (a seller name
set out differently) and a person should look rather than be stopped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional

from engine.proposal.model import Proposal, parse_cost


@dataclass(frozen=True)
class Issue:
    level: str      # "block" or "warn"
    field: str      # the form field it belongs to, for the page to point at
    message: str


_TITLE_DEED = re.compile(r"^(?:T|ST|TL|TE|G|SK)\s?\d{1,7}/\d{4}$", re.I)
_SA_ID = re.compile(r"^\d{13}$")
_CO_REG = re.compile(r"^\d{4}/\d{6}/\d{2}$")


def _squash(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def run(proposal: Proposal, files_root: Path, otp_text: Optional[str], today: date) -> List[Issue]:
    p = proposal
    issues: List[Issue] = []

    def block(field: str, message: str) -> None:
        issues.append(Issue("block", field, message))

    def warn(field: str, message: str) -> None:
        issues.append(Issue("warn", field, message))

    # Seller -------------------------------------------------------------
    if not p.seller_capacity:
        block("seller_capacity", "Choose what kind of seller this is (liquidation, insolvent estate and so on).")
    if not p.seller_name.strip():
        block("seller_name", "The seller's name is missing.")
    ident = p.seller_id.replace(" ", "")
    if ident and not (_SA_ID.match(ident) or _CO_REG.match(ident)):
        warn("seller_id", f"\"{p.seller_id}\" is neither a 13-digit ID number nor a registration number like 2014/203299/07.")

    # Erven ----------------------------------------------------------------
    if not p.erven:
        block("erven", "Add at least one property.")
    for n, erf in enumerate(p.erven, start=1):
        which = f"Property {n}" if len(p.erven) > 1 else "The property"
        if not erf.legal_description.strip():
            block(f"erf_{n}_legal", f"{which}: the legal description is missing.")
        if not erf.known_as.strip():
            block(f"erf_{n}_known_as", f"{which}: the street address (better known as) is missing.")
        if not erf.title_deed.strip():
            block(f"erf_{n}_title_deed", f"{which}: the title deed number is missing.")
        elif not _TITLE_DEED.match(erf.title_deed.strip()):
            warn(f"erf_{n}_title_deed", f"{which}: \"{erf.title_deed}\" does not look like a title deed number (T12345/2001 or ST12345/2017).")

    # Auction ------------------------------------------------------------------
    if p.auction_date is None:
        block("auction_date", "The auction date is missing.")
    elif p.auction_date < today:
        block("auction_date", f"The auction date ({p.auction_date:%-d %B %Y}) is in the past.")
    if not p.time_from.strip():
        block("time_from", "The auction start time is missing.")
    if not p.channel:
        block("channel", "Choose online or on site.")
    if p.channel == "onsite" and not p.venue_address.strip():
        block("venue_address", "An on-site auction needs the venue address.")
    if not p.administrator.strip():
        block("administrator", "The administrator is missing.")

    # Deadline ------------------------------------------------------------------
    if p.deadline is None:
        block("deadline", "The approval deadline is missing.")
    else:
        if p.deadline < today:
            block("deadline", f"The approval deadline ({p.deadline:%-d %B %Y}) is in the past.")
        if p.auction_date is not None and p.deadline >= p.auction_date:
            block("deadline", "The approval deadline must fall before the auction date.")

    # Budget --------------------------------------------------------------------
    printed = 0
    for n, line in enumerate(p.budget, start=1):
        try:
            amount, word = parse_cost(line.cost)
        except ValueError as exc:
            block(f"budget_{n}_cost", f"Budget line \"{line.media.splitlines()[0] if line.media else n}\": {exc}.")
            continue
        if amount is not None or word is not None:
            printed += 1
            if not line.media.strip():
                block(f"budget_{n}_media", f"Budget line {n} has a cost but no media name.")
    if printed == 0:
        block("budget", "The budget has no lines. Type the liquidator's figures, or FREE or N/A.")

    # Files -----------------------------------------------------------------------
    def exists(rel: str) -> bool:
        return bool(rel) and (files_root / rel).is_file()

    if not any(exists(f) for f in p.deeds_images):
        block("deeds_images", "Add the deeds page: upload the Lightstone report or a screenshot.")
    elif len(p.deeds_images) < len(p.erven):
        warn("deeds_images", f"There are {len(p.erven)} properties but {len(p.deeds_images)} deeds page(s).")
    if not exists(p.advert_image):
        block("advert_image", "Add the draft advert image from marketing.")
    if not exists(p.otp_file):
        block("otp_file", "Upload this property's OTP (.docx). The proposal includes it word for word.")

    # Terms the introduction quotes --------------------------------------------------
    t, o = p.terms, p.otp_terms
    if t.deposit_pct is None:
        block("deposit_pct", "The deposit % is missing.")
    if t.confirmation_days is None:
        block("confirmation_days", "The confirmation period is missing.")
    if t.commission_pct is None:
        block("commission_pct", "The commission % is missing.")
    for field, label, mine, theirs in (
        ("deposit_pct", "deposit", t.deposit_pct, o.deposit_pct),
        ("confirmation_days", "confirmation period", t.confirmation_days, o.confirmation_days),
        ("commission_pct", "commission", t.commission_pct, o.commission_pct),
    ):
        if mine is not None and theirs is not None and mine != theirs:
            block(field, f"The {label} typed here ({mine}) is not what the OTP says ({theirs}).")
    for note in p.otp_notes:
        warn("otp_file", note)

    # Cover against the contract ------------------------------------------------------
    if otp_text:
        contract = _squash(otp_text)
        for n, erf in enumerate(p.erven, start=1):
            deed = _squash(erf.title_deed)
            if deed and deed not in contract:
                block(f"erf_{n}_title_deed", f"Title deed {erf.title_deed} is not in the OTP. Check you uploaded this property's OTP.")
        if p.seller_id and _squash(p.seller_id) not in contract:
            warn("seller_id", f"The seller's number {p.seller_id} does not appear in the OTP.")
        if p.seller_name and _squash(p.seller_name) not in contract:
            warn("seller_name", "The seller's name is not written the same way in the OTP.")
        if p.masters_ref and _squash(p.masters_ref) not in contract:
            warn("masters_ref", f"The Master's reference {p.masters_ref} does not appear in the OTP.")

    return issues


def blocking(issues: List[Issue]) -> List[Issue]:
    return [i for i in issues if i.level == "block"]
