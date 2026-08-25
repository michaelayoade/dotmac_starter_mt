"""Published tax evidence upgrades additively through the a3 result seal."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from dotmac_kernel.money import Currency, Money
from dotmac_tax.contracts import StatutoryReportBoxInput, TaxFact
from dotmac_tax.service import (
    TaxConflict,
    _fact_fingerprint,
    create_filing_obligation,
    create_statutory_report_definition,
    determine_tax_set,
    generate_statutory_report,
)
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
CURRENT_TAX = REPO_ROOT / "packages/dotmac-tax/src/dotmac_tax/migrations/versions"
TX_0001_PATH = "packages/dotmac-tax/src/dotmac_tax/migrations/versions/tx_0001_tax.py"
TX_0001_DIGEST = "bf3091556eb5eac401e64cfe342a2d59c17b7d511c0c772aef034340b07012ab"
TX_0002_PATH = (
    "packages/dotmac-tax/src/dotmac_tax/migrations/versions/tx_0002_multi_tax.py"
)
TX_0002_DIGEST = "9b78094519fe8d0785735f3a4e3a37dacdb9901b88eda25c90cdb167474abde0"
_git = shutil.which("git")
assert _git is not None, "git is required to reconstruct released tax bytes"
GIT: str = _git


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


def _released_source(*, tag: str, path: str, digest: str) -> bytes:
    result = subprocess.run(  # noqa: S603 # nosec B603 B607
        [GIT, "show", f"{tag}:{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert hashlib.sha256(result.stdout).hexdigest() == digest
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


def _seed_a2_determination_set(admin_url: str) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    authority_id = uuid.uuid4()
    jurisdiction_id = uuid.uuid4()
    code_id = uuid.uuid4()
    rule_id = uuid.uuid4()
    determination_set_id = uuid.uuid4()
    determination_id = uuid.uuid4()
    line_id = uuid.uuid4()
    source_fingerprint = _fact_fingerprint(
        TaxFact(
            jurisdiction_id=jurisdiction_id,
            occurred_on=date(2026, 7, 12),
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            base_amount=Money.of("1000", Currency("NGN", 2)),
            source_ref="receipt:a2",
            source_version="1",
            evidence_ref="settlement:a2",
        ),
        source_ref="receipt:a2",
        source_version="1",
    )
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO public.tenants (id, slug, name) "
                "VALUES (:id, :slug, 'Tax a2 seal proof')"
            ),
            {"id": tenant_id, "slug": f"tax-a2-{tenant_id.hex[:8]}"},
        )
        connection.execute(
            text(
                "INSERT INTO mod_tax.tax_authorities "
                "(id, tenant_id, code, name, status) VALUES "
                "(:id, :tenant, 'AUTH-A2', 'Authority', 'active')"
            ),
            {"id": authority_id, "tenant": tenant_id},
        )
        connection.execute(
            text(
                "INSERT INTO mod_tax.tax_jurisdictions "
                "(id, tenant_id, authority_id, code, name, country_code, "
                "currency_code, minor_units, status) VALUES "
                "(:id, :tenant, :authority, 'NG-A2', 'Nigeria', 'NG', 'NGN', "
                "2, 'active')"
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
                "status) VALUES (:id, :tenant, :jurisdiction, 'VAT-A2', 'VAT', "
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
                "treatment_code, calculation_sequence, calculation_base_code, "
                "published_at) VALUES (:id, :tenant, :code, 1, '2026-01-01', "
                "10, 'cash-receipt', 'cash-received', 'output', 'percentage', "
                "0.075, false, 0, 'standard_rated', 10, 'source_amount', now())"
            ),
            {"id": rule_id, "tenant": tenant_id, "code": code_id},
        )
        connection.execute(
            text(
                "INSERT INTO mod_tax.tax_determination_sets "
                "(id, tenant_id, jurisdiction_id, occurred_on, fact_kind, "
                "recognition_basis_code, transaction_side, source_amount, "
                "net_amount, tax_amount, gross_amount, currency_code, minor_units, "
                "source_ref, source_version, source_fingerprint, evidence_ref, "
                "determined_at) VALUES (:id, :tenant, :jurisdiction, "
                "'2026-07-12', 'cash-receipt', 'cash-received', 'output', 1000, "
                "1000, 75, 1075, 'NGN', 2, 'receipt:a2', '1', :fingerprint, "
                "'settlement:a2', '2026-07-12 12:00:00+00')"
            ),
            {
                "id": determination_set_id,
                "tenant": tenant_id,
                "jurisdiction": jurisdiction_id,
                "fingerprint": source_fingerprint,
            },
        )
        connection.execute(
            text(
                "INSERT INTO mod_tax.tax_determinations "
                "(id, tenant_id, determination_set_id, component_sequence, "
                "jurisdiction_id, tax_code_id, rule_id, rule_version, occurred_on, "
                "fact_kind, recognition_basis_code, transaction_side, "
                "treatment_code, calculation_base_code, inclusive, base_amount, "
                "tax_amount, recoverable_amount, non_recoverable_amount, "
                "currency_code, minor_units, source_ref, source_version, "
                "source_fingerprint, evidence_ref, determined_at) VALUES "
                "(:id, :tenant, :set_id, 10, :jurisdiction, :code, :rule, 1, "
                "'2026-07-12', 'cash-receipt', 'cash-received', 'output', "
                "'standard_rated', 'source_amount', false, 1000, 75, 0, 75, "
                "'NGN', 2, 'receipt:a2', '1', :fingerprint, 'settlement:a2', "
                "'2026-07-12 12:00:00+00')"
            ),
            {
                "id": determination_id,
                "tenant": tenant_id,
                "set_id": determination_set_id,
                "jurisdiction": jurisdiction_id,
                "code": code_id,
                "rule": rule_id,
                "fingerprint": source_fingerprint,
            },
        )
        connection.execute(
            text(
                "INSERT INTO mod_tax.tax_determination_lines "
                "(id, tenant_id, determination_id, sequence, taxable_amount, "
                "rate, tax_amount) VALUES (:id, :tenant, :determination, 1, "
                "1000, 0.075, 75)"
            ),
            {
                "id": line_id,
                "tenant": tenant_id,
                "determination": determination_id,
            },
        )
    engine.dispose()
    return determination_set_id


def test_published_a1_rows_upgrade_without_rewrite(
    database: tuple[str, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.migration_bindings as assembly_bindings
    from alembic import command

    _, admin_url, app_url = database
    historical = tmp_path / "historical_tax"
    historical.mkdir()
    (historical / "tx_0001_tax.py").write_bytes(
        _released_source(
            tag="dotmac-tax-v0.1.0a1",
            path=TX_0001_PATH,
            digest=TX_0001_DIGEST,
        )
    )
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
                "source_ref, source_version, source_fingerprint, "
                "result_seal_state, result_fingerprint, evidence_ref, "
                "determined_at) VALUES (:id, :tenant, :jurisdiction, "
                "'2026-07-11', 'cash-receipt', 'cash-received', 'output', 1000, "
                "1000, 75, 1075, 'NGN', 2, 'receipt:set-proof', '1', "
                ":fingerprint, 'building', NULL, 'settlement:set-proof', now())"
            ),
            {
                "id": determination_set_id,
                "tenant": tenant_id,
                "jurisdiction": jurisdiction_id,
                "fingerprint": "c" * 64,
            },
        )
        connection.execute(
            text(
                "UPDATE mod_tax.tax_determination_sets SET "
                "result_seal_state = 'sealed', "
                "result_fingerprint = :result_fingerprint WHERE id = :id"
            ),
            {
                "id": determination_set_id,
                "result_fingerprint": f"rv1:{'d' * 64}",
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


def test_published_a2_sets_remain_unsealed_and_new_rows_require_the_rv1_seal(
    database: tuple[str, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.migration_bindings as assembly_bindings
    from alembic import command

    _, admin_url, app_url = database
    historical = tmp_path / "historical_tax_a2"
    historical.mkdir()
    for filename, path, digest in (
        ("tx_0001_tax.py", TX_0001_PATH, TX_0001_DIGEST),
        ("tx_0002_multi_tax.py", TX_0002_PATH, TX_0002_DIGEST),
    ):
        (historical / filename).write_bytes(
            _released_source(
                tag="dotmac-tax-v0.1.0a2",
                path=path,
                digest=digest,
            )
        )
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    monkeypatch.setattr(
        assembly_bindings,
        "ASSEMBLY_PREREQUISITE_BINDINGS",
        tuple(assembly_bindings.ASSEMBLY_PREREQUISITE_BINDINGS),
    )

    command.upgrade(_config(historical), "heads")
    determination_set_id = _seed_a2_determination_set(admin_url)
    command.upgrade(_config(CURRENT_TAX), "heads")

    engine = create_engine(admin_url)
    with engine.connect() as connection:
        legacy = connection.execute(
            text(
                "SELECT s.tenant_id, s.jurisdiction_id, s.result_seal_state, "
                "s.result_fingerprint, d.tax_code_id "
                "FROM mod_tax.tax_determination_sets s "
                "JOIN mod_tax.tax_determinations d "
                "ON d.tenant_id = s.tenant_id "
                "AND d.determination_set_id = s.id WHERE s.id = :id"
            ),
            {"id": determination_set_id},
        ).one()
        validated = connection.scalar(
            text(
                "SELECT convalidated FROM pg_constraint "
                "WHERE conname = "
                "'ck_tax_determination_sets_result_seal'"
            )
        )
        column_grants = connection.execute(
            text(
                "SELECT "
                "has_column_privilege('app_user', "
                "'mod_tax.tax_determination_sets', "
                "'result_seal_state', 'UPDATE'), "
                "has_column_privilege('app_user', "
                "'mod_tax.tax_determination_sets', "
                "'result_fingerprint', 'UPDATE'), "
                "has_table_privilege('app_user', "
                "'mod_tax.tax_determination_sets', 'UPDATE')"
            )
        ).one()
    assert legacy.result_seal_state is None
    assert legacy.result_fingerprint is None
    assert validated is False
    assert tuple(column_grants) == (True, True, False)

    with engine.begin() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(
            text(
                "UPDATE mod_tax.tax_determination_sets "
                "SET result_fingerprint = :fingerprint WHERE id = :id"
            ),
            {
                "id": determination_set_id,
                "fingerprint": f"rv1:{'c' * 64}",
            },
        )

    with pytest.raises(DBAPIError, match="must be sealed before commit"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO mod_tax.tax_determination_sets "
                    "(id, tenant_id, jurisdiction_id, occurred_on, fact_kind, "
                    "recognition_basis_code, transaction_side, source_amount, "
                    "net_amount, tax_amount, gross_amount, currency_code, "
                    "minor_units, source_ref, source_version, source_fingerprint, "
                    "result_seal_state, evidence_ref, determined_at) VALUES "
                    "(:id, :tenant, :jurisdiction, '2026-07-13', "
                    "'cash-receipt', 'cash-received', 'output', 1000, 1000, 75, "
                    "1075, 'NGN', 2, 'receipt:unsealed-new', '1', :fingerprint, "
                    "'building', 'settlement:unsealed-new', now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": legacy.tenant_id,
                    "jurisdiction": legacy.jurisdiction_id,
                    "fingerprint": "b" * 64,
                },
            )

    a2_fact = TaxFact(
        jurisdiction_id=legacy.jurisdiction_id,
        occurred_on=date(2026, 7, 12),
        fact_kind="cash-receipt",
        recognition_basis_code="cash-received",
        transaction_side="output",
        base_amount=Money.of("1000", Currency("NGN", 2)),
        source_ref="receipt:a2",
        source_version="1",
        evidence_ref="settlement:a2",
    )
    app_engine = create_engine(app_url)
    with Session(app_engine) as session:
        session.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(legacy.tenant_id)},
        )
        with pytest.raises(TaxConflict, match="predates the rv1 result-content seal"):
            determine_tax_set(
                session,
                tenant_id=legacy.tenant_id,
                fact=a2_fact,
                determined_at=datetime(2026, 7, 12, 12, tzinfo=UTC),
            )

    new_fact = TaxFact(
        jurisdiction_id=legacy.jurisdiction_id,
        occurred_on=date(2026, 8, 1),
        fact_kind="cash-receipt",
        recognition_basis_code="cash-received",
        transaction_side="output",
        base_amount=Money.of("1000", Currency("NGN", 2)),
        source_ref="receipt:a3-postgres-roundtrip",
        source_version="1",
        evidence_ref="settlement:a3-postgres-roundtrip",
    )
    first_determined_at = datetime(2026, 8, 1, 12, tzinfo=UTC)
    with Session(app_engine) as session:
        session.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(legacy.tenant_id)},
        )
        original = determine_tax_set(
            session,
            tenant_id=legacy.tenant_id,
            fact=new_fact,
            determined_at=first_determined_at,
        )
        definition = create_statutory_report_definition(
            session,
            tenant_id=legacy.tenant_id,
            jurisdiction_id=legacy.jurisdiction_id,
            code="A3-SEAL-PROOF",
            name="A3 seal proof",
            currency=Currency("NGN", 2),
            payable_box_code="PAYABLE",
            boxes=(
                StatutoryReportBoxInput(
                    box_code="PAYABLE",
                    label="Tax payable",
                    sequence=1,
                    tax_code_id=legacy.tax_code_id,
                    value_source="tax_amount",
                    multiplier=Decimal("1"),
                ),
            ),
        )
        obligation = create_filing_obligation(
            session,
            tenant_id=legacy.tenant_id,
            definition_id=definition.id,
            obligation_ref="A3-SEAL-PROOF:2026-08-01",
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 1),
            due_on=date(2026, 8, 31),
            taxpayer_ref="taxpayer:a3-seal-proof",
        )
        first_report = generate_statutory_report(
            session,
            tenant_id=legacy.tenant_id,
            obligation_id=obligation.id,
            generated_by_id=uuid.uuid4(),
            generated_at=first_determined_at,
        )
        obligation_id = obligation.id
        assert first_report.total_payable == Decimal("75.000000")
        session.commit()

    # This is a new database session, not an identity-map replay. PostgreSQL
    # must preserve the exact aware instant, content and rv1 digest.
    with Session(app_engine) as session:
        session.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(legacy.tenant_id)},
        )
        replay = determine_tax_set(
            session,
            tenant_id=legacy.tenant_id,
            fact=new_fact,
            determined_at=first_determined_at + timedelta(days=1),
        )
        assert replay == original

    # app_user owns the normal service path and has only the two-column seal
    # UPDATE grant. Once sealed, it cannot append a component that a report
    # might otherwise aggregate.
    with pytest.raises(DBAPIError, match="result membership is sealed"):
        with app_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(legacy.tenant_id)},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_tax.tax_determinations ("
                    "id, tenant_id, determination_set_id, component_sequence, "
                    "jurisdiction_id, tax_code_id, rule_id, rule_version, "
                    "occurred_on, fact_kind, recognition_basis_code, "
                    "transaction_side, treatment_code, calculation_base_code, "
                    "inclusive, party_category, supply_category, place_code, "
                    "party_classification_id, supply_classification_id, "
                    "place_classification_id, base_amount, tax_amount, "
                    "recoverable_amount, non_recoverable_amount, currency_code, "
                    "minor_units, source_ref, source_version, source_fingerprint, "
                    "evidence_ref, counterparty_ref, determined_at) SELECT "
                    ":id, tenant_id, determination_set_id, component_sequence + 1, "
                    "jurisdiction_id, tax_code_id, rule_id, rule_version, "
                    "occurred_on, fact_kind, recognition_basis_code, "
                    "transaction_side, treatment_code, calculation_base_code, "
                    "inclusive, party_category, supply_category, place_code, "
                    "party_classification_id, supply_classification_id, "
                    "place_classification_id, base_amount, tax_amount, "
                    "recoverable_amount, non_recoverable_amount, currency_code, "
                    "minor_units, source_ref, source_version, source_fingerprint, "
                    "evidence_ref, counterparty_ref, determined_at "
                    "FROM mod_tax.tax_determinations "
                    "WHERE determination_set_id = :set_id LIMIT 1"
                ),
                {"id": uuid.uuid4(), "set_id": original.determination_set_id},
            )

    # Exercise the trigger's line-membership branch independently. A line
    # appended beneath an existing sealed component changes the rv1 result
    # just as surely as a new component would.
    with pytest.raises(DBAPIError, match="result membership is sealed"):
        with app_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(legacy.tenant_id)},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_tax.tax_determination_lines ("
                    "id, tenant_id, determination_id, sequence, taxable_amount, "
                    "rate, tax_amount) SELECT :id, line.tenant_id, "
                    "line.determination_id, line.sequence + 1, "
                    "line.taxable_amount, line.rate, line.tax_amount "
                    "FROM mod_tax.tax_determination_lines line "
                    "JOIN mod_tax.tax_determinations component "
                    "ON component.tenant_id = line.tenant_id "
                    "AND component.id = line.determination_id "
                    "WHERE component.determination_set_id = :set_id LIMIT 1"
                ),
                {"id": uuid.uuid4(), "set_id": original.determination_set_id},
            )

    with Session(app_engine) as session:
        session.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(legacy.tenant_id)},
        )
        second_report = generate_statutory_report(
            session,
            tenant_id=legacy.tenant_id,
            obligation_id=obligation_id,
            generated_by_id=uuid.uuid4(),
            generated_at=first_determined_at + timedelta(days=1),
        )
        assert second_report.total_payable == Decimal("75.000000")
        assert second_report.version == 2
        session.commit()
    app_engine.dispose()
    engine.dispose()
