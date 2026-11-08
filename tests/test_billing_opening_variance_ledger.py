"""ST-4c opening variance ledger against a real PostgreSQL (tenant plane).

Covers record-at-import, the control report, the SQL closure functions, the
database maker-checker and resolution guards, import reversal voiding, and
refusal of a manifest that does not close at window admission.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from dotmac_billing import service as s
from dotmac_billing.commands import IssueCreditNote
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_commands import ReverseOpeningAccountImportV1
from dotmac_billing.opening_contracts import OpeningSourceIdentityV1
from dotmac_billing.opening_variance import (
    list_opening_variances,
    opening_control_report,
    require_resolution_primitive,
)
from dotmac_billing.opening_variance_contracts import (
    OpeningVarianceApprovalV1,
    OpeningVarianceResolutionRefV1,
    OpeningVarianceV1,
    RecogniseOpeningVarianceV1,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests import billing_opening_variance_fixtures as variance_fixture
from tests import test_billing_isolation as b
from tests import test_billing_opening_cash_import as cash
from tests import test_billing_opening_hold_admission as hold
from tests import test_billing_opening_receivable_import as ar
from tests.unit import test_billing_opening_receivable_contracts as evidence

scratch = hold.scratch

DIGEST = variance_fixture.DIGEST
RULES = variance_fixture.RULES


class VarianceApprovals:
    """Fixture port; production maps the Approvals module's public hold contract."""

    def __init__(self, maker: str = "maker:v", checkers=("checker:v",)):
        self.maker, self.checkers = maker, tuple(checkers)

    def hold_approval(
        self, db, *, scope, request_id, subject_type, subject_id, content_digest
    ):
        return OpeningVarianceApprovalV1(
            request_id,
            subject_type,
            subject_id,
            content_digest,
            self.maker,
            self.checkers,
        )


def _variance(part, **kw) -> OpeningVarianceV1:
    values = {
        "level": "partition",
        "partition": part,
        "currency": part.currency,
        "precision": part.precision,
        "lane": "receivable",
        "evidence_digest": DIGEST,
        "rules_digest": RULES,
    }
    values.update(kw)
    kind, key = values.pop("source")
    return OpeningVarianceV1(source=OpeningSourceIdentityV1(kind, key), **values)


def _carrying_command(session, scope, *, exact: bool = False, gap: bool = False):
    """A mixed lot + receivable partition carrying a ledger and a memo variance."""
    account = cash._new_account(session, scope)
    cmd = ar._command(session, scope, account)
    manifest = cmd.manifest
    total = sum(i.residual_minor for i in manifest.receivables)
    part = cmd.partition
    if exact:
        return cmd
    adverse = _variance(
        part,
        basis="ledger_lane",
        reason_code="lane_document_difference",
        source=("lane", part.source_account_key),
        amount_minor=-700,
    )
    positive = _variance(
        part,
        basis="ledger_lane",
        reason_code="lane_document_difference",
        source=("invoice", manifest.receivables[1].source_identity.key),
        amount_minor=300,
    )
    memo = _variance(
        part,
        basis="stored_balance",
        reason_code="stored_balance_difference",
        source=("invoice", manifest.receivables[0].source_identity.key),
        amount_minor=500,
    )
    manifest = replace(
        manifest,
        controls=tuple(
            replace(
                c,
                collectible_receivable_minor=total - 400,
                open_ar_minor=total - 400,
                written_off_total_minor=9,
            )
            for c in manifest.controls
        ),
        variances=(adverse, positive, memo),
    )
    measures = variance_fixture.measures_for(manifest)
    manifest = replace(
        manifest,
        measures=tuple(
            replace(m, stored_balance_minor=m.stored_balance_minor + 500)
            for m in measures
        ),
    )
    if gap:  # E4 cannot be formed: a typed control gap, never a blocker
        manifest = replace(
            manifest,
            measures=tuple(
                replace(m, rebuild_minor=None, rebuild_gap_reason="a10_missing")
                for m in manifest.measures
            ),
        )
    return replace(cmd, manifest=manifest)


def _imported(session, scope, *, seal: bool = True, gap: bool = False):
    cmd = _carrying_command(session, scope, gap=gap)
    cash._prepare_window(session, scope, cmd)
    session.commit()
    receipt = cash._import_command(
        session,
        scope=scope,
        command=cmd,
        verifier=evidence.TrustedVerifier(),
        version=2,
    )
    session.commit()
    if seal:
        cash._seal_window(session, scope)
        session.commit()
    return cmd, receipt


def _scope(scratch, plane="tenant"):
    return cash.runtime._scope_url(scratch, plane)


def _ids(session, scope):
    records, _ = list_opening_variances(session, scope=scope)
    by_amount = {r.amount_minor: r for r in records}
    return records, by_amount


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_import_records_variances_and_the_control_report_closes(scratch, plane):
    scope, url = _scope(scratch, plane)
    with cash._session(scratch, url, scope) as session:
        _imported(session, scope)
        records, by_amount = _ids(session, scope)
        assert sorted(r.amount_minor for r in records) == [-700, 300, 500]
        assert {r.state for r in records} == {"recorded"}
        assert by_amount[-700].customer_adverse and not by_amount[300].customer_adverse
        page, _ = list_opening_variances(
            session, scope=scope, open_customer_adverse=True
        )
        assert [r.amount_minor for r in page] == [-700]
        assert (
            list_opening_variances(
                session, scope=scope, account_id=uuid4(), open_customer_adverse=True
            )[0]
            == ()
        )
        report = opening_control_report(session, scope=scope)
        assert report.closed and all(r.difference_minor == 0 for r in report.rows)
        assert [
            (h.field, h.currency, h.total_minor, h.nonzero_accounts)
            for h in report.history
        ] == [
            ("adjustment_total_minor", "NGN", 0, 0),
            ("prepaid_funding_consumed_minor", "NGN", 0, 0),
            ("refunded_total_minor", "NGN", 0, 0),
            ("written_off_total_minor", "NGN", 9, 1),
        ]
        assert len(report.digest) == 64
        assert (
            report.customer_adverse_count == 1 and report.customer_adverse_minor == -700
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_replay_adds_no_variance_rows(scratch, plane):
    scope, url = _scope(scratch, plane)
    prefix = "platform_" if plane == "platform" else ""

    def count(session, table: str) -> int:
        return session.scalar(
            text(f"SELECT count(*) FROM mod_billing.{prefix}{table}")  # noqa: S608
        )

    with cash._session(scratch, url, scope) as session:
        cmd, receipt = _imported(session, scope, seal=False)
        before = (
            count(session, "opening_variances"),
            count(session, "opening_variance_events"),
        )
        again = cash._import_command(
            session,
            scope=scope,
            command=cmd,
            verifier=evidence.TrustedVerifier(),
            version=2,
        )
        session.commit()
        assert again == receipt
        assert before == (3, 3)
        assert (
            count(session, "opening_variances"),
            count(session, "opening_variance_events"),
        ) == before


def _defects(session, scope, account: UUID, change: str | None = None) -> int:
    manifest = "w.evidence::jsonb->'window'->'manifest'"
    if change is not None:
        manifest = change.replace("{m}", manifest)
    # test-only SQL: the manifest expression is a fixed literal built above
    return session.scalar(
        text(
            "SELECT mod_billing.opening_closure_defects("  # noqa: S608
            f"w.tenant_id, w.id, {manifest}, ARRAY[:a], true) "
            "FROM mod_billing.opening_windows w"
        ),
        {"a": str(account)},
    )


def test_sql_closure_refuses_a_single_minor_unit_in_every_equation(scratch):
    scope, url = _scope(scratch)
    with cash._session(scratch, url, scope) as session:
        cmd, _receipt = _imported(session, scope)
        account = cmd.partition.account_id
        assert _defects(session, scope, account) == 0
        bump = "jsonb_set({m}, '{PATH}', to_jsonb((({m}#>>'{PATH}'))::bigint + 1))"
        for path in (
            "controls,0,collectible_receivable_minor",
            "controls,0,unapplied_customer_credit_minor",
            "measures,0,document_residual_minor",
            "measures,0,stored_balance_minor",
            "measures,0,rebuild_minor",
        ):
            change = bump.replace("PATH", path)
            assert _defects(session, scope, account, change) >= 1, path


def test_the_seal_closure_covers_a_control_partition_nobody_imported(scratch):
    """Finding 5: a control partition with a figure, no T and no variance."""
    scope, url = _scope(scratch)
    with cash._session(scratch, url, scope) as session:
        cmd, _receipt = _imported(session, scope, seal=False)
        stranger = uuid4()
        orphan = (
            "jsonb_set({m}, '{controls}', ({m}->'controls') || jsonb_build_array("
            "jsonb_build_object('partition', jsonb_build_object("
            f"'account_id', '{stranger}'), 'collectible_receivable_minor', 500,"
            "'unapplied_customer_credit_minor', 0, "
            "'prepaid_funding_reserved_minor', 0)))"
        )
        assert _defects(session, scope, stranger, orphan) >= 1


def _event(session, window_id, variance, **values):
    base = {
        "id": uuid4(),
        "t": variance["tenant_id"],
        "w": window_id,
        "v": variance["id"],
        "a": variance["billing_account_id"],
        "kind": "resolving",
        "rk": None,
        "amount": None,
        "gl": None,
        "ev": None,
        "maker": "maker:v",
        "refs": '["checker:v"]',
        "req": uuid4(),
        "actor": "maker:v",
    }
    base.update(values)
    session.execute(
        text(
            "INSERT INTO mod_billing.opening_variance_events (id, tenant_id, "
            "window_id, variance_id, billing_account_id, kind, resolution_kind, "
            "amount_minor, gl_target_class, evidence_ref, maker_ref, approver_refs, "
            "approval_request_id, actor, occurred_at) VALUES (:id, :t, :w, :v, :a, "
            ":kind, :rk, :amount, :gl, :ev, :maker, CAST(:refs AS jsonb), :req, "
            ":actor, now())"
        ),
        base,
    )


def _row(session, amount: int):
    return (
        session.execute(
            text(
                "SELECT id, tenant_id, billing_account_id, window_id "
                "FROM mod_billing.opening_variances WHERE amount_minor = :a"
            ),
            {"a": amount},
        )
        .mappings()
        .one()
    )


def _refused(session, **values) -> None:
    row = values.pop("row")
    with pytest.raises(DBAPIError) as refused:
        with session.begin_nested():
            _event(session, row["window_id"], row, **values)
    assert refused.value.orig.sqlstate == "23514"


def test_database_enforces_maker_checker_on_every_resolution_event(scratch):
    scope, url = _scope(scratch)
    with cash._session(scratch, url, scope) as session:
        _imported(session, scope)
        row = _row(session, 300)
        for bad in (
            {"refs": '["maker:v"]'},  # the maker is a checker
            {"refs": "[]"},  # no checker
            {"refs": '["c","c"]'},  # not distinct
            {"refs": "[1]"},  # not strings
            {"refs": "null"},
            {"maker": ""},
            {"req": None},
            {"actor": "someone:else"},
        ):
            _refused(session, row=row, **bad)
        _event(session, row["window_id"], row)  # a valid resolving event
        # a resolution must equal its approved resolving event
        _refused(
            session,
            row=row,
            kind="resolved",
            rk="recognised",
            amount=300,
            gl="bad_debt",
            refs='["checker:other"]',
        )


def test_database_refuses_recognise_on_adverse_and_a_bad_gl_class(scratch):
    scope, url = _scope(scratch)
    with cash._session(scratch, url, scope) as session:
        _imported(session, scope)
        for amount, gl in (
            (-700, "bad_debt"),  # customer-adverse: position_corrected only
            (300, "customer_liability"),  # wrong class for the receivable lane
        ):
            row, req = _row(session, amount), uuid4()
            _event(session, row["window_id"], row, req=req)
            _refused(
                session,
                row=row,
                kind="resolved",
                rk="recognised",
                amount=amount,
                gl=gl,
                req=req,
            )
        # the same resolution with a valid class on a non-adverse variance passes
        row = _row(session, 300)
        _event(
            session,
            row["window_id"],
            row,
            kind="resolved",
            rk="recognised",
            amount=300,
            gl="bad_debt",
            req=session.execute(
                text(
                    "SELECT approval_request_id "
                    "FROM mod_billing.opening_variance_events "
                    "WHERE variance_id = :v AND kind = 'resolving'"
                ),
                {"v": row["id"]},
            ).scalar_one(),
        )


def test_service_recognise_refuses_adverse_and_memo_and_enforces_maker_checker(
    scratch,
):
    scope, url = _scope(scratch)
    with cash._session(scratch, url, scope) as session:
        _imported(session, scope)
        records, by_amount = _ids(session, scope)

        def command(variance, amount, gl="bad_debt", maker="maker:v"):
            return RecogniseOpeningVarianceV1(
                scope,
                variance.variance_id,
                amount,
                gl,
                uuid4(),
                maker,
                f"recognise:{uuid4()}",
                evidence.NOW,
            )

        approvals = VarianceApprovals()
        for variance, amount in ((by_amount[-700], -700), (by_amount[500], 500)):
            with pytest.raises(BillingRuleViolation) as refused:
                s.recognise_opening_variance(
                    session,
                    scope=scope,
                    command=command(variance, amount),
                    approvals=approvals,
                )
            assert refused.value.code.endswith("variance_recognise_adverse")
            session.rollback()
        with pytest.raises(BillingRuleViolation) as refused:
            s.recognise_opening_variance(
                session,
                scope=scope,
                command=command(by_amount[300], 300),
                approvals=VarianceApprovals(checkers=("maker:v",)),
            )
        assert refused.value.code.endswith("opening_variance_maker_checker")
        session.rollback()
        with pytest.raises(BillingRuleViolation) as refused:
            s.recognise_opening_variance(
                session,
                scope=scope,
                command=command(by_amount[300], 300),
                approvals=None,
            )
        assert refused.value.code.endswith("opening_verifier_unavailable")
        session.rollback()
        assert (
            s.recognise_opening_variance(
                session,
                scope=scope,
                command=command(by_amount[300], 300),
                approvals=approvals,
            )
            == "resolved"
        )
        session.commit()
        after, _ = _ids(session, scope)
        resolved = {r.amount_minor: r for r in after}[300]
        assert resolved.state == "resolved" and resolved.resolved_minor == 300
        # nothing re-resolves a resolved variance
        with pytest.raises(BillingRuleViolation):
            s.recognise_opening_variance(
                session,
                scope=scope,
                command=command(by_amount[300], 300),
                approvals=approvals,
            )


def test_a_credit_note_or_grant_resolves_only_a_receivable_adverse_variance(scratch):
    scope, url = _scope(scratch)
    with cash._session(scratch, url, scope) as session:
        _imported(session, scope)
        _records, by_amount = _ids(session, scope)

        def check(amount, primitive, apply_on_issue):
            require_resolution_primitive(
                session,
                scope=scope,
                variance_id=by_amount[amount].variance_id,
                primitive=primitive,
                apply_on_issue=apply_on_issue,
            )

        check(-700, "credit_note", False)
        check(-700, "grant", True)
        for amount, primitive, apply in (
            (-700, "grant", False),  # a grant needs apply_on_issue
            (300, "credit_note", False),  # V > 0 has no lane effect to correct
            (500, "credit_note", False),  # memo basis resolves by evidence only
        ):
            with pytest.raises(BillingRuleViolation) as refused:
                check(amount, primitive, apply)
            assert refused.value.code.endswith(
                "variance_resolution_lane_effect_missing"
            )


def test_import_reversal_voids_the_variances_and_leaves_t(scratch):
    scope, url = _scope(scratch)
    with cash._session(scratch, url, scope) as session:
        cmd, receipt = _imported(session, scope, seal=False)
        assert _defects(session, scope, cmd.partition.account_id) == 0
        with cash._as_opening_principal(session):
            s.reverse_opening_account_import_v2(
                session,
                scope=scope,
                command=ReverseOpeningAccountImportV1(
                    scope,
                    receipt.receipt_id,
                    "fresh-migration-authority",
                    str(uuid4()),
                    evidence.NOW,
                ),
                verifier=evidence.TrustedVerifier(),
            )
        session.commit()
        records, _ = _ids(session, scope)
        assert {r.state for r in records} == {"voided"}
        # the unwound import is no longer T, and no variance stands for it
        assert _defects(session, scope, cmd.partition.account_id) >= 1
        report = opening_control_report(session, scope=scope)
        assert not report.closed
        assert all(r.variance_minor == 0 for r in report.rows)
        assert all(r.transferred_minor == 0 for r in report.rows)


def test_a_manifest_that_does_not_close_never_becomes_a_window(scratch):
    scope, url = _scope(scratch)
    with cash._session(scratch, url, scope) as session:
        cmd = _carrying_command(session, scope)
        stale = replace(
            cmd.manifest,
            measures=tuple(
                replace(m, document_residual_minor=m.document_residual_minor + 1)
                for m in cmd.manifest.measures
            ),
        )
        with pytest.raises(BillingRuleViolation) as refused:
            cash._prepare_window(session, scope, replace(cmd, manifest=stale))
        assert refused.value.code.endswith("opening_manifest_unclosed")
        session.rollback()
        a10_shaped = replace(cmd.manifest, measures=())
        with pytest.raises(BillingRuleViolation) as refused:
            cash._prepare_window(session, scope, replace(cmd, manifest=a10_shaped))
        assert refused.value.code.endswith("opening_manifest_unclosed")


def test_a_missing_rebuild_figure_opens_a_gap_whose_resolution_needs_maker_checker(
    scratch,
):
    scope, url = _scope(scratch)
    with cash._session(scratch, url, scope) as session:
        cmd, _receipt = _imported(session, scope, gap=True)
        assert _defects(session, scope, cmd.partition.account_id) == 0
        gap = (
            session.execute(
                text(
                    "SELECT id, tenant_id, window_id, billing_account_id "
                    "FROM mod_billing.opening_control_gaps"
                )
            )
            .mappings()
            .one()
        )

        def insert(**values):
            row = {
                "id": uuid4(),
                "t": gap["tenant_id"],
                "w": gap["window_id"],
                "g": gap["id"],
                "a": gap["billing_account_id"],
                "ev": "rebuild-run:1",
                "maker": "maker:g",
                "refs": '["checker:g"]',
                "req": uuid4(),
                "actor": "maker:g",
            }
            row.update(values)
            session.execute(
                text(
                    "INSERT INTO mod_billing.opening_control_gap_events (id, "
                    "tenant_id, window_id, gap_id, billing_account_id, kind, "
                    "evidence_ref, maker_ref, approver_refs, approval_request_id, "
                    "actor, occurred_at) VALUES (:id, :t, :w, :g, :a, 'resolved', "
                    ":ev, :maker, CAST(:refs AS jsonb), :req, :actor, now())"
                ),
                row,
            )

        for bad in (
            {"refs": '["maker:g"]'},
            {"refs": "[]"},
            {"ev": None},
            {"req": None},
            {"actor": "someone:else"},
        ):
            with pytest.raises(DBAPIError) as refused:
                with session.begin_nested():
                    insert(**bad)
            assert refused.value.orig.sqlstate == "23514"
        insert()  # a valid, approved resolution


def _credit_note(document_id, amount: str, **overrides):
    return replace(
        IssueCreditNote(
            document_id,
            b._money(amount),
            b._money("0"),
            b._money(amount),
            "evidenced correction",
            "credit",
            evidence.NOW.date(),
            "billing-test",
            str(uuid4()),
            evidence.NOW,
            "credit.standard",
            "1",
        ),
        **overrides,
    )


def test_a_credit_note_resolves_an_adverse_variance_only_behind_the_approvals_port(
    scratch,
):
    """Findings 1 and 8: no approvals port, no resolution; a resolving posting
    group can never be reversed."""
    scope, url = _scope(scratch)
    with cash._session(scratch, url, scope) as session:
        _cmd, receipt = _imported(session, scope)
        _records, by_amount = _ids(session, scope)
        adverse = by_amount[-700]
        first, _second = ar._bases(session, scope, receipt)
        ref = OpeningVarianceResolutionRefV1(
            adverse.variance_id, -700, uuid4(), "maker:v"
        )
        command = _credit_note(first.document_id, "7", resolves_variance_ref=ref)
        with pytest.raises(BillingRuleViolation) as refused:
            s.issue_credit_note(
                session,
                scope=scope,
                command=command,
                numbering=b.NUMBERING,
                approvals=None,
            )
        assert refused.value.code.endswith("opening_verifier_unavailable")
        session.rollback()
        with pytest.raises(BillingRuleViolation) as refused:  # maker is a checker
            s.issue_credit_note(
                session,
                scope=scope,
                command=command,
                numbering=b.NUMBERING,
                approvals=VarianceApprovals(checkers=("maker:v",)),
            )
        assert refused.value.code.endswith("opening_variance_maker_checker")
        session.rollback()
        note = s.issue_credit_note(
            session,
            scope=scope,
            command=command,
            numbering=b.NUMBERING,
            approvals=VarianceApprovals(),
        )
        session.commit()
        after, _ = _ids(session, scope)
        resolved = {r.amount_minor: r for r in after}[-700]
        assert resolved.state == "resolved" and resolved.resolved_minor == -700
        group = session.scalar(
            text(
                "SELECT posting_group_id FROM mod_billing.opening_variance_events "
                "WHERE variance_id = :v AND kind = 'resolved'"
            ),
            {"v": adverse.variance_id},
        )
        assert group is not None and note.id is not None
        # the resolving group is locked: reversing it is refused, fail closed
        with pytest.raises(BillingRuleViolation) as locked:
            s.reverse_posting_group(
                session,
                scope=scope,
                command=cash.ReversePostingGroupCommand(
                    group, evidence.NOW, str(uuid4())
                ),
            )
        assert locked.value.code.endswith("variance_resolution_locked")
        session.rollback()
        # a credit note that resolves a memo variance has no lane effect: refused
        memo_ref = OpeningVarianceResolutionRefV1(
            by_amount[500].variance_id, 500, uuid4(), "maker:v"
        )
        with pytest.raises(BillingRuleViolation) as refused:
            s.issue_credit_note(
                session,
                scope=scope,
                command=_credit_note(
                    first.document_id, "5", resolves_variance_ref=memo_ref
                ),
                numbering=b.NUMBERING,
                approvals=VarianceApprovals(),
            )
        assert refused.value.code.endswith("variance_resolution_lane_effect_missing")


def test_database_refuses_over_resolution_wrong_sign_and_wrong_kind_for_the_basis(
    scratch,
):
    scope, url = _scope(scratch)
    with cash._session(scratch, url, scope) as session:
        _imported(session, scope)
        ledger, memo = _row(session, 300), _row(session, 500)
        for row, amount in ((ledger, 300), (memo, 500)):
            _event(session, row["window_id"], row, req=uuid4())
            req = session.execute(
                text(
                    "SELECT approval_request_id FROM mod_billing.opening_variance_events "  # noqa: E501
                    "WHERE variance_id = :v AND kind = 'resolving'"
                ),
                {"v": row["id"]},
            ).scalar_one()
            for bad in (
                {"amount": amount + 1, "kind": "resolved"},  # over-resolves
                {"amount": -amount, "kind": "resolved"},  # wrong sign
                {"amount": amount - 1, "kind": "resolved"},  # partial named resolved
                {"amount": amount, "kind": "partially_resolved"},  # full named partial
            ):
                _refused(
                    session,
                    row=row,
                    rk="recognised" if row is ledger else "evidence_confirmed",
                    gl="bad_debt" if row is ledger else None,
                    ev=None if row is ledger else "evidence:1",
                    req=req,
                    **bad,
                )
        # evidence_confirmed never touches the authoritative ledger lane, and a
        # memo basis is never recognised into the GL
        _refused(
            session,
            row=ledger,
            kind="resolved",
            rk="evidence_confirmed",
            amount=300,
            ev="evidence:1",
            req=session.execute(
                text(
                    "SELECT approval_request_id FROM mod_billing.opening_variance_events "  # noqa: E501
                    "WHERE variance_id = :v AND kind = 'resolving'"
                ),
                {"v": ledger["id"]},
            ).scalar_one(),
        )
        _refused(
            session,
            row=memo,
            kind="resolved",
            rk="recognised",
            amount=500,
            gl="bad_debt",
            req=session.execute(
                text(
                    "SELECT approval_request_id FROM mod_billing.opening_variance_events "  # noqa: E501
                    "WHERE variance_id = :v AND kind = 'resolving'"
                ),
                {"v": memo["id"]},
            ).scalar_one(),
        )


def test_the_seal_set_check_refuses_a_recorded_set_that_differs_from_the_manifest(
    scratch,
):
    scope, url = _scope(scratch)
    with cash._session(scratch, url, scope) as session:
        cmd, _receipt = _imported(session, scope, seal=False)

        def matches(change: str | None) -> bool:
            manifest = "w.evidence::jsonb->'window'->'manifest'"
            if change is not None:
                manifest = change.replace("{m}", manifest)
            return session.scalar(
                text(
                    "SELECT mod_billing.opening_variance_set_matches("  # noqa: S608
                    f"w.tenant_id, w.id, {manifest}, ARRAY[:a], true) "
                    "FROM mod_billing.opening_windows w"
                ),
                {"a": str(cmd.partition.account_id)},
            )

        assert matches(None) is True
        assert (
            matches(
                "jsonb_set({m}, '{variances,0,amount_minor}', "
                "to_jsonb((({m}#>>'{variances,0,amount_minor}'))::bigint + 1))"
            )
            is False
        )
        assert matches("jsonb_set({m}, '{variances}', '[]'::jsonb)") is False
