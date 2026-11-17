"""Account terms: typed command contracts and exports (no database)."""

from __future__ import annotations

import dataclasses
import inspect
import uuid
from datetime import UTC, datetime, timedelta

import dotmac_billing
import pytest
from dotmac_billing import (
    BillingRuleViolation,
    RecordAccountTermsV1,
    RecordDefaultTermsV1,
)
from dotmac_billing import account_terms as terms
from dotmac_kernel.cache import PlatformScope, TenantScope

NOW = datetime(2030, 1, 1, tzinfo=UTC)
SCOPE = TenantScope(uuid.uuid4())


def _account(**changes):
    values = {
        "scope": SCOPE,
        "billing_account_id": uuid.uuid4(),
        "effective_from": NOW,
        "actor_ref": "actor",
        "reason": "reason",
    }
    values.update(changes)
    return RecordAccountTermsV1(**values)


def _default(**changes):
    values = {
        "scope": SCOPE,
        "effective_from": NOW,
        "contract_type": "prepaid",
        "period": "monthly",
        "postpaid_billing_timing": "period_end",
        "postpaid_grace": timedelta(days=2),
        "actor_ref": "actor",
        "reason": "reason",
    }
    values.update(changes)
    return RecordDefaultTermsV1(**values)


def test_account_terms_default_every_term_to_none_and_are_frozen():
    command = _account()
    assert (
        command.contract_type,
        command.period,
        command.postpaid_billing_timing,
        command.postpaid_grace,
    ) == (None, None, None, None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        command.reason = "changed"  # type: ignore[misc]
    assert isinstance(_account(scope=PlatformScope()), RecordAccountTermsV1)


@pytest.mark.parametrize(
    "changes",
    [
        {"contract_type": "hybrid"},
        {"period": "weekly"},
        {"postpaid_billing_timing": "mid_period"},
        {"postpaid_grace": timedelta(days=-1)},
        {"postpaid_grace": 3},
        {"effective_from": datetime(2030, 1, 1)},
        {"effective_from": "2030-01-01"},
        {"actor_ref": "  "},
        {"reason": ""},
        {"billing_account_id": "not-a-uuid"},
        {"scope": object()},
    ],
)
def test_account_terms_refuse_invalid_input(changes):
    with pytest.raises(BillingRuleViolation):
        _account(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"contract_type": None},
        {"period": None},
        {"postpaid_billing_timing": None},
        {"postpaid_grace": None},
        {"contract_type": "hybrid"},
        {"postpaid_grace": timedelta(seconds=-1)},
        {"effective_from": datetime(2030, 1, 1)},
        {"reason": " "},
    ],
)
def test_default_terms_require_every_term(changes):
    with pytest.raises(BillingRuleViolation):
        _default(**changes)


def test_zero_grace_and_every_period_are_accepted():
    for period in ("daily", "monthly", "quarterly", "annually"):
        assert _default(period=period, postpaid_grace=timedelta(0)).period == period
    assert _account(postpaid_grace=timedelta(0)).postpaid_grace == timedelta(0)


def test_effective_term_carries_its_source_and_version():
    version = uuid.uuid4()
    term = terms.EffectiveTermV1("daily", "customer", version)
    assert (term.value, term.source, term.version_id) == ("daily", "customer", version)
    with pytest.raises(dataclasses.FrozenInstanceError):
        term.value = "monthly"  # type: ignore[misc]


def test_commands_are_exported_and_take_no_clock_or_commit():
    for name in (
        "AccountTermsV1",
        "EffectiveTermV1",
        "RecordAccountTermsV1",
        "RecordDefaultTermsV1",
        "TermsVersionRefV1",
        "get_effective_account_terms",
        "record_account_terms",
        "record_default_terms",
    ):
        assert name in dotmac_billing.__all__
        assert hasattr(dotmac_billing, name)
    source = inspect.getsource(terms)
    for forbidden in (".commit(", "datetime.now", "utcnow", "time.time", "date.today"):
        assert forbidden not in source
    assert list(inspect.signature(terms.get_effective_account_terms).parameters) == [
        "db",
        "scope",
        "account_id",
        "at",
    ]


def test_read_refuses_naive_instants_and_bad_ids_before_touching_the_database():
    with pytest.raises(BillingRuleViolation):
        terms.get_effective_account_terms(
            None,
            scope=SCOPE,
            account_id=uuid.uuid4(),
            at=datetime(2030, 1, 1),  # type: ignore[arg-type]
        )
    with pytest.raises(BillingRuleViolation):
        terms.get_effective_account_terms(
            None,
            scope=SCOPE,
            account_id="x",
            at=NOW,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "model",
    [
        "BillingAccountTermsVersion",
        "PlatformBillingAccountTermsVersion",
        "BillingTermsDefaultsVersion",
        "PlatformBillingTermsDefaultsVersion",
    ],
)
def test_uniqueness_is_by_effective_from_and_recorded_at_on_every_table(model):
    from dotmac_billing import models
    from sqlalchemy import UniqueConstraint

    table = getattr(models, model).__table__
    unique = [
        c
        for c in table.constraints
        if isinstance(c, UniqueConstraint) and c.name == f"uq_{table.name}_effective"
    ]
    assert len(unique) == 1
    assert {"effective_from", "recorded_at"} <= {c.name for c in unique[0].columns}


def test_exact_retry_is_content_equality_ignoring_attribution():
    from dotmac_billing import models

    def row(**changes):
        values = {"contract_type": None, "period": "daily"}
        values.update(changes)
        return models.PlatformBillingAccountTermsVersion(
            postpaid_billing_timing=None,
            postpaid_grace=None,
            actor_ref="a",
            reason="r",
            **values,
        )

    assert terms._same_terms(row(), row())
    assert not terms._same_terms(row(), row(period="monthly"))
    assert not terms._same_terms(row(), row(contract_type="prepaid"))


def test_append_refuses_an_identical_latest_before_touching_the_database():
    from dotmac_billing import models

    kwargs = {
        "contract_type": "prepaid",
        "period": "daily",
        "postpaid_billing_timing": None,
        "postpaid_grace": None,
        "actor_ref": "a",
        "reason": "r",
    }
    latest = models.PlatformBillingAccountTermsVersion(**kwargs)
    again = models.PlatformBillingAccountTermsVersion(**kwargs)
    with pytest.raises(terms.BillingConflict) as refused:
        terms._append(None, PlatformScope(), again, latest=latest)  # type: ignore[arg-type]
    assert refused.value.code == "billing.terms_version_exists"
