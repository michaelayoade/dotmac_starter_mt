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

# lr_0008/lr_0009 (RD-1): frozen V1-V5 rows deliberately do not carry the
# composable-policy columns; transfers refuse non-default values instead.
# lr_0010 extends the same seam: frozen rows do not carry the bank-version
# pins or prerequisite origin, and the question_bank_versions table joins
# only at V6 (which carries every column below).
A3_UNCARRIED_COLUMNS = {
    "courses": {
        "prerequisite_composition",
        "completion_fraction",
        "certificate_auto_issue",
        "certificate_validity_days",
    },
    "activities": {
        "scoring_policy",
        "is_final",
        "min_attempt_interval_seconds",
        "bank_version_id",
    },
    "certificates": {"expires_at"},
    "course_prerequisites": {"condition_kind", "params", "group_key", "origin"},
    "tracks": {"gating_mode"},
    "cohorts": {"auto_accept_below_action"},
    "course_offerings": {"pacing_default_open"},
    "question_banks": {"current_version_id"},
    "questions": {"bank_version_id"},
    "activity_attempts": {"bank_version_id"},
}


def carried_column_keys(model):
    return set(model.__table__.c.keys()) - A3_UNCARRIED_COLUMNS.get(
        model.__tablename__, set()
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
        assert {field.name for field in fields(row_type)} == carried_column_keys(model)
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
        assert set(relation.c.keys()) == carried_column_keys(model)
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


def test_a3_column_contract_admits_only_the_lr_0008_lr_0009_sets():
    from dotmac_learning import transfer

    expected = {"id": ("uuid", False), "tenant_id": ("uuid", False)}
    assert transfer._column_contract_matches("courses", dict(expected), expected)
    admitted = {
        **expected,
        "prerequisite_composition": ("character varying(8)", False),
    }
    assert transfer._column_contract_matches("courses", admitted, expected)
    # Wrong type, wrong nullability, partial set and unknown extras refuse.
    assert not transfer._column_contract_matches(
        "courses",
        {**expected, "prerequisite_composition": ("character varying(9)", False)},
        expected,
    )
    assert not transfer._column_contract_matches(
        "courses",
        {**expected, "prerequisite_composition": ("character varying(8)", True)},
        expected,
    )
    assert not transfer._column_contract_matches(
        "courses", {**admitted, "other": ("text", True)}, expected
    )
    assert not transfer._column_contract_matches(
        "course_prerequisites",
        {**expected, "condition_kind": ("character varying(32)", False)},
        expected,
    )
    full_prereq = {
        **expected,
        "condition_kind": ("character varying(32)", False),
        "params": ("jsonb", False),
        "group_key": ("character varying(32)", False),
    }
    assert transfer._column_contract_matches(
        "course_prerequisites", full_prereq, expected
    )
    # A table without an lr_0008 allowance never admits extras.
    assert not transfer._column_contract_matches(
        "activities", admitted | {"x": ("text", True)}, expected
    )
    # lr_0009 sets join only as whole sets, after the lr_0008 set.
    s2_courses = {
        "completion_fraction": ("double precision", False),
        "certificate_auto_issue": ("boolean", False),
        "certificate_validity_days": ("integer", True),
    }
    assert transfer._column_contract_matches(
        "courses", {**admitted, **s2_courses}, expected
    )
    assert not transfer._column_contract_matches(
        "courses",
        {**admitted, "completion_fraction": ("double precision", False)},
        expected,
    )
    assert not transfer._column_contract_matches(
        "activities", {**expected, "is_final": ("boolean", False)}, expected
    )
    assert transfer._column_contract_matches(
        "activities",
        {
            **expected,
            "scoring_policy": ("character varying(10)", False),
            "is_final": ("boolean", False),
            "min_attempt_interval_seconds": ("integer", False),
        },
        expected,
    )


def test_a3_s2_column_contract_refuses_type_nullability_and_chain_drift():
    """S2 (lr_0009) matcher drift cases, sealed (S2-RD1-F2).

    Wrong float/timestamp spellings, nullability drift, partial sets and
    out-of-chain S2-only states all mismatch; only migration-chain
    prefixes as whole sets per table admit.
    """
    from dotmac_learning import transfer

    expected = {"id": ("uuid", False), "tenant_id": ("uuid", False)}
    s1_courses = {
        **expected,
        "prerequisite_composition": ("character varying(8)", False),
    }
    s2_courses = {
        "completion_fraction": ("double precision", False),
        "certificate_auto_issue": ("boolean", False),
        "certificate_validity_days": ("integer", True),
    }
    # Out of chain: the S2 set without the S1 set never admits.
    assert not transfer._column_contract_matches(
        "courses", {**expected, **s2_courses}, expected
    )
    full = {**s1_courses, **s2_courses}
    assert transfer._column_contract_matches("courses", dict(full), expected)
    # Wrong float spelling ("real" is float4, not the pinned double precision).
    assert not transfer._column_contract_matches(
        "courses", {**full, "completion_fraction": ("real", False)}, expected
    )
    # Nullability drift in either direction.
    assert not transfer._column_contract_matches(
        "courses", {**full, "certificate_validity_days": ("integer", False)}, expected
    )
    assert not transfer._column_contract_matches(
        "courses", {**full, "certificate_auto_issue": ("boolean", True)}, expected
    )
    # Certificates: only "timestamp with time zone", nullable, admits.
    assert transfer._column_contract_matches(
        "certificates",
        {**expected, "expires_at": ("timestamp with time zone", True)},
        expected,
    )
    assert not transfer._column_contract_matches(
        "certificates",
        {**expected, "expires_at": ("timestamp without time zone", True)},
        expected,
    )
    assert not transfer._column_contract_matches(
        "certificates",
        {**expected, "expires_at": ("timestamp with time zone", False)},
        expected,
    )
    # Activities: partial S2 sets and length drift refuse.
    s2_activities = {
        "scoring_policy": ("character varying(10)", False),
        "is_final": ("boolean", False),
        "min_attempt_interval_seconds": ("integer", False),
    }
    for missing in s2_activities:
        partial = {
            **expected,
            **{k: v for k, v in s2_activities.items() if k != missing},
        }
        assert not transfer._column_contract_matches(
            "activities", partial, expected
        ), missing
    assert not transfer._column_contract_matches(
        "activities",
        {
            **expected,
            **s2_activities,
            "scoring_policy": ("character varying(11)", False),
        },
        expected,
    )
    # Cohorts/offerings: whole single-column sets with pinned shapes only.
    assert transfer._column_contract_matches(
        "cohorts",
        {**expected, "auto_accept_below_action": ("character varying(16)", False)},
        expected,
    )
    assert not transfer._column_contract_matches(
        "cohorts",
        {**expected, "auto_accept_below_action": ("character varying(16)", True)},
        expected,
    )
    assert transfer._column_contract_matches(
        "course_offerings",
        {**expected, "pacing_default_open": ("boolean", False)},
        expected,
    )
    assert not transfer._column_contract_matches(
        "course_offerings",
        {**expected, "pacing_default_open": ("boolean", True)},
        expected,
    )
    # An S2 column on a table that does not own it never admits.
    assert not transfer._column_contract_matches(
        "scores", {**expected, "is_final": ("boolean", False)}, expected
    )


# RD-1 prerequisite-origin probe direction (PR #923 repair): the probe
# default must be lr_0010's standing server default ``ui`` — the value
# frozen imports materialize — so a clean round trip passes, while
# ``yaml_import`` provenance refuses. Proven against the real guard with
# a capturing fake scope: no database involved.
def _rd1_captured_statements(monkeypatch, *, origin_is_non_default: bool):
    from dotmac_learning import transfer as transfer_module

    captured: list[str] = []

    class _Result:
        def __init__(self, value):
            self._value = value

        def scalars(self):
            return iter(self._value)

        def scalar_one(self):
            return self._value

    class _FakeScope:
        def execute(self, statement, parameters=None):
            # pg_catalog probe: report every a3 column present.
            return _Result(
                [
                    "prerequisite_composition",
                    "completion_fraction",
                    "certificate_auto_issue",
                    "certificate_validity_days",
                    "scoring_policy",
                    "is_final",
                    "min_attempt_interval_seconds",
                    "expires_at",
                    "condition_kind",
                    "params",
                    "group_key",
                    "gating_mode",
                    "auto_accept_below_action",
                    "pacing_default_open",
                    "bank_version_id",
                    "current_version_id",
                    "origin",
                    "tenant_id",
                ]
            )

        def extent_query(self, statement, tenant_ids):
            import warnings

            from sqlalchemy.dialects import postgresql
            from sqlalchemy.exc import SAWarning

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SAWarning)
                rendered = str(
                    statement.compile(
                        dialect=postgresql.dialect(),
                        compile_kwargs={"literal_binds": True},
                    )
                )
            captured.append(rendered)
            if "course_prerequisites.origin" in rendered:
                return _Result(origin_is_non_default)
            return _Result(False)

    return transfer_module, _FakeScope(), captured


def test_rd1_origin_probe_defaults_to_ui_and_refuses_yaml_import(monkeypatch):
    transfer_module, scope, captured = _rd1_captured_statements(
        monkeypatch, origin_is_non_default=False
    )
    # Clean direction: every probe at its default — nothing raises.
    transfer_module._refuse_non_default_prerequisite_policy(
        scope, (uuid4(),), include_participation=True
    )
    origin_probes = [s for s in captured if "course_prerequisites.origin" in s]
    assert len(origin_probes) == 1
    assert "origin != 'ui'" in origin_probes[0]
    assert "yaml_import" not in origin_probes[0]

    # Refusal direction: the same probe finding a row (a yaml_import
    # row is exactly what ``origin != 'ui'`` matches beyond the
    # standing default) refuses the transfer.
    transfer_module2, scope2, _ = _rd1_captured_statements(
        monkeypatch, origin_is_non_default=True
    )
    with pytest.raises(SnapshotValidationError, match="RD-1"):
        transfer_module2._refuse_non_default_prerequisite_policy(
            scope2, (uuid4(),), include_participation=True
        )
