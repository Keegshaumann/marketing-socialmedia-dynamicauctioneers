"""One board for a whole block sold under one instruction (6.4, D79).

The client's rule: units in the same apartment block are advertised together -
"Apartments for sale in Ten On Lane" - not as one board per front door. So the
board leads with the COUNT and the scheme, and states the ranges across the set
rather than one unit's numbers.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from engine.render.service import lot_group_summary, render_all
from engine.schema import Identity, Marketing, Physical, PropertyRecord, SaleProcess
from engine.store import RecordStore


def _block(store: RecordStore, scheme: str = "TEN ON LANE") -> None:
    """A parent instruction with two sub-lots, the DP2948.1 shape."""
    def unit(dp, parent, beds, size):
        return PropertyRecord(
            dp=dp, parent_dp=parent,
            identity=Identity(scheme=scheme, suburb="Pietersburg Central",
                              title_type="sectional", mandate_ref="G971/2024"),
            physical=Physical(bedrooms=beds, unit_size_m2=size),
            marketing=Marketing(headline=f"Unit {dp}"),
            sale_process=SaleProcess(method="auction", auction_channel="Online",
                                     auction_type="Liquidation",
                                     auction_date="7 May 2026", auction_time="10:00"),
        )
    for dp, parent, beds, size in (("2948", None, 2, 78.0),
                                   ("2948.1", "2948", 2, 82.0),
                                   ("2948.2", "2948", 3, 111.0)):
        store.upsert(unit(dp, parent, beds, size), state="extracted")


@pytest.fixture()
def store():
    s = RecordStore(db_path=":memory:")
    try:
        yield s
    finally:
        s.close()


def test_a_block_is_summarised_across_its_units(store):
    _block(store)
    group = lot_group_summary("2948.1", store)

    assert group["count"] == 3
    assert group["scheme"] == "TEN ON LANE"
    assert (group["beds_low"], group["beds_high"]) == (2, 3)
    assert (group["size_low"], group["size_high"]) == (78.0, 111.0)


def test_a_property_that_stands_alone_has_no_estate_board(store, tmp_path):
    """"1 apartment in" nothing is not a headline: the board is simply absent."""
    store.upsert(PropertyRecord(dp="3060", identity=Identity(suburb="Pelham North")),
                 state="extracted")

    assert lot_group_summary("3060", store) is None
    fmts = {a.fmt for a in render_all("3060", store, backend="html",
                                      output_root=str(tmp_path))}
    assert "estate_board" not in fmts
    assert "auction_board" in fmts, "the single-property board must still render"


def test_units_in_different_schemes_are_not_one_board(store):
    """Sub-lots of one instruction can be scattered; a board says "in <scheme>",
    so it is only honest when there is one scheme."""
    _block(store)
    stray = store.get("2948.2")
    stray.identity.scheme = "SOMEWHERE ELSE"
    store.upsert(stray)

    assert lot_group_summary("2948.1", store) is None


def test_the_board_leads_with_the_count_and_the_scheme(store, tmp_path):
    _block(store)
    arts = {a.fmt: a for a in render_all("2948.1", store, backend="html",
                                         output_root=str(tmp_path))}
    html = Path(arts["estate_board"].path).read_text(encoding="utf-8")

    assert "3 Apartments" in html or "3 APARTMENTS" in html.upper()
    assert "TEN ON LANE" in html.upper()
    assert "2 TO 3 BED" in html.upper()
    assert "78 TO 111" in html.replace("&plusmn;", "").upper()
    # It is a board, so no property photograph and one reference only.
    assert 'alt="Property photograph"' not in html
    assert "G971/2024" in html


def test_one_bed_size_across_the_block_reads_as_one_value(store, tmp_path):
    """"2 to 2 bed" is not a range."""
    _block(store)
    for dp in ("2948", "2948.1", "2948.2"):
        rec = store.get(dp)
        rec.physical.bedrooms = 2
        rec.physical.unit_size_m2 = 80.0
        store.upsert(rec)

    arts = {a.fmt: a for a in render_all("2948.1", store, backend="html",
                                         output_root=str(tmp_path))}
    html = Path(arts["estate_board"].path).read_text(encoding="utf-8").upper()
    assert "2-BED" in html
    assert "2 TO 2" not in html
