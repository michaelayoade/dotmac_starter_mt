"""Template Studio's two tables, bound to the `mod_tstudio` schema (ADR-0006 D1).

Both models carry `schema_table_args(SCHEMA)`, so every SELECT/INSERT the ORM
emits is fully qualified `mod_tstudio.<table>` — never resolved through
`search_path`, which is connection state a pooler or another module can change.
The schema itself is derived from the ledger allocation, never spelled as a
literal here: `module_schema("tstudio")` is the only supported way to build it.

## Why two tables

`templates` is the addressable thing a caller looks up by `(kind, slug)`;
`template_versions` holds immutable revisions of its content. Version history is
intrinsic to a *studio* — an operator edits a customer-facing message and must be
able to see, and go back to, what was sent before — and retrofitting it later
would be a schema break rather than an addition.

`templates.published_version` is an INTEGER (the version number), not a foreign
key to `template_versions`. A FK both ways would make the two tables mutually
dependent, which forces a migration to create one table, then the other, then
circle back to add the constraint — a shape that is fragile to reorder and adds
nothing here, because a version number is already unique within a template.

## Identity is `(tenant_id, slug, channel)` — ADR-0006 § 5b

Channel is part of the identity rather than an attribute of it. One message
routinely exists as an email and an SMS with different wording, and both are the
"same" template to the code that sends it — Sub's seeder relies on exactly this,
seeding one referral-reward code for `push` and `email`. The pre-5b shape keyed on
a `kind` discriminator with channel as a nullable attribute, which could not
represent that at all.

`kind` is gone. The audit disqualified `kind=document`: ERP's document templates
need Jinja control flow, filters and page geometry, and this module renders by
substitution as a deliberate security posture. Documents are a separate owner.

## Tenancy

Both tables are tenant-scoped with a real `tenant_id NOT NULL` column (not an
EXISTS-join subtype), because both are independently queried. `template_versions`
additionally carries a COMPOSITE foreign key `(tenant_id, template_id)` →
`templates (tenant_id, id)` rather than a plain `template_id` FK: a single-column
reference would let one tenant's version row point at another tenant's template
whenever an id leaked. That is the same defence `party_roles` uses in the kernel,
and it is why `templates` declares the otherwise-redundant
`uq_templates_tenant_id_id`.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

# The module's immutable namespace. `module_schema` is the only supported way to
# build it (the `mod_` form is structural, not a convention), and `tstudio` is
# the short code allocated in `dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER`.
SCHEMA = module_schema("tstudio")

# JSONB on Postgres, plain JSON on SQLite — the kernel's own variant pattern.
_JSON_VARIANT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


class Template(Base, TimestampMixin):
    """A tenant's addressable template: `(slug, channel)` within one tenant."""

    __tablename__ = "templates"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "slug", "channel", name="uq_templates_tenant_slug_channel"
        ),
        # Referenced by `template_versions`' composite FK — see the module
        # docstring. Redundant on its own; load-bearing for tenant integrity.
        UniqueConstraint("tenant_id", "id", name="uq_templates_tenant_id_id"),
        Index("ix_templates_tenant_id", "tenant_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        # The COLUMN OBJECT, not a string. The kernel's own tables are in the
        # `public` compatibility namespace but are registered in
        # `Base.metadata` UNQUALIFIED (they declare no `schema=`), so the
        # qualified string `"public.tenants.id"` resolves to no metadata key and
        # raises `NoReferencedTableError`, while the bare `"tenants.id"` would
        # be exactly the search_path-dependent spelling D1 forbids. Referencing
        # the column directly sidesteps string resolution altogether and is
        # unambiguous on both engines. The MIGRATION, which emits the real DDL,
        # still spells `public.tenants.id` in full.
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    # Delivery channel (`email`, `sms`, …). NOT NULL and part of the identity —
    # see the module docstring. An open registered string, never an enum: a
    # product names the channels it actually delivers on (ADR-0008).
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    # The registered `RenderContext` whose variables this template's revisions
    # are validated against. A plain string, resolved through
    # `dotmac_template_studio.contexts` — the module owns the CHECKING, the
    # product owns the vocabulary.
    context: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.true()
    )
    # The version number currently published, or NULL while a template has only
    # drafts. An integer, not an FK — see the module docstring.
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TemplateVersion(Base, TimestampMixin):
    """One immutable revision of a template's content."""

    __tablename__ = "template_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "template_id", "version", name="uq_template_versions_number"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "template_id"],
            [f"{SCHEMA}.templates.tenant_id", f"{SCHEMA}.templates.id"],
            ondelete="CASCADE",
            name="fk_template_versions_template",
        ),
        Index("ix_template_versions_tenant_id", "tenant_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        # Column object, for the reason `Template.tenant_id` documents.
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    template_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    # Monotonic within `(tenant_id, template_id)`, allocated by the service.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Notification subject line. NULL for a document.
    subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # The placeholder names this revision declares, e.g. `["customer_name"]`.
    # Stored as data so the renderer can report an unknown or missing variable
    # without parsing the body twice.
    variables: Mapped[list[str]] = mapped_column(
        _JSON_VARIANT, nullable=False, server_default=sa.text("'[]'")
    )
    # The party who authored the revision, for the audit trail. A bare UUID
    # column, not an FK: `parties` is another authority's table, and a module
    # never joins its way into one (cross-authority references are ids, per the
    # repo's cross-feature rule).
    author_party_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = [
    "SCHEMA",
    "Template",
    "TemplateVersion",
]
