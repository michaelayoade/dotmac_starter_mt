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

The fixture therefore runs the `rl` lineage's own upgrades against the admin
connection, which is exactly how a vendor assembly will run it. That makes this
a stronger test than riding on the assembly's migration chain would have been:
it proves the lineage stands alone, which is the property a separate consuming
repository actually depends on.

Requires real Postgres (`make test-db-up` / `make test-integration`).
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

_HEX = "c" * 64
_DIGEST = f"sha256:{_HEX}"
_REF = f"registry.example.com/dotmac/canary@{_DIGEST}"
_ORIGIN_CONSTRAINT = "ck_artifact_attestations_origin_kind"

_INSERT = text(
    """
    INSERT INTO mod_rel.release_artifacts
        (id, product_code, version, artifact_kind, origin_class, digest, artifact_ref)
    VALUES (:id, :product, :version, 'container_image',
            :origin, :digest, :ref)
    """
)


def _assert_origin_constraint(error: IntegrityError) -> None:
    """Assert the Postgres diagnostic, not a driver's rendered message.

    psycopg exposes the constraint name on ``diag.constraint_name`` but does
    not promise to repeat it in ``str(error)``.  Matching prose would make the
    canary fail while the database was enforcing the exact intended rule.
    """

    diagnostic = getattr(error.orig, "diag", None)
    assert getattr(diagnostic, "constraint_name", None) == _ORIGIN_CONSTRAINT


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
        rl_0001_release_artifacts as root,
    )
    from dotmac_release_catalog.migrations.versions import (
        rl_0002_artifact_origin as origin,
    )

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with admin_engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            root.upgrade()
            origin.upgrade()
    yield
    with admin_engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            origin.downgrade()
            root.downgrade()


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
            "origin": "dotmac_product",
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
                "origin": "dotmac_product",
                "digest": f"sha256:{'d' * 64}",
                "ref": f"registry.example.com/x@sha256:{'d' * 64}",
            },
        )
        platform_session.rollback()

    def test_platform_api_may_attach_origin_compatible_evidence(
        self, catalogue_schema: None, platform_session: Session
    ) -> None:
        """The origin trigger must not accidentally demand UPDATE privilege."""
        artifact_id = uuid.uuid4()
        platform_session.execute(
            _INSERT,
            {
                "id": artifact_id,
                "product": f"evidence-{artifact_id.hex[:8]}",
                "version": "1.0.0",
                "origin": "dotmac_product",
                "digest": f"sha256:{'e' * 64}",
                "ref": f"registry.example.com/x@sha256:{'e' * 64}",
            },
        )
        platform_session.execute(
            text(
                "INSERT INTO mod_rel.artifact_attestations "
                "(id, artifact_id, attestation_kind, uri, digest) "
                "VALUES (:id, :artifact_id, 'capability_contract', "
                "'https://evidence.example/contract.json', :digest)"
            ),
            {
                "id": uuid.uuid4(),
                "artifact_id": artifact_id,
                "digest": f"sha256:{'f' * 64}",
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


class TestArtifactOriginOwnsTheEvidenceRegime:
    def _insert_artifact(
        self, admin_session: Session, *, origin: str, digest_hex: str
    ) -> uuid.UUID:
        artifact_id = uuid.uuid4()
        digest = f"sha256:{digest_hex * 64}"
        admin_session.execute(
            _INSERT,
            {
                "id": artifact_id,
                "product": f"origin-{artifact_id.hex[:8]}",
                "version": "1.0.0",
                "origin": origin,
                "digest": digest,
                "ref": f"registry.example.com/origin@{digest}",
            },
        )
        return artifact_id

    def _insert_attestation(
        self, admin_session: Session, *, artifact_id: uuid.UUID, kind: str
    ) -> None:
        admin_session.execute(
            text(
                "INSERT INTO mod_rel.artifact_attestations "
                "(id, artifact_id, attestation_kind, uri, digest) "
                "VALUES (:id, :artifact_id, :kind, :uri, :digest)"
            ),
            {
                "id": uuid.uuid4(),
                "artifact_id": artifact_id,
                "kind": kind,
                "uri": "https://evidence.example/result.json",
                "digest": f"sha256:{'9' * 64}",
            },
        )

    def test_raw_sql_refuses_a_product_manifest_on_upstream_bytes(
        self, catalogue_schema: None, admin_session: Session
    ) -> None:
        artifact_id = self._insert_artifact(
            admin_session, origin="upstream_third_party", digest_hex="7"
        )
        with pytest.raises(IntegrityError) as raised:
            self._insert_attestation(
                admin_session,
                artifact_id=artifact_id,
                kind="product_manifest",
            )
        _assert_origin_constraint(raised.value)
        admin_session.rollback()

    def test_raw_sql_refuses_a_capability_contract_on_upstream_bytes(
        self, catalogue_schema: None, admin_session: Session
    ) -> None:
        artifact_id = self._insert_artifact(
            admin_session, origin="upstream_third_party", digest_hex="4"
        )
        with pytest.raises(IntegrityError) as raised:
            self._insert_attestation(
                admin_session,
                artifact_id=artifact_id,
                kind="capability_contract",
            )
        _assert_origin_constraint(raised.value)
        admin_session.rollback()

    def test_raw_sql_refuses_capability_schema_bytes_on_upstream_artifact(
        self, catalogue_schema: None, admin_session: Session
    ) -> None:
        """Schema bytes are product-owned evidence, not an upstream claim.

        The service test alone would leave a raw-SQL publisher able to attach
        an arbitrary request-carried schema to Mailcow or Nextcloud bytes.
        """
        artifact_id = self._insert_artifact(
            admin_session, origin="upstream_third_party", digest_hex="a"
        )
        with pytest.raises(IntegrityError) as raised:
            self._insert_attestation(
                admin_session,
                artifact_id=artifact_id,
                kind="capability_schema",
            )
        _assert_origin_constraint(raised.value)
        admin_session.rollback()

    def test_raw_sql_refuses_capability_composition_on_upstream_artifact(
        self, catalogue_schema: None, admin_session: Session
    ) -> None:
        artifact_id = self._insert_artifact(
            admin_session, origin="upstream_third_party", digest_hex="b"
        )
        with pytest.raises(IntegrityError) as raised:
            self._insert_attestation(
                admin_session,
                artifact_id=artifact_id,
                kind="capability_composition",
            )
        _assert_origin_constraint(raised.value)
        admin_session.rollback()

    def test_raw_sql_refuses_upstream_admission_on_dotmac_bytes(
        self, catalogue_schema: None, admin_session: Session
    ) -> None:
        artifact_id = self._insert_artifact(
            admin_session, origin="dotmac_product", digest_hex="8"
        )
        with pytest.raises(IntegrityError) as raised:
            self._insert_attestation(
                admin_session,
                artifact_id=artifact_id,
                kind="vulnerability_policy_result",
            )
        _assert_origin_constraint(raised.value)
        admin_session.rollback()

    def test_upstream_policy_and_compatibility_results_are_admissible(
        self, catalogue_schema: None, admin_session: Session
    ) -> None:
        artifact_id = self._insert_artifact(
            admin_session, origin="upstream_third_party", digest_hex="6"
        )
        self._insert_attestation(
            admin_session,
            artifact_id=artifact_id,
            kind="vulnerability_policy_result",
        )
        self._insert_attestation(
            admin_session,
            artifact_id=artifact_id,
            kind="compatibility_result",
        )
        admin_session.rollback()

    def test_origin_cannot_be_changed_around_a_product_manifest(
        self, catalogue_schema: None, admin_session: Session
    ) -> None:
        artifact_id = self._insert_artifact(
            admin_session, origin="dotmac_product", digest_hex="5"
        )
        self._insert_attestation(
            admin_session,
            artifact_id=artifact_id,
            kind="product_manifest",
        )
        with pytest.raises(IntegrityError) as raised:
            admin_session.execute(
                text(
                    "UPDATE mod_rel.release_artifacts "
                    "SET origin_class = 'upstream_third_party' WHERE id = :id"
                ),
                {"id": artifact_id},
            )
        _assert_origin_constraint(raised.value)
        admin_session.rollback()

    def test_origin_cannot_be_changed_around_a_capability_contract(
        self, catalogue_schema: None, admin_session: Session
    ) -> None:
        artifact_id = self._insert_artifact(
            admin_session, origin="dotmac_product", digest_hex="3"
        )
        self._insert_attestation(
            admin_session,
            artifact_id=artifact_id,
            kind="capability_contract",
        )
        with pytest.raises(IntegrityError) as raised:
            admin_session.execute(
                text(
                    "UPDATE mod_rel.release_artifacts "
                    "SET origin_class = 'upstream_third_party' WHERE id = :id"
                ),
                {"id": artifact_id},
            )
        _assert_origin_constraint(raised.value)
        admin_session.rollback()

    def test_concurrent_origin_change_and_attestation_serialize_to_one_refusal(
        self, catalogue_schema: None, admin_engine
    ) -> None:
        """Both transactions must not validate against the other's old view.

        The after-statement barrier is a sensitivity proof for the missing-lock
        implementation: without the parent-row lock, both statements reach it
        and both commit. With the lock, the winner times out of the barrier and
        commits; the loser resumes, observes the committed opposing fact, and
        is refused by the named constraint.
        """

        factory = sessionmaker(bind=admin_engine, autocommit=False, autoflush=False)
        artifact_id = uuid.uuid4()
        with factory() as setup:
            setup.execute(
                _INSERT,
                {
                    "id": artifact_id,
                    "product": f"race-{artifact_id.hex[:8]}",
                    "version": "1.0.0",
                    "origin": "dotmac_product",
                    "digest": f"sha256:{'2' * 64}",
                    "ref": f"registry.example.com/race@sha256:{'2' * 64}",
                },
            )
            setup.commit()

        start = threading.Barrier(2)
        after_statement = threading.Barrier(2)

        def attempt(kind: str) -> str:
            with factory() as db:
                db.execute(text("SET LOCAL lock_timeout = '10s'"))
                db.execute(text("SET LOCAL statement_timeout = '15s'"))
                start.wait(timeout=10)
                try:
                    if kind == "origin":
                        db.execute(
                            text(
                                "UPDATE mod_rel.release_artifacts SET "
                                "origin_class = 'upstream_third_party' "
                                "WHERE id = :id"
                            ),
                            {"id": artifact_id},
                        )
                    else:
                        self._insert_attestation(
                            db,
                            artifact_id=artifact_id,
                            kind="capability_contract",
                        )
                    try:
                        after_statement.wait(timeout=1)
                    except threading.BrokenBarrierError:
                        pass
                    db.commit()
                except IntegrityError as exc:
                    _assert_origin_constraint(exc)
                    db.rollback()
                    return "refused"
                return "committed"

        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = sorted(workers.map(attempt, ("origin", "attestation")))

        assert outcomes == ["committed", "refused"]
        with factory() as verify:
            origin = verify.scalar(
                text(
                    "SELECT origin_class FROM mod_rel.release_artifacts "
                    "WHERE id = :id"
                ),
                {"id": artifact_id},
            )
            kinds = tuple(
                verify.scalars(
                    text(
                        "SELECT attestation_kind FROM mod_rel.artifact_attestations "
                        "WHERE artifact_id = :id ORDER BY attestation_kind"
                    ),
                    {"id": artifact_id},
                )
            )
        assert (origin, kinds) in {
            ("dotmac_product", ("capability_contract",)),
            ("upstream_third_party", ()),
        }

    def test_new_raw_sql_artifact_must_state_a_valid_origin(
        self, catalogue_schema: None, admin_session: Session
    ) -> None:
        with pytest.raises(IntegrityError):
            admin_session.execute(
                text(
                    "INSERT INTO mod_rel.release_artifacts "
                    "(id, product_code, version, artifact_kind, digest, artifact_ref) "
                    "VALUES (:id, 'missing-origin', '1', 'container_image', "
                    ":digest, :ref)"
                ),
                {"id": uuid.uuid4(), "digest": _DIGEST, "ref": _REF},
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
                    "origin": "dotmac_product",
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
                    "origin": "dotmac_product",
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
                "origin": "dotmac_product",
                "digest": future,
                "ref": f"registry.example.com/x@{future}",
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
