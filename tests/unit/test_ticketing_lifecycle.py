"""Transition guarding and reason validation — the module's actual behaviour.

The structural contract (closed vocabulary, ledger allocation, RLS in the
migration) is pinned in `tests/architecture/test_ticketing_module.py`. This file
covers what the code *does*.
"""

from __future__ import annotations

import pytest
from dotmac_ticketing import (
    CORE_REASONS,
    LifecycleClass,
    ReasonSpec,
    Status,
    TransitionError,
    UnknownReasonError,
    check_transition,
    register_reasons,
    registered_reasons,
    validate_reason,
)
from dotmac_ticketing.vocabulary import reset_registry_for_tests


@pytest.fixture(autouse=True)
def _isolated_registry():
    """The reason registry is process-global by design; isolate every test."""
    reset_registry_for_tests()
    register_reasons(CORE_REASONS)
    yield
    reset_registry_for_tests()


# ── Transitions ──────────────────────────────────────────────────────────────


def test_a_normal_progression_is_permitted() -> None:
    check_transition(Status.NEW, Status.OPEN)
    check_transition(Status.OPEN, Status.RESOLVED)
    check_transition(Status.RESOLVED, Status.CLOSED)


def test_reasserting_the_current_status_is_a_no_op_not_an_error() -> None:
    """A retried request or an idempotent import re-asserts what it already set.

    Rejecting it would push every caller into a read-then-compare it can lose a
    race on, which is a worse failure than the one it prevents.
    """
    for status in Status:
        check_transition(status, status)


def test_a_merged_ticket_cannot_be_reopened() -> None:
    """Its life continues on the target; reopening makes two live tickets."""
    with pytest.raises(TransitionError, match="merged"):
        check_transition(Status.MERGED, Status.OPEN)


def test_leaving_a_terminal_status_is_enumerable_not_general() -> None:
    check_transition(Status.CLOSED, Status.OPEN)  # a reopen
    check_transition(Status.CLOSED, Status.MERGED)
    with pytest.raises(TransitionError):
        check_transition(Status.CLOSED, Status.PENDING)


def test_the_error_names_the_permitted_targets() -> None:
    """A guard that only says 'no' makes the caller guess."""
    with pytest.raises(TransitionError) as excinfo:
        check_transition(Status.MERGED, Status.OPEN)
    assert "nothing" in str(excinfo.value)

    with pytest.raises(TransitionError) as excinfo:
        check_transition(Status.CANCELLED, Status.CLOSED)
    assert "open" in str(excinfo.value)


def test_every_status_is_reachable_from_new_or_is_deliberately_not() -> None:
    """No status may be stranded — an unreachable one is dead vocabulary."""
    reachable = {Status.NEW}
    frontier = [Status.NEW]
    while frontier:
        current = frontier.pop()
        for target in Status:
            if target in reachable:
                continue
            try:
                check_transition(current, target)
            except TransitionError:
                continue
            reachable.add(target)
            frontier.append(target)
    assert reachable == set(Status)


# ── Reasons ──────────────────────────────────────────────────────────────────


def test_a_reason_must_be_declared_before_it_can_be_used() -> None:
    with pytest.raises(UnknownReasonError, match="not declared"):
        validate_reason("lastmile_rerun", Status.PENDING)


def test_a_declared_reason_validates_and_canonicalises() -> None:
    register_reasons(
        [
            ReasonSpec(
                code="lastmile_rerun",
                description="Awaiting a fiber last-mile re-run.",
                owner="dotmac_sub",
                statuses=frozenset({Status.PENDING, Status.ON_HOLD}),
            )
        ]
    )
    assert validate_reason("  LASTMILE_RERUN ", Status.PENDING) == "lastmile_rerun"


def test_a_reason_is_rejected_for_a_status_it_was_not_declared_for() -> None:
    """A nonsense pair is caught at the write, not discovered in a report."""
    register_reasons(
        [
            ReasonSpec(
                code="awaiting_parts",
                description="Blocked on stock.",
                owner="dotmac_erp",
                statuses=frozenset({Status.ON_HOLD}),
            )
        ]
    )
    with pytest.raises(UnknownReasonError, match="cannot be attached"):
        validate_reason("awaiting_parts", Status.CLOSED)


def test_no_reason_is_always_valid() -> None:
    """Most tickets have none; requiring one invents filler codes."""
    assert validate_reason(None, Status.OPEN) is None
    assert validate_reason("   ", Status.OPEN) is None


def test_two_products_cannot_declare_the_same_code_differently() -> None:
    """The vocabulary fork this registry exists to prevent."""
    spec = ReasonSpec(
        code="escalated",
        description="Escalated to tier 2.",
        owner="dotmac_sub",
        statuses=frozenset({Status.OPEN}),
    )
    register_reasons([spec])
    register_reasons([spec])  # identical re-registration is a no-op

    with pytest.raises(ValueError, match="already declared"):
        register_reasons(
            [
                ReasonSpec(
                    code="escalated",
                    description="Escalated to the vendor.",
                    owner="dotmac_erp",
                    statuses=frozenset({Status.ON_HOLD}),
                )
            ]
        )


def test_a_reason_valid_for_no_status_is_rejected_as_a_tag() -> None:
    with pytest.raises(ValueError, match="tag, not a reason"):
        ReasonSpec(
            code="interesting",
            description="Someone found this interesting.",
            owner="dotmac_sub",
            statuses=frozenset(),
        )


def test_a_reason_code_must_be_canonical() -> None:
    """Two spellings of one reason split every report that filters on it."""
    with pytest.raises(ValueError, match="lowercase"):
        ReasonSpec(
            code="Lastmile_Rerun",
            description="…",
            owner="dotmac_sub",
            statuses=frozenset({Status.PENDING}),
        )


def test_a_reason_needs_an_owning_module() -> None:
    with pytest.raises(ValueError, match="owning module"):
        ReasonSpec(
            code="orphan",
            description="…",
            owner="  ",
            statuses=frozenset({Status.OPEN}),
        )


def test_core_reasons_cover_the_statuses_that_are_useless_without_one() -> None:
    """`pending` and `cancelled` lose the only fact anyone later wants."""
    registered = {spec.code: spec for spec in registered_reasons()}
    assert "awaiting_third_party" in registered
    assert Status.PENDING in registered["awaiting_third_party"].statuses
    assert "withdrawn_by_requester" in registered
    assert Status.CANCELLED in registered["withdrawn_by_requester"].statuses


# ── The class layer is what consumers key off ────────────────────────────────


def test_a_product_reason_needs_no_change_to_any_class_consumer() -> None:
    """The property that makes the module shareable, asserted directly.

    A product declares a reason; `is_open`, `sla_clock_runs` and every other
    class-keyed consumer keep working with no knowledge of it, because the
    reason rides on a status that already has a class.
    """
    register_reasons(
        [
            ReasonSpec(
                code="site_under_construction",
                description="The site is still being built.",
                owner="dotmac_sub",
                statuses=frozenset({Status.ON_HOLD}),
            )
        ]
    )
    status = Status.ON_HOLD
    reason = validate_reason("site_under_construction", status)
    assert reason == "site_under_construction"
    assert status.lifecycle_class is LifecycleClass.WAITING
