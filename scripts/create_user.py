"""Create a platform login with a generated password (D104).

The platform has no users screen, so accounts are made on the server:

    cd /opt/da-marketing
    sudo -u dauction ./venv/bin/python scripts/create_user.py someone@dynamicauctioneers.co.za properties

The password is generated here, printed once, and stored only as a bcrypt hash;
there is no way to read it back afterwards. ``--reset`` gives an existing account
a new generated password (its role is left as it is).

Roles: ``marketing`` and ``approver`` run the marketing pipeline, ``properties``
opens auction proposals only, ``admin`` opens everything.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from webapp import auth, models  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create a platform login with a generated password.")
    parser.add_argument("email")
    parser.add_argument("role", choices=(*auth.ROLES, auth.ADMIN_ROLE))
    parser.add_argument("--reset", action="store_true", help="give an existing account a new password")
    parser.add_argument("--db", help="database path (default: ENGINE_DB, else ./engine.db)")
    args = parser.parse_args(argv)

    if args.db is None:
        load_dotenv()  # ENGINE_DB lives in the server's .env
    db = models.init_db(args.db)
    email = args.email.strip().lower()
    password = secrets.token_urlsafe(15)

    existing = models.get_user(db, email)
    if existing is not None and not args.reset:
        print(f"{email} already exists (role {existing['role']}). Use --reset for a new password.", file=sys.stderr)
        return 1
    if existing is not None:
        models.set_password(db, email, auth.hash_password(password))
        role = existing["role"]
        action = "Password reset"
    else:
        models.create_user(db, email=email, pw_hash=auth.hash_password(password), role=args.role)
        role = args.role
        action = "Account created"

    print(f"{action}: {email} ({role})")
    print(f"Password: {password}")
    print("Shown once; only its hash is stored.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
