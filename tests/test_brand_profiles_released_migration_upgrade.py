"""Populated released bp_0001 upgrades additively for every explicit plane set."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid

import pytest
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from sqlalchemy import create_engine, text

from tests.test_brand_profiles_dual_plane_isolation import (
    ASSEMBLY_VERSIONS,
    BRAND_VERSIONS,
    KERNEL_VERSIONS,
    REPO_ROOT,
    _url_for,
)


@pytest.mark.parametrize(
    "planes",
    [
        (ModulePlane.TENANT,),
        (ModulePlane.PLATFORM,),
        (ModulePlane.TENANT, ModulePlane.PLATFORM),
    ],
)
def test_populated_released_brand_profiles_upgrade(planes, tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    git = shutil.which("git")
    assert git is not None
    path = (
        "packages/dotmac-brand-profiles/src/dotmac_brand_profiles/"
        "migrations/versions/bp_0001_brand_profiles.py"
    )
    released = subprocess.run(  # noqa: S603
        [git, "show", f"dotmac-brand-profiles-v0.1.0a1:{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout
    assert hashlib.sha256(released).hexdigest() == (
        "b6d569955954d6610d06300c0e60a18c02eeaa06cf0758c7f1a85dc8faeb8961"
    )
    assert (
        hashlib.sha256(released).digest()
        == hashlib.sha256(
            (BRAND_VERSIONS / "bp_0001_brand_profiles.py").read_bytes()
        ).digest()
    )
    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "bp_0001_brand_profiles.py").write_bytes(released)
    superuser = os.environ.get("TEST_MIGRATION_DATABASE_URL")
    assert (
        superuser
    ), "TEST_MIGRATION_DATABASE_URL is required for released upgrade proof"
    name = f"brand_upgrade_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    try:
        with setup.connect() as conn:
            conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
            conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        admin_url = _url_for(superuser, name, user="app_admin")
        monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations", f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {versions}"
        )
        cfg.attributes["module_plane_selections"] = (
            ModulePlaneSelection(module="brand_profiles", planes=planes),
        )
        command.upgrade(cfg, "heads")
        tables = []
        if ModulePlane.TENANT in planes:
            tables.append("brand_profiles")
        if ModulePlane.PLATFORM in planes:
            tables.append("platform_brand_profiles")
        before = {}
        with setup.begin() as conn:
            for table in tables:
                extra_names = (
                    ", tenant_id, scope_type" if table == "brand_profiles" else ""
                )
                extra_values = (
                    ", :tenant, 'tenant'" if table == "brand_profiles" else ""
                )
                conn.execute(
                    text(
                        f"INSERT INTO mod_brand.{table} "  # noqa: S608 # fixed table list
                        "(id, profile_code, display_name, product_name, legal_name, "
                        "primary_hex, locked_fields, status, "
                        f"record_version{extra_names}) VALUES "
                        "(:id, 'existing', 'Existing', 'Product', 'Legal', "
                        f"'#123456', '[\"legal_name\"]', 'active', 7{extra_values})"
                    ),
                    {"id": uuid.uuid4(), "tenant": uuid.uuid4()},
                )
                before[table] = dict(
                    conn.execute(text(f"SELECT * FROM mod_brand.{table}"))  # noqa: S608
                    .mappings()
                    .one()
                )
        shutil.copyfile(
            BRAND_VERSIONS / "bp_0002_tagline.py", versions / "bp_0002_tagline.py"
        )
        command.upgrade(cfg, "heads")
        with setup.connect() as conn:
            for table in ("brand_profiles", "platform_brand_profiles"):
                if table not in tables:
                    assert (
                        conn.execute(
                            text("SELECT to_regclass(:t)"), {"t": f"mod_brand.{table}"}
                        ).scalar()
                        is None
                    )
                    continue
                after = dict(
                    conn.execute(text(f"SELECT * FROM mod_brand.{table}"))  # noqa: S608
                    .mappings()
                    .one()
                )
                assert after.pop("tagline") is None
                assert after == before[table]
                assert conn.execute(
                    text(
                        "SELECT is_nullable, character_maximum_length "
                        "FROM information_schema.columns "
                        "WHERE table_schema = 'mod_brand' "
                        "AND table_name = :t AND column_name = 'tagline'"
                    ),
                    {"t": table},
                ).one() == ("YES", 255)
    finally:
        setup.dispose()
        with server.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            conn.execute(text(f'DROP DATABASE "{name}"'))
        server.dispose()
