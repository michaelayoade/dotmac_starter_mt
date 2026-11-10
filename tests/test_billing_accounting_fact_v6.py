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


# -- Security findings 1-2: no orphan group, fact-less event or forged fact ----------


def _variance_row(session, amount: int):
    return (
        session.execute(
            text("SELECT * FROM mod_billing.opening_variances WHERE amount_minor = :a"),
            {"a": amount},
        )
        .mappings()
        .one()
    )


def _next_version(session, account) -> int:
    return (
        session.scalar(
            text(
                "SELECT coalesce(max(source_version), 0) FROM "
                "mod_billing.posting_groups WHERE billing_account_id = :a"
            ),
            {"a": account},
        )
        + 1
    )


def _raw_group(session, tenant, group, account, kind, currency="NGN"):
    session.execute(
        text(
            "INSERT INTO mod_billing.posting_groups (id, tenant_id, "
            "billing_account_id, group_kind, currency, minor_units, source_ref, "
            "source_version, occurred_at) VALUES (:id, :t, :a, :k, :c, 2, 'forged', "
            ":v, now())"
        ),
        {
            "id": group,
            "t": tenant,
            "a": account,
            "k": kind,
            "c": currency,
            "v": _next_version(session, account),
        },
    )


def _raw_fact(session, scope, variance, group, account, event_id, seq, **edits):
    """A contract-valid V6 recognised fact, with optional payload edits."""
    from decimal import Decimal

    from dotmac_billing.opening_variance_contracts import (
        OpeningVarianceFactBlockV1,
        OpeningVarianceResolutionFactV1,
    )
    from dotmac_billing.service import _jsonable
    from dotmac_kernel.fingerprints import fingerprint_of
    from dotmac_kernel.money import Currency, Money

    fact = AccountingFactV6(
        scope,
        "dotmac-billing",
        edits.pop("fact_id", uuid4()),
        6,
        account,
        group,
        f"opening-variance-event:{event_id}",
        "internal",
        "opening_variance_recognised",
        _NOW,
        _NOW,
        OpeningVarianceFactBlockV1(
            variance["id"],
            variance["window_id"],
            event_id,
            seq,
            variance["lane"],
            "ledger_lane",
            variance["reason_code"],
            variance["source_kind"],
            variance["source_ref"],
            Money(Decimal(edits.pop("minor", 300)) / 100, Currency("NGN", 2)),
            False,
            variance["import_receipt_id"],
            variance["evidence_digest"],
            variance["rules_digest"],
        ),
        OpeningVarianceResolutionFactV1(
            "recognised",
            "resolved",
            "bad_debt",
            None,
            edits.pop("approval", None) or edits["req"],
            "maker:v",
            edits.pop("approvers", ("checker:v",)),
        ),
        None,
    )
    edits.pop("req", None)
    payload = _jsonable(fact)
    session.execute(
        text(
            "INSERT INTO mod_billing.accounting_facts (id, tenant_id, "
            "posting_group_id, fact_version, source_system, source_authority, "
            "effect_kind, fact_digest, fact_payload, occurred_at) VALUES (:id, :t, "
            ":g, 6, 'dotmac-billing', 'internal', 'opening_variance_recognised', "
            ":d, CAST(:p AS json), :o)"
        ),
        {
            "id": fact.fact_id,
            "t": variance["tenant_id"],
            "g": group,
            "d": fingerprint_of(payload),
            "p": __import__("json").dumps(payload),
            "o": _NOW,
        },
    )


def _forge(session, scope, *, event=True, fact=True, kind=None, account=None, **kw):
    """Forge a recognised event, group and fact as the online role.

    Returns after the inserts; the caller commits (or not) and asserts."""
    variance = _variance_row(session, 300)
    group, req = uuid4(), uuid4()
    acct = account or variance["billing_account_id"]
    event_id, seq = uuid4(), 1
    if event:
        led._event(session, variance["window_id"], _row_of(variance), req=req)
        event_id, seq = led._event(
            session,
            variance["window_id"],
            _row_of(variance),
            kind="resolved",
            rk="recognised",
            amount=300,
            gl="bad_debt",
            req=req,
            fact=group,
        )
    _raw_group(
        session,
        variance["tenant_id"],
        group,
        acct,
        kind or "opening_variance_recognised",
        kw.pop("currency", "NGN"),
    )
    if fact:
        _raw_fact(session, scope, variance, group, acct, event_id, seq, req=req, **kw)
    return group


def _row_of(variance):
    return {
        "id": variance["id"],
        "tenant_id": variance["tenant_id"],
        "billing_account_id": variance["billing_account_id"],
        "window_id": variance["window_id"],
    }


def _commit_refused(session) -> str:
    with pytest.raises(DBAPIError) as refused:
        session.commit()
    assert refused.value.orig.sqlstate == "23514"
    message = str(refused.value.orig)
    session.rollback()
    return message


def _immediate(session, name: str) -> None:
    session.execute(text(f"SET CONSTRAINTS mod_billing.{name} IMMEDIATE"))


def _refused_now(session, work) -> str:
    with pytest.raises(DBAPIError) as refused:
        work()
    assert refused.value.orig.sqlstate == "23514"
    message = str(refused.value.orig)
    session.rollback()
    return message


def test_an_orphan_group_without_event_or_fact_is_refused(scratch):
    scope, url = led._scope(scratch)
    with cash._session(scratch, url, scope) as session:
        led._imported(session, scope)
        # the group-side check in isolation (other commit-time guards also refuse)
        _immediate(session, "variance_group_complete")
        message = _refused_now(
            session, lambda: _forge(session, scope, event=False, fact=False)
        )
        assert "exactly one matching event" in message
        # and at commit, whatever guard fires first
        _forge(session, scope, event=False, fact=False)
        assert _commit_refused(session)


def test_an_orphan_group_with_a_fact_but_no_event_is_refused(scratch):
    scope, url = led._scope(scratch)
    with cash._session(scratch, url, scope) as session:
        led._imported(session, scope)
        message = _refused_now(
            session, lambda: _forge(session, scope, event=False, fact=True)
        )
        assert "needs its event" in message


def test_an_event_and_group_without_a_fact_is_refused(scratch):
    scope, url = led._scope(scratch)
    with cash._session(scratch, url, scope) as session:
        led._imported(session, scope)
        _forge(session, scope, fact=False)  # event first, then the group, no fact
        message = _refused_now(
            session, lambda: _immediate(session, "variance_event_fact_group")
        )
        assert "exactly one V6 fact on its group" in message
        _forge(session, scope, fact=False)
        assert _commit_refused(session)  # and at commit
        _immediate(session, "variance_group_complete")  # group side, in isolation
        assert "exactly one V6 fact of its kind" in _refused_now(
            session, lambda: _forge(session, scope, fact=False)
        )


def test_an_effect_on_a_variance_group_is_refused_now_and_later(scratch):
    scope, url = led._scope(scratch)
    with cash._session(scratch, url, scope) as session:
        cmd, receipt = led._imported(session, scope)
        _resolve_all(session, scope, receipt)
        recognised = next(
            g for g, k in _v6_groups(session).items() if k.endswith("recognised")
        )
        tenant = session.scalar(
            text("SELECT tenant_id FROM mod_billing.opening_windows")
        )

        def effect(group):
            session.execute(
                text(
                    "INSERT INTO mod_billing.posting_effects (id, tenant_id, "
                    "posting_group_id, billing_account_id, lane, amount_delta, "
                    "currency, minor_units) VALUES (:id, :t, :g, :a, 'receivable', "
                    "1, 'NGN', 2)"
                ),
                {
                    "id": uuid4(),
                    "t": tenant,
                    "g": group,
                    "a": cmd.partition.account_id,
                },
            )

        # later: the committed group, a fresh transaction
        assert "carries no posting effects" in _refused_now(
            session, lambda: effect(recognised)
        )
        # same transaction: a group created and given an effect before commit
        forged = _forge(session, scope, event=False, fact=False)
        assert "carries no posting effects" in _refused_now(
            session, lambda: effect(forged)
        )


@pytest.mark.parametrize(
    "edit",
    [
        {},  # the fact names another event
        {"minor": 301},
        {"approvers": ("checker:other",)},
        {"approval": "random"},
    ],
    ids=["event_id", "amount", "approvers", "approval_request"],
)
def test_a_fact_whose_payload_differs_from_its_event_is_refused(scratch, edit):
    scope, url = led._scope(scratch)
    with cash._session(scratch, url, scope) as session:
        led._imported(session, scope)
        edit = dict(edit)
        if edit.get("approval") == "random":
            edit["approval"] = uuid4()
        if not edit:
            variance = _variance_row(session, 300)
            group, req = uuid4(), uuid4()
            led._event(session, variance["window_id"], _row_of(variance), req=req)
            led._event(
                session,
                variance["window_id"],
                _row_of(variance),
                kind="resolved",
                rk="recognised",
                amount=300,
                gl="bad_debt",
                req=req,
                fact=group,
            )
            _raw_group(
                session,
                variance["tenant_id"],
                group,
                variance["billing_account_id"],
                "opening_variance_recognised",
            )

            def work():
                _raw_fact(
                    session,
                    scope,
                    variance,
                    group,
                    variance["billing_account_id"],
                    uuid4(),
                    1,
                    req=req,
                )
        else:

            def work():
                _forge(session, scope, **edit)

        assert "differs from its" in _refused_now(session, work)


def test_binding_mismatches_are_each_refused_by_their_own_check(scratch):
    scope, url = led._scope(scratch)
    with cash._session(scratch, url, scope) as session:
        led._imported(session, scope)
        other = cash._new_account(session, scope)
        session.commit()

        def forged_then_checked(**kw):
            _forge(session, scope, fact=False, **kw)
            _immediate(session, "variance_event_fact_group")

        assert "kind differs" in _refused_now(
            session,
            lambda: forged_then_checked(kind="opening_variance_position_corrected"),
        )
        assert "account differs" in _refused_now(
            session, lambda: forged_then_checked(account=other)
        )
        assert "currency differs" in _refused_now(
            session, lambda: forged_then_checked(currency="USD")
        )


def test_the_service_path_is_refused_under_set_constraints_all_immediate(scratch):
    """Documents the ordering: event, group, fact, then the commit-time check."""
    scope, url = led._scope(scratch)
    with cash._session(scratch, url, scope) as session:
        cmd = led._carrying_command(session, scope)
        cash._prepare_window(session, scope, cmd)
        session.commit()
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        with pytest.raises(DBAPIError) as refused:
            cash._import_command(
                session,
                scope=scope,
                command=cmd,
                verifier=evidence.TrustedVerifier(),
                version=2,
            )
        assert refused.value.orig.sqlstate == "23514"
        session.rollback()


def test_partial_resolution_and_void_keep_the_pass4_sums(scratch):
    scope, url = led._scope(scratch)
    with cash._session(scratch, url, scope) as session:
        _cmd, _receipt = led._imported(session, scope)
        _records, by_amount = led._ids(session, scope)
        approvals = led.VarianceApprovals()
        state = s.recognise_opening_variance(
            session,
            scope=scope,
            command=RecogniseOpeningVarianceV1(
                scope,
                by_amount[300].variance_id,
                100,
                "bad_debt",
                uuid4(),
                "maker:v",
                f"recognise:{uuid4()}",
                _NOW,
            ),
            approvals=approvals,
        )
        session.commit()
        assert state == "partially_resolved"
        recorded = resolved = 0
        for group, kind in _v6_groups(session).items():
            fact = get_accounting_fact_v6(session, scope=scope, posting_group_id=group)
            minor = int(fact.variance.amount.amount * 100)
            if kind == "opening_variance_recorded":
                recorded += minor
            else:
                assert kind == "opening_variance_recognised" and minor == 100
                resolved += minor
        done = session.scalar(
            text(
                "SELECT coalesce(sum(amount_minor), 0) FROM "
                "mod_billing.opening_variance_events WHERE kind IN "
                "('resolved','partially_resolved')"
            )
        )
        total = session.scalar(
            text(
                "SELECT sum(amount_minor) FROM mod_billing.opening_variances "
                "WHERE basis = 'ledger_lane'"
            )
        )
        # sum(recorded) - sum(resolutions) is exactly what the variances still hold
        assert recorded - resolved == total - done == -500


def test_after_a_void_recorded_minus_voided_is_zero(scratch):
    scope, url = led._scope(scratch)
    with cash._session(scratch, url, scope) as session:
        _cmd, receipt = led._imported(session, scope, seal=False)
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
        sums = Counter()
        for group, kind in _v6_groups(session).items():
            fact = get_accounting_fact_v6(session, scope=scope, posting_group_id=group)
            sums[kind] += int(fact.variance.amount.amount * 100)
        assert sums["opening_variance_recorded"] == -400
        assert sums["opening_variance_voided"] == -400
        assert sums["opening_variance_recorded"] - sums["opening_variance_voided"] == 0
