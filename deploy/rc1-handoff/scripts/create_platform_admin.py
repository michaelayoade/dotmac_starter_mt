#!/usr/bin/env python
"""Bootstrap (or rotate) a platform admin from the CLI.

This is the ONLY way a platform admin comes into existence — there is
deliberately no HTTP self-registration path for the platform control plane
(see `dotmac_kernel.platform_auth`'s module docstring and ADR-0004). Running this
script requires direct platform DB credentials, which puts it behind the
same trust boundary as running migrations.

Usage:
    poetry run python scripts/create_platform_admin.py admin@example.com
    poetry run python scripts/create_platform_admin.py admin@example.com --inactive

The password is prompted (never taken from argv — argv leaks into shell
history and process listings). The upsert is idempotent by email: an
existing admin gets its password rotated and active flag set, a missing one
is created.

The database URL comes from `--database-url`, else `MIGRATION_DATABASE_URL`,
else `PLATFORM_DATABASE_URL` — never a hardcoded default.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

# Allow running as a plain script from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotmac_kernel.models_platform import PlatformAdmin
from dotmac_kernel.security import hash_password


def upsert_admin(
    db: Session, *, email: str, password: str, is_active: bool = True
) -> PlatformAdmin:
    """Idempotent upsert-by-email (case-insensitive, mirroring the
    `uq_platform_admins_lower_email` index): create the admin, or rotate the
    existing one's password and active flag."""
    normalized = email.strip().lower()
    admin = db.scalars(
        select(PlatformAdmin).where(func.lower(PlatformAdmin.email) == normalized)
    ).first()
    if admin is None:
        admin = PlatformAdmin(
            email=normalized,
            password_hash=hash_password(password),
            is_active=is_active,
        )
        db.add(admin)
    else:
        admin.password_hash = hash_password(password)
        admin.is_active = is_active
    db.flush()
    return admin


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email", help="Admin email (unique, case-insensitive)")
    parser.add_argument(
        "--inactive",
        action="store_true",
        help="Create/update the admin as inactive (cannot log in)",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("MIGRATION_DATABASE_URL")
        or os.getenv("PLATFORM_DATABASE_URL")
        or "",
        help=(
            "Platform DB URL (default: MIGRATION_DATABASE_URL, then "
            "PLATFORM_DATABASE_URL from the environment)"
        ),
    )
    args = parser.parse_args()

    if not args.database_url:
        parser.error(
            "no database URL: pass --database-url or set MIGRATION_DATABASE_URL / "
            "PLATFORM_DATABASE_URL"
        )

    password = getpass.getpass("Password: ")
    if not password:
        parser.error("empty password")
    if getpass.getpass("Confirm password: ") != password:
        parser.error("passwords do not match")

    engine = create_engine(args.database_url)
    try:
        with Session(engine) as db:
            existed = (
                db.scalars(
                    select(PlatformAdmin).where(
                        func.lower(PlatformAdmin.email) == args.email.strip().lower()
                    )
                ).first()
                is not None
            )
            admin = upsert_admin(
                db,
                email=args.email,
                password=password,
                is_active=not args.inactive,
            )
            db.commit()
            state = "inactive" if args.inactive else "active"
            action = "updated" if existed else "created"
            print(f"Platform admin {admin.email} {action} ({state}).")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
