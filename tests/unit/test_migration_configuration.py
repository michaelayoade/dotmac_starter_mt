"""Migration material selection and the reference Alembic adapter contract."""

from __future__ import annotations

import runpy
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from dotmac_kernel.migrations.configuration import (
    select_migration_database_url,
    set_alembic_database_url,
)

from alembic.config import Config

OWNER_URL = "postgresql://owner:encoded%25%2F@localhost/migrations"
FALLBACK_URL = "postgresql://runtime@localhost/application"


def test_present_owner_wins_without_rewriting_held_material() -> None:
    owner = f" {OWNER_URL} "
    assert (
        select_migration_database_url(
            migration_database_url=owner, fallback_database_url=FALLBACK_URL
        )
        == owner
    )
    assert select_migration_database_url(migration_database_url=owner) == owner


def test_only_absent_owner_can_use_explicit_fallback() -> None:
    assert (
        select_migration_database_url(
            migration_database_url=None, fallback_database_url=FALLBACK_URL
        )
        == FALLBACK_URL
    )


@pytest.mark.parametrize("owner", ["", " ", "\t\n"])
def test_supplied_blank_owner_refuses_even_with_a_fallback(owner: str) -> None:
    with pytest.raises(ValueError) as caught:
        select_migration_database_url(
            migration_database_url=owner, fallback_database_url=FALLBACK_URL
        )
    assert str(caught.value) == "MIGRATION_DATABASE_URL must not be blank when supplied"
    assert FALLBACK_URL not in str(caught.value)


@pytest.mark.parametrize("fallback", [None, "", " \t"])
def test_absent_owner_requires_nonblank_explicit_fallback(fallback: str | None) -> None:
    with pytest.raises(ValueError, match="A migration database URL is required"):
        select_migration_database_url(
            migration_database_url=None, fallback_database_url=fallback
        )


def test_real_alembic_config_round_trips_percent_encoded_material() -> None:
    config = Config()
    set_alembic_database_url(config, database_url=OWNER_URL)
    assert config.get_main_option("sqlalchemy.url") == OWNER_URL
    section = config.get_section(config.config_ini_section)
    assert section is not None
    assert section["sqlalchemy.url"] == OWNER_URL
    set_alembic_database_url(config, database_url=OWNER_URL)
    assert config.get_main_option("sqlalchemy.url") == OWNER_URL


def _run_reference_adapter(
    monkeypatch, *, offline: bool, engine_factory: MagicMock | None = None
) -> SimpleNamespace:
    """Execute the real adapter, substituting only its assembly/DB boundaries."""
    from alembic import context

    config = Config()
    configure = Mock()
    run_migrations = Mock()
    if engine_factory is None:
        engine_factory = MagicMock()
    connection = object()
    engine_factory.return_value.connect.return_value.__enter__.return_value = connection
    monkeypatch.setattr(context, "config", config, raising=False)
    monkeypatch.setattr(context, "configure", configure)
    monkeypatch.setattr(context, "is_offline_mode", lambda: offline)
    monkeypatch.setattr(context, "begin_transaction", nullcontext)
    monkeypatch.setattr(context, "run_migrations", run_migrations)
    monkeypatch.setattr("sqlalchemy.engine_from_config", engine_factory)

    # Model registration and bindings are product concerns. Retain their call
    # order and values without importing an assembly database runtime here.
    imports = {
        "dotmac_kernel.audit": {},
        "dotmac_kernel.entitlements": {},
        "dotmac_kernel.idempotency_models": {},
        "dotmac_kernel.models_platform": {},
        "dotmac_kernel.settings_models": {},
        "dotmac_kernel.messaging.models": {},
        "dotmac_kernel.models": {"Base": SimpleNamespace(metadata=object())},
        "app.assembly": {"assembly": SimpleNamespace(module_planes=())},
        "app.features.custom_fields.models": {},
        "app.migration_bindings": {"ASSEMBLY_PREREQUISITE_BINDINGS": ()},
    }
    for name, attrs in imports.items():
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
        parent_name, _, attr = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is None:
            parent = ModuleType(parent_name)
            monkeypatch.setitem(sys.modules, parent_name, parent)
        monkeypatch.setattr(parent, attr, module, raising=False)

    bindings = Mock()
    planes = Mock()
    monkeypatch.setattr(
        "dotmac_kernel.prerequisites.install_prerequisite_bindings", bindings
    )
    monkeypatch.setattr("dotmac_kernel.planes.install_module_plane_selections", planes)
    runpy.run_path(str(Path(__file__).resolve().parents[2] / "alembic/env.py"))
    bindings.assert_called_once_with(())
    planes.assert_called_once_with(())
    return SimpleNamespace(
        config=config,
        configure=configure,
        run_migrations=run_migrations,
        engine_factory=engine_factory,
        connection=connection,
    )


@pytest.mark.parametrize("offline", [True, False])
@pytest.mark.parametrize("owner", [OWNER_URL, None])
def test_reference_adapter_uses_selected_material_on_both_paths(
    monkeypatch, offline: bool, owner: str | None
) -> None:
    if owner is None:
        monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("MIGRATION_DATABASE_URL", owner)
    monkeypatch.setenv("DATABASE_URL", FALLBACK_URL)
    result = _run_reference_adapter(monkeypatch, offline=offline)
    expected = owner if owner is not None else FALLBACK_URL
    assert result.config.get_main_option("sqlalchemy.url") == expected
    result.run_migrations.assert_called_once_with()
    if offline:
        assert result.configure.call_args.kwargs["url"] == expected
        result.engine_factory.assert_not_called()
    else:
        assert result.engine_factory.call_args.args[0]["sqlalchemy.url"] == expected
        assert result.configure.call_args.kwargs["connection"] is result.connection


@pytest.mark.parametrize("offline", [True, False])
def test_reference_adapter_refuses_blank_owner_before_engine_creation(
    monkeypatch, offline: bool
) -> None:
    monkeypatch.setenv("MIGRATION_DATABASE_URL", " \t")
    monkeypatch.setenv("DATABASE_URL", FALLBACK_URL)
    engine_factory = MagicMock()
    with pytest.raises(ValueError, match="must not be blank"):
        _run_reference_adapter(
            monkeypatch, offline=offline, engine_factory=engine_factory
        )
    engine_factory.assert_not_called()


@pytest.mark.parametrize("offline", [True, False])
def test_reference_adapter_refuses_missing_material_before_engine_creation(
    monkeypatch, offline: bool
) -> None:
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    engine_factory = MagicMock()
    with pytest.raises(ValueError, match="A migration database URL is required"):
        _run_reference_adapter(
            monkeypatch, offline=offline, engine_factory=engine_factory
        )
    engine_factory.assert_not_called()
