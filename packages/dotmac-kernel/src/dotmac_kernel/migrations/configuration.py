"""Pure migration URL selection and Alembic configuration writing.

The assembly supplies held URL material and explicitly decides whether a
runtime fallback is permitted. This module reads no settings, loads no
credentials, and constructs no engine or session.
"""

from __future__ import annotations

from typing import Protocol


class _AlembicConfig(Protocol):
    def set_main_option(self, name: str, value: str) -> None: ...


def select_migration_database_url(
    *,
    migration_database_url: str | None,
    fallback_database_url: str | None = None,
) -> str:
    """Return held URL bytes, refusing a supplied blank migration owner.

    Only an absent migration owner permits the caller's explicit fallback.
    Refusals contain fixed messages and never include supplied material.
    """
    if migration_database_url is not None:
        if not migration_database_url.strip():
            raise ValueError("MIGRATION_DATABASE_URL must not be blank when supplied")
        return migration_database_url
    if fallback_database_url is None or not fallback_database_url.strip():
        raise ValueError("A migration database URL is required")
    return fallback_database_url


def set_alembic_database_url(config: _AlembicConfig, *, database_url: str) -> None:
    """Escape ConfigParser interpolation only at the Alembic write boundary."""
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


__all__ = ["select_migration_database_url", "set_alembic_database_url"]
