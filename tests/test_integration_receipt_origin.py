"""Hosted Postgres canaries for original engine verification context.

These exercise the actual ingress prepare/batch owners and the migrated final
UPDATE guard. Context is provenance only: no provider or cash qualification.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest
from dotmac_integration import InboundEvent, InboxReceipt, put_config_revision
from dotmac_integration.execution import receipt_verification_origin
from dotmac_integration.ingress import (
    EventVerificationOriginConflict,
    prepare_ingress,
    record_batch,
)
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.test_integration_isolation import (  # noqa: F401
    _installed_capability_registry,
    migrated_scratch,
)
from tests.unit.test_integration_ingress import (  # noqa: F401
    IngressFake,
    _installed_integration_audit_actions,
    address,
    build,
    registry_for,
)


def _event(identity: str) -> tuple[InboundEvent, ...]:
    return (
        InboundEvent(provider_event_id=identity, event_type="received", payload={}),
    )


def test_first_context_survives_retry_rotation_and_atomic_conflict(
    migrated_scratch: tuple[str, str],  # noqa: F811
) -> None:
    engine = create_engine(migrated_scratch[0])
    registry = registry_for(IngressFake())
    identity = uuid.uuid4().hex
    with Session(engine) as db, db.begin():
        installation, _, key = build(db, registry)
        installation_id = installation.id
        prepared = prepare_ingress(db, endpoint=address(key), registry=registry)
        ((receipt_id, created),) = record_batch(db, prepared, _event(identity))
        assert created
        assert receipt_verification_origin(db.get(InboxReceipt, receipt_id)) == (
            prepared.verification_origin
        )
    with Session(engine) as db, db.begin():
        # Reusing the ORIGINAL detached prepared context is an exact retry.
        assert record_batch(db, prepared, _event(identity)) == ((receipt_id, False),)
        installation = db.get(type(installation), installation_id)
        put_config_revision(
            db,
            installation,
            registry=registry,
            config={"variant": "rotated"},
            secret_refs={"signing": "bao://kv/signing"},
        )
        rotated = prepare_ingress(db, endpoint=address(key), registry=registry)
        assert rotated.config_revision_id != prepared.config_revision_id
    fresh_identity = uuid.uuid4().hex
    with pytest.raises(EventVerificationOriginConflict):
        with Session(engine) as db, db.begin():
            # A new row preceding the conflicting duplicate must roll back too.
            record_batch(db, rotated, _event(fresh_identity) + _event(identity))
    with Session(engine) as db, db.begin():
        receipt = db.get(InboxReceipt, receipt_id)
        assert receipt_verification_origin(receipt) == prepared.verification_origin
        assert (
            db.scalar(
                select(InboxReceipt).where(
                    InboxReceipt.provider_event_id == fresh_identity
                )
            )
            is None
        )
        assert record_batch(db, prepared, _event(identity)) == ((receipt_id, False),)
    engine.dispose()


@pytest.mark.parametrize(
    "field,value",
    [
        ("manifest_digest", "e" * 64),
        ("connector_key", "another_connector"),
    ],
)
def test_duplicate_changed_context_is_explicit(
    migrated_scratch: tuple[str, str],  # noqa: F811
    field: str,
    value: str,
) -> None:
    engine = create_engine(migrated_scratch[0])
    registry = registry_for(IngressFake())
    with Session(engine) as db, db.begin():
        _, _, key = build(db, registry)
        prepared = prepare_ingress(db, endpoint=address(key), registry=registry)
        events = _event(uuid.uuid4().hex)
        record_batch(db, prepared, events)
    with pytest.raises(EventVerificationOriginConflict):
        with Session(engine) as db, db.begin():
            record_batch(db, replace(prepared, **{field: value}), events)
    engine.dispose()


def test_legacy_unknown_is_never_backfilled(
    migrated_scratch: tuple[str, str],  # noqa: F811
) -> None:
    from dotmac_integration.execution import receive_verified

    engine = create_engine(migrated_scratch[0])
    registry = registry_for(IngressFake())
    identity = uuid.uuid4().hex
    with Session(engine) as db, db.begin():
        _, _, key = build(db, registry)
        prepared = prepare_ingress(db, endpoint=address(key), registry=registry)
        legacy, _ = receive_verified(
            db,
            installation_id=prepared.installation_id,
            capability_binding_id=prepared.binding_id,
            provider_event_id=identity,
            event_type="received",
            payload={},
        )
        legacy_id = legacy.id
        assert receipt_verification_origin(legacy) is None
    with pytest.raises(EventVerificationOriginConflict):
        with Session(engine) as db, db.begin():
            record_batch(db, prepared, _event(identity))
    with Session(engine) as db:
        assert receipt_verification_origin(db.get(InboxReceipt, legacy_id)) is None
    engine.dispose()


@pytest.mark.parametrize("role", ["app_admin", "platform_api"])
@pytest.mark.parametrize(
    "column,value",
    [
        ("verification_manifest_digest", "e" * 64),
        ("verification_connector_key", "another_connector"),
        ("verification_mode", "poll"),
        ("verification_config_revision_id", None),
        ("installation_id", None),
        ("capability_binding_id", None),
    ],
)
def test_final_guard_refuses_origin_rewrites(
    migrated_scratch: tuple[str, str],  # noqa: F811
    role: str,
    column: str,
    value: object,
) -> None:
    engine = create_engine(migrated_scratch[0])
    registry = registry_for(IngressFake())
    with Session(engine) as db, db.begin():
        _, _, key = build(db, registry)
        prepared = prepare_ingress(db, endpoint=address(key), registry=registry)
        ((receipt_id, _),) = record_batch(db, prepared, _event(uuid.uuid4().hex))
        # Real valid alternative parent IDs isolate the immutable guard from FK.
        other_installation, other_binding, _ = build(db, registry)
        if column == "installation_id":
            value = other_installation.id
        elif column == "capability_binding_id":
            value = other_binding.id
    role_engine = create_engine(
        make_url(migrated_scratch[0]).set(username=role, password=role)
    )
    with pytest.raises(DBAPIError, match="receipt verification origin is immutable"):
        with role_engine.begin() as conn:
            conn.execute(
                update(InboxReceipt)
                .where(InboxReceipt.id == receipt_id)
                .values({column: value})
            )
    role_engine.dispose()
    engine.dispose()


def test_tenant_role_has_no_origin_access(
    migrated_scratch: tuple[str, str],  # noqa: F811
) -> None:
    engine = create_engine(migrated_scratch[1])
    for statement in (
        "SELECT verification_connector_key FROM mod_intg.inbox_receipts",
        "UPDATE mod_intg.inbox_receipts SET verification_mode = 'poll'",
    ):
        with pytest.raises(DBAPIError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "SET LOCAL app.current_tenant = "
                        "'00000000-0000-0000-0000-000000000001'"
                    )
                )
                conn.execute(text(statement))
    engine.dispose()


def test_poll_records_the_actual_prepared_pin(
    migrated_scratch: tuple[str, str],  # noqa: F811
) -> None:
    from dotmac_integration.conformance import fake_plugin
    from dotmac_integration.polling import (
        ensure_polling_checkpoint,
        invoke_poll,
        prepare_poll,
        record_poll_batch,
    )

    engine = create_engine(migrated_scratch[0])
    events = _event(uuid.uuid4().hex)
    registry = registry_for(fake_plugin(inbound=events, next_cursor="after"))
    with Session(engine) as db, db.begin():
        _, binding, _ = build(db, registry)
        registration = ensure_polling_checkpoint(
            db,
            capability_binding_id=binding.id,
            job_key="original-context",
            registry=registry,
        )
        prepared = prepare_poll(
            db, checkpoint_id=registration.checkpoint.id, registry=registry
        )
        batch = invoke_poll(
            prepared, registry=registry, resolve_secrets=lambda refs: {}
        )
        result = record_poll_batch(db, prepared, batch)
        receipt = db.get(InboxReceipt, result.receipt_ids[0])
        assert receipt_verification_origin(receipt) == prepared.verification_origin
        assert prepared.verification_origin.verification_mode == "poll"
        assert (
            prepared.manifest_digest
            == registry.plugin(prepared.connector_key).manifest.digest
        )
    engine.dispose()


def test_guard_sees_changes_from_late_before_trigger_and_has_sensitivity(
    migrated_scratch: tuple[str, str],  # noqa: F811
) -> None:
    engine = create_engine(migrated_scratch[0])
    registry = registry_for(IngressFake())
    with Session(engine) as db, db.begin():
        _, _, key = build(db, registry)
        prepared = prepare_ingress(db, endpoint=address(key), registry=registry)
        ((receipt_id, _),) = record_batch(db, prepared, _event(uuid.uuid4().hex))
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(
                text("""
                CREATE FUNCTION mod_intg.origin_late_mutation_canary()
                RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
                    NEW.verification_manifest_digest := repeat('e', 64);
                    RETURN NEW;
                END $$;
                CREATE TRIGGER zzzz_origin_late_mutation_canary
                BEFORE UPDATE ON mod_intg.inbox_receipts
                FOR EACH ROW EXECUTE FUNCTION mod_intg.origin_late_mutation_canary();
            """)
            )
            with pytest.raises(
                DBAPIError, match="receipt verification origin is immutable"
            ):
                conn.execute(
                    text(
                        "UPDATE mod_intg.inbox_receipts SET state = state "
                        "WHERE id = :id"
                    ),
                    {"id": receipt_id},
                )
        finally:
            transaction.rollback()
    # Sensitivity: disabling the actual guard admits the exact forbidden edit.
    # All destructive DDL and mutation are rolled back in this scratch database.
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(
                text(
                    "ALTER TABLE mod_intg.inbox_receipts DISABLE TRIGGER "
                    "receipt_verification_origin_final"
                )
            )
            conn.execute(
                text(
                    "UPDATE mod_intg.inbox_receipts "
                    "SET verification_manifest_digest = repeat('e', 64) "
                    "WHERE id = :id"
                ),
                {"id": receipt_id},
            )
            assert (
                conn.scalar(
                    text(
                        "SELECT verification_manifest_digest "
                        "FROM mod_intg.inbox_receipts "
                        "WHERE id = :id"
                    ),
                    {"id": receipt_id},
                )
                == "e" * 64
            )
        finally:
            transaction.rollback()
    engine.dispose()


def test_legacy_null_context_cannot_be_written_later(
    migrated_scratch: tuple[str, str],  # noqa: F811
) -> None:
    from dotmac_integration.execution import receive_verified

    engine = create_engine(migrated_scratch[0])
    registry = registry_for(IngressFake())
    with Session(engine) as db, db.begin():
        _, _, key = build(db, registry)
        prepared = prepare_ingress(db, endpoint=address(key), registry=registry)
        legacy, _ = receive_verified(
            db,
            installation_id=prepared.installation_id,
            capability_binding_id=prepared.binding_id,
            provider_event_id=uuid.uuid4().hex,
            event_type="received",
            payload={},
        )
        legacy_id = legacy.id
    with pytest.raises(DBAPIError, match="receipt verification origin is immutable"):
        with Session(engine) as db, db.begin():
            legacy = db.get(InboxReceipt, legacy_id)
            legacy.verification_connector_key = prepared.connector_key
            legacy.verification_manifest_digest = prepared.manifest_digest
            legacy.verification_config_revision_id = prepared.config_revision_id
            legacy.verification_mode = "ingress"
            db.flush()
    with Session(engine) as db:
        assert receipt_verification_origin(db.get(InboxReceipt, legacy_id)) is None
    engine.dispose()


def test_two_actual_contexts_race_and_the_first_winner_retains_its_origin(
    migrated_scratch: tuple[str, str],  # noqa: F811
) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from dotmac_integration.ingress import ReceiptWriteRaced

    engine = create_engine(migrated_scratch[0])
    registry = registry_for(IngressFake())
    events = _event(uuid.uuid4().hex)
    with Session(engine) as db, db.begin():
        installation, _, key = build(db, registry)
        original = prepare_ingress(db, endpoint=address(key), registry=registry)
        put_config_revision(
            db,
            installation,
            registry=registry,
            config={"variant": "rotated"},
            secret_refs={"signing": "bao://kv/signing"},
        )
        rotated = prepare_ingress(db, endpoint=address(key), registry=registry)
    rendezvous = threading.Barrier(2)

    def attempt(prepared):
        try:
            with Session(engine) as db, db.begin():
                db.execute(text("SET LOCAL lock_timeout = '5s'"))
                db.execute(text("SET LOCAL statement_timeout = '10s'"))
                rendezvous.wait(timeout=10)
                result = record_batch(db, prepared, events)
            return prepared, result
        except (ReceiptWriteRaced, EventVerificationOriginConflict):
            return prepared, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, prepared) for prepared in (original, rotated)]
        results = [future.result(timeout=20) for future in futures]
    winners = [(prepared, result) for prepared, result in results if result is not None]
    assert len(winners) == 1
    winning_prepared, ((receipt_id, created),) = winners[0]
    assert created
    losing_prepared = next(prepared for prepared, result in results if result is None)
    with Session(engine) as db:
        assert (
            receipt_verification_origin(db.get(InboxReceipt, receipt_id))
            == winning_prepared.verification_origin
        )
        assert (
            len(
                db.scalars(
                    select(InboxReceipt).where(
                        InboxReceipt.capability_binding_id == original.binding_id,
                        InboxReceipt.provider_event_id == events[0].provider_event_id,
                    )
                ).all()
            )
            == 1
        )
    with pytest.raises(EventVerificationOriginConflict):
        with Session(engine) as db, db.begin():
            record_batch(db, losing_prepared, events)
    with Session(engine) as db, db.begin():
        assert record_batch(db, winning_prepared, events) == ((receipt_id, False),)
    engine.dispose()


@pytest.mark.parametrize(
    "column,expected",
    [
        ("installation_id", "receipt verification origin address differs"),
        ("capability_binding_id", "receipt verification origin address differs"),
        ("verification_connector_key", "receipt verification origin address differs"),
        (
            "verification_config_revision_id",
            "receipt verification origin revision differs",
        ),
        ("verification_manifest_digest", "ck_inbox_receipts_origin_digest"),
        ("verification_mode", "ck_inbox_receipts_verification_origin"),
    ],
)
def test_insert_guard_refuses_mismatched_original_context(
    migrated_scratch: tuple[str, str],  # noqa: F811
    column: str,
    expected: str,
) -> None:
    from sqlalchemy import insert

    engine = create_engine(migrated_scratch[0])
    registry = registry_for(IngressFake())
    receipt_id = uuid.uuid4()
    with Session(engine) as db, db.begin():
        _, _, key = build(db, registry)
        prepared = prepare_ingress(db, endpoint=address(key), registry=registry)
        _, _, other_key = build(db, registry)
        other = prepare_ingress(db, endpoint=address(other_key), registry=registry)
    values = {
        "id": receipt_id,
        "installation_id": prepared.installation_id,
        "capability_binding_id": prepared.binding_id,
        "provider_event_id": uuid.uuid4().hex,
        "event_type": "received",
        "payload_digest": "d" * 64,
        "state": "verified",
        "verification_connector_key": prepared.connector_key,
        "verification_manifest_digest": prepared.manifest_digest,
        "verification_config_revision_id": prepared.config_revision_id,
        "verification_mode": "ingress",
    }
    hostile_values = {
        "installation_id": other.installation_id,
        "capability_binding_id": other.binding_id,
        "verification_connector_key": "different_connector",
        "verification_config_revision_id": other.config_revision_id,
        "verification_manifest_digest": "z" * 64,
        "verification_mode": None,
    }
    values[column] = hostile_values[column]
    # Deliberate direct hostile INSERT proves the database guard independently
    # of the engine's typed address validation. All references exist for real.
    with pytest.raises(DBAPIError, match=expected):
        with engine.begin() as conn:
            conn.execute(insert(InboxReceipt).values(values))
    with Session(engine) as db:
        assert db.get(InboxReceipt, receipt_id) is None
    engine.dispose()
