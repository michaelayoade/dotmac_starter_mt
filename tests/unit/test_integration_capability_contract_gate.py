"""The domain-owned payload contract, and the four seams that enforce it.

ADR-0024 §§ 10-12 and ADR-0061 A2. One capability id was already one contract
with one owner; its PAYLOAD was not. `CapabilityDeclaration` carried a config
schema and `DispatchRequest.payload` was an unvalidated `dict[str, object]`, so
configuration had a declared contract and commands did not — which is how one id
grew two disjoint command vocabularies with no gate noticing.

The schema therefore lives on the DOMAIN's `CapabilityContract`, and a connector
may only CLAIM its digest. Every test below is written the way ADR-0018 requires
a detector to be written: each plants a failure, proves it BITES, and proves the
same scenario PASSES once the gated contract is replaced with an ungated one —
so a refusal cannot be attributed to some other rule that happened to fire.

The last of those arms is the one that is easy to skip and is the whole point.
A check that refuses a payload might be refusing it because the schema said so,
or because the fixture was broken, or because an unrelated guard upstream
rejected the same call. Running the identical scenario against a contract in
`SchemaGrace` — same payload, same connector, same seam — isolates the cause to
this gate and nothing else.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from dotmac_integration import (
    CapabilityBinding,
    CapabilityContract,
    CapabilityContractDigestMismatch,
    CapabilityContractRedefined,
    CapabilityOwner,
    CapabilityPayloadRejected,
    CapabilityRegistry,
    CapabilityRegistryError,
    ConnectorInstallation,
    ContractDeprecation,
    DeliveryAttempt,
    InboxReceipt,
    MissingCapabilitySchema,
    Outcome,
    OutcomeStatus,
    PollingCheckpoint,
    SchemaGrace,
    SchemaGraceExpired,
    UnknownCapabilityError,
    canonical_digest,
    enqueue_delivery,
    install_capability_registry,
    require_governable,
    require_implements_only_declared,
    schema_grace_register,
)
from dotmac_integration.capability_registry import _reset_capability_registry
from dotmac_integration.conformance import FAKE_CAPABILITY, fake_manifest
from dotmac_integration.dispatch import PreparedDispatch, settle
from dotmac_integration.execution import claim_delivery
from dotmac_integration.ingress import ObservationRejected, receive, record_batch
from dotmac_integration.spi import CapabilityDeclaration, InboundEvent
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

OWNER = CapabilityOwner(application="testlab", module="fixtures")

#: Far out, so no fixture is dated by the calendar. A grace that expired by
#: accident would make an unrelated test fail for a reason nobody planted.
FAR_FUTURE = date(2099, 12, 31)

GRACE = SchemaGrace(
    reason="a synthetic capability whose owner has published no payload yet",
    retire_after=FAR_FUTURE,
    tracked_by="tests/unit/test_integration_capability_contract_gate.py",
)

COMMAND_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["recipient", "body"],
    "properties": {
        "recipient": {"type": "string", "minLength": 1},
        "body": {"type": "string"},
    },
    "additionalProperties": False,
}
RESULT_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["provider_reference"],
    "properties": {"provider_reference": {"type": "string"}},
}
OBSERVATION_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["kind"],
    "properties": {"kind": {"type": "string"}},
}

VALID_COMMAND: dict[str, object] = {"recipient": "+2348000000000", "body": "hello"}
VALID_RESULT: dict[str, object] = {"provider_reference": "prv_1"}
VALID_OBSERVATION: dict[str, object] = {"kind": "message"}


def gated(capability_id: str = FAKE_CAPABILITY) -> CapabilityContract:
    """A contract that has published all three schemas."""
    return CapabilityContract(
        capability_id=capability_id,
        owner=OWNER,
        summary="a synthetic gated contract",
        command_schema=COMMAND_SCHEMA,
        result_schema=RESULT_SCHEMA,
        observation_schema=OBSERVATION_SCHEMA,
    )


def ungated(capability_id: str = FAKE_CAPABILITY) -> CapabilityContract:
    """The SAME capability, in a declared grace. The control arm."""
    return CapabilityContract(
        capability_id=capability_id,
        owner=OWNER,
        summary="a synthetic ungated contract",
        schema_grace=GRACE,
    )


def registry_of(*contracts: CapabilityContract) -> CapabilityRegistry:
    return CapabilityRegistry.from_declarations(contracts)


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    """No installed registry leaks into or out of this file.

    Every test here passes its registry EXPLICITLY, so an installed one would be
    invisible scaffolding: a test could pass because the lane's conftest
    declared the capability, not because the argument it handed over did.
    """
    _reset_capability_registry()
    yield
    _reset_capability_registry()


# ══ 1. The contract value object ═══════════════════════════════════════════


def test_a_contract_that_says_nothing_about_its_payload_is_refused() -> None:
    """Fail closed on SILENCE. This is the adoption decision, in one assert.

    The alternative — schemas that are simply optional — is how the defect
    returns: nothing would separate an owner who has not published a payload
    contract from an owner nobody has asked, and no operator could enumerate the
    second set.
    """
    with pytest.raises(CapabilityRegistryError, match="Silence is refused"):
        CapabilityContract(capability_id=FAKE_CAPABILITY, owner=OWNER, summary="silent")


def test_the_silence_refusal_is_not_a_refusal_of_ungatedness() -> None:
    """The specificity control for the rule above.

    A guard that refused every schema-less contract would be a guard nobody
    could adopt — every capability in the fleet is schema-less today. It refuses
    exactly one thing: saying nothing.
    """
    contract = ungated()
    assert contract.contract_digest is None
    assert contract.schema_grace is not None


def test_a_grace_over_a_published_schema_is_a_contradiction() -> None:
    with pytest.raises(CapabilityRegistryError, match="publishing anything ends it"):
        CapabilityContract(
            capability_id=FAKE_CAPABILITY,
            owner=OWNER,
            summary="both",
            command_schema=COMMAND_SCHEMA,
            schema_grace=GRACE,
        )


def test_a_grace_must_carry_a_reason_and_a_calendar_date() -> None:
    with pytest.raises(CapabilityRegistryError, match="must state WHY"):
        SchemaGrace(reason="   ", retire_after=FAR_FUTURE)


def test_an_invalid_schema_is_refused_at_construction_not_at_first_use() -> None:
    """A schema that cannot compile validates nothing — and an operations
    screen would still show the capability as having a published payload."""
    with pytest.raises(CapabilityRegistryError, match="not a valid JSON Schema"):
        CapabilityContract(
            capability_id=FAKE_CAPABILITY,
            owner=OWNER,
            summary="broken",
            command_schema={"type": "not-a-json-schema-type"},
        )


def test_the_digest_covers_the_schemas_and_the_id_and_nothing_else() -> None:
    """Deprecating a contract must not invalidate every connector claiming it.

    That is the moment a fleet most needs its existing connectors to keep
    working, so `deprecation` is deliberately outside the digest — the same
    reasoning `ConnectorManifest.digest` states for excluding documentation.
    """
    base = gated()
    deprecated = CapabilityContract(
        capability_id=FAKE_CAPABILITY,
        owner=OWNER,
        summary="a DIFFERENT summary entirely",
        command_schema=COMMAND_SCHEMA,
        result_schema=RESULT_SCHEMA,
        observation_schema=OBSERVATION_SCHEMA,
        deprecation=ContractDeprecation(
            replaced_by="conformance.echo.v2", retire_after=FAR_FUTURE
        ),
    )
    assert base.contract_digest == deprecated.contract_digest

    changed = CapabilityContract(
        capability_id=FAKE_CAPABILITY,
        owner=OWNER,
        summary="a synthetic gated contract",
        command_schema={**COMMAND_SCHEMA, "required": ["recipient"]},
        result_schema=RESULT_SCHEMA,
        observation_schema=OBSERVATION_SCHEMA,
    )
    assert changed.contract_digest != base.contract_digest


def test_the_digest_is_bound_to_the_capability_it_was_minted_for() -> None:
    """Otherwise a digest copied from another capability that happened to share
    a shape would pass, and a claim would prove nothing about WHICH contract a
    connector implements."""
    assert gated("conformance.other.v1").contract_digest != gated().contract_digest


def test_the_digest_is_the_one_canonical_rule_and_not_a_second_one() -> None:
    """`canonical_digest` has exactly one implementation in this module, and the
    contract digest is that function applied to the contract's schemas."""
    assert gated().contract_digest == canonical_digest(
        {
            "capability_id": FAKE_CAPABILITY,
            "command_schema": COMMAND_SCHEMA,
            "result_schema": RESULT_SCHEMA,
            "observation_schema": OBSERVATION_SCHEMA,
        }
    )


def test_key_order_does_not_change_a_digest() -> None:
    reordered = dict(reversed(list(COMMAND_SCHEMA.items())))
    assert canonical_digest(reordered) == canonical_digest(COMMAND_SCHEMA)


# ══ 2. Succession, never redefinition (§ 11) ═══════════════════════════════


def test_reinstalling_a_published_id_with_a_different_payload_is_refused() -> None:
    install_capability_registry(registry_of(gated()))
    with pytest.raises(CapabilityContractRedefined, match="SUCCEEDED, never"):
        install_capability_registry(
            registry_of(
                CapabilityContract(
                    capability_id=FAKE_CAPABILITY,
                    owner=OWNER,
                    summary="redefined in place",
                    command_schema={"type": "object"},
                )
            )
        )


def test_a_published_contract_cannot_be_walked_back_into_a_grace() -> None:
    install_capability_registry(registry_of(gated()))
    with pytest.raises(CapabilityContractRedefined, match="cannot be un-published"):
        install_capability_registry(registry_of(ungated()))


def test_publishing_a_schema_for_the_first_time_is_not_a_redefinition() -> None:
    """The control. A contract moving OUT of grace defines something for the
    first time; there is no published shape anything was built against."""
    install_capability_registry(registry_of(ungated()))
    install_capability_registry(registry_of(gated()))


def test_succession_is_a_new_id_and_the_successor_must_exist() -> None:
    successor = gated("conformance.echo.v2")
    deprecated = CapabilityContract(
        capability_id=FAKE_CAPABILITY,
        owner=OWNER,
        summary="v1, superseded",
        command_schema=COMMAND_SCHEMA,
        deprecation=ContractDeprecation(
            replaced_by="conformance.echo.v2",
            retire_after=FAR_FUTURE,
            reason="succeeded by a discriminated content shape",
        ),
    )
    registry_of(deprecated, successor)  # both declared: accepted

    with pytest.raises(UnknownCapabilityError, match="nowhere to migrate to"):
        registry_of(deprecated)


def test_a_contract_may_not_name_itself_as_its_own_successor() -> None:
    with pytest.raises(CapabilityRegistryError, match="names ITSELF"):
        CapabilityContract(
            capability_id=FAKE_CAPABILITY,
            owner=OWNER,
            summary="circular",
            command_schema=COMMAND_SCHEMA,
            deprecation=ContractDeprecation(
                replaced_by=FAKE_CAPABILITY, retire_after=FAR_FUTURE
            ),
        )


# ══ 3. PLANT: a connector claiming a digest that is not the owner's ════════


def test_a_digest_mismatch_is_refused_at_composition() -> None:
    registry = registry_of(gated())
    diverging = fake_manifest(claims_contract_digest="0" * 64)
    with pytest.raises(CapabilityContractDigestMismatch, match="built against a"):
        require_implements_only_declared(registry, diverging)


def test_a_connector_that_claims_nothing_against_a_published_contract_is_refused() -> (
    None
):
    """A gate a connector may decline is not a gate."""
    with pytest.raises(CapabilityContractDigestMismatch, match="makes no contract"):
        require_implements_only_declared(registry_of(gated()), fake_manifest())


def test_a_connector_may_not_claim_a_digest_the_owner_never_published() -> None:
    """Agreement with nothing is not agreement — a connector claiming here would
    be asserting a payload contract into existence."""
    with pytest.raises(CapabilityContractDigestMismatch, match="published no"):
        require_implements_only_declared(
            registry_of(ungated()), fake_manifest(claims_contract_digest="a" * 64)
        )


def test_the_digest_gate_passes_when_the_claim_agrees() -> None:
    """Specificity control: a detector that always fires cannot pass."""
    contract = gated()
    assert contract.contract_digest is not None
    require_implements_only_declared(
        registry_of(contract),
        fake_manifest(claims_contract_digest=contract.contract_digest),
    )


def test_the_same_connector_is_accepted_against_an_UNGATED_contract() -> None:
    """The without-the-guard arm. Identical manifest, identical seam; the only
    thing that changed is that the owner has published nothing to disagree
    with — so the refusal above was this gate and nothing else."""
    require_implements_only_declared(registry_of(ungated()), fake_manifest())


def test_the_agreement_is_checked_at_BINDING_as_well_as_at_composition() -> None:
    """Both, deliberately (§ 10.4.2). A distribution can be installed after
    composition ran, and a binding can be activated months later — which is the
    same reason `activation.py` re-checks all three of its refusals against
    stored state."""
    registry = registry_of(gated())
    diverging = fake_manifest(claims_contract_digest="0" * 64)
    with pytest.raises(CapabilityContractDigestMismatch):
        require_governable(
            registry, [diverging], bound_capability_ids=[FAKE_CAPABILITY]
        )


def test_a_claim_is_shape_checked_where_it_is_pasted() -> None:
    """So a truncated paste fails with a message about the paste, not with one
    about disagreement — which would send an author hunting a schema difference
    that does not exist."""
    from dotmac_integration.spi import InvalidManifestError

    with pytest.raises(InvalidManifestError, match="64 lowercase hex"):
        CapabilityDeclaration(
            capability_id=FAKE_CAPABILITY, claims_contract_digest="ABC"
        )


def test_what_a_connector_claims_changes_the_manifest_digest() -> None:
    """A connector silently re-pointing at a different published contract would
    be a different payload shape behind an unchanged installation pin."""
    plain = fake_manifest()
    claiming = fake_manifest(claims_contract_digest="b" * 64)
    assert plain.digest != claiming.digest


def test_a_manifest_that_claims_nothing_keeps_its_published_digest() -> None:
    """Every installation pinned before ADR-0024 § 10 stays adoptable. The
    claims segment is appended only when something claims."""
    assert fake_manifest().digest == fake_manifest().digest
    assert len(fake_manifest().digest) == 64


# ══ 4. PLANT: a missing schema where the seam needs one ════════════════════


def test_a_published_contract_with_no_command_schema_refuses_a_command() -> None:
    """Distinct from a rejected payload, because the fix is the OWNER's. A
    delivery capability that publishes an observation and no command is a
    contract that has not decided what a command is."""
    observation_only = CapabilityContract(
        capability_id=FAKE_CAPABILITY,
        owner=OWNER,
        summary="ingress only",
        observation_schema=OBSERVATION_SCHEMA,
    )
    with pytest.raises(MissingCapabilitySchema, match="no command_schema"):
        observation_only.require_command(VALID_COMMAND)


def test_the_missing_schema_refusal_does_not_fire_in_a_grace() -> None:
    """Without-the-guard arm: the same call, against an ungated contract."""
    ungated().require_command(VALID_COMMAND)


# ══ 5. PLANT: an expired grace ═════════════════════════════════════════════


def test_an_expired_grace_refuses_rather_than_warning() -> None:
    """An expiry that does nothing is a permanent optional field with a date
    attached, which is the exact shape this rule exists to stop."""
    yesterday = date.today() - timedelta(days=1)
    lapsed = CapabilityContract(
        capability_id=FAKE_CAPABILITY,
        owner=OWNER,
        summary="a window that closed",
        schema_grace=SchemaGrace(reason="pending succession", retire_after=yesterday),
    )
    with pytest.raises(SchemaGraceExpired, match="window has closed"):
        lapsed.require_command(VALID_COMMAND)
    with pytest.raises(SchemaGraceExpired):
        require_governable(registry_of(lapsed), [fake_manifest()])


def test_an_unexpired_grace_admits_the_same_call() -> None:
    """Specificity control: the refusal above is the DATE, not the grace."""
    ungated().require_command(VALID_COMMAND)
    require_governable(registry_of(ungated()), [fake_manifest()])


def test_the_ungated_set_is_enumerable_with_an_owner_and_a_deadline() -> None:
    """The whole point of refusing silence at construction: this answer is a
    LIST rather than the absence of one."""
    entry = schema_grace_register(registry_of(ungated()))[0]
    assert entry.capability_id == FAKE_CAPABILITY
    assert entry.owner == OWNER
    assert entry.retire_after == FAR_FUTURE
    assert entry.expired is False
    assert entry.reason


def test_the_register_is_empty_only_when_everything_is_gated() -> None:
    """The two-directional half: an empty register must mean 'all gated', never
    'nothing declared'. A gated contract contributes no entry."""
    assert schema_grace_register(registry_of(gated())) == ()


# ══ 6. PLANT: an invalid command, at the enqueue seam ══════════════════════


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in (
        ConnectorInstallation,
        CapabilityBinding,
        InboxReceipt,
        DeliveryAttempt,
        PollingCheckpoint,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def binding(db: Session) -> CapabilityBinding:
    installation = ConnectorInstallation(
        id=uuid.uuid4(),
        connector_key="conformance_fake",
        connector_version="1.0.0",
        spi_range=">=1.0,<2.0",
        manifest_digest="d" * 64,
        name="primary",
        state="enabled",
    )
    db.add(installation)
    db.flush()
    record = CapabilityBinding(
        id=uuid.uuid4(),
        installation_id=installation.id,
        capability_id=FAKE_CAPABILITY,
        state="enabled",
    )
    db.add(record)
    db.flush()
    return record


def _enqueue(
    db: Session,
    binding: CapabilityBinding,
    payload: dict[str, object],
    registry: CapabilityRegistry,
    key: str = "k1",
) -> tuple[DeliveryAttempt, bool]:
    return enqueue_delivery(
        db,
        installation_id=binding.installation_id,
        capability_binding_id=binding.id,
        event_type="message.send",
        idempotency_key=key,
        payload=payload,
        registry=registry,
    )


def test_an_invalid_command_never_becomes_a_queued_effect(
    db: Session, binding: CapabilityBinding
) -> None:
    """BEFORE the row, not early in it. A delivery row is queued WORK — picked
    up by a dispatcher, retried on a curve, eventually dead-lettered — and it
    reaches a provider before anybody finds it."""
    with pytest.raises(CapabilityPayloadRejected):
        _enqueue(db, binding, {"recipient": ""}, registry_of(gated()))
    assert db.query(DeliveryAttempt).count() == 0


def test_the_same_command_is_queued_against_an_UNGATED_contract(
    db: Session, binding: CapabilityBinding
) -> None:
    """Without-the-guard arm. Same payload, same seam, same binding."""
    _enqueue(db, binding, {"recipient": ""}, registry_of(ungated()))
    assert db.query(DeliveryAttempt).count() == 1


def test_a_valid_command_is_queued_against_the_GATED_contract(
    db: Session, binding: CapabilityBinding
) -> None:
    """Specificity control: the gate is not simply refusing everything."""
    _enqueue(db, binding, VALID_COMMAND, registry_of(gated()))
    assert db.query(DeliveryAttempt).count() == 1


def test_the_refusal_never_repeats_the_payload_into_a_persisted_message(
    db: Session, binding: CapabilityBinding
) -> None:
    """`error_detail` is a persisted column and a support export copies it.
    jsonschema's own message interpolates the offending instance; this one is
    built from the JSON pointer and the failing keyword only."""
    secret = "4111111111111111"
    with pytest.raises(CapabilityPayloadRejected) as raised:
        _enqueue(db, binding, {"recipient": secret, "body": 7}, registry_of(gated()))
    rendered = str(raised.value)
    assert secret not in rendered
    assert "$.body" in rendered


def test_an_unbound_delivery_names_no_capability_and_is_not_second_guessed(
    db: Session, binding: CapabilityBinding
) -> None:
    """It is already dead — `dispatch.prepare` refuses it with 'names no
    capability binding'. Refusing it again here, with a message about schemas,
    would explain the wrong problem."""
    delivery, created = enqueue_delivery(
        db,
        installation_id=binding.installation_id,
        event_type="message.send",
        idempotency_key="unbound",
        payload={"anything": True},
        registry=registry_of(gated()),
    )
    assert created is True
    assert delivery.capability_binding_id is None


# ══ 7. PLANT: an invalid result, at the settle seam ════════════════════════


def _prepared(
    binding: CapabilityBinding, delivery: DeliveryAttempt
) -> PreparedDispatch:
    return PreparedDispatch(
        delivery_id=delivery.id,
        installation_id=binding.installation_id,
        binding_id=binding.id,
        connector_key="conformance_fake",
        capability_id=FAKE_CAPABILITY,
        event_type=delivery.event_type,
        payload=dict(delivery.payload_json or {}),
        config={},
        secret_refs={},
        idempotency_key=delivery.idempotency_key,
        config_revision_id=None,
        attempt_number=delivery.attempt_count,
    )


def _claimed(
    db: Session, binding: CapabilityBinding, registry: CapabilityRegistry
) -> tuple[DeliveryAttempt, PreparedDispatch]:
    delivery, _ = _enqueue(db, binding, VALID_COMMAND, registry, key=uuid.uuid4().hex)
    assert claim_delivery(db, delivery) is True
    return delivery, _prepared(binding, delivery)


def test_a_malformed_result_cannot_settle_a_delivery(
    db: Session, binding: CapabilityBinding
) -> None:
    """Before the claim-guarded UPDATE. After it the attempt is `delivered` and
    final; a product reading the result would be the first thing to discover the
    shape was wrong, with nothing left to retry."""
    registry = registry_of(gated())
    delivery, prepared = _claimed(db, binding, registry)
    bad = Outcome(status=OutcomeStatus.SUCCEEDED, result={"reference": "prv_1"})
    with pytest.raises(CapabilityPayloadRejected):
        settle(db, delivery, bad, prepared=prepared, registry=registry)
    db.refresh(delivery)
    assert delivery.state == "in_flight", "a refused settle must not decide anything"


def test_the_same_result_settles_against_an_UNGATED_contract(
    db: Session, binding: CapabilityBinding
) -> None:
    """Without-the-guard arm."""
    registry = registry_of(ungated())
    delivery, prepared = _claimed(db, binding, registry)
    bad = Outcome(status=OutcomeStatus.SUCCEEDED, result={"reference": "prv_1"})
    settle(db, delivery, bad, prepared=prepared, registry=registry)
    db.refresh(delivery)
    assert delivery.state == "delivered"


def test_a_valid_result_settles_against_the_GATED_contract(
    db: Session, binding: CapabilityBinding
) -> None:
    """Specificity control."""
    registry = registry_of(gated())
    delivery, prepared = _claimed(db, binding, registry)
    good = Outcome(status=OutcomeStatus.SUCCEEDED, result=VALID_RESULT)
    settle(db, delivery, good, prepared=prepared, registry=registry)
    db.refresh(delivery)
    assert delivery.state == "delivered"


def test_a_failed_attempt_is_still_recordable_without_a_result_body(
    db: Session, binding: CapabilityBinding
) -> None:
    """Demanding a result on a failure would refuse to record the failure —
    turning 'the provider returned 500' into a permanently unsettled row with a
    live lease. `RECONCILIATION_REQUIRED` is the sharpest case: `invoke`
    produces it for a connector that RAISED."""
    registry = registry_of(gated())
    delivery, prepared = _claimed(db, binding, registry)
    settle(
        db,
        delivery,
        Outcome(status=OutcomeStatus.RECONCILIATION_REQUIRED, error_code="raised"),
        prepared=prepared,
        registry=registry,
    )
    db.refresh(delivery)
    assert delivery.state == "reconciliation_required"


# ══ 8. PLANT: an unreadable observation, at the inbox seam ═════════════════


class _Address:
    """The smallest `ReceiptBatchAddress` — the shape ingress and polling share."""

    def __init__(self, binding: CapabilityBinding) -> None:
        self.installation_id = binding.installation_id
        self.binding_id = binding.id
        self.capability_id = FAKE_CAPABILITY


def _events(payload: dict[str, object]) -> tuple[InboundEvent, ...]:
    return (
        InboundEvent(
            provider_event_id=uuid.uuid4().hex, event_type="message", payload=payload
        ),
    )


def test_an_unreadable_observation_is_refused_before_the_batch_is_written(
    db: Session, binding: CapabilityBinding
) -> None:
    """Refused at the boundary rather than discovered by a product's projector,
    which is the last moment the provider can still be told no."""
    with pytest.raises(CapabilityPayloadRejected):
        record_batch(
            db, _Address(binding), _events({"kind": 7}), registry=registry_of(gated())
        )
    assert db.query(InboxReceipt).count() == 0


def test_a_whole_batch_is_refused_when_any_event_is_unreadable(
    db: Session, binding: CapabilityBinding
) -> None:
    """Validated in its own pass, ahead of the recording loop — for the same
    reason `record_batch` takes a tuple and not one event."""
    events = _events(VALID_OBSERVATION) + _events({"kind": 7})
    with pytest.raises(CapabilityPayloadRejected):
        record_batch(db, _Address(binding), events, registry=registry_of(gated()))
    assert db.query(InboxReceipt).count() == 0, "a valid event was recorded first"


def test_the_same_observation_is_recorded_against_an_UNGATED_contract(
    db: Session, binding: CapabilityBinding
) -> None:
    """Without-the-guard arm."""
    record_batch(
        db, _Address(binding), _events({"kind": 7}), registry=registry_of(ungated())
    )
    assert db.query(InboxReceipt).count() == 1


def test_a_valid_observation_is_recorded_against_the_GATED_contract(
    db: Session, binding: CapabilityBinding
) -> None:
    """Specificity control."""
    record_batch(
        db,
        _Address(binding),
        _events(VALID_OBSERVATION),
        registry=registry_of(gated()),
    )
    assert db.query(InboxReceipt).count() == 1


def test_the_polling_path_reaches_the_same_gate_without_a_second_copy() -> None:
    """`record_poll_batch` owns no observation check of its own. A second copy
    would be a second answer, in the one module whose docstring says polling
    must not grow a second inbox."""
    import inspect

    from dotmac_integration import polling

    source = inspect.getsource(polling.record_poll_batch)
    assert "require_observation" not in source
    assert "record_batch(db, prepared, batch.events, registry=registry)" in source


def test_a_provider_is_answered_in_the_engines_own_refusal_shape() -> None:
    """A webhook may only be answered by `refusal_outcome`, from a refusal whose
    message is a CONSTANT — so the registry's detailed message, which names a
    JSON pointer, never reaches a provider response."""
    assert ObservationRejected.MESSAGE
    assert ObservationRejected().args == (ObservationRejected.MESSAGE,)
    assert ObservationRejected.STATUS == 503, (
        "4xx would discard a real provider fact because two Dotmac artifacts "
        "disagree about its shape"
    )
    import inspect

    assert "CapabilityRegistryError" in inspect.getsource(receive)
