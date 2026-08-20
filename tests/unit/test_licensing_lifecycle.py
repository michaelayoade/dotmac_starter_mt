"""Lifecycle, acknowledgement and revocation — each refusal proven, not assumed.

Three invariants, and each has a failure mode worth stating:

1. **A licence reaches a status only through a command whose precondition held.**
   Without the guards, every status is reachable and the model means nothing.
2. **An acknowledgement is checked against what this issuer actually produced.**
   Without that check a deployment could mark itself activated on a document
   nobody signed — the exact thing an acknowledgement is supposed to make
   visible rather than enable.
3. **A published revocation list may only grow.** Version monotonicity alone
   does not prevent un-revocation: a higher version that omits an earlier id
   restores access while looking perfectly well-ordered to every receiver.

In-memory SQLite — logic only. Grants, the append-only triggers and the raw-SQL
constraints are proven in `tests/test_licensing_platform_isolation.py`.
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
    AcknowledgeCommand,
    AcknowledgementRefusedError,
    ExpectedStateError,
    InstallationReport,
    IssuanceStatus,
    IssuanceTransitionCommand,
    IssueCommand,
    LicensableGrant,
    LicensedCapability,
    RevocationSupersessionError,
    RevokeCommand,
    SigningKeyStatus,
    TransitionRefusedError,
    acknowledge,
    acknowledgements,
    activate,
    expire,
    get_issuance,
    inspect_issued_envelope,
    issuances_by_key,
    issue_licence,
    licence_view,
    module,
    publish_revocation_list,
    register_signing_key,
    reinstate,
    revoke_licence,
    set_key_status,
    suspend,
)
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
_LATER = _NOW + timedelta(days=400)


class FakeSigner:
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


@pytest.fixture(autouse=True)
def _installed_module_audit_actions() -> None:
    install_audit_actions(AuditActionRegistry.from_manifests([module]))


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _attach(dbapi_connection, _record):  # type: ignore[no-untyped-def]
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
        "capabilities": (LicensedCapability("subscriber.manage", {"quantity": 5}),),
        "agreement_ref": f"agr-{uuid.uuid4().hex[:10]}",
        "allocation_ref": f"alloc-{uuid.uuid4().hex[:10]}",
        "valid_until": _NOW + timedelta(days=365),
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


def _cmd(issuance_id, **kwargs):  # type: ignore[no-untyped-def]
    return IssuanceTransitionCommand(
        command_id=f"cmd-{uuid.uuid4().hex[:12]}", issuance_id=issuance_id, **kwargs
    )


# ── Lifecycle ───────────────────────────────────────────────────────────────


class TestTheLifecycleRefusesMoreThanItPermits:
    def test_an_issued_licence_activates(self, db, signer) -> None:
        view = activate(db, _cmd(_issue(db, signer).id))
        assert view.status == IssuanceStatus.ACTIVE.value
        assert view.activated_at is not None

    def test_an_active_licence_cannot_activate_again(self, db, signer) -> None:
        view = activate(db, _cmd(_issue(db, signer).id))
        with pytest.raises(TransitionRefusedError):
            activate(db, _cmd(view.id))

    def test_suspension_is_reversible_and_records_its_reason(self, db, signer) -> None:
        """The contracted response to a payment hold. ADR-0003: payment failure
        never implicitly deletes a counterparty's data, and a licence record is
        that data."""
        view = activate(db, _cmd(_issue(db, signer).id))
        view = suspend(db, _cmd(view.id, reason="payment hold"))
        assert view.status == IssuanceStatus.SUSPENDED.value
        assert view.envelope, "suspension withholds authority, it does not delete"
        view = reinstate(db, _cmd(view.id))
        assert view.status == IssuanceStatus.ACTIVE.value

    def test_a_suspended_licence_cannot_be_suspended_again(self, db, signer) -> None:
        view = suspend(db, _cmd(_issue(db, signer).id, reason="hold"))
        with pytest.raises(TransitionRefusedError):
            suspend(db, _cmd(view.id, reason="again"))

    def test_an_issued_licence_cannot_be_reinstated(self, db, signer) -> None:
        with pytest.raises(TransitionRefusedError):
            reinstate(db, _cmd(_issue(db, signer).id))

    def test_expiry_is_refused_while_the_licence_is_still_valid(
        self, db, signer
    ) -> None:
        """The guard that stops a mis-scheduled sweep withdrawing authority from
        every live deployment at once."""
        view = _issue(db, signer)
        with pytest.raises(TransitionRefusedError, match="not expired"):
            expire(db, _cmd(view.id), as_of=_NOW + timedelta(days=1))

    def test_expiry_succeeds_once_the_window_has_closed(self, db, signer) -> None:
        view = expire(db, _cmd(_issue(db, signer).id), as_of=_LATER)
        assert view.status == IssuanceStatus.EXPIRED.value

    def test_a_perpetual_licence_can_never_expire(self, db, signer) -> None:
        """`valid_until IS NULL` is a contractual choice, and treating it as
        expired would revoke authority nobody time-limited."""
        view = _issue(db, signer, valid_until=None)
        with pytest.raises(TransitionRefusedError, match="perpetual"):
            expire(db, _cmd(view.id), as_of=_LATER)

    def test_an_expired_licence_refuses_every_further_transition(
        self, db, signer
    ) -> None:
        view = expire(db, _cmd(_issue(db, signer).id), as_of=_LATER)
        for call in (
            lambda: activate(db, _cmd(view.id)),
            lambda: suspend(db, _cmd(view.id, reason="x")),
            lambda: reinstate(db, _cmd(view.id)),
        ):
            with pytest.raises(TransitionRefusedError):
                call()


class TestExpectedStateConcurrency:
    def test_a_stale_record_version_is_refused(self, db, signer) -> None:
        view = _issue(db, signer)
        stale = view.record_version
        activate(db, _cmd(view.id))
        with pytest.raises(ExpectedStateError) as excinfo:
            suspend(
                db,
                _cmd(
                    view.id,
                    expected_status=IssuanceStatus.ISSUED.value,
                    expected_version=stale,
                    reason="x",
                ),
            )
        assert excinfo.value.actual_status == IssuanceStatus.ACTIVE.value

    def test_the_version_advances_on_every_transition(self, db, signer) -> None:
        view = _issue(db, signer)
        assert view.record_version == 1
        view = activate(db, _cmd(view.id))
        assert view.record_version == 2
        view = suspend(db, _cmd(view.id, reason="x"))
        assert view.record_version == 3

    def test_replaying_a_command_id_does_not_transition_twice(self, db, signer) -> None:
        view = _issue(db, signer)
        command = IssuanceTransitionCommand(command_id="cmd-fixed", issuance_id=view.id)
        first = activate(db, command)
        second = activate(db, command)
        assert first.record_version == second.record_version


# ── Acknowledgement ─────────────────────────────────────────────────────────


class TestAcknowledgementsAreCheckedAgainstWhatWeIssued:
    def test_an_applied_report_activates_the_licence(self, db, signer) -> None:
        view = _issue(db, signer)
        acked = acknowledge(
            db,
            AcknowledgeCommand(
                command_id="cmd-a",
                report=InstallationReport(
                    licence_ref=str(view.licence_id),
                    licence_version=view.version,
                    digest=view.digest,
                    outcome="applied",
                    reported_at=_NOW,
                    authenticated_deployment_ref="acme-lagos-1",
                ),
            ),
        )
        assert acked.status == IssuanceStatus.ACTIVE.value
        records = acknowledgements(db, view.id)
        assert len(records) == 1
        assert records[0].authenticated is True

    def test_a_report_naming_a_digest_we_never_signed_is_refused(
        self, db, signer
    ) -> None:
        """The attack an acknowledgement is supposed to make visible rather than
        enable: a target marking itself activated on a document nobody signed."""
        view = _issue(db, signer)
        with pytest.raises(AcknowledgementRefusedError, match="did not sign"):
            acknowledge(
                db,
                AcknowledgeCommand(
                    command_id="cmd-a",
                    report=InstallationReport(
                        licence_ref=str(view.licence_id),
                        licence_version=view.version,
                        digest="sha256:" + "f" * 64,
                        outcome="applied",
                        reported_at=_NOW,
                    ),
                ),
            )

    def test_a_report_for_a_version_we_never_issued_is_refused(
        self, db, signer
    ) -> None:
        view = _issue(db, signer)
        with pytest.raises(AcknowledgementRefusedError, match="never produced"):
            acknowledge(
                db,
                AcknowledgeCommand(
                    command_id="cmd-a",
                    report=InstallationReport(
                        licence_ref=str(view.licence_id),
                        licence_version=99,
                        digest=view.digest,
                        outcome="applied",
                        reported_at=_NOW,
                    ),
                ),
            )

    def test_a_report_naming_a_licence_id_we_could_not_have_produced_is_refused(
        self, db, signer
    ) -> None:
        _issue(db, signer)
        with pytest.raises(AcknowledgementRefusedError):
            acknowledge(
                db,
                AcknowledgeCommand(
                    command_id="cmd-a",
                    report=InstallationReport(
                        licence_ref="not-a-uuid",
                        licence_version=1,
                        digest="sha256:" + "0" * 64,
                        outcome="applied",
                        reported_at=_NOW,
                    ),
                ),
            )

    def test_an_outcome_outside_the_shared_vocabulary_is_refused(
        self, db, signer
    ) -> None:
        view = _issue(db, signer)
        with pytest.raises(AcknowledgementRefusedError, match="vocabulary"):
            acknowledge(
                db,
                AcknowledgeCommand(
                    command_id="cmd-a",
                    report=InstallationReport(
                        licence_ref=str(view.licence_id),
                        licence_version=view.version,
                        digest=view.digest,
                        outcome="maybe",
                        reported_at=_NOW,
                    ),
                ),
            )

    def test_a_rejected_report_is_recorded_and_changes_no_status(
        self, db, signer
    ) -> None:
        """Rejection is information for an operator, not a licence transition.
        Auto-suspending on a receiver's say-so would let one misconfigured
        deployment withdraw its own authority."""
        view = _issue(db, signer)
        acked = acknowledge(
            db,
            AcknowledgeCommand(
                command_id="cmd-a",
                report=InstallationReport(
                    licence_ref=str(view.licence_id),
                    licence_version=view.version,
                    digest=view.digest,
                    outcome="rejected",
                    reason="LicenceExpiredError",
                    reported_at=_NOW,
                ),
            ),
        )
        assert acked.status == IssuanceStatus.ISSUED.value
        records = acknowledgements(db, view.id)
        assert records[0].outcome == "rejected"
        assert records[0].reason == "LicenceExpiredError"

    def test_an_unauthenticated_report_is_recorded_as_unauthenticated(
        self, db, signer
    ) -> None:
        """A self-declared identity inside a payload is not authentication, and
        one column holding both would make "did we verify this?" unanswerable."""
        view = _issue(db, signer)
        acknowledge(
            db,
            AcknowledgeCommand(
                command_id="cmd-a",
                report=InstallationReport(
                    licence_ref=str(view.licence_id),
                    licence_version=view.version,
                    digest=view.digest,
                    outcome="applied",
                    reported_at=_NOW,
                    authenticated_deployment_ref=None,
                ),
            ),
        )
        assert acknowledgements(db, view.id)[0].authenticated is False

    def test_a_repeated_report_from_one_deployment_is_idempotent(
        self, db, signer
    ) -> None:
        view = _issue(db, signer)
        report = InstallationReport(
            licence_ref=str(view.licence_id),
            licence_version=view.version,
            digest=view.digest,
            outcome="applied",
            reported_at=_NOW,
            authenticated_deployment_ref="acme-lagos-1",
        )
        acknowledge(db, AcknowledgeCommand(command_id="cmd-a", report=report))
        acknowledge(db, AcknowledgeCommand(command_id="cmd-b", report=report))
        assert len(acknowledgements(db, view.id)) == 1


# ── Revocation ──────────────────────────────────────────────────────────────


class TestRevocationIsByLineageAndPermanent:
    def test_revoking_marks_every_live_version_revoked(self, db, signer) -> None:
        first = _issue(db, signer)
        second = _issue(db, signer)
        revoke_licence(
            db,
            RevokeCommand(
                command_id="cmd-r", licence_id=second.licence_id, reason="breach"
            ),
        )
        for issuance_id in (first.id, second.id):
            row = get_issuance(db, issuance_id)
            assert row is not None
            # The first was already `replaced` by the second — a terminal state,
            # so revocation leaves it alone rather than rewriting settled history.
            assert row.status in {
                IssuanceStatus.REVOKED.value,
                IssuanceStatus.REPLACED.value,
            }
        current = get_issuance(db, second.id)
        assert current is not None
        assert current.status == IssuanceStatus.REVOKED.value

    def test_revoking_twice_is_idempotent(self, db, signer) -> None:
        view = _issue(db, signer)
        for command_id in ("cmd-r1", "cmd-r2"):
            revoke_licence(
                db,
                RevokeCommand(
                    command_id=command_id,
                    licence_id=view.licence_id,
                    reason="breach",
                ),
            )
        lineage = licence_view(db, view.licence_id)
        assert lineage is not None
        assert lineage.revoked is True

    def test_issuing_after_revocation_mints_a_new_generation(self, db, signer) -> None:
        """The contracted recovery path, and the only reason `generation` exists.
        Without it the resolver returns the revoked lineage and every "recovery"
        document is dead on arrival at every deployment."""
        first = _issue(db, signer)
        revoke_licence(
            db,
            RevokeCommand(
                command_id="cmd-r", licence_id=first.licence_id, reason="breach"
            ),
        )
        recovery = _issue(db, signer)
        assert recovery.licence_id != first.licence_id
        lineage = licence_view(db, recovery.licence_id)
        assert lineage is not None
        assert lineage.generation == 2
        assert lineage.revoked is False

    def test_a_revoked_licence_fails_inspection(self, db, signer) -> None:
        """End to end: the revoked set reaches the verifier a receiver runs."""
        view = _issue(db, signer)
        assert inspect_issued_envelope(db, view.envelope, now=_NOW).valid
        revoke_licence(
            db,
            RevokeCommand(
                command_id="cmd-r", licence_id=view.licence_id, reason="breach"
            ),
        )
        result = inspect_issued_envelope(db, view.envelope, now=_NOW)
        assert not result.valid
        assert result.reason == "RevokedLicenceError"


class TestPublishedRevocationListsMayOnlyGrow:
    def _revoked_ids(self, view) -> list[str]:  # type: ignore[no-untyped-def]
        raw = str(dict(view.envelope)["payload_b64"])
        document = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        return list(document["revoked_licence_ids"])

    def test_the_first_list_is_version_one_and_verifies(self, db, signer) -> None:
        view = _issue(db, signer)
        revoke_licence(
            db,
            RevokeCommand(
                command_id="cmd-r", licence_id=view.licence_id, reason="breach"
            ),
        )
        published = publish_revocation_list(
            db, command_id="cmd-p1", signers=(signer,), now=_NOW
        )
        assert published.list_version == 1
        assert published.entry_count == 1
        assert self._revoked_ids(published) == [str(view.licence_id)]

    def test_a_later_list_carries_every_earlier_id(self, db, signer) -> None:
        first = _issue(db, signer, subject_ref="a")
        second = _issue(db, signer, subject_ref="b")
        revoke_licence(db, RevokeCommand("cmd-r1", first.licence_id, "breach"))
        publish_revocation_list(db, command_id="cmd-p1", signers=(signer,), now=_NOW)
        revoke_licence(db, RevokeCommand("cmd-r2", second.licence_id, "breach"))
        later = publish_revocation_list(
            db, command_id="cmd-p2", signers=(signer,), now=_NOW
        )
        assert later.list_version == 2
        assert set(self._revoked_ids(later)) == {
            str(first.licence_id),
            str(second.licence_id),
        }

    def test_a_list_that_would_omit_an_earlier_id_is_refused(self, db, signer) -> None:
        """The cumulative rule, driven against the failure it exists for.

        A higher version that quietly omits an earlier id restores access while
        looking perfectly well-ordered to every receiver — no deployment would
        report anything wrong.
        """
        from dotmac_licensing import Revocation

        view = _issue(db, signer)
        revoke_licence(db, RevokeCommand("cmd-r", view.licence_id, "breach"))
        publish_revocation_list(db, command_id="cmd-p1", signers=(signer,), now=_NOW)

        # Simulate the omission the rule forbids: the revocation row is gone
        # from the table, so a naive implementation would publish a smaller list.
        db.query(Revocation).delete()
        db.flush()

        with pytest.raises(RevocationSupersessionError, match="may only grow"):
            publish_revocation_list(
                db, command_id="cmd-p2", signers=(signer,), now=_NOW
            )


# ── Key rotation ────────────────────────────────────────────────────────────


class TestKeyRotation:
    def test_retiring_a_key_keeps_it_verifying(self, db, signer) -> None:
        """The rotation overlap. A key with only active/revoked forces every
        deployment to update in lockstep with the issuer."""
        view = _issue(db, signer)
        set_key_status(db, key_id=signer.key_id, status=SigningKeyStatus.RETIRED)
        assert inspect_issued_envelope(db, view.envelope, now=_NOW).valid

    def test_revoking_a_key_stops_its_documents_verifying(self, db, signer) -> None:
        view = _issue(db, signer)
        set_key_status(db, key_id=signer.key_id, status=SigningKeyStatus.REVOKED)
        result = inspect_issued_envelope(db, view.envelope, now=_NOW)
        assert not result.valid
        assert result.reason == "RevokedKeyError"

    def test_the_re_issue_sweep_finds_every_issuance_for_a_key(
        self, db, signer
    ) -> None:
        """Revoking a key is a decision; deciding which lineages to re-issue and
        in what order is the operator's, and this is the list they need."""
        _issue(db, signer, subject_ref="a")
        _issue(db, signer, subject_ref="b")
        assert len(issuances_by_key(db, signer.key_id)) == 2
        assert issuances_by_key(db, "no-such-key") == ()

    def test_registering_a_key_twice_updates_rather_than_duplicates(self, db) -> None:
        register_signing_key(db, key_id="k", public_key_b64="AAAA")
        register_signing_key(db, key_id="k", public_key_b64="BBBB")
        from dotmac_licensing import SigningKey

        rows = db.query(SigningKey).filter(SigningKey.key_id == "k").all()
        assert len(rows) == 1
        assert rows[0].public_key_b64 == "BBBB"

    def test_rotating_an_unregistered_key_is_refused(self, db) -> None:
        with pytest.raises(TransitionRefusedError, match="not registered"):
            set_key_status(db, key_id="ghost", status=SigningKeyStatus.RETIRED)


# ── Inspection ──────────────────────────────────────────────────────────────


class TestInspectionAnswersRatherThanRaises:
    def test_an_expired_licence_returns_a_reason_not_an_exception(
        self, db, signer
    ) -> None:
        """A verification failure IS the answer. Raising would move the
        diagnosis into the caller's exception handler."""
        view = _issue(db, signer)
        result = inspect_issued_envelope(db, view.envelope, now=_LATER)
        assert not result.valid
        assert result.reason == "LicenceExpiredError"
        assert result.detail

    def test_a_deployment_mismatch_is_reported_as_such(self, db, signer) -> None:
        view = _issue(db, signer, deployment_ref="acme-lagos-1")
        result = inspect_issued_envelope(
            db, view.envelope, now=_NOW, expected_deployment_id="acme-abuja-2"
        )
        assert not result.valid
        assert result.reason == "DeploymentMismatchError"

    def test_a_grace_period_reports_in_grace_rather_than_invalid(
        self, db, signer
    ) -> None:
        """Explicitly degraded, not refused — the distinction a support engineer
        needs before telling a customer their service has stopped."""
        view = _issue(db, signer, grace_days=30)
        within_grace = _NOW + timedelta(days=370)
        result = inspect_issued_envelope(db, view.envelope, now=within_grace)
        assert result.valid
        assert result.validity == "in_grace"

    def test_malformed_input_is_an_answer_too(self, db) -> None:
        result = inspect_issued_envelope(db, {"schema": "nonsense"}, now=_NOW)
        assert not result.valid
        assert result.reason == "MalformedLicenceError"
