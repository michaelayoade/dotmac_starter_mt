"""PARITY: installation lifecycle, manifest adoption, delivery, installation API.

This file is the local evidence for the four suites `EXTRACTION.toml` marks as
porting. Each test names its source suite, so `ported_to` in the dossier points
at something real rather than at a promise.

  dotmac_sub:tests/test_integration_installations.py      -> Lifecycle
  dotmac_sub:tests/test_integration_manifest_adoption.py  -> Adoption
  dotmac_sub:tests/test_integration_delivery.py           -> Delivery
  dotmac_sub:tests/test_integration_installation_api.py   -> ApiServiceBehaviour

The installation-API suite is ported as SERVICE behaviour only: the HTTP surface
belongs to the `dotmac_integrator` assembly, which owns its own transport tests.
What ports is what the routes would call.
"""

from __future__ import annotations

import pytest
from dotmac_integration import (
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
    DeliveryAttempt,
    ExecutionPolicy,
    LifecycleError,
    LostClaim,
    Outcome,
    OutcomeStatus,
    SecretValueError,
    add_binding,
    adopt_manifest,
    create_draft,
    disable,
    enable,
    enqueue_delivery,
    invoke,
    prepare,
    preview_adoption,
    put_config_revision,
    quarantine,
    retire,
    set_binding_enabled,
    settle,
)
from dotmac_integration.conformance import (
    FAKE_CAPABILITY,
    fake_manifest,
    fake_plugin,
    fake_registry,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

OTHER_CAPABILITY = "ticket.observation.v1"


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in (
        ConnectorInstallation,
        ConnectorConfigRevision,
        CapabilityBinding,
        DeliveryAttempt,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def registry():
    return fake_registry()


# ── Lifecycle (test_integration_installations.py) ───────────────────────────


def test_a_draft_pins_the_connector_installed_right_now(db: Session, registry) -> None:
    """Capturing the digest at draft time is what makes a later upgrade a
    visible ADOPTION decision rather than a silent change underneath."""
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    assert installation.state == "draft"
    assert installation.manifest_digest == registry.get("conformance_fake").digest
    assert installation.spi_range == str(registry.get("conformance_fake").spi_range)


def test_an_identical_configuration_does_not_mint_a_second_revision(
    db: Session, registry
) -> None:
    """Otherwise every reconcile inflates the history until "when did this last
    change?" stops being answerable."""
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    first, new_first = put_config_revision(db, installation, config={"a": 1})
    second, new_second = put_config_revision(db, installation, config={"a": 1})

    assert new_first and not new_second
    assert first.id == second.id
    assert first.revision == 1


def test_a_changed_configuration_mints_the_next_revision(db: Session, registry) -> None:
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    put_config_revision(db, installation, config={"a": 1})
    second, is_new = put_config_revision(db, installation, config={"a": 2})

    assert is_new
    assert second.revision == 2
    assert installation.current_config_revision_id == second.id


def test_a_literal_secret_never_reaches_a_revision(db: Session, registry) -> None:
    """Refused before the write, because a revision is immutable and ends up in
    every backup."""
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    with pytest.raises(SecretValueError):
        put_config_revision(db, installation, config={"auth": {"api_key": "sk_live_x"}})


def test_a_binding_starts_disabled(db: Session, registry) -> None:
    """Binding states intent; enabling is a live decision. Collapsing them would
    enable a capability the moment someone wrote it down."""
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    binding = add_binding(
        db, installation, registry=registry, capability_id=FAKE_CAPABILITY
    )
    assert binding.state == "disabled"


def test_binding_an_undeclared_capability_is_refused(db: Session, registry) -> None:
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    with pytest.raises(Exception, match="does not declare capability"):
        add_binding(db, installation, registry=registry, capability_id=OTHER_CAPABILITY)


def test_enabling_requires_a_configuration_revision(db: Session, registry) -> None:
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    with pytest.raises(LifecycleError, match="no configuration revision"):
        enable(db, installation, registry=registry)


def test_enablement_is_gated_on_a_live_connection_check(db: Session) -> None:
    """Static validation cannot tell a wrong credential from a right one.

    Without this the failure surfaces on the first real event, after the
    operator was told the integration was live.
    """
    unhealthy = fake_registry(plugins=[fake_plugin(healthy=False)])
    installation = create_draft(
        db, registry=unhealthy, connector_key="conformance_fake", name="primary"
    )
    put_config_revision(db, installation, config={"a": 1})

    with pytest.raises(LifecycleError, match="connection validation failed"):
        enable(db, installation, registry=unhealthy)
    assert installation.state == "validating"
    assert "unreachable" in installation.state_reason


def test_a_healthy_connection_enables(db: Session, registry) -> None:
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    put_config_revision(db, installation, config={"a": 1})
    enable(db, installation, registry=registry)

    assert installation.state == "enabled"
    assert installation.enabled_at is not None
    assert installation.state_reason is None


def test_disable_and_quarantine_are_distinct_states(db: Session, registry) -> None:
    """A disabled installation is waiting for a person; a quarantined one is
    waiting for an explanation. They need different responses."""
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    disable(db, installation, reason="operator paused")
    assert installation.state == "disabled"

    quarantine(db, installation, reason="repeated auth failures")
    assert installation.state == "quarantined"


def test_retiring_keeps_the_configuration_history(db: Session, registry) -> None:
    """Retiring an integration must not destroy the evidence of what it did."""
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    revision, _ = put_config_revision(db, installation, config={"a": 1})
    retire(db, installation, reason="replaced")

    assert installation.state == "retired"
    assert db.get(ConnectorConfigRevision, revision.id) is not None


def test_a_retired_installation_takes_no_more_configuration(
    db: Session, registry
) -> None:
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    retire(db, installation, reason="done")
    with pytest.raises(LifecycleError, match="retired"):
        put_config_revision(db, installation, config={"a": 1})


# ── Manifest adoption (test_integration_manifest_adoption.py) ───────────────


def _upgraded(capabilities=(FAKE_CAPABILITY,)):
    """The same connector key at a newer version, with the old manifest kept
    inside it as the adoption window."""
    return fake_plugin(
        manifest_=fake_manifest(version="2.0.0", capabilities=capabilities),
        historical=(fake_manifest(version="1.0.0"),),
    )


def test_preview_changes_nothing(db: Session, registry) -> None:
    """An operator needs to see the consequence BEFORE the decision."""
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    before = installation.manifest_digest
    upgraded = fake_registry(plugins=[_upgraded()])

    preview = preview_adoption(db, installation, registry=upgraded)

    assert preview.adoption_required
    assert installation.manifest_digest == before


def test_the_adoption_window_honours_a_previously_pinned_digest(
    db: Session, registry
) -> None:
    """The historical manifest lives INSIDE the superseding distribution, so the
    pin an installed revision was adopted against travels with it."""
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    upgraded = fake_registry(plugins=[_upgraded()])
    assert preview_adoption(db, installation, registry=upgraded).honours_current_pin


def test_adoption_is_refused_when_it_would_strand_a_bound_capability(
    db: Session, registry
) -> None:
    """Applying it anyway leaves a binding pointing at a contract the connector
    no longer implements — an integration that reports healthy and cannot run."""
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    add_binding(db, installation, registry=registry, capability_id=FAKE_CAPABILITY)

    narrowed = fake_registry(
        plugins=[
            fake_plugin(
                manifest_=fake_manifest(
                    version="2.0.0", capabilities=("conformance.other.v1",)
                )
            )
        ]
    )
    preview = preview_adoption(db, installation, registry=narrowed)
    assert preview.blocked
    with pytest.raises(LifecycleError, match="would strand"):
        adopt_manifest(db, installation, registry=narrowed)


def test_adoption_is_idempotent(db: Session, registry) -> None:
    """Adoption is exactly the operation someone retries after a timeout, so a
    second attempt must succeed rather than error."""
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    upgraded = fake_registry(plugins=[_upgraded()])

    first = adopt_manifest(db, installation, registry=upgraded)
    second = adopt_manifest(db, installation, registry=upgraded)

    assert first.adoption_required
    assert not second.adoption_required
    assert installation.connector_version == "2.0.0"


# ── Delivery through the fake plugin (test_integration_delivery.py) ─────────


def _enabled(db: Session, registry) -> tuple:
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    put_config_revision(
        db, installation, config={"a": 1}, secret_refs={"token": "bao://kv/x#t"}
    )
    enable(db, installation, registry=registry)
    binding = add_binding(
        db, installation, registry=registry, capability_id=FAKE_CAPABILITY
    )
    set_binding_enabled(db, installation, binding, registry=registry, enabled=True)
    return installation, binding


def test_a_dispatch_reaches_the_plugin_with_materialized_secrets(
    db: Session, registry
) -> None:
    """The invoke phase resolves references to values for ONE call. Nothing
    persists them."""
    installation, binding = _enabled(db, registry)
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        event_type="message.send",
        idempotency_key="k1",
        payload={"text": "hi"},
    )
    plugin = registry.plugin("conformance_fake")

    prepared = prepare(db, delivery, registry=registry)
    assert prepared is not None
    outcome = invoke(
        prepared,
        registry=registry,
        resolve_secrets=lambda refs: {k: "resolved" for k in refs},
    )
    settle(db, delivery, outcome, prepared=prepared)

    assert delivery.state == "delivered"
    assert plugin.seen[0].secrets == {"token": "resolved"}
    assert plugin.seen[0].payload == {"text": "hi"}


def test_invoke_cannot_be_given_a_database_session() -> None:
    """The boundary is enforced by what a caller CANNOT pass.

    A transaction held across provider I/O holds row locks, blocks the
    dispatcher and eventually exhausts the pool — integrations die of this far
    more often than of bad payloads.
    """
    import inspect

    parameters = set(inspect.signature(invoke).parameters)
    assert "db" not in parameters and "session" not in parameters


def test_a_raising_plugin_becomes_retryable_not_an_exception(db: Session) -> None:
    """A connector that throws has told us nothing about whether the effect
    landed; treating that as terminal would discard work that merely timed out.
    """
    broken = fake_registry(
        plugins=[fake_plugin(raises=RuntimeError("provider exploded"))]
    )
    installation, binding = _enabled(db, broken)
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )

    prepared = prepare(db, delivery, registry=broken)
    outcome = invoke(prepared, registry=broken, resolve_secrets=lambda r: {})

    assert outcome.status is OutcomeStatus.RETRYABLE
    assert outcome.error_code == "connector_raised"

    settle(db, delivery, outcome, prepared=prepared)
    assert delivery.state == "retryable"


def test_settle_refuses_to_overwrite_another_workers_outcome(
    db: Session, registry
) -> None:
    """A worker whose lease expired mid-call must not clobber the result of the
    worker that took over."""
    installation, binding = _enabled(db, registry)
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    prepared = prepare(db, delivery, registry=registry)

    # Another worker takes over: the attempt counter moves on.
    delivery.attempt_count += 1
    db.flush()

    with pytest.raises(LostClaim, match="another took over"):
        settle(db, delivery, Outcome(status=OutcomeStatus.SUCCEEDED), prepared=prepared)


def test_a_contended_delivery_prepares_as_none(db: Session, registry) -> None:
    """A lost claim is not an error — the caller moves on."""
    installation, binding = _enabled(db, registry)
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    assert prepare(db, delivery, registry=registry) is not None
    assert prepare(db, delivery, registry=registry) is None


# ── Installation API, SERVICE behaviour only (test_integration_installation_api.py)


def test_the_service_never_commits_or_opens_a_session() -> None:
    """Transaction authority stays with the composing assembly.

    A service that committed would make it impossible to put several of these in
    one unit of work — the reason the host owns the transaction.
    """
    import inspect

    from dotmac_integration import lifecycle

    source = inspect.getsource(lifecycle)
    for forbidden in (
        "db.commit()",
        "db.rollback()",
        "sessionmaker(",
        "create_engine(",
    ):
        assert forbidden not in source, forbidden
    assert "db.flush()" in source


def test_the_service_surface_the_routes_would_call_is_complete(
    db: Session, registry
) -> None:
    """The HTTP layer belongs to the assembly; what ports is what it calls.

    Drives the whole operator journey end to end, which is what the API suite
    covered in the source.
    """
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    put_config_revision(db, installation, config={"a": 1})
    binding = add_binding(
        db, installation, registry=registry, capability_id=FAKE_CAPABILITY
    )
    enable(db, installation, registry=registry)
    set_binding_enabled(db, installation, binding, registry=registry, enabled=True)

    assert installation.state == "enabled"
    assert binding.state == "enabled"

    set_binding_enabled(db, installation, binding, registry=registry, enabled=False)
    disable(db, installation, reason="paused")
    assert binding.state == "disabled"
    assert installation.state == "disabled"


def test_an_installation_name_is_unique_per_connector(db: Session, registry) -> None:
    """Two installations of one connector must be distinguishable by name, or an
    operator cannot say which one they are configuring."""
    from sqlalchemy.exc import IntegrityError

    create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    with pytest.raises(IntegrityError):
        create_draft(
            db, registry=registry, connector_key="conformance_fake", name="primary"
        )


def test_the_policy_is_injected_not_hardcoded(db: Session, registry) -> None:
    """Operational numbers are a deployment decision."""
    installation, binding = _enabled(db, registry)
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    prepared = prepare(
        db, delivery, registry=registry, policy=ExecutionPolicy(lease_seconds=1)
    )
    assert prepared is not None
    assert delivery.leased_until is not None
