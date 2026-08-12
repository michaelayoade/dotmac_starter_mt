"""Validation happens in the module, before anything is written.

The invariant this file protects: **an allocation never contains a capability
code the named product does not declare, and a snapshot is refused atomically or
accepted whole.** A suite that only checked the happy path would pass against an
implementation that wrote three rows and then discovered the fourth code was
undeclared.

In-memory SQLite — logic only. Grants and the live-catalogue replay behaviour
are proven against real Postgres in
`tests/test_entitlement_allocation_canaries.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from dotmac_entitlement_allocation import (
    Allocation,
    AllocationEntry,
    ContractEntitlement,
    ContractSnapshot,
    EmptyAllocationError,
    UndeclaredCapabilityError,
    UnknownProductError,
    allocation_product,
    stage_allocation,
)
from dotmac_kernel.models import Base
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker


class FakeCatalogue:
    """A catalogue reader over a `{product: {codes}}` map.

    Deliberately NOT a stub that always says yes: the failing paths are the
    point, and a permissive fake would make every test below vacuous.
    """

    def __init__(self, declared: dict[str, set[str]]) -> None:
        self.declared = declared
        self.calls: list[tuple[str, str]] = []

    def require_declared(self, *, product_code: str, capability_code: str) -> None:
        self.calls.append((product_code, capability_code))
        if product_code not in self.declared:
            raise UnknownProductError(f"unknown product {product_code!r}")
        if capability_code not in self.declared[product_code]:
            raise UndeclaredCapabilityError(product_code, (capability_code,))


class KeyErrorCatalogue:
    """An adapter that signals "no such code" with a bare `KeyError`.

    The kernel's own `UndeclaredCapabilityError` subclasses `KeyError`, so an
    adapter wrapping `CapabilityCatalogue.require` raises exactly this. The
    module must treat it as undeclared rather than letting it escape as an
    unhandled error.
    """

    def require_declared(self, *, product_code: str, capability_code: str) -> None:
        raise KeyError(capability_code)


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _attach(dbapi_connection, _record):
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS mod_ealloc")

    Base.metadata.create_all(
        engine,
        tables=[t for t in Base.metadata.tables.values() if t.schema == "mod_ealloc"],
    )
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _snapshot(**overrides: object) -> ContractSnapshot:
    fields: dict[str, object] = {
        "contract_ref": uuid.uuid4(),
        "product_code": "dotmac-sub",
        "customer_ref": "acme-isp",
        "content_hash": "h" * 64,
        "source_event_id": "evt-1",
        "entries": (
            ContractEntitlement("billing.invoicing", 1),
            ContractEntitlement("network.provisioning", 25),
        ),
    }
    fields.update(overrides)
    return ContractSnapshot(**fields)  # type: ignore[arg-type]


@pytest.fixture
def catalogue() -> FakeCatalogue:
    return FakeCatalogue(
        {
            "dotmac-sub": {"billing.invoicing", "network.provisioning"},
            "dotmac-erp": {"finance.ledger"},
        }
    )


class TestStaging:
    def test_stages_a_valid_snapshot(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        view = stage_allocation(db, _snapshot(), catalogues=catalogue)
        assert view.replayed is False
        assert view.product_code == "dotmac-sub"
        assert view.entries == (
            ("billing.invoicing", 1),
            ("network.provisioning", 25),
        )

    def test_every_entry_is_checked(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        """Not just the first — a loop that short-circuits on success would let
        a later undeclared code through."""
        stage_allocation(db, _snapshot(), catalogues=catalogue)
        assert catalogue.calls == [
            ("dotmac-sub", "billing.invoicing"),
            ("dotmac-sub", "network.provisioning"),
        ]

    def test_the_validated_product_is_persisted(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        """Licence issuance reads this instead of accepting a fresh value."""
        view = stage_allocation(db, _snapshot(), catalogues=catalogue)
        assert allocation_product(db, view.id) == "dotmac-sub"

    def test_nothing_is_committed(self, db: Session, catalogue: FakeCatalogue) -> None:
        stage_allocation(db, _snapshot(), catalogues=catalogue)
        db.rollback()
        assert db.query(Allocation).count() == 0


class TestRejectionIsAtomic:
    def test_one_undeclared_entry_rejects_the_whole_snapshot(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        """REQUIRED CANARY. The failure mode is a partially written allocation:
        three rows accepted, the fourth code undeclared, caller sees an error
        while the database keeps three entitlements nobody authorized."""
        snapshot = _snapshot(
            entries=(
                ContractEntitlement("billing.invoicing"),
                ContractEntitlement("network.provisioning"),
                ContractEntitlement("nobody.declared.this"),
            )
        )
        with pytest.raises(UndeclaredCapabilityError):
            stage_allocation(db, snapshot, catalogues=catalogue)

        assert db.query(Allocation).count() == 0
        assert db.query(AllocationEntry).count() == 0

    def test_the_error_names_every_undeclared_code_not_just_the_first(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        """A caller repairing a manifest wants the whole list; one at a time
        turns a single review into several."""
        snapshot = _snapshot(
            entries=(
                ContractEntitlement("ghost.one"),
                ContractEntitlement("billing.invoicing"),
                ContractEntitlement("ghost.two"),
            )
        )
        with pytest.raises(UndeclaredCapabilityError) as raised:
            stage_allocation(db, snapshot, catalogues=catalogue)
        assert set(raised.value.codes) == {"ghost.one", "ghost.two"}

    def test_a_bare_keyerror_adapter_is_treated_as_undeclared(
        self, db: Session
    ) -> None:
        """The kernel's UndeclaredCapabilityError IS a KeyError, so an adapter
        over `CapabilityCatalogue.require` raises exactly this."""
        with pytest.raises(UndeclaredCapabilityError):
            stage_allocation(db, _snapshot(), catalogues=KeyErrorCatalogue())
        assert db.query(Allocation).count() == 0


class TestFailClosed:
    def test_an_unknown_product_writes_nothing(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        """REQUIRED CANARY. An unknown product is not an empty catalogue — it is
        a caller who cannot prove anything about the codes it is asking for."""
        with pytest.raises(UnknownProductError):
            stage_allocation(
                db, _snapshot(product_code="typo-product"), catalogues=catalogue
            )
        assert db.query(Allocation).count() == 0

    def test_product_a_catalogue_cannot_authorize_product_b(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        """REQUIRED CANARY. `finance.ledger` is real — for dotmac-erp. Asking
        for it under dotmac-sub must fail, or the product dimension is
        decorative and an allocation could be relabelled."""
        snapshot = _snapshot(
            product_code="dotmac-sub",
            entries=(ContractEntitlement("finance.ledger"),),
        )
        with pytest.raises(UndeclaredCapabilityError):
            stage_allocation(db, snapshot, catalogues=catalogue)
        assert db.query(Allocation).count() == 0

    def test_the_same_code_succeeds_under_its_own_product(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        """Specificity for the test above: it must fail because of the PRODUCT,
        not because the code is unknown everywhere."""
        snapshot = _snapshot(
            product_code="dotmac-erp",
            entries=(ContractEntitlement("finance.ledger"),),
        )
        assert stage_allocation(db, snapshot, catalogues=catalogue).replayed is False

    def test_an_empty_snapshot_is_refused(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        with pytest.raises(EmptyAllocationError):
            stage_allocation(db, _snapshot(entries=()), catalogues=catalogue)

    def test_a_non_positive_quantity_is_refused(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        snapshot = _snapshot(entries=(ContractEntitlement("billing.invoicing", 0),))
        with pytest.raises(EmptyAllocationError):
            stage_allocation(db, snapshot, catalogues=catalogue)

    def test_a_blank_product_code_is_refused_before_the_catalogue_is_asked(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        with pytest.raises(UnknownProductError):
            stage_allocation(db, _snapshot(product_code=""), catalogues=catalogue)
        assert catalogue.calls == []


class TestReplay:
    def test_restaging_the_same_activation_is_a_no_op(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        snapshot = _snapshot()
        first = stage_allocation(db, snapshot, catalogues=catalogue)
        second = stage_allocation(db, snapshot, catalogues=catalogue)

        assert second.replayed is True
        assert second.id == first.id
        assert db.query(Allocation).count() == 1

    def test_a_replay_does_not_reconsult_the_catalogue(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        """REQUIRED CANARY (unit half). An already-staged allocation is immutable
        history whose legality was decided when it was staged. Re-validating it
        would make a delivered entitlement unreplayable the day a capability is
        retired — turning an idempotent redelivery into an outage."""
        snapshot = _snapshot()
        stage_allocation(db, snapshot, catalogues=catalogue)
        calls_after_first = len(catalogue.calls)

        # The catalogue now forgets everything — a retired product.
        catalogue.declared.clear()

        replay = stage_allocation(db, snapshot, catalogues=catalogue)
        assert replay.replayed is True
        assert len(catalogue.calls) == calls_after_first

    def test_a_different_content_hash_is_a_different_allocation(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        """An amended contract version is a new activation, not a mutation."""
        contract = uuid.uuid4()
        stage_allocation(
            db,
            _snapshot(contract_ref=contract, content_hash="a" * 64),
            catalogues=catalogue,
        )
        second = stage_allocation(
            db,
            _snapshot(contract_ref=contract, content_hash="b" * 64),
            catalogues=catalogue,
        )
        assert second.replayed is False
        assert db.query(Allocation).count() == 2


class TestNoTrustedCallerEscape:
    def test_there_is_no_way_to_skip_validation(self) -> None:
        """No `validated=True`, no `skip_validation`, no `trusted`. Any of them
        would make the invariant optional, and an optional invariant is a
        comment."""
        import inspect

        parameters = set(inspect.signature(stage_allocation).parameters)
        assert parameters == {"db", "snapshot", "catalogues"}

    def test_the_catalogue_is_required_not_defaulted(self) -> None:
        """A default would let a caller omit it and silently validate against
        nothing."""
        import inspect

        catalogues = inspect.signature(stage_allocation).parameters["catalogues"]
        assert catalogues.default is inspect.Parameter.empty
        assert catalogues.kind is inspect.Parameter.KEYWORD_ONLY
