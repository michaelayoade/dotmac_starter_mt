"""SqlAlchemyRadiusDb against a real (SQLite) database."""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_connector_freeradius.database import SqlAlchemyRadiusDb
from dotmac_connector_freeradius.render import RenderConfig, render_projection

STEP_INPUT = {
    "username": "user@dotmac",
    "desired_state": "enabled",
    "policy_code": "active",
    "attributes": [{"key": "rate_limit", "value": "50M/50M"}],
}


HELD = "held-test-material"

_DDL = (
    "CREATE TABLE radcheck " "(username TEXT, attribute TEXT, op TEXT, value TEXT)",
    "CREATE TABLE radreply " "(username TEXT, attribute TEXT, op TEXT, value TEXT)",
    "CREATE TABLE radusergroup " "(username TEXT, groupname TEXT, priority INTEGER)",
    "CREATE TABLE radacct (radacctid INTEGER, username TEXT, "
    "nasipaddress TEXT, acctsessionid TEXT, acctstatustype TEXT, "
    "acctinputoctets INTEGER, acctoutputoctets INTEGER, "
    "acctsessiontime INTEGER, acctupdatetime TEXT, "
    "acctstarttime TEXT, acctstoptime TEXT)",
    "CREATE TABLE radpostauth (id INTEGER, username TEXT, reply TEXT, "
    "authdate TEXT, nasipaddress TEXT)",
    "CREATE TABLE nas (nasname TEXT, shortname TEXT, secret TEXT)",
    "INSERT INTO nas VALUES ('10.0.0.1', 'nas-1', 'shared-value')",
    "INSERT INTO radpostauth VALUES (1, 'user@dotmac', "
    "'Access-Accept', '2026-10-27 10:00:00+00:00', '10.0.0.1')",
    "INSERT INTO radacct VALUES (1, 'user@dotmac', '10.0.0.1', "
    "'sess-1', 'Start', 0, 0, 0, '2026-10-27 10:00:00+00:00', "
    "'2026-10-27 10:00:00+00:00', NULL)",
)


def _db(tmp_path) -> SqlAlchemyRadiusDb:
    engine = sa.create_engine(f"sqlite:///{tmp_path}/radius.db")
    with engine.begin() as connection:
        for statement in _DDL:
            connection.execute(sa.text(statement))
    return SqlAlchemyRadiusDb(engine)


def test_apply_read_roundtrip_and_rename_delete(tmp_path) -> None:
    db = _db(tmp_path)
    db.ping()
    rendered = render_projection(
        STEP_INPUT, config=RenderConfig(), cleartext_secret=HELD
    )
    written = db.apply_projection(
        usernames=["user@dotmac", "old@dotmac"], rendered=rendered, lock_key="k"
    )
    assert written == rendered.rows_written
    check, reply, groups = db.read_projection("user@dotmac")
    assert len(check) == len(rendered.check_rows)
    assert len(reply) == len(rendered.reply_rows)
    assert db.read_projection("old@dotmac") == ((), (), ())

    emptied = render_projection(
        {"username": "user@dotmac", "desired_state": "disabled", "attributes": []},
        config=RenderConfig(),
        cleartext_secret=None,
    )
    db.apply_projection(usernames=["user@dotmac"], rendered=emptied, lock_key="k")
    assert db.read_projection("user@dotmac") == ((), (), ())


def test_poll_and_nas_secret(tmp_path) -> None:
    db = _db(tmp_path)
    postauth = db.poll_postauth(after_id=0, limit=10)
    assert [row.reply for row in postauth] == ["Access-Accept"]
    acct = db.poll_accounting(after_id=0, limit=10)
    assert [row.status_type for row in acct] == ["Start"]
    assert db.nas_secret(nas_ref="nas-1", nas_address="10.0.0.1") == "shared-value"
    assert db.nas_secret(nas_ref="nope", nas_address="10.9.9.9") is None
