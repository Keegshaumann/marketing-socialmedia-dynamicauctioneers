"""SQLite persistence for OTPs: the proposal store pointed at its own table (M10, D114)."""

from __future__ import annotations

from engine.otpgen.model import Otp
from engine.proposal.store import ProposalStore


class OtpStore(ProposalStore):
    TABLE = "otps"
    MODEL = Otp
