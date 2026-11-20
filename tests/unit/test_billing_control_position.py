"""Receivable control position (R1): pure assembly, golden vector and refusals.

No database. The session-level checks run against a stub session; the live
snapshot, RLS and concurrency proofs are in ``test_billing_control_position_pg``.
"""

import hashlib
import json
from contextlib import nullcontext
from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal
from typing import get_args, get_type_hints
from uuid import UUID

import pytest
from dotmac_billing import control_position as cp
from dotmac_billing.errors import BillingRuleViolation
from dotmac_kernel.cache import PlatformScope, TenantScope

NOW = datetime(2026, 11, 19, 9, tzinfo=UTC)
TENANT = TenantScope(UUID("00000000-0000-0000-0000-0000000000aa"))
A, B, C = (UUID(int=n) for n in (1, 2, 3))
FP = {A: "a" * 64, B: "b" * 64, C: "c" * 64}


def _position(account, version=1, currency="NGN", minor=2, **overrides):
    row = {
        "billing_account_id": account,
        "source_version": version,
        "posting_group_watermark": UUID(int=1000 + int(account.int) * 10 + version),
        "currency": currency,
        "minor_units": minor,
        "collectible_receivable": Decimal("100.50"),
        "available_credit": Decimal("10.00"),
        "available_non_cash_credit": Decimal("1.25"),
        "prepaid_funding": Decimal("0.00"),
        "state_fingerprint": FP[account],
        "source_authority": "internal",
        "projection_mode": "authoritative",
        "completeness": "complete",
    }
    row.update(overrides)
    return row


def _assemble(positions, *, scope=TENANT, accounts=None, groups=None, counts=None):
    accounts = accounts or {
        p["billing_account_id"]: (p["currency"], p["minor_units"]) for p in positions
    }
    by_account = {p["billing_account_id"]: p for p in positions}
    groups = groups or {
        acct: (p["posting_group_watermark"], p["source_version"])
        for acct, p in by_account.items()
    }
    counts = counts or {acct: p["source_version"] for acct, p in by_account.items()}
    return cp.assemble_control_position(
        scope=scope,
        snapshot_at=NOW,
        accounts=accounts,
        positions=by_account,
        latest_groups=groups,
        group_counts=counts,
    )


def _refusal(positions, **kwargs):
    with pytest.raises(BillingRuleViolation) as refused:
        _assemble(positions, **kwargs)
    return refused.value.code


def test_shape_is_closed_and_the_contract_is_pinned():
    assert cp.RECEIVABLE_CONTROL_POSITION_CONTRACT == (
        "billing.receivable.control_position.v1"
    )
    assert [f.name for f in fields(cp.ReceivableControlCurrencyV1)] == [
        "currency",
        "precision",
        "accounts",
        "collectible_receivable_minor",
        "available_credit_minor",
        "available_non_cash_credit_minor",
        "prepaid_funding_minor",
        "position_fingerprint",
    ]
    hints = get_type_hints(cp.ReceivableControlPositionV1)
    assert get_args(hints["contract"]) == (cp.RECEIVABLE_CONTROL_POSITION_CONTRACT,)
    assert hints["snapshot_at"] is datetime
    assert not any(
        word in f.name
        for f in fields(cp.ReceivableControlCurrencyV1)
        for word in ("balance", "open_documents")
    )


def test_sums_are_exact_minor_units_sorted_by_currency_with_mixed_precision():
    result = _assemble([_position(C, currency="USD"), _position(A), _position(B)])
    assert [c.currency for c in result.currencies] == ["NGN", "USD"]
    ngn, usd = result.currencies
    assert (ngn.accounts, usd.accounts) == (2, 1)
    assert ngn.collectible_receivable_minor == 2 * 10050
    assert ngn.available_credit_minor == 2 * 1000
    assert ngn.available_non_cash_credit_minor == 2 * 125
    assert ngn.prepaid_funding_minor == 0
    assert (result.contract, result.snapshot_at, result.scope) == (
        cp.RECEIVABLE_CONTROL_POSITION_CONTRACT,
        NOW,
        TENANT,
    )


def test_zero_precision_currency_is_exact_integers():
    d = UUID(int=4)
    FP[d] = "d" * 64
    result = _assemble(
        [
            _position(
                d,
                currency="JPY",
                minor=0,
                collectible_receivable=Decimal("7"),
                available_credit=Decimal("0"),
                available_non_cash_credit=Decimal("0"),
                prepaid_funding=Decimal("0"),
            )
        ]
    )
    assert result.currencies[0].precision == 0
    assert result.currencies[0].collectible_receivable_minor == 7


def test_empty_scope_has_no_currencies():
    assert _assemble([]).currencies == ()


def test_golden_fingerprint_vector_is_pinned_for_the_consumer():
    value = cp.control_position_fingerprint(
        scope=TENANT,
        currency="NGN",
        precision=2,
        rows=[(B, 3, FP[B]), (A, 1, FP[A])],  # order is not authority
    )
    assert value == GOLDEN
    # The documented encoding, recomputed with the standard library only.
    encoded = json.dumps(
        {
            "contract": "billing.receivable.control_position.v1",
            "scope": {"kind": "tenant", "tenant_id": str(TENANT.tenant_id)},
            "currency": "NGN",
            "precision": 2,
            "rows": [
                [str(A), "NGN", 1, FP[A]],
                [str(B), "NGN", 3, FP[B]],
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == GOLDEN
    assert value != cp.control_position_fingerprint(
        scope=PlatformScope(),
        currency="NGN",
        precision=2,
        rows=[(A, 1, FP[A]), (B, 3, FP[B])],
    )
    assert value != cp.control_position_fingerprint(
        scope=TENANT, currency="NGN", precision=3, rows=[(A, 1, FP[A]), (B, 3, FP[B])]
    )


GOLDEN = "91ac282a17e722fefb2c3203e34b48caf78c62675384c4508f214eb04d54d3a9"


def test_fingerprint_changes_when_any_one_account_posts():
    base = _assemble([_position(A), _position(B)]).currencies[0].position_fingerprint
    moved = (
        _assemble(
            [_position(A), _position(B, version=2)],
        )
        .currencies[0]
        .position_fingerprint
    )
    assert base != moved


@pytest.mark.parametrize(
    "mutate",
    [
        {"completeness": "partial"},
        {"projection_mode": "shadow"},
        {"source_authority": "external"},
        {"currency": "USD"},
        {"minor_units": 3},
        {"source_version": 2},
        {"posting_group_watermark": UUID(int=99)},
    ],
)
def test_incomplete_or_disagreeing_rows_refuse(mutate):
    assert _refusal_for(mutate) == "billing.control_position_incomplete"


def _refusal_for(mutate):
    row = _position(A)
    accounts = {A: ("NGN", 2)}
    groups = {A: (row["posting_group_watermark"], 1)}
    row.update(mutate)
    return _refusal([row], accounts=accounts, groups=groups, counts={A: 1})


def test_version_gaps_history_without_a_row_and_a_row_without_history_refuse():
    row = _position(A, version=3)
    groups = {A: (row["posting_group_watermark"], 3)}
    assert _refusal([row], groups=groups, counts={A: 2}) == (
        "billing.control_position_incomplete"
    )
    with pytest.raises(BillingRuleViolation) as refused:  # V1-only history
        cp.assemble_control_position(
            scope=TENANT,
            snapshot_at=NOW,
            accounts={A: ("NGN", 2)},
            positions={},
            latest_groups={A: (UUID(int=5), 1)},
            group_counts={A: 1},
        )
    assert "1 account" in refused.value.message
    with pytest.raises(BillingRuleViolation):  # a row but no posting group
        cp.assemble_control_position(
            scope=TENANT,
            snapshot_at=NOW,
            accounts={A: ("NGN", 2)},
            positions={A: _position(A)},
            latest_groups={},
            group_counts={},
        )


def test_precision_conflict_and_inexact_amounts_refuse():
    one, two = _position(A), _position(B, minor=3)
    accounts = {A: ("NGN", 2), B: ("NGN", 3)}
    assert _refusal([one, two], accounts=accounts) == (
        "billing.control_position_precision_conflict"
    )
    inexact = _position(A, collectible_receivable=Decimal("1.005"))
    assert _refusal([inexact]) == "billing.control_position_precision_conflict"


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _Connection:
    def __init__(self, level):
        self.level = level

    def get_isolation_level(self):
        return self.level


class _Session:
    no_autoflush = nullcontext()

    def __init__(self, level, read_only, tenant=None):
        self.level, self.read_only, self.tenant = level, read_only, tenant
        self.statements = []

    def connection(self):
        return _Connection(self.level)

    def execute(self, statement, *args, **kwargs):
        sql = str(statement)
        self.statements.append(sql)
        if "transaction_read_only" in sql:
            return _Result(self.read_only)
        if "current_setting" in sql:
            return _Result(self.tenant)
        raise AssertionError("no query may run after a refused precondition")


@pytest.mark.parametrize(
    ("level", "read_only"),
    [
        ("READ COMMITTED", "on"),
        ("REPEATABLE READ", "off"),  # read-write repeatable read
        ("READ UNCOMMITTED", "on"),
    ],
)
def test_wrong_transaction_is_refused_before_any_read(level, read_only):
    session = _Session(level, read_only)
    with pytest.raises(BillingRuleViolation) as refused:
        cp.get_receivable_control_position_v1(session, scope=TENANT)
    assert refused.value.code == (
        "billing.control_position_requires_repeatable_read_read_only"
    )


@pytest.mark.parametrize("tenant", [None, "", "00000000-0000-0000-0000-0000000000bb"])
def test_a_wrong_or_unset_tenant_context_is_refused(tenant):
    session = _Session("SERIALIZABLE", "on", tenant)
    with pytest.raises(BillingRuleViolation) as refused:
        cp.get_receivable_control_position_v1(session, scope=TENANT)
    assert refused.value.code == "billing.control_position_tenant_context"


def test_an_unusable_scope_is_refused():
    with pytest.raises(BillingRuleViolation):
        cp.get_receivable_control_position_v1(
            _Session("SERIALIZABLE", "on"), scope=None
        )
