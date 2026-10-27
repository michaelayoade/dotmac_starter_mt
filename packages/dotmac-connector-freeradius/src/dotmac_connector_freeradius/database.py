"""SQLAlchemy `RadiusDbPort` over the FreeRADIUS SQL schema.

Table names come from configuration (deployments rename them); they are
validated as plain identifiers before any DDL-free reflection is used as an
f-string-free `Table` construction — a configured name can never become SQL
text because it is only ever a `Table` name argument.

Apply is the incumbent's discipline, per login: transaction-scoped advisory
lock (Postgres), DELETE the login's rows from all three tables, INSERT the
rendered set, one transaction. Withdrawal of a renamed login deletes the old
username in the same transaction (ADR-0076 section 8.6).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from dotmac_connector_freeradius.ports import AccountingRow, PostAuthRow
from dotmac_connector_freeradius.render import RenderedProjection, Row

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _table(name: str, *columns: sa.Column) -> sa.Table:
    if not _IDENTIFIER.fullmatch(name):
        raise ValueError(f"configured table name {name!r} is not a plain identifier")
    return sa.Table(name, sa.MetaData(), *columns, extend_existing=True)


def _aware(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    raise ValueError(f"expected a timestamp, got {type(value).__name__}")


class SqlAlchemyRadiusDb:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        radcheck_table: str = "radcheck",
        radreply_table: str = "radreply",
        radusergroup_table: str = "radusergroup",
        radacct_table: str = "radacct",
        radpostauth_table: str = "radpostauth",
        nas_table: str = "nas",
    ) -> None:
        self._engine = engine
        string, integer = sa.String, sa.Integer
        self._radcheck = _table(
            radcheck_table,
            sa.Column("username", string),
            sa.Column("attribute", string),
            sa.Column("op", string),
            sa.Column("value", string),
        )
        self._radreply = _table(
            radreply_table,
            sa.Column("username", string),
            sa.Column("attribute", string),
            sa.Column("op", string),
            sa.Column("value", string),
        )
        self._radusergroup = _table(
            radusergroup_table,
            sa.Column("username", string),
            sa.Column("groupname", string),
            sa.Column("priority", integer),
        )
        self._radacct = _table(
            radacct_table,
            sa.Column("radacctid", integer),
            sa.Column("username", string),
            sa.Column("nasipaddress", string),
            sa.Column("acctsessionid", string),
            sa.Column("acctstatustype", string),
            sa.Column("acctinputoctets", integer),
            sa.Column("acctoutputoctets", integer),
            sa.Column("acctsessiontime", integer),
            sa.Column("acctupdatetime", sa.DateTime(timezone=True)),
            sa.Column("acctstarttime", sa.DateTime(timezone=True)),
            sa.Column("acctstoptime", sa.DateTime(timezone=True)),
        )
        self._radpostauth = _table(
            radpostauth_table,
            sa.Column("id", integer),
            sa.Column("username", string),
            sa.Column("reply", string),
            sa.Column("authdate", sa.DateTime(timezone=True)),
            sa.Column("nasipaddress", string),
        )
        self._nas = _table(
            nas_table,
            sa.Column("nasname", string),
            sa.Column("shortname", string),
            sa.Column("secret", string),
        )

    def ping(self) -> None:
        with self._engine.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
            for table in (self._radcheck, self._radreply, self._radusergroup):
                connection.execute(sa.select(table.c.username).limit(0))

    def apply_projection(
        self,
        *,
        usernames: Sequence[str],
        rendered: RenderedProjection,
        lock_key: str,
    ) -> int:
        names = list(dict.fromkeys(usernames))
        with self._engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(
                    sa.text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                    {"lock_key": lock_key},
                )
            for table in (self._radcheck, self._radreply, self._radusergroup):
                connection.execute(sa.delete(table).where(table.c.username.in_(names)))
            written = 0
            for table, rows in (
                (self._radcheck, rendered.check_rows),
                (self._radreply, rendered.reply_rows),
                (self._radusergroup, rendered.group_rows),
            ):
                if rows:
                    connection.execute(sa.insert(table), list(rows))
                    written += len(rows)
            return written

    def read_projection(
        self, username: str
    ) -> tuple[tuple[Row, ...], tuple[Row, ...], tuple[Row, ...]]:
        def read(table: sa.Table, columns: tuple[str, ...]) -> tuple[Row, ...]:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    sa.select(table).where(table.c.username == username)
                ).mappings()
                return tuple(
                    {column: row.get(column) for column in columns} for row in rows
                )

        return (
            read(self._radcheck, ("username", "attribute", "op", "value")),
            read(self._radreply, ("username", "attribute", "op", "value")),
            read(self._radusergroup, ("username", "groupname", "priority")),
        )

    def poll_postauth(self, *, after_id: int, limit: int) -> tuple[PostAuthRow, ...]:
        table = self._radpostauth
        with self._engine.connect() as connection:
            rows = connection.execute(
                sa.select(table)
                .where(table.c.id > after_id)
                .order_by(table.c.id)
                .limit(limit)
            ).mappings()
            return tuple(
                PostAuthRow(
                    row_id=int(row["id"]),
                    username=str(row["username"] or ""),
                    reply=str(row["reply"] or ""),
                    authdate=_aware(row["authdate"]),
                    nas_address=(
                        str(row["nasipaddress"]) if row.get("nasipaddress") else None
                    ),
                )
                for row in rows
            )

    def poll_accounting(
        self, *, after_id: int, limit: int
    ) -> tuple[AccountingRow, ...]:
        table = self._radacct
        with self._engine.connect() as connection:
            rows = connection.execute(
                sa.select(table)
                .where(table.c.radacctid > after_id)
                .order_by(table.c.radacctid)
                .limit(limit)
            ).mappings()
            out: list[AccountingRow] = []
            for row in rows:
                event_at = (
                    row.get("acctupdatetime")
                    or row.get("acctstoptime")
                    or row.get("acctstarttime")
                )
                out.append(
                    AccountingRow(
                        row_id=int(row["radacctid"]),
                        username=str(row["username"] or ""),
                        nas_address=(
                            str(row["nasipaddress"])
                            if row.get("nasipaddress")
                            else None
                        ),
                        session_id=str(row["acctsessionid"] or ""),
                        status_type=str(row["acctstatustype"] or ""),
                        input_octets=int(row["acctinputoctets"] or 0),
                        output_octets=int(row["acctoutputoctets"] or 0),
                        session_seconds=int(row["acctsessiontime"] or 0),
                        event_at=_aware(event_at),
                    )
                )
            return tuple(out)

    def nas_secret(self, *, nas_ref: str, nas_address: str) -> str | None:
        table = self._nas
        with self._engine.connect() as connection:
            row = connection.execute(
                sa.select(table.c.secret).where(
                    sa.or_(
                        table.c.shortname == nas_ref,
                        table.c.nasname == nas_address,
                        table.c.nasname == nas_ref,
                    )
                )
            ).first()
            return str(row[0]) if row and row[0] else None


__all__ = ["SqlAlchemyRadiusDb"]
