"""Bring the sets the studio already prepared up to the 25 Nov 2026 rules (D129).

    sudo -u dauction ./venv/bin/python scripts/studio_refresh_sets.py [--dry-run] [DP3025 ...]

For every property the studio has read (or the ones named):
- the advert's features chosen again, 4 or 5 that matter, from what the read
  already found (a small Claude question, no documents);
- the QR link taken from the team's own QR code in the folder (their GoHighLevel
  tracking link to the property's page), when there is one;
- the office number written the one way in the words.

With --choose-photos, Claude chooses the photos again (the ads now use three
beside the background; about R2 each, no documents), unless a person placed photos.

With --read-when-thin, a property that comes back with fewer than 4 real
features is read again in full (documents and photos, about R10), since the
summary of the first read may have left out what the documents say.

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

from studio import catalog, facts as F, prepare, state  # noqa: E402
from webapp import models  # noqa: E402


def refresh(db: str, ref: str, dry_run: bool, read_when_thin: bool = False, choose_photos: bool = False) -> str:
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
            got = F.pick_features(prop, facts)
        except Exception as exc:  # keep the old ones; say why
            got = {}
            notes.append(f"features kept ({exc})")
        if got.get("features"):   # fewer than 4 holds the set for a person to add one (publish.missing)
            for k in ("features", "main_title", "extra_units", "shared_features"):
                if k not in row["edited"]:
                    facts[k] = got.get(k, [] if k != "main_title" else "")
            new = got["features"]
            notes.append(("features: " if len(new) >= 4 else f"only {len(new)} real features: ")
                         + " / ".join(f["text"] for f in new)
                         + "".join(f" | {u.get('title')}: " + " / ".join(i["text"] for i in u.get("items", []))
                                   for u in got.get("extra_units") or []))
    if "qr_link" not in row["edited"]:
        link = F.team_qr_link(catalog.items(prop))
        if link and link != facts.get("qr_link"):
            facts["qr_link"] = link
            notes.append(f"QR link {link}")
    if facts != row["facts"] and not dry_run:
        state.save(db, ref, facts=facts)
    real = [x for x in facts.get("features") or [] if (x.get("text") or "").strip()]
    if read_when_thin and len(real) < 4 and "features" not in row["edited"] and not dry_run:
        prepare.read(db, ref, prepare.STUDIO)
        again = state.load(db, ref)
        feats = again["facts"].get("features") or []
        notes.append(f"read again in full ({again['reading']}): " + " / ".join(x["text"] for x in feats))
    if choose_photos and not dry_run and not any("read again" in n for n in notes):
        row = state.load(db, ref)
        if prepare.placed_by_a_person(row):
            notes.append("photos left as she placed them")
        else:
            items = catalog.items(prop)
            ok = prepare.choose_photos(db, prop, items, row, row["mode"] or prepare.auto_mode(prop))
            notes.append("photos chosen again" if ok else "photo choice failed; kept")
    return f"{ref}: " + ("; ".join(notes) or "nothing to change")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("refs", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--read-when-thin", action="store_true")
    ap.add_argument("--choose-photos", action="store_true")
    args = ap.parse_args()
    db = models.resolve_db_path()
    refs = args.refs or sorted(ref for ref, r in state.all_rows(db).items() if r["reading"] == "done")
    with ThreadPoolExecutor(4) as ex:
        for line in ex.map(lambda r: refresh(db, r, args.dry_run, args.read_when_thin, args.choose_photos), refs):
            print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
