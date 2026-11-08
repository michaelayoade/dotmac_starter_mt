"""AccountingFactV6 against a real PostgreSQL: emission, reader, ordering, DB guards."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from uuid import uuid4

import pytest
from dotmac_billing import service as s
from dotmac_billing.document_queries import (
    ListAccountingFactsV1,
    SummarizeAccountingFactWindowV1,
    list_accounting_facts,
    summarize_accounting_fact_window,
)
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_commands import ReverseOpeningAccountImportV1
from dotmac_billing.opening_queries import _decode_row
from dotmac_billing.opening_variance import get_accounting_fact_v6
from dotmac_billing.opening_variance_contracts import (
    AccountingFactV6,
    OpeningVarianceResolutionRefV1,
    RecogniseOpeningVarianceV1,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests import test_billing_isolation as b
from tests import test_billing_opening_cash_import as cash
from tests import test_billing_opening_receivable_import as ar
from tests import test_billing_opening_variance_ledger as led
from tests.unit import test_billing_opening_receivable_contracts as evidence

scratch = led.scratch
_NOW = evidence.NOW


def _window():
    return _NOW - timedelta(days=1), _NOW + timedelta(days=1)


def _v6_groups(session) -> dict:
    rows = session.execute(
        text(
            "SELECT g.id, g.group_kind FROM mod_billing.posting_groups g "
            "WHERE g.group_kind LIKE 'opening_variance_%'"
        )
    ).all()
    return {r[0]: r[1] for r in rows}


def _outbox(session) -> Counter:
    rows = session.execute(
        text(
            "SELECT event_type, payload FROM public.outbox_events "
            "WHERE event_type LIKE 'billing.accounting.fact.v6' "
            "OR event_type LIKE 'billing.opening.variance.%'"
        )
    ).all()
    kinds: Counter = Counter()
    for event_type, payload in rows:
        kinds[(event_type, (payload or {}).get("effect_kind"))] += 1
    return kinds


def _resolve_all(session, scope, receipt):
    """Recognise +300 and correct -700 through a real credit note."""
    _records, by_amount = led._ids(session, scope)
    approvals = led.VarianceApprovals()
    s.recognise_opening_variance(
        session,
        scope=scope,
        command=RecogniseOpeningVarianceV1(
            scope,
            by_amount[300].variance_id,
            300,
            "bad_debt",
            uuid4(),
            "maker:v",
            f"recognise:{uuid4()}",
            _NOW,
        ),
        approvals=approvals,
    )
    first, _second = ar._bases(session, scope, receipt)
    ref = OpeningVarianceResolutionRefV1(
        by_amount[-700].variance_id, -700, uuid4(), "maker:v"
    )
    s.issue_credit_note(
        session,
        scope=scope,
        command=led._credit_note(first.document_id, "7", resolves_variance_ref=ref),
        numbering=b.NUMBERING,
        approvals=approvals,
    )
    session.commit()


def test_each_kind_is_one_effect_free_group_one_fact_and_one_v6_outbox_event(scratch):
    scope, url = led._scope(scratch)
    with cash._session(scratch, url, scope) as session:
        _cmd, receipt = led._imported(session, scope)
        _resolve_all(session, scope, receipt)
        groups = _v6_groups(session)
        assert Counter(groups.values()) == {
            "opening_variance_recorded": 2,  # memo and batch scope post nothing
            "opening_variance_recognised": 1,
            "opening_variance_position_corrected": 1,
        }
        # no posting effects, one fact per group, group kind == effect_kind
        for group, kind in groups.items():
            assert (
                session.scalar(
                    text(
                        "SELECT count(*) FROM mod_billing.posting_effects "
                        "WHERE posting_group_id = :g"
                    ),
                    {"g": group},
                )
                == 0
            )
            fact = get_accounting_fact_v6(session, scope=scope, posting_group_id=group)
            assert fact.effect_kind == kind and fact.posting_group_id == group
            assert fact.reverses_fact_id is None
        outbox = _outbox(session)
        assert set(outbox.values()) == {2, 1}  # recorded x2; the others once
        assert {k[0] for k in outbox} == {"billing.accounting.fact.v6"}
        assert Counter(k[1] for k in outbox.elements()) == {
            "opening_variance_recorded": 2,
            "opening_variance_recognised": 1,
            "opening_variance_position_corrected": 1,
        }


def test_v6_round_trips_and_a_tampered_payload_is_refused(scratch):
    scope, url = led._scope(scratch)
    with cash._session(scratch, url, scope) as session:
        led._imported(session, scope)
        group = next(iter(_v6_groups(session)))
        fact = get_accounting_fact_v6(session, scope=scope, posting_group_id=group)
        assert isinstance(fact, AccountingFactV6) and fact.fact_version == 6
        assert (
            get_accounting_fact_v6(session, scope=scope, posting_group_id=group) == fact
        )
        row = (
            session.execute(
                text(
                    "SELECT * FROM mod_billing.accounting_facts "
                    "WHERE posting_group_id = :g"
                ),
                {"g": group},
            )
            .mappings()
            .one()
        )
        assert (
            _decode_row(
                dict(row),
                AccountingFactV6,
                payload="fact_payload",
                digest="fact_digest",
            )
            == fact
        )
        forged = dict(row)
        forged["fact_payload"] = {**row["fact_payload"], "source_ref": "x"}
        with pytest.raises(BillingRuleViolation):
            _decode_row(
                forged, AccountingFactV6, payload="fact_payload", digest="fact_digest"
            )
        with pytest.raises(BillingRuleViolation):  # the V5 reader never sees V6
            from dotmac_billing import get_accounting_fact_v5

            get_accounting_fact_v5(session, scope=scope, posting_group_id=group)


def test_the_ordered_index_and_summary_include_v6(scratch):
    scope, url = led._scope(scratch)
    with cash._session(scratch, url, scope) as session:
        led._imported(session, scope)
        start, end = _window()
        page = list_accounting_facts(
            session,
            scope=scope,
            request=ListAccountingFactsV1(scope, start, end, limit=500),
        )
        summary = summarize_accounting_fact_window(
            session,
            scope=scope,
            request=SummarizeAccountingFactWindowV1(scope, start, end),
        )
        v6 = [e for e in page.entries if e.effect_kind.startswith("opening_variance_")]
        assert len(v6) == 2 and summary.count == len(page.entries)
        assert {e.source_ref.split(":")[0] for e in v6} == {"opening-variance-event"}


def test_the_pass4_property_facts_equal_open_ledger_variances(scratch):
    scope, url = led._scope(scratch)
    with cash._session(scratch, url, scope) as session:
        _cmd, receipt = led._imported(session, scope)
        _resolve_all(session, scope, receipt)
        total = {"opening_variance_recorded": 0, "opening_variance_voided": 0}
        resolved = 0
        for group, kind in _v6_groups(session).items():
            fact = get_accounting_fact_v6(session, scope=scope, posting_group_id=group)
            minor = int(fact.variance.amount.amount * 100)
            if kind in total:
                total[kind] += minor
            else:
                resolved += minor
        open_ledger = session.scalar(
            text(
                "SELECT coalesce(sum(v.amount_minor), 0) FROM mod_billing."
                "opening_variances v WHERE v.basis = 'ledger_lane' "
                "AND v.scope_level = 'partition'"
            )
        )
        # sum(recorded) - sum(voided) - sum(resolutions) is what is still open
        # (here both ledger variances are fully resolved, so nothing is)
        still_open = (
            total["opening_variance_recorded"]
            - total["opening_variance_voided"]
            - resolved
        )
        assert open_ledger == -400 and still_open == 0


def test_a_void_emits_a_voided_fact_reversing_the_recorded_one(scratch):
    scope, url = led._scope(scratch)
    with cash._session(scratch, url, scope) as session:
        cmd, receipt = led._imported(session, scope, seal=False)
        recorded = {
            fact.fact_id
            for fact in (
                get_accounting_fact_v6(session, scope=scope, posting_group_id=g)
                for g in _v6_groups(session)
            )
        }
        with cash._as_opening_principal(session):
            s.reverse_opening_account_import_v2(
                session,
                scope=scope,
                command=ReverseOpeningAccountImportV1(
                    scope, receipt.receipt_id, "fresh", str(uuid4()), _NOW
                ),
                verifier=evidence.TrustedVerifier(),
            )
        session.commit()
        facts = [
            get_accounting_fact_v6(session, scope=scope, posting_group_id=g)
            for g in _v6_groups(session)
        ]
        voided = [f for f in facts if f.effect_kind == "opening_variance_voided"]
        assert len(voided) == 2 and {f.reverses_fact_id for f in voided} == recorded
        # the groups' reverses_group_id stays NULL: only a fact-level link
        assert (
            session.scalar(
                text(
                    "SELECT count(*) FROM mod_billing.posting_groups "
                    "WHERE group_kind = 'opening_variance_voided' "
                    "AND reverses_group_id IS NOT NULL"
                )
            )
            == 0
        )
        outbox = _outbox(session)
        assert {k[0] for k in outbox} == {"billing.accounting.fact.v6"}
        assert (
            sum(n for k, n in outbox.items() if k[1] == "opening_variance_voided") == 2
        )


def test_database_refuses_an_event_without_its_matching_fact_group(scratch):
    scope, url = led._scope(scratch)
    with cash._session(scratch, url, scope) as session:
        cmd, receipt = led._imported(session, scope)
        row = led._row(session, 300)
        req = uuid4()
        led._event(session, row["window_id"], row, req=req)
        session.commit()
        wrong = receipt.posting_group_id  # a real group of the wrong kind
        for fact in (uuid4(), wrong):  # no group at all; a group of another kind
            led._event(
                session,
                row["window_id"],
                row,
                kind="resolved",
                rk="recognised",
                amount=300,
                gl="bad_debt",
                req=req,
                fact=fact,
            )
            with pytest.raises(DBAPIError) as refused:
                session.commit()  # the group is checked at commit
            assert refused.value.orig.sqlstate == "23514"
            session.rollback()


def test_the_online_role_cannot_create_recorded_or_voided_groups(scratch):
    scope, url = led._scope(scratch)
    with cash._session(scratch, url, scope) as session:
        cmd, _receipt = led._imported(session, scope)
        account = cmd.partition.account_id
        tenant = session.scalar(
            text("SELECT tenant_id FROM mod_billing.opening_windows")
        )

        version = (
            session.scalar(
                text(
                    "SELECT max(source_version) FROM mod_billing.posting_groups "
                    "WHERE billing_account_id = :a"
                ),
                {"a": account},
            )
            + 1
        )

        def insert(kind: str) -> None:
            session.execute(
                text(
                    "INSERT INTO mod_billing.posting_groups (id, tenant_id, "
                    "billing_account_id, group_kind, currency, minor_units, "
                    "source_ref, source_version, occurred_at) VALUES (:id, :t, :a, "
                    ":k, 'NGN', 2, 'x', :v, now())"
                ),
                {"id": uuid4(), "t": tenant, "a": account, "k": kind, "v": version},
            )

        for kind in ("opening_variance_recorded", "opening_variance_voided"):
            with pytest.raises(DBAPIError) as refused:
                with session.begin_nested():
                    insert(kind)
            assert "restricted opening principal required" in str(refused.value)
        # the online kinds are not opening-principal kinds: the guard lets them by
        with session.begin_nested() as savepoint:
            insert("opening_variance_recognised")
            savepoint.rollback()


def test_a_replay_adds_no_facts(scratch):
    scope, url = led._scope(scratch)
    with cash._session(scratch, url, scope) as session:
        cmd, receipt = led._imported(session, scope, seal=False)
        before = len(_v6_groups(session)), sum(_outbox(session).values())
        again = cash._import_command(
            session,
            scope=scope,
            command=cmd,
            verifier=evidence.TrustedVerifier(),
            version=2,
        )
        session.commit()
        assert again == receipt
        assert (len(_v6_groups(session)), sum(_outbox(session).values())) == before
        assert before[0] == 2
