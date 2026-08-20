"""Evidence binds, or the transition does not happen.

The invariant this file protects, and the reason the module exists in this
shape: **activation requires the exact approval evidence and the accepted
snapshot; it never infers approval from a status string an adapter maintains.**

That is a property with a specific failure mode. Without digest binding,
"approved" becomes a token that can be moved onto broader terms than anyone
reviewed — change the quantity from 500 to 50,000 after the approval, and an
unbound implementation activates it. Every test below is one way to attempt that
move.

In-memory SQLite. The binding is arithmetic over a digest, so it needs no
Postgres; the append-only storage of the evidence document is proven in
`tests/test_commercial_agreements_platform_isolation.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import UTC, date, datetime

import pytest
from dotmac_commercial_agreements import (
    ActivateCommand,
    ActivationEvidence,
    Agreement,
    AgreementPeriod,
    ApprovalEvidence,
    ApproveCommand,
    CommercialTerms,
    DraftCommand,
    EvidenceRefusedError,
    LineInput,
    ProposeCommand,
    accepted_snapshot,
    activate,
    approve,
    module,
    open_draft,
    propose,
    snapshot_digest,
)
from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
from dotmac_kernel.models import Base
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

_TODAY = date(2026, 9, 1)
_END = date(2027, 8, 31)
_DECIDED = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
_SATISFIED = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)


class FakeCatalogue:
    def require_declared(self, product_code: str, codes: tuple[str, ...]) -> None:
        return None


@pytest.fixture(autouse=True)
def _installed_module_audit_actions() -> None:
    install_audit_actions(AuditActionRegistry.from_manifests([module]))


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _attach(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        dbapi_connection.isolation_level = None
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS mod_agreements")

    @event.listens_for(engine, "begin")
    def _emit_begin(connection):  # type: ignore[no-untyped-def]
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(
        engine,
        tables=[
            table
            for table in Base.metadata.tables.values()
            if table.schema == "mod_agreements"
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
def proposed(db: Session):
    view = open_draft(
        db,
        DraftCommand(
            command_id=f"cmd-{uuid.uuid4().hex[:10]}",
            reference=f"AGR-{uuid.uuid4().hex[:8]}",
            counterparty_ref="acme-operator",
            agreement_type="oem_reseller",
            period=AgreementPeriod(_TODAY, _END),
            lines=(
                LineInput(
                    product_code="dotmac_sub",
                    capability_code="subscriber.manage",
                    quantity=500,
                    terms=CommercialTerms("12.50", "NGN"),
                ),
            ),
        ),
        catalogue=FakeCatalogue(),
    )
    return propose(
        db,
        ProposeCommand(
            command_id=f"cmd-{uuid.uuid4().hex[:10]}",
            agreement_id=view.id,
            approval_policy_code="commercial.oem",
            approval_policy_version=3,
        ),
        catalogue=FakeCatalogue(),
    )


def _evidence(
    digest: str, *, policy: str = "commercial.oem", version: int = 3
) -> ApprovalEvidence:
    return ApprovalEvidence(
        policy_code=policy,
        policy_version=version,
        decision_ref="apr-0001",
        content_digest=digest,
        decided_at=_DECIDED,
    )


# ── The digest itself ───────────────────────────────────────────────────────


class TestTheSnapshotDigestIsDeterministic:
    """If the digest is not stable, binding is meaningless — an approval would
    go stale on its own between two reads of unchanged data."""

    def test_the_same_agreement_digests_identically_every_time(
        self, db, proposed
    ) -> None:
        row = db.get(Agreement, proposed.id)
        assert row is not None
        assert snapshot_digest(accepted_snapshot(row)) == snapshot_digest(
            accepted_snapshot(row)
        )
        assert proposed.content_hash == snapshot_digest(accepted_snapshot(row))

    def test_line_order_does_not_change_the_digest(self, db, proposed) -> None:
        """The snapshot sorts its lines by a total order.

        Without that, rebuilding the same agreement with its lines loaded in a
        different order would silently invalidate an approval nobody changed.
        """
        row = db.get(Agreement, proposed.id)
        assert row is not None
        first = snapshot_digest(accepted_snapshot(row))
        row.lines.reverse()
        assert snapshot_digest(accepted_snapshot(row)) == first

    def test_changing_a_quantity_changes_the_digest(self, db, proposed) -> None:
        """The property the whole binding rests on."""
        row = db.get(Agreement, proposed.id)
        assert row is not None
        before = snapshot_digest(accepted_snapshot(row))
        row.lines[0].quantity = 50_000
        assert snapshot_digest(accepted_snapshot(row)) != before

    def test_changing_a_price_changes_the_digest(self, db, proposed) -> None:
        row = db.get(Agreement, proposed.id)
        assert row is not None
        before = snapshot_digest(accepted_snapshot(row))
        row.lines[0].unit_amount = "0.01"
        assert snapshot_digest(accepted_snapshot(row)) != before

    def test_changing_the_period_changes_the_digest(self, db, proposed) -> None:
        row = db.get(Agreement, proposed.id)
        assert row is not None
        before = snapshot_digest(accepted_snapshot(row))
        row.expiry_date = date(2030, 1, 1)
        assert snapshot_digest(accepted_snapshot(row)) != before


# ── Approval binding ────────────────────────────────────────────────────────


class TestApprovalBindsToTheFrozenDigest:
    def test_matching_evidence_is_accepted(self, db, proposed) -> None:
        view = approve(
            db,
            ApproveCommand(
                "cmd-a", proposed.id, _evidence(proposed.content_hash or "")
            ),
        )
        assert view.status == "approved"
        assert view.approval_decision_ref == "apr-0001"
        # `.replace(tzinfo=None)` because SQLite has no tz-aware type and
        # returns the stored instant naive; Postgres returns it aware. The
        # assertion is about WHOSE clock was recorded, and that survives the
        # normalisation — a module that stamped its own `now()` would differ by
        # far more than a tzinfo.
        assert view.approved_at is not None
        assert view.approved_at.replace(tzinfo=None) == _DECIDED.replace(
            tzinfo=None
        ), "the deciding owner's clock is recorded, not this module's"

    def test_a_mismatched_digest_is_refused(self, db, proposed) -> None:
        with pytest.raises(EvidenceRefusedError, match="terms changed"):
            approve(db, ApproveCommand("cmd-a", proposed.id, _evidence("f" * 64)))

    def test_evidence_naming_a_different_policy_is_refused(self, db, proposed) -> None:
        """A quorum reached under the wrong policy is not this agreement's
        approval, however genuine the decision was."""
        with pytest.raises(EvidenceRefusedError, match="policy"):
            approve(
                db,
                ApproveCommand(
                    "cmd-a",
                    proposed.id,
                    _evidence(proposed.content_hash or "", policy="commercial.direct"),
                ),
            )

    def test_evidence_naming_a_different_policy_version_is_refused(
        self, db, proposed
    ) -> None:
        """Policy revisions differ in quorum and eligibility. An approval under
        v2 is not an approval under v3."""
        with pytest.raises(EvidenceRefusedError, match="policy version"):
            approve(
                db,
                ApproveCommand(
                    "cmd-a",
                    proposed.id,
                    _evidence(proposed.content_hash or "", version=2),
                ),
            )


# ── The attack this module exists to refuse ─────────────────────────────────


class TestApprovalIsStaleRatherThanTransferable:
    def test_widening_the_terms_after_approval_makes_activation_impossible(
        self, db, proposed
    ) -> None:
        """The whole point, driven end to end.

        Approve 500 units, then quietly raise it to 50,000 and try to activate
        on the approval that was given. An unbound implementation activates. This
        one refuses, because the frozen digest no longer describes the rows.
        """
        digest = proposed.content_hash or ""
        approve(db, ApproveCommand("cmd-a", proposed.id, _evidence(digest)))

        row = db.get(Agreement, proposed.id)
        assert row is not None
        row.lines[0].quantity = 50_000
        # Re-freeze the way a compromised or careless adapter would: change the
        # rows AND the stored digest, so only the EVIDENCE still names the old
        # terms.
        row.content_hash = snapshot_digest(accepted_snapshot(row))
        db.flush()

        with pytest.raises(EvidenceRefusedError, match="terms changed"):
            activate(
                db,
                ActivateCommand(
                    command_id="cmd-b",
                    agreement_id=proposed.id,
                    approval_evidence=_evidence(digest),
                    activation_evidence=ActivationEvidence(
                        "countersignature", "doc-1", _SATISFIED
                    ),
                ),
            )

    def test_an_agreement_with_no_frozen_snapshot_refuses_evidence(
        self, db, proposed
    ) -> None:
        """Belt and braces for the `content_hash IS NULL` path — a draft, or a
        rejected agreement whose digest was cleared."""
        row = db.get(Agreement, proposed.id)
        assert row is not None
        row.content_hash = None
        db.flush()
        with pytest.raises(EvidenceRefusedError, match="no frozen snapshot"):
            approve(db, ApproveCommand("cmd-a", proposed.id, _evidence("a" * 64)))


# ── Activation evidence ─────────────────────────────────────────────────────


class TestActivationRequiresItsOwnEvidence:
    @pytest.fixture
    def approved(self, db, proposed):
        return approve(
            db,
            ApproveCommand(
                "cmd-a", proposed.id, _evidence(proposed.content_hash or "")
            ),
        )

    def test_activation_re_checks_the_approval_evidence(self, db, approved) -> None:
        """Not because the module distrusts its own `approved` column, but so an
        auditor reading one history row can verify the activation without
        trusting that an earlier row was written correctly."""
        with pytest.raises(EvidenceRefusedError):
            activate(
                db,
                ActivateCommand(
                    command_id="cmd-b",
                    agreement_id=approved.id,
                    approval_evidence=_evidence("e" * 64),
                    activation_evidence=ActivationEvidence(
                        "countersignature", "doc-1", _SATISFIED
                    ),
                ),
            )

    def test_a_blank_activation_rule_is_refused(self, db, approved) -> None:
        with pytest.raises(EvidenceRefusedError, match="named activation rule"):
            activate(
                db,
                ActivateCommand(
                    command_id="cmd-b",
                    agreement_id=approved.id,
                    approval_evidence=_evidence(approved.content_hash or ""),
                    activation_evidence=ActivationEvidence("   ", "doc-1", _SATISFIED),
                ),
            )

    def test_a_rule_with_no_reference_is_refused(self, db, approved) -> None:
        """Activation is rule-driven, not "a form was submitted" — the source
        implementation's guard, kept."""
        with pytest.raises(EvidenceRefusedError, match="requires a reference"):
            activate(
                db,
                ActivateCommand(
                    command_id="cmd-b",
                    agreement_id=approved.id,
                    approval_evidence=_evidence(approved.content_hash or ""),
                    activation_evidence=ActivationEvidence(
                        "countersignature", "  ", _SATISFIED
                    ),
                ),
            )

    def test_a_refused_activation_leaves_no_partial_write(self, db, approved) -> None:
        """Evidence is checked BEFORE any row changes.

        An implementation that advanced the status and then validated would
        leave the agreement `active` with a history row claiming a transition
        that the caller was told had failed.
        """
        from dotmac_commercial_agreements import get, history

        before = len(history(db, approved.id))
        with pytest.raises(EvidenceRefusedError):
            activate(
                db,
                ActivateCommand(
                    command_id="cmd-b",
                    agreement_id=approved.id,
                    approval_evidence=_evidence(approved.content_hash or ""),
                    activation_evidence=ActivationEvidence("rule", "", _SATISFIED),
                ),
            )
        after = get(db, approved.id)
        assert after is not None
        assert after.status == "approved", "the status did not move"
        assert after.record_version == approved.record_version
        assert len(history(db, approved.id)) == before


# ── The evidence document is stored whole ───────────────────────────────────


class TestTheEvidenceIsStoredNotSummarised:
    def test_the_activation_history_row_carries_both_evidences_and_the_snapshot(
        self, db, proposed
    ) -> None:
        """Stored whole so an auditor can reconstruct the decision without
        joining to an owner that may since have retired the policy."""
        from dotmac_commercial_agreements import AgreementEvent

        digest = proposed.content_hash or ""
        approve(db, ApproveCommand("cmd-a", proposed.id, _evidence(digest)))
        activate(
            db,
            ActivateCommand(
                command_id="cmd-b",
                agreement_id=proposed.id,
                approval_evidence=_evidence(digest),
                activation_evidence=ActivationEvidence(
                    "countersignature", "doc-4471", _SATISFIED
                ),
            ),
        )
        row = (
            db.query(AgreementEvent)
            .filter(AgreementEvent.agreement_id == proposed.id)
            .order_by(AgreementEvent.sequence.desc())
            .first()
        )
        assert row is not None
        assert row.evidence is not None
        assert row.evidence["approval"]["content_digest"] == digest
        assert row.evidence["approval"]["policy_version"] == 3
        assert row.evidence["activation"]["rule"] == "countersignature"
        assert row.evidence["activation"]["reference"] == "doc-4471"
        assert row.evidence["accepted_snapshot"]["lines"][0]["quantity"] == 500

    def test_no_evidence_field_holds_anything_secret_shaped(self, db, proposed) -> None:
        """This module stores references and digests, never material.

        A weak assertion by design — it cannot prove the absence of every secret
        — but it fails loudly if someone adds a field whose NAME advertises one,
        which is how such a field actually arrives.
        """
        from dotmac_commercial_agreements import AgreementEvent

        digest = proposed.content_hash or ""
        approve(db, ApproveCommand("cmd-a", proposed.id, _evidence(digest)))
        rows = (
            db.query(AgreementEvent)
            .filter(AgreementEvent.agreement_id == proposed.id)
            .all()
        )
        banned = {"private_key", "secret", "password", "token", "signing_key"}
        for row in rows:
            keys = set(row.evidence or {})
            assert not (keys & banned), f"evidence carries {keys & banned}"
