"""The three-phase POLL engine and its checkpoint/receipt atomicity."""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from dotmac_integration import (
    CapabilityBinding,
    CheckpointConflict,
    CheckpointDefinitionConflict,
    CheckpointLifecycleError,
    ConnectorConfigRevision,
    ConnectorInstallation,
    ConnectorMode,
    ConnectorRegistry,
    EventIdentityCollision,
    InboundDisposition,
    InboundEvent,
    InboxReceipt,
    PollConnectorRaised,
    PollContractError,
    PollingCheckpoint,
    PollingCheckpointRegistration,
    advance_checkpoint,
    enabled_polling_checkpoint_page,
    ensure_polling_checkpoint,
    invoke_poll,
    payload_digest,
    poll_once,
    prepare_poll,
    record_poll_batch,
)
from dotmac_integration.conformance import FAKE_CAPABILITY, FakePlugin, fake_plugin
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


@pytest.fixture()
def engine() -> Engine:
    value = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in (
        ConnectorInstallation,
        ConnectorConfigRevision,
        CapabilityBinding,
        InboxReceipt,
        PollingCheckpoint,
    ):
        cast(Any, model.__table__).create(value)
    return value


def _seed(
    engine: Engine,
    *,
    events: tuple[InboundEvent, ...] = (),
    next_cursor: str | None = "page-2",
    poll_raises: BaseException | None = None,
) -> tuple[ConnectorRegistry, FakePlugin, uuid.UUID]:
    plugin = fake_plugin(
        inbound=events,
        next_cursor=next_cursor,
        poll_raises=poll_raises,
    )
    registry = ConnectorRegistry((plugin,))
    with Session(engine) as db:
        installation = ConnectorInstallation(
            id=uuid.uuid4(),
            connector_key=plugin.manifest.connector_key,
            connector_version=plugin.manifest.version,
            spi_range=str(plugin.manifest.spi_range),
            manifest_digest=plugin.manifest.digest,
            name="poll-source",
            state="enabled",
        )
        db.add(installation)
        db.flush()
        revision = ConnectorConfigRevision(
            id=uuid.uuid4(),
            installation_id=installation.id,
            revision=1,
            schema_version="1",
            config_digest="c" * 64,
            config_json={"variant": "primary"},
            secret_refs={"token": "bao://integrator/poll/token"},
        )
        db.add(revision)
        db.flush()
        installation.current_config_revision_id = revision.id
        binding = CapabilityBinding(
            id=uuid.uuid4(),
            installation_id=installation.id,
            capability_id=FAKE_CAPABILITY,
            state="enabled",
        )
        db.add(binding)
        db.flush()
        checkpoint = PollingCheckpoint(
            id=uuid.uuid4(),
            capability_binding_id=binding.id,
            job_key="live-tail",
            version=1,
            cursor_json={"cursor": "page-1"},
        )
        db.add(checkpoint)
        db.commit()
        return registry, plugin, checkpoint.id


def _unit_of_work(engine: Engine):
    @contextmanager
    def _open() -> Iterator[Session]:
        with Session(engine) as db:
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    return _open


def test_invoke_poll_cannot_be_given_a_database_session() -> None:
    assert "db" not in inspect.signature(invoke_poll).parameters
    assert "session" not in inspect.signature(invoke_poll).parameters


def test_checkpoint_registration_is_idempotent_and_detached(engine: Engine) -> None:
    registry, _, seeded_id = _seed(engine)
    with Session(engine) as db:
        seeded = db.get(PollingCheckpoint, seeded_id)
        assert seeded is not None
        binding_id = seeded.capability_binding_id
        first = ensure_polling_checkpoint(
            db,
            capability_binding_id=binding_id,
            job_key="  reconciliation-tail  ",
            initial_cursor="page-7",
            registry=registry,
        )
        replay = ensure_polling_checkpoint(
            db,
            capability_binding_id=binding_id,
            job_key="reconciliation-tail",
            initial_cursor="page-7",
            registry=registry,
        )
        db.commit()

    assert isinstance(first, PollingCheckpointRegistration)
    assert first.created is True
    assert replay.created is False
    assert replay.checkpoint == first.checkpoint
    assert first.checkpoint.job_key == "reconciliation-tail"
    assert first.checkpoint.cursor == "page-7"
    assert first.checkpoint.version == 1


def test_checkpoint_registration_refuses_changed_initial_cursor(engine: Engine) -> None:
    registry, _, seeded_id = _seed(engine)
    with Session(engine) as db:
        seeded = db.get(PollingCheckpoint, seeded_id)
        assert seeded is not None
        binding_id = seeded.capability_binding_id
        ensure_polling_checkpoint(
            db,
            capability_binding_id=binding_id,
            job_key="daily-reconciliation",
            initial_cursor="day-1",
            registry=registry,
        )
        with pytest.raises(CheckpointDefinitionConflict, match="initial cursor"):
            ensure_polling_checkpoint(
                db,
                capability_binding_id=binding_id,
                job_key="daily-reconciliation",
                initial_cursor="day-2",
                registry=registry,
            )


def test_advanced_checkpoint_replays_only_when_initial_cursor_is_omitted(
    engine: Engine,
) -> None:
    registry, _, seeded_id = _seed(engine)
    with Session(engine) as db:
        seeded = db.get(PollingCheckpoint, seeded_id)
        assert seeded is not None
        binding_id = seeded.capability_binding_id
        registered = ensure_polling_checkpoint(
            db,
            capability_binding_id=binding_id,
            job_key="advanced-tail",
            initial_cursor="original",
            registry=registry,
        )
        checkpoint = db.get(PollingCheckpoint, registered.checkpoint.id)
        assert checkpoint is not None
        advance_checkpoint(
            db,
            checkpoint=checkpoint,
            cursor={"cursor": "current"},
            expected_version=1,
        )

        replay = ensure_polling_checkpoint(
            db,
            capability_binding_id=binding_id,
            job_key="advanced-tail",
            registry=registry,
        )
        assert replay.created is False
        assert replay.checkpoint.cursor == "current"
        for explicit in ("original", "current", None):
            with pytest.raises(
                CheckpointDefinitionConflict, match="no longer revalidatable"
            ):
                ensure_polling_checkpoint(
                    db,
                    capability_binding_id=binding_id,
                    job_key="advanced-tail",
                    initial_cursor=explicit,
                    registry=registry,
                )


@pytest.mark.parametrize("job_key", ["", "   ", "x" * 161])
def test_checkpoint_registration_refuses_invalid_job_keys(
    engine: Engine, job_key: str
) -> None:
    registry, _, seeded_id = _seed(engine)
    with Session(engine) as db:
        seeded = db.get(PollingCheckpoint, seeded_id)
        assert seeded is not None
        with pytest.raises(CheckpointLifecycleError):
            ensure_polling_checkpoint(
                db,
                capability_binding_id=seeded.capability_binding_id,
                job_key=job_key,
                registry=registry,
            )


def test_checkpoint_registration_refuses_a_missing_binding(engine: Engine) -> None:
    registry, _, _ = _seed(engine)
    with (
        Session(engine) as db,
        pytest.raises(CheckpointLifecycleError, match="binding does not exist"),
    ):
        ensure_polling_checkpoint(
            db,
            capability_binding_id=uuid.uuid4(),
            job_key="missing-binding",
            registry=registry,
        )


def test_checkpoint_registration_refuses_a_non_poll_binding(engine: Engine) -> None:
    registry, plugin, seeded_id = _seed(engine)
    object.__setattr__(plugin, "modes_", frozenset({ConnectorMode.DELIVERY}))
    with Session(engine) as db:
        seeded = db.get(PollingCheckpoint, seeded_id)
        assert seeded is not None
        with pytest.raises(CheckpointLifecycleError, match="poll mode"):
            ensure_polling_checkpoint(
                db,
                capability_binding_id=seeded.capability_binding_id,
                job_key="impossible-poll",
                registry=registry,
            )


def test_checkpoint_selector_pages_only_enabled_binding_installations(
    engine: Engine,
) -> None:
    _, _, enabled_id = _seed(engine)
    with Session(engine) as db:
        enabled = db.get(PollingCheckpoint, enabled_id)
        assert enabled is not None
        binding = db.get(CapabilityBinding, enabled.capability_binding_id)
        assert binding is not None
        installation = db.get(ConnectorInstallation, binding.installation_id)
        assert installation is not None
        enabled.advanced_at = datetime(2026, 1, 2, tzinfo=UTC)
        earlier_enabled_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
        db.add(
            PollingCheckpoint(
                id=earlier_enabled_id,
                capability_binding_id=binding.id,
                job_key="earlier-enabled",
                cursor_json={"cursor": None},
                advanced_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )

        disabled_binding = CapabilityBinding(
            id=uuid.uuid4(),
            installation_id=installation.id,
            capability_id="disabled.binding.v1",
            state="disabled",
        )
        db.add(disabled_binding)
        db.flush()
        disabled_binding_checkpoint = PollingCheckpoint(
            id=uuid.uuid4(),
            capability_binding_id=disabled_binding.id,
            job_key="disabled-binding",
            cursor_json={"cursor": None},
        )
        db.add(disabled_binding_checkpoint)

        disabled_installation = ConnectorInstallation(
            id=uuid.uuid4(),
            connector_key="disabled_connector",
            connector_version="1.0.0",
            spi_range=">=1.0,<2.0",
            manifest_digest="d" * 64,
            name="disabled-installation",
            state="disabled",
        )
        db.add(disabled_installation)
        db.flush()
        disabled_installation_binding = CapabilityBinding(
            id=uuid.uuid4(),
            installation_id=disabled_installation.id,
            capability_id="disabled.installation.v1",
            state="enabled",
        )
        db.add(disabled_installation_binding)
        db.flush()
        disabled_installation_checkpoint = PollingCheckpoint(
            id=uuid.uuid4(),
            capability_binding_id=disabled_installation_binding.id,
            job_key="disabled-installation",
            cursor_json={"cursor": None},
        )
        db.add(disabled_installation_checkpoint)
        db.commit()

        expected = tuple(sorted((earlier_enabled_id, enabled_id)))
        first = enabled_polling_checkpoint_page(db, limit=1)
        assert first.checkpoint_ids == expected[:1]
        assert first.next_after == expected[0]
        second = enabled_polling_checkpoint_page(db, limit=1, after=first.next_after)
        assert second.checkpoint_ids == expected[1:]
        assert second.next_after is None
        assert first.checkpoint_ids + second.checkpoint_ids == expected


def test_checkpoint_selector_requires_an_explicit_positive_bound(
    engine: Engine,
) -> None:
    with Session(engine) as db, pytest.raises(ValueError, match="positive"):
        enabled_polling_checkpoint_page(db, limit=0)


def test_prepare_pins_config_references_cursor_and_checkpoint_version(
    engine: Engine,
) -> None:
    registry, _, checkpoint_id = _seed(engine)
    with Session(engine) as db:
        prepared = prepare_poll(db, checkpoint_id=checkpoint_id, registry=registry)

    assert prepared.cursor == "page-1"
    assert prepared.expected_version == 1
    assert prepared.config == {"variant": "primary"}
    assert prepared.secret_refs == {"token": "bao://integrator/poll/token"}


def test_invoke_materializes_secrets_without_passing_a_session(engine: Engine) -> None:
    event = InboundEvent("provider-1", "observed", {"amount": "1.00"})
    registry, plugin, checkpoint_id = _seed(engine, events=(event,))
    with Session(engine) as db:
        prepared = prepare_poll(db, checkpoint_id=checkpoint_id, registry=registry)

    batch = invoke_poll(
        prepared,
        registry=registry,
        resolve_secrets=lambda refs: {"token": "held-material"},
    )

    assert batch.events == (event,)
    assert batch.next_cursor == "page-2"
    assert plugin.cursors_seen == ["page-1"]
    assert plugin.configs_seen == [{"variant": "primary"}]
    assert plugin.secrets_seen == [{"token": "held-material"}]
    assert not any(
        isinstance(value, Session) for value in plugin.secrets_seen[0].values()
    )


def test_a_connector_exception_exposes_only_its_type(engine: Engine) -> None:
    registry, _, checkpoint_id = _seed(
        engine,
        poll_raises=RuntimeError("held-material provider-body"),
    )
    with Session(engine) as db:
        prepared = prepare_poll(db, checkpoint_id=checkpoint_id, registry=registry)

    with pytest.raises(PollConnectorRaised) as caught:
        invoke_poll(
            prepared,
            registry=registry,
            resolve_secrets=lambda refs: {"token": "held-material"},
        )

    rendered = str(caught.value)
    assert "RuntimeError" in rendered
    assert "held-material" not in rendered
    assert "provider-body" not in rendered


def test_a_wrong_poll_return_shape_is_refused_before_any_write(engine: Engine) -> None:
    registry, plugin, checkpoint_id = _seed(engine)
    object.__setattr__(plugin, "poll_contract_broken", True)
    with Session(engine) as db:
        prepared = prepare_poll(db, checkpoint_id=checkpoint_id, registry=registry)

    with pytest.raises(PollContractError):
        invoke_poll(
            prepared,
            registry=registry,
            resolve_secrets=lambda refs: {"token": "held-material"},
        )
    with Session(engine) as db:
        assert db.scalar(select(InboxReceipt)) is None


def test_poll_once_records_the_batch_and_then_advances_the_cursor(
    engine: Engine,
) -> None:
    events = (
        InboundEvent("provider-1", "observed", {"amount": "1.00"}),
        InboundEvent(
            "provider-2",
            "unsupported",
            {"reason": "unsupported"},
            disposition=InboundDisposition.RECORD_ONLY,
        ),
    )
    registry, _, checkpoint_id = _seed(engine, events=events)

    result = poll_once(
        checkpoint_id=checkpoint_id,
        registry=registry,
        resolve_secrets=lambda refs: {"token": "held-material"},
        unit_of_work=_unit_of_work(engine),
    )

    assert result.recorded == 2
    assert result.duplicates == 0
    assert result.checkpoint_version == 2
    with Session(engine) as db:
        checkpoint = db.get(PollingCheckpoint, checkpoint_id)
        assert checkpoint is not None
        assert checkpoint.cursor_json == {"cursor": "page-2"}
        receipts = db.scalars(
            select(InboxReceipt).order_by(InboxReceipt.provider_event_id)
        ).all()
        assert [receipt.provider_event_id for receipt in receipts] == [
            "provider-1",
            "provider-2",
        ]
        assert receipts[1].state == "processed"


def test_an_empty_poll_is_valid_and_advances_the_cursor(engine: Engine) -> None:
    registry, _, checkpoint_id = _seed(engine, events=(), next_cursor="idle-2")
    result = poll_once(
        checkpoint_id=checkpoint_id,
        registry=registry,
        resolve_secrets=lambda refs: {"token": "held-material"},
        unit_of_work=_unit_of_work(engine),
    )
    assert result.recorded == 0
    with Session(engine) as db:
        checkpoint = db.get(PollingCheckpoint, checkpoint_id)
        assert checkpoint is not None
        assert checkpoint.cursor_json == {"cursor": "idle-2"}


def test_a_receipt_collision_rolls_back_the_entire_batch_and_cursor(
    engine: Engine,
) -> None:
    conflict = InboundEvent("provider-1", "observed", {"amount": "2.00"})
    later = InboundEvent("provider-2", "observed", {"amount": "3.00"})
    registry, _, checkpoint_id = _seed(engine, events=(conflict, later))
    with Session(engine) as db:
        checkpoint = db.get(PollingCheckpoint, checkpoint_id)
        assert checkpoint is not None
        binding = db.get(CapabilityBinding, checkpoint.capability_binding_id)
        assert binding is not None
        db.add(
            InboxReceipt(
                id=uuid.uuid4(),
                installation_id=binding.installation_id,
                capability_binding_id=binding.id,
                provider_event_id="provider-1",
                event_type="observed",
                payload_digest=payload_digest({"amount": "1.00"}),
                payload_json={"amount": "1.00"},
                state="verified",
            )
        )
        db.commit()

    with pytest.raises(EventIdentityCollision):
        poll_once(
            checkpoint_id=checkpoint_id,
            registry=registry,
            resolve_secrets=lambda refs: {"token": "held-material"},
            unit_of_work=_unit_of_work(engine),
        )

    with Session(engine) as db:
        checkpoint = db.get(PollingCheckpoint, checkpoint_id)
        assert checkpoint is not None
        assert checkpoint.version == 1
        assert checkpoint.cursor_json == {"cursor": "page-1"}
        assert (
            db.scalar(
                select(InboxReceipt).where(
                    InboxReceipt.provider_event_id == "provider-2"
                )
            )
            is None
        )


def test_a_stale_worker_cannot_leave_receipts_past_its_lost_cursor(
    engine: Engine,
) -> None:
    first = InboundEvent("provider-1", "observed", {"n": 1})
    registry, _, checkpoint_id = _seed(engine, events=(first,))
    with Session(engine) as db:
        stale = prepare_poll(db, checkpoint_id=checkpoint_id, registry=registry)
    batch = invoke_poll(
        stale,
        registry=registry,
        resolve_secrets=lambda refs: {"token": "held-material"},
    )

    with Session(engine) as db:
        record_poll_batch(db, stale, batch)
        db.commit()

    second = InboundEvent("provider-2", "observed", {"n": 2})
    stale_batch = type(batch)(events=(second,), next_cursor="page-3")
    with Session(engine) as db, pytest.raises(CheckpointConflict):
        record_poll_batch(db, stale, stale_batch)
        db.commit()

    with Session(engine) as db:
        assert (
            db.scalar(
                select(InboxReceipt).where(
                    InboxReceipt.provider_event_id == "provider-2"
                )
            )
            is None
        )
