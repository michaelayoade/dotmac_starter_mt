"""Hosted migration compatibility: null history is never first-dispatch proof."""

import json
from uuid import uuid4

import pytest
from dotmac_integration import InboxReceipt
from dotmac_integration.receipt_delivery import (
    ProductAcceptance,
    ReceiptClaims,
    deliver_receipt,
)
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.test_integration_isolation import (  # noqa: F401
    _installed_capability_registry,
    migrated_scratch,
)
from tests.test_integration_receipt_delivery_isolation import _receipt
from tests.test_integration_receipt_origin_migration import _config
from tests.unit.test_integration_ingress import (  # noqa: F401
    _installed_integration_audit_actions,
)


def test_predecessor_null_rows_and_attempt_reset_remain_unknown(
    migrated_scratch,  # noqa: F811
    request,
):
    from dotmac_integration.capability_registry import capability_registry

    from alembic import command

    admin_url, _ = migrated_scratch
    config = _config(admin_url)
    command.downgrade(config, "ig_0016_receipt_origin")
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        legacy = _receipt(conn, request, state="retryable", attempt_count=7)
        untouched = _receipt(conn, request, state="processed", attempt_count=3)
    command.upgrade(config, "heads")
    with engine.begin() as conn:
        assert (
            conn.scalar(
                text(
                    "SELECT column_default FROM information_schema.columns WHERE "
                    "table_schema='mod_intg' AND table_name='inbox_receipts' AND "
                    "column_name='delivery_snapshot_json'"
                )
            )
            is None
        )
    from dotmac_integration.operations import replay_receipt

    with Session(engine) as db, db.begin():
        row = db.get(InboxReceipt, legacy)
        replay_receipt(db, row, reason="hosted legacy replay")
        assert row.attempt_count == 0
        assert row.delivery_snapshot_json is None
    store = ReceiptClaims(sessionmaker(bind=engine), registry=capability_registry())

    class Gateway:
        def deliver(self, request):
            pytest.fail("legacy unknown history cannot reach a gateway")

    report = deliver_receipt(receipt_id=legacy, store=store, gateway=Gateway())
    assert report.preparation_refused
    assert report.outcome.acceptance is ProductAcceptance.INDETERMINATE
    assert report.outcome.error_code == "dispatch_snapshot_unavailable"
    with Session(engine) as db:
        row = db.get(InboxReceipt, legacy)
        assert row.delivery_snapshot_json is None
        assert row.state == "reconciliation_required"
        terminal = db.get(InboxReceipt, untouched)
        assert terminal.state == "processed" and terminal.attempt_count == 3
        assert terminal.delivery_snapshot_json is None
    assert store.claim(receipt_id=untouched) is None
    with pytest.raises(DBAPIError, match="unknown dispatch history cannot be promoted"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE mod_intg.inbox_receipts "
                    "SET delivery_snapshot_json=CAST(:snapshot AS jsonb) WHERE id=:id"
                ),
                {"id": legacy, "snapshot": '{"schema_version":1,"state":"unstarted"}'},
            )
    engine.dispose()


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"schema_version": True, "state": "unstarted"},
        {"schema_version": 2, "state": "unstarted"},
        {"schema_version": 1, "state": "unstarted", "payload": {"private": "value"}},
        {"schema_version": 1, "state": "frozen", "source": {}, "destination": {}},
    ],
)
def test_database_rejects_malformed_snapshot_inserts(
    migrated_scratch,  # noqa: F811
    request,
    document,
):
    from tests.test_integration_isolation import _installation_and_binding

    engine = create_engine(migrated_scratch[0])
    with engine.begin() as conn:
        installation_id, binding_id = _installation_and_binding(conn, request)
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(
                text("""
                INSERT INTO mod_intg.inbox_receipts
                    (id, installation_id, capability_binding_id, provider_event_id,
                     event_type, payload_digest, delivery_snapshot_json)
                VALUES (:id,:installation,:binding,:event,'received',
                        :digest,CAST(:snapshot AS jsonb))
            """),
                {
                    "id": uuid4(),
                    "installation": installation_id,
                    "binding": binding_id,
                    "event": uuid4().hex,
                    "digest": "a" * 64,
                    "snapshot": json.dumps(document),
                },
            )
    engine.dispose()
