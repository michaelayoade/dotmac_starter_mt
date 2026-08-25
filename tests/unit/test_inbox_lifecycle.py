"""Conversation status transitions and the product-declared reason layer."""

from __future__ import annotations

import pytest
from dotmac_inbox.lifecycle import (
    InvalidTransitionError,
    ReasonSpec,
    Status,
    UnknownReasonError,
    is_open,
    register_reasons,
    registered_reasons,
    reset_registry_for_tests,
    validate_reason,
    validate_transition,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry_for_tests()
    yield
    reset_registry_for_tests()


# ── Transitions ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (Status.OPEN, Status.PENDING),
        (Status.OPEN, Status.RESOLVED),
        (Status.PENDING, Status.OPEN),
        (Status.SNOOZED, Status.OPEN),
        (Status.SNOOZED, Status.RESOLVED),
    ],
)
def test_legal_transitions_are_permitted(source: Status, target: Status) -> None:
    assert validate_transition(source, target) is target


def test_a_resolved_conversation_reopens_on_a_new_message() -> None:
    """Both source products do this, and modelling it as 'create a new
    conversation' loses the thread the customer can plainly see."""
    assert validate_transition(Status.RESOLVED, Status.OPEN) is Status.OPEN


@pytest.mark.parametrize("target", [Status.PENDING, Status.SNOOZED])
def test_a_resolved_conversation_cannot_move_sideways(target: Status) -> None:
    """RESOLVED → PENDING would mean 'closed, but waiting on the customer',
    which is a contradiction that makes every report ambiguous."""
    with pytest.raises(InvalidTransitionError):
        validate_transition(Status.RESOLVED, target)


def test_a_no_op_transition_is_idempotent_rather_than_an_error() -> None:
    """A caller re-asserting a status it already has is idempotent; raising
    would push every caller into a read-compare-write dance."""
    assert validate_transition(Status.OPEN, Status.OPEN) is Status.OPEN


def test_the_error_names_the_legal_targets() -> None:
    with pytest.raises(InvalidTransitionError) as excinfo:
        validate_transition(Status.RESOLVED, Status.SNOOZED)
    assert "open" in str(excinfo.value)


def test_transitions_accept_plain_strings_from_the_database() -> None:
    """Status is stored as text, so callers read strings back out."""
    assert validate_transition("open", "resolved") is Status.RESOLVED


# ── The is_open predicate ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (Status.OPEN, True),
        (Status.PENDING, True),
        # Deliberately True: a snoozed conversation is live, just not visible.
        # Callers wanting "needs attention now" must say so explicitly.
        (Status.SNOOZED, True),
        (Status.RESOLVED, False),
    ],
)
def test_is_open_counts_everything_but_resolved(status: Status, expected: bool) -> None:
    assert is_open(status) is expected


# ── The reason layer ─────────────────────────────────────────────────────────


def _to_ticket() -> ReasonSpec:
    """CRM models this as a fifth STATUS. It is a reason for `resolved`."""
    return ReasonSpec(
        code="resolved_to_ticket",
        owner="test_product",
        statuses=frozenset({Status.RESOLVED}),
        label="Handed off to a ticket",
    )


def test_a_declared_reason_is_valid_on_its_declared_status() -> None:
    register_reasons([_to_ticket()])
    assert validate_reason("resolved_to_ticket", status=Status.RESOLVED) == (
        "resolved_to_ticket"
    )


def test_a_reason_is_rejected_on_a_status_it_was_not_declared_for() -> None:
    """`resolved_to_ticket` on an OPEN conversation is nonsense that should fail
    at the write rather than confuse a report."""
    register_reasons([_to_ticket()])
    with pytest.raises(UnknownReasonError, match="not valid on status 'open'"):
        validate_reason("resolved_to_ticket", status=Status.OPEN)


def test_an_undeclared_reason_is_rejected_and_the_error_teaches() -> None:
    register_reasons([_to_ticket()])
    with pytest.raises(UnknownReasonError) as excinfo:
        validate_reason("spam", status=Status.RESOLVED)
    message = str(excinfo.value)
    assert "resolved_to_ticket" in message
    assert "register_reasons" in message
    # The tag escape hatch must be in the error, or every filterable term
    # becomes a registry row that rots.
    assert "tag" in message


def test_no_reason_is_the_common_case_and_passes() -> None:
    assert validate_reason(None, status=Status.OPEN) is None


def test_a_reason_declared_for_no_status_is_a_tag_and_is_refused() -> None:
    with pytest.raises(ValueError, match="use the conversation's tags"):
        ReasonSpec(code="interesting", owner="test_product")


def test_redeclaring_a_reason_with_a_different_scope_is_a_conflict() -> None:
    register_reasons([_to_ticket()])
    with pytest.raises(ValueError, match="already declared"):
        register_reasons(
            [
                ReasonSpec(
                    code="resolved_to_ticket",
                    owner="other_product",
                    statuses=frozenset({Status.OPEN}),
                )
            ]
        )


def test_registered_reasons_is_ordered_for_stable_output() -> None:
    register_reasons(
        [
            _to_ticket(),
            ReasonSpec(
                code="duplicate",
                owner="test_product",
                statuses=frozenset({Status.RESOLVED}),
            ),
        ]
    )
    assert [r.code for r in registered_reasons()] == ["duplicate", "resolved_to_ticket"]
