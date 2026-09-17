"""``map_item`` — translation, disposition/issues consistency, fingerprint."""

from __future__ import annotations

import copy

import pytest
from dotmac_connector_sub_accounting.mapping import (
    CAPABILITY_ID,
    CONTRACT_VERSION,
    SubAccountingMappingError,
    map_item,
)

INVOICE_ID = "11111111-1111-1111-1111-111111111111"
ACCOUNT_ID = "22222222-2222-2222-2222-222222222222"


def _item(**overrides: object) -> dict[str, object]:
    # Field names match Sub's real `InvoiceAccountingSyncRead`
    # (dotmac_sub/app/schemas/billing.py:334), verified by direct cross-repo
    # read — not the `id`/`status`/`total_amount` this fixture used before,
    # which matched the (buggy) mapper rather than Sub's actual wire shape.
    base: dict[str, object] = {
        "source_invoice_id": INVOICE_ID,
        "account_id": ACCOUNT_ID,
        "contract_version": CONTRACT_VERSION,
        "source_kind": "native",
        "updated_at": "2026-09-01T00:00:00+00:00",
        "disposition": "ready",
        "total": "100.00",
        "currency": "ngn",
        "issues": [],
    }
    base.update(overrides)
    return base


def _blocked_issue(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "code": "amount_mismatch",
        "line_id": "line-1",
        "expected_amount": "50.00",
        "actual_amount": "40.00",
    }
    base.update(overrides)
    return base


# -- happy path ---------------------------------------------------------------


def test_maps_a_ready_item_with_no_issues() -> None:
    event, updated_at, invoice_id = map_item(_item())

    assert invoice_id == INVOICE_ID
    assert updated_at.isoformat() == "2026-09-01T00:00:00+00:00"
    assert event.event_type == CAPABILITY_ID
    assert event.payload["capability_id"] == CAPABILITY_ID
    assert event.payload["source_invoice_id"] == INVOICE_ID
    assert event.payload["source_account_id"] == ACCOUNT_ID
    assert event.payload["source_updated_at"] == "2026-09-01T00:00:00+00:00"
    assert event.payload["source_kind"] == "native"
    assert event.payload["disposition"] == "ready"
    assert event.payload["source_total_amount"] == "100.00"
    assert event.payload["source_currency"] == "NGN"
    assert event.payload["issues"] == []
    assert "organization_id" not in event.payload
    assert "idempotency_key" not in event.payload
    assert "source_issued_at" not in event.payload
    assert "source_due_at" not in event.payload
    assert isinstance(event.payload["projection_fingerprint"], str)
    assert len(event.payload["projection_fingerprint"]) == 64


def test_maps_a_blocked_item_and_renames_line_id_to_source_line_id() -> None:
    event, _, _ = map_item(_item(disposition="blocked", issues=[_blocked_issue()]))

    assert event.payload["issues"] == [
        {
            "code": "amount_mismatch",
            "source_line_id": "line-1",
            "expected_amount": "50.00",
            "actual_amount": "40.00",
        }
    ]
    assert "line_id" not in event.payload["issues"][0]


def test_optional_dates_pass_through_when_present() -> None:
    event, _, _ = map_item(
        _item(issued_at="2026-08-01T00:00:00Z", due_at="2026-09-15T00:00:00Z")
    )
    assert event.payload["source_issued_at"] == "2026-08-01T00:00:00Z"
    assert event.payload["source_due_at"] == "2026-09-15T00:00:00Z"


# -- disposition/issues consistency -------------------------------------------


def test_rejects_blocked_disposition_with_no_issues() -> None:
    with pytest.raises(SubAccountingMappingError, match="no issues"):
        map_item(_item(disposition="blocked", issues=[]))


def test_rejects_non_blocked_disposition_with_issues() -> None:
    with pytest.raises(SubAccountingMappingError, match="carries issues"):
        map_item(_item(disposition="ready", issues=[_blocked_issue()]))


# -- contract version and identity --------------------------------------------


def test_rejects_unsupported_contract_version() -> None:
    with pytest.raises(SubAccountingMappingError, match="contract_version"):
        map_item(_item(contract_version="invoice-accounting-sync.v1"))


def test_rejects_invalid_invoice_id() -> None:
    with pytest.raises(SubAccountingMappingError, match="id is not a valid UUID"):
        map_item(_item(source_invoice_id="not-a-uuid"))


def test_rejects_invalid_account_id() -> None:
    with pytest.raises(SubAccountingMappingError, match="account_id"):
        map_item(_item(account_id="not-a-uuid"))


def test_preserves_the_exact_uuid_string_sub_sent() -> None:
    # Validated, never reformatted -- required so the next poll's cursor can
    # echo the exact value back to Sub.
    mixed_case = "11111111-AAAA-1111-1111-111111111111"
    event, _, invoice_id = map_item(_item(source_invoice_id=mixed_case))
    assert event.payload["source_invoice_id"] == mixed_case
    assert invoice_id == mixed_case


# -- timestamps -----------------------------------------------------------


def test_rejects_a_naive_updated_at() -> None:
    with pytest.raises(SubAccountingMappingError, match="timezone"):
        map_item(_item(updated_at="2026-09-01T00:00:00"))


def test_rejects_a_malformed_updated_at() -> None:
    with pytest.raises(SubAccountingMappingError, match="updated_at"):
        map_item(_item(updated_at="not-a-timestamp"))


def test_rejects_a_naive_optional_date() -> None:
    with pytest.raises(SubAccountingMappingError, match="issued_at"):
        map_item(_item(issued_at="2026-08-01T00:00:00"))


# -- money and issues shape ----------------------------------------------


def test_rejects_missing_header_money_fields() -> None:
    with pytest.raises(SubAccountingMappingError, match="money"):
        map_item(_item(total=None))


def test_rejects_an_invalid_currency() -> None:
    with pytest.raises(SubAccountingMappingError, match="money"):
        map_item(_item(currency="not-a-currency"))


def test_rejects_an_issue_entry_missing_a_field() -> None:
    with pytest.raises(SubAccountingMappingError, match="issue entry"):
        map_item(
            _item(
                disposition="blocked",
                issues=[{"code": "x", "line_id": "line-1"}],
            )
        )


def test_rejects_issues_that_is_not_a_list() -> None:
    with pytest.raises(SubAccountingMappingError, match="issues is not a list"):
        map_item(_item(issues={"code": "x"}))


# -- fingerprint stability and sensitivity ------------------------------------


def test_fingerprint_is_stable_across_repeated_computation() -> None:
    first, _, _ = map_item(_item())
    second, _, _ = map_item(_item())
    assert (
        first.payload["projection_fingerprint"]
        == second.payload["projection_fingerprint"]
    )


def test_fingerprint_is_stable_regardless_of_raw_dict_key_order() -> None:
    raw = _item()
    reordered = dict(reversed(list(raw.items())))
    first, _, _ = map_item(raw)
    second, _, _ = map_item(reordered)
    assert (
        first.payload["projection_fingerprint"]
        == second.payload["projection_fingerprint"]
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: item.update(source_kind="splynx_legacy"),
        lambda item: item.update(disposition="blocked", issues=[_blocked_issue()]),
        lambda item: item.update(total="100.01"),
        lambda item: item.update(currency="usd"),
        lambda item: item.update(updated_at="2026-09-01T00:00:01+00:00"),
        lambda item: item.update(account_id="33333333-3333-3333-3333-333333333333"),
    ],
)
def test_fingerprint_changes_when_an_admitted_fact_changes(mutate) -> None:
    baseline = _item()
    mutated = copy.deepcopy(baseline)
    mutate(mutated)

    baseline_event, _, _ = map_item(baseline)
    mutated_event, _, _ = map_item(mutated)

    assert (
        baseline_event.payload["projection_fingerprint"]
        != mutated_event.payload["projection_fingerprint"]
    )
