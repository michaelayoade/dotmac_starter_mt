#!/usr/bin/env python
"""Audited requeue of one dead-lettered dispatch (ADR-0006 § 5g, K-5).

DRY RUN BY DEFAULT: without `--apply` the whole command runs (including every
refusal) and then rolls back, so it reports exactly what `--apply` would do and
changes nothing. With `--apply` it commits one dead -> pending transition, one
audit row naming the actor and reason, and one new `delivery.dispatch.v1` event.

Connects as the RLS-enforced `app_user` role (`DATABASE_URL`), with the tenant
context set to `--tenant-id`: another tenant's dispatch is "not found". It never
clears a suppression and never requeues a dispatch that already has a final
receipt (`dotmac_kernel.delivery_requeue`). Output carries ids and a status
only, never an address, subject or body.

Usage:
  DATABASE_URL=postgresql+psycopg://app_user:...@host/db \
  python scripts/requeue_dead_dispatch.py --tenant-id <uuid> --dispatch-id <uuid> \
      --actor-id <operator> --source-application <code> --reason "gate lifted" \
      [--apply]
"""

from __future__ import annotations

import argparse
import os
import sys
from uuid import UUID

from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
from dotmac_kernel.delivery import DeliveryError
from dotmac_kernel.delivery_requeue import requeue_dead_dispatch
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--dispatch-id", type=UUID, required=True)
    parser.add_argument("--actor-id", required=True, help="the operator's identity")
    parser.add_argument("--source-application", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument(
        "--apply", action="store_true", help="commit (default is a dry run)"
    )
    args = parser.parse_args(argv)

    url = os.getenv("DATABASE_URL")
    if not url:
        print("set DATABASE_URL (the app_user role)", file=sys.stderr)
        return 2

    install_audit_actions(AuditActionRegistry.from_manifests(()))
    engine = create_engine(url, pool_pre_ping=True)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        db.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": str(args.tenant_id)},
        )
        try:
            result = requeue_dead_dispatch(
                db,
                args.tenant_id,
                dispatch_id=args.dispatch_id,
                actor_type="user",
                actor_id=args.actor_id,
                source_application=args.source_application,
                reason=args.reason,
            )
        except (DeliveryError, ValueError, KeyError) as exc:
            db.rollback()
            print(f"refused: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        if not args.apply:
            db.rollback()
            print(
                f"dry run: dispatch {result.dispatch_id} "
                f"{'would be requeued' if result.requeued else 'is already pending'}"
                "; nothing changed (pass --apply)"
            )
            return 0
        db.commit()
        print(
            f"dispatch {result.dispatch_id}: "
            f"{'requeued' if result.requeued else 'already pending, no change'}"
            + (f" (event {result.outbox_event_id})" if result.outbox_event_id else "")
        )
        return 0
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
