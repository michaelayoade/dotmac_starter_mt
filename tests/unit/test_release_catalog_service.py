"""The write path routes through the validators, always.

`identity.py` can refuse a tag; that is only useful if every write goes through
it. These tests prove the service does, and that the models are not a second,
unvalidated entry point people will find by accident.

In-memory SQLite, so this is logic only — grants and the CHECK constraint are
proven against real Postgres in `tests/test_release_catalog_immutability.py`.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from dotmac_kernel.assembly import ProductAssemblySpec
from dotmac_kernel.models import Base
from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.product_database_catalog import (
    ComposedDatabaseLineageHeadV1,
    DatabaseCatalogOwnerKind,
    DatabaseCatalogOwnerV1,
    DatabaseColumnContractV1,
    DatabaseColumnGeneration,
    DatabasePersistencePlane,
    DatabaseRelationKind,
    DatabaseTableContractV1,
    HostDatabaseCatalogFragmentV1,
    ModuleDatabaseCatalogContributionV1,
    ModuleDatabaseCatalogSnapshot,
    ModuleDatabaseTableContractV1,
    PostgresTypeContractV1,
    PostgresTypeKind,
    ProductDatabaseCatalogSnapshot,
)
from dotmac_kernel.product_manifest import ProductManifestSnapshot
from dotmac_release_catalog import (
    ArtifactKind,
    AttestationKind,
    Digest,
    DigestError,
    DuplicateSingularAttestationError,
    ModuleDatabaseCatalogMismatchError,
    ProductDatabaseCatalogMismatchError,
    ProductManifestMismatchError,
    ReleaseArtifact,
    TypedAttestationRequiredError,
    UnknownArtifactError,
    UnpinnedReferenceError,
    attest_artifact,
    attest_module_database_catalog,
    attest_product_database_catalog,
    attest_product_manifest,
    publish_artifact,
)
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

_HEX = "a" * 64
_OTHER = "b" * 64
_DIGEST = f"sha256:{_HEX}"
_REF = f"registry.example.com/dotmac/app@{_DIGEST}"


@pytest.fixture
def db() -> Generator[Session, None, None]:
    """SQLite has no schemas, so `mod_rel` is attached as one.

    Without this the models' fully qualified names — which are the point on
    Postgres — simply fail to resolve here.
    """
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _attach(dbapi_connection, _record):
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS mod_rel")

    Base.metadata.create_all(
        engine,
        tables=[
            table
            for table in Base.metadata.tables.values()
            if table.schema == "mod_rel"
        ],
    )
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _publish(db: Session, **overrides: object) -> ReleaseArtifact:
    kwargs: dict[str, object] = {
        "product_code": "dotmac-sub",
        "version": "7.100.7",
        "artifact_kind": ArtifactKind.CONTAINER_IMAGE,
        "digest": _DIGEST,
        "artifact_ref": _REF,
    }
    kwargs.update(overrides)
    return publish_artifact(db, **kwargs)  # type: ignore[arg-type]


def _database_snapshot(
    *,
    product_code: str = "dotmac-sub",
    product_version: str = "7.100.7",
) -> ProductDatabaseCatalogSnapshot:
    owner = DatabaseCatalogOwnerV1(DatabaseCatalogOwnerKind.KERNEL, "kernel")
    table = DatabaseTableContractV1(
        schema="public",
        name="tenants",
        owner=owner,
        plane=DatabasePersistencePlane.HOST,
        relation_kind=DatabaseRelationKind.TABLE,
        columns=(
            DatabaseColumnContractV1(
                name="id",
                ordinal=1,
                postgres_type=PostgresTypeContractV1(
                    kind=PostgresTypeKind.BASE,
                    schema="pg_catalog",
                    name="uuid",
                    formatted="uuid",
                ),
                nullable=False,
                generation=DatabaseColumnGeneration.NONE,
            ),
        ),
    )
    return ProductDatabaseCatalogSnapshot.from_assembly(
        ProductAssemblySpec(name=product_code),
        product_version=product_version,
        postgres_major=16,
        host_fragments=(
            HostDatabaseCatalogFragmentV1(
                owner=owner,
                lineage_head="0034_example_kernel_head",
                tables=(table,),
            ),
            HostDatabaseCatalogFragmentV1(
                owner=DatabaseCatalogOwnerV1(
                    DatabaseCatalogOwnerKind.ASSEMBLY, product_code
                ),
                lineage_head="a999_catalog_fixture",
                tables=(
                    DatabaseTableContractV1(
                        schema="public",
                        name="assembly_contract_marker",
                        owner=DatabaseCatalogOwnerV1(
                            DatabaseCatalogOwnerKind.ASSEMBLY, product_code
                        ),
                        plane=DatabasePersistencePlane.HOST,
                        relation_kind=DatabaseRelationKind.TABLE,
                        columns=table.columns,
                    ),
                ),
            ),
        ),
        composed_lineage_heads=(
            ComposedDatabaseLineageHeadV1(
                DatabaseCatalogOwnerV1(DatabaseCatalogOwnerKind.ASSEMBLY, product_code),
                "a999_catalog_fixture",
            ),
            ComposedDatabaseLineageHeadV1(
                DatabaseCatalogOwnerV1(DatabaseCatalogOwnerKind.KERNEL, "kernel"),
                "0034_example_kernel_head",
            ),
        ),
    )


def _product_manifest(
    *,
    product_code: str = "dotmac-sub",
    product_version: str = "7.100.7",
) -> ProductManifestSnapshot:
    return ProductManifestSnapshot(
        product_code=product_code,
        product_version=product_version,
        capability_codes=("network.radius",),
    )


def _module_database_snapshot(
    *, distribution_name: str = "dotmac-sub", distribution_version: str = "7.100.7"
) -> ModuleDatabaseCatalogSnapshot:
    manifest = ModuleManifest(
        code="release_catalog_fixture",
        version="0.4.0",
        core=False,
        short_code="rel",
        migration_prefix="rl",
        migration_branch="release_catalog",
        platform_tables=("release_artifacts",),
        database_catalog=ModuleDatabaseCatalogContributionV1(
            lineage_head="rl_0002_singular_attestations",
            tables=(
                ModuleDatabaseTableContractV1(
                    name="release_artifacts",
                    relation_kind=DatabaseRelationKind.TABLE,
                    columns=_database_snapshot().fragments[1].tables[0].columns,
                ),
            ),
        ),
    )
    return ModuleDatabaseCatalogSnapshot.from_manifest(
        manifest,
        distribution_name=distribution_name,
        distribution_version=distribution_version,
        composed_lineage_head=ComposedDatabaseLineageHeadV1(
            DatabaseCatalogOwnerV1(
                DatabaseCatalogOwnerKind.MODULE, "release_catalog_fixture"
            ),
            "rl_0002_singular_attestations",
        ),
    )


class TestPublishValidatesEveryTime:
    def test_records_a_well_formed_artifact(self, db: Session) -> None:
        artifact = _publish(db)
        assert artifact.digest == _DIGEST
        assert artifact.artifact_ref == _REF
        assert artifact.artifact_kind == "container_image"

    def test_accepts_an_already_parsed_digest(self, db: Session) -> None:
        artifact = _publish(db, digest=Digest.parse(_DIGEST))
        assert artifact.digest == _DIGEST

    def test_refuses_a_tag_as_the_reference(self, db: Session) -> None:
        with pytest.raises(UnpinnedReferenceError):
            _publish(db, artifact_ref="registry.example.com/dotmac/app:latest")

    def test_refuses_a_reference_that_pins_other_bytes(self, db: Session) -> None:
        """The failure that survives every syntactic check: both values are
        individually valid, and together they address different artifacts."""
        with pytest.raises(UnpinnedReferenceError, match="same bytes"):
            _publish(db, artifact_ref=f"registry.example.com/app@sha256:{_OTHER}")

    def test_refuses_an_unacceptable_digest(self, db: Session) -> None:
        with pytest.raises(DigestError):
            _publish(db, digest=f"md5:{'a' * 32}", artifact_ref=f"r/x@md5:{'a' * 32}")

    def test_normalises_the_digest_through_the_value_object(self, db: Session) -> None:
        """Stored as `str(Digest)`, not as whatever the caller passed, so a
        whitespace-padded input cannot become a second row under the UNIQUE."""
        artifact = _publish(db, digest=f"  {_DIGEST}  ")
        assert artifact.digest == _DIGEST

    def test_nothing_is_committed(self, db: Session) -> None:
        """Hard rule 8: `dotmac_kernel.db` is the one transaction authority. A
        module that committed would take a decision belonging to the assembly's
        request or job boundary."""
        _publish(db)
        assert db.in_transaction()
        db.rollback()
        assert db.query(ReleaseArtifact).count() == 0


class TestAttest:
    def test_records_a_claim_about_a_published_artifact(self, db: Session) -> None:
        artifact = _publish(db)
        attestation = attest_artifact(
            db,
            artifact_id=artifact.id,
            attestation_kind=AttestationKind.SBOM,
            uri="https://example.com/sbom.json",
            digest=f"sha256:{_OTHER}",
        )
        assert attestation.attestation_kind == "sbom"
        assert attestation.digest == f"sha256:{_OTHER}"

    def test_generic_seam_refuses_a_product_manifest_digest(self, db: Session) -> None:
        artifact = _publish(db)
        with pytest.raises(TypedAttestationRequiredError, match="typed declaration"):
            attest_artifact(
                db,
                artifact_id=artifact.id,
                attestation_kind=AttestationKind.PRODUCT_MANIFEST,
                uri="https://example.com/product-manifest.json",
                digest=f"sha256:{_OTHER}",
            )

    def test_allows_multiple_signatures_for_one_artifact(self, db: Session) -> None:
        artifact = _publish(db)
        first = attest_artifact(
            db,
            artifact_id=artifact.id,
            attestation_kind=AttestationKind.SIGNATURE,
            uri="https://example.com/signature-a.json",
            digest=f"sha256:{_OTHER}",
        )
        second = attest_artifact(
            db,
            artifact_id=artifact.id,
            attestation_kind=AttestationKind.SIGNATURE,
            uri="https://example.com/signature-b.json",
            digest=_DIGEST,
        )

        assert first.id != second.id

    def test_refuses_to_attest_an_artifact_that_does_not_exist(
        self, db: Session
    ) -> None:
        import uuid

        with pytest.raises(UnknownArtifactError):
            attest_artifact(
                db,
                artifact_id=uuid.uuid4(),
                attestation_kind=AttestationKind.SIGNATURE,
                uri="https://example.com/sig",
                digest=_DIGEST,
            )

    def test_the_attestation_digest_is_validated_too(self, db: Session) -> None:
        """It is the digest OF THE DOCUMENT. Unvalidated, "the SBOM at this URI"
        is a mutable pointer by another route."""
        artifact = _publish(db)
        with pytest.raises(DigestError):
            attest_artifact(
                db,
                artifact_id=artifact.id,
                attestation_kind=AttestationKind.SBOM,
                uri="https://example.com/sbom.json",
                digest="not-a-digest",
            )

    def test_generic_seam_refuses_a_database_catalog_digest(self, db: Session) -> None:
        """A label plus opaque digest cannot stand in for inspected content."""
        artifact = _publish(db)

        with pytest.raises(TypedAttestationRequiredError, match="typed declaration"):
            attest_artifact(
                db,
                artifact_id=artifact.id,
                attestation_kind=AttestationKind.PRODUCT_DATABASE_CATALOG,
                uri="https://example.com/product-database-catalog.json",
                digest=f"sha256:{_OTHER}",
            )


class TestAttestProductManifest:
    def test_records_typed_content_and_derives_the_digest(self, db: Session) -> None:
        artifact = _publish(db)
        snapshot = _product_manifest()

        attestation = attest_product_manifest(
            db,
            artifact_id=artifact.id,
            uri="https://example.com/product-manifest.json",
            snapshot=snapshot,
        )

        assert attestation.attestation_kind == "product_manifest"
        assert attestation.digest == snapshot.digest

    def test_refuses_a_different_product_identity(self, db: Session) -> None:
        artifact = _publish(db)

        with pytest.raises(ProductManifestMismatchError, match="product_code"):
            attest_product_manifest(
                db,
                artifact_id=artifact.id,
                uri="https://example.com/product-manifest.json",
                snapshot=_product_manifest(product_code="dotmac-erp"),
            )

    def test_refuses_a_second_manifest_for_one_artifact(self, db: Session) -> None:
        artifact = _publish(db)
        snapshot = _product_manifest()
        attest_product_manifest(
            db,
            artifact_id=artifact.id,
            uri="https://example.com/product-manifest.json",
            snapshot=snapshot,
        )

        with pytest.raises(
            DuplicateSingularAttestationError, match="already has its singular"
        ):
            attest_product_manifest(
                db,
                artifact_id=artifact.id,
                uri="https://example.com/replacement-product-manifest.json",
                snapshot=snapshot,
            )


class TestAttestProductDatabaseCatalog:
    def test_records_the_typed_snapshot_and_derives_its_digest(
        self, db: Session
    ) -> None:
        artifact = _publish(db)
        snapshot = _database_snapshot()

        attestation = attest_product_database_catalog(
            db,
            artifact_id=artifact.id,
            uri="https://example.com/product-database-catalog.json",
            snapshot=snapshot,
        )

        assert attestation.attestation_kind == "product_database_catalog"
        assert attestation.digest == snapshot.digest

    @pytest.mark.parametrize(
        ("snapshot", "message"),
        [
            (_database_snapshot(product_code="dotmac-erp"), "product_code"),
            (_database_snapshot(product_version="7.100.8"), "product_version"),
        ],
    )
    def test_refuses_a_snapshot_for_a_different_artifact_identity(
        self,
        db: Session,
        snapshot: ProductDatabaseCatalogSnapshot,
        message: str,
    ) -> None:
        artifact = _publish(db)

        with pytest.raises(ProductDatabaseCatalogMismatchError, match=message):
            attest_product_database_catalog(
                db,
                artifact_id=artifact.id,
                uri="https://example.com/product-database-catalog.json",
                snapshot=snapshot,
            )


class TestAttestModuleDatabaseCatalog:
    def test_binds_distribution_identity_not_manifest_release(
        self, db: Session
    ) -> None:
        artifact = _publish(db)
        snapshot = _module_database_snapshot()

        attestation = attest_module_database_catalog(
            db,
            artifact_id=artifact.id,
            uri="https://example.com/module-database-catalog.json",
            snapshot=snapshot,
        )

        assert attestation.attestation_kind == "module_database_catalog"
        assert attestation.digest == snapshot.digest
        assert snapshot.module_release_version == "0.4.0"
        assert snapshot.distribution_version == artifact.version

    def test_refuses_a_different_distribution_version(self, db: Session) -> None:
        artifact = _publish(db)
        with pytest.raises(ModuleDatabaseCatalogMismatchError, match="version"):
            attest_module_database_catalog(
                db,
                artifact_id=artifact.id,
                uri="https://example.com/module-database-catalog.json",
                snapshot=_module_database_snapshot(distribution_version="7.100.8"),
            )

    def test_refuses_a_free_form_map(self, db: Session) -> None:
        artifact = _publish(db)

        with pytest.raises(TypeError, match="opaque maps"):
            attest_product_database_catalog(
                db,
                artifact_id=artifact.id,
                uri="https://example.com/product-database-catalog.json",
                snapshot={"product_code": "dotmac-sub"},  # type: ignore[arg-type]
            )

    def test_refuses_a_second_database_catalog_for_one_artifact(
        self, db: Session
    ) -> None:
        artifact = _publish(db)
        snapshot = _database_snapshot()
        attest_product_database_catalog(
            db,
            artifact_id=artifact.id,
            uri="https://example.com/product-database-catalog.json",
            snapshot=snapshot,
        )

        with pytest.raises(
            DuplicateSingularAttestationError, match="already has its singular"
        ):
            attest_product_database_catalog(
                db,
                artifact_id=artifact.id,
                uri="https://example.com/replacement-database-catalog.json",
                snapshot=snapshot,
            )


class TestThereIsNoUpdatePath:
    def test_the_module_exposes_no_update_or_delete_function(self) -> None:
        """Not "it exists and raises" — it does not exist.

        The online role holds no UPDATE privilege, so an update function would
        be an API promising something the database refuses. Correcting a
        published artifact is an offline `app_admin` migration under review.
        """
        import dotmac_release_catalog as module

        forbidden = {"update_artifact", "delete_artifact", "retract_artifact"}
        assert forbidden & set(module.__all__) == set()
        assert not any(hasattr(module, name) for name in forbidden)
