"""The migration configuration seam cannot acquire a database runtime."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

MODULE = (
    Path(__file__).resolve().parents[2]
    / "packages/dotmac-kernel/src/dotmac_kernel/migrations/configuration.py"
)


def _imports(source: str) -> set[str]:
    tree = ast.parse(source)
    return {
        alias.name if isinstance(node, ast.Import) else node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }


def test_configuration_imports_only_the_typing_protocol() -> None:
    assert _imports(MODULE.read_text()) <= {"__future__", "typing"}


def test_import_guard_notices_a_planted_settings_dependency() -> None:
    assert not _imports(
        MODULE.read_text() + "\nfrom dotmac_kernel.config import settings\n"
    ) <= {"__future__", "typing"}


_PROBE = """
import sqlalchemy
import sqlalchemy.orm

def forbidden(*args, **kwargs):
    raise AssertionError("migration configuration constructed a database runtime")

sqlalchemy.create_engine = forbidden
sqlalchemy.engine.create_engine = forbidden
sqlalchemy.orm.sessionmaker = forbidden
sqlalchemy.orm.Session.__init__ = forbidden
from dotmac_kernel.migrations.configuration import (
    select_migration_database_url,
    set_alembic_database_url,
)
import sys
assert "dotmac_kernel.db" not in sys.modules
assert "dotmac_kernel.session_runtime" not in sys.modules
assert select_migration_database_url(migration_database_url="held-url") == "held-url"
"""


def _probe(source: str) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "MIGRATION_DATABASE_URL"}
    }
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=30,
    )


def test_configuration_import_constructs_no_engine_or_session() -> None:
    result = _probe(_PROBE)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "violation", ['sqlalchemy.create_engine("sqlite://")', "sqlalchemy.orm.Session()"]
)
def test_runtime_probe_notices_planted_runtime_construction(violation: str) -> None:
    result = _probe(_PROBE + f"\n{violation}\n")
    assert result.returncode != 0
    assert "constructed a database runtime" in result.stderr
