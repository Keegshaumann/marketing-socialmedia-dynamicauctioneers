"""Copy what the Ad Studio needs from the director mirror into its own file.

Run as the ``director`` user every ten minutes (docs/deploy/da-studio-mirror.*).

Why a copy: the director's database is in WAL mode and owned by ``director``. A
second account can only open it read-only, and a read-only reader cannot rebuild
the WAL index while the director is writing, so the studio failed with "attempt
to write a readonly database" whenever the hourly sync ran. Opened by its owner,
the source reads normally; the copy is a plain rollback-journal file the studio
reads with no special access.

Only the two tables the studio uses are copied (``files`` and ``items``), never
the director's logins or the email subjects it keeps, and ``items`` gets an index
on ``path`` for the per-folder lookups.

Usage: python3 copy_director_db.py [SOURCE] [DESTINATION]
"""

from __future__ import annotations

import os
import sqlite3
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "/opt/dynamic-director/director.db"
DST = sys.argv[2] if len(sys.argv) > 2 else "/var/lib/da-studio/director.db"


def main() -> None:
    tmp = DST + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    dst = sqlite3.connect(tmp, uri=True, timeout=60)
    dst.execute("PRAGMA journal_mode=DELETE")
    dst.execute("ATTACH DATABASE ? AS src", (f"file:{SRC}?mode=ro",))
    dst.execute("BEGIN")   # one read transaction: both tables from the same moment
    dst.execute("CREATE TABLE main.files AS SELECT id, dept, state, data FROM src.files")
    dst.execute("CREATE TABLE main.items AS SELECT drive_id, item_id, name, is_folder, modified, size, "
                "child_count, path FROM src.items")
    dst.execute("COMMIT")
    dst.execute("DETACH DATABASE src")
    dst.execute("CREATE INDEX items_path ON items (path)")
    dst.execute("CREATE INDEX files_id ON files (id)")
    dst.commit()
    files = dst.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    dst.close()
    if not files:
        os.remove(tmp)
        sys.exit("the director mirror had no files; kept the previous copy")
    os.chmod(tmp, 0o640)
    os.replace(tmp, DST)   # readers holding the old file keep reading it


if __name__ == "__main__":
    main()
