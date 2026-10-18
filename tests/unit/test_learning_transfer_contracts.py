"""Closed storage contracts; these canaries precede the transfer implementation."""

from dataclasses import MISSING, fields, replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_learning.models import TENANT_MODELS
from dotmac_learning.transfer_contracts import (
    ActivityRowV1,
    CertificateRowV1,
    CourseCompletionRowV1,
    CourseRowV1,
    Float8Bits,
    JsonbText,
    LearningEventRowV1,
    LearningSnapshotV1,
    ScoreRowV1,
    SnapshotValidationError,
    SubmissionRowV1,
)

ROW_TYPES = (
    CourseRowV1,
    ActivityRowV1,
    SubmissionRowV1,
    ScoreRowV1,
    CourseCompletionRowV1,
    CertificateRowV1,
    LearningEventRowV1,
)


def empty_snapshot(tenant):
    return LearningSnapshotV1(
        version=1,
        tenant_ids=(tenant,),
        courses=(),
        activities=(),
        submissions=(),
        scores=(),
        course_completions=(),
        certificates=(),
        learning_events=(),
    )


def test_every_persisted_field_is_mandatory_including_nullable_history():
    for row_type, model in zip(ROW_TYPES, TENANT_MODELS, strict=True):
        assert {field.name for field in fields(row_type)} == set(
            model.__table__.c.keys()
        )
        assert all(
            field.default is MISSING and field.default_factory is MISSING
            for field in fields(row_type)
        )
    assert all(field.default is MISSING for field in fields(LearningSnapshotV1))


@pytest.mark.parametrize(
    "extent",
    [
        (),
        (
            uuid4(),
            uuid4(),
        ),
        ("not-a-uuid",),
    ],
)
def test_extent_rejects_empty_duplicate_or_untyped_uuid(extent):
    if len(extent) == 2:
        extent = (extent[0], extent[0])
    with pytest.raises(SnapshotValidationError):
        replace(empty_snapshot(uuid4()), tenant_ids=extent)


def test_zero_row_tenant_is_explicit_and_valid():
    tenant = uuid4()
    assert empty_snapshot(tenant).tenant_ids == (tenant,)


@pytest.mark.parametrize("bits", ["", "0" * 15, "0" * 17, "A" * 16, "g" * 16, 0])
def test_float_bits_are_exact_lowercase_64_bit_hex(bits):
    with pytest.raises(SnapshotValidationError):
        Float8Bits(bits)


def test_json_text_preserves_storage_representation_without_python_json_coercion():
    values = (
        "null",
        '"null"',
        "1.23456789012345678901234567890",
        "[1, null]",
        '{"x": 1}',
    )
    assert [str(JsonbText(value)) for value in values] == list(values)
    with pytest.raises(SnapshotValidationError):
        JsonbText(None)


def test_extent_membership_duplicates_and_native_types_are_checked():
    tenant = uuid4()
    now = datetime(2026, 10, 17, 0, 0, 0, 123456, tzinfo=UTC)
    row = CourseRowV1(
        id=uuid4(),
        tenant_id=tenant,
        created_at=now,
        updated_at=now,
        slug="historical",
        title="",
        discipline="",
        source_ref="",
        version=-7,
        status="legacy",
        listed=True,
    )
    snapshot = replace(empty_snapshot(tenant), courses=(row,))
    with pytest.raises(SnapshotValidationError):
        replace(snapshot, courses=(row, row))
    with pytest.raises(SnapshotValidationError):
        replace(snapshot, tenant_ids=(uuid4(),))
    with pytest.raises(SnapshotValidationError):
        replace(row, version=True)
    with pytest.raises(SnapshotValidationError):
        replace(row, listed=1)
    with pytest.raises(SnapshotValidationError):
        replace(row, id=str(row.id))
    with pytest.raises(SnapshotValidationError):
        replace(snapshot, version=2)
    with pytest.raises(TypeError):
        CourseRowV1(
            **{
                field.name: getattr(row, field.name)
                for field in fields(row)
                if field.name != "updated_at"
            }
        )


def test_transfer_uses_closed_core_relations_and_no_formatted_sql_text():
    import ast
    import re
    from pathlib import Path

    from dotmac_learning import transfer
    from sqlalchemy.sql.elements import ColumnElement

    for contract, model in zip(transfer._TABLES, TENANT_MODELS, strict=True):
        relation = contract.clause
        assert relation.name == model.__table__.name
        assert relation.schema == model.__table__.schema
        assert set(relation.c.keys()) == set(model.__table__.c.keys())
        for column in contract.columns:
            projection = transfer._export_column(contract, column)
            assert isinstance(projection, ColumnElement)
            assert projection.name == column.name

    def formatted_sql_nodes(source):
        findings = []
        tree = ast.parse(source) if isinstance(source, str) else source
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                literal = "".join(
                    part.value
                    for part in node.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                )
                if re.search(
                    r"\b(SELECT|INSERT|DELETE|UPDATE|CREATE|ALTER)\b",
                    literal,
                    re.IGNORECASE,
                ):
                    findings.append(node)
        return findings

    actual_source = Path(transfer.__file__).read_text()
    assert not formatted_sql_nodes(actual_source)
    # Sensitivity: the same checker sees a formatted identifier regression,
    # and ignores the module-owned diagnostic text that remains admissible.
    planted_tree = ast.parse(actual_source)
    planted_tree.body.append(
        ast.Assign(
            targets=[ast.Name(id="statement", ctx=ast.Store())],
            value=ast.JoinedStr(
                values=[
                    ast.Constant(value="SELECT * FROM "),
                    ast.FormattedValue(
                        value=ast.Name(id="name", ctx=ast.Load()),
                        conversion=-1,
                    ),
                ]
            ),
        )
    )
    assert formatted_sql_nodes(planted_tree)
    assert not formatted_sql_nodes('message = f"canonical storage: {name}"')
