"""Hosted PostgreSQL proofs: sealed-hold admission (D-5) and the seal participant.

Fixture ports qualify injected evidence only; they are not production authority.
"""

# SQL identifiers come only from fixed table names.
# ruff: noqa: S608
import json
from dataclasses import fields, replace
from uuid import UUID, uuid4

import pytest
from dotmac_billing import opening_hold_contracts as hold
from dotmac_billing import service as s
from dotmac_billing.errors import BillingConflict, BillingRuleViolation
from dotmac_billing.opening_contracts import OpeningEvidenceError
from dotmac_billing.opening_lane import (
    admit_held_opening_lane_partition,
    import_opening_lane_partition,
    seal_opening_lane,
)
from dotmac_billing.opening_variance_contracts import OpeningOperationalHoldV1
from dotmac_billing.opening_window_contracts import OpeningAuthoritySwitchReceiptV1
from dotmac_billing.opening_window_queries import (
    get_opening_authority_switch_receipt,
    get_opening_hold_admissions,
    get_opening_hold_list,
    get_opening_window_admissions,
    get_opening_window_seal,
)
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests import billing_opening_window_fixtures as window_fixture
from tests import test_billing_isolation as b
from tests import test_billing_opening_cash_import as cash
from tests import test_billing_opening_receivable_import as ar
from tests.unit import test_billing_opening_receivable_contracts as evidence

MAKER = "maker:fixture"
CHECKER = "checker:fixture"
DIGEST = "sha256:" + "a" * 64


@pytest.fixture
def scratch():
    with b._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        with cash._opening_scratch(urls) as scoped:
            yield scoped


class Approvals:
    """Fixture port; production maps the Approvals module's public hold contract."""

    def __init__(self, **override):
        self.override = override
        self.calls = 0

    def hold_approval(
        self, db, *, scope, request_id, subject_type, subject_id, content_digest
    ):
        self.calls += 1
        values = {
            "request_id": request_id,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "content_digest": content_digest,
            "requester_ref": MAKER,
            "approver_refs": (CHECKER,),
        }
        values.update(self.override)
        if values.get("none"):
            return None
        return hold.HeldOpeningHoldApprovalV1(**values)


class Authority:
    def __init__(self, approvals):
        self._approvals = approvals

    def import_verifier(self):
        return evidence.TrustedVerifier()

    def seal_verifier(self):
        return window_fixture.TrustedWindowVerifier()

    def hold_approvals(self):
        return self._approvals


def _two_partitions(session, scope, dual_control="on"):
    """One window manifest freezing an admitted partition and a held one."""
    admitted_id, held_id = (
        cash._new_account(session, scope),
        cash._new_account(session, scope),
    )
    first = ar._command(session, scope, admitted_id)
    second = ar._command(session, scope, held_id)
    manifest = replace(
        first.manifest,
        population=first.manifest.population + second.manifest.population,
        dispositions=first.manifest.dispositions + second.manifest.dispositions,
        controls=first.manifest.controls + second.manifest.controls,
        receivables=first.manifest.receivables + second.manifest.receivables,
        cash_lots=first.manifest.cash_lots + second.manifest.cash_lots,
        measures=first.manifest.measures + second.manifest.measures,
        counts=replace(
            first.manifest.counts,
            **{
                f.name: getattr(first.manifest.counts, f.name)
                + getattr(second.manifest.counts, f.name)
                for f in fields(first.manifest.counts)
            },
        ),
    )
    first = replace(first, manifest=manifest)
    second = replace(second, manifest=manifest, idempotency_key=f"hold:{held_id}")
    cash._prepare_window(session, scope, first, dual_control)
    session.commit()
    return first, second


def _operational(ids):
    """The sealed hold list equals the typed operational holds (Q1, 2026-11-08)."""
    return tuple(
        OpeningOperationalHoldV1(account_id, "target_not_pristine")
        for account_id in sorted(ids, key=str)
    )


def _import(session, scope, command):
    return cash._import_command(
        session,
        scope=scope,
        command=command,
        verifier=evidence.TrustedVerifier(),
        version=2,
    )


def _seal(session, scope, *, held, participant=None, prebuilt=None, **override):
    window = session.info["opening_scratch"].windows[scope]
    seal_command = prebuilt or replace(
        window_fixture._seal_command(session, scope, window),
        held_account_ids=tuple(sorted(held, key=str)),
        operational_holds=_operational(held),
    )
    seal_command = replace(seal_command, **override)
    with cash._as_opening_principal(session):
        return s.seal_opening_window(
            session,
            scope=scope,
            command=seal_command,
            verifier=window_fixture.TrustedWindowVerifier(),
            authority_switch=participant,
        )


def _admit(session, scope, command, *, approvals=None, **override):
    window = session.info["opening_scratch"].windows[scope]
    values = {
        "scope": scope,
        "window_id": window.window_id,
        "import_command": command,
        "disposition": hold.OpeningHoldDispositionV1("disposition:fixture", DIGEST),
        "maker_ref": MAKER,
        "approval_request_id": uuid4(),
    }
    values.update(override)
    admit = hold.AdmitHeldOpeningAccountV2(**values)
    with cash._as_opening_principal(session):
        return s.admit_held_opening_account_v2(
            session,
            scope=scope,
            command=admit,
            verifier=evidence.TrustedVerifier(),
            approvals=approvals if approvals is not None else Approvals(),
        )


def _held_id(command) -> UUID:
    return command.partition.account_id


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_held_partition_is_admitted_once_after_the_seal_and_audited(scratch, plane):
    scope, url = cash.runtime._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as session:
        admitted, held = _two_partitions(session, scope)
        _import(session, scope, admitted)
        session.commit()
        seal = _seal(session, scope, held=[_held_id(held)])
        session.commit()
        assert get_opening_hold_list(session, scope=scope) == (_held_id(held),)
        assert len(seal.command.admissions) == 1
        baseline = cash._counts(session, plane, _held_id(held))
        assert get_opening_hold_admissions(session, scope=scope) == ()

        receipt = _admit(session, scope, held)
        session.commit()
        assert receipt.billing_account_id == _held_id(held)
        audit = get_opening_hold_admissions(session, scope=scope)
        assert len(audit) == 1 and audit[0].receipt_id == receipt.receipt_id
        assert audit[0].maker_ref == MAKER and audit[0].approval.approver_refs == (
            CHECKER,
        )
        # The sealed population is immutable: the held admission is audit-only.
        assert len(get_opening_window_admissions(session, scope=scope)) == 1
        assert get_opening_window_seal(session, scope=scope) == seal
        after = cash._counts(session, plane, _held_id(held))
        assert after != baseline

        # Exact replay needs neither a fresh approval nor changes anything.
        approvals = Approvals()
        assert _admit_replay(session, scope, held, audit[0], approvals) == receipt
        assert approvals.calls == 0
        assert cash._counts(session, plane, _held_id(held)) == after

        # A second admission of the same partition is refused, never doubled.
        with pytest.raises(BillingConflict, match="already admitted"):
            _admit(session, scope, replace(held, idempotency_key="second-key"))
        session.rollback()
        assert len(get_opening_hold_admissions(session, scope=scope)) == 1


def _admit_replay(session, scope, held, audit, approvals):
    return _admit(
        session,
        scope,
        held,
        approvals=approvals,
        approval_request_id=audit.approval.request_id,
    )


def test_changed_replay_is_an_idempotency_conflict(scratch):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as session:
        admitted, held = _two_partitions(session, scope)
        _import(session, scope, admitted)
        session.commit()
        _seal(session, scope, held=[_held_id(held)])
        session.commit()
        request = uuid4()
        _admit(session, scope, held, approval_request_id=request)
        session.commit()
        with pytest.raises(IdempotencyConflict):
            _admit(
                session,
                scope,
                held,
                approval_request_id=request,
                disposition=hold.OpeningHoldDispositionV1("disposition:other", DIGEST),
                approvals=Approvals(),
            )


@pytest.mark.parametrize(
    ("approvals", "code"),
    [
        (Approvals(none=True), "opening_hold_approval_missing"),
        (
            Approvals(content_digest="sha256:" + "b" * 64),
            "opening_hold_approval_binding",
        ),
        (Approvals(subject_type="other.subject"), "opening_hold_approval_binding"),
        (Approvals(subject_id="elsewhere"), "opening_hold_approval_binding"),
        (Approvals(request_id=uuid4()), "opening_hold_approval_binding"),
        (Approvals(approver_refs=(MAKER,)), "opening_hold_maker_checker"),
        (Approvals(approver_refs=(CHECKER, MAKER)), "opening_hold_maker_checker"),
        (Approvals(approver_refs=()), "opening_hold_maker_checker"),
        (Approvals(approver_refs=(CHECKER, CHECKER)), "opening_hold_maker_checker"),
        (Approvals(requester_ref="someone-else"), "opening_hold_maker_checker"),
    ],
)
def test_maker_checker_and_approval_binding_refusals_write_nothing(
    scratch, approvals, code
):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as session:
        admitted, held = _two_partitions(session, scope)
        _import(session, scope, admitted)
        session.commit()
        _seal(session, scope, held=[_held_id(held)])
        session.commit()
        baseline = cash._counts(session, "tenant", _held_id(held))
        with pytest.raises(BillingRuleViolation) as refused:
            _admit(session, scope, held, approvals=approvals)
        assert refused.value.code == f"billing.{code}"
        session.rollback()
        assert cash._counts(session, "tenant", _held_id(held)) == baseline
        assert get_opening_hold_admissions(session, scope=scope) == ()
        # Positive control: the same command with a proper approval succeeds.
        _admit(session, scope, held)
        session.commit()
        assert len(get_opening_hold_admissions(session, scope=scope)) == 1


def test_missing_port_and_unavailable_approvals_fail_closed(scratch):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as session:
        admitted, held = _two_partitions(session, scope)
        _import(session, scope, admitted)
        session.commit()
        _seal(session, scope, held=[_held_id(held)])
        session.commit()
        window = session.info["opening_scratch"].windows[scope]
        command = hold.AdmitHeldOpeningAccountV2(
            scope,
            window.window_id,
            held,
            hold.OpeningHoldDispositionV1("d", DIGEST),
            MAKER,
            uuid4(),
        )
        with pytest.raises(BillingRuleViolation, match="approvals port"):
            with cash._as_opening_principal(session):
                s.admit_held_opening_account_v2(
                    session,
                    scope=scope,
                    command=command,
                    verifier=evidence.TrustedVerifier(),
                    approvals=None,
                )
        session.rollback()

        class Unavailable:
            def hold_approval(self, *a, **k):
                from dotmac_billing.opening_contracts import (
                    OpeningVerificationUnavailable,
                )

                raise OpeningVerificationUnavailable("approvals offline")

        with pytest.raises(BillingRuleViolation, match="unavailable"):
            _admit(session, scope, held, approvals=Unavailable())


def test_only_sealed_hold_list_partitions_before_any_post_seal_activity(scratch):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as session:
        admitted, held = _two_partitions(session, scope)
        _import(session, scope, admitted)
        session.commit()
        # Before the seal there is no hold list to admit from.
        with pytest.raises(BillingRuleViolation) as early:
            _admit(session, scope, held)
        assert early.value.code == "billing.opening_hold_window_not_sealed"
        session.rollback()
        _seal(session, scope, held=[_held_id(held)])
        session.commit()
        # An account admitted into the sealed population is not on the hold list.
        with pytest.raises(BillingRuleViolation) as listed:
            _admit(session, scope, replace(admitted, idempotency_key="again"))
        assert listed.value.code == "billing.opening_hold_not_listed"
        session.rollback()
        # Post-seal activity on the held account refuses admission.
        command = b._settlement_command(
            _held_id(held), scope=scope, source_key=str(uuid4()), amount="10.00"
        )
        s.accept_settlement(
            session,
            scope=scope,
            command=command,
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        session.commit()
        before = cash._counts(session, "tenant", _held_id(held))
        with pytest.raises(BillingRuleViolation) as active:
            _admit(session, scope, held)
        assert active.value.code == "billing.opening_hold_post_seal_activity"
        session.rollback()
        assert cash._counts(session, "tenant", _held_id(held)) == before
        assert get_opening_hold_admissions(session, scope=scope) == ()


def test_database_refuses_maker_as_checker_even_if_service_check_is_bypassed(
    scratch, monkeypatch
):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as session:
        admitted, held = _two_partitions(session, scope)
        _import(session, scope, admitted)
        session.commit()
        _seal(session, scope, held=[_held_id(held)])
        session.commit()
        monkeypatch.setattr(
            s, "require_hold_maker_checker", lambda command, approval: approval
        )
        with pytest.raises(DBAPIError) as refused:
            _admit(session, scope, held, approvals=Approvals(approver_refs=(MAKER,)))
        assert refused.value.orig.sqlstate == "23514"
        session.rollback()
        assert get_opening_hold_admissions(session, scope=scope) == ()


class Switch:
    def __init__(self, fail=None):
        self.fail, self.calls, self.seen = fail, 0, None
        self.receipt = OpeningAuthoritySwitchReceiptV1(
            "fixture:d1-participant", "v1", "starter", "state:" + "d" * 8
        )

    def switch_authority(self, session, context):
        self.calls += 1
        self.seen = get_opening_window_seal(session, scope=context.scope)
        session.execute(text("SELECT set_config('billing.test_switch', 'on', true)"))
        if self.fail is not None:
            raise self.fail
        return self.receipt


def _marker(session):
    return session.scalar(text("SELECT current_setting('billing.test_switch', true)"))


@pytest.mark.parametrize(
    "failure", [RuntimeError("d1 flip failed"), BillingRuleViolation("x", "y")]
)
def test_participant_failure_rolls_the_seal_back_and_retry_can_succeed(
    scratch, failure
):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as session:
        admitted, held = _two_partitions(session, scope)
        _import(session, scope, admitted)
        session.commit()
        window = session.info["opening_scratch"].windows[scope]
        fixed = replace(
            window_fixture._seal_command(session, scope, window),
            held_account_ids=(_held_id(held),),
            operational_holds=_operational([_held_id(held)]),
        )
        failing = Switch(failure)
        with pytest.raises(BillingRuleViolation) as refused:
            _seal(session, scope, held=[], participant=failing, prebuilt=fixed)
        assert refused.value.code == "billing.opening_authority_switch_failed"
        assert (
            failing.calls == 1 and failing.seen is not None
        )  # ran inside the seal txn
        assert get_opening_window_seal(session, scope=scope) is None
        assert not _marker(session)  # participant's own writes rolled back too
        session.rollback()
        with cash._session(scratch, url, scope) as observer:
            assert get_opening_window_seal(observer, scope=scope) is None
        # Same command key, healthy participant: seals exactly once.
        ok = Switch()
        seal = _seal(session, scope, held=[], participant=ok, prebuilt=fixed)
        assert ok.calls == 1 and ok.seen == seal
        session.commit()
        again = Switch()
        assert _seal(session, scope, held=[], participant=again, prebuilt=fixed) == seal
        assert again.calls == 0  # replay never re-runs the participant
        assert window.window_id == seal.command.window_id


def test_participant_failure_rollback_survives_a_caller_that_commits_after(scratch):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as session:
        admitted, held = _two_partitions(session, scope)
        _import(session, scope, admitted)
        session.commit()
        with pytest.raises(BillingRuleViolation):
            _seal(
                session,
                scope,
                held=[_held_id(held)],
                participant=Switch(RuntimeError("no")),
            )
        # Even a caller that swallows the error and commits finds no seal.
        session.commit()
        assert get_opening_window_seal(session, scope=scope) is None
        assert not _marker(session)


def test_seal_hold_list_must_be_frozen_manifest_partitions_and_unadmitted(scratch):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as session:
        admitted, held = _two_partitions(session, scope)
        _import(session, scope, admitted)
        session.commit()
        # A hold id outside the frozen population is refused.
        with pytest.raises(BillingRuleViolation) as stranger:
            _seal(session, scope, held=[_held_id(held), uuid4()])
        assert stranger.value.code == "billing.opening_hold_list_invalid"
        session.rollback()
        # Holding an already-admitted partition leaves the population wrong.
        with pytest.raises(BillingRuleViolation, match="population"):
            _seal(session, scope, held=[_held_id(admitted), _held_id(held)])
        session.rollback()
        # Not holding the unadmitted partition leaves the population incomplete.
        with pytest.raises(BillingRuleViolation, match="population"):
            _seal(session, scope, held=[])
        session.rollback()
        assert get_opening_window_seal(session, scope=scope) is None


def test_lane_port_requires_trusted_authority_and_drives_the_whole_lane(scratch):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as session:
        admitted, held = _two_partitions(session, scope)
        authority = Authority(Approvals())
        with pytest.raises(BillingRuleViolation, match="assembly authority"):
            import_opening_lane_partition(
                session, scope=scope, command=admitted, authority=object()
            )
        session.rollback()
        with cash._as_opening_principal(session):
            first = import_opening_lane_partition(
                session, scope=scope, command=admitted, authority=authority
            )
        session.commit()
        assert set(first.as_result()) == {
            "receipt_id",
            "billing_account_id",
            "posting_group_id",
        }
        window = session.info["opening_scratch"].windows[scope]
        seal_command = replace(
            window_fixture._seal_command(session, scope, window),
            held_account_ids=(_held_id(held),),
            operational_holds=_operational([_held_id(held)]),
        )
        with cash._as_opening_principal(session):
            seal_opening_lane(
                session, scope=scope, command=seal_command, authority=authority
            )
        session.commit()
        command = hold.AdmitHeldOpeningAccountV2(
            scope,
            window.window_id,
            held,
            hold.OpeningHoldDispositionV1("d", DIGEST),
            MAKER,
            uuid4(),
        )
        with cash._as_opening_principal(session):
            result = admit_held_opening_lane_partition(
                session, scope=scope, command=command, authority=authority
            )
        session.commit()
        assert result.billing_account_id == _held_id(held)


def test_switch_receipt_is_stored_with_the_seal_and_replay_returns_it(scratch):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as session:
        admitted, held = _two_partitions(session, scope)
        _import(session, scope, admitted)
        session.commit()
        window = session.info["opening_scratch"].windows[scope]
        fixed = replace(
            window_fixture._seal_command(session, scope, window),
            held_account_ids=(_held_id(held),),
            operational_holds=_operational([_held_id(held)]),
        )
        assert get_opening_authority_switch_receipt(session, scope=scope) is None
        participant = Switch()
        _seal(session, scope, held=[], participant=participant, prebuilt=fixed)
        session.commit()
        assert (
            get_opening_authority_switch_receipt(session, scope=scope)
            == participant.receipt
        )
        _seal(session, scope, held=[], participant=Switch(), prebuilt=fixed)
        assert (
            get_opening_authority_switch_receipt(session, scope=scope)
            == participant.receipt
        )
        # Append-only: no rewrite or delete, even by the owner role.
        for statement in (
            "UPDATE mod_billing.opening_authority_switch_receipts SET state_digest='x'",
            "DELETE FROM mod_billing.opening_authority_switch_receipts",
        ):
            with pytest.raises(DBAPIError):
                session.execute(text(statement))
            session.rollback()


@pytest.mark.parametrize("bad", [None, "not a receipt"])
def test_missing_or_invalid_switch_receipt_rolls_the_seal_back(scratch, bad):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as session:
        admitted, held = _two_partitions(session, scope)
        _import(session, scope, admitted)
        session.commit()
        participant = Switch()
        participant.receipt = bad
        with pytest.raises(BillingRuleViolation) as refused:
            _seal(session, scope, held=[_held_id(held)], participant=participant)
        assert refused.value.code == "billing.opening_authority_switch_failed"
        session.rollback()
        assert get_opening_window_seal(session, scope=scope) is None
        assert get_opening_authority_switch_receipt(session, scope=scope) is None


def test_failed_participant_leaves_no_receipt_row(scratch):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as session:
        admitted, held = _two_partitions(session, scope)
        _import(session, scope, admitted)
        session.commit()
        with pytest.raises(BillingRuleViolation):
            _seal(
                session,
                scope,
                held=[_held_id(held)],
                participant=Switch(RuntimeError("x")),
            )
        session.commit()
        assert get_opening_authority_switch_receipt(session, scope=scope) is None
        assert get_opening_window_seal(session, scope=scope) is None


def test_database_refuses_commit_of_post_seal_import_without_hold_record(scratch):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as session:
        admitted, held = _two_partitions(session, scope)
        _import(session, scope, admitted)
        session.commit()
        _seal(session, scope, held=[_held_id(held)])
        session.commit()
        # Planted bypass: the writer without the audit hook, as a raw caller of
        # the opening principal could do. The deferred trigger refuses the commit.
        with cash._as_opening_principal(session):
            s._import_opening_account_v2(
                session,
                scope=scope,
                command=held,
                verifier=evidence.TrustedVerifier(),
                operation_scope="billing.opening.bypass",
                fingerprint=None,
                admit=lambda sess, verified: None,
                after_import=None,
            )
        with pytest.raises(DBAPIError) as refused:
            session.commit()
        assert refused.value.orig.sqlstate == "23514"
        session.rollback()
        assert get_opening_hold_admissions(session, scope=scope) == ()
        # The audited route still works afterwards: nothing was left behind.
        _admit(session, scope, held)
        session.commit()
        assert len(get_opening_hold_admissions(session, scope=scope)) == 1


def test_seal_hold_list_must_equal_the_typed_operational_holds(scratch):
    """Finding 9 / Q1: listed without a reason, a reason not listed, a duplicate
    and a non-carryable partition are all refused; there is no manifest mark."""
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as session:
        admitted, held = _two_partitions(session, scope)
        _import(session, scope, admitted)
        session.commit()
        window = session.info["opening_scratch"].windows[scope]
        base = window_fixture._seal_command(session, scope, window)
        held_id = _held_id(held)
        stranger = uuid4()  # carryable nowhere in the frozen manifest
        # (a) listed without a reason
        with pytest.raises(BillingRuleViolation) as refused:
            _seal(
                session,
                scope,
                held=[],
                prebuilt=replace(base, held_account_ids=(held_id,)),
            )
        assert refused.value.code == "billing.opening_hold_list_invalid"
        session.rollback()
        # (b) a reason for a partition that is not listed, (c) a duplicate
        with pytest.raises(OpeningEvidenceError):
            replace(base, operational_holds=_operational([held_id]))
        with pytest.raises(OpeningEvidenceError):
            replace(base, held_account_ids=(held_id, held_id))
        # (d) a non-carryable partition listed with a reason
        with pytest.raises(BillingRuleViolation) as refused:
            _seal(
                session,
                scope,
                held=[],
                prebuilt=replace(
                    base,
                    held_account_ids=(stranger,),
                    operational_holds=_operational([stranger]),
                ),
            )
        assert refused.value.code == "billing.opening_hold_list_invalid"
        session.rollback()
        assert get_opening_window_seal(session, scope=scope) is None


# --- D-9: two-person approval is a per-window policy (bi_0019) -----------------


def _off_ready(session, scope):
    admitted, held = _two_partitions(session, scope, "off")
    _import(session, scope, admitted)
    session.commit()
    _seal(session, scope, held=[_held_id(held)])
    session.commit()
    return held


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_off_admits_without_an_approval_and_records_it(scratch, plane):
    scope, url = cash.runtime._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as session:
        held = _off_ready(session, scope)
        approvals = Approvals()
        receipt = _admit(
            session, scope, held, approvals=approvals, approval_request_id=None
        )
        session.commit()
        assert approvals.calls == 0  # the Approvals port is never consulted
        (audit,) = get_opening_hold_admissions(session, scope=scope)
        assert audit.receipt_id == receipt.receipt_id
        assert audit.dual_control == "off" and audit.approval is None
        assert audit.maker_ref == MAKER
        table = (
            "opening_hold_admissions"
            if plane == "tenant"
            else "platform_opening_hold_admissions"
        )
        row = session.execute(
            text(
                f"SELECT approval_request_id, approver_refs, evidence "
                f"FROM mod_billing.{table}"
            )
        ).one()
        assert row.approval_request_id is None and row.approver_refs == []
        assert row.evidence["approval"] is None
        assert row.evidence["dual_control"] == "off"
        assert row.evidence["maker_ref"] == MAKER
        assert row.evidence["disposition"]["disposition_digest"] == DIGEST


def test_off_refuses_a_supplied_approval_rather_than_ignoring_it(scratch):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as session:
        held = _off_ready(session, scope)
        approvals = Approvals()
        baseline = cash._counts(session, "tenant", _held_id(held))
        with pytest.raises(BillingRuleViolation) as refused:
            _admit(session, scope, held, approvals=approvals)  # carries an id
        assert refused.value.code == "billing.opening_hold_approval_not_applicable"
        assert approvals.calls == 0
        session.rollback()
        assert cash._counts(session, "tenant", _held_id(held)) == baseline
        assert get_opening_hold_admissions(session, scope=scope) == ()


def test_off_keeps_every_non_approval_control(scratch):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as session:
        held = _off_ready(session, scope)
        with pytest.raises(BillingRuleViolation):  # not on the hold list
            _admit(
                session,
                scope,
                _import_for_stranger(session, scope),
                approval_request_id=None,
            )
        session.rollback()
        with pytest.raises(OpeningEvidenceError):  # a digest is mandatory
            hold.OpeningHoldDispositionV1("d", "not-a-digest")
        with pytest.raises(OpeningEvidenceError):  # a maker is mandatory
            _admit(session, scope, held, approval_request_id=None, maker_ref=" ")
        session.rollback()
        assert get_opening_hold_admissions(session, scope=scope) == ()
        _admit(session, scope, held, approval_request_id=None)  # control
        session.commit()
        assert len(get_opening_hold_admissions(session, scope=scope)) == 1


def _import_for_stranger(session, scope):
    stranger = cash._new_account(session, scope)
    return ar._command(session, scope, stranger)


def test_on_without_an_approval_is_still_missing(scratch):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as session:
        admitted, held = _two_partitions(session, scope)  # "on"
        _import(session, scope, admitted)
        session.commit()
        _seal(session, scope, held=[_held_id(held)])
        session.commit()
        with pytest.raises(BillingRuleViolation) as refused:
            _admit(session, scope, held, approval_request_id=None)
        assert refused.value.code == "billing.opening_hold_approval_missing"
        session.rollback()


def _bypass_import(session, scope, held):
    """The writer without the audit hook (as a raw caller of the principal)."""
    with cash._as_opening_principal(session):
        return s._import_opening_account_v2(
            session,
            scope=scope,
            command=held,
            verifier=evidence.TrustedVerifier(),
            operation_scope="billing.opening.bypass",
            fingerprint=None,
            admit=lambda sess, verified: None,
            after_import=None,
        )


def _raw_hold_row(session, scope, receipt, *, request, refs, approval, dual):
    window = session.info["opening_scratch"].windows[scope]
    body = {
        "maker_ref": MAKER,
        "receipt_id": str(receipt.receipt_id),
        "billing_account_id": str(receipt.billing_account_id),
        "window_id": str(window.window_id),
        "disposition": {"disposition_digest": DIGEST},
        "approval": approval,
    }
    if dual is not None:
        body["dual_control"] = dual
    return session.execute(
        text(
            "INSERT INTO mod_billing.opening_hold_admissions (id, tenant_id, "
            "window_id, billing_account_id, receipt_id, disposition_digest, "
            "maker_ref, approval_request_id, approver_refs, evidence, "
            "evidence_digest) VALUES (:id, :t, :w, :a, :r, :d, :m, :req, "
            "CAST(:refs AS json), CAST(:ev AS json), :dg)"
        ),
        {
            "id": uuid4(),
            "t": scope.tenant_id,
            "w": window.window_id,
            "a": receipt.billing_account_id,
            "r": receipt.receipt_id,
            "d": DIGEST,
            "m": MAKER,
            "req": request,
            "refs": json.dumps(refs),
            "ev": json.dumps(body),
            "dg": "0" * 64,
        },
    )


def _refused_raw(session, scope, receipt, **values):
    with cash._as_opening_principal(session):
        with pytest.raises(DBAPIError) as refused:
            with session.begin_nested():
                _raw_hold_row(session, scope, receipt, **values)
    assert refused.value.orig.sqlstate == "23514"


def test_database_refuses_null_approval_under_on_and_any_approval_under_off(scratch):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    stand = {"request_id": str(uuid4()), "requester_ref": MAKER}
    with cash._session(scratch, url, scope) as session:  # ON window
        admitted, held = _two_partitions(session, scope)
        _import(session, scope, admitted)
        session.commit()
        _seal(session, scope, held=[_held_id(held)])
        session.commit()
        receipt = _bypass_import(session, scope, held)
        _refused_raw(  # NULL id under ON
            session,
            scope,
            receipt,
            request=None,
            refs=[CHECKER],
            approval=stand,
            dual=None,
        )
        _refused_raw(  # an ON window cannot claim "off"
            session, scope, receipt, request=None, refs=[], approval=None, dual="off"
        )
        session.rollback()


def test_database_refuses_approvals_under_an_off_window(scratch):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    stand = {"request_id": str(uuid4()), "requester_ref": MAKER}
    with cash._session(scratch, url, scope) as session:
        held = _off_ready(session, scope)
        receipt = _bypass_import(session, scope, held)
        for values in (
            {"request": uuid4(), "refs": [], "approval": None, "dual": "off"},
            {"request": None, "refs": [CHECKER], "approval": None, "dual": "off"},
            {"request": None, "refs": [], "approval": stand, "dual": "off"},
            {"request": None, "refs": [], "approval": None, "dual": None},
            {"request": None, "refs": [], "approval": None, "dual": "on"},
        ):
            _refused_raw(session, scope, receipt, **values)
        session.rollback()
