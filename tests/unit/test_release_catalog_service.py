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
from dotmac_kernel.models import Base
from dotmac_release_catalog import (
    ArtifactKind,
    AttestationKind,
    Digest,
    DigestError,
    ReleaseArtifact,
    UnknownArtifactError,
    UnpinnedReferenceError,
    attest_artifact,
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
