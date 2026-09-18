"""The write path routes through the validators, always.

`identity.py` can refuse a tag; that is only useful if every write goes through
it. These tests prove the service does, and that the models are not a second,
unvalidated entry point people will find by accident.

In-memory SQLite, so this is logic only — grants and the CHECK constraint are
proven against real Postgres in `tests/test_release_catalog_immutability.py`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Generator

import pytest
from dotmac_kernel.models import Base
from dotmac_kernel.product_database_catalog import (
    ProductDatabaseCatalogDigestMismatchError,
    ProductDatabaseCatalogError,
)
from dotmac_release_catalog import (
    ArtifactKind,
    AttestationKind,
    DatabaseCatalogArtifactMismatchError,
    Digest,
    DigestError,
    ReleaseArtifact,
    UnknownArtifactError,
    UnpinnedReferenceError,
    attest_artifact,
    attest_module_database_catalog,
    attest_product_database_catalog,
    publish_artifact,
)
from dotmac_release_catalog.models import ArtifactAttestation
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

_HEX = "a" * 64
_OTHER = "b" * 64
_DIGEST = f"sha256:{_HEX}"
_REF = f"registry.example.com/dotmac/app@{_DIGEST}"


def _canonical(document: dict[str, object]) -> bytes:
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _catalogue_table(
    *, owner_kind: str, owner_code: str, plane: str
) -> dict[str, object]:
    return {
        "schema": "mod_tst" if owner_kind == "module" else "public",
        "name": "records",
        "owner": {"kind": owner_kind, "code": owner_code},
        "plane": plane,
        "relation_kind": "table",
        "columns": [
            {
                "name": "id",
                "ordinal": 1,
                "postgres_type": {
                    "kind": "base",
                    "schema": "pg_catalog",
                    "name": "uuid",
                    "formatted": "uuid",
                },
                "nullable": False,
                "generation": "none",
                "expression": "",
                "collation": None,
            }
        ],
    }


def _module_catalogue_bytes(
    *,
    distribution_name: str = "dotmac-widget",
    distribution_version: str = "1.0.0",
    module_release_version: str = "1.0.0",
) -> bytes:
    return _canonical(
        {
            "schema": "dotmac.module-database-catalog/v1",
            "scope": "tables_and_columns",
            "distribution_name": distribution_name,
            "distribution_version": distribution_version,
            "module_code": "widget",
            "module_release_version": module_release_version,
            "manifest_contract_version": 1,
            "database_schema": "mod_tst",
            "lineage_head": "wi_0001_records",
            "tables": [
                _catalogue_table(
                    owner_kind="module", owner_code="widget", plane="platform"
                )
            ],
        }
    )


def _product_catalogue_bytes(
    *, product_code: str = "dotmac-sub", product_version: str = "7.100.7"
) -> bytes:
    return _canonical(
        {
            "schema": "dotmac.product-database-catalog/v1",
            "scope": "tables_and_columns",
            "product_code": product_code,
            "product_version": product_version,
            "postgres_major": 16,
            "complete_schemas": ["public"],
            "fragments": [
                {
                    "owner": {"kind": "assembly", "code": product_code},
                    "lineage_head": "as_0001_records",
                    "selected_planes": ["host"],
                    "tables": [
                        _catalogue_table(
                            owner_kind="assembly", owner_code=product_code, plane="host"
                        )
                    ],
                }
            ],
        }
    )


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

    def test_records_a_product_manifest_as_its_own_claim(self, db: Session) -> None:
        artifact = _publish(db)
        attestation = attest_artifact(
            db,
            artifact_id=artifact.id,
            attestation_kind=AttestationKind.PRODUCT_MANIFEST,
            uri="https://example.com/product-manifest.json",
            digest=f"sha256:{_OTHER}",
        )

        assert attestation.attestation_kind == "product_manifest"

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

    @pytest.mark.parametrize(
        "kind",
        (
            AttestationKind.MODULE_DATABASE_CATALOG,
            AttestationKind.PRODUCT_DATABASE_CATALOG,
        ),
    )
    def test_generic_writer_refuses_database_catalogue_kinds(
        self, db: Session, kind: AttestationKind
    ) -> None:
        artifact = _publish(db)
        with pytest.raises(ValueError, match="typed database-catalogue"):
            attest_artifact(
                db,
                artifact_id=artifact.id,
                attestation_kind=kind,
                uri="held://database-catalogue.json",
                digest=f"sha256:{_OTHER}",
            )


class TestDatabaseCatalogueAttestations:
    """Writers bind canonical snapshot evidence; they do not claim completeness.

    ``from_json_bytes`` proves the held document's canonical self-consistency
    and digest. A release path separately needs ``from_assembly`` evidence for
    control-plane completeness and independently held provenance tying artifact
    bytes to the schema declaration.
    """

    def test_module_writer_reverifies_bytes_and_binds_distribution_identity(
        self, db: Session
    ) -> None:
        artifact = _publish(
            db,
            product_code="dotmac-widget",
            version="1.0.0",
        )
        # The wheel version and the module's manifest release are separate
        # identities; the release artifact identifies the wheel bytes.
        snapshot = _module_catalogue_bytes(module_release_version="0.9.0")
        digest = "sha256:" + hashlib.sha256(snapshot).hexdigest()

        attestation = attest_module_database_catalog(
            db,
            artifact_id=artifact.id,
            snapshot_bytes=snapshot,
            expected_digest=digest,
            uri="held://module-database-catalogue.json",
        )

        assert attestation.attestation_kind == "module_database_catalog"
        assert attestation.digest == digest

    def test_product_writer_binds_product_identity_without_artifact_kind_branch(
        self, db: Session
    ) -> None:
        artifact = _publish(db, artifact_kind=ArtifactKind.OFFLINE_BUNDLE)
        snapshot = _product_catalogue_bytes()
        digest = "sha256:" + hashlib.sha256(snapshot).hexdigest()

        attestation = attest_product_database_catalog(
            db,
            artifact_id=artifact.id,
            snapshot_bytes=snapshot,
            expected_digest=digest,
            uri="held://product-database-catalogue.json",
        )

        assert attestation.attestation_kind == "product_database_catalog"
        assert attestation.digest == digest

    def test_writer_refuses_digest_that_does_not_match_the_held_bytes(
        self, db: Session
    ) -> None:
        artifact = _publish(db)
        with pytest.raises(ProductDatabaseCatalogDigestMismatchError):
            attest_product_database_catalog(
                db,
                artifact_id=artifact.id,
                snapshot_bytes=_product_catalogue_bytes(),
                expected_digest="sha256:" + "0" * 64,
                uri="held://product-database-catalogue.json",
            )

    def test_writer_refuses_a_snapshot_for_another_release(self, db: Session) -> None:
        artifact = _publish(db)
        snapshot = _module_catalogue_bytes()
        with pytest.raises(
            DatabaseCatalogArtifactMismatchError, match="does not identify"
        ):
            attest_module_database_catalog(
                db,
                artifact_id=artifact.id,
                snapshot_bytes=snapshot,
                expected_digest="sha256:" + hashlib.sha256(snapshot).hexdigest(),
                uri="held://module-database-catalogue.json",
            )

    def test_product_writer_refuses_a_snapshot_for_another_product_version(
        self, db: Session
    ) -> None:
        artifact = _publish(db)
        snapshot = _product_catalogue_bytes(product_version="7.100.8")
        with pytest.raises(
            DatabaseCatalogArtifactMismatchError, match="does not identify"
        ):
            attest_product_database_catalog(
                db,
                artifact_id=artifact.id,
                snapshot_bytes=snapshot,
                expected_digest="sha256:" + hashlib.sha256(snapshot).hexdigest(),
                uri="held://product-database-catalogue.json",
            )

    def test_product_writer_refuses_module_scope_bytes(self, db: Session) -> None:
        artifact = _publish(db)
        snapshot = _module_catalogue_bytes()
        with pytest.raises(ProductDatabaseCatalogError, match="fields differ"):
            attest_product_database_catalog(
                db,
                artifact_id=artifact.id,
                snapshot_bytes=snapshot,
                expected_digest="sha256:" + hashlib.sha256(snapshot).hexdigest(),
                uri="held://module-database-catalogue.json",
            )

    def test_module_writer_refuses_product_scope_bytes(self, db: Session) -> None:
        artifact = _publish(db)
        snapshot = _product_catalogue_bytes()
        with pytest.raises(ProductDatabaseCatalogError, match="fields differ"):
            attest_module_database_catalog(
                db,
                artifact_id=artifact.id,
                snapshot_bytes=snapshot,
                expected_digest="sha256:" + hashlib.sha256(snapshot).hexdigest(),
                uri="held://product-database-catalogue.json",
            )

    def test_writer_refuses_noncanonical_bytes_even_when_their_digest_matches(
        self, db: Session
    ) -> None:
        artifact = _publish(db)
        canonical = _product_catalogue_bytes()
        noncanonical = json.dumps(
            json.loads(canonical), indent=2, sort_keys=True
        ).encode("utf-8")
        with pytest.raises(ProductDatabaseCatalogError, match="not the canonical"):
            attest_product_database_catalog(
                db,
                artifact_id=artifact.id,
                snapshot_bytes=noncanonical,
                expected_digest="sha256:" + hashlib.sha256(noncanonical).hexdigest(),
                uri="held://product-database-catalogue.json",
            )


def test_database_catalogue_indexes_are_partial_and_singular() -> None:
    """SQLite metadata mirrors PostgreSQL's migration predicates for unit use."""
    indexes = {index.name: index for index in ArtifactAttestation.__table__.indexes}
    expected = {
        "uq_artifact_attestations_module_database_catalog": "module_database_catalog",
        "uq_artifact_attestations_product_database_catalog": "product_database_catalog",
    }
    for name, kind in expected.items():
        index = indexes[name]
        assert index.unique
        assert [column.name for column in index.columns] == ["artifact_id"]
        assert str(index.dialect_options["postgresql"]["where"]) == (
            f"attestation_kind = '{kind}'"
        )
        assert str(index.dialect_options["sqlite"]["where"]) == (
            f"attestation_kind = '{kind}'"
        )


@pytest.mark.parametrize(
    "kind",
    ("module_database_catalog", "product_database_catalog"),
)
def test_database_catalogue_partial_indexes_refuse_distinct_competing_claims(
    db: Session, kind: str
) -> None:
    """The DB, not a preflight query, closes the concurrent-writer race."""
    artifact = _publish(db)
    db.add_all(
        (
            ArtifactAttestation(
                artifact_id=artifact.id,
                attestation_kind=kind,
                uri="held://first.json",
                digest="sha256:" + "c" * 64,
            ),
            ArtifactAttestation(
                artifact_id=artifact.id,
                attestation_kind=kind,
                uri="held://second.json",
                digest="sha256:" + "d" * 64,
            ),
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


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
