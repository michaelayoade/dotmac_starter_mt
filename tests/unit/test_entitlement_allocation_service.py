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
    AllocatedCapability,
    Allocation,
    AllocationConflictError,
    AllocationEntry,
    AllocationStatus,
    ContractEntitlement,
    ContractSnapshot,
    DuplicateCapabilityError,
    EmptyAllocationError,
    UndeclaredCapabilityError,
    UnknownProductError,
    allocation_product,
    module,
    stage_allocation,
)
from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
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


class BrokenAdapter:
    """An adapter that leaks its backing store's exception instead of
    translating.

    The kernel's own `UndeclaredCapabilityError` IS a `KeyError`, so it is
    tempting for the service to catch `KeyError` broadly. An earlier revision
    did, and it disguised three different failures as the cheapest one: a
    genuine undeclared code, a missing product, and an adapter defect.
    """

    def require_declared(self, *, product_code: str, capability_code: str) -> None:
        raise KeyError(capability_code)


@pytest.fixture(autouse=True)
def _installed_module_audit_actions() -> None:
    """Exercise the module as an adopter does: its manifest is installed."""
    install_audit_actions(AuditActionRegistry.from_manifests([module]))


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _attach(dbapi_connection, _record):
        # pysqlite does not emit BEGIN on its own, which leaves SAVEPOINT
        # semantics broken — and the service now runs inside one
        # (`conflict_savepoint`, via the kernel's at-most-once owner). Without
        # SQLAlchemy's documented workaround a rollback silently keeps the row,
        # and `test_nothing_is_committed` would fail against CORRECT code.
        dbapi_connection.isolation_level = None
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS mod_ealloc")

    @event.listens_for(engine, "begin")
    def _emit_begin(connection):
        connection.exec_driver_sql("BEGIN")

    # The service now writes through the kernel's at-most-once owner and its
    # platform audit trail, so the fixture must build those tables too — a
    # module that reuses a kernel facility inherits its storage.
    Base.metadata.create_all(
        engine,
        tables=[
            table
            for table in Base.metadata.tables.values()
            if table.schema == "mod_ealloc"
            or table.name
            in {
                "platform_idempotency_records",
                "platform_audit_events",
                "platform_admins",
            }
        ],
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
        # Unique per snapshot by default: `source_event_id` is a DELIVERY
        # identity, and reusing one across two different activations is itself
        # a conflict (proved in TestTheDeliveryKeyIsAlsoGuarded below).
        "source_event_id": f"evt-{uuid.uuid4().hex[:12]}",
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
            AllocatedCapability("billing.invoicing", 1),
            AllocatedCapability("network.provisioning", 25),
        )
        assert view.status is AllocationStatus.STAGED

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

    def test_an_untranslated_adapter_error_surfaces_as_itself(
        self, db: Session
    ) -> None:
        """The port requires adapters to raise THIS module's errors. When one
        does not, the defect must be visible as a defect — not relabelled as an
        undeclared capability, which would send someone to edit a manifest that
        was never wrong."""
        with pytest.raises(KeyError) as raised:
            stage_allocation(db, _snapshot(), catalogues=BrokenAdapter())
        assert not isinstance(raised.value, UndeclaredCapabilityError)
        assert db.query(Allocation).count() == 0


class TestDuplicatesAreRejectedNotAggregated:
    def test_a_repeated_capability_code_is_refused(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        """Deciding whether two lines of 10 seats mean 20, or that the second
        supersedes the first, is a COMMERCIAL rule owned by whoever owns
        contracts. Inventing one here would make this module a quantity
        authority as well as a projection."""
        snapshot = _snapshot(
            entries=(
                ContractEntitlement("billing.invoicing", 10),
                ContractEntitlement("billing.invoicing", 10),
            )
        )
        with pytest.raises(DuplicateCapabilityError) as raised:
            stage_allocation(db, snapshot, catalogues=catalogue)
        assert raised.value.codes == ("billing.invoicing",)

    def test_duplicates_fail_before_the_catalogue_is_consulted(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        """Early and cheap, not late through a unique index whose message is
        about an index."""
        snapshot = _snapshot(
            entries=(
                ContractEntitlement("billing.invoicing"),
                ContractEntitlement("billing.invoicing"),
            )
        )
        with pytest.raises(DuplicateCapabilityError):
            stage_allocation(db, snapshot, catalogues=catalogue)
        assert catalogue.calls == []
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

    def test_the_same_activation_with_different_entries_conflicts(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        """REQUIRED. `(contract_ref, content_hash)` identifies an activated
        contract VERSION. The same pair arriving with a different entitlement
        set is not a replay — it is two different claims about one activation,
        and returning the first silently would hide the disagreement forever."""
        contract, digest = uuid.uuid4(), "c" * 64
        stage_allocation(
            db,
            _snapshot(
                contract_ref=contract,
                content_hash=digest,
                entries=(ContractEntitlement("billing.invoicing"),),
            ),
            catalogues=catalogue,
        )
        with pytest.raises(AllocationConflictError):
            stage_allocation(
                db,
                _snapshot(
                    contract_ref=contract,
                    content_hash=digest,
                    entries=(ContractEntitlement("network.provisioning"),),
                ),
                catalogues=catalogue,
            )

    @pytest.mark.parametrize(
        ("field", "value"),
        [("product_code", "dotmac-erp"), ("customer_ref", "someone-else")],
    )
    def test_the_same_activation_with_a_different_claim_conflicts(
        self, db: Session, catalogue: FakeCatalogue, field: str, value: str
    ) -> None:
        """Every field of the claim is fingerprinted, not just the entries."""
        contract, digest = uuid.uuid4(), "d" * 64
        base = {"contract_ref": contract, "content_hash": digest}
        stage_allocation(db, _snapshot(**base), catalogues=catalogue)
        with pytest.raises(AllocationConflictError):
            stage_allocation(
                db, _snapshot(**base, **{field: value}), catalogues=catalogue
            )

    def test_a_redelivery_under_a_new_event_id_is_still_a_replay(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        """The fingerprint excludes `source_event_id` on purpose: two
        deliveries of one activation are the same CLAIM, and including the
        delivery id would make every redelivery look like a conflict."""
        contract, digest = uuid.uuid4(), "e" * 64
        first = stage_allocation(
            db,
            _snapshot(contract_ref=contract, content_hash=digest, source_event_id="a"),
            catalogues=catalogue,
        )
        again = stage_allocation(
            db,
            _snapshot(contract_ref=contract, content_hash=digest, source_event_id="b"),
            catalogues=catalogue,
        )
        assert again.replayed is True
        assert again.id == first.id

    def test_entry_order_does_not_change_the_fingerprint(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        """A transport must not be able to manufacture a conflict by reordering
        a list."""
        contract, digest = uuid.uuid4(), "f" * 64
        forward = (
            ContractEntitlement("billing.invoicing"),
            ContractEntitlement("network.provisioning", 25),
        )
        stage_allocation(
            db,
            _snapshot(contract_ref=contract, content_hash=digest, entries=forward),
            catalogues=catalogue,
        )
        replay = stage_allocation(
            db,
            _snapshot(
                contract_ref=contract, content_hash=digest, entries=forward[::-1]
            ),
            catalogues=catalogue,
        )
        assert replay.replayed is True

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


class TestTheDeliveryKeyIsAlsoGuarded:
    """`source_event_id` identifies a DELIVERY; `(contract_ref, content_hash)`
    identifies an ACTIVATION. Both need guarding, and they fail differently."""

    def test_the_same_event_id_carrying_a_different_claim_conflicts(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        """REQUIRED. An at-least-once transport may redeliver an event, but it
        must never reuse one event id for a different request — that is a
        producer defect, and the kernel's at-most-once owner is what notices."""
        from dotmac_kernel.idempotency import IdempotencyConflict

        stage_allocation(
            db, _snapshot(source_event_id="evt-shared"), catalogues=catalogue
        )
        with pytest.raises(IdempotencyConflict):
            stage_allocation(
                db, _snapshot(source_event_id="evt-shared"), catalogues=catalogue
            )

    def test_staging_writes_exactly_one_audit_event(
        self, db: Session, catalogue: FakeCatalogue
    ) -> None:
        """Inside the idempotent operation, so a redelivered event cannot
        produce a trail that overstates how often this happened."""
        from dotmac_kernel.audit import PlatformAuditEvent

        snapshot = _snapshot()
        stage_allocation(db, snapshot, catalogues=catalogue)
        stage_allocation(db, snapshot, catalogues=catalogue)  # replay

        events = (
            db.query(PlatformAuditEvent)
            .filter(PlatformAuditEvent.action == "entitlement_allocation.staged")
            .all()
        )
        assert len(events) == 1
        assert events[0].details["product_code"] == "dotmac-sub"
        assert events[0].details["entries"] == 2

    def test_the_audit_action_is_declared_on_the_manifest(self) -> None:
        """ADR-0008: a consumer may only reference a DECLARED member, and a
        declared member needs a real consumer. This is both directions at once."""
        from dotmac_entitlement_allocation import AUDIT_ACTION_STAGED, module

        assert AUDIT_ACTION_STAGED in module.audit_actions


class TestNoTrustedCallerEscape:
    def test_there_is_no_way_to_skip_validation(self) -> None:
        """No `validated=True`, no `skip_validation`, no `trusted`. Any of them
        would make the invariant optional, and an optional invariant is a
        comment."""
        import inspect

        parameters = set(inspect.signature(stage_allocation).parameters)
        assert parameters == {"db", "snapshot", "catalogues", "actor_admin_id"}

    def test_the_catalogue_is_required_not_defaulted(self) -> None:
        """A default would let a caller omit it and silently validate against
        nothing."""
        import inspect

        catalogues = inspect.signature(stage_allocation).parameters["catalogues"]
        assert catalogues.default is inspect.Parameter.empty
        assert catalogues.kind is inspect.Parameter.KEYWORD_ONLY
