"""Provider metadata is corroboration only — proven, not asserted.

`provider-capability-sources.md` § 2.2 says the destination-scope invariant
currently holds *by absence*: nothing routes on provider metadata because
nothing routes anywhere at all. This file is what replaces absence with a
property, and it does it three ways, because each catches a different mistake:

1. **Behaviour.** Plant hostile provider metadata and a hostile `scope_json`,
   and prove the resolved destination is unchanged.
2. **Signature.** Prove `resolve_destination` has no parameter through which a
   provider-influenced value could arrive. A future edit that adds one fails
   here, before any behavioural test has a chance to be written for it.
3. **Types.** Prove no function taking provider input returns a
   `DestinationBinding` or a `DestinationProfile`. That is the shape an
   invariant-inverting refactor would take, and it would leave every
   behavioural test above still passing.

Each guard carries a sensitivity proof — it is shown to FAIL on what it forbids.
A structural check that has nothing to find passes for the wrong reason.

SQLite in-memory: this is routing logic and refusal structure, not tenancy.
`mod_intg` is platform-plane and its grants are proved against real Postgres by
the composed live-catalog gate.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from dotmac_integration import (
    CapabilityBinding,
    CapabilityContract,
    CapabilityOwner,
    CapabilityRegistry,
    ConnectorConfigRevision,
    ConnectorInstallation,
    Corroboration,
    DestinationBinding,
    DestinationBindingError,
    DestinationDisagreement,
    DestinationNotBound,
    DestinationProfile,
    DestinationProfileMissing,
    LocalScope,
    UntrustedDestination,
    corroborate,
    destination_client,
    install_destination_profiles,
    require_corroborated,
    require_profile,
    resolve_destination,
)
from dotmac_integration import destination_binding as module_under_test
from dotmac_integration.destination_binding import _reset_destination_profiles
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

#: Synthetic throughout. This module must be provable without naming a real
#: fleet capability — the host of a vocabulary never enumerates its members.
CAPABILITY = "alpha_domain.receive.v1"
OWNER_APP = "alpha"
HOSTILE_APP = "beta"

CONTRACT = CapabilityContract(
    capability_id=CAPABILITY,
    owner=CapabilityOwner(application=OWNER_APP, module="messages"),
    summary="a synthetic inbound contract",
)
REGISTRY = CapabilityRegistry.from_declarations([CONTRACT])


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    ConnectorInstallation.__table__.create(engine)
    ConnectorConfigRevision.__table__.create(engine)
    CapabilityBinding.__table__.create(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def _clean_profiles() -> Iterator[None]:
    _reset_destination_profiles()
    yield
    _reset_destination_profiles()


class _RecordingClient:
    """A destination client that records rather than delivering."""

    def __init__(self) -> None:
        self.delivered: list[tuple[DestinationBinding, Mapping[str, object]]] = []

    def deliver(
        self, *, binding: DestinationBinding, envelope: Mapping[str, object]
    ) -> object:
        self.delivered.append((binding, envelope))
        return None


def _bound(
    db: Session,
    *,
    destinations: object | None = "default",
    scope_json: dict[str, object] | None = None,
    capability: str = CAPABILITY,
) -> CapabilityBinding:
    """An installation + immutable revision + binding, wired the normal way."""
    installation = ConnectorInstallation(
        id=uuid.uuid4(),
        connector_key="fake_connector",
        connector_version="1.0.0",
        spi_range=">=1.0,<2.0",
        manifest_digest="d" * 64,
        name="primary",
        environment="production",
        state="enabled",
    )
    db.add(installation)
    db.flush()

    if destinations == "default":
        destinations = {
            capability: {
                "application": OWNER_APP,
                "scope": {"kind": "inbox", "ref": "support"},
            }
        }
    config: dict[str, object] = {"endpoint": "https://provider.example"}
    if destinations is not None:
        config["destinations"] = destinations

    revision = ConnectorConfigRevision(
        id=uuid.uuid4(),
        installation_id=installation.id,
        revision=1,
        schema_version="1",
        config_json=config,
        secret_refs={},
        config_digest="c" * 64,
        validation_status="valid",
    )
    db.add(revision)
    db.flush()
    installation.current_config_revision_id = revision.id
    db.flush()

    binding = CapabilityBinding(
        id=uuid.uuid4(),
        installation_id=installation.id,
        capability_id=capability,
        state="enabled",
        scope_json=scope_json,
    )
    db.add(binding)
    db.flush()
    return binding


# ── 1. The binding names application, scope and contract version ───────────


def test_a_binding_names_the_application_the_scope_and_the_contract_version(
    db: Session,
) -> None:
    binding = _bound(db)
    destination = resolve_destination(
        db, capability_binding_id=binding.id, registry=REGISTRY
    )
    assert destination.application == OWNER_APP
    assert destination.scope == LocalScope(kind="inbox", ref="support")
    assert destination.contract_version == 1
    # Provenance: WHICH immutable revision established this destination.
    assert destination.config_revision_id is not None


def test_the_contract_version_comes_from_the_id_so_it_cannot_disagree(
    db: Session,
) -> None:
    versioned = "alpha_domain.receive.v3"
    registry = CapabilityRegistry.from_declarations(
        [
            CapabilityContract(
                capability_id=versioned,
                owner=CONTRACT.owner,
                summary="v3 of the same contract",
            )
        ]
    )
    binding = _bound(
        db,
        capability=versioned,
        destinations={
            versioned: {
                "application": OWNER_APP,
                "scope": {"kind": "inbox", "ref": "support"},
            }
        },
    )
    destination = resolve_destination(
        db, capability_binding_id=binding.id, registry=registry
    )
    assert destination.contract_version == 3


def test_a_destination_binding_is_deeply_immutable(db: Session) -> None:
    destination = resolve_destination(
        db, capability_binding_id=_bound(db).id, registry=REGISTRY
    )
    with pytest.raises(AttributeError):
        destination.application = HOSTILE_APP  # type: ignore[misc]
    with pytest.raises(AttributeError):
        destination.scope.ref = "elsewhere"  # type: ignore[misc]


# ── 2. Fail closed in every direction ──────────────────────────────────────


def test_no_destination_block_is_a_refusal_not_a_default(db: Session) -> None:
    binding = _bound(db, destinations=None)
    with pytest.raises(DestinationNotBound, match="BEFORE any provider"):
        resolve_destination(db, capability_binding_id=binding.id, registry=REGISTRY)


def test_a_destination_bound_for_another_capability_does_not_serve_this_one(
    db: Session,
) -> None:
    binding = _bound(
        db,
        destinations={
            "alpha_domain.emit.v1": {
                "application": OWNER_APP,
                "scope": {"kind": "inbox", "ref": "support"},
            }
        },
    )
    with pytest.raises(DestinationNotBound, match="binds no destination"):
        resolve_destination(db, capability_binding_id=binding.id, registry=REGISTRY)


def test_an_application_without_a_scope_is_refused(db: Session) -> None:
    binding = _bound(db, destinations={CAPABILITY: {"application": OWNER_APP}})
    with pytest.raises(DestinationNotBound, match="declares no scope"):
        resolve_destination(db, capability_binding_id=binding.id, registry=REGISTRY)


def test_an_unknown_binding_id_is_a_refusal(db: Session) -> None:
    with pytest.raises(DestinationNotBound, match="does not exist"):
        resolve_destination(db, capability_binding_id=uuid.uuid4(), registry=REGISTRY)


def test_an_installation_with_no_current_revision_has_no_destination(
    db: Session,
) -> None:
    binding = _bound(db)
    installation = db.get(ConnectorInstallation, binding.installation_id)
    assert installation is not None
    installation.current_config_revision_id = None
    db.flush()
    with pytest.raises(DestinationNotBound, match="no current config revision"):
        resolve_destination(db, capability_binding_id=binding.id, registry=REGISTRY)


# ── 3. The declared owner is the only permitted destination ────────────────


def test_a_configuration_cannot_reassign_a_capabilitys_owner(db: Session) -> None:
    # The strongest single property here: even an operator-written, immutable,
    # digested configuration cannot route a capability to an application that
    # did not declare it. Two independent authorities must agree.
    binding = _bound(
        db,
        destinations={
            CAPABILITY: {
                "application": HOSTILE_APP,
                "scope": {"kind": "inbox", "ref": "support"},
            }
        },
    )
    with pytest.raises(UntrustedDestination) as exc:
        resolve_destination(db, capability_binding_id=binding.id, registry=REGISTRY)
    message = str(exc.value)
    assert HOSTILE_APP in message
    assert "alpha/messages" in message
    assert "cannot reassign" in message


def test_a_binding_on_an_undeclared_capability_is_refused_before_routing(
    db: Session,
) -> None:
    from dotmac_integration import UnknownCapabilityError

    binding = _bound(db)
    with pytest.raises(UnknownCapabilityError):
        resolve_destination(
            db,
            capability_binding_id=binding.id,
            registry=CapabilityRegistry.from_declarations([]),
        )


# ── 4. Provider metadata never selects — behaviour ─────────────────────────


def test_a_hostile_scope_json_does_not_change_the_destination(db: Session) -> None:
    # `scope_json` is documented, tested and asserted to be display-only. This
    # proves it behaviourally: the most routing-shaped payload anyone could put
    # in that column changes nothing.
    binding = _bound(
        db,
        scope_json={
            "application": HOSTILE_APP,
            "destination": HOSTILE_APP,
            "scope": {"kind": "inbox", "ref": "attacker"},
        },
    )
    destination = resolve_destination(
        db, capability_binding_id=binding.id, registry=REGISTRY
    )
    assert destination.application == OWNER_APP
    assert destination.scope == LocalScope(kind="inbox", ref="support")


def test_the_resolver_never_reads_scope_json() -> None:
    """Guard: `scope_json` is not a routing input, at the source level.

    Behaviour alone is not enough. A future edit could read `scope_json` as a
    "hint" or a fallback in a branch no existing test reaches, and every
    behavioural assertion above would still pass.
    """
    assert _reads_scope_json(_module_source()) is False


def test_the_scope_json_guard_bites() -> None:
    """Sensitivity proof for the guard above.

    A check over a file that happens not to contain a name passes for the wrong
    reason. This shows the same detector flagging the code it forbids — in both
    the attribute and the subscript forms, because a reader reaching for
    `scope_json` will write one or the other.
    """
    assert _reads_scope_json("def f(b):\n    return b.scope_json\n") is True
    assert _reads_scope_json("def f(b):\n    return b.__dict__['scope_json']\n") is True
    assert _reads_scope_json("def f(b):\n    return b.policy_json\n") is False


def _module_source() -> str:
    return Path(str(module_under_test.__file__)).read_text(encoding="utf-8")


def _reads_scope_json(source: str) -> bool:
    """Any reference to the name at all, docstrings and comments removed.

    Docstrings are stripped rather than searched, so the module may *explain*
    why it does not read `scope_json` without the explanation defeating the
    guard. Comments never reach the AST.
    """
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "scope_json":
            return True
        if isinstance(node, ast.Constant) and node.value == "scope_json":
            return True
        if isinstance(node, ast.Name) and node.id == "scope_json":
            return True
    return False


# ── 5. Provider metadata never selects — signature ─────────────────────────

#: Parameter names through which a provider-influenced value could arrive. The
#: point is not that these exact words are dangerous; it is that a destination
#: resolver has no legitimate reason to accept ANY of them, so the set can be
#: broad without producing false refusals.
PROVIDER_INPUT_NAMES = frozenset(
    {
        "payload",
        "payload_json",
        "body",
        "raw_body",
        "headers",
        "metadata",
        "meta",
        "event",
        "provider_event",
        "provider_payload",
        "claim",
        "claimed_application",
        "application",
        "destination",
    }
)


def _parameter_names(func: object) -> frozenset[str]:
    return frozenset(inspect.signature(func).parameters)  # type: ignore[arg-type]


def test_the_resolver_accepts_no_provider_influenced_parameter() -> None:
    """The security property, stated as a signature.

    Not "we validate the payload" but "the payload is not an input". A resolver
    that cannot see provider data cannot be tricked by it, and this fails the
    build the moment a parameter is added through which it could arrive.
    """
    assert _parameter_names(resolve_destination) & PROVIDER_INPUT_NAMES == frozenset()
    assert _parameter_names(resolve_destination) == frozenset(
        {"db", "capability_binding_id", "registry"}
    )


def test_the_signature_guard_bites() -> None:
    """Sensitivity proof: the same check flags a resolver that took a payload."""

    def hostile_resolver(db: object, *, payload: dict[str, object]) -> None: ...

    assert _parameter_names(hostile_resolver) & PROVIDER_INPUT_NAMES == {"payload"}


def test_a_profile_is_reachable_only_from_a_resolved_binding() -> None:
    """The second half: no function turns an application NAME into a client.

    Even if provider metadata somehow produced the string `"alpha"`, there is no
    lookup that accepts it — `require_profile` and `destination_client` both
    take a `DestinationBinding` this module resolved.
    """
    for func in (require_profile, destination_client):
        parameters = inspect.signature(func).parameters
        assert list(parameters) == ["binding"]
        assert parameters["binding"].annotation in (
            DestinationBinding,
            "DestinationBinding",
        )


# ── 6. Provider metadata never selects — return types ──────────────────────


def test_no_function_taking_provider_input_returns_a_destination() -> None:
    """Guard: corroboration cannot be refactored into selection.

    `corroborate` is the only function here that touches a provider claim, and
    it returns a finding. If a later change made it — or any sibling — return a
    `DestinationBinding` or a `DestinationProfile`, provider metadata would
    select the destination while every behavioural test still passed.
    """
    assert _selects_from_provider_input(_module_source()) == []


def test_the_return_type_guard_bites() -> None:
    """Sensitivity proof: exactly the refactor the guard exists to catch."""
    hostile = (
        "def pick(binding, *, claimed_application):\n"
        "    return DestinationBinding()\n"
    )
    assert _selects_from_provider_input(hostile) == ["pick"]
    benign = (
        "def pick(binding, *, claimed_application) -> Corroboration:\n"
        "    return Corroboration()\n"
    )
    assert _selects_from_provider_input(benign) == []


#: Types that ARE a routing decision. A function fed provider data may not
#: produce one.
_ROUTING_RETURNS = frozenset({"DestinationBinding", "DestinationProfile"})


def _selects_from_provider_input(source: str) -> list[str]:
    """Functions that both accept provider input and yield a routing decision.

    Reads the declared return annotation where there is one, and falls back to
    the constructors actually returned where there is not — an unannotated
    function must not be able to slip past by omitting the annotation the guard
    reads.
    """
    offenders: list[str] = []
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        args = node.args
        names = {a.arg for a in (*args.args, *args.kwonlyargs, *args.posonlyargs)}
        if not names & PROVIDER_INPUT_NAMES:
            continue
        returns = node.returns
        if returns is not None:
            if isinstance(returns, ast.Name) and returns.id in _ROUTING_RETURNS:
                offenders.append(node.name)
            elif (
                isinstance(returns, ast.Constant) and returns.value in _ROUTING_RETURNS
            ):
                offenders.append(node.name)
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Return)
                and isinstance(inner.value, ast.Call)
                and isinstance(inner.value.func, ast.Name)
                and inner.value.func.id in _ROUTING_RETURNS
            ):
                offenders.append(node.name)
                break
    return offenders


# ── 7. Corroboration records; it never routes ──────────────────────────────


def test_a_matching_provider_claim_is_corroboration(db: Session) -> None:
    destination = resolve_destination(
        db, capability_binding_id=_bound(db).id, registry=REGISTRY
    )
    finding = corroborate(destination, claimed_application=OWNER_APP)
    assert isinstance(finding, Corroboration)
    assert finding.agrees is True


def test_provider_silence_is_not_a_disagreement(db: Session) -> None:
    # Most providers have no concept of the destination application. Treating
    # silence as conflict would fail every honest delivery.
    destination = resolve_destination(
        db, capability_binding_id=_bound(db).id, registry=REGISTRY
    )
    finding = corroborate(destination, claimed_application=None)
    assert finding.agrees is True
    assert finding.claimed_application is None


def test_a_disagreeing_claim_fails_closed_and_never_reroutes(db: Session) -> None:
    # § 11.2: provider metadata naming B, binding naming A → routed to A, the
    # disagreement recorded, and the delivery refused rather than reconciled
    # toward the payload.
    destination = resolve_destination(
        db, capability_binding_id=_bound(db).id, registry=REGISTRY
    )
    finding = corroborate(destination, claimed_application=HOSTILE_APP)
    assert finding.agrees is False
    assert finding.binding_application == OWNER_APP  # unchanged
    assert destination.application == OWNER_APP  # and the binding is untouched

    with pytest.raises(DestinationDisagreement) as exc:
        require_corroborated(destination, claimed_application=HOSTILE_APP)
    assert "not reconciled toward provider metadata" in str(exc.value)


# ── 8. The assembly supplies profiles and authenticated clients ────────────


def test_the_module_holds_no_client_until_the_assembly_supplies_one(
    db: Session,
) -> None:
    destination = resolve_destination(
        db, capability_binding_id=_bound(db).id, registry=REGISTRY
    )
    with pytest.raises(DestinationProfileMissing, match="composing assembly"):
        require_profile(destination)


def test_a_supplied_profile_is_reached_through_the_binding(db: Session) -> None:
    client = _RecordingClient()
    install_destination_profiles(
        [
            DestinationProfile(
                application=OWNER_APP,
                contract_versions=frozenset({1}),
                client=client,
            )
        ]
    )
    destination = resolve_destination(
        db, capability_binding_id=_bound(db).id, registry=REGISTRY
    )
    assert destination_client(destination) is client
    destination_client(destination).deliver(binding=destination, envelope={"a": 1})
    assert client.delivered[0][0] is destination


def test_a_destination_that_has_not_deployed_the_contract_refuses_loudly(
    db: Session,
) -> None:
    install_destination_profiles(
        [
            DestinationProfile(
                application=OWNER_APP,
                contract_versions=frozenset({2}),  # v1 not (or no longer) accepted
                client=_RecordingClient(),
            )
        ]
    )
    destination = resolve_destination(
        db, capability_binding_id=_bound(db).id, registry=REGISTRY
    )
    with pytest.raises(DestinationProfileMissing, match="mis-parse"):
        require_profile(destination)


def test_two_profiles_for_one_application_are_refused() -> None:
    with pytest.raises(DestinationBindingError, match="iteration order"):
        install_destination_profiles(
            [
                DestinationProfile(
                    application=OWNER_APP,
                    contract_versions=frozenset({1}),
                    client=_RecordingClient(),
                ),
                DestinationProfile(
                    application=OWNER_APP,
                    contract_versions=frozenset({2}),
                    client=_RecordingClient(),
                ),
            ]
        )


def test_installing_profiles_replaces_rather_than_merges() -> None:
    first = _RecordingClient()
    install_destination_profiles(
        [
            DestinationProfile(
                application=OWNER_APP, contract_versions=frozenset({1}), client=first
            )
        ]
    )
    install_destination_profiles(
        [
            DestinationProfile(
                application=HOSTILE_APP,
                contract_versions=frozenset({1}),
                client=_RecordingClient(),
            )
        ]
    )
    binding = DestinationBinding(
        capability_binding_id=uuid.uuid4(),
        capability_id=CAPABILITY,
        application=OWNER_APP,
        scope=LocalScope(kind="inbox", ref="support"),
        contract_version=1,
        config_revision_id=uuid.uuid4(),
    )
    with pytest.raises(DestinationProfileMissing):
        require_profile(binding)


def test_a_profile_accepting_no_version_is_not_a_destination() -> None:
    with pytest.raises(DestinationBindingError, match="accepts no"):
        DestinationProfile(
            application=OWNER_APP,
            contract_versions=frozenset(),
            client=_RecordingClient(),
        )
