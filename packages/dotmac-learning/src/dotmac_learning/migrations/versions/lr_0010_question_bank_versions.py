"""Add the question-bank version layer and provenance (lr_0010).

Additive over lr_0009: one immutable-version table, bank/activity/attempt
version pins, the bank natural-key unique, and prerequisite provenance.
The backfill publishes v1 (origin=backfill) for every existing bank with
its canonical content hash, points questions, banks and activities at it,
and pins OPEN attempts only — closed history keeps a NULL pin rather
than a fabricated one. Existing prerequisites backfill origin=yaml_import
(they were file-created in practice), which is what the fixed
reconciler may delete; the standing insert default is then ui, so a
prerequisite created without an explicit origin is never file-owned.

Downgrade is rehearsal-only: it refuses once any bank has moved beyond
its single backfilled published v1, or any ui-origin prerequisite exists.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    PARTY_PERSON_CATALOG_V1,
    TENANT_SCOPE_CATALOG_V1,
    resolve_depends_on,
)
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "lr_0010_question_bank_versions"
down_revision = "lr_0009_composable_policies"
branch_labels = None
REQUIRES = (
    TENANT_SCOPE_CATALOG_V1.name,
    MODULE_DATABASE_ROLES_V1.name,
    PARTY_PERSON_CATALOG_V1.name,
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_learning"


def _canonical_content_hash(questions: list[dict[str, Any]]) -> str:
    # Byte-identical to bank_lifecycle.canonical_content_hash; hosted
    # migration tests pin the two definitions together.
    ordered = sorted(questions, key=lambda q: str(q["ext_id"]))
    payload = [
        {
            "ext_id": q["ext_id"],
            "stem": q["stem"],
            "type": q["type"],
            "options": q.get("options", []),
            "correct": q.get("correct", []),
            "rubric_category": q["rubric_category"],
            "category": q.get("category"),
            "explanation": q.get("explanation", ""),
            "weight": q["weight"],
        }
        for q in ordered
    ]
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _lint_violations(
    *, chapter: int | None, kind: str, questions: list[dict[str, Any]]
) -> list[str]:
    from dotmac_learning_bank_rules import BankDoc, lint_bank

    doc = BankDoc(
        course="",
        chapter=chapter,
        kind=kind,
        version=1,
        questions=[
            {
                "id": q["ext_id"],
                "stem": q["stem"],
                "type": q["type"],
                "options": q.get("options", []),
                "correct": q.get("correct", []),
                "rubric_category": q["rubric_category"],
                "category": q.get("category"),
                "explanation": q.get("explanation", ""),
                "weight": q["weight"],
            }
            for q in questions
        ],
        policy={},
    )
    return lint_bank(doc)


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    bind = op.get_bind()
    duplicates = bind.scalar(
        sa.text(
            "SELECT count(*) FROM ("
            "SELECT 1 FROM mod_learning.question_banks "
            "GROUP BY tenant_id, course_id, chapter_number, kind "
            "HAVING count(*) > 1) AS dupes"
        )
    )
    if duplicates:
        raise RuntimeError(
            f"{duplicates} duplicate question-bank natural key(s) exist; "
            "merge them before lr_0010 can enforce bank identity"
        )

    op.create_table(
        "question_bank_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bank_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(10), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "lint_violations",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("base_version_id", sa.Uuid(), nullable=True),
        sa.Column("draft_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_actor", sa.Uuid(), nullable=True),
        sa.Column("published_by", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_by", sa.Uuid(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retire_reason", sa.Text(), nullable=True),
        sa.Column("superseded_by_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_question_bank_versions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "bank_id",
            "version_no",
            name="uq_question_bank_versions_bank_no",
        ),
        sa.CheckConstraint(
            "state IN ('draft', 'published', 'retired')",
            name="ck_question_bank_versions_state",
        ),
        sa.CheckConstraint(
            "origin IN ('ui', 'yaml_import', 'backfill')",
            name="ck_question_bank_versions_origin",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "bank_id"],
            ["mod_learning.question_banks.tenant_id", "mod_learning.question_banks.id"],
            ondelete="CASCADE",
            name="fk_question_bank_versions_tenant_bank",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_question_bank_versions_tenant_id",
        "question_bank_versions",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_question_bank_versions_bank_id",
        "question_bank_versions",
        ["bank_id"],
        schema=_SCHEMA,
    )
    # Same tenant-isolation posture as every sibling lr table
    # (lr_0001..lr_0007): forced RLS plus the standing app grants.
    # Downgrade drops the table, which removes both symmetrically.
    op.execute(
        "ALTER TABLE mod_learning.question_bank_versions " "ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE mod_learning.question_bank_versions " "FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        "CREATE POLICY question_bank_versions_tenant_isolation "
        "ON mod_learning.question_bank_versions "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id())"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_learning.question_bank_versions TO app_user, platform_api"
    )
    op.create_index(
        "uq_question_bank_versions_one_draft",
        "question_bank_versions",
        ["tenant_id", "bank_id"],
        unique=True,
        postgresql_where=sa.text("state = 'draft'"),
        schema=_SCHEMA,
    )

    op.add_column(
        "question_banks",
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        schema=_SCHEMA,
    )
    op.create_foreign_key(
        "fk_question_banks_tenant_current_version",
        "question_banks",
        "question_bank_versions",
        ["tenant_id", "current_version_id"],
        ["tenant_id", "id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_index(
        "ix_question_banks_current_version_id",
        "question_banks",
        ["current_version_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_question_banks_natural_key",
        "question_banks",
        ["tenant_id", "course_id", "chapter_number", "kind"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        schema=_SCHEMA,
    )

    op.add_column(
        "questions",
        sa.Column("bank_version_id", sa.Uuid(), nullable=True),
        schema=_SCHEMA,
    )
    op.create_foreign_key(
        "fk_questions_tenant_bank_version",
        "questions",
        "question_bank_versions",
        ["tenant_id", "bank_version_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
    )
    op.create_index(
        "ix_questions_bank_version_id",
        "questions",
        ["bank_version_id"],
        schema=_SCHEMA,
    )

    op.add_column(
        "activities",
        sa.Column("bank_version_id", sa.Uuid(), nullable=True),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_activities_bank_version_id",
        "activities",
        ["bank_version_id"],
        schema=_SCHEMA,
    )
    op.add_column(
        "activity_attempts",
        sa.Column("bank_version_id", sa.Uuid(), nullable=True),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_activity_attempts_bank_version_id",
        "activity_attempts",
        ["bank_version_id"],
        schema=_SCHEMA,
    )
    op.add_column(
        "course_prerequisites",
        sa.Column(
            "origin",
            sa.String(16),
            nullable=False,
            server_default="yaml_import",
        ),
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_course_prerequisites_origin",
        "course_prerequisites",
        "origin IN ('yaml_import', 'ui')",
        schema=_SCHEMA,
    )
    # The yaml_import server default above exists only to backfill rows
    # that predate the column. The standing default is fail-closed: a
    # row created without an origin is app-authored (ui), and the
    # reconciler stamps yaml_import explicitly on its own rows.
    op.alter_column(
        "course_prerequisites",
        "origin",
        server_default="ui",
        schema=_SCHEMA,
    )

    _backfill(bind)

    op.drop_constraint("uq_questions_bank_ext", "questions", schema=_SCHEMA)
    op.create_unique_constraint(
        "uq_questions_bank_version_ext",
        "questions",
        ["tenant_id", "bank_version_id", "ext_id"],
        schema=_SCHEMA,
    )


def _backfill(bind: sa.Connection) -> None:
    banks = (
        bind.execute(
            sa.text(
                "SELECT id, tenant_id, chapter_number, kind "
                "FROM mod_learning.question_banks ORDER BY tenant_id, id"
            )
        )
        .mappings()
        .all()
    )
    for bank in banks:
        questions = [
            dict(row)
            for row in bind.execute(
                sa.text(
                    "SELECT ext_id, stem, type, options, correct, "
                    "rubric_category, category, explanation, weight "
                    "FROM mod_learning.questions "
                    "WHERE tenant_id = :tenant_id AND bank_id = :bank_id "
                    "ORDER BY ext_id"
                ),
                {"tenant_id": bank["tenant_id"], "bank_id": bank["id"]},
            ).mappings()
        ]
        version_id = uuid.uuid4()
        bind.execute(
            sa.text(
                "INSERT INTO mod_learning.question_bank_versions "
                "(id, tenant_id, bank_id, version_no, state, origin, "
                "content_hash, lint_violations) VALUES "
                "(:id, :tenant_id, :bank_id, 1, 'published', 'backfill', "
                ":content_hash, CAST(:lint AS jsonb))"
            ),
            {
                "id": version_id,
                "tenant_id": bank["tenant_id"],
                "bank_id": bank["id"],
                "content_hash": _canonical_content_hash(questions),
                "lint": json.dumps(
                    _lint_violations(
                        chapter=bank["chapter_number"],
                        kind=bank["kind"],
                        questions=questions,
                    )
                ),
            },
        )
        bind.execute(
            sa.text(
                "UPDATE mod_learning.questions SET bank_version_id = :vid "
                "WHERE tenant_id = :tenant_id AND bank_id = :bank_id"
            ),
            {"vid": version_id, "tenant_id": bank["tenant_id"], "bank_id": bank["id"]},
        )
        bind.execute(
            sa.text(
                "UPDATE mod_learning.question_banks "
                "SET current_version_id = :vid, version = 1 "
                "WHERE tenant_id = :tenant_id AND id = :bank_id"
            ),
            {"vid": version_id, "tenant_id": bank["tenant_id"], "bank_id": bank["id"]},
        )
        bind.execute(
            sa.text(
                "UPDATE mod_learning.activities SET bank_version_id = :vid "
                "WHERE tenant_id = :tenant_id AND bank_id = :bank_id"
            ),
            {"vid": version_id, "tenant_id": bank["tenant_id"], "bank_id": bank["id"]},
        )
    # Open sittings pin to their activity's backfilled version; closed
    # attempts keep NULL (their content cannot be reconstructed honestly).
    bind.execute(
        sa.text(
            "UPDATE mod_learning.activity_attempts AS attempt "
            "SET bank_version_id = activity.bank_version_id "
            "FROM mod_learning.activities AS activity "
            "WHERE activity.tenant_id = attempt.tenant_id "
            "AND activity.id = attempt.activity_id "
            "AND attempt.submitted_at IS NULL"
        )
    )
    unpinned = bind.scalar(
        sa.text(
            "SELECT count(*) FROM mod_learning.questions "
            "WHERE bank_version_id IS NULL"
        )
    )
    if unpinned:
        raise RuntimeError(
            f"bank-version backfill left {unpinned} question(s) unpinned; "
            "refusing to continue"
        )


def downgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    bind = op.get_bind()
    moved = bind.scalar(
        sa.text(
            "SELECT count(*) FROM mod_learning.question_bank_versions "
            "WHERE state <> 'published' OR version_no IS DISTINCT FROM 1 "
            "OR origin <> 'backfill'"
        )
    )
    banks = bind.scalar(sa.text("SELECT count(*) FROM mod_learning.question_banks"))
    versions = bind.scalar(
        sa.text("SELECT count(*) FROM mod_learning.question_bank_versions")
    )
    ui_prereqs = bind.scalar(
        sa.text(
            "SELECT count(*) FROM mod_learning.course_prerequisites "
            "WHERE origin <> 'yaml_import'"
        )
    )
    if moved or versions != banks or ui_prereqs:
        raise RuntimeError(
            "lr_0010 downgrade is rehearsal-only: bank versions or "
            "prerequisite provenance have been populated beyond the "
            "backfill baseline"
        )
    op.drop_constraint("uq_questions_bank_version_ext", "questions", schema=_SCHEMA)
    op.create_unique_constraint(
        "uq_questions_bank_ext",
        "questions",
        ["tenant_id", "bank_id", "ext_id"],
        schema=_SCHEMA,
    )
    op.drop_constraint(
        "ck_course_prerequisites_origin", "course_prerequisites", schema=_SCHEMA
    )
    op.drop_column("course_prerequisites", "origin", schema=_SCHEMA)
    op.drop_index(
        "ix_activity_attempts_bank_version_id",
        table_name="activity_attempts",
        schema=_SCHEMA,
    )
    op.drop_column("activity_attempts", "bank_version_id", schema=_SCHEMA)
    op.drop_index(
        "ix_activities_bank_version_id", table_name="activities", schema=_SCHEMA
    )
    op.drop_column("activities", "bank_version_id", schema=_SCHEMA)
    op.drop_index(
        "ix_questions_bank_version_id", table_name="questions", schema=_SCHEMA
    )
    op.drop_constraint("fk_questions_tenant_bank_version", "questions", schema=_SCHEMA)
    op.drop_column("questions", "bank_version_id", schema=_SCHEMA)
    op.drop_index(
        "uq_question_banks_natural_key",
        table_name="question_banks",
        schema=_SCHEMA,
    )
    op.drop_index(
        "ix_question_banks_current_version_id",
        table_name="question_banks",
        schema=_SCHEMA,
    )
    op.drop_constraint(
        "fk_question_banks_tenant_current_version",
        "question_banks",
        schema=_SCHEMA,
    )
    op.drop_column("question_banks", "current_version_id", schema=_SCHEMA)
    op.drop_index(
        "uq_question_bank_versions_one_draft",
        table_name="question_bank_versions",
        schema=_SCHEMA,
    )
    op.drop_index(
        "ix_question_bank_versions_bank_id",
        table_name="question_bank_versions",
        schema=_SCHEMA,
    )
    op.drop_index(
        "ix_question_bank_versions_tenant_id",
        table_name="question_bank_versions",
        schema=_SCHEMA,
    )
    op.drop_table("question_bank_versions", schema=_SCHEMA)
