"""The ingress engine, held to the four properties that make it safe.

1. **The URL addresses ONE minted endpoint.** Not a `(connector_key,
   capability_id)` pair — several installations may serve one such pair, so the
   pair names a set rather than an endpoint — and not the binding's primary key,
   which is neither minted nor rotatable and is already disclosed in
   operator-facing error text.
2. **No session is live across the plugin call.** `verify_and_normalize` has no
   `db` parameter, so the boundary is enforced by what a caller CANNOT pass.
3. **One collision rolls back the WHOLE provider batch.** A partial write leaves
   the provider believing the batch was accepted while some events were never
   recorded, and it will not resend the ones that landed.
4. **The raw body, the headers and the secrets never escape.** Not into a
   refusal's message, not into the returned outcome, not into a stored receipt.

Each test names the damage it prevents, and the vacuity guards are as
load-bearing as the assertions: "nothing was written" passes trivially if
nothing ever writes, and "every event was recorded" passes trivially on an
empty batch.
"""

from __future__ import annotations

import inspect
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from typing import Any

import pytest
from dotmac_integration import (
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorContract,
    ConnectorInstallation,
    ConnectorRaised,
    ConnectorUnavailable,
    DeliveryAttempt,
    EndpointNotServiceable,
    EndpointNotUsable,
    EndpointUnknown,
    EventIdentityCollision,
    EventSubscription,
    InboxReceipt,
    IngressCode,
    IngressOutcome,
    IngressRefused,
    LifecycleError,
    ManifestPinUnhonoured,
    ModeNotAvailable,
    NotAChallenge,
    PayloadTooLarge,
    PollingCheckpoint,
    PreparedIngress,
    ReceiptWriteFailed,
    ReceiptWriteRaced,
    SignatureRejected,
    add_binding,
    answer_challenge,
    create_draft,
    enable,
    mint_ingress_endpoint,
    prepare_ingress,
    receive,
    record_batch,
    refusal_outcome,
    revoke_ingress_endpoint,
    rotate_ingress_endpoint,
    set_binding_enabled,
    verify_and_normalize,
)
from dotmac_integration.conformance import FAKE_CAPABILITY, fake_plugin, fake_registry
from dotmac_integration.spi import ConnectorMode, InboundEvent
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

#: Distinctive strings planted in every input a refusal could conceivably echo.
#: Long and unmistakable, so a leak is a substring match rather than a judgement
#: call about whether some fragment "looks like" the body.
BODY_SENTINEL = b"SENTINEL-RAW-BODY-8f2a"
HEADER_SENTINEL = "SENTINEL-SIGNATURE-9c1d"
PARAM_SENTINEL = "SENTINEL-QUERY-PARAM-4b7e"
SECRET_SENTINEL = "SENTINEL-MATERIALIZED-SECRET-1a5f"

#: Every platform table this module owns. Row counts are swept across ALL of
#: them, so "nothing was persisted" cannot be true of the inbox while a new
#: table for unverified bodies quietly appears beside it.
ALL_MODELS = (
    ConnectorInstallation,
    ConnectorConfigRevision,
    CapabilityBinding,
    EventSubscription,
    InboxReceipt,
    DeliveryAttempt,
    PollingCheckpoint,
)


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in ALL_MODELS:
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session


class RecordingUnitOfWork:
    """A unit of work that COMMITS on a clean exit and unwinds on an exception.

    Injected rather than composed in the module, because whole-batch atomicity
    is a correctness property of ingress: hand-composed by a caller it would be
    enforced only by that caller getting it right, and the caller is a different
    repository.

    Every entry, exit, commit and rollback is recorded, which is how the
    collision tests prove the transaction unwound rather than inferring it from
    a row count that a subsequent write could have restored.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.entered = 0
        self.exited = 0
        self.committed = 0
        self.rolled_back = 0
        #: A marker appended at each boundary, so a test can assert the plugin
        #: call fell BETWEEN two units of work rather than inside one.
        self.timeline: list[str] = []

    def __call__(self):  # type: ignore[no-untyped-def]
        return self._unit()

    @contextmanager
    def _unit(self) -> Iterator[Session]:
        self.entered += 1
        self.timeline.append("enter")
        try:
            yield self.session
        except BaseException:
            self.rolled_back += 1
            self.timeline.append("rollback")
            self.session.rollback()
            raise
        self.committed += 1
        self.timeline.append("commit")
        self.session.commit()


class SpySession:
    """A session that records every call and issues none.

    Used only to prove that a malformed endpoint key never reaches SQL: an
    unbounded attacker-supplied string must be refused against a fixed shape
    BEFORE a query is built from it.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("execute")
        raise AssertionError("a query was issued for a malformed endpoint key")

    def get(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("get")
        raise AssertionError("a row was fetched for a malformed endpoint key")


def _resolver(values: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    """A secret resolver that counts its calls.

    Secrets must be materialized ONCE, in the phase with no session, and must
    not be re-resolved per event.
    """
    resolved = values if values is not None else {"signing": SECRET_SENTINEL}
    calls: list[dict] = []

    def resolve(refs):  # type: ignore[no-untyped-def]
        calls.append(dict(refs))
        return dict(resolved)

    resolve.calls = calls  # type: ignore[attr-defined]
    return resolve


def _installed(
    db: Session,
    registry,
    *,
    name: str = "primary",
    config: dict | None = None,
    secret_refs: dict | None = None,
) -> tuple[ConnectorInstallation, CapabilityBinding]:
    from dotmac_integration import put_config_revision

    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name=name
    )
    put_config_revision(
        db,
        installation,
        config=config or {"variant": "a"},
        secret_refs=secret_refs or {"signing": "bao://kv/signing"},
    )
    enable(db, installation, registry=registry)
    binding = add_binding(
        db, installation, registry=registry, capability_id=FAKE_CAPABILITY
    )
    set_binding_enabled(db, installation, binding, registry=registry, enabled=True)
    return installation, binding


def _endpoint(db: Session, registry, **kwargs: Any) -> tuple[Any, Any, str]:
    installation, binding = _installed(db, registry, **kwargs)
    key = mint_ingress_endpoint(db, binding, registry=registry)
    return installation, binding, key


def _events(*ids: str) -> tuple[InboundEvent, ...]:
    return tuple(
        InboundEvent(provider_event_id=i, event_type="thing.happened", payload={"i": i})
        for i in ids
    )


def _row_counts(db: Session) -> dict[str, int]:
    return {
        model.__tablename__: db.execute(
            select(func.count()).select_from(model)
        ).scalar_one()
        for model in ALL_MODELS
    }


def _headers() -> dict[str, str]:
    return {"signature": HEADER_SENTINEL, "content-type": "application/json"}


def assert_every_event_recorded(
    db: Session, binding: CapabilityBinding, events: tuple[InboundEvent, ...]
) -> None:
    """The shared batch assertion, written so it CANNOT pass vacuously.

    `count == len(events)` is satisfied by `0 == 0`, which is exactly what a
    future refactor that made `normalize` return nothing would produce. So the
    helper requires a non-empty batch of its own accord, and
    `test_the_all_recorded_assertion_is_not_satisfied_by_an_empty_batch` calls
    it with `()` to prove the guard is live.
    """
    assert events, (
        "assert_every_event_recorded was handed an empty batch — 'all recorded' "
        "would be vacuously true"
    )
    stored = set(
        db.execute(
            select(InboxReceipt.provider_event_id).where(
                InboxReceipt.capability_binding_id == binding.id
            )
        )
        .scalars()
        .all()
    )
    assert stored == {e.provider_event_id for e in events}


# ── Addressing ──────────────────────────────────────────────────────────────


def test_the_endpoint_key_resolves_the_whole_chain_in_one_transaction(
    db: Session,
) -> None:
    """One lookup must yield installation, connector, capability, pin, config.

    Returning only the binding would force the caller to re-query installation
    and revision AFTER the transaction closed — which is where a lazy-load
    against a dead session, or a config read that straddles a revision change,
    comes from.
    """
    registry = fake_registry()
    installation, binding, key = _endpoint(db, registry)

    prepared = prepare_ingress(db, endpoint_id=key, registry=registry)

    assert prepared.installation_id == installation.id
    assert prepared.binding_id == binding.id
    assert prepared.connector_key == "conformance_fake"
    assert prepared.capability_id == FAKE_CAPABILITY
    assert prepared.manifest_digest == installation.manifest_digest
    assert prepared.config == {"variant": "a"}
    assert prepared.secret_refs == {"signing": "bao://kv/signing"}
    assert prepared.config_revision_id == installation.current_config_revision_id


def test_an_unminted_binding_has_no_endpoint(db: Session) -> None:
    """Minting is a deliberate act, not a property every binding has.

    With the primary key as the address, EVERY binding in the fleet would carry
    a live ingress URL — delivery-only ones included — and the PK is already
    interpolated into operator-facing error text.
    """
    registry = fake_registry()
    _, binding = _installed(db, registry)

    assert binding.ingress_endpoint_key is None

    with pytest.raises(EndpointUnknown):
        prepare_ingress(db, endpoint_id=str(binding.id), registry=registry)


def test_two_installations_of_one_connector_get_distinct_endpoints(
    db: Session,
) -> None:
    """The reason `(connector_key, capability_id)` cannot be the address.

    A production and a test provider account are two installations of one
    connector serving one capability. Addressed by the pair they collapse onto
    one endpoint, and traffic for one lands against the other's configuration.
    """
    registry = fake_registry()
    _, first, first_key = _endpoint(db, registry, name="production", config={"n": 1})
    _, second, second_key = _endpoint(db, registry, name="sandbox", config={"n": 2})

    assert first.capability_id == second.capability_id
    assert first_key != second_key

    assert prepare_ingress(db, endpoint_id=first_key, registry=registry).config == {
        "n": 1
    }
    assert prepare_ingress(db, endpoint_id=second_key, registry=registry).config == {
        "n": 2
    }


def test_a_malformed_endpoint_key_is_indistinguishable_from_an_unknown_one() -> None:
    """Two failures at once, and both matter.

    An unbounded attacker string must never reach SQL, and a distinct status or
    code for "malformed" would tell a scanner when it had guessed the right
    SHAPE — turning the endpoint into a probing oracle.
    """
    spy = SpySession()
    registry = fake_registry()

    for malformed in ("", "x", "../etc/passwd", "A" * 5000, "g" * 48, "0" * 47):
        with pytest.raises(EndpointUnknown):
            prepare_ingress(spy, endpoint_id=malformed, registry=registry)

    assert spy.calls == [], "a malformed endpoint key reached the database"
    assert (
        refusal_outcome(EndpointUnknown()).code is IngressCode.ENDPOINT_UNKNOWN
    ), "malformed and unknown must be the same answer to an outside observer"


def test_rotation_retires_the_old_endpoint_and_keeps_the_receipts(
    db: Session,
) -> None:
    """The property the primary key cannot offer.

    The PK is FK-referenced `ON DELETE CASCADE` from three tables, so rotating
    it after a leak would mean rewriting the inbox history. Receipts are keyed
    on the BINDING, so rotating this column keeps every one of them.
    """
    registry = fake_registry()
    plugin = fake_plugin(inbound=_events("evt_1"))
    registry = fake_registry(plugins=[plugin])
    _, binding, original = _endpoint(db, registry)
    uow = RecordingUnitOfWork(db)

    accepted = receive(
        uow,
        endpoint_id=original,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=registry,
        resolve_secrets=_resolver(),
    )
    assert accepted.code is IngressCode.ACCEPTED

    rotated = rotate_ingress_endpoint(db, binding)
    db.commit()

    assert rotated != original
    with pytest.raises(EndpointUnknown):
        prepare_ingress(db, endpoint_id=original, registry=registry)
    assert prepare_ingress(db, endpoint_id=rotated, registry=registry).binding_id == (
        binding.id
    )
    assert _row_counts(db)["inbox_receipts"] == 1, "rotation destroyed the history"

    revoke_ingress_endpoint(db, binding)
    db.flush()
    with pytest.raises(EndpointUnknown):
        prepare_ingress(db, endpoint_id=rotated, registry=registry)
    assert _row_counts(db)["inbox_receipts"] == 1, "revocation destroyed the history"


def test_minting_refuses_a_connector_that_does_not_declare_ingress(
    db: Session,
) -> None:
    """An endpoint whose only possible answer is 503 must not be handed out."""
    delivery_only = fake_plugin(modes_=frozenset({ConnectorMode.DELIVERY}))
    registry = fake_registry(plugins=[delivery_only])
    _, binding = _installed(db, registry)

    with pytest.raises(LifecycleError):
        mint_ingress_endpoint(db, binding, registry=registry)
    assert binding.ingress_endpoint_key is None


def test_minting_twice_is_refused_rather_than_silently_rotating(db: Session) -> None:
    """Minting and rotating are different intentions.

    One says "this binding should be reachable"; the other says "the address it
    publishes has been compromised". Only the second may retire a URL that lives
    in a third party's console, so the first must not do it by accident.
    """
    registry = fake_registry()
    _, binding, key = _endpoint(db, registry)

    with pytest.raises(LifecycleError):
        mint_ingress_endpoint(db, binding, registry=registry)
    assert binding.ingress_endpoint_key == key


def test_rotating_an_unminted_binding_is_refused(db: Session) -> None:
    """Rotation carries no mode gate, so it must not be able to CREATE an
    endpoint — that would be minting through the one door with no check."""
    registry = fake_registry()
    _, binding = _installed(db, registry)

    with pytest.raises(LifecycleError):
        rotate_ingress_endpoint(db, binding)
    assert binding.ingress_endpoint_key is None


# ── Refusals ────────────────────────────────────────────────────────────────


def test_a_disabled_binding_refuses_with_endpoint_not_usable(db: Session) -> None:
    """`selection`'s usability rule, reapplied at ingress.

    Two copies of "usable" drift, and the ingress copy would drift silently
    because nothing dispatches through it.
    """
    registry = fake_registry()
    installation, binding, key = _endpoint(db, registry)
    set_binding_enabled(db, installation, binding, registry=registry, enabled=False)

    with pytest.raises(EndpointNotUsable):
        prepare_ingress(db, endpoint_id=key, registry=registry)


def test_a_disabled_installation_refuses_with_endpoint_not_usable(
    db: Session,
) -> None:
    """The whole chain, not just the binding — a quarantined installation must
    stop receiving even while its bindings still read `enabled`."""
    registry = fake_registry()
    installation, _, key = _endpoint(db, registry)
    installation.state = "quarantined"
    db.flush()

    with pytest.raises(EndpointNotUsable):
        prepare_ingress(db, endpoint_id=key, registry=registry)


def test_an_uninstalled_connector_is_retryable_not_a_404(db: Session) -> None:
    """OUR misconfiguration must be retryable.

    The provider's redelivery window is the only remaining copy of those events;
    a 4xx tells it to give up on content we could still accept once the
    connector is reinstalled.
    """
    registry = fake_registry()
    _, _, key = _endpoint(db, registry)
    empty = fake_registry(plugins=[fake_plugin(manifest_=_other_manifest())])

    with pytest.raises(ConnectorUnavailable) as caught:
        prepare_ingress(db, endpoint_id=key, registry=empty)

    outcome = refusal_outcome(caught.value)
    assert outcome.status_code == 503
    assert isinstance(caught.value, EndpointNotServiceable)


def _other_manifest():  # type: ignore[no-untyped-def]
    from dotmac_integration.conformance import fake_manifest

    return fake_manifest(connector_key="some_other_connector")


def test_an_unhonoured_manifest_pin_refuses_before_the_handler_is_looked_up(
    db: Session,
) -> None:
    """A pin the connector no longer honours must not reach the plugin.

    The installation was configured for a payload shape the connector has since
    superseded, so calling it would run verification against a contract nobody
    claims to implement.
    """
    plugin = fake_plugin()
    registry = fake_registry(plugins=[plugin])
    installation, _, key = _endpoint(db, registry)
    installation.manifest_digest = "0" * 64
    db.flush()

    with pytest.raises(ManifestPinUnhonoured):
        prepare_ingress(db, endpoint_id=key, registry=registry)
    assert plugin.verified == [], "the plugin was reached under a stale contract"


def test_a_delivery_only_connector_behind_an_ingress_url_is_a_stated_refusal(
    db: Session,
) -> None:
    """`require_mode` runs BEFORE `ingress_handler_for`.

    After it, the operator sees an `AttributeError` from inside a lookup —
    which reads as a broken plugin rather than as a connector that does not
    receive.
    """
    registry = fake_registry()
    _, binding, key = _endpoint(db, registry)

    delivery_only = fake_plugin(modes_=frozenset({ConnectorMode.DELIVERY}))
    narrowed = fake_registry(plugins=[delivery_only])

    with pytest.raises(ModeNotAvailable):
        prepare_ingress(db, endpoint_id=key, registry=narrowed)
    assert binding.ingress_endpoint_key == key


def _leaf_refusals() -> list[type[IngressRefused]]:
    """Every LEAF refusal, walked transitively.

    Built from the class hierarchy rather than a hand-written list, so a new
    refusal added with no status decision fails the parametrization instead of
    inheriting one by accident. Grouping bases (those with subclasses) are
    excluded because they are never raised.
    """

    def walk(cls: type) -> Iterator[type]:
        subclasses = cls.__subclasses__()
        if not subclasses:
            yield cls
            return
        for sub in subclasses:
            yield from walk(sub)

    return sorted(walk(IngressRefused), key=lambda c: c.__name__)


@pytest.mark.parametrize("refusal", _leaf_refusals(), ids=lambda c: c.__name__)
def test_every_refusal_has_a_decided_status(refusal: type[IngressRefused]) -> None:
    """A refusal without a status is a refusal without a retry instruction.

    An ingress status IS a retry instruction to the provider — the one place a
    default would silently destroy events.
    """
    assert refusal.MESSAGE, f"{refusal.__name__} carries no message"
    assert isinstance(refusal.CODE, IngressCode)
    assert refusal.STATUS in {400, 401, 404, 413, 503}


def test_the_refusal_walk_actually_finds_the_hierarchy() -> None:
    """The sensitivity proof for the parametrization above (ADR-0018).

    A walk that returned nothing, or only the base, would make every
    `test_every_refusal_has_a_decided_status` case pass by not existing.
    """
    found = {c.__name__ for c in _leaf_refusals()}
    assert len(found) >= 12
    assert {"EndpointUnknown", "SignatureRejected", "PayloadTooLarge"} <= found
    assert "EndpointNotServiceable" not in found, "a grouping base is never raised"
    assert "IngressRefused" not in found


# ── The no-session boundary ─────────────────────────────────────────────────


def test_verify_and_normalize_takes_no_session() -> None:
    """Enforced by what a caller CANNOT pass.

    A `db=None` parameter added "just for logging" is how a session ends up
    held across provider-controlled work.
    """
    for function in (
        verify_and_normalize,
        __import__(
            "dotmac_integration.ingress", fromlist=["challenge_response"]
        ).challenge_response,
    ):
        parameters = inspect.signature(function).parameters
        assert "db" not in parameters, function.__name__
        assert not any(
            "Session" in str(p.annotation) for p in parameters.values()
        ), function.__name__


def test_the_prepared_carrier_holds_no_orm_object() -> None:
    """An ORM object on the carrier drags a session into the plugin phase.

    `slots` matters as much as the field types: without it a session can be
    smuggled on as an ad-hoc attribute after construction.
    """
    assert is_dataclass(PreparedIngress)
    assert PreparedIngress.__dataclass_params__.frozen  # type: ignore[attr-defined]
    assert "__slots__" in PreparedIngress.__dict__, "slots is missing"

    # Plain data only. `dict[str, Any]` is admitted because a config revision's
    # JSON is genuinely heterogeneous; what matters is that no ORM class,
    # `Session` or `Any` bare appears, since those are what carry a connection.
    allowed = {"str", "UUID", "dict", "dict[str, Any]", "UUID | None"}
    for f in fields(PreparedIngress):
        assert f.type in allowed, f"{f.name}: {f.type}"

    # The proof that `slots` is doing work: an instance rejects an ad-hoc
    # attribute, which is the route a session would take onto the carrier.
    carrier = PreparedIngress(
        endpoint_key="0" * 48,
        installation_id=uuid.uuid4(),
        binding_id=uuid.uuid4(),
        connector_key="k",
        capability_id=FAKE_CAPABILITY,
    )
    with pytest.raises((AttributeError, TypeError)):
        carrier.db = object()  # type: ignore[attr-defined]


def test_the_plugin_never_receives_a_session(db: Session) -> None:
    """The fake records everything handed to it; a session must never appear.

    "Everything" is the load-bearing word. A sweep over the raw bodies alone
    reduces to `not isinstance(b"...", Session)` — true of bytes for reasons
    that have nothing to do with this module — while the route a session would
    ACTUALLY take is `config`, a `dict[str, Any]` whose VALUES no field-type
    check inspects. So the kit records the config and the secrets each plugin
    call was handed, and the sweep runs over those as well.
    """
    plugin = fake_plugin(inbound=_events("evt_1"))
    registry = fake_registry(plugins=[plugin])
    _, _, key = _endpoint(db, registry)
    uow = RecordingUnitOfWork(db)

    receive(
        uow,
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=registry,
        resolve_secrets=_resolver(),
    )

    # Both plugin calls recorded what crossed, or the sweep below runs over less
    # than it claims to.
    assert len(plugin.configs_seen) == 2, "verify and normalize both take config"
    assert len(plugin.secrets_seen) == 1, "verify takes secrets; normalize does not"

    handed: list[Any] = [*plugin.verified, *plugin.normalized, *plugin.challenged]
    for mapping in (*plugin.configs_seen, *plugin.secrets_seen):
        handed.append(mapping)
        handed.extend(mapping.values())
    assert handed
    for item in handed:
        assert not isinstance(item, Session)
        assert not hasattr(item, "execute")


def test_the_session_sweep_catches_one_smuggled_through_a_config_value(
    db: Session,
) -> None:
    """The ADR-0018 sensitivity proof for the test above.

    `PreparedIngress.config` is `dict[str, Any]`, and
    `test_the_prepared_carrier_holds_no_orm_object` checks field TYPES only — so
    a session placed in a config VALUE passes every structural check in this
    file. The sweep has to be what catches it, or the property is asserted
    about bytes and nothing else.
    """
    plugin = fake_plugin(inbound=_events("evt_1"))
    registry = fake_registry(plugins=[plugin])
    _, _, key = _endpoint(db, registry)
    prepared = prepare_ingress(db, endpoint_id=key, registry=registry)
    smuggled = PreparedIngress(
        endpoint_key=prepared.endpoint_key,
        installation_id=prepared.installation_id,
        binding_id=prepared.binding_id,
        connector_key=prepared.connector_key,
        capability_id=prepared.capability_id,
        config={"variant": "a", "smuggled": db},
        secret_refs=prepared.secret_refs,
    )

    verify_and_normalize(
        smuggled,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=registry,
        resolve_secrets=_resolver(),
    )

    handed = [value for mapping in plugin.configs_seen for value in mapping.values()]
    assert any(
        isinstance(item, Session) for item in handed
    ), "the fake did not record the config values, so the sweep proves nothing"


# ── Verify and normalize ────────────────────────────────────────────────────


def test_verify_receives_the_exact_bytes(db: Session) -> None:
    """Any decode-and-re-encode invalidates a correct HMAC.

    Trailing whitespace and non-UTF-8 bytes, both of which a JSON round-trip
    silently changes.
    """
    raw = b'{"a": 1}  \n\xff\xfe'
    plugin = fake_plugin()
    registry = fake_registry(plugins=[plugin])
    _, _, key = _endpoint(db, registry)
    prepared = prepare_ingress(db, endpoint_id=key, registry=registry)

    verify_and_normalize(
        prepared,
        raw_body=raw,
        headers=_headers(),
        registry=registry,
        resolve_secrets=_resolver(),
    )

    assert plugin.verified == [raw]
    assert plugin.normalized == [raw]


def test_normalize_is_never_called_on_an_unverified_body(db: Session) -> None:
    """ "Let us normalize first, to log what came in" is how unverified,
    attacker-shaped content reaches a parser and then a table."""
    plugin = fake_plugin(signature_valid=False, inbound=_events("evt_1"))
    registry = fake_registry(plugins=[plugin])
    _, _, key = _endpoint(db, registry)
    prepared = prepare_ingress(db, endpoint_id=key, registry=registry)

    with pytest.raises(SignatureRejected):
        verify_and_normalize(
            prepared,
            raw_body=BODY_SENTINEL,
            headers=_headers(),
            registry=registry,
            resolve_secrets=_resolver(),
        )

    assert plugin.verified == [BODY_SENTINEL]
    assert plugin.normalized == []


def test_a_rejected_signature_persists_nothing_anywhere(db: Session) -> None:
    """Across ALL SEVEN platform tables, not just the inbox.

    There is no table for unverified bodies and there must not be one: storing
    what failed verification is storing exactly the material that must not be
    stored.
    """
    plugin = fake_plugin(signature_valid=False, inbound=_events("evt_1"))
    registry = fake_registry(plugins=[plugin])
    _, _, key = _endpoint(db, registry)
    db.commit()
    before = _row_counts(db)

    uow = RecordingUnitOfWork(db)
    outcome = receive(
        uow,
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=registry,
        resolve_secrets=_resolver(),
    )

    assert outcome.status_code == 401
    assert outcome.code is IngressCode.SIGNATURE_REJECTED
    assert _row_counts(db) == before

    # VACUITY GUARD: the identical request with a good signature must write, or
    # "nothing was persisted" would be true because nothing ever persists.
    accepting = fake_plugin(inbound=_events("evt_1"))
    accepting_registry = fake_registry(plugins=[accepting])
    ok = receive(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=accepting_registry,
        resolve_secrets=_resolver(),
    )
    assert ok.code is IngressCode.ACCEPTED
    assert _row_counts(db)["inbox_receipts"] == before["inbox_receipts"] + 1


def test_a_raising_normalize_records_nothing_and_is_retryable(db: Session) -> None:
    """A throw tells us nothing about what was normalized, so nothing may be
    written — a partially-parsed batch is not a batch."""
    plugin = fake_plugin(inbound=_events("a", "b"), normalize_raises=ValueError("boom"))
    registry = fake_registry(plugins=[plugin])
    _, _, key = _endpoint(db, registry)
    db.commit()
    before = _row_counts(db)

    outcome = receive(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=registry,
        resolve_secrets=_resolver(),
    )

    assert (outcome.status_code, outcome.code) == (503, IngressCode.CONNECTOR_RAISED)
    assert _row_counts(db) == before


def test_a_normalize_returning_a_list_is_a_contract_refusal(db: Session) -> None:
    """The engine does not iterate whatever it is handed."""
    plugin = fake_plugin(inbound=_events("a"), ingress_contract_broken=True)
    registry = fake_registry(plugins=[plugin])
    _, _, key = _endpoint(db, registry)
    prepared = prepare_ingress(db, endpoint_id=key, registry=registry)

    with pytest.raises(ConnectorContract):
        verify_and_normalize(
            prepared,
            raw_body=BODY_SENTINEL,
            headers=_headers(),
            registry=registry,
            resolve_secrets=_resolver(),
        )


def test_secrets_reach_verify_materialized_and_normalize_gets_config_without_them(
    db: Session,
) -> None:
    """Normalization that needs a secret is doing verification in the wrong
    place, so `normalize` is not given any — checked against the SPI signature
    rather than against the fake, which could simply ignore an argument."""
    plugin = fake_plugin(inbound=_events("a", "b", "c"))
    registry = fake_registry(plugins=[plugin])
    _, _, key = _endpoint(db, registry)
    prepared = prepare_ingress(db, endpoint_id=key, registry=registry)
    resolve = _resolver()

    verify_and_normalize(
        prepared,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=registry,
        resolve_secrets=resolve,
    )

    assert resolve.calls == [
        {"signing": "bao://kv/signing"}
    ], "secrets must be materialized exactly once, not per event"
    handler = plugin.ingress_handler_for(FAKE_CAPABILITY)
    assert "secrets" in inspect.signature(handler.verify).parameters
    assert "secrets" not in inspect.signature(handler.normalize).parameters


# ── Batch atomicity ─────────────────────────────────────────────────────────


def test_the_whole_normalized_tuple_is_recorded_in_one_unit_of_work(
    db: Session,
) -> None:
    """`record_batch` takes the TUPLE, never one event.

    There is no loop for a caller to put a transaction boundary inside, which is
    what makes whole-batch atomicity structural rather than a convention.
    """
    events = _events("a", "b", "c")
    plugin = fake_plugin(inbound=events)
    registry = fake_registry(plugins=[plugin])
    _, binding, key = _endpoint(db, registry)
    uow = RecordingUnitOfWork(db)

    outcome = receive(
        uow,
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=registry,
        resolve_secrets=_resolver(),
    )

    assert outcome.code is IngressCode.ACCEPTED
    assert (outcome.normalized, outcome.recorded, outcome.duplicates) == (3, 3, 0)
    assert len(outcome.receipt_ids) == 3
    assert uow.entered == 2 and uow.rolled_back == 0
    assert_every_event_recorded(db, binding, events)

    parameters = inspect.signature(record_batch).parameters
    assert "events" in parameters and "event" not in parameters


def test_an_empty_normalized_tuple_is_accepted_and_writes_nothing(
    db: Session,
) -> None:
    """A provider that sends a keepalive, or a batch entirely filtered by the
    connector, is not a failure — and must not be answered as one."""
    plugin = fake_plugin(inbound=())
    registry = fake_registry(plugins=[plugin])
    _, _, key = _endpoint(db, registry)
    db.commit()
    before = _row_counts(db)

    outcome = receive(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=registry,
        resolve_secrets=_resolver(),
    )

    assert (outcome.status_code, outcome.code) == (200, IngressCode.ACCEPTED)
    assert (outcome.normalized, outcome.recorded) == (0, 0)
    assert _row_counts(db) == before


def test_the_all_recorded_assertion_is_not_satisfied_by_an_empty_batch(
    db: Session,
) -> None:
    """The explicit vacuity guard for `assert_every_event_recorded`.

    `count == len(events)` degenerates to `0 == 0` the moment a refactor makes
    `normalize` return nothing, and every batch test above would keep passing.
    """
    registry = fake_registry()
    _, binding, _ = _endpoint(db, registry)

    with pytest.raises(AssertionError, match="vacuously"):
        assert_every_event_recorded(db, binding, ())


def test_a_collision_mid_batch_rolls_back_the_events_before_it(db: Session) -> None:
    """THE test. A partial write is worse than a refused batch.

    The provider is told the batch was accepted while some events were never
    recorded, and it will not resend the ones that landed. Three events, the
    SECOND collides: the first must not survive, and the third must never be
    attempted.
    """
    from dotmac_integration import receive_verified

    registry = fake_registry()
    installation, binding, key = _endpoint(db, registry)
    # A pre-existing receipt for `b` with DIFFERENT content — the collision.
    receive_verified(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        provider_event_id="b",
        event_type="thing.happened",
        payload={"i": "something else entirely"},
    )
    db.commit()

    events = _events("a", "b", "c")
    plugin = fake_plugin(inbound=events)
    colliding = fake_registry(plugins=[plugin])
    uow = RecordingUnitOfWork(db)

    outcome = receive(
        uow,
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=colliding,
        resolve_secrets=_resolver(),
    )

    assert (outcome.status_code, outcome.code) == (
        503,
        IngressCode.EVENT_IDENTITY_COLLISION,
    )
    surviving = set(db.execute(select(InboxReceipt.provider_event_id)).scalars().all())
    assert surviving == {"b"}, (
        "an event recorded before the collision survived — the provider will "
        "never resend it"
    )

    # VACUITY GUARD: the identical fixture WITHOUT the collision must persist
    # all three, or "only the pre-existing row survives" proves nothing.
    _, other_binding, other_key = _endpoint(db, registry, name="uncollided")
    clean = receive(
        RecordingUnitOfWork(db),
        endpoint_id=other_key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=colliding,
        resolve_secrets=_resolver(),
    )
    assert clean.code is IngressCode.ACCEPTED
    assert_every_event_recorded(db, other_binding, events)


def test_the_collision_outcome_is_produced_after_the_unit_of_work_unwound(
    db: Session,
) -> None:
    """The same regression as above, caught structurally rather than by count.

    If the `except` moved INSIDE the `with`, the block would exit cleanly and
    commit the partial batch. The injected unit of work is what makes that
    visible: it must record a rollback and no second commit.
    """
    from dotmac_integration import receive_verified

    registry = fake_registry()
    installation, binding, key = _endpoint(db, registry)
    receive_verified(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        provider_event_id="b",
        event_type="thing.happened",
        payload={"i": "different"},
    )
    db.commit()

    plugin = fake_plugin(inbound=_events("a", "b", "c"))
    uow = RecordingUnitOfWork(db)

    receive(
        uow,
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=fake_registry(plugins=[plugin]),
        resolve_secrets=_resolver(),
    )

    assert uow.entered == 2
    assert uow.rolled_back == 1
    # The first unit of work (prepare) commits a no-op; the recording one must
    # not, or a partial batch was persisted.
    assert uow.committed == 1
    assert uow.timeline == ["enter", "commit", "enter", "rollback"]


def test_a_whole_batch_retry_after_a_collision_is_repaired(db: Session) -> None:
    """Why 503 rather than a 4xx: the provider retries, and the retry works.

    Once the operator resolves the collision, the SAME batch replays cleanly —
    which is only true because nothing partial was committed the first time.
    """
    from dotmac_integration import receive_verified

    registry = fake_registry()
    installation, binding, key = _endpoint(db, registry)
    stale, _ = receive_verified(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        provider_event_id="b",
        event_type="thing.happened",
        payload={"i": "different"},
    )
    db.commit()

    events = _events("a", "b", "c")
    ingress_registry = fake_registry(plugins=[fake_plugin(inbound=events)])
    first = receive(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=ingress_registry,
        resolve_secrets=_resolver(),
    )
    assert first.code is IngressCode.EVENT_IDENTITY_COLLISION

    # The operator resolves it, and the provider redelivers the WHOLE batch.
    db.delete(stale)
    db.commit()

    replayed = receive(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=ingress_registry,
        resolve_secrets=_resolver(),
    )
    assert replayed.code is IngressCode.ACCEPTED
    assert replayed.recorded == 3
    assert_every_event_recorded(db, binding, events)


def test_a_redelivered_batch_is_idempotent_and_reports_duplicates(
    db: Session,
) -> None:
    """`receive_verified` dedups on `(binding, provider_event_id)`, which is
    what makes a whole-batch retry correct rather than duplicative."""
    events = _events("a", "b", "c")
    registry = fake_registry(plugins=[fake_plugin(inbound=events)])
    _, binding, key = _endpoint(db, registry)

    first = receive(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=registry,
        resolve_secrets=_resolver(),
    )
    second = receive(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=registry,
        resolve_secrets=_resolver(),
    )

    assert (first.recorded, first.duplicates) == (3, 0)
    assert (second.recorded, second.duplicates) == (0, 3)
    assert set(second.receipt_ids) == set(first.receipt_ids)
    assert _row_counts(db)["inbox_receipts"] == 3
    assert_every_event_recorded(db, binding, events)


def test_a_partially_redelivered_batch_records_only_the_new_events(
    db: Session,
) -> None:
    """Deduplication is per EVENT, not per batch.

    A provider that resends `{A,B}` inside `{A,B,C}` must produce one new
    receipt, not three and not zero.
    """
    registry = fake_registry(plugins=[fake_plugin(inbound=_events("a", "b"))])
    _, binding, key = _endpoint(db, registry)
    receive(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=registry,
        resolve_secrets=_resolver(),
    )

    grown = _events("a", "b", "c")
    outcome = receive(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=fake_registry(plugins=[fake_plugin(inbound=grown)]),
        resolve_secrets=_resolver(),
    )

    assert (outcome.recorded, outcome.duplicates) == (1, 2)
    assert _row_counts(db)["inbox_receipts"] == 3
    assert_every_event_recorded(db, binding, grown)


# ── Never logged, never stored ──────────────────────────────────────────────


def test_no_receipt_stores_request_headers(db: Session) -> None:
    """`headers_json` ends up in every backup.

    Forwarding the request headers puts the signature header — and any
    `authorization` or `cookie` a misconfigured proxy passed through —
    permanently in the database.
    """
    plugin = fake_plugin(inbound=_events("a", "b"))
    registry = fake_registry(plugins=[plugin])
    _, _, key = _endpoint(db, registry)
    headers = _headers()

    receive(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=headers,
        registry=registry,
        resolve_secrets=_resolver(),
    )

    receipts = db.execute(select(InboxReceipt)).scalars().all()
    assert receipts
    for receipt in receipts:
        assert receipt.headers_json is None

    # VACUITY GUARD: this is about SUPPRESSION, not absence. The sensitive
    # header must genuinely have been in the mapping the engine handed over.
    assert HEADER_SENTINEL in headers.values()


def test_no_ingress_refusal_can_be_given_a_message() -> None:
    """The message is a class constant, so there is no parameter to interpolate
    a request fragment into. Not a discipline — an impossibility."""
    for refusal in _leaf_refusals():
        if refusal is ConnectorRaised:
            continue
        assert inspect.signature(refusal).parameters == {}, refusal.__name__
        assert str(refusal()) == refusal.MESSAGE


def test_no_refusal_message_contains_any_request_material(db: Session) -> None:
    """The highest-value test here: every refusal path, every sentinel.

    Body, header value, query parameter, resolved secret and the endpoint key
    are all planted, and none may appear in the exception's `str`, its `repr`,
    its rendered cause chain, or the serialized outcome.
    """
    sentinels = (
        BODY_SENTINEL.decode("latin-1"),
        HEADER_SENTINEL,
        PARAM_SENTINEL,
        SECRET_SENTINEL,
    )

    registry = fake_registry()
    installation, binding, key = _endpoint(db, registry)
    good = prepare_ingress(db, endpoint_id=key, registry=registry)

    def drive_refusals():  # type: ignore[no-untyped-def]
        yield EndpointUnknown()
        yield EndpointNotUsable()
        yield PayloadTooLarge()

        rejecting = fake_registry(plugins=[fake_plugin(signature_valid=False)])
        yield _refusal(
            verify_and_normalize,
            good,
            raw_body=BODY_SENTINEL,
            headers=_headers(),
            registry=rejecting,
            resolve_secrets=_resolver(),
        )

        raising = fake_registry(
            plugins=[fake_plugin(ingress_raises=ValueError(BODY_SENTINEL))]
        )
        yield _refusal(
            verify_and_normalize,
            good,
            raw_body=BODY_SENTINEL,
            headers=_headers(),
            registry=raising,
            resolve_secrets=_resolver(),
        )

        broken = fake_registry(
            plugins=[fake_plugin(inbound=_events("a"), ingress_contract_broken=True)]
        )
        yield _refusal(
            verify_and_normalize,
            good,
            raw_body=BODY_SENTINEL,
            headers=_headers(),
            registry=broken,
            resolve_secrets=_resolver(),
        )

        silent = fake_registry(plugins=[fake_plugin()])
        yield _refusal(
            __import__(
                "dotmac_integration.ingress", fromlist=["challenge_response"]
            ).challenge_response,
            good,
            params={"not-the-key": PARAM_SENTINEL},
            registry=silent,
            resolve_secrets=_resolver(),
        )

    for exc in drive_refusals():
        rendered = "\n".join(
            (
                str(exc),
                repr(exc),
                str(exc.__cause__),
                repr(exc.__cause__),
                json.dumps(_serialize(refusal_outcome(exc, prepared=good))),
            )
        )
        for sentinel in sentinels:
            assert sentinel not in rendered, f"{type(exc).__name__} leaked {sentinel!r}"

    assert installation.id and binding.id  # the fixture really was built


def _refusal(function, *args: Any, **kwargs: Any) -> IngressRefused:  # type: ignore[no-untyped-def]
    """Call something expected to refuse, and hand back the refusal."""
    try:
        function(*args, **kwargs)
    except IngressRefused as exc:
        return exc
    raise AssertionError(f"{function.__name__} did not refuse")


def _serialize(outcome: IngressOutcome) -> dict:
    """The structural serialization an assembly performs — no field allowlist.

    Written this way deliberately: a leak test that hand-picked fields would
    stop covering the field somebody adds next.
    """
    return {f.name: str(getattr(outcome, f.name)) for f in fields(outcome)}


def test_a_raising_ingress_plugin_loses_its_message_and_its_cause(
    db: Session,
) -> None:
    """`from None` is load-bearing.

    The plugin's exception message is built from provider-controlled bytes, and
    `__cause__` would carry it into any traceback the edge logs. The TYPE NAME
    survives, because it is the only part an operator can act on.
    """
    plugin = fake_plugin(ingress_raises=ValueError("SENTINEL-PLUGIN-MESSAGE leaked"))
    registry = fake_registry(plugins=[plugin])
    _, _, key = _endpoint(db, registry)
    prepared = prepare_ingress(db, endpoint_id=key, registry=registry)

    with pytest.raises(ConnectorRaised) as caught:
        verify_and_normalize(
            prepared,
            raw_body=BODY_SENTINEL,
            headers=_headers(),
            registry=registry,
            resolve_secrets=_resolver(),
        )

    assert "SENTINEL-PLUGIN-MESSAGE" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert "ValueError" in str(caught.value)


def test_a_connector_raised_refuses_a_type_name_that_is_not_one() -> None:
    """`.isidentifier()` is what stops a message masquerading as a type name."""
    assert "ValueError" in str(ConnectorRaised("ValueError"))
    smuggled = ConnectorRaised(f"ValueError: {BODY_SENTINEL!r} at line 3")
    assert BODY_SENTINEL.decode("latin-1") not in str(smuggled)
    assert "Exception" in str(smuggled)


def test_the_outcome_has_no_field_that_can_hold_request_material() -> None:
    """A frozen expected set, because the assembly serializes EVERY field.

    Adding `raw_body` or `endpoint_key` "for debugging" would put it in an HTTP
    response with no allowlist standing in the way.
    """
    expected = {
        "status_code": "int",
        "code": "IngressCode",
        "installation_id": "UUID | None",
        "binding_id": "UUID | None",
        "normalized": "int",
        "recorded": "int",
        "duplicates": "int",
        "receipt_ids": "tuple[UUID, ...]",
        "challenge_body": "str | None",
        "media_type": "str",
    }
    assert {f.name: f.type for f in fields(IngressOutcome)} == expected


def test_the_outcome_does_not_echo_the_endpoint_key(db: Session) -> None:
    """Whoever holds the key can drive the connector's `verify`.

    Echoing it into a 404 hands it to a scanner that guessed it — which is the
    one case where the response is the attacker's confirmation.
    """
    registry = fake_registry()
    _, _, key = _endpoint(db, registry)

    accepted = receive(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=registry,
        resolve_secrets=_resolver(),
    )
    missing = receive(
        RecordingUnitOfWork(db),
        endpoint_id="f" * 48,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=registry,
        resolve_secrets=_resolver(),
    )

    assert missing.status_code == 404
    for outcome in (accepted, missing):
        assert key not in json.dumps(_serialize(outcome))
        assert "f" * 48 not in json.dumps(_serialize(outcome))


# ── Transaction shape ───────────────────────────────────────────────────────


def test_the_engine_opens_two_units_of_work_and_none_spans_the_plugin_call(
    db: Session,
) -> None:
    """A session held across `verify`/`normalize` holds row locks for as long as
    a connector takes — the failure `dispatch.py` exists to prevent, restated
    for the inbound direction."""

    class TimelinePlugin:
        """Wraps the fake so the plugin call lands ON the recorded timeline.

        The four base-protocol members are forwarded EXPLICITLY rather than
        through `__getattr__`: a `runtime_checkable` protocol's `isinstance`
        resolves members with `inspect.getattr_static`, which deliberately does
        not fire `__getattr__`, so a delegating wrapper fails `discover()`'s
        `ConnectorPlugin` check with a message about the wrapper's type.
        """

        def __init__(self, inner, uow: RecordingUnitOfWork) -> None:
            self._inner = inner
            self._uow = uow

        @property
        def manifest(self) -> Any:
            return self._inner.manifest

        @property
        def historical_manifests(self) -> Any:
            return self._inner.historical_manifests

        @property
        def modes(self) -> Any:
            return self._inner.modes

        def validate_connection(self, *, config: dict, secrets: dict) -> Any:
            return self._inner.validate_connection(config=config, secrets=secrets)

        def ingress_handler_for(self, capability_id: str) -> Any:
            inner = self._inner.ingress_handler_for(capability_id)
            uow = self._uow

            class _Timed:
                def challenge(self, params, *, config, secrets):  # type: ignore[no-untyped-def]
                    uow.timeline.append("challenge")
                    return inner.challenge(params, config=config, secrets=secrets)

                def verify(self, raw_body, headers, *, config, secrets):  # type: ignore[no-untyped-def]
                    uow.timeline.append("verify")
                    return inner.verify(
                        raw_body, headers, config=config, secrets=secrets
                    )

                def normalize(self, raw_body, headers, *, config):  # type: ignore[no-untyped-def]
                    uow.timeline.append("normalize")
                    return inner.normalize(raw_body, headers, config=config)

            return _Timed()

    registry = fake_registry()
    _, _, key = _endpoint(db, registry)
    uow = RecordingUnitOfWork(db)
    timed = fake_registry(
        plugins=[TimelinePlugin(fake_plugin(inbound=_events("a")), uow)]
    )

    receive(
        uow,
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=timed,
        resolve_secrets=_resolver(),
    )

    assert uow.entered == 2
    assert uow.timeline == [
        "enter",
        "commit",
        "verify",
        "normalize",
        "enter",
        "commit",
    ]


def test_prepare_ingress_writes_nothing(db: Session) -> None:
    """The resolve phase's clean exit must stay a no-op.

    A phase that started mutating would make the read-only unit of work a real
    write, and the "two short transactions" claim would quietly become one long
    one plus a surprise.
    """
    registry = fake_registry()
    _, _, key = _endpoint(db, registry)
    db.commit()
    before = _row_counts(db)

    prepare_ingress(db, endpoint_id=key, registry=registry)

    assert not db.new and not db.dirty and not db.deleted
    assert _row_counts(db) == before


# ── Challenge ───────────────────────────────────────────────────────────────


def test_a_handshake_answers_with_the_exact_string_and_a_plain_media_type(
    db: Session,
) -> None:
    """Providers compare the RAW echoed body. Wrapping it in JSON fails the
    handshake, and a failed handshake means the subscription is never created."""
    registry = fake_registry()
    _, _, key = _endpoint(db, registry)

    outcome = answer_challenge(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        params={"challenge": "echo-me-1234", "mode": "subscribe"},
        registry=registry,
        resolve_secrets=_resolver(),
    )

    assert (outcome.status_code, outcome.code) == (
        200,
        IngressCode.CHALLENGE_ANSWERED,
    )
    assert outcome.challenge_body == "echo-me-1234"
    assert outcome.media_type == "text/plain"


def test_a_handshake_writes_nothing(db: Session) -> None:
    """A subscription handshake is not an event."""
    registry = fake_registry()
    _, _, key = _endpoint(db, registry)
    db.commit()
    before = _row_counts(db)

    answer_challenge(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        params={"challenge": "echo"},
        registry=registry,
        resolve_secrets=_resolver(),
    )

    assert _row_counts(db) == before


def test_a_non_handshake_is_a_stated_refusal(db: Session) -> None:
    """`None` must not become a 200 with an empty body — the provider would
    record a successful subscription that never verified."""
    registry = fake_registry()
    _, _, key = _endpoint(db, registry)

    outcome = answer_challenge(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        params={"unrelated": PARAM_SENTINEL},
        registry=registry,
        resolve_secrets=_resolver(),
    )

    assert (outcome.status_code, outcome.code) == (400, IngressCode.NOT_A_CHALLENGE)
    assert outcome.challenge_body is None
    assert refusal_outcome(NotAChallenge()).status_code == 400


def test_a_none_from_challenge_refuses_and_does_not_fall_through_to_delivery(
    db: Session,
) -> None:
    """`IngressHandler`'s docstring and the engine must say the same thing.

    The SPI used to describe `None` as "not a handshake for me — the caller then
    treats the request as a delivery", which the engine has never done: it
    answers 400 and `verify` is never reached. A connector author reading the
    protocol would have built for a fall-through that does not exist, and would
    have found out from a provider's failed subscription. The behaviour is the
    deliberate one — offering a bodied delivery to `challenge` lets a plugin
    that returns non-`None` by accident swallow a batch — so the docstring was
    corrected to match, and this pins the half a docstring cannot.
    """
    plugin = fake_plugin(inbound=_events("a"))
    registry = fake_registry(plugins=[plugin])
    _, _, key = _endpoint(db, registry)

    outcome = receive(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        # No bytes, so the handshake branch is taken; no `challenge` param, so
        # the fake's `params.get("challenge")` returns `None`.
        raw_body=b"",
        headers=_headers(),
        params={"unrelated": PARAM_SENTINEL},
        registry=registry,
        resolve_secrets=_resolver(),
    )

    assert (outcome.status_code, outcome.code) == (400, IngressCode.NOT_A_CHALLENGE)
    assert len(plugin.challenged) == 1
    assert plugin.verified == [], "a refused handshake fell through to delivery"
    assert plugin.normalized == []


def test_a_bodyless_post_reaches_challenge_and_a_bodied_one_does_not(
    db: Session,
) -> None:
    """Asking `challenge` on every delivery lets a plugin that returns
    non-`None` by accident silently swallow a whole batch of real events.

    "A request with no bytes cannot carry a signed payload" is a protocol fact,
    not a guess from the HTTP verb and not provider knowledge.
    """
    plugin = fake_plugin(inbound=_events("a"))
    registry = fake_registry(plugins=[plugin])
    _, _, key = _endpoint(db, registry)

    bodied = receive(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        params={"challenge": "would-have-swallowed-the-batch"},
        registry=registry,
        resolve_secrets=_resolver(),
    )
    assert bodied.code is IngressCode.ACCEPTED
    assert plugin.challenged == [], "a delivery was offered to challenge first"

    bodyless = receive(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        raw_body=b"",
        headers=_headers(),
        params={"challenge": "echo"},
        registry=registry,
        resolve_secrets=_resolver(),
    )
    assert bodyless.code is IngressCode.CHALLENGE_ANSWERED
    assert plugin.challenged == [{"challenge": "echo"}]


def test_the_whole_params_mapping_crosses_the_boundary(db: Session) -> None:
    """Selecting a key here would be provider knowledge in a module that may
    hold none — which parameter carries the echo is the connector's business."""
    plugin = fake_plugin()
    registry = fake_registry(plugins=[plugin])
    _, _, key = _endpoint(db, registry)
    params = {"challenge": "echo", "mode": "subscribe", "extra": PARAM_SENTINEL}

    answer_challenge(
        RecordingUnitOfWork(db),
        endpoint_id=key,
        params=params,
        registry=registry,
        resolve_secrets=_resolver(),
    )

    assert plugin.challenged == [params]


# ── The size limit belongs to the edge ──────────────────────────────────────


def test_payload_too_large_is_defined_here_and_raised_by_nobody_here() -> None:
    """A byte cap that varied by connector would be a connector-specific limit,
    which this module may not hold (accepted design item 6)."""
    from pathlib import Path

    package = Path(
        __import__("dotmac_integration").__file__  # type: ignore[arg-type]
    ).parent
    sources = list(package.rglob("*.py"))
    assert len(sources) >= 15, "the source scan found almost nothing"

    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert "raise PayloadTooLarge" not in text, path


def test_the_ingress_engine_takes_no_max_bytes_parameter() -> None:
    """The same regression, via a parameter rather than a raise."""
    from dotmac_integration import ingress as engine

    forbidden = ("max_bytes", "max_body", "byte_limit", "size_limit")
    for name in dir(engine):
        function = getattr(engine, name)
        if not callable(function) or not inspect.isfunction(function):
            continue
        parameters = set(inspect.signature(function).parameters)
        assert not parameters & set(forbidden), name


def test_refusal_outcome_turns_an_edge_refusal_into_the_same_shape() -> None:
    """The edge constructs the refusal; it never authors a status code.

    An ingress status IS a retry instruction to the provider, and that decision
    stays in one place.
    """
    outcome = refusal_outcome(PayloadTooLarge())

    assert outcome.status_code == 413
    assert outcome.code is IngressCode.PAYLOAD_TOO_LARGE
    assert outcome.installation_id is None and outcome.binding_id is None
    assert outcome.receipt_ids == ()


def test_the_receipt_write_race_is_a_typed_refusal_not_a_driver_error() -> None:
    """SQLite cannot stage the race; Postgres does, in the isolation suite.

    What is checked here is that the refusal EXISTS with a decided status, so
    the untyped `IntegrityError` path cannot come back by simply having no
    typed alternative to convert into.
    """
    assert refusal_outcome(ReceiptWriteRaced()).status_code == 503
    assert refusal_outcome(ReceiptWriteRaced()).code is IngressCode.RECEIPT_WRITE_RACED
    assert refusal_outcome(EventIdentityCollision()).status_code == 503


def test_the_endpoint_key_shape_is_the_one_the_engine_admits() -> None:
    """Minting and admitting must agree, or every minted endpoint 404s.

    A silent disagreement here is the worst kind: it would look like a routing
    bug in the assembly, in a different repository.
    """
    import secrets as stdlib_secrets

    from dotmac_integration.ingress import _ENDPOINT_KEY_RE
    from dotmac_integration.lifecycle import _ENDPOINT_KEY_BYTES

    for _ in range(20):
        assert _ENDPOINT_KEY_RE.fullmatch(stdlib_secrets.token_hex(_ENDPOINT_KEY_BYTES))
    assert _ENDPOINT_KEY_BYTES * 8 >= 128, "an endpoint key is a bearer secret"
    assert not _ENDPOINT_KEY_RE.fullmatch(str(uuid.uuid4()))


# ── The uniqueness-race conversion ──────────────────────────────────────────
#
# `test_the_receipt_write_race_is_a_typed_refusal_not_a_driver_error` above
# asserts only that `ReceiptWriteRaced` EXISTS with a decided status — it
# executes no line of `record_batch`. The conversion itself (the constraint-name
# match, and the re-raise of everything else) had zero coverage: staging a real
# race needs Postgres, but the CONVERSION does not. The driver error is
# synthesised with the exact message Postgres emits, so a rename of the index
# on one side and not the other fails here rather than at 3am as a 500.


def _record_batch_against_a_raising_receiver(
    db: Session, monkeypatch: pytest.MonkeyPatch, orig: Exception
) -> None:
    from dotmac_integration import ingress as engine
    from sqlalchemy.exc import IntegrityError

    registry = fake_registry()
    installation, binding, _ = _endpoint(db, registry)
    prepared = PreparedIngress(
        endpoint_key="0" * 48,
        installation_id=installation.id,
        binding_id=binding.id,
        connector_key="conformance_fake",
        capability_id=FAKE_CAPABILITY,
    )

    def raising(*args: Any, **kwargs: Any) -> Any:
        raise IntegrityError("INSERT INTO inbox_receipts ...", {}, orig)

    monkeypatch.setattr(engine, "receive_verified", raising)
    record_batch(db, prepared, _events("a"))


def test_a_uniqueness_race_becomes_a_typed_refusal_not_a_driver_error(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loser of a concurrent redelivery must not reach a route as a 500.

    The message is the one Postgres actually emits, naming the index declared on
    `InboxReceipt` — so the two halves cannot drift apart silently.
    """
    from dotmac_integration.ingress import _RECEIPT_UNIQUE
    from dotmac_integration.models import InboxReceipt as Receipt

    declared = {c.name for c in Receipt.__table__.constraints} | {
        i.name for i in Receipt.__table__.indexes
    }
    assert _RECEIPT_UNIQUE in declared, (
        "the engine matches on a constraint name the model does not declare — "
        "every real race would surface as an untyped IntegrityError"
    )

    postgres_says = Exception(
        f'duplicate key value violates unique constraint "{_RECEIPT_UNIQUE}"'
    )
    with pytest.raises(ReceiptWriteRaced):
        _record_batch_against_a_raising_receiver(db, monkeypatch, postgres_says)


def test_an_unrelated_integrity_error_is_not_dressed_up_as_a_race(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sensitivity proof for the test above.

    A conversion that treated EVERY `IntegrityError` as the race would pass the
    test above while telling an operator that a genuine schema violation was a
    concurrent redelivery. It is still typed — the driver's message carries the
    bound parameters and must not travel — but it carries the OTHER code, and
    `ReceiptWriteFailed` is not a subclass of `ReceiptWriteRaced` in either
    direction, so `pytest.raises` cannot pass by inheritance.
    """
    with pytest.raises(ReceiptWriteFailed) as caught:
        _record_batch_against_a_raising_receiver(
            db, monkeypatch, Exception("null value in column violates not-null")
        )

    assert not isinstance(caught.value, ReceiptWriteRaced)
    assert caught.value.CODE is IngressCode.RECEIPT_WRITE_FAILED
    # The driver's message is what carries `[parameters: ...]`. `from None` is
    # what stops it, so the chain must be suppressed as well as replaced.
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_a_failed_write_carries_no_fragment_of_the_driver_message(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal's own text is a constant, like every other refusal's.

    `IngressRefused.__init__` takes no arguments, so this cannot regress by
    someone interpolating the driver error "just for operators" — but the
    conversion is new, and the sentinel proves the material really was in the
    exception it replaced.
    """
    leaky = Exception(f"INSERT failed [parameters: {{'payload': '{BODY_SENTINEL!r}'}}]")

    with pytest.raises(ReceiptWriteFailed) as caught:
        _record_batch_against_a_raising_receiver(db, monkeypatch, leaky)

    assert str(caught.value) == "the batch could not be recorded"
    assert "SENTINEL" not in str(caught.value)


# ── The outcome outlives the unit of work ───────────────────────────────────
#
# `RecordingUnitOfWork` above wraps ONE shared session that is never closed, so
# every post-commit read of an ORM attribute silently re-SELECTs instead of
# raising. The unit of work a deployment actually ships does not: it opens a
# fresh session per unit, commits on a clean exit and CLOSES it in a `finally`,
# with SQLAlchemy's default `expire_on_commit=True`. Anything the engine reads
# off an ORM object AFTER the block has exited is detached at that point.


class SessionPerUnitOfWork:
    """The unit of work this fleet actually ships, in miniature.

    `dotmac_kernel.db.platform_session` is a zero-argument context-manager
    factory that opens a fresh session, commits on a clean exit, rolls back on
    an exception and closes in a `finally` — so it IS a `UnitOfWork` and a
    consuming assembly can pass it literally. Reproduced here because the
    shared-session fixture cannot see a detached read.
    """

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.timeline: list[str] = []

    def __call__(self):  # type: ignore[no-untyped-def]
        return self._unit()

    @contextmanager
    def _unit(self) -> Iterator[Session]:
        session = Session(self.engine)
        self.timeline.append("enter")
        try:
            yield session
        except BaseException:
            session.rollback()
            self.timeline.append("rollback")
            raise
        else:
            session.commit()
            self.timeline.append("commit")
        finally:
            session.close()


def test_the_accepted_outcome_is_built_from_values_not_from_detached_rows(
    db: Session,
) -> None:
    """The mirror of the hazard this module exists to prevent, and worse.

    A batch that COMMITS and is then reported as a failure tells the provider
    the events were never recorded — the same lie as a partial write, inverted,
    and unlike a collision it never converges: the redelivery finds the rows,
    reads them off the same detached objects, and fails identically. The
    endpoint never answers 200 for that binding again, and a provider disables a
    webhook that keeps failing.
    """
    events = _events("a", "b", "c")
    registry = fake_registry(plugins=[fake_plugin(inbound=events)])
    _, binding, key = _endpoint(db, registry)
    db.commit()
    binding_id = binding.id
    engine = db.get_bind()
    db.close()

    outcome = receive(
        SessionPerUnitOfWork(engine),
        endpoint_id=key,
        raw_body=BODY_SENTINEL,
        headers=_headers(),
        registry=registry,
        resolve_secrets=_resolver(),
    )

    assert outcome.code is IngressCode.ACCEPTED
    assert (outcome.normalized, outcome.recorded, outcome.duplicates) == (3, 3, 0)
    assert len(outcome.receipt_ids) == 3
    assert all(isinstance(receipt_id, uuid.UUID) for receipt_id in outcome.receipt_ids)
    with Session(engine) as check:
        assert_every_event_recorded(
            check, check.get(CapabilityBinding, binding_id), events
        )


def test_a_redelivery_of_a_committed_batch_still_answers_two_hundred(
    db: Session,
) -> None:
    """The convergence half. A batch that landed must report as duplicates.

    `receive_verified` is idempotent, so the whole-batch retry is correct — but
    only if the ACCEPT path can be built at all after the unit of work closed.
    """
    events = _events("a", "b")
    registry = fake_registry(plugins=[fake_plugin(inbound=events)])
    _, _, key = _endpoint(db, registry)
    db.commit()
    engine = db.get_bind()
    db.close()

    def deliver() -> IngressOutcome:
        return receive(
            SessionPerUnitOfWork(engine),
            endpoint_id=key,
            raw_body=BODY_SENTINEL,
            headers=_headers(),
            registry=registry,
            resolve_secrets=_resolver(),
        )

    first, second = deliver(), deliver()

    assert (first.recorded, first.duplicates) == (2, 0)
    assert (second.status_code, second.code) == (200, IngressCode.ACCEPTED)
    assert (second.recorded, second.duplicates) == (0, 2)
    assert second.receipt_ids == first.receipt_ids


def test_record_batch_hands_back_values_that_need_no_session(db: Session) -> None:
    """The carrier rule applied to the RETURN path.

    `PreparedIngress` may hold no ORM object because one "would lazy-load
    against a transaction that has closed". The same is true of what
    `record_batch` hands back — and it is exported, so an assembly may call the
    three phases directly and place the boundary exactly where `receive` does.
    """
    registry = fake_registry()
    installation, binding, _ = _endpoint(db, registry)
    prepared = PreparedIngress(
        endpoint_key="0" * 48,
        installation_id=installation.id,
        binding_id=binding.id,
        connector_key="conformance_fake",
        capability_id=FAKE_CAPABILITY,
    )

    recorded = record_batch(db, prepared, _events("a"))

    assert isinstance(recorded, tuple)
    for receipt_id, is_new in recorded:
        assert isinstance(
            receipt_id, uuid.UUID
        ), "an ORM object travelled out of the recording phase"
        assert isinstance(is_new, bool)
