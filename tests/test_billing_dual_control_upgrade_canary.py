"""bi_0018 -> bi_0019 upgrade canary and guard catalog (both planes).

A window and hold admission written under bi_0018 (no ``dual_control`` key) must
read back as "on", keep their stored digests, still admit with an approval and
refuse every approval-free shape in the database after the upgrade.
"""

# SQL identifiers come only from fixed table names.
# ruff: noqa: S608
import json
from functools import reduce
from uuid import uuid4

import pytest
from dotmac_billing import service as s
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_variance_contracts import (
    ConfirmOpeningVarianceEvidenceV1,
    RecogniseOpeningVarianceV1,
)
from dotmac_billing.opening_window_queries import (
    get_opening_hold_admissions,
    get_opening_window,
)
from dotmac_kernel.cache import PlatformScope
from dotmac_kernel.fingerprints import fingerprint_of
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from alembic import command as alembic_command
from tests import test_billing_isolation as b
from tests import test_billing_opening_cash_import as cash
from tests import test_billing_opening_hold_admission as hold
from tests import test_billing_opening_receivable_import as ar
from tests import test_billing_opening_variance_ledger as vl
from tests.unit import test_billing_opening_receivable_contracts as evidence

PREDECESSOR = "bi_0018_account_terms"
TARGET = "bi_0019_opening_dual_control"


def _prefix(plane):
    return "" if plane == "tenant" else "platform_"


def _stripped_jsonable():
    """``service._jsonable`` as bi_0018 stored a window: no ``dual_control`` key."""
    original = s._jsonable

    def stripped(value):
        encoded = original(value)
        if "window" in encoded and set(encoded) <= {"window", "verification"}:
            encoded["window"].pop("dual_control", None)
        return encoded

    return stripped


@pytest.fixture(params=["tenant", "platform"])
def plane(request):
    return request.param


@pytest.fixture
def at_predecessor(plane):
    selection = ModulePlane.TENANT if plane == "tenant" else ModulePlane.PLATFORM
    with b._scratch_database((selection,), target_revision=PREDECESSOR) as urls:
        with cash._opening_scratch(urls, planes=(plane,)) as scratch:

            def upgrade():
                alembic_command.upgrade(cash.predecessor._config((selection,)), TARGET)

            yield scratch, upgrade


def _partitions(session, scope, count):
    accounts = [cash._new_account(session, scope) for _ in range(count)]
    commands = [ar._command(session, scope, account) for account in accounts]
    first = commands[0]

    def merged(name):
        return reduce(
            lambda a, c: a + getattr(c.manifest, name),
            commands[1:],
            getattr(first.manifest, name),
        )

    from dataclasses import fields, replace

    manifest = replace(
        first.manifest,
        population=merged("population"),
        dispositions=merged("dispositions"),
        controls=merged("controls"),
        receivables=merged("receivables"),
        cash_lots=merged("cash_lots"),
        measures=merged("measures"),
        counts=replace(
            first.manifest.counts,
            **{
                f.name: sum(getattr(c.manifest.counts, f.name) for c in commands)
                for f in fields(first.manifest.counts)
            },
        ),
    )
    out = [replace(first, manifest=manifest)]
    for command, account in zip(commands[1:], accounts[1:], strict=True):
        out.append(
            replace(command, manifest=manifest, idempotency_key=f"hold:{account}")
        )
    return out


def _rows(session, plane, name, columns="id, evidence, evidence_digest"):
    return session.execute(
        text(f"SELECT {columns} FROM mod_billing.{_prefix(plane)}{name} ORDER BY id")
    ).all()


def _raw_hold(session, scope, plane, receipt, *, request, refs, approval, dual):
    window = session.info["opening_scratch"].windows[scope]
    body = {
        "maker_ref": hold.MAKER,
        "receipt_id": str(receipt.receipt_id),
        "billing_account_id": str(receipt.billing_account_id),
        "window_id": str(window.window_id),
        "disposition": {"disposition_digest": hold.DIGEST},
        "approval": approval,
    }
    if dual is not None:
        body["dual_control"] = dual
    columns = "id, window_id, billing_account_id, receipt_id, disposition_digest, "
    values = ":id, :w, :a, :r, :d, "
    params = {
        "id": uuid4(),
        "w": window.window_id,
        "a": receipt.billing_account_id,
        "r": receipt.receipt_id,
        "d": hold.DIGEST,
        "m": hold.MAKER,
        "req": request,
        "refs": json.dumps(refs),
        "ev": json.dumps(body),
        "dg": "0" * 64,
    }
    if plane == "tenant":
        columns, values = columns + "tenant_id, ", values + ":t, "
        params["t"] = scope.tenant_id
    session.execute(
        text(
            f"INSERT INTO mod_billing.{_prefix(plane)}opening_hold_admissions "
            f"({columns}maker_ref, approval_request_id, approver_refs, evidence, "
            f"evidence_digest) VALUES ({values}:m, :req, CAST(:refs AS json), "
            "CAST(:ev AS json), :dg)"
        ),
        params,
    )


def test_a_legacy_window_and_hold_admission_survive_the_upgrade(
    at_predecessor, plane, monkeypatch
):
    scratch, upgrade = at_predecessor
    scope, url = cash.runtime._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as session:
        admitted, h1, h2, h3 = _partitions(session, scope, 4)
        with monkeypatch.context() as legacy:
            legacy.setattr(s, "_jsonable", _stripped_jsonable())
            cash._prepare_window(session, scope, admitted, "on")
        session.commit()
        hold._import(session, scope, admitted)
        session.commit()
        held = [hold._held_id(c) for c in (h1, h2, h3)]
        hold._seal(session, scope, held=held)
        session.commit()
        hold._admit(session, scope, h1)  # the old admission, with its approval
        session.commit()
        window_before = _rows(session, plane, "opening_windows")
        hold_before = _rows(session, plane, "opening_hold_admissions")
        assert "dual_control" not in window_before[0].evidence["window"]
        assert "dual_control" not in hold_before[0].evidence
        old_admissions = get_opening_hold_admissions(session, scope=scope)
        session.rollback()

        upgrade()

    with cash._session(scratch, url, scope) as session:
        # 2. Old rows read back as "on"; the stored bytes and digests are unchanged.
        assert get_opening_window(session, scope=scope).dual_control == "on"
        assert get_opening_hold_admissions(session, scope=scope) == old_admissions
        assert old_admissions[0].dual_control == "on"
        assert _rows(session, plane, "opening_windows") == window_before
        assert _rows(session, plane, "opening_hold_admissions") == hold_before
        for row in window_before + hold_before:
            assert fingerprint_of(row.evidence) == row.evidence_digest
        # 3. A new admission on the legacy window succeeds with an approval and
        # stores no dual_control key (byte-identical ON evidence).
        hold._admit(session, scope, h2)
        session.commit()
        latest = _rows(session, plane, "opening_hold_admissions")
        assert len(latest) == 2
        for row in latest:
            assert "dual_control" not in row.evidence
            assert row.evidence["approval"] is not None
        assert all(
            item.dual_control == "on"
            for item in get_opening_hold_admissions(session, scope=scope)
        )
        # 4. The database refuses approval-free shapes on the legacy window.
        receipt = hold._bypass_import(session, scope, h3)
        stand = {"request_id": str(uuid4()), "requester_ref": hold.MAKER}
        for values in (
            {"request": None, "refs": [hold.CHECKER], "approval": stand, "dual": None},
            {"request": None, "refs": [], "approval": None, "dual": "off"},
            {"request": None, "refs": [], "approval": None, "dual": None},
        ):
            with cash._as_opening_principal(session):
                with pytest.raises(DBAPIError) as refused:
                    with session.begin_nested():
                        _raw_hold(session, scope, plane, receipt, **values)
            assert refused.value.orig.sqlstate == "23514"
        session.rollback()


def _events(session, plane, table, id_column):
    return session.execute(
        text(
            f"SELECT {id_column} AS subject, window_id, billing_account_id"
            + (", tenant_id" if plane == "tenant" else "")
            + f" FROM mod_billing.{_prefix(plane)}{table}"
        )
    ).mappings()


def _raw_event(session, plane, subject, *, gap, **values):
    row = {
        "id": uuid4(),
        "w": subject["window_id"],
        "s": subject["subject"],
        "a": subject["billing_account_id"],
        "maker": "maker:v",
        "refs": "[]",
        "req": None,
        "actor": "maker:v",
        "ev": "rebuild-run:1",
        **values,
    }
    scope_column = ", tenant_id" if plane == "tenant" else ""
    scope_value = ", :t" if plane == "tenant" else ""
    if plane == "tenant":
        row["t"] = subject["tenant_id"]
    p = _prefix(plane)
    if gap:
        sql = (
            f"INSERT INTO mod_billing.{p}opening_control_gap_events (id{scope_column}, "
            "window_id, gap_id, billing_account_id, kind, evidence_ref, maker_ref, "
            "approver_refs, approval_request_id, actor, occurred_at) "
            f"VALUES (:id{scope_value}, :w, :s, :a, 'resolved', :ev, :maker, "
            "CAST(:refs AS jsonb), :req, :actor, now())"
        )
    else:
        sql = (
            f"INSERT INTO mod_billing.{p}opening_variance_events (id{scope_column}, "
            "window_id, variance_id, billing_account_id, kind, maker_ref, "
            "approver_refs, approval_request_id, actor, occurred_at) "
            f"VALUES (:id{scope_value}, :w, :s, :a, 'resolving', :maker, "
            "CAST(:refs AS jsonb), :req, :actor, now())"
        )
    session.execute(text(sql), row)


def test_a_legacy_window_still_requires_an_approval_for_variances_and_gaps(
    at_predecessor, plane, monkeypatch
):
    scratch, upgrade = at_predecessor
    scope, url = cash.runtime._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as session:
        with monkeypatch.context() as legacy:
            legacy.setattr(s, "_jsonable", _stripped_jsonable())
            vl._imported(session, scope, gap=True, dual_control="on")
        session.commit()
        upgrade()

    with cash._session(scratch, url, scope) as session:
        assert get_opening_window(session, scope=scope).dual_control == "on"
        records, by_amount = vl._ids(session, scope)

        def recognise(amount, **overrides):
            return RecogniseOpeningVarianceV1(
                scope,
                by_amount[amount].variance_id,
                amount,
                "bad_debt",
                overrides.pop("request", None),
                "maker:v",
                f"recognise:{uuid4()}",
                evidence.NOW,
            )

        with pytest.raises(BillingRuleViolation) as refused:
            s.recognise_opening_variance(
                session,
                scope=scope,
                command=recognise(300),
                approvals=vl.VarianceApprovals(),
            )
        assert refused.value.code.endswith("opening_variance_approval_missing")
        session.rollback()
        with pytest.raises(BillingRuleViolation) as refused:
            s.confirm_opening_variance_evidence(
                session,
                scope=scope,
                command=ConfirmOpeningVarianceEvidenceV1(
                    scope,
                    by_amount[500].variance_id,
                    500,
                    "evidence:memo",
                    None,
                    "maker:v",
                    f"confirm:{uuid4()}",
                    evidence.NOW,
                ),
                approvals=vl.VarianceApprovals(),
            )
        assert refused.value.code.endswith("opening_variance_approval_missing")
        session.rollback()
        # The database refuses the approval-free (off-shaped) resolution itself.
        variance = _events(session, plane, "opening_variances", "id").all()[0]
        gap = _events(session, plane, "opening_control_gaps", "id").all()[0]
        for subject, is_gap in ((variance, False), (gap, True)):
            with pytest.raises(DBAPIError) as refused:
                with session.begin_nested():
                    _raw_event(session, plane, subject, gap=is_gap)
            assert refused.value.orig.sqlstate == "23514"
        # Positive control: the two-person path still resolves on the legacy window.
        assert (
            s.recognise_opening_variance(
                session,
                scope=scope,
                command=recognise(300, request=uuid4()),
                approvals=vl.VarianceApprovals(),
            )
            == "resolved"
        )
        session.rollback()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_the_rewritten_guards_use_the_policy_helper_exactly_once(plane):
    selection = ModulePlane.TENANT if plane == "tenant" else ModulePlane.PLATFORM
    p = _prefix(plane)
    with b._scratch_database((selection,)) as urls:
        with b._session(urls[0], PlatformScope()) as session:
            definition = {
                name: session.scalar(
                    text(
                        "SELECT pg_get_functiondef("
                        f"'mod_billing.{name}()'::regprocedure)"
                    )
                )
                for name in (
                    f"guard_{p}opening_variance_event",
                    f"guard_{p}opening_gap_event",
                    f"guard_{p}opening_hold_admission",
                    f"guard_{p}opening_window",
                )
            }
    helper = f"{p}opening_resolution_approval_ok("
    for name in (f"guard_{p}opening_variance_event", f"guard_{p}opening_gap_event"):
        assert definition[name].count(helper) == 1, name
        assert "mod_billing.opening_approval_ok(" not in definition[name], name
    hold_guard = definition[f"guard_{p}opening_hold_admission"]
    assert "dual_control" in hold_guard
    assert "opening_approval_ok" not in hold_guard
    assert "'dual_control'" in definition[f"guard_{p}opening_window"]
