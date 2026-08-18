"""Architecture canaries for the media-observations ownership boundary."""

from __future__ import annotations

import ast
import os
import pathlib
import shutil
import subprocess
import sys
import tomllib
import venv

import dotmac_media_observations
import pytest
from dotmac_kernel.namespaces import (
    MEDIA_OBSERVATIONS_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
    NamespaceRegistry,
    revision_id_pattern,
)
from dotmac_media_observations.manifest import module
from dotmac_media_observations.models import (
    ALL_TABLES,
    APPEND_ONLY_TABLES,
    TENANT_TABLES,
)
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "packages/dotmac-media-observations"
SOURCE = PACKAGE_ROOT / "src/dotmac_media_observations"
MIGRATION = SOURCE / "migrations/versions/mo_0001_media_observations.py"


def _python_sources() -> dict[pathlib.Path, str]:
    return {
        path: path.read_text(encoding="utf-8")
        for path in SOURCE.rglob("*.py")
        if "migrations" not in path.parts
    }


def test_manifest_matches_the_immutable_tenant_only_allocation() -> None:
    assert MEDIA_OBSERVATIONS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.migration_owner() == MEDIA_OBSERVATIONS_MIGRATION_OWNER
    assert MEDIA_OBSERVATIONS_MIGRATION_OWNER.db_schema == "mod_mediaobs"
    assert MEDIA_OBSERVATIONS_MIGRATION_OWNER.prefix == "mo"
    assert module.platform_tables == ()
    assert module.tables == TENANT_TABLES
    registry = NamespaceRegistry.from_manifests([module])
    assert registry.declared_platform_tables("mod_mediaobs") == frozenset()
    assert registry.declared_tables("mod_mediaobs") == frozenset(TENANT_TABLES)


def test_every_table_is_tenant_scoped_with_composite_identity() -> None:
    for table in ALL_TABLES:
        assert "tenant_id" in table.c, table.fullname
        assert not table.c.tenant_id.nullable, table.fullname
        composites = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        assert ("tenant_id", "id") in composites, table.fullname


def test_every_internal_foreign_key_carries_tenant_id() -> None:
    for table in ALL_TABLES:
        for constraint in table.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            targets = {element.column.table.name for element in constraint.elements}
            if targets == {"tenants"}:
                continue
            assert "tenant_id" in {column.name for column in constraint.columns}, (
                table.fullname,
                constraint.name,
            )


def test_migration_forces_rls_and_grants_every_tenant_table() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    for table in TENANT_TABLES:
        assert f"ALTER TABLE mod_mediaobs.{table} ENABLE ROW LEVEL SECURITY;" in sql
        assert f"ALTER TABLE mod_mediaobs.{table} FORCE ROW LEVEL SECURITY;" in sql
        assert f"CREATE POLICY {table}_tenant_isolation" in sql
        assert "tenant_id = public.app_current_tenant_id()" in sql
        assert f"ON mod_mediaobs.{table}" in sql


def test_append_only_tables_have_trigger_and_read_append_grants_only() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    for table in APPEND_ONLY_TABLES:
        assert f"CREATE TRIGGER {table.name}_append_only" in sql
        for role in ("app_user", "platform_api"):
            assert (
                f"GRANT SELECT, INSERT ON mod_mediaobs.{table.name} TO {role};" in sql
            )
        assert (
            "GRANT SELECT, INSERT, UPDATE"
            not in sql.split(f"mod_mediaobs.{table.name}", 1)[0][-80:]
        )


def test_provider_and_product_names_do_not_enter_package_code() -> None:
    forbidden = {
        "meta",
        "facebook",
        "google",
        "tiktok",
        "linkedin",
        "lead",
        "party",
        "customer",
        "subscriber",
        "quote",
        "order",
    }
    for path, source in _python_sources().items():
        tree = ast.parse(source)
        identifiers = {
            node.id.lower() for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        assert not forbidden & identifiers, (path, sorted(forbidden & identifiers))


def test_module_imports_neither_assembly_sibling_modules_nor_provider_sdks() -> None:
    forbidden_roots = {
        "app",
        "dotmac_integration",
        "dotmac_campaigns",
        "dotmac_content",
        "dotmac_publishing",
        "dotmac_files",
        "dotmac_sites",
        "dotmac_web_analytics",
    }
    for path, source in _python_sources().items():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imports = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imports = {(node.module or "").split(".")[0]}
            else:
                continue
            assert not forbidden_roots & imports, (path, imports)


def test_no_raw_payload_or_person_profile_column_exists() -> None:
    forbidden = {
        "raw_payload",
        "payload",
        "email",
        "phone",
        "person_id",
        "party_id",
        "lead_id",
        "customer_id",
        "subscriber_id",
        "audience",
        "revenue",
    }
    for table in ALL_TABLES:
        assert not forbidden & set(table.c.keys()), (
            table.fullname,
            set(table.c.keys()),
        )


def test_node_and_metric_codes_are_columns_not_fixed_enums() -> None:
    sources = _python_sources()
    for path, source in sources.items():
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {getattr(base, "id", "") for base in node.bases}
            if "Enum" in bases or "StrEnum" in bases:
                assert node.name not in {"NodeCode", "MetricCode", "Provider"}, path


def test_revision_and_wheel_surface_are_independently_installable() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    revision = next(
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and getattr(node.targets[0], "id", "") == "revision"
    )
    assert revision_id_pattern("mo").fullmatch(revision)
    assert "down_revision = None" in source
    assert 'branch_labels = ("media_observations",)' in source
    assert "resolve_depends_on(REQUIRES)" in source
    assert "require_prerequisites(op.get_bind(), REQUIRES)" in source

    package = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
    assert package["tool"]["poetry"]["dependencies"]["dotmac-kernel"] == ">=0.1.0a72"
    included = package["tool"]["poetry"]["include"]
    assert any("migrations/**/*" in row["path"] for row in included)


def test_public_surface_is_explicit_and_resolves() -> None:
    assert dotmac_media_observations.__all__
    assert len(dotmac_media_observations.__all__) == len(
        set(dotmac_media_observations.__all__)
    )
    for name in dotmac_media_observations.__all__:
        assert hasattr(dotmac_media_observations, name), name


@pytest.mark.slow
def test_clean_wheel_installs_registers_and_carries_its_lineage(
    tmp_path: pathlib.Path,
) -> None:
    poetry = shutil.which("poetry")
    assert poetry is not None, "the pinned Poetry executable is required"
    kernel_dist = tmp_path / "kernel-dist"
    module_dist = tmp_path / "module-dist"
    for package, output in (
        (ROOT / "packages/dotmac-kernel", kernel_dist),
        (PACKAGE_ROOT, module_dist),
    ):
        result = subprocess.run(  # noqa: S603
            [poetry, "build", "--format", "wheel", "--output", str(output)],
            cwd=package,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    kernel_wheel = next(kernel_dist.glob("*.whl"))
    module_wheel = next(module_dist.glob("*.whl"))
    consumer = tmp_path / "consumer"
    venv.EnvBuilder(with_pip=True).create(consumer)
    bin_dir = consumer / ("Scripts" if sys.platform == "win32" else "bin")
    pip = bin_dir / "pip"
    python = bin_dir / "python"
    installed = subprocess.run(  # noqa: S603
        [str(pip), "install", "--quiet", str(kernel_wheel), str(module_wheel)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert installed.returncode == 0, installed.stderr

    env = {key: value for key, value in os.environ.items() if key != "DATABASE_URL"}
    probe = subprocess.run(  # noqa: S603
        [
            str(python),
            "-I",
            "-c",
            (
                "import pathlib, dotmac_media_observations as media; "
                "from dotmac_kernel.namespaces import NamespaceRegistry; "
                "here=pathlib.Path(media.__file__).resolve(); "
                "assert 'site-packages' in str(here), here; "
                "NamespaceRegistry.from_manifests([media.module]); "
                "versions=media.versions_dir(); "
                "assert (versions/'mo_0001_media_observations.py').is_file(); "
                "print(media.__version__, media.module.db_schema)"
            ),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "0.1.0a1 mod_mediaobs"


def test_dossier_keeps_adoption_paused_and_attribution_outside() -> None:
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text())
    assert dossier["status"] == "audit-complete"
    assert dossier["source_mode"] == "product-first"
    assert dossier["contract_consumers"] == []
    assert dossier["candidate_consumers"] == ["dotmac_backoffice", "dotmac_sub"]
    assert "PAUSED" in dossier["first_cutover"]
    assert "official attribution" in dossier["contract"]


def test_attribution_boundary_detector_fires_on_a_planted_violation() -> None:
    def violations(source: str) -> set[str]:
        tree = ast.parse(source)
        names = {
            node.id.lower() for node in ast.walk(tree) if isinstance(node, ast.Name)
        }
        return names & {"lead_id", "customer_id", "authoritative_revenue"}

    assert violations("def write(lead_id):\n    return lead_id\n") == {"lead_id"}
    assert not violations("def emit(observation_id):\n    return observation_id\n")
