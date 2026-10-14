"""Hosted PostgreSQL proof of immutable inverses after a durable opening seal.

Fixture verifiers qualify injected test authority, never production evidence.
Migration custody ends before a new connection authenticates as ordinary LOGIN.
"""

from dataclasses import replace
from uuid import uuid4

import pytest
from dotmac_billing import opening_queries as cash_queries
from dotmac_billing import opening_receivable_queries as ar_queries
from dotmac_billing import service as s
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_commands import (
    CorrectOpeningCashRefundV1,
    OpeningCashMovementEvidenceV1,
    ReverseOpeningAccountImportV1,
)
from dotmac_billing.opening_contracts import OpeningVerificationUnavailable
from dotmac_billing.opening_window_queries import (
    get_opening_window_admissions,
    get_opening_window_seal,
)
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import text
from sqlalchemy.engine import make_url

from tests import billing_opening_window_fixtures as window
from tests import test_billing_isolation as b
from tests import test_billing_opening_cash_import as cash
from tests import test_billing_opening_receivable_import as ar

# Identifiers below come only from these closed table names and the plane enum.
# ruff: noqa: S608
FROZEN_TABLES = (
    "opening_windows",
    "opening_window_admissions",
    "opening_window_seals",
    "cash_source_claims",
    "receivable_source_claims",
    "opening_cash_lots",
    "opening_receivable_bases",
    "opening_historical_discharge_claims",
)
APPEND_TABLES = (
    "opening_import_receipts",
    "opening_cash_effects",
    "posting_groups",
    "posting_effects",
    "accounting_facts",
)


@pytest.fixture
def scratch():
    with b._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


def _snapshot(db, plane):
    prefix = "platform_" if plane == "platform" else ""
    return {
        name: list(
            db.execute(
                text(
                    f"SELECT to_jsonb(evidence) FROM mod_billing.{prefix}{name} "
                    "AS evidence ORDER BY to_jsonb(evidence)::text"
                )
            ).scalars()
        )
        for name in (*FROZEN_TABLES, *APPEND_TABLES)
    }


class FreshInverseVerifier:
    """Each invocation independently binds this exact inverse and original."""

    def __init__(self, version, mode="allow"):
        self.delegate = (
            cash.TrustedVerifier() if version == 1 else ar.evidence.TrustedVerifier()
        )
        self.mode = mode
        self.calls = []

    def _verify(self, request, method):
        self.calls.append(request)
        if self.mode == "unavailable":
            raise OpeningVerificationUnavailable("fixture authority unavailable")
        if self.mode == "denied":
            return None
        verified = getattr(self.delegate, method)(request)
        if self.mode == "misbound":
            return replace(verified, receipt_evidence_digest="0" * 64)
        return verified

    def verify_import_reversal(self, request):
        return self._verify(request, "verify_import_reversal")

    def verify_import_reversal_v2(self, request):
        return self._verify(request, "verify_import_reversal_v2")


def _assert_authority_refusals(url, scope, plane, version, inverse, command):
    # This applies equally to a new inverse and an exact committed retry.
    for mode, code in (
        ("missing", "billing.opening_verifier_unavailable"),
        ("unavailable", "billing.opening_verifier_unavailable"),
        ("denied", "billing.opening_authority_binding"),
        ("misbound", "billing.opening_authority_binding"),
    ):
        verifier = None if mode == "missing" else FreshInverseVerifier(version, mode)
        with b._session(url, scope) as db:
            assert db.scalar(text("SELECT current_user = session_user"))
            assert db.scalar(text("SELECT session_user")) == make_url(url).username
            before = _snapshot(db, plane)
            with pytest.raises(BillingRuleViolation) as refused:
                inverse(db, scope=scope, command=command, verifier=verifier)
            assert refused.value.code == code
            assert _snapshot(db, plane) == before
            if verifier is not None:
                assert len(verifier.calls) == 1


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("version", [1, 2])
def test_committed_seal_allows_only_fresh_authorized_linked_inverse(
    scratch, plane, version
):
    scope, url = cash.runtime._scope_url(scratch, plane)
    fixture = cash if version == 1 else ar
    importer = s.import_opening_account if version == 1 else s.import_opening_account_v2
    inverse_owner = (
        s.reverse_opening_account_import
        if version == 1
        else s.reverse_opening_account_import_v2
    )
    import_verifier = (
        cash.TrustedVerifier() if version == 1 else ar.evidence.TrustedVerifier()
    )
    with b._session(url, scope) as db:
        account = cash._new_account(db, scope)
        other_account = cash._new_account(db, scope)
        command = fixture._command(db, scope, account)
        fresh_command = fixture._command(db, scope, other_account)

    with window._principal(scratch, plane) as principal:
        custody = window._provision(scratch, scope, principal, command)
        with window._restricted_session(scratch, scope, principal) as migration:
            assert migration.scalar(text("SELECT current_user")) == principal[0]
            original = importer(
                migration, scope=scope, command=command, verifier=import_verifier
            )
            seal_command = window._seal_command(migration, scope, custody)
            seal = s.seal_opening_window(
                migration,
                scope=scope,
                command=seal_command,
                verifier=window.TrustedWindowVerifier(),
            )
            migration.commit()

        unwind = ReverseOpeningAccountImportV1(
            scope,
            original.receipt_id,
            "fixture:fresh-post-seal-authority",
            str(uuid4()),
            cash.preflight.NOW,
        )
        _assert_authority_refusals(url, scope, plane, version, inverse_owner, unwind)
        with b._session(url, scope) as ordinary:
            assert ordinary.scalar(text("SELECT current_user = session_user"))
            assert (
                ordinary.scalar(text("SELECT session_user")) == make_url(url).username
            )
            assert get_opening_window_seal(ordinary, scope=scope) == seal
            frozen = _snapshot(ordinary, plane)
            assert frozen["cash_source_claims"]
            if version == 2:
                assert frozen["receivable_source_claims"]
                assert frozen["opening_historical_discharge_claims"]
            refund = s.record_opening_cash_refund(
                ordinary,
                scope=scope,
                command=cash._refund(scope, original.lot_ids[0]),
                verifier=cash.TrustedVerifier(),
            )
            ordinary.commit()

        with b._session(url, scope) as ordinary:
            before = _snapshot(ordinary, plane)
            verifier = FreshInverseVerifier(version)
            with pytest.raises(BillingRuleViolation) as refused:
                inverse_owner(ordinary, scope=scope, command=unwind, verifier=verifier)
            assert refused.value.code == "billing.opening_import_has_dependents"
            assert len(verifier.calls) == 1
            assert _snapshot(ordinary, plane) == before
            s.correct_opening_cash_refund(
                ordinary,
                scope=scope,
                command=CorrectOpeningCashRefundV1(
                    scope,
                    refund.effect_id,
                    refund.amount,
                    OpeningCashMovementEvidenceV1(
                        "payments", str(uuid4()), "2", "c" * 64, "corrected"
                    ),
                    "fixture:refund-correction-authority",
                    str(uuid4()),
                    cash.preflight.NOW,
                ),
                verifier=cash.TrustedVerifier(),
            )
            ordinary.commit()

        with b._session(url, scope) as ordinary:
            assert ordinary.scalar(text("SELECT current_user = session_user"))
            verifier = FreshInverseVerifier(version)
            inverse = inverse_owner(
                ordinary, scope=scope, command=unwind, verifier=verifier
            )
            assert len(verifier.calls) == 1
            assert inverse.receipt_kind == "unwind"
            assert inverse.original_receipt_id == original.receipt_id
            assert inverse.posting_group_id != original.posting_group_id
            assert inverse.manifest == original.manifest
            assert inverse.total_residual == original.total_residual
            if version == 2:
                assert inverse.total_receivable == original.total_receivable
            ordinary.commit()

        _assert_authority_refusals(url, scope, plane, version, inverse_owner, unwind)
        with b._session(url, scope) as ordinary:
            before = _snapshot(ordinary, plane)
            verifier = FreshInverseVerifier(version)
            assert (
                inverse_owner(ordinary, scope=scope, command=unwind, verifier=verifier)
                == inverse
            )
            assert len(verifier.calls) == 1
            assert _snapshot(ordinary, plane) == before
            assert get_opening_window_seal(ordinary, scope=scope) == seal
            assert (
                get_opening_window_admissions(ordinary, scope=scope)
                == seal_command.admissions
            )
            for table in FROZEN_TABLES:
                assert before[table] == frozen[table], table
            for table in APPEND_TABLES:
                assert all(row in before[table] for row in frozen[table]), table
            groups = before["posting_groups"]
            linked = [
                row for row in groups if row["id"] == str(inverse.posting_group_id)
            ]
            assert len(linked) == 1
            assert linked[0]["reverses_group_id"] is None
            facts = [
                row
                for row in before["accounting_facts"]
                if row["posting_group_id"] == str(inverse.posting_group_id)
            ]
            assert len(facts) == 1
            assert facts[0]["fact_version"] == (4 if version == 1 else 5)
            assert facts[0]["fact_payload"]["corrects_posting_group_id"] == str(
                original.posting_group_id
            )
            position = cash._position(ordinary, scope, account)
            assert position.available_credit == 0
            assert position.collectible_receivable == 0
            for lot_id in original.lot_ids:
                assert cash_queries.get_opening_cash_lot_position(
                    ordinary, scope=scope, lot_id=lot_id
                ).unwound
            if version == 2:
                for basis_id in original.basis_ids:
                    assert ar_queries.get_opening_receivable_position(
                        ordinary, scope=scope, basis_id=basis_id
                    ).unwound

        # Even the still-bound migration principal cannot admit new money after
        # reversal: the committed seal and original source custody are retained.
        with window._restricted_session(scratch, scope, principal) as migration:
            for offered in (
                replace(command, idempotency_key=str(uuid4())),
                fresh_command,
            ):
                before = _snapshot(migration, plane)
                with pytest.raises(BillingRuleViolation, match="sealed"):
                    importer(
                        migration,
                        scope=scope,
                        command=offered,
                        verifier=import_verifier,
                    )
                assert _snapshot(migration, plane) == before
