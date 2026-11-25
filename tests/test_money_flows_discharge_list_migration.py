"""PostgreSQL proof of the money-flows ``mf_0003`` discharge-reference list."""

# ``migrated`` is the shared fixture from tests.money_flows_fixtures, imported by
# name so pytest registers it here; test arguments then shadow the import.
# ruff: noqa: F401, F811

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

from tests.money_flows_fixtures import (
    KERNEL,
    ROOT,
    VERSIONS,
    _as_tenant,
    _tenant,
    migrated,
)

COLUMN = "withholding_discharge_list_ref"


def _config():
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("version_locations", f"{KERNEL} {VERSIONS}")
    return cfg


def _insert_refused(app, tenant, refs) -> None:
    with app.begin() as connection:
        _as_tenant(connection, tenant)
        connection.execute(
            text(
                "INSERT INTO mod_mflows.settle_instances "  # noqa: S608
                "(id, tenant_id, state, reason_code, policy_version, "
                "definition_version, confirmation_id, intent_id, "
                f"{COLUMN}) VALUES (:id, :tenant, 'refused', 'x', 'p1', "
                "'settle.v1', :confirmation, :intent, CAST(:refs AS json))"
            ),
            {
                "id": uuid.uuid4(),
                "tenant": tenant,
                "confirmation": uuid.uuid4(),
                "intent": uuid.uuid4(),
                "refs": refs,
            },
        )


def test_the_column_is_a_nullable_json_list_beside_the_legacy_reference(
    migrated,
) -> None:
    engine = create_engine(migrated[0])
    with engine.begin() as connection:
        rows = dict(
            connection.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'mod_mflows' "
                    "AND table_name = 'settle_instances' "
                    "AND column_name IN ('withholding_discharge_ref', :c)"
                ),
                {"c": COLUMN},
            ).all()
        )
        nullable = connection.scalar(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'mod_mflows' "
                "AND table_name = 'settle_instances' AND column_name = :c"
            ),
            {"c": COLUMN},
        )
    engine.dispose()
    assert rows == {"withholding_discharge_ref": "character varying", COLUMN: "json"}
    assert nullable == "YES"


def test_downgrade_refuses_while_a_row_holds_several_references(migrated) -> None:
    from alembic import command

    admin_url, app_url = migrated
    tenant = _tenant(admin_url)
    app = create_engine(app_url)
    _insert_refused(app, tenant, '["a", "b"]')
    app.dispose()
    cfg = _config()
    with pytest.raises(RuntimeError, match="several discharge references"):
        command.downgrade(cfg, "mf_0002_funded_charge_instances")
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL row_security = off"))
        connection.execute(text("DELETE FROM mod_mflows.settle_instances"))
    command.downgrade(cfg, "mf_0002_funded_charge_instances")
    with engine.begin() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema = 'mod_mflows' "
                    "AND table_name = 'settle_instances' AND column_name = :c"
                ),
                {"c": COLUMN},
            )
            == 0
        )
    command.upgrade(cfg, "money_flows@head")
    engine.dispose()
