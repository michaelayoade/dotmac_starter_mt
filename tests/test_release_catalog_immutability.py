"""Live-database canaries: a published artifact cannot be rewritten.

The module's docstrings claim immutability. A claim in a docstring is a comment.
These tests drive a real Postgres with the real roles and assert that the
database itself refuses, so the property survives a future router, a psql
session, and anyone who forgets `publish_artifact`.

Three independent mechanisms, tested independently because they fail
independently:

1. `platform_api` — the ONLINE request-path role — holds SELECT and INSERT only.
   No UPDATE, no DELETE. This is what makes "rows are never updated" true rather
   than merely intended.
2. `ck_release_artifacts_ref_pins_digest` proves `artifact_ref` ends in `@` plus
   the row's own `digest`, closing the raw-SQL path that bypasses
   `identity.pinned_reference`.
3. `app_user` — the tenant data-plane role — cannot reach the catalogue at all.

## Why this applies the lineage itself

The starter assembly deliberately does NOT compose this module — an
import-linter contract forbids `app` importing it, because it is
vendor-assembly-only, and the composed migration gate correctly refuses a
lineage whose owner is not in the assembly's composition. So `mod_rel` is not in
this repository's `alembic.ini` and `make test-db-up` does not create it.

The fixture therefore runs the Release Catalog lineage's own `upgrade()`
functions against the admin connection, which is exactly how a vendor assembly
will run it. That makes this
a stronger test than riding on the assembly's migration chain would have been:
it proves the lineage stands alone, which is the property a separate consuming
repository actually depends on.

Requires real Postgres (`make test-db-up` / `make test-integration`).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

_HEX = "c" * 64
_DIGEST = f"sha256:{_HEX}"
_REF = f"registry.example.com/dotmac/canary@{_DIGEST}"

_INSERT = text(
    """
    INSERT INTO mod_rel.release_artifacts
        (id, product_code, version, artifact_kind, digest, artifact_ref)
    VALUES (:id, :product, :version, 'container_image', :digest, :ref)
    """
)

_INSERT_ATTESTATION = text(
    """
    INSERT INTO mod_rel.artifact_attestations
        (id, artifact_id, attestation_kind, uri, digest)
    VALUES (:id, :artifact_id, :kind, :uri, :digest)
    """
)


def _session_for(env_var: str, label: str) -> Generator[Session, None, None]:
    url = os.getenv(env_var)
    if not url:
        pytest.skip(f"{env_var} not set — this canary requires a real {label} role")
    engine = create_engine(url, future=True)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = factory()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
        engine.dispose()


@pytest.fixture
def platform_session() -> Generator[Session, None, None]:
    """The ONLINE role. What a request handler actually holds."""
    yield from _session_for("TEST_PLATFORM_DATABASE_URL", "platform_api")


@pytest.fixture
def app_user_session() -> Generator[Session, None, None]:
    """The tenant data-plane role."""
    yield from _session_for("TEST_DATABASE_URL", "app_user")


@pytest.fixture(scope="module")
def catalogue_schema(admin_engine) -> Generator[None, None, None]:
    """Apply the module's own lineage, standalone, as a vendor assembly would."""
    from dotmac_release_catalog.migrations.versions import (  # type: ignore[import-not-found]
        rl_0001_release_artifacts as lineage,
    )
    from dotmac_release_catalog.migrations.versions import (  # type: ignore[import-not-found]
        rl_0002_singular_attestations as singular_attestations,
    )

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with admin_engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            lineage.upgrade()
            singular_attestations.upgrade()
    yield
    with admin_engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            singular_attestations.downgrade()
            lineage.downgrade()


@pytest.fixture
def published(
    catalogue_schema: None, admin_session: Session
) -> Generator[uuid.UUID, None, None]:
    """One artifact, inserted by the offline role, rolled back afterwards."""
    artifact_id = uuid.uuid4()
    admin_session.execute(
        _INSERT,
        {
            "id": artifact_id,
            "product": f"canary-{artifact_id.hex[:8]}",
            "version": "1.0.0",
            "digest": _DIGEST,
            "ref": _REF,
        },
    )
    admin_session.commit()
    yield artifact_id
    admin_session.execute(
        text("DELETE FROM mod_rel.release_artifacts WHERE id = :id"),
        {"id": artifact_id},
    )
    admin_session.commit()


class TestTheOnlineRoleCannotRewriteHistory:
    def test_platform_api_may_insert(
        self, catalogue_schema: None, platform_session: Session
    ) -> None:
        """The privilege it MUST have — otherwise the tests below prove nothing
        except that the role is broken."""
        artifact_id = uuid.uuid4()
        platform_session.execute(
            _INSERT,
            {
                "id": artifact_id,
                "product": f"canary-{artifact_id.hex[:8]}",
                "version": "1.0.0",
                "digest": f"sha256:{'d' * 64}",
                "ref": f"registry.example.com/x@sha256:{'d' * 64}",
            },
        )
        platform_session.rollback()

    def test_platform_api_cannot_update_a_published_artifact(
        self, platform_session: Session, published: uuid.UUID
    ) -> None:
        """The core immutability guarantee. Not a service convention — a
        privilege the online role does not hold."""
        with pytest.raises((ProgrammingError, DBAPIError), match="permission denied"):
            platform_session.execute(
                text(
                    "UPDATE mod_rel.release_artifacts "
                    "SET version = '9.9.9' WHERE id = :id"
                ),
                {"id": published},
            )

    def test_platform_api_cannot_delete_a_published_artifact(
        self, platform_session: Session, published: uuid.UUID
    ) -> None:
        with pytest.raises((ProgrammingError, DBAPIError), match="permission denied"):
            platform_session.execute(
                text("DELETE FROM mod_rel.release_artifacts WHERE id = :id"),
                {"id": published},
            )

    def test_platform_api_cannot_rewrite_an_attestation(
        self, catalogue_schema: None, platform_session: Session
    ) -> None:
        """Same rule for the claims. An attestation that can be repointed is a
        signature that proves nothing."""
        with pytest.raises((ProgrammingError, DBAPIError), match="permission denied"):
            platform_session.execute(
                text("UPDATE mod_rel.artifact_attestations SET uri = 'x'")
            )


class TestTheOfflineRoleCanStillRepair:
    def test_app_admin_may_update(
        self, admin_session: Session, published: uuid.UUID
    ) -> None:
        """Immutability must not mean unrepairable.

        A mis-recorded artifact, or a legally required erasure, has to be
        possible by SOMEONE. Confining it to the role that already runs reviewed
        migrations is what makes it a deliberate act rather than an accident
        during a request.
        """
        admin_session.execute(
            text(
                "UPDATE mod_rel.release_artifacts "
                "SET source_revision = 'corrected' WHERE id = :id"
            ),
            {"id": published},
        )
        admin_session.rollback()


class TestTheReferenceCannotDriftFromTheDigest:
    def test_raw_sql_cannot_insert_a_ref_that_pins_other_bytes(
        self, catalogue_schema: None, admin_session: Session
    ) -> None:
        """The failure that passes every syntactic check and still deploys the
        wrong artifact — and the one `publish_artifact` cannot prevent when
        nobody calls it."""
        constraint = "ck_release_artifacts_ref_pins_digest"
        with pytest.raises(IntegrityError, match=constraint):
            admin_session.execute(
                _INSERT,
                {
                    "id": uuid.uuid4(),
                    "product": "canary-drift",
                    "version": "1.0.0",
                    "digest": _DIGEST,
                    "ref": f"registry.example.com/x@sha256:{'e' * 64}",
                },
            )
        admin_session.rollback()

    def test_raw_sql_cannot_insert_a_tag_as_a_reference(
        self, catalogue_schema: None, admin_session: Session
    ) -> None:
        constraint = "ck_release_artifacts_ref_pins_digest"
        with pytest.raises(IntegrityError, match=constraint):
            admin_session.execute(
                _INSERT,
                {
                    "id": uuid.uuid4(),
                    "product": "canary-tag",
                    "version": "1.0.0",
                    "digest": _DIGEST,
                    "ref": "registry.example.com/dotmac/app:latest",
                },
            )
        admin_session.rollback()

    def test_the_constraint_does_not_close_the_algorithm_vocabulary(
        self, catalogue_schema: None, admin_session: Session
    ) -> None:
        """Specificity proof.

        The CHECK is deliberately WEAKER than `identity.Digest` — it compares the
        two columns and says nothing about the algorithm. That is what keeps a
        future second algorithm a module release rather than an ALTER TABLE on
        every deployment, and this test fails if someone 'strengthens' the
        constraint into a regex on sha256.
        """
        future = f"sha512:{'f' * 128}"
        admin_session.execute(
            _INSERT,
            {
                "id": uuid.uuid4(),
                "product": "canary-future-alg",
                "version": "1.0.0",
                "digest": future,
                "ref": f"registry.example.com/x@{future}",
            },
        )
        admin_session.rollback()


class TestDeclarationAttestationCardinality:
    def test_raw_sql_cannot_record_two_product_catalogues(
        self, admin_session: Session, published: uuid.UUID
    ) -> None:
        values = {
            "artifact_id": published,
            "kind": "product_database_catalog",
            "uri": "https://example.com/catalog-a.json",
            "digest": f"sha256:{'d' * 64}",
        }
        admin_session.execute(
            _INSERT_ATTESTATION,
            {"id": uuid.uuid4(), **values},
        )

        with pytest.raises(
            IntegrityError, match="uq_artifact_attestations_singular_kind"
        ):
            admin_session.execute(
                _INSERT_ATTESTATION,
                {
                    "id": uuid.uuid4(),
                    **values,
                    "uri": "https://example.com/catalog-b.json",
                    "digest": f"sha256:{'e' * 64}",
                },
            )
        admin_session.rollback()

    def test_raw_sql_may_record_multiple_signatures(
        self, admin_session: Session, published: uuid.UUID
    ) -> None:
        for suffix, character in (("a", "d"), ("b", "e")):
            admin_session.execute(
                _INSERT_ATTESTATION,
                {
                    "id": uuid.uuid4(),
                    "artifact_id": published,
                    "kind": "signature",
                    "uri": f"https://example.com/signature-{suffix}.json",
                    "digest": f"sha256:{character * 64}",
                },
            )
        admin_session.rollback()


class TestTheDataPlaneCannotSeeTheCatalogueAtAll:
    @pytest.mark.parametrize("table", ["release_artifacts", "artifact_attestations"])
    def test_app_user_cannot_read(
        self, catalogue_schema: None, app_user_session: Session, table: str
    ) -> None:
        """A data plane learns which artifact to run from a signed licence or a
        deployment plan — never by querying the vendor's catalogue."""
        # `table` is parametrized from a literal list in this file, not input.
        probe = text(f"SELECT 1 FROM mod_rel.{table} LIMIT 1")  # noqa: S608
        with pytest.raises((ProgrammingError, DBAPIError), match="permission denied"):
            app_user_session.execute(probe)
