"""SQLite persistence for property reports: the proposal store on its own table (M11, D116)."""

from __future__ import annotations

from engine.propertyreport.model import PropertyReport
from engine.proposal.store import ProposalStore


class ReportStore(ProposalStore):
    TABLE = "property_reports"
    MODEL = PropertyReport
    NAME_FIELD = "owner_name"
