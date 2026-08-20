"""Brand profile tables — the `brand_profiles` lineage root. DUAL-PLANE.

One lineage, two planes, and the DDL is conditional on the assembly's explicit
`ModulePlaneSelection` (ADR-0028). `selected_module_planes` reads that selection;
a missing or unsupported one fails the static composition before this runs.

## The two planes are isolated by different mechanisms, and both are checked

- **Tenant** (`brand_profiles`): `tenant_id UUID NOT NULL`, composite uniques,
  RLS **ENABLEd and FORCEd**, policy created in this same migration (hard rule
  11). FORCE is the half that is easy to omit and impossible to notice — without
  it the table OWNER, which migrations run as, bypasses its own policy, so every
  migration-time check passes while production leaks.
- **Platform** (`platform_brand_profiles`, `platform_brand_host_bindings`): no
  tenant column, no RLS at all, `app_user` REVOKEd. On that plane the revoke IS
  the isolation (hard rule 27), and it is checked as strictly as a policy.

## No foreign key crosses the planes

Hard rule 27: two planes share a lifecycle, never a row. A tenant profile cannot
reference a platform one and vice versa — the OEM brand a vendor ships and the
brand an operator sets are separate records that a resolver merges at read time,
never rows that point at each other.

## There is no CSS column on either plane

ADR-0006 D8, made structural. This module stores two hex values; `dotmac_ui`
validates them and is the only thing that produces CSS. A `custom_css` column
here would reintroduce exactly what D8 retired.

## No CHECK on `scope_type`, `status` or `enabled_surfaces`

ADR-0008: those vocabularies belong to the products. Sub's scope chain is
`organization, reseller, platform`; another product's is not, and a CHECK would
need a different constraint per deployment. The CHECKs that ARE here constrain
what is true regardless — a record version starts at 1.

Revision ID: bp_0001_brand_profiles
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "bp_0001_brand_profiles"
down_revision = None
branch_labels = ("brand_profiles",)

MODULE_CODE = "brand_profiles"

# Three lists, written out in full, because this module's prerequisites GENUINELY
# differ per plane — which is the case the split exists for.
#
# COMMON: the at-most-once ledger (written at REQUEST time by every upsert) and
# the database roles (the tenant plane FORCEs RLS against the tenant app role,
# the platform plane REVOKEs that same role; both halves need the roles).
#
# TENANT: the tenant catalogue and `app_current_tenant_id()`, which every policy
# below calls. A control plane selecting PLATFORM alone installs this module
# without a `tenants` table, which is exactly the vendor-side case.
#
# PLATFORM: the platform audit log, written by the platform upsert path.
COMMON_REQUIRES = ("module_database_roles.v1", "idempotency_ledger.v1")
TENANT_REQUIRES = ("tenant_scope_catalog.v1",)
PLATFORM_REQUIRES = ("platform_audit_log.v1",)

depends_on = resolve_depends_on(
    COMMON_REQUIRES,
    tenant=TENANT_REQUIRES,
    platform=PLATFORM_REQUIRES,
    module=MODULE_CODE,
)

_SCHEMA = "mod_brand"


#: The shared field set, built once per plane. A helper rather than duplicated
#: literals because the two tables must stay structurally identical — a column
#: added to one plane and not the other is a resolver that returns a field on
#: one deployment and not another, and nothing would catch it.
def _profile_columns() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("profile_code", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("product_name", sa.String(length=160), nullable=True),
        sa.Column("legal_name", sa.String(length=200), nullable=True),
        sa.Column("legal_address", postgresql.JSONB(), nullable=True),
        # Two colours, and no CSS column — see the module docstring (ADR-0006 D8).
        sa.Column("primary_hex", sa.String(length=7), nullable=True),
        sa.Column("accent_hex", sa.String(length=7), nullable=True),
        # Opaque `dotmac-files` references, never dereferenced (ADR-0022).
        sa.Column("logo_file_ref", sa.String(length=200), nullable=True),
        sa.Column("dark_logo_file_ref", sa.String(length=200), nullable=True),
        sa.Column("icon_file_ref", sa.String(length=200), nullable=True),
        sa.Column("support_email", sa.String(length=255), nullable=True),
        sa.Column("support_phone", sa.String(length=40), nullable=True),
        sa.Column("support_url", sa.String(length=512), nullable=True),
        sa.Column("sender_email", sa.String(length=255), nullable=True),
        sa.Column("sender_name", sa.String(length=160), nullable=True),
        sa.Column("enabled_surfaces", postgresql.JSONB(), nullable=True),
        sa.Column("default_locale", sa.String(length=35), nullable=True),
        sa.Column("default_timezone", sa.String(length=64), nullable=True),
        sa.Column("mobile_build_profile_ref", sa.String(length=200), nullable=True),
        sa.Column("locked_fields", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("record_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    requires = list(COMMON_REQUIRES)
    if ModulePlane.TENANT in planes:
        requires.extend(TENANT_REQUIRES)
    if ModulePlane.PLATFORM in planes:
        requires.extend(PLATFORM_REQUIRES)
    # Prove every effect for the SELECTED planes before any DDL of this module's
    # own runs. Deploy is the last moment at which a missing prerequisite is a
    # failed migration rather than a failed request in production.
    require_prerequisites(op.get_bind(), tuple(requires))

    op.execute("CREATE SCHEMA IF NOT EXISTS mod_brand;")
    op.execute("GRANT USAGE ON SCHEMA mod_brand TO app_admin;")
    if ModulePlane.TENANT in planes:
        op.execute("GRANT USAGE ON SCHEMA mod_brand TO app_user;")
    if ModulePlane.PLATFORM in planes:
        op.execute("GRANT USAGE ON SCHEMA mod_brand TO platform_api;")

    if ModulePlane.TENANT in planes:
        _create_tenant_plane()
    if ModulePlane.PLATFORM in planes:
        _create_platform_plane()


def _create_tenant_plane() -> None:
    op.create_table(
        "brand_profiles",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_type", sa.String(length=40), nullable=False),
        # NULLABLE on purpose: the tenant-wide scope has no narrower id. A
        # sentinel would be a fake id, which ADR-0023 refuses for tenants and
        # which is no better here.
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=True),
        *_profile_columns(),
        # Composite with `tenant_id`, per hard rule 11: two tenants naming a
        # profile `default` is ordinary, not a collision.
        sa.UniqueConstraint(
            "tenant_id", "profile_code", name="uq_brand_profiles_tenant_code"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "scope_type",
            "scope_id",
            "profile_code",
            name="uq_brand_profiles_tenant_scope_code",
        ),
        sa.CheckConstraint("record_version >= 1", name="ck_brand_profiles_version"),
        schema="mod_brand",
    )
    op.create_index(
        "ix_brand_profiles_tenant_id",
        "brand_profiles",
        ["tenant_id"],
        schema="mod_brand",
    )
    op.create_index(
        "ix_brand_profiles_status", "brand_profiles", ["status"], schema="mod_brand"
    )

    # ENABLE *and* FORCE, in the same migration that creates the table (hard
    # rule 11). FORCE is the half that is easy to omit and impossible to notice:
    # without it the table owner — which migrations run as — bypasses its own
    # policy, so every migration-time check passes while production leaks.
    op.execute("ALTER TABLE mod_brand.brand_profiles ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_brand.brand_profiles FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY brand_profiles_tenant_isolation
            ON mod_brand.brand_profiles
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_brand.brand_profiles "
        "TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_brand.brand_profiles "
        "TO app_admin;"
    )


def _create_platform_plane() -> None:
    op.create_table(
        "platform_brand_profiles",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        *_profile_columns(),
        sa.UniqueConstraint("profile_code", name="uq_platform_brand_profiles_code"),
        sa.CheckConstraint(
            "record_version >= 1", name="ck_platform_brand_profiles_version"
        ),
        schema="mod_brand",
    )
    op.create_index(
        "ix_platform_brand_profiles_status",
        "platform_brand_profiles",
        ["status"],
        schema="mod_brand",
    )

    op.create_table(
        "platform_brand_host_bindings",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_canonical", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Within the platform plane only. NO foreign key reaches the tenant
        # plane — hard rule 27: two planes share a lifecycle, never a row.
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["mod_brand.platform_brand_profiles.id"],
            name="fk_platform_brand_host_bindings_profile_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("host", name="uq_platform_brand_host_bindings_host"),
        schema="mod_brand",
    )
    op.create_index(
        "ix_platform_brand_host_bindings_profile_id",
        "platform_brand_host_bindings",
        ["profile_id"],
        schema="mod_brand",
    )

    # No RLS — not even ENABLEd-with-no-policy, which would deny every row to the
    # control plane while reading as protected. On this plane the REVOKE is the
    # isolation, and it is checked as strictly as a policy (hard rule 27).
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON mod_brand.platform_brand_profiles "
        "TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_brand.platform_brand_host_bindings TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_brand.platform_brand_profiles "
        "TO app_admin;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_brand.platform_brand_host_bindings TO app_admin;"
    )
    op.execute("REVOKE ALL ON mod_brand.platform_brand_profiles FROM app_user;")
    op.execute("REVOKE ALL ON mod_brand.platform_brand_host_bindings FROM app_user;")


def downgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    if ModulePlane.PLATFORM in planes:
        op.drop_index(
            "ix_platform_brand_host_bindings_profile_id",
            "platform_brand_host_bindings",
            schema="mod_brand",
        )
        op.drop_table("platform_brand_host_bindings", schema="mod_brand")
        op.drop_index(
            "ix_platform_brand_profiles_status",
            "platform_brand_profiles",
            schema="mod_brand",
        )
        op.drop_table("platform_brand_profiles", schema="mod_brand")
    if ModulePlane.TENANT in planes:
        op.execute(
            "DROP POLICY IF EXISTS brand_profiles_tenant_isolation "
            "ON mod_brand.brand_profiles;"
        )
        op.drop_index("ix_brand_profiles_status", "brand_profiles", schema="mod_brand")
        op.drop_index(
            "ix_brand_profiles_tenant_id", "brand_profiles", schema="mod_brand"
        )
        op.drop_table("brand_profiles", schema="mod_brand")
    op.execute("DROP SCHEMA IF EXISTS mod_brand RESTRICT;")
