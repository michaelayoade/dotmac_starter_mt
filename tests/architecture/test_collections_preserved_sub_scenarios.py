"""Product-first preservation guard for the pinned Sub Collections behavior.

The scenario corpus is executable input for the future module conformance
suite.  This file can pass before the package exists: it proves that the source
cases are explicit, product-neutral, exact-money, aware-time fixtures and that
the deliberate extraction corrections remain named rather than silently
changing Sub behavior.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "collections_preserved_sub_scenarios.json"
)
SUB_REVISION = "d1a1a913e287ffadaf21b7da7be448f2c28b5483"
EXPECTED_SCENARIO_IDS = (
    "arrears_partial_position",
    "arrears_resolved_position",
    "advance_underfunded_position",
    "advance_future_coverage",
    "advance_incomplete_source",
    "case_step_delivery_and_terminal_replay",
    "case_close_and_fresh_reopen",
    "timer_reschedule_generation",
    "arrangement_rounding_exact_total",
    "arrangement_explicit_month_end_schedule",
    "arrangement_exact_exposure_membership",
    "settlement_observation_not_staff_payment",
    "grace_inclusive_last_day",
    "grace_zero_is_immediately_actionable",
    "action_stale_preview_is_refused",
    "action_receipt_is_product_owner_evidence",
    "notice_suppression_is_not_grace_or_action_authority",
)
EXPECTED_SOURCE_TESTS = {
    "tests/test_collections_target_lifecycle.py::test_postpaid_policy_counts_partial_settlement",
    "tests/test_collections_target_lifecycle.py::test_postpaid_policy_ignores_settled_and_not_yet_due",
    "tests/test_collections_target_lifecycle.py::test_prepaid_policy_proposes_only_when_underfunded",
    "tests/test_collections_target_lifecycle.py::test_prepaid_policy_waits_for_the_service_period",
    "tests/test_collections_target_lifecycle.py::test_prepaid_policy_fails_closed_for_cutover_quarantine",
    "tests/test_collections_target_lifecycle.py::test_the_case_ladder_escalates_one_step_per_proposal",
    "tests/test_collections_target_lifecycle.py::test_the_consequence_request_is_a_reason_scoped_owner_output",
    "tests/test_collections_target_lifecycle.py::test_close_cancels_timers_and_stages_restore_evidence",
    "tests/test_collections_target_lifecycle.py::test_closing_without_a_live_case_is_idempotent",
    "tests/test_collections_target_lifecycle.py::test_reopening_after_close_starts_a_fresh_case",
    "tests/test_collections_target_lifecycle.py::test_each_step_replaces_the_exact_next_action_timer",
    "tests/test_payment_arrangements.py::TestPaymentArrangements::test_create_arrangement_rounding",
    "tests/test_payment_arrangements.py::TestPaymentArrangementHelpers::test_calculate_end_date_monthly_clamps_short_month_and_recovers_anchor",
    "tests/test_payment_arrangements.py::TestPaymentArrangements::test_create_within_invoice_balance_succeeds",
    "tests/test_payment_arrangement_safe_actions.py::test_record_payment_targets_previewed_installment_without_ledger_claim",
    "tests/test_payment_arrangement_safe_actions.py::test_changed_state_rejects_stale_preview",
    "tests/test_grace_policy_sot.py::test_dunning_offsets_begin_after_grace_end",
    "tests/test_grace_policy_sot.py::test_configured_zero_grace_is_immediately_actionable",
    "tests/test_dunning_staff_safe_actions.py::test_changed_case_state_rejects_stale_preview",
    "tests/test_financial_access_consequence_evidence.py::test_suspend_confirmation_rejects_stale_preview",
    "tests/test_financial_access_consequence_evidence.py::test_suspend_confirmation_links_exact_lock_and_dunning_action",
    "tests/test_notification_queue_suppression.py::test_a_hard_bounce_stops_even_the_invoice",
}
FORBIDDEN_PRODUCT_KEYS = {
    "account_id",
    "billing_mode",
    "invoice_id",
    "payment_id",
    "prepaid",
    "postpaid",
    "subscriber_id",
    "subscription_id",
}
MONEY_RE = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{2}$")


class FixtureContractError(ValueError):
    """The checked-in product-first fixture no longer meets its contract."""


def _load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text())


def _fail(message: str) -> None:
    raise FixtureContractError(message)


def _validate_tree(value: object, *, path: str = "root") -> None:
    if isinstance(value, float):
        _fail(f"float:{path}")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_tree(item, path=f"{path}[{index}]")
        return
    if not isinstance(value, dict):
        return

    for key, item in value.items():
        item_path = f"{path}.{key}"
        if key in FORBIDDEN_PRODUCT_KEYS or "balance" in key:
            _fail(f"product-key:{item_path}")
        if key.endswith("_amount"):
            if not isinstance(item, str) or not MONEY_RE.fullmatch(item):
                _fail(f"exact-money:{item_path}")
            Decimal(item)
        if key == "currency" and (
            not isinstance(item, str) or not re.fullmatch(r"[A-Z]{3}", item)
        ):
            _fail(f"currency:{item_path}")
        if (key.endswith("_at") or key == "as_of") and item is not None:
            if not isinstance(item, str):
                _fail(f"instant:{item_path}")
            instant = datetime.fromisoformat(item.replace("Z", "+00:00"))
            if instant.tzinfo is None:
                _fail(f"naive-instant:{item_path}")
        _validate_tree(item, path=item_path)


def _validate_arrangement(scenario: dict[str, Any]) -> None:
    inputs = scenario["input"]
    exposures = inputs.get("exposures", inputs.get("protected_exposures"))
    if not isinstance(exposures, list) or not exposures:
        _fail(f"arrangement-membership:{scenario['id']}")
    identities = {
        (exposure["source_owner"], exposure["exposure_ref"]) for exposure in exposures
    }
    if len(identities) != len(exposures):
        _fail(f"arrangement-duplicate-exposure:{scenario['id']}")

    installments = inputs.get("installments")
    if installments is None:
        return
    ordinals = [installment["ordinal"] for installment in installments]
    if ordinals != list(range(1, len(installments) + 1)):
        _fail(f"arrangement-ordinals:{scenario['id']}")
    exposure_total = sum(
        (Decimal(exposure["admitted_amount"]) for exposure in exposures),
        Decimal("0.00"),
    )
    schedule_total = sum(
        (Decimal(installment["amount"]) for installment in installments),
        Decimal("0.00"),
    )
    expected_total = Decimal(scenario["expected"]["schedule_total_amount"])
    if exposure_total != schedule_total or schedule_total != expected_total:
        _fail(f"arrangement-total:{scenario['id']}")


def _validate_grace(scenario: dict[str, Any]) -> None:
    inputs = scenario["input"]
    if inputs.get("anchor_kind") not in {
        "exposure_at",
        "request_at",
        "accepted_notice_receipt_at",
    }:
        _fail(f"grace-anchor-kind:{scenario['id']}")
    if not inputs.get("anchor_at"):
        _fail(f"grace-anchor-at:{scenario['id']}")
    if inputs.get("duration_days", -1) < 0:
        _fail(f"grace-duration:{scenario['id']}")


def validate_fixture(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != 1:
        _fail("schema-version")
    if payload.get("source_repository") != "dotmac_sub":
        _fail("source-repository")
    if payload.get("source_revision") != SUB_REVISION:
        _fail("source-revision")

    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        _fail("scenarios")
    ids = tuple(scenario.get("id") for scenario in scenarios)
    if ids != EXPECTED_SCENARIO_IDS:
        _fail("scenario-ratchet")

    observed_sources: set[str] = set()
    for scenario in scenarios:
        preservation = scenario.get("preservation")
        if preservation not in {"preserved", "corrected_boundary"}:
            _fail(f"preservation:{scenario['id']}")
        correction = scenario.get("correction")
        if preservation == "corrected_boundary" and not correction:
            _fail(f"missing-correction:{scenario['id']}")
        if preservation == "preserved" and correction is not None:
            _fail(f"unexpected-correction:{scenario['id']}")
        source_tests = scenario.get("source_tests")
        if not isinstance(source_tests, list) or not source_tests:
            _fail(f"source-tests:{scenario['id']}")
        observed_sources.update(source_tests)
        _validate_tree(scenario, path=f"scenario:{scenario['id']}")
        if scenario["id"].startswith("arrangement_"):
            _validate_arrangement(scenario)
        if scenario["id"].startswith("grace_"):
            _validate_grace(scenario)

    if observed_sources != EXPECTED_SOURCE_TESTS:
        _fail("source-test-ratchet")

    incomplete = next(
        scenario
        for scenario in scenarios
        if scenario["id"] == "advance_incomplete_source"
    )
    if incomplete["expected"] != {
        "decision": "blocked",
        "case_advanced": False,
        "request_emitted": False,
        "retryable": True,
    }:
        _fail("unavailable-must-fail-closed")


def test_pinned_sub_scenarios_are_complete_exact_and_product_neutral() -> None:
    validate_fixture(_load_fixture())


def _mutate_source_revision(payload: dict[str, Any]) -> None:
    payload["source_revision"] = "moving-main"


def _mutate_money_to_float(payload: dict[str, Any]) -> None:
    payload["scenarios"][0]["input"]["position"]["collectible_receivable_amount"] = (
        15000.0
    )


def _mutate_product_key(payload: dict[str, Any]) -> None:
    payload["scenarios"][0]["input"]["account_id"] = "account:1"


def _mutate_grace_anchor(payload: dict[str, Any]) -> None:
    grace = next(
        scenario
        for scenario in payload["scenarios"]
        if scenario["id"] == "grace_inclusive_last_day"
    )
    grace["input"]["anchor_at"] = None


def _mutate_arrangement_total(payload: dict[str, Any]) -> None:
    arrangement = next(
        scenario
        for scenario in payload["scenarios"]
        if scenario["id"] == "arrangement_rounding_exact_total"
    )
    arrangement["input"]["installments"][2]["amount"] = "33.33"


@pytest.mark.parametrize(
    "mutation",
    [
        _mutate_source_revision,
        _mutate_money_to_float,
        _mutate_product_key,
        _mutate_grace_anchor,
        _mutate_arrangement_total,
    ],
)
def test_preservation_guard_sensitivity(
    mutation: Any,
) -> None:
    payload = copy.deepcopy(_load_fixture())
    mutation(payload)
    with pytest.raises(FixtureContractError):
        validate_fixture(payload)
