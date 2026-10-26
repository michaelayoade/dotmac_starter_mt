"""Unit canaries for the lr_0010 bank-version layer and SnapshotV6."""

from dataclasses import MISSING, fields, replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from dotmac_learning import bank_transfer, transfer
from dotmac_learning.attempt_models import ATTEMPT_MODELS
from dotmac_learning.bank_lifecycle import canonical_content_hash
from dotmac_learning.bank_lifecycle_contracts import (
    BankAuditContext,
    SittingQuestion,
)
from dotmac_learning.bank_transfer_contracts import (
    _ALL_ROW_TYPES_V6,
    ActivityAttemptRowV6,
    ActivityRowV6,
    LearningSnapshotV6,
    QuestionBankRowV6,
    QuestionBankVersionRowV6,
    SnapshotClosureErrorV6,
)
from dotmac_learning.curriculum_models import CURRICULUM_MODELS
from dotmac_learning.delivery_models import DELIVERY_MODELS
from dotmac_learning.models import TENANT_MODELS
from dotmac_learning.participation_models import (
    PARTICIPATION_MODELS,
    CoursePrerequisite,
)
from dotmac_learning.transfer_contracts import (
    Float8Bits,
    JsonbText,
    SnapshotValidationError,
)

ALL_MODELS = (
    *TENANT_MODELS,
    *CURRICULUM_MODELS,
    *PARTICIPATION_MODELS,
    *ATTEMPT_MODELS,
    *DELIVERY_MODELS,
)


def _question(ext_id="q1", **overrides):
    base = {
        "ext_id": ext_id,
        "stem": "Stem?",
        "type": "single",
        "options": ["aaaa", "bbbb"],
        "correct": ["aaaa"],
        "rubric_category": "recall",
        "category": None,
        "explanation": "",
        "weight": 1,
    }
    return {**base, **overrides}


def test_content_hash_is_order_independent_and_content_sensitive():
    a, b = _question("a"), _question("b")
    assert canonical_content_hash([a, b]) == canonical_content_hash([b, a])
    assert canonical_content_hash([a]) != canonical_content_hash(
        [_question("a", correct=["bbbb"])]
    )
    assert canonical_content_hash([a]) != canonical_content_hash(
        [_question("a", weight=2)]
    )
    assert canonical_content_hash([]) == canonical_content_hash([])
    assert len(canonical_content_hash([a])) == 64


def test_bank_audit_context_rejects_unknown_actor_types():
    BankAuditContext(tenant_id=uuid4(), actor_type="user", source_application="tests")
    with pytest.raises(ValueError):
        BankAuditContext(
            tenant_id=uuid4(), actor_type="admin", source_application="tests"
        )


def test_sitting_projection_has_no_answer_key_fields():
    names = {field.name for field in fields(SittingQuestion)}
    assert names == {"ext_id", "stem", "type", "options"}
    assert "correct" not in names and "explanation" not in names


def test_v6_carries_every_persisted_column_of_all_28_tables():
    assert len(ALL_MODELS) == 28
    models = {model.__tablename__: model for model in ALL_MODELS}
    assert {table for table, _ in _ALL_ROW_TYPES_V6} == set(models)
    total = 0
    for table, row_type in _ALL_ROW_TYPES_V6:
        row_fields = fields(row_type)
        assert {field.name for field in row_fields} == set(
            models[table].__table__.c.keys()
        ), table
        assert all(
            field.default is MISSING and field.default_factory is MISSING
            for field in row_fields
        )
        total += len(row_fields)
    assert total == sum(len(model.__table__.c) for model in ALL_MODELS)
    assert len(fields(LearningSnapshotV6)) == 30


def test_v6_snapshot_rejects_wrong_version_and_row_types():
    tenant = uuid4()
    empty = LearningSnapshotV6(
        version=6,
        tenant_ids=(tenant,),
        **{table: () for table, _ in _ALL_ROW_TYPES_V6},
    )
    assert empty.version == 6
    with pytest.raises(SnapshotValidationError):
        LearningSnapshotV6(
            version=5,
            tenant_ids=(tenant,),
            **{table: () for table, _ in _ALL_ROW_TYPES_V6},
        )
    with pytest.raises(SnapshotValidationError):
        LearningSnapshotV6(
            version=6,
            tenant_ids=(tenant,),
            **{
                table: (() if table != "courses" else ("not-a-row",))
                for table, _ in _ALL_ROW_TYPES_V6
            },
        )


def test_bank_version_row_is_repr_safe():
    from datetime import UTC, datetime

    from dotmac_learning.transfer_contracts import JsonbText

    now = datetime(2020, 1, 1, tzinfo=UTC)
    row = QuestionBankVersionRowV6(
        id=uuid4(),
        tenant_id=uuid4(),
        bank_id=uuid4(),
        version_no=1,
        state="published",
        origin="backfill",
        content_hash="0" * 64,
        lint_violations=JsonbText("[]"),
        base_version_id=None,
        draft_revision=0,
        created_by_actor=None,
        published_by=None,
        published_at=None,
        retired_by=None,
        retired_at=None,
        retire_reason=None,
        superseded_by_version_id=None,
        created_at=now,
        updated_at=now,
    )
    assert repr(row) == f"QuestionBankVersionRowV6(id={row.id})"


def test_bank_column_group_admits_only_whole_lr_0010_set_in_chain():
    expected = {"id": ("uuid", False), "tenant_id": ("uuid", False)}
    s2_activities = {
        "scoring_policy": ("character varying(10)", False),
        "is_final": ("boolean", False),
        "min_attempt_interval_seconds": ("integer", False),
    }
    # The bank pin alone (without the lr_0009 set) is out of chain.
    assert not transfer._column_contract_matches(
        "activities", {**expected, "bank_version_id": ("uuid", True)}, expected
    )
    assert transfer._column_contract_matches(
        "activities",
        {**expected, **s2_activities, "bank_version_id": ("uuid", True)},
        expected,
    )
    # Wrong nullability refuses.
    assert not transfer._column_contract_matches(
        "activities",
        {**expected, **s2_activities, "bank_version_id": ("uuid", False)},
        expected,
    )
    # Prerequisite origin joins only after the lr_0008 condition set.
    prereq_s1 = {
        "condition_kind": ("character varying(32)", False),
        "params": ("jsonb", False),
        "group_key": ("character varying(32)", False),
    }
    assert not transfer._column_contract_matches(
        "course_prerequisites",
        {**expected, "origin": ("character varying(16)", False)},
        expected,
    )
    assert transfer._column_contract_matches(
        "course_prerequisites",
        {**expected, **prereq_s1, "origin": ("character varying(16)", False)},
        expected,
    )


def test_prerequisite_origin_standing_default_is_fail_closed_ui():
    # lr_0010 backfills pre-existing rows as yaml_import, but a row
    # created without an origin must never become file-owned: the
    # reconciler stamps yaml_import explicitly on its own rows.
    column = CoursePrerequisite.__table__.c.origin
    assert column.default is not None and column.default.arg == "ui"
    assert column.server_default is not None
    assert column.server_default.arg == "ui"


_NOW = datetime(2020, 1, 1, tzinfo=UTC)
_ONE = Float8Bits("3ff0000000000000")
_TENANT = uuid4()


def _v6_version(bank_id, **overrides):
    values = {
        "id": uuid4(),
        "tenant_id": _TENANT,
        "bank_id": bank_id,
        "version_no": 1,
        "state": "published",
        "origin": "backfill",
        "content_hash": "0" * 64,
        "lint_violations": JsonbText("[]"),
        "base_version_id": None,
        "draft_revision": 0,
        "created_by_actor": None,
        "published_by": None,
        "published_at": _NOW,
        "retired_by": None,
        "retired_at": None,
        "retire_reason": None,
        "superseded_by_version_id": None,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    values.update(overrides)
    return QuestionBankVersionRowV6(**values)


def _v6_bank(current_version_id=None):
    return QuestionBankRowV6(
        id=uuid4(),
        tenant_id=_TENANT,
        created_at=_NOW,
        updated_at=_NOW,
        course_id=uuid4(),
        chapter_number=1,
        kind="chapter",
        version=1,
        current_version_id=current_version_id,
    )


def _v6_snapshot(banks, versions, activities=(), attempts=()):
    values = {table: () for table, _ in _ALL_ROW_TYPES_V6}
    values.update(
        question_banks=tuple(banks),
        question_bank_versions=tuple(versions),
        activities=tuple(activities),
        activity_attempts=tuple(attempts),
    )
    return LearningSnapshotV6(version=6, tenant_ids=(_TENANT,), **values)


def _coherent_v6():
    bank1, bank2 = _v6_bank(), _v6_bank()
    v1, v2 = _v6_version(bank1.id), _v6_version(bank2.id)
    bank1 = replace(bank1, current_version_id=v1.id)
    activity = ActivityRowV6(
        id=uuid4(),
        tenant_id=_TENANT,
        created_at=_NOW,
        updated_at=_NOW,
        course_id=uuid4(),
        chapter_number=1,
        type="mcq_test",
        bank_id=bank1.id,
        title="T",
        pass_threshold=_ONE,
        max_attempts=None,
        assessment_mode="graded",
        grading="auto",
        weight=_ONE,
        question_count=None,
        scoring_policy="best",
        is_final=False,
        min_attempt_interval_seconds=0,
        bank_version_id=v1.id,
    )
    attempt = ActivityAttemptRowV6(
        id=uuid4(),
        tenant_id=_TENANT,
        created_at=_NOW,
        updated_at=_NOW,
        activity_id=activity.id,
        person_id=uuid4(),
        question_ext_ids=JsonbText("[]"),
        started_at=_NOW,
        submitted_at=None,
        bank_version_id=v1.id,
    )
    snapshot = _v6_snapshot([bank1, bank2], [v1, v2], [activity], [attempt])
    return snapshot, bank1, bank2, v1, v2, activity, attempt


def _assert_closure_refuses(snapshot):
    with pytest.raises(SnapshotClosureErrorV6):
        bank_transfer._closure(cast(Any, None), snapshot)


def test_v6_closure_enforces_bank_coherence():
    snapshot, bank1, bank2, v1, v2, activity, attempt = _coherent_v6()
    # The coherent estate closes (scope is unused without events).
    bank_transfer._closure(cast(Any, None), snapshot)
    # A bank's current pointer must be its OWN published version.
    _assert_closure_refuses(
        replace(
            snapshot,
            question_banks=(replace(bank1, current_version_id=v2.id), bank2),
        )
    )
    _assert_closure_refuses(
        replace(
            snapshot,
            question_bank_versions=(
                replace(v1, state="draft", version_no=None),
                v2,
            ),
        )
    )
    # Activity and attempt pins must belong to the activity's bank.
    _assert_closure_refuses(
        replace(snapshot, activities=(replace(activity, bank_version_id=v2.id),))
    )
    _assert_closure_refuses(
        replace(snapshot, activity_attempts=(replace(attempt, bank_version_id=v2.id),))
    )
    # Lineage references resolve in-tenant and never cross banks.
    _assert_closure_refuses(
        replace(
            snapshot,
            question_bank_versions=(replace(v1, base_version_id=uuid4()), v2),
        )
    )
    _assert_closure_refuses(
        replace(
            snapshot,
            question_bank_versions=(replace(v1, base_version_id=v2.id), v2),
        )
    )
    _assert_closure_refuses(
        replace(
            snapshot,
            question_bank_versions=(
                replace(v1, superseded_by_version_id=uuid4()),
                v2,
            ),
        )
    )
    _assert_closure_refuses(
        replace(
            snapshot,
            question_bank_versions=(
                replace(v1, superseded_by_version_id=v2.id),
                v2,
            ),
        )
    )
    # Without an activity bank, the pin keeps its existence-only rule.
    bank_transfer._closure(
        cast(Any, None),
        replace(snapshot, activities=(replace(activity, bank_id=None),)),
    )
