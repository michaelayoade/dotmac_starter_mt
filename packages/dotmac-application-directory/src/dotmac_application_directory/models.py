"""The directory's one table, bound to the `mod_appdir` schema (ADR-0006 D1).

`application_bindings` is the tenant's connected-application portfolio: one row
per application instance the tenant has, carrying the descriptor copy, the
lifecycle state, the provenance and the reconciliation state.

## The column that is not here

There is **no** column naming a person, a member, a group, a role, a grant or a
permission — and there never may be one. ADR-0021 §3 makes directory visibility
distinct from authorization, and a table is where that distinction would be lost
first: one `granted_role_codes` column and the directory has quietly become an
access control list that no target application agreed to.

This is enforced, not merely asked for:
`tests/architecture/test_application_directory_module.py
::test_the_directory_holds_no_authorization_column` fails the build on a column
whose name matches the authorization vocabulary. Desired allocation belongs to
`dotmac-application-access` (deferred, ADR-0021 §5); effective grants belong to
the target application and to nothing else.

## Why the descriptor is copied rather than referenced

The binding stores `descriptor_version`, `descriptor_digest` and
`role_catalogue_digest` — a copy of what the application last said about itself.
A reference would mean the Workspace could not answer "what did we believe, and
when" without reaching the application, which is exactly what it cannot do when
the application is unreachable. The copy is a cache with one canonical writer
(the reconciler) and a freshness timestamp; the application remains the
authority, and `reconciliation_status` is the honest statement of how far the
copy can be trusted.

## Tenancy

`tenant_id NOT NULL`, composite uniques including it, RLS ENABLEd and FORCEd in
the same migration (hard rule 11). `uq_application_bindings_tenant_id_id` is
otherwise redundant, and exists so that any future table — the access module's,
or a product's — references a binding through the COMPOSITE `(tenant_id, id)`
rather than a bare id. A single-column reference would let one tenant's row
point at another tenant's binding the moment an id leaked.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

#: The module's immutable namespace, from the ledger allocation.
SCHEMA = module_schema("appdir")

#: `sha256:` + 64 hex characters, as `descriptor.py` produces. Sized exactly
#: rather than generously: a digest column wide enough for prose invites
#: something that is not a digest.
_DIGEST_LENGTH = 71


class ApplicationBinding(Base, TimestampMixin):
    """One application instance connected to one Workspace tenant."""

    __tablename__ = "application_bindings"
    __table_args__ = (
        # One binding per instance per tenant. The tenant may hold many
        # instances of the same application (`application_code` repeats), which
        # is why the uniqueness is over the pair and not over the code alone.
        UniqueConstraint(
            "tenant_id",
            "application_code",
            "instance_ref",
            name="uq_application_bindings_tenant_application_instance",
        ),
        # The composite-FK target for anything that later references a binding.
        UniqueConstraint(
            "tenant_id", "id", name="uq_application_bindings_tenant_id_id"
        ),
        Index("ix_application_bindings_tenant_id", "tenant_id"),
        # The launcher's query: this tenant's bindings by state. State leads
        # because the launcher filters on it before anything else.
        Index("ix_application_bindings_tenant_state", "tenant_id", "state"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        # The column object, not a string: the kernel's tables are registered
        # unqualified in `Base.metadata`, so `"public.tenants.id"` resolves to
        # no metadata key while a bare `"tenants.id"` would be search_path
        # dependent. The migration spells the qualified form out in full.
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )

    # ── Identity of the connected application ───────────────────────────────
    #: The logical application, e.g. `"sub"`. Stable across instances.
    application_code: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Which deployment/instance. Opaque here; meaningful to the fleet owner.
    instance_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The tenant identifier INSIDE the target application. Never assumed equal
    #: to `tenant_id` — the two are issued by different planes.
    local_tenant_ref: Mapped[str] = mapped_column(String(200), nullable=False)

    # ── The descriptor copy ─────────────────────────────────────────────────
    admin_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_audience: Mapped[str] = mapped_column(String(200), nullable=False)
    descriptor_version: Mapped[int] = mapped_column(Integer, nullable=False)
    descriptor_digest: Mapped[str] = mapped_column(
        String(_DIGEST_LENGTH), nullable=False
    )
    #: Stored alongside the whole-descriptor digest because they answer
    #: different questions — see `descriptor.py`. A binding whose
    #: `descriptor_digest` moved but whose `role_catalogue_digest` did not has
    #: had its admin URL or audience change, and no allocation needs re-issuing.
    role_catalogue_digest: Mapped[str] = mapped_column(
        String(_DIGEST_LENGTH), nullable=False
    )

    # ── Lifecycle and provenance ────────────────────────────────────────────
    #: One of `lifecycle.BindingState`. Closed in Python, text in the database —
    #: see that module for why.
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    #: One of `lifecycle.BindingSource`. Provenance, never authority.
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    # ── Freshness ───────────────────────────────────────────────────────────
    #: When the descriptor copy was last successfully read from the
    #: application. NULL means never — which is why `reconciliation_status`
    #: starts at `unknown` rather than `stale`.
    descriptor_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: One of `lifecycle.ReconciliationStatus`.
    reconciliation_status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Why the last reconciliation failed. NULL whenever the status is not
    #: `failed`; the service clears it on every success so a stale explanation
    #: cannot outlive the failure it described.
    reconciliation_error: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = ["SCHEMA", "ApplicationBinding"]
