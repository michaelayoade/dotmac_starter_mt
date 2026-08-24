"""Prove released subscriptions a1 data upgrades without rewritten history."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
CURRENT_SUBSCRIPTIONS = (
    REPO_ROOT
    / "packages/dotmac-subscriptions/src/dotmac_subscriptions/migrations/versions"
)
MIGRATION_PATH = (
    "packages/dotmac-subscriptions/src/dotmac_subscriptions/migrations/versions/"
    "su_0001_subscriptions.py"
)
RELEASE_TAG = "dotmac-subscriptions-v0.1.0a1"
RELEASED_DIGEST = "bbc6a1da801259a734988c976800c404ce30f4a3b8cf3f24a48410f557e3f252"
GIT = shutil.which("git")
assert GIT is not None, "git is required to reconstruct released migration bytes"


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — upgrade proofs need PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


def _released_source() -> bytes:
    result = subprocess.run(  # noqa: S603 # nosec B603 B607
        [GIT, "show", f"{RELEASE_TAG}:{MIGRATION_PATH}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert hashlib.sha256(result.stdout).hexdigest() == RELEASED_DIGEST
    return result.stdout


def _config(versions: Path) -> Any:
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option(
        "version_locations",
        f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {versions}",
    )
    cfg.attributes["module_plane_selections"] = (
        ModulePlaneSelection(
            module="subscriptions",
            planes=(ModulePlane.PLATFORM,),
        ),
    )
    return cfg


@contextmanager
def _scratch_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    superuser = _superuser_url()
    name = f"subscriptions_upgrade_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO platform_api'))
    setup.dispose()

    admin_url = _url_for(superuser, name, user="app_admin")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    try:
        yield admin_url
    finally:
        with server.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _seed_a1_offer(admin_url: str, prices: tuple[tuple[str, int], ...]) -> uuid.UUID:
    offer_id = uuid.uuid4()
    version_id = uuid.uuid4()
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO mod_subscriptions.platform_offers "
                "(id, code, name, status) VALUES "
                "(:id, 'released-a1', 'Released a1 offer', 'published')"
            ),
            {"id": offer_id},
        )
        conn.execute(
            text(
                "INSERT INTO mod_subscriptions.platform_offer_versions "
                "(id, offer_id, version, state, effective_from, source_code, "
                "source_id, source_version, command_id, content_digest) VALUES "
                "(:id, :offer, 1, 'published', now(), 'accepted_order_line', "
                ":source, 1, :command, :digest)"
            ),
            {
                "id": version_id,
                "offer": offer_id,
                "source": uuid.uuid4(),
                "command": uuid.uuid4(),
                "digest": "a" * 64,
            },
        )
        for index, (charge_model, amount) in enumerate(prices):
            conn.execute(
                text(
                    "INSERT INTO mod_subscriptions.platform_offer_version_prices "
                    "(id, offer_version_id, price_key, charge_model_code, amount, "
                    "currency, scale, quantity) VALUES "
                    "(:id, :version, :key, :charge_model, :amount, 'NGN', 2, 1)"
                ),
                {
                    "id": uuid.uuid4(),
                    "version": version_id,
                    "key": f"price-{index}",
                    "charge_model": charge_model,
                    "amount": amount,
                },
            )
    engine.dispose()
    return version_id


def test_released_a1_offer_evidence_derives_a2_policy_and_can_downgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alembic import command

    historical = tmp_path / "released_subscriptions"
    historical.mkdir()
    (historical / "su_0001_subscriptions.py").write_bytes(_released_source())

    with _scratch_database(monkeypatch) as admin_url:
        command.upgrade(_config(historical), "heads")
        version_id = _seed_a1_offer(admin_url, (("recurring_access", 100),))
        command.upgrade(_config(CURRENT_SUBSCRIPTIONS), "heads")

        engine = create_engine(admin_url)
        with engine.connect() as conn:
            assert conn.execute(
                text(
                    "SELECT charge_model_code, pricing_mode FROM "
                    "mod_subscriptions.platform_offer_versions WHERE id = :id"
                ),
                {"id": version_id},
            ).one() == ("recurring_access", "catalog_price")
            with pytest.raises(DBAPIError, match="ck_platform_offer_prices_amounts"):
                conn.execute(
                    text(
                        "INSERT INTO "
                        "mod_subscriptions.platform_offer_version_prices "
                        "(id, offer_version_id, price_key, charge_model_code, "
                        "amount, currency, scale, quantity) VALUES "
                        "(:id, :version, 'zero', 'recurring_access', 0, 'NGN', 2, 1)"
                    ),
                    {"id": uuid.uuid4(), "version": version_id},
                )
        engine.dispose()

        command.downgrade(_config(CURRENT_SUBSCRIPTIONS), "su_0001_subscriptions")
        engine = create_engine(admin_url)
        with engine.connect() as conn:
            columns = set(
                conn.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'mod_subscriptions' AND "
                        "table_name = 'platform_offer_versions'"
                    )
                )
            )
        engine.dispose()
        assert "charge_model_code" not in columns
        assert "pricing_mode" not in columns


@pytest.mark.parametrize(
    ("prices", "message"),
    (
        ((), "without exactly one legacy charge model"),
        ((("recurring_access", 0),), "contains a non-positive price"),
        (
            (("recurring_access", 100), ("usage_metered", 50)),
            "without exactly one legacy charge model",
        ),
    ),
    ids=("missing-price", "zero-price", "mixed-charge-model"),
)
def test_ambiguous_a1_offer_evidence_fails_closed_before_a2_ddl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prices: tuple[tuple[str, int], ...],
    message: str,
) -> None:
    from alembic import command

    historical = tmp_path / "released_subscriptions"
    historical.mkdir()
    (historical / "su_0001_subscriptions.py").write_bytes(_released_source())

    with _scratch_database(monkeypatch) as admin_url:
        command.upgrade(_config(historical), "heads")
        _seed_a1_offer(admin_url, prices)
        with pytest.raises(RuntimeError, match=message):
            command.upgrade(_config(CURRENT_SUBSCRIPTIONS), "heads")

        engine = create_engine(admin_url)
        with engine.connect() as conn:
            heads = set(conn.scalars(text("SELECT version_num FROM alembic_version")))
            columns = set(
                conn.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'mod_subscriptions' AND "
                        "table_name = 'platform_offer_versions'"
                    )
                )
            )
        engine.dispose()
        assert "su_0001_subscriptions" in heads
        assert "su_0002_offer_pricing" not in heads
        assert "charge_model_code" not in columns
        assert "pricing_mode" not in columns
