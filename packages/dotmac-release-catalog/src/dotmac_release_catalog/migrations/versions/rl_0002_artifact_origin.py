"""Bind artifact origin to its admissible attestation contracts.

Revision ID: rl_0002_artifact_origin
Revises: rl_0001_release_artifacts
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "rl_0002_artifact_origin"
down_revision = "rl_0001_release_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "release_artifacts",
        sa.Column(
            "origin_class",
            sa.String(length=40),
            nullable=False,
            server_default="dotmac_product",
        ),
        schema="mod_rel",
    )
    op.create_check_constraint(
        "ck_release_artifacts_origin_class",
        "release_artifacts",
        "origin_class IN ('dotmac_product', 'upstream_third_party')",
        schema="mod_rel",
    )
    # The default exists only to classify rows written by the four earlier
    # releases. Leaving it in place would let a new raw-SQL publisher omit the
    # decision and silently label upstream bytes as a Dotmac product.
    op.alter_column(
        "release_artifacts",
        "origin_class",
        server_default=None,
        schema="mod_rel",
    )
    op.execute(
        """
        CREATE FUNCTION mod_rel.enforce_artifact_attestation_origin()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            artifact_origin text;
        BEGIN
            -- Serialize this evidence write with any concurrent offline origin
            -- repair without requiring UPDATE privilege on the parent table.
            -- `platform_api` deliberately has SELECT+INSERT only, and Postgres
            -- treats SELECT FOR UPDATE as an UPDATE-privileged operation.
            PERFORM pg_advisory_xact_lock(
                hashtextextended(NEW.artifact_id::text, 0)
            );
            SELECT origin_class INTO STRICT artifact_origin
            FROM mod_rel.release_artifacts
            WHERE id = NEW.artifact_id;

            IF NEW.attestation_kind IN (
                'product_manifest',
                'capability_contract',
                'capability_schema',
                'capability_composition'
            )
               AND artifact_origin <> 'dotmac_product' THEN
                RAISE EXCEPTION 'upstream artifact cannot carry Dotmac product evidence'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_artifact_attestations_origin_kind';
            END IF;

            IF NEW.attestation_kind IN (
                'vulnerability_policy_result',
                'compatibility_result'
            ) AND artifact_origin <> 'upstream_third_party' THEN
                RAISE EXCEPTION 'Dotmac artifact cannot carry upstream admission result'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_artifact_attestations_origin_kind';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_artifact_attestations_origin_kind
        BEFORE INSERT OR UPDATE OF artifact_id, attestation_kind
        ON mod_rel.artifact_attestations
        FOR EACH ROW
        EXECUTE FUNCTION mod_rel.enforce_artifact_attestation_origin();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_rel.enforce_artifact_origin_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            -- Pair with the attestation trigger's transaction advisory lock.
            -- The update path is offline/admin-only, but must still serialize
            -- with a concurrent online evidence insert.
            PERFORM pg_advisory_xact_lock(hashtextextended(NEW.id::text, 0));
            IF NEW.origin_class = 'upstream_third_party'
               AND EXISTS (
                   SELECT 1 FROM mod_rel.artifact_attestations
                   WHERE artifact_id = NEW.id
                     AND attestation_kind IN (
                         'product_manifest',
                         'capability_contract',
                         'capability_schema',
                         'capability_composition'
                     )
               ) THEN
                RAISE EXCEPTION 'upstream artifact cannot carry Dotmac product evidence'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_artifact_attestations_origin_kind';
            END IF;

            IF NEW.origin_class = 'dotmac_product'
               AND EXISTS (
                   SELECT 1 FROM mod_rel.artifact_attestations
                   WHERE artifact_id = NEW.id
                     AND attestation_kind IN (
                         'vulnerability_policy_result',
                         'compatibility_result'
                     )
               ) THEN
                RAISE EXCEPTION 'Dotmac artifact cannot carry upstream admission result'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_artifact_attestations_origin_kind';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_release_artifacts_origin_update
        BEFORE UPDATE OF origin_class
        ON mod_rel.release_artifacts
        FOR EACH ROW
        EXECUTE FUNCTION mod_rel.enforce_artifact_origin_update();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER trg_release_artifacts_origin_update "
        "ON mod_rel.release_artifacts;"
    )
    op.execute("DROP FUNCTION mod_rel.enforce_artifact_origin_update();")
    op.execute(
        "DROP TRIGGER trg_artifact_attestations_origin_kind "
        "ON mod_rel.artifact_attestations;"
    )
    op.execute("DROP FUNCTION mod_rel.enforce_artifact_attestation_origin();")
    op.drop_constraint(
        "ck_release_artifacts_origin_class",
        "release_artifacts",
        schema="mod_rel",
        type_="check",
    )
    op.drop_column("release_artifacts", "origin_class", schema="mod_rel")
