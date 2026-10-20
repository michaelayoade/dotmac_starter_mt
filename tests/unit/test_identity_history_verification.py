"""Bounded data contract canaries; hosted public-owner positives live separately."""

from dataclasses import replace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from dotmac_kernel.migrations import identity_history as history
from sqlalchemy.dialects import postgresql

from tests.unit.test_identity_history import history_case

assert history_case


def _receipt(rows, expected, operation):
    return history.IdentityHistoryImportReceiptV1(
        counts=history.IdentityHistoryCountsV1(
            **{name: len(getattr(rows, name)) for name in history._FAMILIES}
        ),
        decision_ref=operation.decision_ref,
        source_revision=expected.source_revision,
        current_audit_ids=tuple(uuid4() for _ in expected.cohort.tenants),
    )


def test_receipt_counts_references_and_current_keys(history_case):
    _, rows, expected, operation = history_case
    receipt = _receipt(rows, expected, operation)
    tenants, keys = history._verification_receipt_preflight(
        rows, expected, operation, receipt
    )
    assert tenants == sorted(expected.cohort.tenants, key=str)
    assert keys is receipt.current_audit_ids
    assert "verify_identity_history" in history.__all__
    for malformed in (
        replace(receipt, counts=replace(receipt.counts, tenants=True)),
        replace(receipt, counts=replace(receipt.counts, people=999)),
        replace(receipt, decision_ref="unrelated"),
        replace(receipt, source_revision="b" * 40),
        replace(receipt, current_audit_ids=()),
        replace(receipt, current_audit_ids=(str(keys[0]),)),
        replace(receipt, current_audit_ids=(rows.audit_events[0].id,)),
    ):
        with pytest.raises(history.IdentityHistoryImportRefused):
            history._verification_receipt_preflight(
                rows, expected, operation, malformed
            )


def test_duplicate_current_keys_refuse_without_historical_overlap(history_case):
    _, rows, expected, operation = history_case
    expected = replace(
        expected, cohort=replace(expected.cohort, tenants=frozenset((uuid4(), uuid4())))
    )
    receipt = _receipt(rows, expected, operation)
    with pytest.raises(history.IdentityHistoryImportRefused):
        history._verification_receipt_preflight(
            rows,
            expected,
            operation,
            replace(receipt, current_audit_ids=(receipt.current_audit_ids[0],) * 2),
        )


def test_jsonb_predicates_are_typed_bound_and_distinguish_sql_null(history_case):
    _, rows, _, _ = history_case
    table, original = next(
        (table, values)
        for table, values in history._projections(rows)
        if table.name == "audit_events"
    )
    sentinel = (
        '{"sentinel": 900719925474099312345678901234567890, "decimal": 0.1234'
        "5678901234567890}"
    )
    values = {**original, "details": history.HistoricalJsonbV1(sentinel)}
    statement = (
        sa.select(sa.func.count())
        .select_from(table)
        .where(*history._comparison_predicates(table, values))
    )
    compiled = statement.compile(dialect=postgresql.dialect())
    assert sentinel not in str(compiled)
    assert sentinel in compiled.params.values()
    assert "IS NOT DISTINCT FROM" in str(compiled)
    assert "JSONB" in str(compiled) and "TEXT" in str(compiled)
    assert original["actor_type"] is None
    assert not any(isinstance(value, float) for value in compiled.params.values())
    assert sentinel not in repr(values["details"])


def test_current_audit_times_use_root_database_expression(history_case):
    _, rows, _, _ = history_case
    table, values = next(
        (table, values)
        for table, values in history._projections(rows)
        if table.name == "audit_events"
    )
    values.update(
        created_at=sa.func.transaction_timestamp(),
        occurred_at=sa.func.transaction_timestamp(),
    )
    sql = str(
        sa.select(sa.func.count())
        .select_from(table)
        .where(*history._comparison_predicates(table, values))
    )
    assert sql.count("transaction_timestamp()") == 2
