"""AccountingFactV6 (ledger-lane opening-variance facts): contract and V5 freeze.

V6 is a distinct family. V1-V5 classes and literals do not change; the V5 payload
carries the a11 manifest.
"""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime
from typing import get_type_hints
from uuid import UUID

import pytest
from dotmac_billing.opening_contracts import OpeningEvidenceError
from dotmac_billing.opening_receivable_contracts import AccountingFactV5
from dotmac_billing.opening_variance_contracts import (
    AccountingFactV6,
    OpeningVarianceFactBlockV1,
    OpeningVarianceResolutionFactV1,
)
from dotmac_kernel.cache import PlatformScope
from dotmac_kernel.fingerprints import fingerprint_of
from dotmac_kernel.money import Currency, Money

NOW = datetime(2026, 10, 13, tzinfo=UTC)
D = "d" * 64
NGN = Currency("NGN", 2)


def _u(n: int) -> UUID:
    return UUID(int=n)


def _money(minor: int) -> Money:
    from decimal import Decimal

    return Money(Decimal(minor) / 100, NGN)


def _block(**over) -> OpeningVarianceFactBlockV1:
    values = dict(  # noqa: C408
        variance_id=_u(1),
        window_id=_u(2),
        event_id=_u(3),
        event_seq=7,
        lane="receivable",
        basis="ledger_lane",
        reason_code="lane_document_difference",
        source_kind="lane",
        source_ref="account:1",
        amount=_money(-700),
        customer_adverse=True,
        import_receipt_id=_u(4),
        evidence_digest=D,
        rules_digest=D,
    )
    values.update(over)
    return OpeningVarianceFactBlockV1(**values)


def _resolution(**over) -> OpeningVarianceResolutionFactV1:
    values = dict(  # noqa: C408
        resolution_kind="position_corrected",
        resolved_state="resolved",
        gl_target_class=None,
        corrected_by_posting_group_id=_u(9),
        approval_request_id=_u(5),
        maker_ref="maker",
        approver_refs=("checker",),
    )
    values.update(over)
    return OpeningVarianceResolutionFactV1(**values)


def _fact(kind="opening_variance_recorded", **over) -> AccountingFactV6:
    event = over.pop("block", _block())
    values = dict(  # noqa: C408
        scope=PlatformScope(),
        source_system="dotmac-billing",
        fact_id=_u(10),
        fact_version=6,
        billing_account_id=_u(11),
        posting_group_id=_u(12),
        source_ref=f"opening-variance-event:{event.event_id}",
        source_authority="internal",
        effect_kind=kind,
        occurred_at=NOW,
        committed_at=NOW,
        variance=event,
        resolution=None,
        reverses_fact_id=None,
    )
    values.update(over)
    return AccountingFactV6(**values)


def test_v5_is_frozen() -> None:
    assert [f.name for f in fields(AccountingFactV5)] == [
        "scope",
        "source_system",
        "fact_id",
        "fact_version",
        "billing_account_id",
        "posting_group_id",
        "source_ref",
        "source_authority",
        "effect_kind",
        "occurred_at",
        "committed_at",
        "effects",
        "import_receipt",
        "receivable_bases",
        "opening_cash_effects",
        "corrects_posting_group_id",
    ]
    hints = get_type_hints(AccountingFactV5)
    assert str(hints["effect_kind"]) == (
        "typing.Literal['opening_account_imported', 'opening_account_import_reversed']"
    )
    shape = [[f.name, str(hints[f.name])] for f in fields(AccountingFactV5)]
    assert fingerprint_of({"shape": shape}) == (
        "98c542329303d8693caee08c94f7050aa98699f0ec371e06c2cf90734891fa02"
    )


def test_v6_is_a_distinct_family_with_its_own_closed_literals() -> None:
    assert str(get_type_hints(AccountingFactV6)["effect_kind"]) == (
        "typing.Literal['opening_variance_recorded', 'opening_variance_voided', "
        "'opening_variance_recognised', 'opening_variance_position_corrected']"
    )
    assert {f.name for f in fields(AccountingFactV6)}.isdisjoint(
        {"effects", "import_receipt", "receivable_bases"}
    )


def test_valid_facts_of_every_kind_construct() -> None:
    assert _fact().resolution is None
    assert _fact("opening_variance_voided", reverses_fact_id=_u(20)).reverses_fact_id
    _fact(
        "opening_variance_position_corrected",
        resolution=_resolution(),
    )
    ok = _block(amount=_money(300), customer_adverse=False)
    _fact(
        "opening_variance_recognised",
        block=ok,
        resolution=_resolution(
            resolution_kind="recognised",
            gl_target_class="bad_debt",
            corrected_by_posting_group_id=None,
        ),
    )


@pytest.mark.parametrize(
    "build",
    [
        # resolution is None exactly for recorded and voided
        lambda: _fact("opening_variance_recorded", resolution=_resolution()),
        lambda: _fact("opening_variance_position_corrected", resolution=None),
        # resolution kind must match the effect kind
        lambda: _fact(
            "opening_variance_position_corrected",
            resolution=_resolution(
                resolution_kind="recognised",
                gl_target_class="bad_debt",
                corrected_by_posting_group_id=None,
            ),
        ),
        # gl_target_class only for recognised; corrected_by only for corrected
        lambda: _resolution(gl_target_class="bad_debt"),
        lambda: _resolution(corrected_by_posting_group_id=None),
        lambda: _resolution(
            resolution_kind="recognised",
            gl_target_class="bad_debt",
        ),
        # per-lane GL class (Q6)
        lambda: _fact(
            "opening_variance_recognised",
            block=_block(amount=_money(300), customer_adverse=False),
            resolution=_resolution(
                resolution_kind="recognised",
                gl_target_class="customer_liability",
                corrected_by_posting_group_id=None,
            ),
        ),
        # reverses_fact_id only on voided, and voided needs it
        lambda: _fact("opening_variance_recorded", reverses_fact_id=_u(20)),
        lambda: _fact("opening_variance_voided", reverses_fact_id=None),
        # customer_adverse must follow the lane and sign
        lambda: _block(customer_adverse=False),
        lambda: _block(lane="customer_credit", customer_adverse=False),
        # zero amount, bad reason, source kind outside the reason policy
        lambda: _block(amount=_money(0)),
        lambda: _block(reason_code="made_up"),
        lambda: _block(source_kind="payment"),
        # a memo-only reason is not a ledger-lane reason
        lambda: _block(reason_code="stored_balance_difference", source_kind="invoice"),
        # maker among approvers, empty or duplicate approvers
        lambda: _resolution(approver_refs=("maker",)),
        lambda: _resolution(approver_refs=()),
        lambda: _resolution(approver_refs=("c", "c")),
        # source_ref binds the event; the system and authority are closed
        lambda: _fact(source_ref="opening-variance-event:other"),
        lambda: _fact(source_system="elsewhere"),
        lambda: _fact(source_authority="provider_owned"),
        lambda: _fact(fact_version=5),
        lambda: _fact("opening_account_imported"),
    ],
)
def test_every_invariant_refuses(build) -> None:
    with pytest.raises(OpeningEvidenceError):
        build()


def test_a_recognised_fact_is_never_customer_adverse() -> None:
    with pytest.raises(OpeningEvidenceError):
        _fact(
            "opening_variance_recognised",
            resolution=_resolution(
                resolution_kind="recognised",
                gl_target_class="bad_debt",
                corrected_by_posting_group_id=None,
            ),
        )  # the default block is the adverse -700 receivable variance


def test_the_frozen_replace_keeps_the_closed_checks() -> None:
    with pytest.raises(OpeningEvidenceError):
        replace(_fact(), effect_kind="invoice_issued")


def test_a_real_v5_fact_keeps_its_exact_bytes() -> None:
    """Pins V5 bytes from a11 onward: a V5 fact written by the importer is decoded
    and re-serialised to the identical payload and pinned digest. (V5 embeds the
    a11 V2 manifest, so this is a regression pin, not proof of a10 bytes.)"""
    import json
    from pathlib import Path

    from dotmac_billing.opening_queries import _decode_row
    from dotmac_billing.service import _jsonable

    path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "billing"
        / "accounting_fact_v5_pinned.json"
    )
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["digest"] == (
        "4f52df7b0392148349c9140d1e03ce605c0e38ff2dbdf7fa88eb771e451c4c44"
    )
    fact = _decode_row(
        {"fact_payload": stored["payload"], "fact_digest": stored["digest"]},
        AccountingFactV5,
        payload="fact_payload",
        digest="fact_digest",
    )
    payload = _jsonable(fact)
    assert json.loads(json.dumps(payload)) == stored["payload"]
    assert fingerprint_of(payload) == stored["digest"]
