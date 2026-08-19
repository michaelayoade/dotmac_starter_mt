"""Issuance signs once, verifies before recording, and never issues twice.

The invariant this file protects: **a recorded issuance is one the pinned kernel
verifier accepts, and one allocation produces exactly one of them.** A suite that
only asserted "a row appeared" would pass against an implementation that signed
a payload it then re-serialised, or that minted a second version every time an
at-least-once transport redelivered the same activation.

The fake signer here is a REAL Ed25519 keypair from `cryptography` — which this
package deliberately does not depend on, and the test suite legitimately does.
A stub that returned constant bytes would make every verification assertion below
vacuous: the round-trip check is the thing under test, and it can only be tested
against signatures that actually verify.

In-memory SQLite — logic only. Grants, the append-only triggers, the raw-SQL
constraints and migration-from-empty are proven against real Postgres in
`tests/test_licensing_platform_isolation.py`.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
from dotmac_kernel.models import Base
from dotmac_licensing import (
    EmptyGrantError,
    IssuanceStatus,
    IssueCommand,
    LicenceIssuance,
    LicensableGrant,
    LicensedCapability,
    SignerRefusedError,
    UnverifiableIssuanceError,
    build_keyring,
    current_issuance,
    inspect_issued_envelope,
    issue_licence,
    licence_view,
    module,
)
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class FakeSigner:
    """A real Ed25519 keypair, in memory, for the duration of one test.

    Real rather than a stub because the round-trip verification is the property
    under test: a signer returning constant bytes would make every `verify`
    assertion pass for the wrong reason, and would let a regression that stopped
    signing the right payload go unnoticed.
    """

    def __init__(self, key_id: str = "test-key-1") -> None:
        self._private = Ed25519PrivateKey.generate()
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def public_key_b64(self) -> str:
        raw = self._private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def sign(self, payload: bytes) -> bytes:
        return self._private.sign(payload)


class WrongPayloadSigner(FakeSigner):
    """Signs something OTHER than what it was handed.

    Exists to prove the round-trip check is load-bearing. Without it, the only
    evidence that `verify_licence` is called at all would be that it does not
    crash — which is also true of an implementation that never calls it.
    """

    def sign(self, payload: bytes) -> bytes:
        return self._private.sign(payload + b"tampered")


@pytest.fixture(autouse=True)
def _installed_module_audit_actions() -> None:
    install_audit_actions(AuditActionRegistry.from_manifests([module]))


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _attach(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        # pysqlite does not emit BEGIN on its own, which leaves SAVEPOINT
        # semantics broken — and every command runs inside one, via the kernel's
        # at-most-once owner.
        dbapi_connection.isolation_level = None
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS mod_licensing")

    @event.listens_for(engine, "begin")
    def _emit_begin(connection):  # type: ignore[no-untyped-def]
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(
        engine,
        tables=[
            table
            for table in Base.metadata.tables.values()
            if table.schema == "mod_licensing"
            or table.name
            in {
                "platform_idempotency_records",
                "platform_audit_events",
                "platform_admins",
                "platform_outbox_events",
            }
        ],
    )
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def signer() -> FakeSigner:
    return FakeSigner()


def _grant(**overrides: object) -> LicensableGrant:
    fields: dict[str, object] = {
        "subject_ref": "acme-operator",
        "product_code": "dotmac_sub",
        "capabilities": (LicensedCapability("subscriber.manage", {"quantity": 500}),),
        "agreement_ref": f"agr-{uuid.uuid4().hex[:10]}",
        "allocation_ref": f"alloc-{uuid.uuid4().hex[:10]}",
        "valid_until": _NOW + timedelta(days=365),
        "grace_days": 14,
    }
    fields.update(overrides)
    return LicensableGrant(**fields)  # type: ignore[arg-type]


def _issue(db: Session, signer: FakeSigner, **overrides: object):
    return issue_licence(
        db,
        IssueCommand(
            command_id=f"cmd-{uuid.uuid4().hex[:12]}", grant=_grant(**overrides)
        ),
        signers=(signer,),
        now=_NOW,
    )


def _payload_of(envelope: dict[str, object]) -> dict[str, object]:
    raw = str(envelope["payload_b64"])
    return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


# ── The happy path, asserted on what it produced ────────────────────────────


class TestIssuanceProducesAVerifiableDocument:
    def test_the_envelope_verifies_against_this_issuers_own_keyring(
        self, db, signer
    ) -> None:
        """The property the whole module rests on, asserted directly."""
        view = _issue(db, signer)
        result = inspect_issued_envelope(db, view.envelope, now=_NOW)
        assert result.valid, f"{result.reason}: {result.detail}"
        assert result.licence_id == str(view.licence_id)
        assert result.licence_version == 1
        assert result.digest == view.digest

    def test_the_public_key_is_registered_before_it_is_used(self, db, signer) -> None:
        """Registering after signing would produce a document the issuer's own
        round-trip check could not verify — the failure would look like a
        crypto bug rather than an ordering one."""
        _issue(db, signer)
        keyring = build_keyring(db)
        assert keyring.get(signer.key_id) is not None

    def test_the_payload_carries_the_capabilities_and_limits_verbatim(
        self, db, signer
    ) -> None:
        view = _issue(
            db,
            signer,
            capabilities=(
                LicensedCapability("subscriber.manage", {"quantity": 500}),
                LicensedCapability("billing.invoicing", {"seats": 3}),
            ),
        )
        document = _payload_of(dict(view.envelope))
        assert document["capabilities"] == [
            {"code": "subscriber.manage", "limits": {"quantity": 500}},
            {"code": "billing.invoicing", "limits": {"seats": 3}},
        ]

    def test_an_unbound_licence_omits_the_deployment_key_entirely(
        self, db, signer
    ) -> None:
        """An ABSENT key is a portable licence; a null one would be a bound
        licence with no target. The distinction is contractual."""
        view = _issue(db, signer, deployment_ref=None)
        assert "deployment_id" not in _payload_of(dict(view.envelope))["subject"]  # type: ignore[index]

    def test_a_bound_licence_carries_its_deployment(self, db, signer) -> None:
        view = _issue(db, signer, deployment_ref="acme-lagos-1")
        subject = _payload_of(dict(view.envelope))["subject"]
        assert subject["deployment_id"] == "acme-lagos-1"  # type: ignore[index]
        assert view.deployment_ref == "acme-lagos-1"

    def test_the_stored_digest_matches_the_payload_that_was_signed(
        self, db, signer
    ) -> None:
        """Serialising twice — once to sign, once to store — is how a digest and
        a signature come to describe different documents, and the failure only
        shows up at a receiver."""
        from dotmac_kernel.licensing import payload_digest

        view = _issue(db, signer)
        raw = str(dict(view.envelope)["payload_b64"])
        payload = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        assert view.digest == payload_digest(payload)


# ── The round-trip check is load-bearing ────────────────────────────────────


class TestTheVerifierRoundTripRefusesBadIssuance:
    def test_a_signer_that_signs_the_wrong_bytes_is_refused(self, db) -> None:
        """Sensitivity proof for the round trip itself.

        Without this test the only evidence `verify_licence` is called would be
        that nothing crashes — which is also true of an implementation that
        never calls it.
        """
        with pytest.raises(UnverifiableIssuanceError, match="fails the pinned"):
            _issue(db, WrongPayloadSigner())

    def test_nothing_is_recorded_when_verification_fails(self, db) -> None:
        """Verify BEFORE record. An implementation that recorded first would
        leave a licence no deployment can apply, discovered at the receiver."""
        with pytest.raises(UnverifiableIssuanceError):
            _issue(db, WrongPayloadSigner())
        assert db.query(LicenceIssuance).count() == 0


# ── Refusals before anything is written ─────────────────────────────────────


class TestIssuanceRefusesWhatItCannotSign:
    def test_a_grant_with_no_capabilities_is_refused(self, db, signer) -> None:
        """An empty licence is applied successfully by every deployment and
        authorises nothing — a silent outage rather than a loud refusal."""
        with pytest.raises(EmptyGrantError):
            _issue(db, signer, capabilities=())

    def test_no_signer_at_all_is_refused(self, db) -> None:
        with pytest.raises(SignerRefusedError, match="at least one signer"):
            issue_licence(
                db,
                IssueCommand(command_id="cmd-x", grant=_grant()),
                signers=(),
                now=_NOW,
            )

    def test_a_signer_with_no_public_key_is_refused(self, db) -> None:
        """A signer that could sign without publishing its public half would
        produce documents nothing can verify — including our own round trip."""

        class Mute(FakeSigner):
            @property
            def public_key_b64(self) -> str:
                return ""

        with pytest.raises(SignerRefusedError, match="publishes no public key"):
            _issue(db, Mute())

    def test_two_signers_claiming_one_key_id_are_refused(self, db) -> None:
        """A receiver cannot tell which one signed."""
        first, second = FakeSigner("shared"), FakeSigner("shared")
        with pytest.raises(SignerRefusedError, match="different public material"):
            issue_licence(
                db,
                IssueCommand(command_id="cmd-x", grant=_grant()),
                signers=(first, second),
                now=_NOW,
            )

    def test_refusal_leaves_no_partial_write(self, db, signer) -> None:
        with pytest.raises(EmptyGrantError):
            _issue(db, signer, capabilities=())
        assert db.query(LicenceIssuance).count() == 0


# ── Idempotency, at two different identities ────────────────────────────────


class TestOneAllocationProducesOneIssuance:
    def test_re_issuing_the_same_allocation_returns_the_existing_version(
        self, db, signer
    ) -> None:
        """Two licences for one allocation means the same entitlement
        authorised twice — exactly what an idempotent issuer must prevent."""
        allocation = "alloc-fixed"
        first = _issue(db, signer, allocation_ref=allocation)
        second = _issue(db, signer, allocation_ref=allocation)
        assert first.id == second.id
        assert first.version == second.version == 1
        assert db.query(LicenceIssuance).count() == 1

    def test_replaying_a_command_id_does_not_mint_a_second_version(
        self, db, signer
    ) -> None:
        """The other identity: the same COMMAND redelivered, which is what an
        at-least-once transport does."""
        command = IssueCommand(command_id="cmd-fixed", grant=_grant())
        first = issue_licence(db, command, signers=(signer,), now=_NOW)
        second = issue_licence(db, command, signers=(signer,), now=_NOW)
        assert first.id == second.id
        assert db.query(LicenceIssuance).count() == 1


# ── Versions and supersession ───────────────────────────────────────────────


class TestVersionsAdvanceAndSupersede:
    def test_a_second_issuance_takes_the_next_version_in_the_lineage(
        self, db, signer
    ) -> None:
        first = _issue(db, signer)
        second = _issue(db, signer)
        assert second.licence_id == first.licence_id, "same subject+product"
        assert (first.version, second.version) == (1, 2)

    def test_the_previous_version_becomes_replaced_not_expired(
        self, db, signer
    ) -> None:
        """They answer different questions: a replaced version was fine and is
        no longer current; an expired one ran out. An operator asking "would
        re-issuing help?" needs to tell them apart."""
        first = _issue(db, signer)
        second = _issue(db, signer)
        from dotmac_licensing import get_issuance

        superseded = get_issuance(db, first.id)
        assert superseded is not None
        assert superseded.status == IssuanceStatus.REPLACED.value
        assert superseded.replaced_by_version == second.version

    def test_current_issuance_is_the_highest_version(self, db, signer) -> None:
        _issue(db, signer)
        latest = _issue(db, signer)
        current = current_issuance(db, latest.licence_id)
        assert current is not None
        assert current.id == latest.id

    def test_a_different_product_gets_its_own_lineage(self, db, signer) -> None:
        first = _issue(db, signer, product_code="dotmac_sub")
        second = _issue(db, signer, product_code="dotmac_erp")
        assert first.licence_id != second.licence_id


# ── Rotation overlap ────────────────────────────────────────────────────────


class TestRotationOverlapIsASequenceNotAMode:
    def test_two_signers_produce_two_signatures_on_one_payload(self, db) -> None:
        """What makes rotation non-breaking: a deployment holding EITHER keyring
        verifies the same document."""
        primary, previous = FakeSigner("key-new"), FakeSigner("key-old")
        view = issue_licence(
            db,
            IssueCommand(command_id="cmd-1", grant=_grant()),
            signers=(primary, previous),
            now=_NOW,
        )
        signatures = dict(view.envelope)["signatures"]
        assert {s["key_id"] for s in signatures} == {"key-new", "key-old"}  # type: ignore[union-attr]

    def test_the_issuance_records_the_primary_key(self, db) -> None:
        """`key_id` answers "which key does this belong to" for the re-issue
        sweep after a key is revoked; the overlap signature is in the envelope."""
        primary, previous = FakeSigner("key-new"), FakeSigner("key-old")
        view = issue_licence(
            db,
            IssueCommand(command_id="cmd-1", grant=_grant()),
            signers=(primary, previous),
            now=_NOW,
        )
        assert view.key_id == "key-new"

    def test_both_public_keys_reach_the_distributed_keyring(self, db) -> None:
        primary, previous = FakeSigner("key-new"), FakeSigner("key-old")
        issue_licence(
            db,
            IssueCommand(command_id="cmd-1", grant=_grant()),
            signers=(primary, previous),
            now=_NOW,
        )
        keyring = build_keyring(db)
        assert keyring.get("key-new") is not None
        assert keyring.get("key-old") is not None


# ── The module holds no key material ────────────────────────────────────────


class TestNoSigningMaterialIsHeld:
    def test_the_signing_key_model_has_no_private_column(self) -> None:
        """Structural, not conventional: a database dump cannot leak what the
        schema has no column for."""
        from dotmac_licensing import SigningKey

        columns = set(SigningKey.__table__.columns.keys())
        assert "public_key_b64" in columns
        for forbidden in ("private_key", "private_key_b64", "secret", "key_material"):
            assert forbidden not in columns

    def test_the_package_ships_no_signer_implementation(self) -> None:
        """A signer in a shared library is a default that ships. The source's
        `ephemeral` mode was correct for a product and would be a hazard here."""
        import dotmac_licensing
        from dotmac_licensing.ports import LicenceSigner

        concrete = [
            name
            for name in dir(dotmac_licensing)
            if isinstance(getattr(dotmac_licensing, name), type)
            and getattr(dotmac_licensing, name) is not LicenceSigner
            and isinstance(getattr(dotmac_licensing, name), type)
            and issubclass(getattr(dotmac_licensing, name), object)
            and hasattr(getattr(dotmac_licensing, name), "sign")
        ]
        assert not concrete, f"{concrete} implement a signer inside the package"

    def test_no_issued_row_stores_anything_key_shaped(self, db, signer) -> None:
        """A weak assertion by design — it cannot prove the absence of every
        secret — but it fails loudly if a field whose NAME advertises one is
        added, which is how such a field actually arrives."""
        view = _issue(db, signer)
        blob = json.dumps(dict(view.envelope)).lower()
        for banned in ("private", "secret", "-----begin"):
            assert banned not in blob


# ── Reads ───────────────────────────────────────────────────────────────────


class TestLineageReads:
    def test_the_lineage_view_lists_versions_in_order(self, db, signer) -> None:
        first = _issue(db, signer)
        _issue(db, signer)
        view = licence_view(db, first.licence_id)
        assert view is not None
        assert [issuance.version for issuance in view.issuances] == [1, 2]
        assert view.revoked is False
        assert view.generation == 1


# ── Transaction authority ───────────────────────────────────────────────────


class TestTheModuleOwnsNoTransaction:
    def test_nothing_is_committed_so_a_rollback_discards_it(self, db, signer) -> None:
        """Hard rule 8. If the service committed, the rollback would not remove
        the row — which is exactly what this asserts against."""
        _issue(db, signer)
        db.rollback()
        assert db.query(LicenceIssuance).count() == 0
