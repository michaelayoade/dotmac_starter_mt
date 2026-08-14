"""Alembic environment — the reference ASSEMBLY's migration environment.

Connects as app_admin (RLS bypass) — set MIGRATION_DATABASE_URL or DATABASE_URL.

Composes two lineages (kernel-boundary Task 1c): the KERNEL base migrations
(shipped as `dotmac_kernel` package data) and this assembly's own migrations
(`alembic/versions`, currently the `a001` custom-fields adoption). The two
directories are listed in `alembic.ini`'s `version_locations`; `target_metadata`
below is composed from the kernel `Base` (all kernel models) PLUS the assembly's
feature models, so autogenerate sees the whole schema.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from dotmac_kernel import (
    audit,  # noqa: F401  (register AuditEvent for autogenerate)
    entitlements,  # noqa: F401  (register TenantEntitlementGrant)
    idempotency_models,  # noqa: F401  (register Idempotency/PlatformIdempotencyRecord)
    models_platform,  # noqa: F401  (register PlatformAdmin/PlatformSession)
    settings_models,  # noqa: F401  (register DomainSetting)
)
from dotmac_kernel.messaging import (
    models as messaging_models,  # noqa: F401  (register OutboxEvent/PlatformOutboxEvent)
)
from dotmac_kernel.models import (  # registers Tenant/Party/Role/PartyRole/AuthSession/UserCredential
    Base,
)
from dotmac_kernel.planes import install_module_plane_selections
from dotmac_kernel.prerequisites import install_prerequisite_bindings
from sqlalchemy import engine_from_config, pool

from alembic import context
from app.assembly import assembly
from app.features.custom_fields import (
    models as custom_fields,  # noqa: F401  (register CustomFieldDefinition)
)
from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

config = context.config

# Installed BEFORE the revision map is built: a composed module lineage resolves
# its `depends_on` from these bindings at script-load time, so an assembly that
# composes a module without answering what it requires fails loudly here rather
# than ordering wrongly. See `app/migration_bindings.py`.
install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
install_module_plane_selections(
    config.attributes.get("module_plane_selections", assembly.module_planes)
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    return os.getenv("MIGRATION_DATABASE_URL") or os.getenv("DATABASE_URL") or ""


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
