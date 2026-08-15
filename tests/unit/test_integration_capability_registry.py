"""Capability ownership — declared by the business owner, never by the host.

The three governance failures are three DIFFERENT operator actions, so this file
proves they are three different exceptions with three different sentences. A
single "invalid capability" error would say something is wrong and not what to
do, and that is the failure mode ADR-0008's registries exist to remove.

No test here names a real fleet capability id. The ids are synthetic on purpose:
this module must be provable without knowing a single member of the vocabulary
it hosts, which is the same property `test_capability_ownership.py` asserts of
the source.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from dotmac_integration import (
    EMPTY_REGISTRY,
    CapabilityContract,
    CapabilityOwner,
    CapabilityRegistry,
    CapabilityRegistryError,
    CapabilityRegistryNotInstalled,
    DuplicateCapabilityDeclaration,
    OrphanCapabilityError,
    UnknownCapabilityError,
    capability_registry,
    contract_from_declaration,
    install_capability_registry,
    require_declared_for_binding,
    require_governable,
    require_implements_only_declared,
    require_no_orphans,
)
from dotmac_integration.capability_registry import _reset_capability_registry
from dotmac_integration.spi import (
    CapabilityDeclaration,
    ConnectorManifest,
    InvalidManifestError,
    SpiRange,
)

OWNER = CapabilityOwner(application="alpha", module="messages")
OTHER_OWNER = CapabilityOwner(application="beta", module="records")

RECEIVE = "alpha_domain.receive.v1"
EMIT = "alpha_domain.emit.v1"


def _contract(capability_id: str, owner: CapabilityOwner = OWNER) -> CapabilityContract:
    return CapabilityContract(
        capability_id=capability_id, owner=owner, summary="a synthetic contract"
    )


def _manifest(*capability_ids: str, key: str = "fake_connector") -> ConnectorManifest:
    return ConnectorManifest(
        connector_key=key,
        version="1.0.0",
        spi_range=SpiRange.parse(">=1.0,<2.0"),
        capabilities=tuple(
            CapabilityDeclaration(capability_id=c) for c in capability_ids
        ),
    )


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    _reset_capability_registry()
    yield
    _reset_capability_registry()


# ── The contract value object ──────────────────────────────────────────────


def test_a_contract_reuses_the_spi_id_shape_rather_than_a_second_regex() -> None:
    # The SPI owns what a legal capability id looks like. A second opinion here
    # would be a second answer to one question, and the two would drift.
    with pytest.raises(InvalidManifestError):
        _contract("NotACapabilityId")
    with pytest.raises(InvalidManifestError):
        _contract("alpha_domain.receive")  # no version — the version IS identity


def test_the_version_is_read_out_of_the_id_so_the_two_cannot_disagree() -> None:
    assert _contract("alpha_domain.receive.v1").contract_version == 1
    assert _contract("alpha_domain.receive.v12").contract_version == 12
    assert _contract(RECEIVE).domain == "alpha_domain.receive"


def test_a_contract_nobody_can_describe_is_refused() -> None:
    with pytest.raises(CapabilityRegistryError, match="no summary"):
        CapabilityContract(capability_id=RECEIVE, owner=OWNER, summary="   ")


def test_an_owner_key_must_be_a_key_not_a_label() -> None:
    with pytest.raises(CapabilityRegistryError, match="application"):
        CapabilityOwner(application="Alpha App", module="messages")
    with pytest.raises(CapabilityRegistryError, match="module"):
        CapabilityOwner(application="alpha", module="")


def test_contracts_are_deeply_immutable() -> None:
    contract = _contract(RECEIVE)
    with pytest.raises(AttributeError):
        contract.capability_id = EMIT  # type: ignore[misc]
    with pytest.raises(AttributeError):
        contract.owner.application = "beta"  # type: ignore[misc]


def test_an_owner_may_publish_its_spi_declaration_directly() -> None:
    # So the id is stated ONCE, in the shape connector authors already read.
    declaration = CapabilityDeclaration(capability_id=RECEIVE)
    contract = contract_from_declaration(
        declaration, owner=OWNER, summary="inbound messages"
    )
    assert contract.capability_id == declaration.capability_id
    assert contract.owner is OWNER


# ── Refusal 1: declared twice ──────────────────────────────────────────────


def test_two_owners_claiming_one_capability_is_refused_at_construction() -> None:
    with pytest.raises(DuplicateCapabilityDeclaration) as exc:
        CapabilityRegistry.from_declarations(
            [_contract(RECEIVE, OWNER), _contract(RECEIVE, OTHER_OWNER)]
        )
    message = str(exc.value)
    # Both owners are named: the fix is theirs, and an error listing only the id
    # sends the operator hunting for who published it.
    assert "alpha/messages" in message
    assert "beta/records" in message
    assert "declared twice" in message


def test_one_owner_declaring_the_same_capability_twice_is_also_refused() -> None:
    with pytest.raises(DuplicateCapabilityDeclaration):
        CapabilityRegistry.from_declarations([_contract(RECEIVE), _contract(RECEIVE)])


def test_two_versions_of_one_contract_are_not_a_duplicate() -> None:
    # `.v1` and `.v2` are different contracts one owner may publish
    # independently — refusing them together would forbid a migration window.
    registry = CapabilityRegistry.from_declarations(
        [_contract("alpha_domain.receive.v1"), _contract("alpha_domain.receive.v2")]
    )
    assert len(registry.contracts) == 2


# ── Refusal 2: named but never declared ────────────────────────────────────


def test_a_binding_naming_an_undeclared_capability_is_refused() -> None:
    registry = CapabilityRegistry.from_declarations([_contract(RECEIVE)])
    with pytest.raises(UnknownCapabilityError) as exc:
        require_declared_for_binding(
            registry, capability_id=EMIT, connector_key="fake_connector"
        )
    message = str(exc.value)
    assert "not declared by any owning application" in message
    assert RECEIVE in message  # says what IS declared, so the fix is one step
    assert "fake_connector" in message


def test_a_connector_cannot_mint_a_capability_by_publishing_a_manifest() -> None:
    registry = CapabilityRegistry.from_declarations([_contract(RECEIVE)])
    with pytest.raises(UnknownCapabilityError) as exc:
        require_implements_only_declared(registry, _manifest(RECEIVE, EMIT))
    message = str(exc.value)
    assert EMIT in message
    assert "not a way to mint one" in message


def test_a_connector_implementing_only_declared_capabilities_passes() -> None:
    registry = CapabilityRegistry.from_declarations(
        [_contract(RECEIVE), _contract(EMIT)]
    )
    require_implements_only_declared(registry, _manifest(RECEIVE, EMIT))


# ── Refusal 3: declared, implemented by nothing ────────────────────────────


def test_a_declaration_no_connector_implements_is_an_orphan() -> None:
    registry = CapabilityRegistry.from_declarations(
        [_contract(RECEIVE), _contract(EMIT, OTHER_OWNER)]
    )
    with pytest.raises(OrphanCapabilityError) as exc:
        require_no_orphans(registry, [_manifest(RECEIVE)])
    message = str(exc.value)
    assert EMIT in message
    assert "beta/records" in message  # the owner decides the fix
    assert RECEIVE not in message  # the implemented one is not reported


def test_a_fully_implemented_registry_has_no_orphans() -> None:
    registry = CapabilityRegistry.from_declarations([_contract(RECEIVE)])
    require_no_orphans(registry, [_manifest(RECEIVE)])


def test_the_three_failures_are_three_distinct_types() -> None:
    # Sensitivity proof for the whole file: an `except CapabilityRegistryError`
    # that caught all three would make every assertion above pass while telling
    # an operator nothing. They must be distinguishable.
    assert (
        len(
            {
                DuplicateCapabilityDeclaration,
                UnknownCapabilityError,
                OrphanCapabilityError,
            }
        )
        == 3
    )
    for error in (
        DuplicateCapabilityDeclaration,
        UnknownCapabilityError,
        OrphanCapabilityError,
    ):
        assert issubclass(error, CapabilityRegistryError)
        assert not any(
            issubclass(error, other) and error is not other
            for other in (
                DuplicateCapabilityDeclaration,
                UnknownCapabilityError,
                OrphanCapabilityError,
            )
        )


def test_require_governable_runs_all_three_in_operator_order() -> None:
    registry = CapabilityRegistry.from_declarations([_contract(RECEIVE)])
    require_governable(registry, [_manifest(RECEIVE)], bound_capability_ids=[RECEIVE])
    with pytest.raises(UnknownCapabilityError):
        require_governable(registry, [_manifest(RECEIVE)], bound_capability_ids=[EMIT])
    with pytest.raises(OrphanCapabilityError):
        require_governable(
            CapabilityRegistry.from_declarations([_contract(RECEIVE), _contract(EMIT)]),
            [_manifest(RECEIVE)],
        )


# ── The installed seam: fail closed, and "empty" is not "absent" ───────────


def test_an_uninstalled_registry_refuses_rather_than_answering_empty() -> None:
    with pytest.raises(CapabilityRegistryNotInstalled) as exc:
        capability_registry()
    assert "install_capability_registry" in str(exc.value)


def test_an_explicitly_empty_registry_is_a_legitimate_installed_state() -> None:
    # A deployment integrating with nothing declares nothing — and that must be
    # a STATEMENT it made, not the shape a forgotten call happens to produce.
    install_capability_registry(EMPTY_REGISTRY)
    assert capability_registry().declared_ids == frozenset()
    with pytest.raises(UnknownCapabilityError):
        require_declared_for_binding(capability_registry(), capability_id=RECEIVE)


def test_installing_replaces_rather_than_accumulates() -> None:
    install_capability_registry(
        CapabilityRegistry.from_declarations([_contract(RECEIVE)])
    )
    install_capability_registry(CapabilityRegistry.from_declarations([_contract(EMIT)]))
    assert capability_registry().declared_ids == frozenset({EMIT})


def test_the_registry_reports_what_one_application_owns() -> None:
    registry = CapabilityRegistry.from_declarations(
        [_contract(RECEIVE, OWNER), _contract(EMIT, OTHER_OWNER)]
    )
    assert [c.capability_id for c in registry.owned_by("alpha")] == [RECEIVE]
    assert registry.owned_by("gamma") == ()
