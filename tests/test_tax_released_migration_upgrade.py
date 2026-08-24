"""Published ``tx_0001`` evidence upgrades additively to multi-tax a2."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
CURRENT_TAX = REPO_ROOT / "packages/dotmac-tax/src/dotmac_tax/migrations/versions"
MIGRATION_PATH = "packages/dotmac-tax/src/dotmac_tax/migrations/versions/tx_0001_tax.py"
TAG = "dotmac-tax-v0.1.0a1"
DIGEST = "bf3091556eb5eac401e64cfe342a2d59c17b7d511c0c772aef034340b07012ab"
GIT = shutil.which("git")
assert GIT is not None, "git is required to reconstruct released tax bytes"


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — tax upgrade proof needs PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def database() -> Iterator[tuple[str, str, str]]:
    superuser = _superuser_url()
    name = f"tax_upgrade_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as connection:
        connection.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        connection.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()
    try:
        yield (
            _url_for(superuser, name),
            _url_for(superuser, name, user="app_admin"),
            _url_for(superuser, name, user="app_user"),
        )
    finally:
        with server.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _released_source() -> bytes:
    result = subprocess.run(  # noqa: S603 # nosec B603 B607
        [GIT, "show", f"{TAG}:{MIGRATION_PATH}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert hashlib.sha256(result.stdout).hexdigest() == DIGEST
    return result.stdout


def _config(versions: Path):
    from alembic.config import Config

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    config.set_main_option("version_locations", f"{KERNEL_VERSIONS} {versions}")
    return config


def _seed_a1_evidence(
    admin_url: str,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    tenant_id = uuid.uuid4()
    authority_id = uuid.uuid4()
    jurisdiction_id = uuid.uuid4()
    code_id = uuid.uuid4()
    rule_id = uuid.uuid4()
    determination_id = uuid.uuid4()
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO public.tenants (id, slug, name) "
                "VALUES (:id, :slug, 'Tax upgrade proof')"
            ),
            {"id": tenant_id, "slug": f"tax-proof-{tenant_id.hex[:8]}"},
        )
        connection.execute(
            text(
                "INSERT INTO mod_tax.tax_authorities "
                "(id, tenant_id, code, name, status) VALUES "
                "(:id, :tenant, 'AUTH', 'Authority', 'active')"
            ),
            {"id": authority_id, "tenant": tenant_id},
        )
        connection.execute(
            text(
                "INSERT INTO mod_tax.tax_jurisdictions "
                "(id, tenant_id, authority_id, code, name, country_code, "
                "currency_code, minor_units, status) VALUES "
                "(:id, :tenant, :authority, 'NG', 'Nigeria', 'NG', 'NGN', 2, "
                "'active')"
            ),
            {
                "id": jurisdiction_id,
                "tenant": tenant_id,
                "authority": authority_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO mod_tax.tax_codes "
                "(id, tenant_id, jurisdiction_id, code, name, tax_kind_code, "
                "status) VALUES (:id, :tenant, :jurisdiction, 'VAT', 'VAT', "
                "'value-added', 'active')"
            ),
            {"id": code_id, "tenant": tenant_id, "jurisdiction": jurisdiction_id},
        )
        connection.execute(
            text(
                "INSERT INTO mod_tax.tax_rules "
                "(id, tenant_id, tax_code_id, version, effective_from, priority, "
                "fact_kind, recognition_basis_code, transaction_side, "
                "calculation_method, rate, inclusive, recoverable_rate, "
                "published_at) VALUES (:id, :tenant, :code, 1, '2026-01-01', "
                "10, 'cash-receipt', 'cash-received', 'output', 'percentage', "
                "0.075, false, 0, now())"
            ),
            {"id": rule_id, "tenant": tenant_id, "code": code_id},
        )
        connection.execute(
            text(
                "INSERT INTO mod_tax.tax_determinations "
                "(id, tenant_id, jurisdiction_id, tax_code_id, rule_id, "
                "rule_version, occurred_on, fact_kind, recognition_basis_code, "
                "transaction_side, base_amount, tax_amount, recoverable_amount, "
                "non_recoverable_amount, currency_code, minor_units, source_ref, "
                "source_version, source_fingerprint, evidence_ref, determined_at) "
                "VALUES (:id, :tenant, :jurisdiction, :code, :rule, 1, "
                "'2026-07-10', 'cash-receipt', 'cash-received', 'output', 1000, "
                "75, 0, 75, 'NGN', 2, 'receipt:legacy', '1', :fingerprint, "
                "'settlement:legacy', now())"
            ),
            {
                "id": determination_id,
                "tenant": tenant_id,
                "jurisdiction": jurisdiction_id,
                "code": code_id,
                "rule": rule_id,
                "fingerprint": "a" * 64,
            },
        )
    engine.dispose()
    return tenant_id, jurisdiction_id, code_id, rule_id, determination_id


def test_published_a1_rows_upgrade_without_rewrite(
    database: tuple[str, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.migration_bindings as assembly_bindings
    from alembic import command

    _, admin_url, app_url = database
    historical = tmp_path / "historical_tax"
    historical.mkdir()
    (historical / "tx_0001_tax.py").write_bytes(_released_source())
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    monkeypatch.setattr(
        assembly_bindings,
        "ASSEMBLY_PREREQUISITE_BINDINGS",
        tuple(assembly_bindings.ASSEMBLY_PREREQUISITE_BINDINGS),
    )

    command.upgrade(_config(historical), "heads")
    tenant_id, jurisdiction_id, code_id, rule_id, determination_id = _seed_a1_evidence(
        admin_url
    )
    command.upgrade(_config(CURRENT_TAX), "heads")

    engine = create_engine(admin_url)
    with engine.connect() as connection:
        rule = connection.execute(
            text(
                "SELECT treatment_code, calculation_sequence, "
                "calculation_base_code FROM mod_tax.tax_rules WHERE id = :id"
            ),
            {"id": rule_id},
        ).one()
        legacy = connection.execute(
            text(
                "SELECT base_amount, tax_amount, determination_set_id, "
                "treatment_code FROM mod_tax.tax_determinations WHERE id = :id"
            ),
            {"id": determination_id},
        ).one()
        new_tables = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'mod_tax' AND table_name IN "
                    "('tax_subject_classifications','tax_determination_sets')"
                )
            )
        }
    assert tuple(rule) == ("standard_rated", 100, "source_amount")
    assert legacy.base_amount == 1000
    assert legacy.tax_amount == 75
    assert legacy.determination_set_id is None
    assert legacy.treatment_code is None
    assert new_tables == {"tax_subject_classifications", "tax_determination_sets"}

    classification_id = uuid.uuid4()
    determination_set_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO mod_tax.tax_subject_classifications "
                "(id, tenant_id, tax_code_id, subject_kind, subject_ref, "
                "category_code, version, effective_from, basis_code, "
                "evidence_ref, published_by_ref, source_ref, source_version, "
                "source_fingerprint, published_at) VALUES "
                "(:id, :tenant, :code, 'party', 'party:proof', 'registered', 1, "
                "'2026-01-01', 'registration', 'evidence:proof', 'user:proof', "
                "'classification:proof', '1', :fingerprint, now())"
            ),
            {
                "id": classification_id,
                "tenant": tenant_id,
                "code": code_id,
                "fingerprint": "b" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO mod_tax.tax_determination_sets "
                "(id, tenant_id, jurisdiction_id, occurred_on, fact_kind, "
                "recognition_basis_code, transaction_side, source_amount, "
                "net_amount, tax_amount, gross_amount, currency_code, minor_units, "
                "source_ref, source_version, source_fingerprint, evidence_ref, "
                "determined_at) VALUES (:id, :tenant, :jurisdiction, "
                "'2026-07-11', 'cash-receipt', 'cash-received', 'output', 1000, "
                "1000, 75, 1075, 'NGN', 2, 'receipt:set-proof', '1', "
                ":fingerprint, 'settlement:set-proof', now())"
            ),
            {
                "id": determination_set_id,
                "tenant": tenant_id,
                "jurisdiction": jurisdiction_id,
                "fingerprint": "c" * 64,
            },
        )

    with engine.begin() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(
            text(
                "UPDATE mod_tax.tax_subject_classifications "
                "SET category_code = 'changed' WHERE id = :id"
            ),
            {"id": classification_id},
        )
    with engine.begin() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(
            text(
                "UPDATE mod_tax.tax_determination_sets "
                "SET tax_amount = 76 WHERE id = :id"
            ),
            {"id": determination_set_id},
        )

    other_tenant_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO public.tenants (id, slug, name) "
                "VALUES (:id, :slug, 'Other tenant')"
            ),
            {
                "id": other_tenant_id,
                "slug": f"tax-proof-{other_tenant_id.hex[:8]}",
            },
        )
    app_engine = create_engine(app_url)
    with app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(other_tenant_id)},
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM mod_tax.tax_subject_classifications")
            )
            == 0
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM mod_tax.tax_determination_sets")
            )
            == 0
        )
    with (
        app_engine.begin() as connection,
        pytest.raises(DBAPIError, match="row-level security"),
    ):
        connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(other_tenant_id)},
        )
        connection.execute(
            text(
                "INSERT INTO mod_tax.tax_subject_classifications "
                "(id, tenant_id, tax_code_id, subject_kind, subject_ref, "
                "category_code, version, effective_from, basis_code, "
                "evidence_ref, published_by_ref, source_ref, source_version, "
                "source_fingerprint, published_at) VALUES "
                "(:id, :tenant, :code, 'party', 'party:cross-tenant', "
                "'registered', 1, '2026-01-01', 'registration', "
                "'evidence:cross-tenant', 'user:proof', "
                "'classification:cross-tenant', '1', :fingerprint, now())"
            ),
            {
                "id": uuid.uuid4(),
                "tenant": tenant_id,
                "code": code_id,
                "fingerprint": "d" * 64,
            },
        )
    app_engine.dispose()
    engine.dispose()
