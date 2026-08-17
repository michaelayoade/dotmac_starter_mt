"""The minted ingress endpoint and the three-phase ingress engine.

Two slices, and the seam between them is the endpoint: minting decides whether
an address exists at all, and the engine decides what an existing address does
with a request.

## The test this file exists for

`test_a_configured_but_disabled_binding_answers_a_handshake_and_refuses_a_delivery`.
Several providers will not activate a subscription until the endpoint has
answered a GET handshake. A single eligibility predicate for both operations
makes that circular: the operator cannot enable the binding until the provider
is subscribed, and the provider cannot subscribe until the binding is enabled.
The endpoint refuses the one request that would unblock it, forever, and the
symptom is a 404 nobody can explain.

Everything else here is the supporting cast: the gates that must fire BEFORE a
key exists, the refusals that must be typed and constant, and the atomicity that
must survive a mid-batch collision.

## The fake connector lives here, not in the shipped kit

`dotmac_integration.conformance` is frozen and ships a DELIVERY fake. The
receiving contract (`IngressHandler`, `IngressPlugin`) is structural, so a fake
that satisfies it needs no change to the kit — it just needs the methods. When
the SPI freeze lifts and the receiving contract moves into `spi.py`, this fake
belongs in the kit beside the delivery one.
"""

from __future__ import annotations

import inspect
import logging
import re
import traceback
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import pytest
from dotmac_integration import (
    Acknowledgement,
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
    ConnectorRaised,
    ConnectorRegistry,
    ConnectorUnavailable,
    DeliveryAttempt,
    EndpointAddress,
    EndpointNotUsable,
    EndpointUnknown,
    EventSubscription,
    HandlerUnavailable,
    InboundEvent,
    InboxReceipt,
    IngressCode,
    IngressOperation,
    IngressOutcome,
    IngressRefused,
    IngressRequest,
    InvalidAcknowledgementError,
    LifecycleError,
    ManifestPinUnhonoured,
    ModeNotAvailable,
    NotAChallenge,
    PayloadTooLarge,
    PollingCheckpoint,
    PreparedIngress,
    ReceiptWriteFailed,
    SecretsUnavailable,
    VerificationResult,
    add_binding,
    answer_challenge,
    create_draft,
    enable,
    mint_ingress_endpoint,
    module,
    prepare_ingress,
    put_config_revision,
    receive,
    record_batch,
    refusal_outcome,
    revoke_ingress_endpoint,
    rotate_ingress_endpoint,
    set_binding_enabled,
    verify_and_normalize,
)
from dotmac_integration.conformance import FAKE_CAPABILITY, fake_manifest
from dotmac_integration.conformance import fake_registry as _fake_registry
from dotmac_integration.ingress import (
    HANDSHAKE_INSTALLATION_STATES,
    EndpointNotServiceable,
    challenge_response,
)
from dotmac_integration.spi import ConnectorManifest, ConnectorMode, Diagnostic
from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
from dotmac_kernel.models_platform import PlatformAdmin, PlatformAuditEvent
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

SECRET_SENTINEL = "SENTINEL-MATERIALIZED-SECRET-1a5f"
BODY_SENTINEL = b'{"leak":"SENTINEL-RAW-BODY-8f2a"}'
HEADER_SENTINEL = "SENTINEL-SIGNATURE-9c1d"

MODULE_MODELS = (
    ConnectorInstallation,
    ConnectorConfigRevision,
    CapabilityBinding,
    EventSubscription,
    InboxReceipt,
    DeliveryAttempt,
    PollingCheckpoint,
)


@pytest.fixture(autouse=True)
def _installed_integration_audit_actions() -> None:
    """The standalone module tests compose its declaration registry."""
    install_audit_actions(AuditActionRegistry.from_manifests([module]))


# ── A connector that RECEIVES ───────────────────────────────────────────────


_UNSET = object()


class IngressFake:
    """A connector that answers from constructor arguments — no provider.

    Deliberately a plain class rather than a frozen dataclass: several tests
    need it to be dishonest in a specific way (declare a mode it does not
    implement, return a shape the engine will not act on), and the point of each
    is that the ENGINE refuses rather than that the fake is well-behaved.

    It records what crossed the boundary so a test can assert that secrets
    arrived materialized and that no database session did.
    """

    def __init__(
        self,
        *,
        manifest: ConnectorManifest | None = None,
        historical: tuple[ConnectorManifest, ...] = (),
        # Declares BOTH because it implements both `handler_for` and
        # `ingress_handler_for`. SPI 1.1's mode conformance is two-way, so a
        # fake that implements a hook it does not declare is refused at
        # discovery — which is the guard working, not a fixture problem.
        # Tests that need a dishonest plugin still pass `modes=` explicitly.
        modes: frozenset[ConnectorMode] = frozenset(
            {ConnectorMode.INGRESS, ConnectorMode.DELIVERY}
        ),
        verified: bool | VerificationResult = True,
        events: tuple[InboundEvent, ...] = (),
        acknowledgement: Acknowledgement | None = None,
        normalize_returns: Any = _UNSET,
        challenge_answer: Any = _UNSET,
        raises: BaseException | None = None,
        handler_raises: BaseException | None = None,
        handler_returns: Any = _UNSET,
    ) -> None:
        self.manifest_ = manifest or fake_manifest()
        self.historical = historical
        self.modes_ = modes
        self.verified = verified
        self.events = events
        self.acknowledgement = acknowledgement
        self.normalize_returns = normalize_returns
        self.challenge_answer = challenge_answer
        self.raises = raises
        self.handler_raises = handler_raises
        self.handler_returns = handler_returns
        #: `(hook, request, config, secrets)` for every crossing.
        self.seen: list[tuple[str, IngressRequest, dict[str, object], Any]] = []

    # -- the frozen ConnectorPlugin half --
    @property
    def manifest(self) -> ConnectorManifest:
        return self.manifest_

    @property
    def historical_manifests(self) -> tuple[ConnectorManifest, ...]:
        return self.historical

    @property
    def modes(self) -> frozenset[ConnectorMode]:
        return self.modes_

    def handler_for(self, capability_id: str) -> Any:
        self.manifest_.require_declares(capability_id)
        return lambda request: None

    def validate_connection(
        self, *, config: dict[str, object], secrets: dict[str, object]
    ) -> tuple[Diagnostic, ...]:
        return (Diagnostic(ok=True, code="reachable"),)

    # -- the receiving half --
    def ingress_handler_for(self, capability_id: str) -> Any:
        if self.handler_raises is not None:
            raise self.handler_raises
        if self.handler_returns is not _UNSET:
            return self.handler_returns
        return _Handler(self)


class DeliveryOnlySpi:
    """Declares ingress, but genuinely does not implement the receiving hook.

    `IngressFake` can no longer express this: it always defines
    `ingress_handler_for`, and the engine's refusal is a STRUCTURAL check
    (`isinstance(plugin, IngressPlugin)`), not a behavioural one. The shipped
    conformance fake used to serve this role — it declared `INGRESS` and served
    no handler — but under SPI 1.1 it implements all three hooks, so the case
    needs a plugin written for it.
    """

    def __init__(self, *, modes: frozenset[ConnectorMode]) -> None:
        self.manifest_ = fake_manifest()
        self.modes_ = modes

    @property
    def manifest(self) -> ConnectorManifest:
        return self.manifest_

    @property
    def historical_manifests(self) -> tuple[ConnectorManifest, ...]:
        return ()

    @property
    def modes(self) -> frozenset[ConnectorMode]:
        return self.modes_

    def handler_for(self, capability_id: str) -> Any:
        self.manifest_.require_declares(capability_id)
        return lambda request: None

    def validate_connection(
        self, *, config: dict[str, object], secrets: dict[str, object]
    ) -> tuple[Diagnostic, ...]:
        return (Diagnostic(ok=True, code="reachable"),)


class _Handler:
    """The three receiving hooks, driven from the fake's knobs."""

    def __init__(self, fake: IngressFake) -> None:
        self.fake = fake

    def verify(
        self,
        request: IngressRequest,
        *,
        config: dict[str, object],
        secrets: dict[str, object],
    ) -> bool | VerificationResult:
        self.fake.seen.append(("verify", request, config, secrets))
        if self.fake.raises is not None:
            raise self.fake.raises
        return self.fake.verified

    def normalize(self, request: IngressRequest, *, config: dict[str, object]) -> Any:
        self.fake.seen.append(("normalize", request, config, None))
        if self.fake.raises is not None:
            raise self.fake.raises
        if self.fake.normalize_returns is not _UNSET:
            return self.fake.normalize_returns
        return self.fake.events, self.fake.acknowledgement

    def challenge(
        self,
        request: IngressRequest,
        *,
        config: dict[str, object],
        secrets: dict[str, object],
    ) -> Acknowledgement | None:
        self.fake.seen.append(("challenge", request, config, secrets))
        if self.fake.raises is not None:
            raise self.fake.raises
        if self.fake.challenge_answer is not _UNSET:
            return self.fake.challenge_answer  # type: ignore[no-any-return]
        return Acknowledgement(body=b"echo")


def registry_for(fake: Any) -> Any:
    return _fake_registry(plugins=[fake])


def installed_after_discovery(fake: Any) -> ConnectorRegistry:
    """A registry built WITHOUT discovery's compatibility refusal.

    Not a shortcut — it is the only way to model the case
    `require_compatible` exists for. `discover()` refuses an incompatible range
    at boot, so a registry that contains one can only have arrived the way a
    real one does: the distribution was installed, or upgraded, after discovery
    ran. Going through `fake_registry` here would fail in the FIXTURE and leave
    the request-time re-check untested.
    """
    return ConnectorRegistry((fake,))


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in MODULE_MODELS:
        model.__table__.create(engine)
    # The kernel's ONE platform audit ledger. Created here rather than spied on,
    # because "the key is never audited" is a claim about a persisted ROW and a
    # spy would only prove it about a function argument.
    PlatformAdmin.__table__.create(engine)
    PlatformAuditEvent.__table__.create(engine)
    with Session(engine) as session:
        yield session


class Uow:
    """Commits on a clean exit, unwinds on an exception — as a deployment's."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def __call__(self) -> Any:
        return self._unit()

    @contextmanager
    def _unit(self) -> Iterator[Session]:
        try:
            yield self.session
        except BaseException:
            self.session.rollback()
            raise
        else:
            self.session.commit()


def resolver(value: str = SECRET_SENTINEL) -> Any:
    def resolve(refs: Mapping[str, str]) -> Mapping[str, str]:
        return {name: value for name in refs}

    return resolve


def headers() -> dict[str, str]:
    return {"signature": HEADER_SENTINEL, "content-type": "application/json"}


def delivery_request() -> IngressRequest:
    return IngressRequest(raw_body=BODY_SENTINEL, headers=headers())


def build(
    db: Session,
    registry: Any,
    *,
    installation_state: str = "enabled",
    binding_enabled: bool = True,
    mint: bool = True,
) -> tuple[ConnectorInstallation, CapabilityBinding, str | None]:
    """An installation, a configured binding and (usually) a minted endpoint.

    `installation_state` and `binding_enabled` are separate knobs on purpose:
    the eligibility split is a claim about their INDEPENDENCE, and a factory
    that moved them together could not express the case the split exists for.
    """
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name=uuid.uuid4().hex
    )
    put_config_revision(
        db,
        installation,
        registry=registry,
        config={"variant": "a"},
        secret_refs={"signing": "bao://kv/signing"},
    )
    binding = add_binding(
        db, installation, registry=registry, capability_id=FAKE_CAPABILITY
    )
    if installation_state == "enabled":
        enable(db, installation, registry=registry)
    else:
        installation.state = installation_state
    if binding_enabled:
        # Enabling a binding requires an enabled installation, so the two knobs
        # are only independent in the direction the split needs them to be.
        set_binding_enabled(db, installation, binding, registry=registry, enabled=True)
    key = mint_ingress_endpoint(db, binding, registry=registry) if mint else None
    db.flush()
    return installation, binding, key


def address(key: str | None) -> EndpointAddress:
    assert key is not None
    return EndpointAddress(key)


# ══ Slice A — endpoint identity and lifecycle ═══════════════════════════════


def test_a_minted_key_is_192_bits_of_hex_on_the_binding(db: Session) -> None:
    """The shape is the one `prepare_ingress` will admit before it queries.

    A mismatch between what minting produces and what the engine accepts would
    make every minted endpoint a 404 — and, because the two refusals are
    deliberately indistinguishable, an unexplainable one.
    """
    registry = registry_for(IngressFake())
    _, binding, key = build(db, registry)

    assert key is not None
    assert re.fullmatch(r"[0-9a-f]{48}", key), key
    assert binding.ingress_endpoint_key == key


def test_two_bindings_get_distinct_keys(db: Session) -> None:
    registry = registry_for(IngressFake())
    _, _, first = build(db, registry)
    _, _, second = build(db, registry)
    assert first != second


def test_minting_twice_is_refused_rather_than_treated_as_rotation(
    db: Session,
) -> None:
    """Two different intentions. Only ONE of them should retire a URL that
    already lives in a third party's console."""
    registry = registry_for(IngressFake())
    _, binding, key = build(db, registry)

    with pytest.raises(LifecycleError, match="already publishes"):
        mint_ingress_endpoint(db, binding, registry=registry)
    assert binding.ingress_endpoint_key == key


# ── The gates that must fire BEFORE a key exists ────────────────────────────
#
# Each of these is a failure that would otherwise be DEFERRED to the provider's
# first request — the worst possible moment, because by then the address is in
# a third party's console and the operator has been told the integration is
# live.


def test_minting_refuses_a_connector_the_running_module_is_incompatible_with(
    db: Session,
) -> None:
    """The compatibility gate.

    The installation is drafted against a compatible connector and the INSTALLED
    distribution then moves to a range that excludes this module — which is the
    real sequence, since the plugin does not change, the host does.
    """
    registry = registry_for(IngressFake())
    _, binding, _ = build(db, registry, mint=False)

    upgraded = installed_after_discovery(
        IngressFake(manifest=fake_manifest(version="9.0.0", spi_range=">=9.0,<10.0"))
    )
    with pytest.raises(LifecycleError, match="not usable in this runtime"):
        mint_ingress_endpoint(db, binding, registry=upgraded)
    assert binding.ingress_endpoint_key is None


def test_minting_refuses_an_installation_whose_pin_is_no_longer_honoured(
    db: Session,
) -> None:
    """The manifest-pin gate — the one most easily forgotten.

    An installation whose connector was upgraded past its adoption window is
    still `enabled`, still passes every state check, and still fails every
    delivery. Minting for it publishes a URL whose only possible answer is 503.
    """
    registry = registry_for(IngressFake())
    _, binding, _ = build(db, registry, mint=False)

    # Same SPI range, different capability set -> a different digest, and no
    # historical manifest claiming the old one.
    superseded = registry_for(
        IngressFake(
            manifest=fake_manifest(
                version="2.0.0", capabilities=(FAKE_CAPABILITY, "conformance.two.v1")
            )
        )
    )
    with pytest.raises(LifecycleError, match="no longer honours"):
        mint_ingress_endpoint(db, binding, registry=superseded)
    assert binding.ingress_endpoint_key is None


def test_a_declared_historical_manifest_keeps_minting_open(db: Session) -> None:
    """The other half of the pin gate — otherwise it would be a check nothing
    can pass, which is indistinguishable from a check that always fires."""
    registry = registry_for(IngressFake())
    _, binding, _ = build(db, registry, mint=False)

    original = fake_manifest()
    adopted = registry_for(
        IngressFake(
            manifest=fake_manifest(
                version="2.0.0", capabilities=(FAKE_CAPABILITY, "conformance.two.v1")
            ),
            historical=(original,),
        )
    )
    assert mint_ingress_endpoint(db, binding, registry=adopted)


def test_minting_refuses_a_connector_that_does_not_declare_ingress(
    db: Session,
) -> None:
    """A delivery-only connector has no receiving address to publish."""
    registry = installed_after_discovery(
        IngressFake(modes=frozenset({ConnectorMode.DELIVERY}))
    )
    _, binding, _ = build(db, registry, mint=False)

    with pytest.raises(LifecycleError, match="does not declare ingress"):
        mint_ingress_endpoint(db, binding, registry=registry)
    assert binding.ingress_endpoint_key is None


def test_minting_refuses_a_connector_that_declares_ingress_without_implementing_it(
    db: Session,
) -> None:
    """BOTH halves of the claim are checked.

    SPI 1.1 refuses this shape at discovery, so the registry can only hold one
    the way a real deployment would: the distribution was installed, or
    upgraded, after discovery ran. The engine check is therefore defence in
    depth rather than dead code — without it, minting would succeed and the
    provider's first request would surface an `AttributeError` from inside the
    plugin phase, where there is no row to record it against.
    """
    registry = installed_after_discovery(
        DeliveryOnlySpi(
            modes=frozenset({ConnectorMode.INGRESS, ConnectorMode.DELIVERY})
        )
    )
    assert ConnectorMode.INGRESS in registry.plugin("conformance_fake").modes
    _, binding, _ = build(db, registry, mint=False)

    with pytest.raises(LifecycleError, match="serves no ingress handler"):
        mint_ingress_endpoint(db, binding, registry=registry)


def test_minting_does_not_require_an_enabled_binding(db: Session) -> None:
    """The anti-circularity property, one layer up from the engine.

    A binding is minted BEFORE it is enabled in every provider flow that
    requires a completed handshake first. Gating minting on `enabled` would
    rebuild the deadlock `answer_challenge` exists to break — the operator could
    not even obtain the URL to paste into the provider's console.
    """
    registry = registry_for(IngressFake())
    _, binding, key = build(
        db, registry, installation_state="draft", binding_enabled=False
    )
    assert binding.state == "disabled"
    assert key is not None


# ── Rotation and revocation ─────────────────────────────────────────────────


def test_rotation_replaces_the_address_and_keeps_every_receipt(db: Session) -> None:
    """Receipts are keyed on the BINDING, which is the property the primary key
    cannot offer — it is FK-referenced `ON DELETE CASCADE` from three tables."""
    event = InboundEvent(provider_event_id="evt-1", event_type="e", payload={"i": 1})
    registry = registry_for(IngressFake(events=(event,)))
    _, binding, first = build(db, registry)

    receive(
        Uow(db),
        endpoint=address(first),
        request=delivery_request(),
        registry=registry,
        resolve_secrets=resolver(),
    )
    assert db.query(InboxReceipt).count() == 1

    second = rotate_ingress_endpoint(db, binding, registry=registry)
    assert second != first
    assert binding.ingress_endpoint_key == second
    assert db.query(InboxReceipt).count() == 1, "rotation touched the evidence"

    # The retired address stops resolving, and does so as an UNKNOWN endpoint —
    # a rotated key must not be distinguishable from one that never existed.
    with pytest.raises(EndpointUnknown):
        prepare_ingress(db, endpoint=address(first), registry=registry)


def test_rotating_an_unminted_binding_is_refused(db: Session) -> None:
    """There is nothing to rotate, and treating it as a mint would skip the
    gates that only minting applies."""
    registry = registry_for(IngressFake())
    _, binding, _ = build(db, registry, mint=False)
    with pytest.raises(LifecycleError, match="nothing to rotate"):
        rotate_ingress_endpoint(db, binding, registry=registry)


def test_rotation_re_runs_the_gates(db: Session) -> None:
    """Rotation is exactly when an operator discovers the connector moved.

    Publishing a fresh address for an installation that can no longer serve it
    would replace a working URL with a permanently broken one — and the operator
    would have already pasted it into the provider's console by the time the
    first 503 arrived.
    """
    registry = registry_for(IngressFake())
    _, binding, key = build(db, registry)

    superseded = registry_for(
        IngressFake(
            manifest=fake_manifest(
                version="2.0.0", capabilities=(FAKE_CAPABILITY, "conformance.two.v1")
            )
        )
    )
    with pytest.raises(LifecycleError, match="no longer honours"):
        rotate_ingress_endpoint(db, binding, registry=superseded)
    assert binding.ingress_endpoint_key == key, "a refused rotation still rotated"


def test_revocation_withdraws_the_address_and_leaves_the_binding_alone(
    db: Session,
) -> None:
    """Not the same act as disabling. The binding keeps delivering and keeps
    every receipt; only the inbound address stops existing."""
    event = InboundEvent(provider_event_id="evt-1", event_type="e", payload={"i": 1})
    registry = registry_for(IngressFake(events=(event,)))
    _, binding, key = build(db, registry)
    receive(
        Uow(db),
        endpoint=address(key),
        request=delivery_request(),
        registry=registry,
        resolve_secrets=resolver(),
    )

    revoke_ingress_endpoint(db, binding)

    assert binding.ingress_endpoint_key is None
    assert binding.state == "enabled"
    assert db.query(InboxReceipt).count() == 1
    with pytest.raises(EndpointUnknown):
        prepare_ingress(db, endpoint=address(key), registry=registry)


def test_revocation_is_ungated_and_idempotent(db: Session) -> None:
    """A leak does not wait for a compatible connector.

    Every gate above exists to stop an address being PUBLISHED that cannot work;
    none of them has anything to say about withdrawing one. Refusing to revoke
    because the distribution went missing would leave a live bearer credential
    in a third party's console with no way to kill it — so `revoke` takes no
    registry at all, which is what makes it ungatable rather than merely
    ungated.
    """
    assert "registry" not in inspect.signature(revoke_ingress_endpoint).parameters

    registry = registry_for(IngressFake())
    _, binding, _ = build(db, registry)

    revoke_ingress_endpoint(db, binding)
    revoke_ingress_endpoint(db, binding)
    assert binding.ingress_endpoint_key is None


# ── Audit ───────────────────────────────────────────────────────────────────


def audit_rows(db: Session) -> list[PlatformAuditEvent]:
    return list(db.query(PlatformAuditEvent).order_by(PlatformAuditEvent.action).all())


def test_all_three_operations_write_a_platform_audit_event(db: Session) -> None:
    """Minting, rotating and revoking a bearer credential are each a
    security-relevant act, and the trail an incident reads is the platform
    ledger — not a second one this module keeps."""
    registry = registry_for(IngressFake())
    installation, binding, _ = build(db, registry)
    rotate_ingress_endpoint(db, binding, registry=registry, actor="ops@dotmac")
    revoke_ingress_endpoint(db, binding, actor="ops@dotmac")

    actions = [row.action for row in audit_rows(db)]
    assert actions == [
        "integration.ingress_endpoint.minted",
        "integration.ingress_endpoint.revoked",
        "integration.ingress_endpoint.rotated",
    ]
    for row in audit_rows(db):
        assert row.entity_type == "capability_binding"
        assert row.entity_id == str(binding.id)
        assert row.details["installation_id"] == str(installation.id)
        assert row.details["capability_id"] == FAKE_CAPABILITY


def test_the_declared_audit_actions_match_what_lifecycle_writes(db: Session) -> None:
    """ADR-0008's rule: a code with no writer is dead vocabulary that reads as a
    working trail, and a written code with no declaration cannot be reviewed.

    Checked in BOTH directions and against the ledger rather than against a
    constant, so a rename that touched only the declaration fails.
    """
    from dotmac_integration import module
    from dotmac_integration.lifecycle import ENDPOINT_AUDIT_ACTIONS
    from dotmac_integration.operations import AUDIT_ACTION_PREFIX

    declared = set(module.audit_actions)
    composed = {f"{AUDIT_ACTION_PREFIX}.{a}" for a in ENDPOINT_AUDIT_ACTIONS}
    assert composed <= declared

    registry = registry_for(IngressFake())
    _, binding, _ = build(db, registry)
    rotate_ingress_endpoint(db, binding, registry=registry)
    revoke_ingress_endpoint(db, binding)
    written = {row.action for row in audit_rows(db)}
    assert written == composed


def test_the_audit_trail_never_records_the_key(db: Session) -> None:
    """The ledger is read by more people, kept for longer and exported more
    often than any other table in the fleet. A bearer credential in it is a
    credential in every one of those places.

    Asserted against the PERSISTED ROW, not against a spy's kwargs: a spy would
    prove something about a function argument, and what matters is what survives
    in the database.
    """
    registry = registry_for(IngressFake())
    _, binding, minted = build(db, registry)
    rotated = rotate_ingress_endpoint(db, binding, registry=registry)
    revoke_ingress_endpoint(db, binding)
    db.flush()

    rendered = "\n".join(
        f"{row.action}|{row.entity_type}|{row.entity_id}|{row.details!r}"
        for row in audit_rows(db)
    )
    assert minted is not None
    for key in (minted, rotated):
        assert key not in rendered
        # nor a prefix long enough to matter, nor a digest of it
        assert key[:16] not in rendered

    # Sensitivity (ADR-0018): the rendering must actually contain the fields it
    # claims to search, or an empty string would pass for the wrong reason.
    assert str(binding.id) in rendered
    assert "ingress_endpoint.minted" in rendered


# ══ Slice B — the three-phase engine ═══════════════════════════════════════
#
# ── THE ELIGIBILITY SPLIT ───────────────────────────────────────────────────


def test_a_configured_but_disabled_binding_answers_a_handshake_and_refuses_a_delivery(
    db: Session,
) -> None:
    """THE CIRCULARITY CASE, in one test, in both directions.

    A provider that will not activate a subscription until the endpoint has
    answered a GET leaves the binding in exactly this state: configured, minted,
    and still DISABLED. With one predicate for both operations the handshake is
    refused, the subscription is never activated, and the binding can never be
    enabled — the endpoint refuses the one request that would unblock it.

    So the handshake must succeed HERE, and the delivery must still refuse HERE,
    and both halves are asserted together because either alone is satisfiable by
    a predicate that is simply wrong in the other direction.
    """
    registry = registry_for(IngressFake())
    _, binding, key = build(
        db, registry, installation_state="draft", binding_enabled=False
    )
    assert binding.state == "disabled"

    handshake = answer_challenge(
        Uow(db),
        endpoint=address(key),
        request=IngressRequest(params={"challenge": "abc"}),
        registry=registry,
        resolve_secrets=resolver(),
    )
    assert (handshake.status_code, handshake.code) == (
        200,
        IngressCode.CHALLENGE_ANSWERED,
    )
    assert handshake.acknowledgement is not None
    assert handshake.acknowledgement.body == b"echo"

    delivery = receive(
        Uow(db),
        endpoint=address(key),
        request=delivery_request(),
        registry=registry,
        resolve_secrets=resolver(),
    )
    assert (delivery.status_code, delivery.code) == (
        404,
        IngressCode.ENDPOINT_NOT_USABLE,
    )
    assert db.query(InboxReceipt).count() == 0


@pytest.mark.parametrize(
    ("installation_state", "binding_enabled", "handshake_ok", "delivery_ok"),
    [
        # The activation sequence, in order. A handshake is answered throughout
        # it; a delivery only at the end.
        ("draft", False, True, False),
        ("validating", False, True, False),
        ("enabled", False, True, False),
        ("enabled", True, True, True),
        # And the states that mean "stop". A handshake is a step in bringing an
        # integration UP; an operator who turned one off, or a platform that
        # stopped trusting it, stated the opposite — so NEITHER is served, and
        # a disabled installation is the case a `state != retired` check would
        # have wrongly admitted.
        ("disabled", False, False, False),
        ("quarantined", False, False, False),
        ("retired", False, False, False),
    ],
)
def test_the_two_operations_have_two_eligibility_rules(
    db: Session,
    installation_state: str,
    binding_enabled: bool,
    handshake_ok: bool,
    delivery_ok: bool,
) -> None:
    """The whole matrix, so the split is a property rather than one example.

    Read down the `handshake_ok` column: it never depends on `binding_enabled`.
    Read down `delivery_ok`: it depends on both. That difference IS the fix.
    """
    registry = registry_for(IngressFake())
    _, _, key = build(
        db,
        registry,
        installation_state=installation_state,
        binding_enabled=binding_enabled,
    )

    def prepared(operation: IngressOperation) -> bool:
        try:
            prepare_ingress(
                db, endpoint=address(key), registry=registry, operation=operation
            )
        except EndpointNotUsable:
            return False
        return True

    assert prepared(IngressOperation.HANDSHAKE) is handshake_ok
    assert prepared(IngressOperation.DELIVERY) is delivery_ok


def test_the_handshake_state_set_is_the_bring_up_states_only() -> None:
    """Stated as a frozen set rather than as `not in {retired}`.

    A negative rule silently admits every state added later — including
    `quarantined`, which was added to mean "the platform stopped trusting
    this". An allowlist makes a new state a deliberate decision.
    """
    assert HANDSHAKE_INSTALLATION_STATES == frozenset(
        {"draft", "validating", "enabled"}
    )

    from dotmac_integration.models import INSTALLATION_STATES

    assert HANDSHAKE_INSTALLATION_STATES < set(INSTALLATION_STATES)
    assert set(INSTALLATION_STATES) - HANDSHAKE_INSTALLATION_STATES == {
        "disabled",
        "quarantined",
        "retired",
    }


def test_delivery_and_handshake_never_fall_through_to_each_other(
    db: Session,
) -> None:
    """A bodyless POST is still a DELIVERY.

    Inferring the operation from an empty body is wrong in both directions: a
    provider that signs an empty body and expects the event recorded would get a
    400 while being told the endpoint worked, and a provider that confirms a
    subscription with a BODIED request could not handshake at all.
    """
    registry = registry_for(IngressFake(events=()))
    _, _, key = build(db, registry)

    empty_post = receive(
        Uow(db),
        endpoint=address(key),
        request=IngressRequest(raw_body=b"", headers=headers()),
        registry=registry,
        resolve_secrets=resolver(),
    )
    assert empty_post.code is IngressCode.ACCEPTED
    assert [hook for hook, *_ in registry.plugin("conformance_fake").seen] == [
        "verify",
        "normalize",
    ], "an empty body reached the handshake hook"

    bodied_get = answer_challenge(
        Uow(db),
        endpoint=address(key),
        request=IngressRequest(raw_body=b"{}", params={"challenge": "abc"}),
        registry=registry,
        resolve_secrets=resolver(),
    )
    assert bodied_get.code is IngressCode.CHALLENGE_ANSWERED


# ── Phase 1: prepare ────────────────────────────────────────────────────────


def test_a_malformed_key_is_indistinguishable_from_an_unknown_one(
    db: Session,
) -> None:
    """Distinguishing them turns the endpoint into a probing oracle that tells a
    scanner when it has guessed the right SHAPE."""
    registry = registry_for(IngressFake())
    build(db, registry)

    malformed = refusal_outcome(
        _refusal(
            lambda: prepare_ingress(
                db, endpoint=EndpointAddress("nope"), registry=registry
            )
        )
    )
    unknown = refusal_outcome(
        _refusal(
            lambda: prepare_ingress(
                db, endpoint=EndpointAddress("f" * 48), registry=registry
            )
        )
    )
    assert malformed == unknown
    assert (malformed.status_code, malformed.code) == (
        404,
        IngressCode.ENDPOINT_UNKNOWN,
    )


def _refusal(call: Any) -> IngressRefused:
    with pytest.raises(IngressRefused) as raised:
        call()
    return raised.value


def test_a_malformed_key_never_reaches_sql(db: Session) -> None:
    """Validated against a fixed shape BEFORE any query, so an unbounded
    attacker string never becomes a bound parameter."""
    registry = registry_for(IngressFake())
    build(db, registry)

    class Tripwire:
        def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("a malformed key reached the database")

        def get(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("a malformed key reached the database")

    with pytest.raises(EndpointUnknown):
        prepare_ingress(
            Tripwire(), endpoint=EndpointAddress("' OR 1=1 --"), registry=registry
        )


def test_prepare_re_checks_compatibility_and_the_pin_at_request_time(
    db: Session,
) -> None:
    """Minting checked both, months ago, and cannot know what is installed now.

    This is the case that actually bites: the plugin did not change, the host
    did — or the distribution was upgraded past its adoption window while the
    endpoint stayed live in a provider's console.
    """
    registry = registry_for(IngressFake())
    _, _, key = build(db, registry)

    incompatible = installed_after_discovery(
        IngressFake(manifest=fake_manifest(version="9.0.0", spi_range=">=9.0,<10.0"))
    )
    with pytest.raises(ConnectorUnavailable):
        prepare_ingress(db, endpoint=address(key), registry=incompatible)

    superseded = registry_for(
        IngressFake(
            manifest=fake_manifest(
                version="2.0.0", capabilities=(FAKE_CAPABILITY, "conformance.two.v1")
            )
        )
    )
    with pytest.raises(ManifestPinUnhonoured):
        prepare_ingress(db, endpoint=address(key), registry=superseded)


def test_prepare_refuses_a_connector_that_stopped_receiving(db: Session) -> None:
    """A STATED refusal, before any handler lookup — never an `AttributeError`
    from inside one. Reaching this means the installed distribution changed
    under a live endpoint, which is exactly what a check at minting cannot see.
    """
    registry = registry_for(IngressFake())
    _, _, key = build(db, registry)

    for changed in (
        installed_after_discovery(
            IngressFake(modes=frozenset({ConnectorMode.DELIVERY}))
        ),
        installed_after_discovery(  # declares INGRESS, serves no handler
            DeliveryOnlySpi(
                modes=frozenset({ConnectorMode.INGRESS, ConnectorMode.DELIVERY})
            )
        ),
    ):
        with pytest.raises(ModeNotAvailable):
            prepare_ingress(db, endpoint=address(key), registry=changed)


def test_the_carrier_holds_no_session_and_no_endpoint_key(db: Session) -> None:
    """`PreparedIngress` crosses into a phase that has no transaction.

    An ORM object would lazy-load against a session that has closed; `slots`
    stops one being smuggled on as an ad-hoc attribute. And the endpoint key is
    not a field at all — a value that does not exist in a frame cannot be
    rendered out of one, which is stronger than hiding it would be.
    """
    registry = registry_for(IngressFake())
    installation, binding, key = build(db, registry)
    prepared = prepare_ingress(db, endpoint=address(key), registry=registry)

    assert isinstance(prepared, PreparedIngress)
    assert not hasattr(prepared, "__dict__"), "slots would not stop a smuggled session"
    assert "endpoint" not in set(PreparedIngress.__dataclass_fields__)
    assert key is not None
    assert key not in repr(prepared)
    assert prepared.installation_id == installation.id
    assert prepared.binding_id == binding.id
    assert prepared.secret_refs == {"signing": "bao://kv/signing"}
    for value in vars(type(prepared)).values():
        assert not isinstance(value, Session)


# ── Phase 2: verify and normalize, WITHOUT a session ────────────────────────


def test_the_plugin_phase_cannot_be_handed_a_session() -> None:
    """The boundary is enforced by what a caller CANNOT pass.

    A comment asking a caller not to hold a transaction across provider-shaped
    work is not a boundary; a signature with no session parameter is.
    """
    for phase in (verify_and_normalize, challenge_response):
        parameters = inspect.signature(phase).parameters
        assert "db" not in parameters
        assert "session" not in parameters
    assert list(inspect.signature(verify_and_normalize).parameters) == [
        "prepared",
        "request",
        "registry",
        "resolve_secrets",
        "observe_verification",
    ]
    assert list(inspect.signature(challenge_response).parameters) == [
        "prepared",
        "request",
        "registry",
        "resolve_secrets",
    ]


def test_one_request_object_reaches_both_hooks(db: Session) -> None:
    """What `verify` authenticated is provably what `normalize` interpreted.

    Every provider worth verifying signs the BYTES rather than a
    re-serialization of them, so a decode-and-re-encode anywhere on this path
    would invalidate a correct HMAC — and two separately-derived copies would
    let the two hooks disagree with nothing noticing.
    """
    fake = IngressFake(events=())
    registry = registry_for(fake)
    _, _, key = build(db, registry)
    request = delivery_request()

    receive(
        Uow(db),
        endpoint=address(key),
        request=request,
        registry=registry,
        resolve_secrets=resolver(),
    )
    seen = [(hook, seen_request) for hook, seen_request, _, _ in fake.seen]
    assert [hook for hook, _ in seen] == ["verify", "normalize"]
    assert all(seen_request is request for _, seen_request in seen)


def test_secrets_arrive_materialized_and_references_do_not(db: Session) -> None:
    """The module HOLDS references and the deployment dereferences them
    (ADR-0009). A handler receives values, never a `bao://` pointer."""
    fake = IngressFake(events=())
    registry = registry_for(fake)
    _, _, key = build(db, registry)

    receive(
        Uow(db),
        endpoint=address(key),
        request=delivery_request(),
        registry=registry,
        resolve_secrets=resolver(),
    )
    verify_call = next(entry for entry in fake.seen if entry[0] == "verify")
    assert verify_call[3] == {"signing": SECRET_SENTINEL}
    assert verify_call[2] == {"variant": "a"}


def test_a_rejected_signature_persists_nothing(db: Session) -> None:
    """401, and no table for unverified bodies — storing what failed
    verification is storing exactly the material that must not be stored."""
    fake = IngressFake(verified=False)
    registry = registry_for(fake)
    _, _, key = build(db, registry)

    outcome = receive(
        Uow(db),
        endpoint=address(key),
        request=delivery_request(),
        registry=registry,
        resolve_secrets=resolver(),
    )
    assert (outcome.status_code, outcome.code) == (401, IngressCode.SIGNATURE_REJECTED)
    assert outcome.acknowledgement is None
    assert db.query(InboxReceipt).count() == 0
    assert [hook for hook, *_ in fake.seen] == ["verify"], "normalize was reached"


def test_verification_evidence_reaches_a_generic_observer_before_normalization(
    db: Session,
) -> None:
    """A connector may identify WHICH configured secret positions matched.

    The engine reports those positions through a provider-neutral observer.  It
    never learns a secret name or value, while the assembly can count rotation
    traffic without importing or branching on a connector.
    """
    evidence = VerificationResult(accepted=True, matched_secret_positions=(0, 2))
    fake = IngressFake(verified=evidence, normalize_returns=object())
    registry = registry_for(fake)
    _, _, key = build(db, registry)
    observed: list[VerificationResult] = []

    outcome = receive(
        Uow(db),
        endpoint=address(key),
        request=delivery_request(),
        registry=registry,
        resolve_secrets=resolver(),
        observe_verification=observed.append,
    )

    assert observed == [evidence]
    assert outcome.code is IngressCode.CONNECTOR_CONTRACT
    assert db.query(InboxReceipt).count() == 0


def test_a_legacy_boolean_verification_result_remains_compatible(db: Session) -> None:
    """SPI 1.2 adds evidence without invalidating honest SPI 1.0/1.1 plugins."""
    fake = IngressFake(verified=True)
    registry = registry_for(fake)
    _, _, key = build(db, registry)
    observed: list[VerificationResult] = []

    receive(
        Uow(db),
        endpoint=address(key),
        request=delivery_request(),
        registry=registry,
        resolve_secrets=resolver(),
        observe_verification=observed.append,
    )

    assert observed == [VerificationResult(accepted=True, matched_secret_positions=())]


def test_a_truthy_non_contract_verification_result_is_refused(db: Session) -> None:
    """Before SPI 1.2 any truthy object silently authenticated a request."""
    fake = IngressFake(verified=object())  # type: ignore[arg-type]
    registry = registry_for(fake)
    _, _, key = build(db, registry)

    outcome = receive(
        Uow(db),
        endpoint=address(key),
        request=delivery_request(),
        registry=registry,
        resolve_secrets=resolver(),
    )

    assert outcome.code is IngressCode.CONNECTOR_CONTRACT
    assert db.query(InboxReceipt).count() == 0


def test_a_raising_verification_observer_cannot_break_ingress(db: Session) -> None:
    """Telemetry is optional evidence, never an ingress availability dependency."""
    event = InboundEvent(
        provider_event_id="evt-observer-failure",
        event_type="e",
        payload={"i": 1},
    )
    fake = IngressFake(
        verified=VerificationResult(True, (1,)),
        events=(event,),
    )
    registry = registry_for(fake)
    _, _, key = build(db, registry)

    def unavailable(_result: VerificationResult) -> None:
        raise RuntimeError("metrics backend unavailable")

    outcome = receive(
        Uow(db),
        endpoint=address(key),
        request=delivery_request(),
        registry=registry,
        resolve_secrets=resolver(),
        observe_verification=unavailable,
    )

    assert outcome.code is IngressCode.ACCEPTED
    assert (
        db.query(InboxReceipt)
        .filter_by(provider_event_id=event.provider_event_id)
        .one()
    )


def test_verification_evidence_has_no_surface_for_secret_material() -> None:
    evidence = VerificationResult(True, (0,))

    assert set(evidence.__slots__) == {"accepted", "matched_secret_positions"}
    assert SECRET_SENTINEL not in repr(evidence)
    with pytest.raises((AttributeError, TypeError)):
        object.__setattr__(evidence, "secret", SECRET_SENTINEL)


def test_a_raising_connector_carries_only_its_type_name(db: Session) -> None:
    """The plugin's message is built from provider-controlled bytes.

    So the TYPE NAME travels and the text does not — and `.isidentifier()` is
    what makes that structural rather than a convention a message could
    masquerade past.
    """
    leak = "the body was " + BODY_SENTINEL.decode()
    fake = IngressFake(raises=ValueError(leak))
    registry = registry_for(fake)
    _, _, key = build(db, registry)

    refusal = _refusal(
        lambda: verify_and_normalize(
            prepare_ingress(db, endpoint=address(key), registry=registry),
            request=delivery_request(),
            registry=registry,
            resolve_secrets=resolver(),
        )
    )
    assert isinstance(refusal, ConnectorRaised)
    assert "ValueError" in str(refusal)
    assert leak not in str(refusal)
    assert refusal.__cause__ is None, "`from None` was dropped; the message chains"

    assert "Exception" in str(ConnectorRaised("not an identifier at all"))


def test_a_secret_resolver_failure_is_typed_and_sanitised(db: Session) -> None:
    """A secret store's own error text routinely names the path, the token or
    the response body that failed. It is the one exception on this path that
    must never be chained."""
    registry = registry_for(IngressFake())
    _, _, key = build(db, registry)

    def exploding(refs: Mapping[str, str]) -> Mapping[str, str]:
        raise RuntimeError(f"bao denied token {SECRET_SENTINEL} at kv/signing")

    outcome = receive(
        Uow(db),
        endpoint=address(key),
        request=delivery_request(),
        registry=registry,
        resolve_secrets=exploding,
    )
    assert (outcome.status_code, outcome.code) == (
        503,
        IngressCode.SECRETS_UNAVAILABLE,
    )
    refusal = _refusal(
        lambda: verify_and_normalize(
            prepare_ingress(db, endpoint=address(key), registry=registry),
            request=delivery_request(),
            registry=registry,
            resolve_secrets=exploding,
        )
    )
    assert isinstance(refusal, SecretsUnavailable)
    assert SECRET_SENTINEL not in str(refusal)
    assert refusal.__cause__ is None


def test_a_failed_handler_lookup_is_typed_and_sanitised(db: Session) -> None:
    """A connector that receives, but not for THIS capability.

    A different operator problem from one that does not receive at all, and
    both must arrive as a decided status rather than as whatever the plugin
    chose to throw.
    """
    registry = registry_for(IngressFake())
    _, _, key = build(db, registry)
    prepared = prepare_ingress(db, endpoint=address(key), registry=registry)

    for broken in (
        installed_after_discovery(
            IngressFake(handler_raises=KeyError(SECRET_SENTINEL))
        ),
        installed_after_discovery(IngressFake(handler_returns=object())),
    ):
        refusal = _refusal(
            lambda registry=broken: verify_and_normalize(
                prepared,
                request=delivery_request(),
                registry=registry,
                resolve_secrets=resolver(),
            )
        )
        assert isinstance(refusal, HandlerUnavailable)
        assert SECRET_SENTINEL not in str(refusal)
        assert refusal.__cause__ is None


@pytest.mark.parametrize(
    "returned",
    [
        (InboundEvent(provider_event_id="a", event_type="e", payload={}),),
        ((InboundEvent(provider_event_id="a", event_type="e", payload={}),),),
        ((), b"raw bytes"),
        ("not a tuple at all"),
        ((["not a tuple"], None)),
        (((object(),), None)),
    ],
)
def test_normalize_must_return_the_pair_or_nothing_is_acted_on(
    db: Session, returned: Any
) -> None:
    """The two halves have very different destinations: the first is iterated
    into the database, the second is written into an HTTP response.

    A plugin returning a bare tuple of events would otherwise be indexed, and
    its first `InboundEvent` would become the acknowledgement — provider content
    written back to the provider as a body the engine never inspected.
    """
    registry = registry_for(IngressFake(normalize_returns=returned))
    _, _, key = build(db, registry)

    outcome = receive(
        Uow(db),
        endpoint=address(key),
        request=delivery_request(),
        registry=registry,
        resolve_secrets=resolver(),
    )
    assert (outcome.status_code, outcome.code) == (
        503,
        IngressCode.CONNECTOR_CONTRACT,
    )
    assert db.query(InboxReceipt).count() == 0


def test_a_challenge_that_is_not_recognised_is_a_400(db: Session) -> None:
    registry = registry_for(IngressFake(challenge_answer=None))
    _, _, key = build(db, registry)

    outcome = answer_challenge(
        Uow(db),
        endpoint=address(key),
        request=IngressRequest(params={"nothing": "here"}),
        registry=registry,
        resolve_secrets=resolver(),
    )
    assert (outcome.status_code, outcome.code) == (400, IngressCode.NOT_A_CHALLENGE)
    assert isinstance(
        _refusal(
            lambda: challenge_response(
                prepare_ingress(
                    db,
                    endpoint=address(key),
                    registry=registry,
                    operation=IngressOperation.HANDSHAKE,
                ),
                request=IngressRequest(),
                registry=registry,
                resolve_secrets=resolver(),
            )
        ),
        NotAChallenge,
    )


def test_a_challenge_returning_a_non_acknowledgement_is_refused(db: Session) -> None:
    """The return value goes into an HTTP response body. The engine does not
    write back whatever it is handed."""
    registry = registry_for(IngressFake(challenge_answer=b"bare bytes"))
    _, _, key = build(db, registry)

    outcome = answer_challenge(
        Uow(db),
        endpoint=address(key),
        request=IngressRequest(params={"challenge": "abc"}),
        registry=registry,
        resolve_secrets=resolver(),
    )
    assert outcome.code is IngressCode.CONNECTOR_CONTRACT


# ── The acknowledgement: the connector owns the BODY, the engine the STATUS ──


def test_the_connector_cannot_choose_a_status_code() -> None:
    """An ingress status is a RETRY INSTRUCTION to the provider — 200 means
    "never send this again". Only the engine knows whether the batch committed,
    so a connector-chosen status would let a plugin acknowledge events that were
    rolled back."""
    fields = set(Acknowledgement.__dataclass_fields__)
    assert fields == {"body", "media_type"}


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [(None, "application/json"), ("application/xml", "application/xml")],
)
def test_the_engine_supplies_the_delivery_media_type_default(
    db: Session, supplied: str | None, expected: str
) -> None:
    registry = registry_for(
        IngressFake(events=(), acknowledgement=Acknowledgement(b"ok", supplied))
    )
    _, _, key = build(db, registry)
    outcome = receive(
        Uow(db),
        endpoint=address(key),
        request=delivery_request(),
        registry=registry,
        resolve_secrets=resolver(),
    )
    assert outcome.acknowledgement is not None
    assert outcome.acknowledgement.media_type == expected


def test_the_handshake_default_is_plain_text(db: Session) -> None:
    """Providers compare the RAW echoed body. Wrapping it in JSON fails the
    subscription with a 200 — the least debuggable outcome there is."""
    registry = registry_for(IngressFake())
    _, _, key = build(db, registry)
    outcome = answer_challenge(
        Uow(db),
        endpoint=address(key),
        request=IngressRequest(params={"challenge": "abc"}),
        registry=registry,
        resolve_secrets=resolver(),
    )
    assert outcome.acknowledgement is not None
    assert outcome.acknowledgement.media_type == "text/plain"


@pytest.mark.parametrize(
    "media_type",
    ["text/plain\r\nX-Injected: yes", "text/plain\nSet-Cookie: a=b", "notatype", ""],
)
def test_a_media_type_is_validated_because_it_is_a_response_header(
    media_type: str,
) -> None:
    """An unvalidated media type carrying CRLF is header injection performed by
    a connector — through a field the engine writes into a response verbatim."""
    with pytest.raises(InvalidAcknowledgementError):
        Acknowledgement(body=b"x", media_type=media_type)


# ── Phase 3: record the WHOLE batch, atomically ─────────────────────────────


def test_the_whole_batch_is_recorded_and_counted(db: Session) -> None:
    events = tuple(
        InboundEvent(provider_event_id=f"evt-{i}", event_type="e", payload={"i": i})
        for i in range(3)
    )
    registry = registry_for(IngressFake(events=events))
    installation, binding, key = build(db, registry)

    outcome = receive(
        Uow(db),
        endpoint=address(key),
        request=delivery_request(),
        registry=registry,
        resolve_secrets=resolver(),
    )
    assert (outcome.status_code, outcome.code) == (200, IngressCode.ACCEPTED)
    assert (outcome.normalized, outcome.recorded, outcome.duplicates) == (3, 3, 0)
    assert len(outcome.receipt_ids) == 3
    assert outcome.installation_id == installation.id
    assert outcome.binding_id == binding.id
    assert db.query(InboxReceipt).count() == 3


def test_a_redelivered_batch_is_a_200_of_duplicates(db: Session) -> None:
    """`receive_verified` is idempotent on `(binding, provider_event_id)`, which
    is what makes a whole-batch retry after a 5xx correct rather than
    duplicative."""
    events = (
        InboundEvent(provider_event_id="evt-1", event_type="e", payload={"i": 1}),
    )
    registry = registry_for(IngressFake(events=events))
    _, _, key = build(db, registry)

    for _ in range(2):
        outcome = receive(
            Uow(db),
            endpoint=address(key),
            request=delivery_request(),
            registry=registry,
            resolve_secrets=resolver(),
        )
    assert (outcome.recorded, outcome.duplicates) == (0, 1)
    assert db.query(InboxReceipt).count() == 1


def test_one_collision_rolls_back_the_whole_batch(db: Session) -> None:
    """A partial write is worse than a refusal, because it LOOKS DELIVERED.

    The provider is told the batch was accepted while some events were never
    recorded, and it does not resend the ones that landed. So the second event
    colliding must take the first and third with it.
    """
    from dotmac_integration import receive_verified

    registry = registry_for(IngressFake())
    installation, binding, key = build(db, registry)
    receive_verified(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        provider_event_id="evt-b",
        event_type="e",
        payload={"content": "the original"},
    )
    db.commit()

    events = tuple(
        InboundEvent(provider_event_id=f"evt-{c}", event_type="e", payload={"c": c})
        for c in ("a", "b", "c")
    )
    fake = registry_for(IngressFake(events=events))
    outcome = receive(
        Uow(db),
        endpoint=address(key),
        request=delivery_request(),
        registry=fake,
        resolve_secrets=resolver(),
    )

    assert (outcome.status_code, outcome.code) == (
        503,
        IngressCode.EVENT_IDENTITY_COLLISION,
    )
    assert outcome.acknowledgement is None, "a batch that rolled back was acknowledged"
    db.rollback()
    stored = {row.provider_event_id for row in db.query(InboxReceipt).all()}
    assert stored == {"evt-b"}, "an event recorded before the collision survived"


def test_record_batch_takes_the_tuple_so_there_is_no_loop_to_split(
    db: Session,
) -> None:
    """Whole-batch atomicity is STRUCTURAL rather than a convention: there is no
    per-event entry point for a caller to put a transaction boundary inside."""
    parameters = inspect.signature(record_batch).parameters
    assert list(parameters) == ["db", "prepared", "events"]
    assert parameters["events"].annotation == "tuple[InboundEvent, ...]"


def test_a_write_failure_becomes_a_typed_refusal_not_a_driver_message(
    db: Session,
) -> None:
    """SQLAlchemy's `StatementError.__str__` appends `[SQL: ...] [parameters:
    (...)]` unless the engine sets `hide_parameters`, which nothing in this
    fleet does — and those parameters are the normalized payload verbatim."""
    leak = "SENTINEL-NORMALIZED-PAYLOAD-3d0c"
    events = (
        InboundEvent(
            provider_event_id="evt-1",
            event_type=None,  # type: ignore[arg-type]
            payload={"leak": leak},
        ),
    )
    registry = registry_for(IngressFake(events=events))
    _, _, key = build(db, registry)

    outcome = receive(
        Uow(db),
        endpoint=address(key),
        request=delivery_request(),
        registry=registry,
        resolve_secrets=resolver(),
    )
    assert (outcome.status_code, outcome.code) == (
        503,
        IngressCode.RECEIPT_WRITE_FAILED,
    )
    assert leak not in repr(outcome)
    assert outcome.receipt_ids == ()


def test_a_blank_provider_event_id_is_a_typed_refusal(db: Session) -> None:
    """`receive_verified` raises `ExecutionError` for a blank id. Its message is
    a constant, so nothing leaks along it — but untyped it leaves the edge
    inventing a status for a case the module already decided."""
    registry = registry_for(
        IngressFake(
            events=(InboundEvent(provider_event_id="   ", event_type="e", payload={}),)
        )
    )
    _, _, key = build(db, registry)
    prepared = prepare_ingress(db, endpoint=address(key), registry=registry)

    with pytest.raises(ReceiptWriteFailed):
        record_batch(
            db,
            prepared,
            (InboundEvent(provider_event_id="  ", event_type="e", payload={}),),
        )


def test_request_headers_are_never_persisted(db: Session) -> None:
    """`headers_json` ends up in every backup. Forwarding the request headers
    would put the signature header — and any authorization header or cookie a
    misconfigured proxy passed through — permanently in the database.

    A connector that needs a provider request id lifts it into the payload
    during `normalize`, which makes header retention a connector decision
    expressed as normalized data rather than a blanket copy.
    """
    events = (
        InboundEvent(provider_event_id="evt-1", event_type="e", payload={"i": 1}),
    )
    registry = registry_for(IngressFake(events=events))
    _, _, key = build(db, registry)

    receive(
        Uow(db),
        endpoint=address(key),
        request=delivery_request(),
        registry=registry,
        resolve_secrets=resolver(),
    )
    receipt = db.query(InboxReceipt).one()
    assert receipt.headers_json is None
    assert HEADER_SENTINEL not in repr(receipt.payload_json)


def test_no_connector_code_runs_after_the_batch_commits(db: Session) -> None:
    """The acknowledgement is BUILT before persistence and EMITTED after it.

    A plugin call after the commit would put a raise on the far side of a
    durable write: the events are stored, the provider is told 5xx, and it
    redelivers forever into a handler that keeps raising.
    """
    events = (
        InboundEvent(provider_event_id="evt-1", event_type="e", payload={"i": 1}),
    )
    fake = IngressFake(events=events, acknowledgement=Acknowledgement(b"ok"))
    registry = registry_for(fake)
    _, _, key = build(db, registry)

    outcome = receive(
        Uow(db),
        endpoint=address(key),
        request=delivery_request(),
        registry=registry,
        resolve_secrets=resolver(),
    )
    assert outcome.code is IngressCode.ACCEPTED
    # `verify` and `normalize`, and nothing after them.
    assert [hook for hook, *_ in fake.seen] == ["verify", "normalize"]


# ── The refusal vocabulary ──────────────────────────────────────────────────


def leaf_refusals() -> list[type[IngressRefused]]:
    """Every concrete refusal, walked rather than listed.

    A hand-written list is the thing that goes stale: a new refusal added
    without a status is exactly what this walk exists to catch, and a list would
    simply not mention it.
    """
    found: list[type[IngressRefused]] = []
    pending = [IngressRefused]
    while pending:
        current = pending.pop()
        children = current.__subclasses__()
        pending.extend(children)
        if not children:
            found.append(current)
    return found


def test_every_leaf_refusal_decides_a_code_and_a_status() -> None:
    """A grouping base carries neither, so a new leaf cannot inherit a status by
    accident — an ingress status is a retry instruction, and inheriting the
    wrong one silently destroys events."""
    leaves = leaf_refusals()
    assert len(leaves) >= 14, "the walk found almost nothing"
    for refusal in leaves:
        assert "CODE" in vars(refusal), refusal.__name__
        assert "STATUS" in vars(refusal), refusal.__name__
        assert refusal.MESSAGE, refusal.__name__

    # The grouping bases deliberately decide neither.
    for base in (IngressRefused, EndpointNotServiceable):
        assert "STATUS" not in vars(base)
        assert "CODE" not in vars(base)


def test_every_refusal_takes_no_arguments_except_the_type_name_one() -> None:
    """Interpolating a request fragment into a refusal is not a discipline
    someone has to remember — it is impossible, because there is nothing to
    interpolate into."""
    for refusal in leaf_refusals():
        if refusal is ConnectorRaised:
            continue
        assert list(inspect.signature(refusal).parameters) == [], refusal.__name__
        assert str(refusal()) == refusal.MESSAGE


def test_every_our_side_refusal_answers_503() -> None:
    """The provider's redelivery window is the only remaining copy of those
    events. A 4xx for our own misconfiguration throws them away."""
    for refusal in leaf_refusals():
        if issubclass(refusal, EndpointNotServiceable):
            assert refusal.STATUS == 503, refusal.__name__


def test_payload_too_large_is_defined_here_and_raised_at_the_edge() -> None:
    """A byte cap that varied by connector would be a connector-specific limit,
    and this module may hold none. So the edge constructs the refusal and asks
    for the status rather than authoring `413` itself."""
    from dotmac_integration import ingress as engine

    assert "raise PayloadTooLarge" not in inspect.getsource(engine)
    for name, value in vars(engine).items():
        if inspect.isfunction(value) and value.__module__ == engine.__name__:
            assert "max_bytes" not in inspect.signature(value).parameters, name

    outcome = refusal_outcome(PayloadTooLarge())
    assert (outcome.status_code, outcome.code) == (413, IngressCode.PAYLOAD_TOO_LARGE)


def test_the_outcome_carries_no_field_that_could_hold_request_material() -> None:
    """THE REDACTION BOUNDARY. A consuming assembly serializes the scalar fields
    of this object with no field allowlist, so a field able to hold a body, a
    header, a query parameter, a secret or the endpoint key would leak it by
    construction."""
    scalars = {
        name: field.type
        for name, field in IngressOutcome.__dataclass_fields__.items()
        if name != "acknowledgement"
    }
    assert set(scalars) == {
        "status_code",
        "code",
        "installation_id",
        "binding_id",
        "normalized",
        "recorded",
        "duplicates",
        "receipt_ids",
    }
    for name, annotation in scalars.items():
        assert "str" not in str(annotation), f"{name} can hold arbitrary text"
        assert "bytes" not in str(annotation), name
        assert "dict" not in str(annotation), name


# ── The endpoint key must never appear in logs or diagnostics ───────────────


def render_everything(db: Session, keys: tuple[str, ...], registry: Any) -> str:
    """Everything an operator or an error reporter could ever SEE.

    Not a promise in a docstring: the outcomes, the refusal messages, the full
    tracebacks WITH frame locals — which is what an error reporter uploads — the
    carrier's rendering, and every persisted audit row.
    """
    rendered: list[str] = []

    def drive(call: Any) -> None:
        try:
            result = call()
        except BaseException as exc:
            rendered.append(
                "".join(
                    traceback.TracebackException(
                        type(exc), exc, exc.__traceback__, capture_locals=True
                    ).format()
                )
            )
        else:
            rendered.append(repr(result))
            rendered.append(str(result))

    for key in keys:
        endpoint = EndpointAddress(key)
        drive(
            lambda e=endpoint: receive(
                Uow(db),
                endpoint=e,
                request=delivery_request(),
                registry=registry,
                resolve_secrets=resolver(),
            )
        )
        drive(
            lambda e=endpoint: answer_challenge(
                Uow(db),
                endpoint=e,
                request=IngressRequest(params={"challenge": "abc"}),
                registry=registry,
                resolve_secrets=resolver(),
            )
        )
        drive(lambda e=endpoint: prepare_ingress(db, endpoint=e, registry=registry))
        drive(lambda e=endpoint: repr(e))

    db.flush()
    for row in audit_rows(db):
        rendered.append(
            f"{row.action} {row.entity_type} {row.entity_id} {row.details!r}"
        )
    return "\n".join(rendered)


def test_the_endpoint_key_appears_in_no_log_line_outcome_or_diagnostic(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """GREPPED, not promised.

    The key is a BEARER credential: whoever holds it can drive the connector's
    `verify`. So every rendered surface is produced for real and searched — a
    live endpoint, a revoked one (404) and a never-minted one (404), across both
    façades, with tracebacks rendered the way an error reporter renders them.

    The one place the key legitimately exists is the `EndpointAddress` the
    caller constructed. That type has no `repr`, which is what keeps it out of
    the frame locals `capture_locals=True` prints — and `prepare_ingress`'s
    parameter is pinned by every traceback that leaves it.
    """
    registry = registry_for(IngressFake(events=()))
    _, binding, live = build(db, registry)
    rotated = rotate_ingress_endpoint(db, binding, registry=registry)
    revoke_ingress_endpoint(db, binding)
    never_minted = "0" * 48
    assert live is not None
    # Committed BEFORE the rendering, because the first refused request unwinds
    # the deployment's unit of work — which would otherwise take the audit rows
    # this test then searches with it, and the search would pass by finding
    # nothing.
    db.commit()

    with caplog.at_level(logging.DEBUG):
        rendered = render_everything(db, (live, rotated, never_minted), registry)
    everything = rendered + "\n" + caplog.text

    for key in (live, rotated):
        assert key not in everything, "the endpoint key reached a rendered surface"
        assert key[:24] not in everything, "half the key is still most of the key"
    assert caplog.text == "", "this module logged; it installs no logger for a reason"

    # ── Sensitivity (ADR-0018) ──────────────────────────────────────────────
    # A harness that rendered nothing, or a `render_everything` whose `drive`
    # swallowed every result, would satisfy every assertion above while proving
    # nothing at all. So: the rendering must be substantial, must contain the
    # tracebacks and outcomes it claims to, and the SEARCH ITSELF must be shown
    # to find a key when one really is present.
    assert len(rendered) > 2000, "the rendering is too small to have covered the paths"
    assert "IngressOutcome" in rendered
    assert "Traceback" in rendered
    assert "EndpointUnknown" in rendered
    assert str(binding.id) in rendered, "the rendering reached the audit rows"
    assert live in f"a leaked line holding {live} verbatim"


def test_the_address_wrapper_is_what_keeps_the_key_out_of_frame_locals() -> None:
    """The mechanism behind the grep above, asserted directly.

    `traceback.TracebackException(capture_locals=True)` — what an error reporter
    uses — renders every frame local with `repr()`. A bare `str` parameter would
    put a bearer credential into every such report; a `repr`-less wrapper cannot.
    """
    key = "a" * 48
    endpoint = EndpointAddress(key)
    assert key not in repr(endpoint)
    assert key not in str(endpoint)
    assert endpoint.key == key, "this is a rendering rule, not a removal"

    # And it is the WRAPPER doing the work, not the value being unusual.
    assert key in repr(key)
