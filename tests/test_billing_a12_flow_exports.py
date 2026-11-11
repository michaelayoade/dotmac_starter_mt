"""Billing a12 flows exports (G1) and the open-documents read (G2): unit proofs.

No database: the read's keyset and bounded-scan logic runs against a stub
session and a stubbed ``get_document_exposure``.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import dotmac_billing as billing
import pytest
from dotmac_billing import document_queries as dq
from dotmac_billing.contracts import DueDateBasisStatus
from dotmac_billing.errors import BillingRuleViolation
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.money import Currency, Money

NGN = Currency("NGN", 2)
ACCOUNT = UUID(int=7)
T0 = datetime(2026, 8, 17, 10, tzinfo=UTC)

FLOW_NAMES = (
    # F1
    "accept_settlement",
    "allocate_settlement",
    "AllocationCommand",
    "accept_withholding_discharge",
    "AcceptWithholdingDischarge",
    # F2
    "apply_available_credit",
    "ApplyAvailableCreditCommand",
    # F3-F5
    "void_document",
    "issue_credit_note",
    "IssueCreditNote",
    "grant_non_cash_credit",
    "GrantNonCashCredit",
    "deallocate_settlement",
    "DeallocationCommand",
    "refund_settlement",
    "RefundCommand",
    "reverse_posting_group",
    "reverse_withholding_discharge",
    "reverse_cash_credit_draw",
    "reverse_non_cash_credit_application",
    "ReversePostingGroupCommand",
    "issue_document",
    "IssueDocument",
    # ST-13
    "OpeningLaneAssemblyAuthorityV1",
    "OpeningLanePartitionResultV1",
    "import_opening_lane_partition",
    "admit_held_opening_lane_partition",
    "seal_opening_lane",
    # G2
    "list_open_receivable_documents",
    "OpenReceivableDocumentV1",
    "OpenReceivableDocumentPageV1",
    "OpenReceivableDocumentCursorV1",
)


@pytest.mark.parametrize("name", FLOW_NAMES)
def test_flow_names_are_in_the_top_level_all(name):
    assert name in billing.__all__
    assert getattr(billing, name) is not None


def test_all_has_no_duplicates_and_resolves():
    assert len(billing.__all__) == len(set(billing.__all__))
    for name in billing.__all__:
        assert hasattr(billing, name), name


def test_exports_are_the_owner_objects_not_wrappers():
    from dotmac_billing import commands, opening_lane, service

    assert billing.accept_settlement is service.accept_settlement
    assert billing.AllocationCommand is commands.AllocationCommand
    assert billing.seal_opening_lane is opening_lane.seal_opening_lane
    for name in (
        "reverse_withholding_discharge",
        "reverse_cash_credit_draw",
        "reverse_non_cash_credit_application",
    ):
        assert getattr(billing, name) is getattr(service, name)
    assert "apply_non_cash_credit" not in billing.__all__


# --- the read -------------------------------------------------------------


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Session:
    """Serves candidate keys after the cursor in key order; records the SQL."""

    def __init__(self, keys):
        self.keys = sorted(keys)
        self.statements = []
        self.no_autoflush = _Ctx()

    def execute(self, statement):
        self.statements.append(statement)
        compiled = statement.compile()
        limit = statement._limit_clause.value
        # The cursor literal, when present, is the last two bound values.
        after = None
        values = [v for v in compiled.params.values() if isinstance(v, datetime)]
        ids = [v for v in compiled.params.values() if isinstance(v, UUID)]
        if values:
            after = (values[-1], ids[-1])
        rows = [k for k in self.keys if after is None or k > after]
        return _Rows(rows[:limit])


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _exposure(
    document_id,
    *,
    state="open",
    amount="10",
    due=None,
    version=3,
    basis=DueDateBasisStatus.VERIFIED,
):
    return type(
        "Exposure",
        (),
        {
            "financial_state": state,
            "collectible_receivable": Money.of(amount, NGN),
            "due_at": due,
            "service_ref": f"service:{document_id.int}",
            "source_version": version,
            "due_date_basis": type("Basis", (), {"status": basis})(),
        },
    )()


def _keys(n):
    return [(T0 + timedelta(minutes=i // 2), UUID(int=100 + i)) for i in range(n)]


def _install(monkeypatch, exposures):
    seen = []

    def fake(db, *, scope, document_id):
        seen.append(document_id)
        return exposures[document_id]

    monkeypatch.setattr(dq, "get_document_exposure", fake)
    return seen


def _walk(session, *, limit, scope=None):
    scope = scope or PlatformScope()
    out, cursor, pages = [], None, 0
    while True:
        page = dq.list_open_receivable_documents(
            session,
            scope=scope,
            account=ACCOUNT,
            currency=NGN,
            cursor=cursor,
            limit=limit,
        )
        pages += 1
        out.extend(page.entries)
        cursor = page.next_cursor
        if cursor is None:
            return out, pages
        assert pages < 100


def test_returns_only_open_and_partially_resolved_positive_in_key_order(monkeypatch):
    keys = _keys(7)
    states = [
        "open",
        "resolved",
        "partially_resolved",
        "cancelled",
        "open",
        "open",
        "open",
    ]
    exposures = {
        k[1]: _exposure(
            k[1], state=s, amount="0" if s in ("resolved", "cancelled") else "5"
        )
        for k, s in zip(keys, states, strict=True)
    }
    _install(monkeypatch, exposures)
    entries, _ = _walk(_Session(keys), limit=3)
    wanted = [
        k
        for k, s in zip(keys, states, strict=True)
        if s in ("open", "partially_resolved")
    ]
    assert [(e.issued_at, e.document_id) for e in entries] == wanted
    assert entries[1].financial_state == "partially_resolved"
    assert entries[0].collectible_receivable == Money.of("5", NGN)
    assert entries[0].service_ref == f"service:{keys[0][1].int}"
    assert entries[0].source_version == 3


def test_zero_amount_open_state_is_not_collectible(monkeypatch):
    keys = _keys(1)
    _install(monkeypatch, {keys[0][1]: _exposure(keys[0][1], amount="0")})
    entries, _ = _walk(_Session(keys), limit=5)
    assert entries == []


def test_pagination_is_complete_without_duplicates_and_ends_with_none(monkeypatch):
    keys = _keys(9)
    exposures = {k[1]: _exposure(k[1]) for k in keys}
    seen = _install(monkeypatch, exposures)
    entries, pages = _walk(_Session(keys), limit=4)
    assert [(e.issued_at, e.document_id) for e in entries] == keys
    assert len(set(seen)) == len(seen) == 9
    assert pages >= 3


def test_billing_applies_no_policy_ordering_unknown_due_stays_in_key_order(monkeypatch):
    keys = _keys(3)
    dues = [None, T0 - timedelta(days=9), T0]
    exposures = {k[1]: _exposure(k[1], due=d) for k, d in zip(keys, dues, strict=True)}
    _install(monkeypatch, exposures)
    entries, _ = _walk(_Session(keys), limit=10)
    assert [e.due_at for e in entries] == dues


def test_scan_is_bounded_and_cursor_resumes(monkeypatch):
    monkeypatch.setattr(dq, "MAX_OPEN_DOCUMENT_SCAN", 6)
    keys = _keys(10)
    exposures = {k[1]: _exposure(k[1], state="resolved", amount="0") for k in keys}
    exposures[keys[9][1]] = _exposure(keys[9][1])
    seen = _install(monkeypatch, exposures)
    session = _Session(keys)
    first = dq.list_open_receivable_documents(
        session, scope=PlatformScope(), account=ACCOUNT, currency=NGN, limit=2
    )
    assert first.entries == () and first.next_cursor is not None
    assert len(seen) == 6
    entries, _ = _walk(session, limit=2)
    assert [e.document_id for e in entries] == [keys[9][1]]


def test_exposure_refusal_propagates_and_is_not_skipped(monkeypatch):
    keys = _keys(2)

    def boom(db, *, scope, document_id):
        raise BillingRuleViolation("exposure_fact_stale", "stale")

    monkeypatch.setattr(dq, "get_document_exposure", boom)
    with pytest.raises(BillingRuleViolation):
        dq.list_open_receivable_documents(
            _Session(keys), scope=PlatformScope(), account=ACCOUNT, currency=NGN
        )


def test_query_is_filtered_by_account_currency_invoice_and_tenant(monkeypatch):
    _install(monkeypatch, {})
    session = _Session([])
    tenant = TenantScope(UUID(int=9))
    dq.list_open_receivable_documents(
        session, scope=tenant, account=ACCOUNT, currency=NGN
    )
    sql = str(session.statements[0].compile())
    for fragment in ("billing_account_id", "document_kind", "currency", "tenant_id"):
        assert fragment in sql
    assert "ORDER BY" in sql and "issued_at" in sql
    assert "platform_documents" in str(_platform_sql(monkeypatch))


def _platform_sql(monkeypatch):
    session = _Session([])
    dq.list_open_receivable_documents(
        session, scope=PlatformScope(), account=ACCOUNT, currency=NGN
    )
    return session.statements[0]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": -1},
        {"limit": dq.MAX_OPEN_DOCUMENT_PAGE + 1},
        {"limit": True},
        {"cursor": (T0, UUID(int=1))},
        {"account": "not-a-uuid"},
        {"currency": "NGN"},
    ],
)
def test_bad_arguments_are_refused_before_any_query(kwargs):
    session = _Session([])
    args = {"scope": PlatformScope(), "account": ACCOUNT, "currency": NGN} | kwargs
    with pytest.raises(BillingRuleViolation):
        dq.list_open_receivable_documents(session, **args)
    assert session.statements == []


def test_non_scope_is_refused():
    with pytest.raises(BillingRuleViolation):
        dq.list_open_receivable_documents(
            _Session([]), scope=object(), account=ACCOUNT, currency=NGN
        )


def test_entry_and_cursor_are_frozen():
    entry = dq.OpenReceivableDocumentV1(
        UUID(int=1),
        T0,
        None,
        Money.of("1", NGN),
        None,
        1,
        "open",
        DueDateBasisStatus.UNKNOWN_UNVERIFIED,
    )
    with pytest.raises(AttributeError):
        entry.source_version = 2
    assert replace(entry, source_version=2).source_version == 2
    assert Decimal("1") == entry.collectible_receivable.amount


def test_due_at_basis_is_taken_from_the_native_due_date_basis(monkeypatch):
    keys = _keys(2)
    exposures = {
        keys[0][1]: _exposure(keys[0][1], basis=DueDateBasisStatus.VERIFIED),
        keys[1][1]: _exposure(
            keys[1][1], due=None, basis=DueDateBasisStatus.UNKNOWN_UNVERIFIED
        ),
    }
    _install(monkeypatch, exposures)
    entries, _ = _walk(_Session(keys), limit=10)
    assert [e.due_at_basis for e in entries] == [
        DueDateBasisStatus.VERIFIED,
        DueDateBasisStatus.UNKNOWN_UNVERIFIED,
    ]


@pytest.mark.parametrize(
    ("collection_status", "basis", "expected"),
    [
        ("verified", DueDateBasisStatus.VERIFIED, DueDateBasisStatus.VERIFIED),
        (
            "verified",
            DueDateBasisStatus.UNKNOWN_UNVERIFIED,
            DueDateBasisStatus.UNKNOWN_UNVERIFIED,
        ),
        (
            "unknown_unverified",
            DueDateBasisStatus.VERIFIED,
            DueDateBasisStatus.UNKNOWN_UNVERIFIED,
        ),
    ],
)
def test_imported_item_due_basis_is_verified_only_if_both_are(
    monkeypatch, collection_status, basis, expected
):
    from dotmac_billing.opening_receivable_contracts import ReceivableExposureV2

    keys = _keys(1)
    document_id = keys[0][1]
    exposure = object.__new__(ReceivableExposureV2)
    for name, value in {
        "financial_state": "open",
        "collectible_receivable": Money.of("10", NGN),
        "source_version": 4,
        "collection": type(
            "Collection",
            (),
            {
                "status": collection_status,
                "due_at": T0,
                "service_ref": "service:imported",
                "due_date_basis": type("Basis", (), {"status": basis})(),
            },
        )(),
    }.items():
        object.__setattr__(exposure, name, value)
    _install(monkeypatch, {document_id: exposure})
    (entry,) = _walk(_Session(keys), limit=5)[0]
    assert entry.due_at_basis is expected
    assert entry.due_at == T0 and entry.service_ref == "service:imported"
