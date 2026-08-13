"""Binding multiplicity and the dispatch seam — enabled is not selected.

This is the file that encodes the 2026-08-13 ruling, and it exists because an
earlier reading of ADR-0024 § 7 got it wrong. § 7 says "each `(installation,
capability)` has exactly one active connector binding" — the TUPLE. It was read
as a global per-capability constraint, which would have blocked a topology the
source product supports and the ADR permits.

    enabled    this installation is capable and permitted. MANY installations
               may be enabled for one capability.
    selected   the binding chosen for ONE dispatch. Zero or several fail closed.

So the schema constrains the tuple and nothing else, and every ambiguity is
resolved per dispatch where the caller's intent exists.

SQLite in-memory: this is logic and constraint structure, not tenancy. The
platform-plane grants are proved against real Postgres by the composed
live-catalog gate.
"""

from __future__ import annotations

import uuid

import pytest
from dotmac_integration import (
    ActivationRefused,
    AmbiguousBindingError,
    CapabilityBinding,
    ConnectorInstallation,
    NoEnabledBindingError,
    require_activatable,
    resolve_binding,
)
from dotmac_integration.conformance import FAKE_CAPABILITY, fake_registry
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

OTHER_CAPABILITY = "ticket.observation.v1"


@pytest.fixture()
def db() -> Session:
    # `schema_translate_map` is the repo's established way to run a
    # module-schema model on SQLite (see test_files.py): the models stay bound
    # to `mod_intg`, and only the engine rewrites it away.
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    ConnectorInstallation.__table__.create(engine)
    CapabilityBinding.__table__.create(engine)
    with Session(engine) as session:
        yield session


def _installation(
    db: Session,
    *,
    key: str = "conformance_fake",
    name: str = "primary",
    state: str = "enabled",
) -> ConnectorInstallation:
    installation = ConnectorInstallation(
        id=uuid.uuid4(),
        connector_key=key,
        connector_version="1.0.0",
        spi_range=">=1.0,<2.0",
        manifest_digest="d" * 64,
        name=name,
        environment="production",
        state=state,
    )
    db.add(installation)
    db.flush()
    return installation


def _binding(
    db: Session,
    installation: ConnectorInstallation,
    *,
    capability: str = FAKE_CAPABILITY,
    state: str = "enabled",
    default: bool | None = None,
) -> CapabilityBinding:
    binding = CapabilityBinding(
        id=uuid.uuid4(),
        installation_id=installation.id,
        capability_id=capability,
        state=state,
        policy_json={"default": default} if default is not None else None,
    )
    db.add(binding)
    db.flush()
    return binding


# ── 1. Duplicate binding within ONE installation fails ──────────────────────


def test_one_installation_may_bind_a_capability_only_once(db: Session) -> None:
    installation = _installation(db)
    _binding(db, installation)
    with pytest.raises(IntegrityError):
        _binding(db, installation)


# ── 2. Two installations MAY implement the same capability ──────────────────


def test_two_installations_may_implement_one_capability(db: Session) -> None:
    """The topology the earlier misreading would have forbidden.

    ADR-0024 § 7 scopes uniqueness to (installation, capability); `capability_id`
    alone is deliberately unconstrained.
    """
    first = _installation(db, name="primary")
    second = _installation(db, name="secondary")
    _binding(db, first)
    _binding(db, second)

    stored = db.query(CapabilityBinding).filter_by(capability_id=FAKE_CAPABILITY).all()
    assert len(stored) == 2


# ── 3. Explicit binding selection succeeds ──────────────────────────────────


def test_an_explicitly_named_binding_is_the_answer(db: Session) -> None:
    """The generic dispatch seam: unambiguous by construction — no policy, no
    defaulting, no ordering."""
    first = _installation(db, name="primary")
    second = _installation(db, name="secondary")
    _binding(db, first)
    wanted = _binding(db, second)

    chosen = resolve_binding(
        db, capability_id=FAKE_CAPABILITY, capability_binding_id=wanted.id
    )
    assert chosen.id == wanted.id


def test_a_named_but_disabled_binding_is_refused_not_rerouted(db: Session) -> None:
    """A caller naming a stale binding has a bug. Silently serving a DIFFERENT
    connector would hide it and send data somewhere unintended."""
    first = _installation(db, name="primary")
    second = _installation(db, name="secondary")
    _binding(db, first)  # a perfectly good alternative
    stale = _binding(db, second, state="disabled")

    with pytest.raises(NoEnabledBindingError, match="never silently rerouted"):
        resolve_binding(
            db, capability_id=FAKE_CAPABILITY, capability_binding_id=stale.id
        )


def test_a_binding_on_a_disabled_installation_is_unusable(db: Session) -> None:
    """A binding is only usable if the whole chain is."""
    installation = _installation(db, state="disabled")
    _binding(db, installation)
    with pytest.raises(NoEnabledBindingError):
        resolve_binding(db, capability_id=FAKE_CAPABILITY)


# ── 4. Under-specified multi-binding dispatch fails as ambiguous ────────────


def test_several_enabled_bindings_without_a_default_fail_closed(db: Session) -> None:
    """Never 'pick the first'. Ordering would make the answer depend on insert
    time, and the caller would never learn there was a choice."""
    first = _installation(db, name="primary")
    second = _installation(db, name="secondary")
    _binding(db, first)
    _binding(db, second)

    with pytest.raises(AmbiguousBindingError) as excinfo:
        resolve_binding(db, capability_id=FAKE_CAPABILITY)
    # The message must name the collision, or the operator cannot fix it in one
    # step.
    assert "primary" in str(excinfo.value) and "secondary" in str(excinfo.value)


def test_exactly_one_declared_default_resolves(db: Session) -> None:
    first = _installation(db, name="primary")
    second = _installation(db, name="secondary")
    _binding(db, first, default=True)
    _binding(db, second)

    chosen = resolve_binding(db, capability_id=FAKE_CAPABILITY)
    assert chosen.installation_id == first.id


def test_two_declared_defaults_are_still_ambiguous(db: Session) -> None:
    first = _installation(db, name="primary")
    second = _installation(db, name="secondary")
    _binding(db, first, default=True)
    _binding(db, second, default=True)

    with pytest.raises(AmbiguousBindingError, match="exactly one must"):
        resolve_binding(db, capability_id=FAKE_CAPABILITY)


def test_connector_key_narrows_without_defaulting(db: Session) -> None:
    first = _installation(db, key="alpha", name="a")
    _installation(db, key="beta", name="b")
    _binding(db, first)
    _binding(db, db.query(ConnectorInstallation).filter_by(connector_key="beta").one())

    chosen = resolve_binding(db, capability_id=FAKE_CAPABILITY, connector_key="alpha")
    assert chosen.installation_id == first.id


def test_no_enabled_binding_fails_closed(db: Session) -> None:
    """Absent routing must refuse, never 'do nothing quietly' — which reads as
    success to a caller that just handed over a payload."""
    with pytest.raises(NoEnabledBindingError, match="no enabled binding"):
        resolve_binding(db, capability_id=OTHER_CAPABILITY)


# ── 5. Undeclared capability and incompatible SPI refuse activation ─────────


def test_activation_refuses_an_undeclared_capability(db: Session) -> None:
    installation = _installation(db)
    binding = _binding(db, installation, capability=OTHER_CAPABILITY, state="disabled")
    registry = fake_registry()  # declares FAKE_CAPABILITY only

    with pytest.raises(ActivationRefused, match="does not declare capability"):
        require_activatable(installation, binding, registry)


def test_activation_refuses_an_incompatible_stored_spi_range(db: Session) -> None:
    """The case that actually bites: the plugin did not change, the HOST did.

    Storing the range on the installation is what makes a later module upgrade
    able to refuse a previously activated binding.
    """
    installation = _installation(db)
    installation.spi_range = ">=9.0,<10.0"
    binding = _binding(db, installation, state="disabled")

    with pytest.raises(ActivationRefused, match="stored SPI range refuses"):
        require_activatable(installation, binding, fake_registry())


def test_activation_refuses_a_connector_that_is_not_installed(db: Session) -> None:
    installation = _installation(db, key="never_installed")
    binding = _binding(db, installation, state="disabled")
    with pytest.raises(ActivationRefused, match="not installed in this runtime"):
        require_activatable(installation, binding, fake_registry())


def test_activation_refuses_a_quarantined_installation(db: Session) -> None:
    installation = _installation(db, state="quarantined")
    binding = _binding(db, installation, state="disabled")
    with pytest.raises(ActivationRefused, match="quarantined"):
        require_activatable(installation, binding, fake_registry())


def test_activation_accepts_a_declared_capability_on_a_compatible_connector(
    db: Session,
) -> None:
    """Specificity for the four refusals above."""
    installation = _installation(db)
    binding = _binding(db, installation, state="disabled")
    require_activatable(installation, binding, fake_registry())


def test_activation_reports_every_reason_not_only_the_first(db: Session) -> None:
    """An operator fixing an activation wants the whole list; one problem at a
    time turns a single edit into three round trips."""
    from dotmac_integration import check_activation

    installation = _installation(db, state="retired")
    installation.spi_range = ">=9.0,<10.0"
    binding = _binding(db, installation, capability=OTHER_CAPABILITY, state="disabled")

    verdict = check_activation(installation, binding, fake_registry())
    assert not verdict.ok
    assert len(verdict.reasons) >= 3


def test_a_capability_alone_is_not_a_uniqueness_constraint() -> None:
    """SENSITIVITY PROOF for the ruling, read off the schema itself.

    If someone later "fixes" the perceived duplicate-ownership gap by adding a
    unique constraint on capability_id, this fails and points at the ADR.
    """
    uniques = [
        tuple(sorted(column.name for column in constraint.columns))
        for constraint in CapabilityBinding.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    ]
    assert ("capability_id", "installation_id") in uniques
    assert ("capability_id",) not in uniques, (
        "capability_id must NOT be globally unique: ADR-0024 §7 scopes "
        "uniqueness to (installation, capability), and many installations may "
        "implement one capability. Choosing between them is a DISPATCH "
        "decision — see dotmac_integration.selection."
    )


def test_scope_json_is_in_no_uniqueness_constraint() -> None:
    """JSON equality cannot detect overlapping scopes, so a constraint over it
    would claim a guarantee it cannot make."""
    for constraint in CapabilityBinding.__table__.constraints:
        names = {column.name for column in constraint.columns}
        assert "scope_json" not in names, constraint.name
