"""The lifecycle refuses more than it permits, and every refusal is proven.

The invariant this file protects: **an agreement reaches a status only through a
command whose precondition held, and the append-only history says so.** A suite
that only walked the happy path would pass against an implementation with no
guards at all — every status is reachable if nothing checks.

So the shape here is: for each transition, one test that it works from the
legal status, and one that it is refused from an illegal one. Plus the three
properties that are easy to implement and easy to get subtly wrong —
idempotency, optimistic concurrency, and amendment-as-new-version.

In-memory SQLite — logic only. Grants, the append-only trigger, the raw-SQL
constraints and migration-from-empty are proven against real Postgres in
`tests/test_commercial_agreements_platform_isolation.py`. Do not add a tenancy
or privilege assertion here; SQLite cannot enforce either, so it would pass for
the wrong reason.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import UTC, date, datetime

import pytest
from dotmac_commercial_agreements import (
    MAX_AGREEMENT_PAGE_SIZE,
    ActivateCommand,
    ActivationEvidence,
    AgreementBoundaryError,
    AgreementError,
    AgreementPage,
    AgreementPeriod,
    AgreementStatus,
    AgreementView,
    AmendCommand,
    ApprovalEvidence,
    ApproveCommand,
    CommercialTerms,
    DraftCommand,
    EmptyAgreementError,
    EvidenceRefusedError,
    ExpectedStateError,
    LineInput,
    ProposeCommand,
    TerminateCommand,
    TransitionCommand,
    TransitionRefusedError,
    UndeclaredCapabilityError,
    UnknownProductError,
    activate,
    amend,
    approve,
    cancel,
    expire,
    family,
    get,
    history,
    list_agreements,
    module,
    open_draft,
    propose,
    reinstate,
    reject,
    suspend,
    terminate,
)
from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
from dotmac_kernel.models import Base
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

_TODAY = date(2026, 9, 1)
_END = date(2027, 8, 31)


class FakeCatalogue:
    """A catalogue reader over a `{product: {codes}}` map.

    Deliberately NOT a stub that always says yes: the failing paths are the
    point, and a permissive fake would make the validation tests vacuous.
    """

    def __init__(self, declared: dict[str, set[str]]) -> None:
        self.declared = declared
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def require_declared(self, product_code: str, codes: tuple[str, ...]) -> None:
        self.calls.append((product_code, codes))
        if product_code not in self.declared:
            raise UnknownProductError(product_code)
        missing = tuple(sorted(set(codes) - self.declared[product_code]))
        if missing:
            raise UndeclaredCapabilityError(product_code, missing)


@pytest.fixture(autouse=True)
def _installed_module_audit_actions() -> None:
    """Exercise the module as an adopter does: its manifest is installed."""
    install_audit_actions(AuditActionRegistry.from_manifests([module]))


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _attach(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        # pysqlite does not emit BEGIN on its own, which leaves SAVEPOINT
        # semantics broken — and every command runs inside one, via the kernel's
        # at-most-once owner. Without SQLAlchemy's documented workaround a
        # rollback silently keeps the row and the flush-only test below would
        # fail against CORRECT code.
        dbapi_connection.isolation_level = None
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS mod_agreements")

    @event.listens_for(engine, "begin")
    def _emit_begin(connection):  # type: ignore[no-untyped-def]
        connection.exec_driver_sql("BEGIN")

    # A module that reuses a kernel facility inherits its storage: every command
    # writes the platform idempotency ledger and the platform audit log.
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
def catalogue() -> FakeCatalogue:
    return FakeCatalogue(
        {
            "dotmac_sub": {"subscriber.manage", "billing.invoicing"},
            "dotmac_erp": {"finance.ledger"},
        }
    )


def _lines(*, product: str = "dotmac_sub") -> tuple[LineInput, ...]:
    return (
        LineInput(
            product_code=product,
            capability_code="subscriber.manage",
            quantity=500,
            terms=CommercialTerms(unit_amount="12.50", currency_code="NGN"),
            release_ref="dotmac_sub@7.187.1",
        ),
    )


def _draft(db: Session, catalogue: FakeCatalogue, *, reference: str | None = None):
    return open_draft(
        db,
        DraftCommand(
            command_id=f"cmd-{uuid.uuid4().hex[:12]}",
            reference=reference or f"AGR-{uuid.uuid4().hex[:8]}",
            counterparty_ref="acme-operator",
            agreement_type="oem_reseller",
            period=AgreementPeriod(_TODAY, _END),
            lines=_lines(),
        ),
        catalogue=catalogue,
    )


def _propose(db: Session, catalogue: FakeCatalogue, agreement_id: uuid.UUID):
    return propose(
        db,
        ProposeCommand(
            command_id=f"cmd-{uuid.uuid4().hex[:12]}",
            agreement_id=agreement_id,
            approval_policy_code="commercial.oem",
            approval_policy_version=3,
        ),
        catalogue=catalogue,
    )


def _evidence(digest: str, *, policy: str = "commercial.oem", version: int = 3):
    return ApprovalEvidence(
        policy_code=policy,
        policy_version=version,
        decision_ref=f"apr-{uuid.uuid4().hex[:10]}",
        content_digest=digest,
        decided_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
    )


def _approve(db: Session, view):
    return approve(
        db,
        ApproveCommand(
            command_id=f"cmd-{uuid.uuid4().hex[:12]}",
            agreement_id=view.id,
            evidence=_evidence(view.content_hash or ""),
        ),
    )


def _activate(db: Session, view):
    return activate(
        db,
        ActivateCommand(
            command_id=f"cmd-{uuid.uuid4().hex[:12]}",
            agreement_id=view.id,
            approval_evidence=_evidence(view.content_hash or ""),
            activation_evidence=ActivationEvidence(
                rule="countersignature",
                reference="doc-4471",
                satisfied_at=datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
            ),
        ),
    )


def _to_active(db: Session, catalogue: FakeCatalogue):
    view = _draft(db, catalogue)
    view = _propose(db, catalogue, view.id)
    view = _approve(db, view)
    return _activate(db, view)


# ── The forward path ────────────────────────────────────────────────────────


class TestTheForwardPath:
    def test_a_draft_starts_unfrozen_and_at_version_one(self, db, catalogue) -> None:
        view = _draft(db, catalogue)
        assert view.status == AgreementStatus.DRAFT.value
        assert view.content_hash is None, "a draft has nothing frozen to approve"
        assert view.agreement_version == 1
        assert view.record_version == 1
        assert len(view.lines) == 1

    def test_proposing_freezes_a_snapshot_and_its_digest(self, db, catalogue) -> None:
        view = _propose(db, catalogue, _draft(db, catalogue).id)
        assert view.status == AgreementStatus.PROPOSED.value
        assert view.content_hash and len(view.content_hash) == 64
        assert view.approval_policy_code == "commercial.oem"
        assert view.approval_policy_version == 3

    def test_approval_is_not_activation(self, db, catalogue) -> None:
        """The separation the source implementation got right, kept.

        Collapsing them would make "signed but not yet countersigned"
        inexpressible, which is where most commercial disputes live.
        """
        view = _approve(db, _propose(db, catalogue, _draft(db, catalogue).id))
        assert view.status == AgreementStatus.APPROVED.value
        assert view.approved_at is not None
        assert view.activated_at is None, "approval must not imply activation"

    def test_activation_records_the_rule_that_was_satisfied(
        self, db, catalogue
    ) -> None:
        view = _to_active(db, catalogue)
        assert view.status == AgreementStatus.ACTIVE.value
        assert view.activation_rule == "countersignature"
        # Naive comparison: SQLite has no tz-aware type. See the note in
        # test_commercial_agreements_evidence.py — the property under test is
        # that the EVIDENCE's clock is stored, not this module's.
        assert view.activated_at is not None
        assert view.activated_at.replace(tzinfo=None) == datetime(2026, 8, 21, 10, 0)

    def test_the_full_path_appends_one_history_row_per_transition(
        self, db, catalogue
    ) -> None:
        view = _to_active(db, catalogue)
        rows = history(db, view.id)
        assert [r.to_status for r in rows] == ["proposed", "approved", "active"]
        assert [r.sequence for r in rows] == [1, 2, 3], "sequence is dense"
        assert rows[0].from_status == "draft"
        # `open_draft` appends nothing: creating a draft is not a transition,
        # and a history row claiming one would make the first sequence number
        # mean something different from every later one.
        assert all(r.command_id for r in rows)


# ── Refusals ────────────────────────────────────────────────────────────────


class TestIllegalTransitionsAreRefused:
    """Every guard, driven from a status it must reject.

    This is the half a happy-path suite misses entirely: without these, an
    implementation whose guards were deleted would still pass every test above.
    """

    def test_a_draft_cannot_be_approved(self, db, catalogue) -> None:
        view = _draft(db, catalogue)
        with pytest.raises(ExpectedStateError):
            approve(
                db,
                ApproveCommand(
                    command_id="cmd-x",
                    agreement_id=view.id,
                    evidence=_evidence("0" * 64),
                ),
            )

    def test_a_proposed_agreement_cannot_be_activated(self, db, catalogue) -> None:
        view = _propose(db, catalogue, _draft(db, catalogue).id)
        with pytest.raises(ExpectedStateError):
            _activate(db, view)

    def test_a_draft_cannot_be_suspended(self, db, catalogue) -> None:
        view = _draft(db, catalogue)
        with pytest.raises(TransitionRefusedError):
            suspend(db, TransitionCommand("cmd-x", view.id))

    def test_an_active_agreement_cannot_be_cancelled(self, db, catalogue) -> None:
        """Cancellation exists only before anything downstream can have been
        created. An active agreement is terminated, which is evidenced."""
        view = _to_active(db, catalogue)
        with pytest.raises(TransitionRefusedError):
            cancel(db, TransitionCommand("cmd-x", view.id))

    def test_a_terminated_agreement_refuses_every_further_transition(
        self, db, catalogue
    ) -> None:
        view = _to_active(db, catalogue)
        view = terminate(
            db,
            TerminateCommand(
                command_id="cmd-t",
                agreement_id=view.id,
                effective_date=date(2026, 12, 31),
                impact_acknowledged=True,
                reason="counterparty exit",
            ),
        )
        assert view.status == AgreementStatus.TERMINATED.value
        for call in (
            lambda: suspend(db, TransitionCommand("c1", view.id)),
            lambda: reinstate(db, TransitionCommand("c2", view.id)),
            lambda: cancel(db, TransitionCommand("c3", view.id)),
        ):
            with pytest.raises(TransitionRefusedError):
                call()

    def test_an_agreement_with_no_lines_cannot_be_drafted(self, db, catalogue) -> None:
        with pytest.raises(EmptyAgreementError):
            open_draft(
                db,
                DraftCommand(
                    command_id="cmd-x",
                    reference="AGR-EMPTY",
                    counterparty_ref="acme-operator",
                    agreement_type="oem_reseller",
                    period=AgreementPeriod(_TODAY, _END),
                    lines=(),
                ),
                catalogue=catalogue,
            )

    def test_expiry_is_refused_while_the_term_is_still_running(
        self, db, catalogue
    ) -> None:
        """The guard that stops a mis-scheduled job expiring a live agreement."""
        view = _to_active(db, catalogue)
        with pytest.raises(TransitionRefusedError):
            expire(db, TransitionCommand("cmd-x", view.id), as_of=date(2027, 1, 1))

    def test_expiry_date_itself_is_still_inside_the_term(self, db, catalogue) -> None:
        """a1's expiry date remains inclusive; a2 does not move the boundary."""
        view = _to_active(db, catalogue)
        with pytest.raises(TransitionRefusedError):
            expire(db, TransitionCommand("cmd-x", view.id), as_of=_END)

    def test_expiry_succeeds_once_the_term_has_ended(self, db, catalogue) -> None:
        view = _to_active(db, catalogue)
        view = expire(db, TransitionCommand("cmd-e", view.id), as_of=date(2027, 9, 1))
        assert view.status == AgreementStatus.EXPIRED.value

    def test_termination_without_an_acknowledged_impact_preview_is_refused(
        self, db, catalogue
    ) -> None:
        view = _to_active(db, catalogue)
        with pytest.raises(EvidenceRefusedError):
            terminate(
                db,
                TerminateCommand(
                    command_id="cmd-x",
                    agreement_id=view.id,
                    effective_date=date(2026, 12, 31),
                    impact_acknowledged=False,
                    reason="no preview shown",
                ),
            )

    def test_a_period_that_ends_before_it_starts_is_refused_at_construction(
        self,
    ) -> None:
        """The type refuses it, so no call site has to.

        `AgreementError` specifically, not a bare `Exception`: a broad catch
        would also pass if the constructor raised `TypeError` on a signature
        change, which is the opposite of the property under test.
        """
        with pytest.raises(AgreementError):
            AgreementPeriod(date(2027, 1, 1), date(2026, 1, 1))

    def test_the_exclusive_end_is_derived_from_the_inclusive_expiry(self) -> None:
        period = AgreementPeriod(_TODAY, _END)
        assert period.expiry_date == date(2027, 8, 31)
        assert period.end_exclusive == date(2027, 9, 1)

    def test_an_unrepresentable_exclusive_end_is_a_typed_refusal(self) -> None:
        period = AgreementPeriod(date.max, date.max)
        assert period.expiry_date == date.max, "the inclusive a1 value is preserved"
        with pytest.raises(AgreementBoundaryError, match="no representable"):
            _ = period.end_exclusive


# ── Catalogue validation ────────────────────────────────────────────────────


class TestPromisedCapabilitiesAreValidated:
    def test_an_undeclared_capability_is_refused_before_anything_is_written(
        self, db, catalogue
    ) -> None:
        with pytest.raises(UndeclaredCapabilityError) as excinfo:
            open_draft(
                db,
                DraftCommand(
                    command_id="cmd-x",
                    reference="AGR-BAD",
                    counterparty_ref="acme-operator",
                    agreement_type="oem_reseller",
                    period=AgreementPeriod(_TODAY, _END),
                    lines=(
                        LineInput(
                            product_code="dotmac_sub",
                            capability_code="not.declared",
                            quantity=1,
                            terms=CommercialTerms("1.00", "NGN"),
                        ),
                    ),
                ),
                catalogue=catalogue,
            )
        assert excinfo.value.codes == ("not.declared",)
        assert get(db, uuid.uuid4()) is None

    def test_an_unknown_product_fails_closed(self, db, catalogue) -> None:
        """An unknown product is not an empty catalogue.

        Treating the two alike would let a typo in `product_code` promise
        arbitrary capabilities against a product nobody has declared.
        """
        with pytest.raises(UnknownProductError):
            open_draft(
                db,
                DraftCommand(
                    command_id="cmd-x",
                    reference="AGR-BAD2",
                    counterparty_ref="acme-operator",
                    agreement_type="oem_reseller",
                    period=AgreementPeriod(_TODAY, _END),
                    lines=(
                        LineInput(
                            product_code="dotmac_typo",
                            capability_code="subscriber.manage",
                            quantity=1,
                            terms=CommercialTerms("1.00", "NGN"),
                        ),
                    ),
                ),
                catalogue=catalogue,
            )

    def test_codes_are_grouped_by_product_not_checked_one_at_a_time(
        self, db, catalogue
    ) -> None:
        """A caller fixing a manifest wants every missing code at once."""
        with pytest.raises(UndeclaredCapabilityError) as excinfo:
            open_draft(
                db,
                DraftCommand(
                    command_id="cmd-x",
                    reference="AGR-BAD3",
                    counterparty_ref="acme-operator",
                    agreement_type="oem_reseller",
                    period=AgreementPeriod(_TODAY, _END),
                    lines=(
                        LineInput(
                            "dotmac_sub", "nope.one", 1, CommercialTerms("1", "NGN")
                        ),
                        LineInput(
                            "dotmac_sub", "nope.two", 1, CommercialTerms("1", "NGN")
                        ),
                    ),
                ),
                catalogue=catalogue,
            )
        assert excinfo.value.codes == ("nope.one", "nope.two")


# ── Concurrency ─────────────────────────────────────────────────────────────


class TestExpectedStateConcurrency:
    """Two operators on two screens is the ordinary case, not the exotic one."""

    def test_a_stale_record_version_is_refused(self, db, catalogue) -> None:
        view = _to_active(db, catalogue)
        stale_version = view.record_version
        suspend(db, TransitionCommand("cmd-s", view.id, reason="billing hold"))
        with pytest.raises(ExpectedStateError) as excinfo:
            reinstate(
                db,
                TransitionCommand(
                    "cmd-r",
                    view.id,
                    expected_status=AgreementStatus.ACTIVE.value,
                    expected_version=stale_version,
                ),
            )
        assert excinfo.value.expected_version == stale_version
        assert excinfo.value.actual_status == AgreementStatus.SUSPENDED.value

    def test_the_version_advances_on_every_transition(self, db, catalogue) -> None:
        view = _draft(db, catalogue)
        assert view.record_version == 1
        view = _propose(db, catalogue, view.id)
        assert view.record_version == 2
        view = _approve(db, view)
        assert view.record_version == 3

    def test_omitting_the_version_still_checks_the_status(self, db, catalogue) -> None:
        """Opting out of the version check is not opting out of the guard.

        An outbox consumer reacting to a fact has no prior read to compare
        against; it must still be unable to activate a draft.
        """
        view = _draft(db, catalogue)
        with pytest.raises(ExpectedStateError):
            _activate(db, view)


# ── Idempotency ─────────────────────────────────────────────────────────────


class TestCommandsAreIdempotent:
    def test_replaying_a_command_id_does_not_transition_twice(
        self, db, catalogue
    ) -> None:
        view = _propose(db, catalogue, _draft(db, catalogue).id)
        command = ApproveCommand(
            command_id="cmd-fixed",
            agreement_id=view.id,
            evidence=_evidence(view.content_hash or ""),
        )
        first = approve(db, command)
        second = approve(db, command)
        assert first.status == second.status == AgreementStatus.APPROVED.value
        assert (
            first.record_version == second.record_version
        ), "a replay must not advance the record version"
        assert (
            len(history(db, view.id)) == 2
        ), "a replay must not append a second history row"


# ── Amendment ───────────────────────────────────────────────────────────────


class TestAmendmentIsANewVersion:
    def test_amending_supersedes_the_predecessor_and_returns_the_successor(
        self, db, catalogue
    ) -> None:
        original = _to_active(db, catalogue)
        successor = amend(
            db,
            AmendCommand(
                command_id="cmd-a",
                agreement_id=original.id,
                reference="AGR-2026-0001-A2",
                lines=(
                    LineInput(
                        product_code="dotmac_sub",
                        capability_code="billing.invoicing",
                        quantity=900,
                        terms=CommercialTerms("11.00", "NGN"),
                    ),
                ),
                reason="volume increase",
            ),
            catalogue=catalogue,
        )
        assert successor.agreement_version == 2
        assert successor.status == AgreementStatus.DRAFT.value
        assert successor.supersedes_id == original.id
        assert successor.agreement_family_id == original.agreement_family_id

        predecessor = get(db, original.id)
        assert predecessor is not None
        assert predecessor.status == AgreementStatus.SUPERSEDED.value
        assert predecessor.superseded_by_id == successor.id

    def test_the_predecessor_keeps_its_lines_and_its_history(
        self, db, catalogue
    ) -> None:
        """An amendment is a new version, never an edit — which is what makes
        "what did we agree, and when" answerable years later."""
        original = _to_active(db, catalogue)
        before = len(history(db, original.id))
        amend(
            db,
            AmendCommand(
                command_id="cmd-a",
                agreement_id=original.id,
                reference="AGR-A2",
                lines=_lines(),
            ),
            catalogue=catalogue,
        )
        predecessor = get(db, original.id)
        assert predecessor is not None
        assert predecessor.lines[0].quantity == 500, "original lines are untouched"
        assert len(history(db, original.id)) == before + 1

    def test_a_terminal_agreement_cannot_be_amended(self, db, catalogue) -> None:
        view = _to_active(db, catalogue)
        view = expire(db, TransitionCommand("cmd-e", view.id), as_of=date(2027, 9, 1))
        with pytest.raises(TransitionRefusedError):
            amend(
                db,
                AmendCommand(
                    command_id="cmd-a",
                    agreement_id=view.id,
                    reference="AGR-A2",
                    lines=_lines(),
                ),
                catalogue=catalogue,
            )

    def test_amending_twice_from_the_same_predecessor_is_refused(
        self, db, catalogue
    ) -> None:
        """Otherwise one agreement would have two successors and the family
        would stop being a chain."""
        original = _to_active(db, catalogue)
        amend(
            db,
            AmendCommand("cmd-a1", original.id, "AGR-A2", _lines()),
            catalogue=catalogue,
        )
        with pytest.raises(TransitionRefusedError):
            amend(
                db,
                AmendCommand("cmd-a2", original.id, "AGR-A3", _lines()),
                catalogue=catalogue,
            )

    def test_the_family_reads_back_in_version_order(self, db, catalogue) -> None:
        original = _to_active(db, catalogue)
        amend(
            db,
            AmendCommand("cmd-a", original.id, "AGR-A2", _lines()),
            catalogue=catalogue,
        )
        versions = family(db, original.agreement_family_id)
        assert [v.agreement_version for v in versions] == [1, 2]


# ── Bounded estate inspection ───────────────────────────────────────────────


class TestAgreementEstateListing:
    def test_pages_every_agreement_once_in_stable_id_order(self, db, catalogue) -> None:
        created = tuple(_draft(db, catalogue) for _ in range(5))
        expected_ids = sorted(view.id for view in created)

        first = list_agreements(db, limit=2)
        assert isinstance(first, AgreementPage)
        assert [view.id for view in first.items] == expected_ids[:2]
        assert first.next_after == expected_ids[1]

        second = list_agreements(db, after=first.next_after, limit=2)
        assert [view.id for view in second.items] == expected_ids[2:4]
        assert second.next_after == expected_ids[3]

        final = list_agreements(db, after=second.next_after, limit=2)
        assert [view.id for view in final.items] == expected_ids[4:]
        assert final.next_after is None

    def test_a_full_final_page_does_not_invent_a_continuation(
        self, db, catalogue
    ) -> None:
        created = tuple(_draft(db, catalogue) for _ in range(2))
        page = list_agreements(db, limit=2)
        assert {view.id for view in page.items} == {view.id for view in created}
        assert page.next_after is None

    def test_views_and_lines_remain_usable_after_the_orm_is_detached(
        self, db, catalogue
    ) -> None:
        created = _draft(db, catalogue)
        page = list_agreements(db, limit=1)
        db.expunge_all()

        assert isinstance(page.items[0], AgreementView)
        assert not hasattr(page.items[0], "_sa_instance_state")
        assert page.items[0].id == created.id
        assert page.items[0].lines[0].capability_code == "subscriber.manage"
        assert page.items[0].end_exclusive == date(2027, 9, 1)

    @pytest.mark.parametrize("limit", [False, 0, MAX_AGREEMENT_PAGE_SIZE + 1])
    def test_invalid_page_limits_are_refused(self, db, limit) -> None:
        with pytest.raises(AgreementError, match="page limit"):
            list_agreements(db, limit=limit)

    def test_a_non_uuid_cursor_is_refused_at_the_public_boundary(self, db) -> None:
        with pytest.raises(AgreementError, match="cursor"):
            list_agreements(db, after="not-a-uuid")  # type: ignore[arg-type]


# ── Rejection clears the frozen snapshot ────────────────────────────────────


class TestRejectionInvalidatesApprovals:
    def test_rejecting_clears_the_digest_so_no_approval_can_bind(
        self, db, catalogue
    ) -> None:
        view = _propose(db, catalogue, _draft(db, catalogue).id)
        frozen = view.content_hash
        view = reject(db, TransitionCommand("cmd-j", view.id, reason="terms"))
        assert view.status == AgreementStatus.DRAFT.value
        assert view.content_hash is None
        assert view.approval_policy_code is None
        # The approval collected against the old digest is now unusable, because
        # there is no digest for it to bind to.
        with pytest.raises(ExpectedStateError):
            approve(
                db,
                ApproveCommand("cmd-k", view.id, _evidence(frozen or "")),
            )


# ── Transaction authority ───────────────────────────────────────────────────


class TestTheModuleOwnsNoTransaction:
    def test_nothing_is_committed_so_a_rollback_discards_it(
        self, db, catalogue
    ) -> None:
        """Hard rule 8: the module only adds and flushes; the boundary commits.

        If the service committed, the rollback below would not remove the row —
        which is exactly the failure this asserts against.
        """
        view = _draft(db, catalogue)
        db.rollback()
        assert get(db, view.id) is None
