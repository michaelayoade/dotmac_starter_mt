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
    UndeclaredCapabilityError,
    UnknownProductError,
    allocation_product,
    stage_allocation,
)
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
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
