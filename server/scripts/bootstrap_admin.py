from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.identity_db import init_identity_db
from app.core.web_security import create_admin_user


def main():
    parser = argparse.ArgumentParser(description="Create a LEHUE Web Admin account")
    parser.add_argument("username")
    parser.add_argument("--role", choices=["pi", "ra"], default="ra")
    parser.add_argument("--display-name", default="")
    parser.add_argument("--password", default="")
    args = parser.parse_args()
    password = args.password or secrets.token_urlsafe(18)
    init_identity_db()
    try:
        create_admin_user(args.username, password, args.role, args.display_name)
    except Exception as exc:
        raise SystemExit(f"Cannot create admin user: {exc}")
    print("LEHUE Web Admin user created")
    print(f"username = {args.username.lower()}")
    print(f"role     = {args.role}")
    print(f"password = {password}")
    print("Save the password now. Only a salted hash is stored.")


if __name__ == "__main__":
    main()
