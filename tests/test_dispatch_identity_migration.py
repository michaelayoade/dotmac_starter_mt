"""Migration 0032 (dispatch identity): up, down on an empty table, refusal on data.

Runs on a scratch database as `app_admin` like production. Requires Postgres.
"""

# ruff: noqa: S608  (SQL interpolates only the module-constant table name)

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.test_migration_split_rehearsals import (
    _both_locations,
    _downgrade,
    _table_exists,
    _upgrade,
    _versions,
    scratch_db,  # noqa: F401  (fixture)
)

REVISION = "0032_communication_dispatches"
#: The kernel head above it (K-6). `alembic_version` holds heads, not ancestors,
#: so while 0034 is the head the live row to look for is 0034.
HEAD = "0036_typed_claim_grant"
PREVIOUS = "0031_rate_limit_buckets"
TABLE = "communication_dispatches"


def test_up_then_down_on_an_empty_table_then_up_again(scratch_db: str) -> None:  # noqa: F811
    _upgrade(scratch_db, "heads")
    assert HEAD in _versions(scratch_db)
    assert _table_exists(scratch_db, TABLE)

    _downgrade(scratch_db, PREVIOUS, version_locations=_both_locations())
    assert REVISION not in _versions(scratch_db)
    assert not _table_exists(scratch_db, TABLE)

    _upgrade(scratch_db, "heads")
    assert _table_exists(scratch_db, TABLE)


def test_down_refuses_a_non_empty_table(scratch_db: str) -> None:  # noqa: F811
    _upgrade(scratch_db, "heads")
    eng = create_engine(scratch_db)
    tenant = uuid.uuid4()
    try:
        with eng.begin() as conn:
            conn.execute(
                text("INSERT INTO tenants (id, slug, name) VALUES (:i, :s, 'T')"),
                {"i": str(tenant), "s": f"t{tenant.hex[:8]}"},
            )
            conn.execute(
                text(
                    f"INSERT INTO {TABLE} (dispatch_id, tenant_id, idempotency_key, "
                    "channel, category, template_provenance, status) VALUES "
                    "(:d, :t, 'k', 'email', 'transactional', 'legacy:email_outbox', "
                    "'history')"
                ),
                {"d": str(uuid.uuid4()), "t": str(tenant)},
            )
    finally:
        eng.dispose()
    with pytest.raises(Exception, match="refusing to drop non-empty"):
        _downgrade(scratch_db, PREVIOUS, version_locations=_both_locations())
    assert _table_exists(scratch_db, TABLE)
    assert HEAD in _versions(scratch_db)  # the whole downgrade rolled back


def test_table_constraints_hold_on_the_migrated_schema(scratch_db: str) -> None:  # noqa: F811
    """History rows are bare; non-history rows need identity and a body."""
    _upgrade(scratch_db, "heads")
    eng = create_engine(scratch_db)
    tenant = uuid.uuid4()
    base = (
        f"INSERT INTO {TABLE} (dispatch_id, tenant_id, idempotency_key, channel, "
        "category, template_provenance, {cols}) VALUES (:d, :t, :k, 'email', "
        "'transactional', 'legacy:email_outbox', {vals})"
    )
    try:
        with eng.begin() as conn:
            conn.execute(
                text("INSERT INTO tenants (id, slug, name) VALUES (:i, :s, 'T')"),
                {"i": str(tenant), "s": f"t{tenant.hex[:8]}"},
            )

        def attempt(cols: str, vals: str, key: str) -> None:
            with eng.begin() as conn:
                conn.execute(
                    text(base.format(cols=cols, vals=vals)),
                    {"d": str(uuid.uuid4()), "t": str(tenant), "k": key},
                )

        # history with content: refused
        with pytest.raises(DBAPIError):
            attempt("status, address", "'history', 'a@example.com'", "c1")
        # pending without fingerprint/address: refused
        with pytest.raises(DBAPIError):
            attempt("status, text_body", "'pending', 'x'", "c2")
        # pending with no body and not redacted: refused
        with pytest.raises(DBAPIError):
            attempt(
                "status, address, content_fingerprint",
                f"'pending', 'a@example.com', '{'0' * 64}'",
                "c3",
            )
        # unknown status: refused
        with pytest.raises(DBAPIError):
            attempt("status", "'queued'", "c4")
        # a well-formed pending row: accepted
        attempt(
            "status, address, content_fingerprint, text_body",
            f"'pending', 'a@example.com', '{'0' * 64}', 'hi'",
            "ok",
        )
    finally:
        eng.dispose()
