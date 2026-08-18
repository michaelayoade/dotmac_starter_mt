"""Retention is a POLICY the deployment states, not a number a library holds.

Five guards, each with a sensitivity proof (ADR-0018, hard rule 25): a detector
that has never been shown to fire is a comment with a test's name on it.

* no default retention period and no default legal-policy owner exist ANYWHERE
  in the module — not as a dataclass default, not as a ready-made policy
  object. A default here would become the fleet's data-retention posture
  without anyone deciding it, and it would be invisible precisely because it
  worked;
* redaction may only ever write the three content columns. The guard reads the
  UPDATE statement itself, so widening it to `payload_digest` fails the build
  rather than the next redelivery;
* every receipt state the SCHEMA allows has a stated retention disposition, so
  a state added by a later slice cannot silently inherit "safe to redact";
* every refusal reason comes from the closed set;
* nothing persists a retention or health status. Health is counted from facts,
  for the same reason `operations.health_report` is.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from dotmac_integration import (
    InboxReceipt,
    RetentionPolicy,
    classify_receipt,
    module,
)
from dotmac_integration import retention as retention_module

# Guard vocabulary, imported from the SUBMODULE on purpose. `REDACTABLE_COLUMNS`
# and `REFUSAL_REASONS` describe how this module polices itself; publishing them
# on the top-level surface would make an internal guard a compatibility promise.
REDACTABLE_COLUMNS = retention_module.REDACTABLE_COLUMNS
REFUSAL_REASONS = retention_module.REFUSAL_REASONS

RETENTION_SOURCE = Path(retention_module.__file__)

#: A retention decision must be stated for every state the CHECK constraint
#: admits, plus one the module has never heard of.
UNKNOWN_STATE = "a_state_no_slice_has_written_yet"


# ── Detectors, written as pure functions so they can be proven to bite ──────


def _fields_without_a_default(cls: type) -> set[str]:
    """Dataclass fields a caller MUST supply."""
    return {
        f.name
        for f in dataclasses.fields(cls)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING  # type: ignore[misc]
    }


def _values_keywords(source: str) -> set[str]:
    """Every column named in a `.values(...)` call in this source.

    An AST walk rather than a regex: `values(` appears in comments and
    docstrings, and a guard that a comment can satisfy is not a guard.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "values":
            found.update(kw.arg for kw in node.keywords if kw.arg is not None)
    return found


def _persisted_status_columns(names: Iterable[str]) -> set[str]:
    """Column names that would make a derived verdict into a stored one.

    `state` on a receipt is a real lifecycle column and is not caught: what is
    forbidden is a SUMMARY someone can set — `health`, `retention_status`, a
    cached `*_health` — beside facts the ledger already holds.
    """
    forbidden = re.compile(r"(^|_)(health|healthy)(_|$)|_status$|^status$")
    return {name for name in names if forbidden.search(name)}


def _declared_receipt_states() -> set[str]:
    """The states the SCHEMA admits, read from the CHECK constraint itself.

    Read rather than restated: a second hand-written list is the thing that
    drifts, and drift here means a new state silently inheriting "safe to
    redact".
    """
    for constraint in InboxReceipt.__table__.constraints:
        if constraint.name == "ck_inbox_receipts_state":
            return set(re.findall(r"'([a-z_]+)'", str(constraint.sqltext)))
    raise AssertionError("inbox_receipts lost its state CHECK constraint")


# ── 1. No default period, no default owner, anywhere ────────────────────────


def test_the_policy_demands_both_decisions_from_its_caller() -> None:
    required = _fields_without_a_default(RetentionPolicy)
    assert {
        "payload_retention_days",
        "replay_evidence_retention_days",
        "legal_policy_owner",
    } <= required, (
        "a default retention period or legal-policy owner appeared on "
        "RetentionPolicy. A default here IS the deployment's data-retention "
        "policy, decided by whoever typed the number rather than by whoever is "
        "accountable for it"
    )


def test_the_default_detector_bites() -> None:
    """Sensitivity proof: the same predicate, run against the violation."""

    @dataclasses.dataclass(frozen=True)
    class _Smuggled:
        payload_retention_days: int = 90
        legal_policy_owner: str = "legal@example"

    assert _fields_without_a_default(_Smuggled) == set()


def test_the_module_offers_no_ready_made_policy_object() -> None:
    """`ExecutionPolicy` ships `DEFAULT_POLICY` because Sub's production numbers
    are a faithful port. Retention has no such source and must have no such
    constant — an importable policy is a default with an extra step."""
    ready_made = {
        name
        for name in dir(retention_module)
        if isinstance(getattr(retention_module, name), RetentionPolicy)
    }
    assert not ready_made, (
        f"{sorted(ready_made)} is an importable retention policy. Whoever "
        "imports it inherits a period nobody chose"
    )


def test_the_ready_made_detector_bites() -> None:
    class _Namespace:
        DEFAULT_RETENTION = RetentionPolicy(
            payload_retention_days=90,
            replay_evidence_retention_days=180,
            legal_policy_owner="legal@example",
        )

    caught = {
        name
        for name in dir(_Namespace)
        if isinstance(getattr(_Namespace, name), RetentionPolicy)
    }
    assert caught == {"DEFAULT_RETENTION"}


# ── 2. Redaction may only write the content columns ─────────────────────────


def test_redaction_writes_only_the_three_content_columns() -> None:
    written = _values_keywords(RETENTION_SOURCE.read_text(encoding="utf-8"))
    assert written == set(REDACTABLE_COLUMNS), (
        f"retention's UPDATE writes {sorted(written)}. Everything outside "
        f"{sorted(REDACTABLE_COLUMNS)} is deduplication identity, ordering or "
        "outcome evidence, and a provider's redelivery depends on all of it"
    )
    receipt_columns = {c.name for c in InboxReceipt.__table__.columns}
    assert set(REDACTABLE_COLUMNS) <= receipt_columns


def test_the_update_detector_bites() -> None:
    """Sensitivity proof: the widening this guard exists to catch."""
    violation = "sa.update(InboxReceipt).values(payload_json=None, payload_digest=None)"
    assert _values_keywords(violation) == {"payload_json", "payload_digest"}


# ── 3. Every schema state has a stated disposition ──────────────────────────


def test_every_state_the_schema_admits_has_a_retention_disposition() -> None:
    """A later slice adding a receipt state must decide what retention does with
    it. Failing here is this guard working: the fix is one line in
    `classify_receipt`, and the alternative is a new state quietly inheriting
    "safe to redact"."""
    policy = RetentionPolicy(
        payload_retention_days=30,
        replay_evidence_retention_days=180,
        legal_policy_owner="owner",
    )
    now = datetime.now(UTC)

    for state in _declared_receipt_states() | {UNKNOWN_STATE}:
        receipt = InboxReceipt(
            state=state,
            received_at=now - timedelta(days=400),
            payload_json={"a": 1},
            payload_digest="d" * 64,
        )
        reason = classify_receipt(receipt, policy=policy, now=now, held=False)
        assert reason is None or reason in REFUSAL_REASONS, (
            f"state {state!r} classified as {reason!r}, which is not in the "
            "closed refusal set. An ad-hoc reason cannot be counted or alerted"
        )
        if state != "processed":
            assert reason is not None, (
                f"state {state!r} is treated as safe to redact. Only a receipt "
                "that reached its outcome may age out"
            )


def test_the_declared_state_reader_bites() -> None:
    """Sensitivity proof for the constraint reader: it finds the real states,
    so a lost constraint or a renamed check fails loudly rather than returning
    an empty set that trivially satisfies the loop above."""
    states = _declared_receipt_states()
    assert "processed" in states and "reconciliation_required" in states
    assert len(states) >= 6


# ── 4. Refusal reasons are a closed set ─────────────────────────────────────


def test_the_refusal_vocabulary_is_closed_and_unique() -> None:
    assert len(REFUSAL_REASONS) == len(set(REFUSAL_REASONS))
    assert {"legal_hold", "leased", "unresolved", "reconciliation_required"} <= set(
        REFUSAL_REASONS
    ), "the four in-flight refusals are the point of the slice"


def test_every_retention_audit_action_is_declared_and_written() -> None:
    """ADR-0008 applied to this vocabulary: a declared action with no writer is
    dead vocabulary that reads as a working trail, and an undeclared action is
    refused by `write_audit_event` at the moment it matters."""
    source = RETENTION_SOURCE.read_text(encoding="utf-8")
    declared = {a for a in module.audit_actions if ".retention." in a}
    assert declared, "the manifest declares no retention audit action"
    for action in declared:
        written = action.removeprefix("integration.")
        assert f'action="{written}"' in source, (
            f"{action} is declared on the manifest but nothing in retention.py "
            "writes it"
        )


# ── 5. Nothing persists a status ────────────────────────────────────────────


def test_no_retention_table_persists_a_health_or_status_summary() -> None:
    """Health is counted from facts at read time, like
    `operations.health_report`. A stored summary is a second writer over facts
    the ledger already holds, and it drifts the instant a sweep dies half-way —
    which is exactly when someone reads it."""
    columns = [c.name for c in retention_module.ReceiptLegalHold.__table__.columns]
    offenders = _persisted_status_columns(columns)
    assert not offenders, (
        f"{sorted(offenders)} would make a derived verdict settable. "
        "`retention_backlog` counts rows instead, and it cannot lie"
    )


@pytest.mark.parametrize(
    "name", ["health", "retention_status", "queue_health", "status", "sweep_status"]
)
def test_the_status_column_detector_bites(name: str) -> None:
    assert _persisted_status_columns([name]) == {name}


@pytest.mark.parametrize("name", ["state", "released_at", "placed_by", "policy_owner"])
def test_the_status_column_detector_does_not_over_reach(name: str) -> None:
    """`state` is a real lifecycle column, not a cached verdict. A detector that
    banned it would be unusable and would be turned off."""
    assert _persisted_status_columns([name]) == set()
