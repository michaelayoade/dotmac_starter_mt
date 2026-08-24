"""Declaring a binding is not configuring one — and re-declaring erases nothing.

`lifecycle.add_binding` is idempotent BY CONTRACT ("rebinding the same
installation/capability updates the one existing binding", 0.1.0a6), which is
precisely why it is the function every activation and reconcile sequence calls
again on a binding that already exists. While it wrote `scope_json` and
`policy_json` unconditionally from parameters that DEFAULTED TO `None`, that
second call reset two columns the caller never mentioned.

`policy_json` is not decoration. `dotmac_integration.selection` reads
`policy_json["default"]` to pick between several bindings enabled for one
capability, so losing it turns a working outbound configuration into a
fail-closed `AmbiguousBindingError` at the next dispatch — with every state
column in the control plane still reading `enabled`. That is the shape of the
report: activation appears to succeed and outbound traffic stops.

The fix separates the decisions. `add_binding` owns EXISTENCE;
`set_binding_selection_policy` and `set_binding_scope` own the two columns an
operator sets; `set_binding_enabled` owns enablement; and
`destination_binding.establish_destination` owns where traffic lands. An
omitted argument now PRESERVES, while an explicit one — `None` included —
still writes, because "no selection policy" has to stay expressible.

SQLite in-memory: this is service logic, not tenancy. The module owns no
tenant-plane table at all (ADR-0023; `manifest.tables` is empty).
"""

from __future__ import annotations

import pytest
from dotmac_integration import (
    AmbiguousBindingError,
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
    add_binding,
    create_draft,
    enable,
    put_config_revision,
    resolve_binding,
    set_binding_enabled,
    set_binding_scope,
    set_binding_selection_policy,
)
from dotmac_integration.conformance import FAKE_CAPABILITY, fake_registry
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


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
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def registry():
    return fake_registry()


def _configured(db: Session, registry, *, name: str) -> ConnectorInstallation:
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name=name
    )
    put_config_revision(db, installation, registry=registry, config={"a": 1})
    return installation


# ── 1. Re-declaration preserves what it did not mention ─────────────────────


def test_redeclaring_a_binding_preserves_its_policy_and_scope(
    db: Session, registry
) -> None:
    """THE regression. An idempotent re-declaration is not an edit."""
    installation = _configured(db, registry, name="primary")
    binding = add_binding(
        db,
        installation,
        registry=registry,
        capability_id=FAKE_CAPABILITY,
        scope={"account": "one"},
        policy={"default": True},
    )

    add_binding(db, installation, registry=registry, capability_id=FAKE_CAPABILITY)

    assert binding.policy_json == {"default": True}
    assert binding.scope_json == {"account": "one"}


def test_an_explicitly_named_value_still_overwrites(db: Session, registry) -> None:
    """Preservation is about OMISSION. A named value is still a write."""
    installation = _configured(db, registry, name="primary")
    binding = add_binding(
        db,
        installation,
        registry=registry,
        capability_id=FAKE_CAPABILITY,
        scope={"account": "one"},
        policy={"default": True},
    )

    add_binding(
        db,
        installation,
        registry=registry,
        capability_id=FAKE_CAPABILITY,
        scope={"account": "two"},
        policy={"default": False},
    )

    assert binding.scope_json == {"account": "two"}
    assert binding.policy_json == {"default": False}


def test_an_explicit_none_still_clears(db: Session, registry) -> None:
    """`None` is a real value — "not the default" — and must stay writable.

    This is why omission needed its own marker rather than reusing `None`: with
    `None` as the default there is no way to say "clear it" that is
    distinguishable from saying nothing at all.
    """
    installation = _configured(db, registry, name="primary")
    binding = add_binding(
        db,
        installation,
        registry=registry,
        capability_id=FAKE_CAPABILITY,
        scope={"account": "one"},
        policy={"default": True},
    )

    add_binding(
        db,
        installation,
        registry=registry,
        capability_id=FAKE_CAPABILITY,
        scope=None,
        policy=None,
    )

    assert binding.scope_json is None
    assert binding.policy_json is None


def test_a_new_binding_starts_with_neither_column_set(db: Session, registry) -> None:
    """The omission marker is never a stored value.

    A sentinel that reached the column would be worse than the defect it
    replaces: `policy_json` is JSON, so an unserializable marker would fail at
    flush time, and a serializable one would be read back by `selection` as a
    policy nobody wrote.
    """
    installation = _configured(db, registry, name="primary")
    binding = add_binding(
        db, installation, registry=registry, capability_id=FAKE_CAPABILITY
    )
    db.flush()

    assert binding.scope_json is None
    assert binding.policy_json is None


# ── 2. The outbound consequence, end to end ─────────────────────────────────


def test_reactivating_after_a_redeclaration_keeps_outbound_dispatch_resolvable(
    db: Session, registry
) -> None:
    """The reported symptom, driven through the real service functions.

    Two installations of one connector both serve the capability — the topology
    ADR-0024 § 7 permits and `selection` exists for. One is marked the default.
    An operator re-declares that binding and brings the installation back up;
    outbound dispatch must still resolve to exactly one binding.

    On the unfixed module the re-declaration nulls `policy_json`, so the final
    `resolve_binding` sees two enabled bindings and zero declared defaults and
    refuses with `AmbiguousBindingError` — outbound traffic stops while every
    state column reads `enabled`.
    """
    primary = _configured(db, registry, name="primary")
    secondary = _configured(db, registry, name="secondary")
    primary_binding = add_binding(
        db,
        primary,
        registry=registry,
        capability_id=FAKE_CAPABILITY,
        policy={"default": True},
    )
    secondary_binding = add_binding(
        db, secondary, registry=registry, capability_id=FAKE_CAPABILITY
    )
    for installation, binding in (
        (primary, primary_binding),
        (secondary, secondary_binding),
    ):
        enable(db, installation, registry=registry)
        set_binding_enabled(db, installation, binding, registry=registry, enabled=True)
    assert resolve_binding(db, capability_id=FAKE_CAPABILITY) is primary_binding

    # The reconcile: re-assert the desired binding set, naming only the
    # capability. This invalidates the installation's activation by design.
    add_binding(db, primary, registry=registry, capability_id=FAKE_CAPABILITY)
    assert primary.state == "draft"
    assert primary_binding.state == "disabled"

    # ... and bring it back up, which is where the operator is told it worked.
    enable(db, primary, registry=registry)
    set_binding_enabled(db, primary, primary_binding, registry=registry, enabled=True)

    assert resolve_binding(db, capability_id=FAKE_CAPABILITY) is primary_binding


def test_the_ambiguity_this_guards_is_real(db: Session, registry) -> None:
    """Sensitivity proof for the test above.

    Without it, the end-to-end test would pass for the wrong reason if
    `resolve_binding` ever stopped refusing an ambiguous set — a green test over
    a check that no longer bites. Clearing the default explicitly, through its
    named owner, must still produce the refusal.
    """
    primary = _configured(db, registry, name="primary")
    secondary = _configured(db, registry, name="secondary")
    primary_binding = add_binding(
        db,
        primary,
        registry=registry,
        capability_id=FAKE_CAPABILITY,
        policy={"default": True},
    )
    secondary_binding = add_binding(
        db, secondary, registry=registry, capability_id=FAKE_CAPABILITY
    )
    for installation, binding in (
        (primary, primary_binding),
        (secondary, secondary_binding),
    ):
        enable(db, installation, registry=registry)
        set_binding_enabled(db, installation, binding, registry=registry, enabled=True)

    set_binding_selection_policy(db, primary_binding, policy=None)

    with pytest.raises(AmbiguousBindingError):
        resolve_binding(db, capability_id=FAKE_CAPABILITY)


# ── 3. The named owners ─────────────────────────────────────────────────────


def test_the_policy_owner_writes_only_its_own_column(db: Session, registry) -> None:
    """Selection is read live, per dispatch. Nothing validated against it.

    So changing it must not return the installation to `draft` — an operator
    switching which connector is the default would otherwise take the
    integration down and have to re-run a connection check to get it back.
    """
    installation = _configured(db, registry, name="primary")
    binding = add_binding(
        db,
        installation,
        registry=registry,
        capability_id=FAKE_CAPABILITY,
        scope={"account": "one"},
    )
    enable(db, installation, registry=registry)
    set_binding_enabled(db, installation, binding, registry=registry, enabled=True)

    set_binding_selection_policy(
        db, binding, policy={"default": True}, actor="platform_admin:test"
    )

    assert binding.policy_json == {"default": True}
    assert binding.scope_json == {"account": "one"}
    assert binding.state == "enabled"
    assert installation.state == "enabled"
    assert binding.updated_by == "platform_admin:test"


def test_the_scope_owner_writes_only_its_own_column(db: Session, registry) -> None:
    installation = _configured(db, registry, name="primary")
    binding = add_binding(
        db,
        installation,
        registry=registry,
        capability_id=FAKE_CAPABILITY,
        policy={"default": True},
    )
    enable(db, installation, registry=registry)
    set_binding_enabled(db, installation, binding, registry=registry, enabled=True)

    set_binding_scope(db, binding, scope={"account": "two"})

    assert binding.scope_json == {"account": "two"}
    assert binding.policy_json == {"default": True}
    assert binding.state == "enabled"
    assert installation.state == "enabled"
