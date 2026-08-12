"""Live canaries: the catalogue check and the grants, against real Postgres.

The unit suite proves the service refuses. These prove the refusal leaves the
DATABASE untouched — a rollback that never happened, or an autoflush that wrote
before validation, would pass in-memory and fail here — and that immutability is
a privilege rather than a convention.

Applies the module's lineage standalone rather than through `alembic.ini`: the
starter assembly deliberately does not compose this vendor-only module, so
`mod_ealloc` is not in its migration chain. Running `ea_0001.upgrade()` directly
is how a vendor assembly will run it, and proves the lineage stands alone.

Requires real Postgres (`make test-db-up` / `make test-integration`).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
from dotmac_entitlement_allocation import (
    Allocation,
    ContractEntitlement,
    ContractSnapshot,
    IncompleteAllocationError,
    UndeclaredCapabilityError,
    UnknownProductError,
    allocation_product,
    stage_allocation,
)
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker


class LiveCatalogue:
    """A catalogue whose contents can change between calls, as a real one does."""

    def __init__(self, declared: dict[str, set[str]]) -> None:
        self.declared = declared

    def require_declared(self, *, product_code: str, capability_code: str) -> None:
        if product_code not in self.declared:
            raise UnknownProductError(f"unknown product {product_code!r}")
        if capability_code not in self.declared[product_code]:
            raise UndeclaredCapabilityError(product_code, (capability_code,))


def _session_for(env_var: str, label: str) -> Generator[Session, None, None]:
    url = os.getenv(env_var)
    if not url:
        pytest.skip(f"{env_var} not set — this canary requires a real {label} role")
    engine = create_engine(url, future=True)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
        engine.dispose()


@pytest.fixture
def platform_session() -> Generator[Session, None, None]:
    yield from _session_for("TEST_PLATFORM_DATABASE_URL", "platform_api")


@pytest.fixture
def app_user_session() -> Generator[Session, None, None]:
    yield from _session_for("TEST_DATABASE_URL", "app_user")


@pytest.fixture(scope="module")
def allocation_schema(admin_engine) -> Generator[None, None, None]:
    from dotmac_entitlement_allocation.migrations.versions import (
        ea_0001_allocations as lineage,
    )

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with admin_engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            lineage.upgrade()
    yield
    with admin_engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            lineage.downgrade()


@pytest.fixture
def catalogue() -> LiveCatalogue:
    return LiveCatalogue(
        {
            "dotmac-sub": {"billing.invoicing", "network.provisioning"},
            "dotmac-erp": {"finance.ledger"},
        }
    )


def _snapshot(**overrides: object) -> ContractSnapshot:
    fields: dict[str, object] = {
        "contract_ref": uuid.uuid4(),
        "product_code": "dotmac-sub",
        "customer_ref": "acme-isp",
        "content_hash": uuid.uuid4().hex * 2,
        "source_event_id": f"evt-{uuid.uuid4().hex[:8]}",
        "entries": (ContractEntitlement("billing.invoicing", 1),),
    }
    fields.update(overrides)
    return ContractSnapshot(**fields)  # type: ignore[arg-type]


class TestNothingIsWrittenWhenValidationFails:
    def test_an_unknown_product_writes_nothing(
        self,
        allocation_schema: None,
        platform_session: Session,
        catalogue: LiveCatalogue,
    ) -> None:
        """REQUIRED CANARY, against the real database. An autoflush before the
        catalogue check would pass in memory and leave a row here."""
        before = platform_session.query(Allocation).count()
        with pytest.raises(UnknownProductError):
            stage_allocation(
                platform_session,
                _snapshot(product_code="never-declared"),
                catalogues=catalogue,
            )
        platform_session.rollback()
        assert platform_session.query(Allocation).count() == before

    def test_one_undeclared_entry_rejects_the_complete_snapshot(
        self,
        allocation_schema: None,
        platform_session: Session,
        catalogue: LiveCatalogue,
    ) -> None:
        """REQUIRED CANARY. Two good codes and one bad one must leave zero rows,
        not two."""
        before = platform_session.query(Allocation).count()
        snapshot = _snapshot(
            entries=(
                ContractEntitlement("billing.invoicing"),
                ContractEntitlement("network.provisioning"),
                ContractEntitlement("not.declared.anywhere"),
            )
        )
        with pytest.raises(UndeclaredCapabilityError):
            stage_allocation(platform_session, snapshot, catalogues=catalogue)
        platform_session.rollback()
        assert platform_session.query(Allocation).count() == before

    def test_product_a_catalogue_cannot_authorize_product_b(
        self,
        allocation_schema: None,
        platform_session: Session,
        catalogue: LiveCatalogue,
    ) -> None:
        """REQUIRED CANARY. `finance.ledger` exists — under dotmac-erp."""
        snapshot = _snapshot(
            product_code="dotmac-sub",
            entries=(ContractEntitlement("finance.ledger"),),
        )
        with pytest.raises(UndeclaredCapabilityError):
            stage_allocation(platform_session, snapshot, catalogues=catalogue)
        platform_session.rollback()


class TestAStagedAllocationSurvivesACatalogueChange:
    def test_replay_still_works_after_the_capability_is_retired(
        self,
        allocation_schema: None,
        platform_session: Session,
        catalogue: LiveCatalogue,
    ) -> None:
        """REQUIRED CANARY. An allocation's legality was decided when it was
        staged. If a retired capability made an existing allocation unreplayable,
        an idempotent redelivery would become an outage for a customer whose
        entitlement was legitimately granted months earlier."""
        snapshot = _snapshot()
        first = stage_allocation(platform_session, snapshot, catalogues=catalogue)
        platform_session.commit()

        catalogue.declared["dotmac-sub"].discard("billing.invoicing")

        replay = stage_allocation(platform_session, snapshot, catalogues=catalogue)
        assert replay.replayed is True
        assert replay.id == first.id

        platform_session.rollback()

    def test_a_new_allocation_of_the_retired_capability_is_refused(
        self,
        allocation_schema: None,
        platform_session: Session,
        catalogue: LiveCatalogue,
    ) -> None:
        """Specificity for the canary above: replay is exempt, NEW staging is
        not. Without this, "don't re-validate on replay" could be implemented as
        "don't validate", and both tests would still pass."""
        catalogue.declared["dotmac-sub"].discard("billing.invoicing")
        with pytest.raises(UndeclaredCapabilityError):
            stage_allocation(platform_session, _snapshot(), catalogues=catalogue)
        platform_session.rollback()


class TestLicenceIssuanceReadsTheStoredProduct:
    def test_the_product_is_the_one_validated_not_one_supplied_later(
        self,
        allocation_schema: None,
        platform_session: Session,
        catalogue: LiveCatalogue,
    ) -> None:
        """REQUIRED CANARY. This is the relabelling path: without a stored
        product, an allocation validated against dotmac-sub could be issued as a
        dotmac-erp licence and every code would still resolve — in the wrong
        catalogue. `allocation_product` is the only supported way to answer
        "which product is this allocation for", and it takes no product
        argument, so there is nothing for a caller to override.
        """
        import inspect

        view = stage_allocation(platform_session, _snapshot(), catalogues=catalogue)
        platform_session.flush()

        assert allocation_product(platform_session, view.id) == "dotmac-sub"
        assert set(inspect.signature(allocation_product).parameters) == {
            "db",
            "allocation_id",
        }
        platform_session.rollback()


class TestAStagedAllocationCannotBeAppendedTo:
    """The hole an INSERT-only grant leaves on a CHILD table.

    `platform_api` needs INSERT on allocation_entries to stage at all, so the
    grant that makes the PARENT immutable leaves the child appendable: raw SQL
    could add a capability to an already-staged allocation and bypass catalogue
    validation entirely. The earlier canaries covered parent UPDATE and child
    DELETE and missed exactly this.
    """

    def test_platform_api_cannot_append_an_entry_after_staging(
        self,
        allocation_schema: None,
        platform_session: Session,
        catalogue: LiveCatalogue,
    ) -> None:
        """REQUIRED CANARY. The capability appended here was never validated."""
        view = stage_allocation(platform_session, _snapshot(), catalogues=catalogue)
        platform_session.commit()

        with pytest.raises((IntegrityError, DBAPIError), match="already staged"):
            platform_session.execute(
                text(
                    "INSERT INTO mod_ealloc.allocation_entries "
                    "(id, allocation_id, capability_code, quantity) "
                    "VALUES (gen_random_uuid(), :aid, 'never.validated', 1)"
                ),
                {"aid": view.id},
            )
        platform_session.rollback()

    def test_app_admin_cannot_append_either(
        self,
        allocation_schema: None,
        admin_session: Session,
        platform_session: Session,
        catalogue: LiveCatalogue,
    ) -> None:
        """The trigger deliberately does NOT exempt the offline role. A reviewed
        repair that must add an entry disables the trigger explicitly in its own
        migration, which leaves the exemption visible in a diff rather than
        baked into the trigger where nobody would look for it."""
        view = stage_allocation(platform_session, _snapshot(), catalogues=catalogue)
        platform_session.commit()

        with pytest.raises((IntegrityError, DBAPIError), match="already staged"):
            admin_session.execute(
                text(
                    "INSERT INTO mod_ealloc.allocation_entries "
                    "(id, allocation_id, capability_code, quantity) "
                    "VALUES (gen_random_uuid(), :aid, 'never.validated', 1)"
                ),
                {"aid": view.id},
            )
        admin_session.rollback()

    def test_staging_itself_still_works(
        self,
        allocation_schema: None,
        platform_session: Session,
        catalogue: LiveCatalogue,
    ) -> None:
        """Specificity: the trigger must block LATE inserts, not all inserts.
        Without this, "deny appends" could be implemented as "deny entries" and
        the canary above would still pass."""
        view = stage_allocation(platform_session, _snapshot(), catalogues=catalogue)
        assert view.entries
        platform_session.rollback()

    def test_the_seal_cannot_be_lifted(
        self,
        allocation_schema: None,
        platform_session: Session,
        catalogue: LiveCatalogue,
    ) -> None:
        """One-way. Without this, sealing could be undone and the allocation
        would be appendable again by anyone who could flip it back."""
        view = stage_allocation(platform_session, _snapshot(), catalogues=catalogue)
        platform_session.commit()
        with pytest.raises((IntegrityError, DBAPIError), match="cannot be lifted"):
            platform_session.execute(
                text("UPDATE mod_ealloc.allocations SET sealed = false WHERE id = :id"),
                {"id": view.id},
            )
        platform_session.rollback()

    def test_the_online_role_may_update_no_business_column(
        self,
        allocation_schema: None,
        platform_session: Session,
        catalogue: LiveCatalogue,
    ) -> None:
        """The column-level grant is what keeps every business column immutable
        while still letting the service seal. `updated_at` rides along with the
        seal as ORM metadata; nothing that carries meaning does."""
        view = stage_allocation(platform_session, _snapshot(), catalogues=catalogue)
        platform_session.commit()
        with pytest.raises((ProgrammingError, DBAPIError), match="permission denied"):
            platform_session.execute(
                text(
                    "UPDATE mod_ealloc.allocations SET customer_ref = 'x' "
                    "WHERE id = :id"
                ),
                {"id": view.id},
            )
        platform_session.rollback()

    def test_a_non_positive_quantity_is_refused_by_the_database(
        self,
        allocation_schema: None,
        admin_session: Session,
    ) -> None:
        """REQUIRED. The service checks it, but the service cannot police a path
        that never calls it."""
        allocation_id = uuid.uuid4()
        admin_session.execute(
            text(
                "INSERT INTO mod_ealloc.allocations (id, contract_ref, product_code,"
                " customer_ref, content_hash, status, source_event_id,"
                " snapshot_fingerprint) VALUES (:id, :cref, 'p', 'c', :h, 'staged',"
                " 'e', 'f')"
            ),
            {"id": allocation_id, "cref": uuid.uuid4(), "h": uuid.uuid4().hex},
        )
        with pytest.raises(IntegrityError, match="ck_allocation_entries_quantity"):
            admin_session.execute(
                text(
                    "INSERT INTO mod_ealloc.allocation_entries "
                    "(id, allocation_id, capability_code, quantity) "
                    "VALUES (gen_random_uuid(), :aid, 'x', 0)"
                ),
                {"aid": allocation_id},
            )
        admin_session.rollback()


class TestConcurrentStaging:
    def test_two_sessions_racing_one_activation_produce_one_allocation(
        self,
        allocation_schema: None,
        admin_session: Session,
        catalogue: LiveCatalogue,
    ) -> None:
        """REQUIRED CANARY. The check-then-insert path has a TOCTOU window: two
        deliveries of one activation under DIFFERENT event ids both read
        nothing, both insert, and the unique constraint decides. The loser must
        resolve to a replay of the winner, not surface an IntegrityError.
        """
        url = os.getenv("TEST_PLATFORM_DATABASE_URL")
        if not url:
            pytest.skip("TEST_PLATFORM_DATABASE_URL not set")
        engine = create_engine(url, future=True)
        factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        first, second = factory(), factory()
        contract, digest = uuid.uuid4(), uuid.uuid4().hex * 2
        try:
            # Unique per run: the kernel's `platform_idempotency_records` lives
            # in `public` and survives a `mod_ealloc` drop, so a fixed key would
            # collide with an earlier run's DIFFERENT request — which the
            # at-most-once owner would correctly report as a conflict, failing
            # this test for a reason that has nothing to do with the race.
            run = uuid.uuid4().hex[:8]
            a = _snapshot(
                contract_ref=contract,
                content_hash=digest,
                source_event_id=f"race-a-{run}",
            )
            b = _snapshot(
                contract_ref=contract,
                content_hash=digest,
                source_event_id=f"race-b-{run}",
            )
            # Both read before either writes — the race, made deterministic.
            assert (
                first.execute(
                    select(Allocation).where(Allocation.contract_ref == contract)
                ).scalar_one_or_none()
                is None
            )
            assert (
                second.execute(
                    select(Allocation).where(Allocation.contract_ref == contract)
                ).scalar_one_or_none()
                is None
            )

            winner = stage_allocation(first, a, catalogues=catalogue)
            first.commit()

            loser = stage_allocation(second, b, catalogues=catalogue)
            assert loser.replayed is True
            assert loser.id == winner.id
            second.rollback()

            count = (
                second.execute(
                    select(Allocation).where(Allocation.contract_ref == contract)
                )
                .scalars()
                .all()
            )
            assert len(count) == 1
        finally:
            first.rollback()
            second.rollback()
            for session in (first, second):
                session.close()
            engine.dispose()
            # Cleanup runs as the OFFLINE role: `platform_api` has no DELETE,
            # which is the property under test two classes down.
            admin_session.execute(
                text("DELETE FROM mod_ealloc.allocations WHERE contract_ref = :c"),
                {"c": contract},
            )
            admin_session.commit()


class TestEveryDeliveryIsRecorded:
    """A delivery key must never be spendable without being recorded.

    The hole: an activation replay that returned BEFORE reaching the
    at-most-once owner. Stage claim A under event-a and claim B under event-b,
    then replay claim A under event-b — because A already existed, the call
    succeeded and never discovered that event-b belongs to a different request.
    """

    def test_replaying_one_claim_under_another_claims_event_id_conflicts(
        self,
        allocation_schema: None,
        platform_session: Session,
        catalogue: LiveCatalogue,
    ) -> None:
        """REQUIRED CANARY. The exact counterexample."""
        from dotmac_kernel.idempotency import IdempotencyConflict

        run = uuid.uuid4().hex[:8]
        claim_a = _snapshot(source_event_id=f"event-a-{run}")
        claim_b = _snapshot(source_event_id=f"event-b-{run}")

        stage_allocation(platform_session, claim_a, catalogues=catalogue)
        stage_allocation(platform_session, claim_b, catalogues=catalogue)
        platform_session.commit()

        # Claim A again, but spending claim B's delivery key.
        replay_a_under_b = ContractSnapshot(
            contract_ref=claim_a.contract_ref,
            product_code=claim_a.product_code,
            customer_ref=claim_a.customer_ref,
            content_hash=claim_a.content_hash,
            source_event_id=claim_b.source_event_id,
            entries=claim_a.entries,
        )
        with pytest.raises(IdempotencyConflict):
            stage_allocation(platform_session, replay_a_under_b, catalogues=catalogue)
        platform_session.rollback()

    def test_an_honest_replay_records_its_own_delivery_key(
        self,
        allocation_schema: None,
        platform_session: Session,
        catalogue: LiveCatalogue,
    ) -> None:
        """A redelivery under a NEW event id is a legitimate replay — and it must
        still leave a ledger row, or the same key stays spendable for a
        different request afterwards."""
        from dotmac_kernel.idempotency_models import PlatformIdempotencyRecord

        run = uuid.uuid4().hex[:8]
        first = _snapshot(source_event_id=f"first-{run}")
        again = ContractSnapshot(
            contract_ref=first.contract_ref,
            product_code=first.product_code,
            customer_ref=first.customer_ref,
            content_hash=first.content_hash,
            source_event_id=f"second-{run}",
            entries=first.entries,
        )

        stage_allocation(platform_session, first, catalogues=catalogue)
        replay = stage_allocation(platform_session, again, catalogues=catalogue)
        assert replay.replayed is True
        platform_session.flush()

        keys = {
            row.key
            for row in platform_session.query(PlatformIdempotencyRecord)
            .filter(
                PlatformIdempotencyRecord.key.in_([f"first-{run}", f"second-{run}"])
            )
            .all()
        }
        assert keys == {f"first-{run}", f"second-{run}"}
        platform_session.rollback()


class TestAnUnsealedAllocationIsNotHistory:
    """A committed but unsealed row is an INCOMPLETE write, not a fact.

    It can only exist if something crashed between the parent insert and the
    seal, or if raw SQL wrote one. Either way it must never be replayed as
    history, and must never feed licence issuance — which would issue against
    an entitlement set nobody finished validating.
    """

    @pytest.fixture
    def unsealed(self, admin_session: Session) -> uuid.UUID:
        allocation_id = uuid.uuid4()
        admin_session.execute(
            text(
                "INSERT INTO mod_ealloc.allocations (id, contract_ref, product_code,"
                " customer_ref, content_hash, status, source_event_id,"
                " snapshot_fingerprint, sealed) VALUES (:id, :cref, 'dotmac-sub',"
                " 'acme-isp', :h, 'staged', 'orphan', 'deadbeef', false)"
            ),
            {"id": allocation_id, "cref": uuid.uuid4(), "h": uuid.uuid4().hex * 2},
        )
        admin_session.commit()
        yield allocation_id
        admin_session.execute(
            text("DELETE FROM mod_ealloc.allocations WHERE id = :id"),
            {"id": allocation_id},
        )
        admin_session.commit()

    def test_licence_issuance_cannot_read_an_unsealed_allocations_product(
        self,
        allocation_schema: None,
        platform_session: Session,
        unsealed: uuid.UUID,
    ) -> None:
        """REQUIRED CANARY. `allocation_product` is what licence issuance calls;
        answering for an unfinished allocation is how a licence gets issued
        against entitlements nobody validated."""
        with pytest.raises(IncompleteAllocationError):
            allocation_product(platform_session, unsealed)

    def test_an_unsealed_row_is_not_replayable_history(
        self,
        allocation_schema: None,
        platform_session: Session,
        catalogue: LiveCatalogue,
        unsealed: uuid.UUID,
    ) -> None:
        """REQUIRED CANARY. Staging the same activation must not quietly adopt
        the half-written row as a replay."""
        row = platform_session.execute(
            text(
                "SELECT contract_ref, content_hash FROM mod_ealloc.allocations "
                "WHERE id = :id"
            ),
            {"id": unsealed},
        ).one()
        snapshot = _snapshot(contract_ref=row[0], content_hash=row[1])
        with pytest.raises(IncompleteAllocationError):
            stage_allocation(platform_session, snapshot, catalogues=catalogue)
        platform_session.rollback()


class TestImmutabilityIsAPrivilege:
    def test_platform_api_cannot_update_a_staged_allocation(
        self,
        allocation_schema: None,
        platform_session: Session,
        catalogue: LiveCatalogue,
    ) -> None:
        view = stage_allocation(platform_session, _snapshot(), catalogues=catalogue)
        platform_session.commit()
        with pytest.raises((ProgrammingError, DBAPIError), match="permission denied"):
            platform_session.execute(
                text(
                    "UPDATE mod_ealloc.allocations "
                    "SET product_code = 'dotmac-erp' WHERE id = :id"
                ),
                {"id": view.id},
            )
        platform_session.rollback()

    def test_platform_api_cannot_delete_an_allocation_entry(
        self, allocation_schema: None, platform_session: Session
    ) -> None:
        with pytest.raises((ProgrammingError, DBAPIError), match="permission denied"):
            platform_session.execute(text("DELETE FROM mod_ealloc.allocation_entries"))

    @pytest.mark.parametrize("table", ["allocations", "allocation_entries"])
    def test_the_data_plane_cannot_read_allocations(
        self, allocation_schema: None, app_user_session: Session, table: str
    ) -> None:
        """Ruling C4: the data plane writes its OWN grants from a signed
        envelope, and never learns what it may write by reading the vendor."""
        probe = text(f"SELECT 1 FROM mod_ealloc.{table} LIMIT 1")  # noqa: S608
        with pytest.raises((ProgrammingError, DBAPIError), match="permission denied"):
            app_user_session.execute(probe)
