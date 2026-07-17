"""Alembic environment.

Connects as app_admin (RLS bypass) — set MIGRATION_DATABASE_URL or DATABASE_URL.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core import (
    audit,  # noqa: F401  (register AuditEvent for autogenerate)
    settings_models,  # noqa: F401  (register DomainSetting)
)
from app.core.models import Base  # registers Tenant/Party/Role/PartyRole/AuthSession
from app.features.auth import (
    models as auth,  # noqa: F401  (register UserCredential for autogenerate)
)
from app.features.custom_fields import (
    models as custom_fields,  # noqa: F401  (register CustomFieldDefinition)
)

config = context.config

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
