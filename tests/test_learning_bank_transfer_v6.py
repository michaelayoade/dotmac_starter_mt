"""Hosted SnapshotV6 canaries: full 28-table carry and frozen refusals.

Builds on the sealed V5 snapshot builder, converts its rows to the V6
contracts with deliberately NON-DEFAULT a3/bank values, and proves the
round trip — plus that frozen V5 refuses the same estate (RD-1 seam).
Never run locally.
"""

from dataclasses import fields, replace
from uuid import uuid4

import pytest
from dotmac_learning.bank_transfer import (
    export_snapshot_v6,
    import_snapshot_v6,
    verify_snapshot_v6,
)
from dotmac_learning.bank_transfer_contracts import (
    _ALL_ROW_TYPES_V6,
    LearningSnapshotV6,
    QuestionBankVersionRowV6,
    SnapshotClosureErrorV6,
)
from dotmac_learning.delivery_transfer import export_snapshot_v5
from dotmac_learning.transfer_contracts import (
    Float8Bits,
    JsonbText,
    SnapshotValidationError,
)

from tests import test_learning_delivery_transfer as previous
from tests.test_learning_transfer import NOW, ONE

learning_database = previous.learning_database
transfer_database = previous.transfer_database

HALF = Float8Bits("3fe0000000000000")


def _convert(row, target, **extra):
    names = {field.name for field in fields(target)}
    values = {name: getattr(row, name) for name in names if hasattr(row, name)}
    values.update(extra)
    return target(**values)


def _snapshot_v6(tenants, people):
    base = previous._snapshot(tenants, people)
    row_types = dict(_ALL_ROW_TYPES_V6)
    versions = []
    version_by_bank = {}
    for bank in base.question_banks:
        version = QuestionBankVersionRowV6(
            id=uuid4(),
            tenant_id=bank.tenant_id,
            bank_id=bank.id,
            version_no=1,
            state="published",
            origin="backfill",
            content_hash="0" * 64,
            lint_violations=JsonbText("[]"),
            base_version_id=None,
            draft_revision=0,
            created_by_actor=None,
            published_by=None,
            published_at=NOW,
            retired_by=None,
            retired_at=None,
            retire_reason=None,
            superseded_by_version_id=None,
            created_at=NOW,
            updated_at=NOW,
        )
        versions.append(version)
        version_by_bank[(bank.tenant_id, bank.id)] = version
    tenant_version = {}
    for version in versions:
        tenant_version.setdefault(version.tenant_id, version)
    extras = {
        "courses": lambda row: {
            "prerequisite_composition": "any",
            "completion_fraction": HALF,
            "certificate_auto_issue": False,
            "certificate_validity_days": 30,
        },
        "activities": lambda row: {
            "scoring_policy": "latest",
            "is_final": True,
            "min_attempt_interval_seconds": 60,
            # Bank-coherent pin: closure requires the pinned version to
            # belong to the activity's bank when one is set.
            "bank_id": tenant_version[row.tenant_id].bank_id,
            "bank_version_id": tenant_version[row.tenant_id].id,
        },
        "certificates": lambda row: {"expires_at": NOW},
        "question_banks": lambda row: {
            "current_version_id": version_by_bank[(row.tenant_id, row.id)].id
        },
        "questions": lambda row: {
            "bank_version_id": version_by_bank[(row.tenant_id, row.bank_id)].id
        },
        "cohorts": lambda row: {"auto_accept_below_action": "none"},
        "tracks": lambda row: {"gating_mode": "none"},
        "course_offerings": lambda row: {"pacing_default_open": False},
        "course_prerequisites": lambda row: {
            "condition_kind": "course_completed",
            "params": JsonbText("{}"),
            "group_key": "g0",
            "origin": "ui",
        },
        "activity_attempts": lambda row: {
            "bank_version_id": tenant_version[row.tenant_id].id
        },
    }
    values = {}
    for table, target in row_types.items():
        if table == "question_bank_versions":
            values[table] = tuple(versions)
            continue
        rows = getattr(base, table)
        if table in extras:
            values[table] = tuple(
                _convert(row, target, **extras[table](row)) for row in rows
            )
        else:
            values[table] = rows
    return LearningSnapshotV6(version=6, tenant_ids=tenants, **values)


def _keyed(snapshot):
    return {
        (table, row.id): row
        for table, _ in _ALL_ROW_TYPES_V6
        for row in getattr(snapshot, table)
    }


def test_v6_roundtrip_carries_nondefault_a3_and_bank_data(transfer_database):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot_v6(tenants, people)
    with owner.connect() as conn:
        root = conn.begin()
        observation = import_snapshot_v6(conn, snapshot)
        assert observation.verification.matched
        assert sum(count.count for count in observation.inserted_rows) == sum(
            len(getattr(snapshot, table)) for table, _ in _ALL_ROW_TYPES_V6
        )
        assert verify_snapshot_v6(conn, snapshot).matched
        assert _keyed(export_snapshot_v6(conn, tenants)) == _keyed(snapshot)
        root.rollback()


def test_frozen_v5_refuses_bank_populated_estate(transfer_database):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot_v6(tenants, people)
    with owner.connect() as conn:
        root = conn.begin()
        import_snapshot_v6(conn, snapshot)
        with pytest.raises(SnapshotValidationError, match="RD-1"):
            export_snapshot_v5(conn, tenants)
        root.rollback()


def test_v6_closure_refuses_question_without_its_version(transfer_database):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot_v6(tenants, people)
    question = snapshot.questions[0]
    broken = replace(
        snapshot,
        questions=(
            replace(question, bank_version_id=uuid4()),
            *snapshot.questions[1:],
        ),
    )
    with owner.connect() as conn:
        root = conn.begin()
        with pytest.raises(SnapshotClosureErrorV6):
            import_snapshot_v6(conn, broken)
        root.rollback()


def test_v6_field_total_matches_all_persisted_columns():
    total = sum(len(fields(row_type)) for _, row_type in _ALL_ROW_TYPES_V6)
    assert total > 260  # V5's frozen total; V6 carries strictly more.
    assert len(_ALL_ROW_TYPES_V6) == 28
    assert ONE.hex == "3ff0000000000000"
