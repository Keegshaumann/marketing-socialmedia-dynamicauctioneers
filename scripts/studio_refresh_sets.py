"""Bring the sets the studio already prepared up to the 25 Nov 2026 rules (D129).

    sudo -u dauction ./venv/bin/python scripts/studio_refresh_sets.py [--dry-run] [DP3025 ...]

For every property the studio has read (or the ones named):
- the advert's features chosen again, 4 or 5 that matter, from what the read
  already found (a small Claude question, no documents);
- the QR link taken from the team's own QR code in the folder (their GoHighLevel
  tracking link to the property's page), when there is one;
- the office number written the one way in the words.

A field a person typed is never touched. The studio's job then files the changed
sets again by itself (the layout version moved to 2, so every filed set is made
again with the new layout).
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from studio import catalog, facts as F, state  # noqa: E402
from webapp import models  # noqa: E402


def refresh(db: str, ref: str, dry_run: bool) -> str:
    prop = catalog.get(ref)
    row = state.load(db, ref)
    if prop is None or row is None or row["reading"] != "done":
        return f"{ref}: skipped (not read)"
    facts = F.tidy(dict(row["facts"]))
    notes = []
    if facts != row["facts"]:
        notes.append("phone tidied")
    if "features" not in row["edited"]:
        try:
            new = F.pick_features(prop, facts)
        except Exception as exc:  # keep the old ones; say why
            new = []
            notes.append(f"features kept ({exc})")
        if new:   # fewer than 4 holds the set for a person to add one (publish.missing)
            facts["features"] = new
            notes.append(("features: " if len(new) >= 4 else f"only {len(new)} real features: ")
                         + " / ".join(f["text"] for f in new))
    if "qr_link" not in row["edited"]:
        link = F.team_qr_link(catalog.items(prop))
        if link and link != facts.get("qr_link"):
            facts["qr_link"] = link
            notes.append(f"QR link {link}")
    if facts != row["facts"] and not dry_run:
        state.save(db, ref, facts=facts)
    return f"{ref}: " + ("; ".join(notes) or "nothing to change")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("refs", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    db = models.resolve_db_path()
    refs = args.refs or sorted(ref for ref, r in state.all_rows(db).items() if r["reading"] == "done")
    with ThreadPoolExecutor(4) as ex:
        for line in ex.map(lambda r: refresh(db, r, args.dry_run), refs):
            print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
