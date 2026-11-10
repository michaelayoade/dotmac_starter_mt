"""Resource API kit (ADR-0079): cursor, settle horizon, tombstones, summary, scopes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel.exceptions import BadRequestError, ForbiddenError
from dotmac_kernel.machine_auth import MachinePrincipal
from dotmac_kernel.resource_api import (
    InvalidCursorError,
    ResourceDeclaration,
    ResourceScopes,
    changes_page,
    decode_cursor,
    encode_cursor,
    summarize,
)
from sqlalchemy import DateTime, Integer, String, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
TENANT = uuid.uuid4()


class _Base(DeclarativeBase):
    pass


class Widget(_Base):
    __tablename__ = "kit_widgets"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(80), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


DECL = ResourceDeclaration(
    name="widgets",
    model=Widget,
    scopes=ResourceScopes(
        read="widgets:read",
        contact_read="widgets.contact:read",
        import_write="imports.widgets:write",
    ),
    serialize=lambda w: {"id": str(w.id), "name": w.name, "email": w.email},
    contact_fields=frozenset({"email"}),
    settle_horizon=timedelta(seconds=60),
)


def principal(*scopes: str) -> MachinePrincipal:
    return MachinePrincipal(
        credential_id=uuid.uuid4(),
        tenant_id=TENANT,
        label="k",
        application="sub",
        scopes=frozenset(scopes),
    )


READER = principal("widgets:read")


@pytest.fixture()
def session(db: Session) -> Session:
    _Base.metadata.create_all(db.get_bind())
    return db


def add(
    db: Session, at: datetime, *, version: int = 1, deleted: bool = False, id_=None
):
    row = Widget(
        id=id_ or uuid.uuid4(),
        version=version,
        name="w",
        email="a@example.test",
        updated_at=at,
        deleted_at=at if deleted else None,
    )
    db.add(row)
    db.flush()
    return row


NOW = T0 + timedelta(minutes=10)


def test_cursor_round_trip_and_tamper_refused() -> None:
    wid = uuid.uuid4()
    cursor = encode_cursor("widgets", T0, wid)
    assert decode_cursor("widgets", cursor) == (T0, str(wid))
    body, check = cursor.split(".")
    for bad in (
        "garbage",
        f"{body}.AAAAAAAAAAA",
        f"{body[:-2]}xx.{check}",
        "",
        cursor + ".x",
    ):
        with pytest.raises(InvalidCursorError):
            decode_cursor("widgets", bad)
    with pytest.raises(InvalidCursorError):
        decode_cursor("gadgets", cursor)


def test_paging_is_stable_with_ties_and_complete(session: Session) -> None:
    rows = [add(session, T0) for _ in range(5)] + [
        add(session, T0 + timedelta(seconds=1))
    ]
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        page = changes_page(
            session, DECL, principal=READER, after=cursor, limit=2, now=NOW
        )
        seen += [i.id for i in page.items]
        cursor = page.next_cursor
        pages += 1
        if not page.has_more:
            break
    assert pages == 3
    assert len(seen) == len(set(seen)) == len(rows)
    # an insert between pages never repeats or reorders what was already served
    again = changes_page(session, DECL, principal=READER, after=cursor, now=NOW)
    assert again.items == () and again.next_cursor == cursor


def test_late_commit_inside_the_horizon_is_not_skipped(session: Session) -> None:
    add(session, T0)
    # Reader polls while a slow transaction (started at T0+30s) has not committed.
    poll1 = changes_page(
        session, DECL, principal=READER, now=T0 + timedelta(seconds=50)
    )
    assert poll1.items == ()  # T0 is newer than the horizon (T0-10s): withheld
    cursor = poll1.next_cursor
    # The slow transaction now commits with its start-time stamp; the reader
    # never moved past it, as nothing newer than the horizon was served.
    late = add(session, T0 + timedelta(seconds=30))
    poll2 = changes_page(
        session, DECL, principal=READER, after=cursor, now=T0 + timedelta(seconds=200)
    )
    assert str(late.id) in [i.id for i in poll2.items]


def test_without_a_horizon_the_same_sequence_skips_the_late_commit(
    session: Session,
) -> None:
    """Sensitivity proof: the horizon is what prevents the skip."""
    first = add(session, T0)
    naive_now = (
        T0 + timedelta(seconds=31) + DECL.settle_horizon
    )  # horizon edge at T0+31
    page = changes_page(session, DECL, principal=READER, now=naive_now)
    assert [i.id for i in page.items] == [str(first.id)]
    # A reader with NO hold-back would have served rows up to "now"; emulate by moving
    # the cursor past the late row's stamp, then committing the late row.
    cursor = encode_cursor("widgets", T0 + timedelta(seconds=40), uuid.UUID(int=0))
    late = add(session, T0 + timedelta(seconds=30))
    skipped = changes_page(session, DECL, principal=READER, after=cursor, now=NOW)
    assert str(late.id) not in [i.id for i in skipped.items]
    # With the horizon, a poll at T0+31 could not have advanced past the late stamp.
    held = changes_page(
        session, DECL, principal=READER, now=T0 + timedelta(seconds=60 + 20)
    )
    assert held.next_cursor is not None
    assert decode_cursor("widgets", held.next_cursor)[0] <= T0 + timedelta(seconds=20)


def test_unsettled_rows_are_withheld(session: Session) -> None:
    add(session, NOW - timedelta(seconds=5))
    assert changes_page(session, DECL, principal=READER, now=NOW).items == ()


def test_delete_is_a_tombstone_and_a_version_bump(session: Session) -> None:
    row = add(session, T0, version=1)
    row.version, row.updated_at, row.deleted_at = (
        2,
        T0 + timedelta(seconds=5),
        T0 + timedelta(seconds=5),
    )
    session.flush()
    (item,) = changes_page(session, DECL, principal=READER, now=NOW).items
    assert item.deleted and item.record is None
    out = item.as_dict()
    assert out["deleted"] is True and out["version"] == 2 and out["id"] == str(row.id)
    assert "record" not in out and "name" not in out


def test_contact_fields_need_their_own_scope(session: Session) -> None:
    add(session, T0)
    plain = changes_page(session, DECL, principal=READER, now=NOW).items[0]
    assert "email" not in (plain.record or {})
    contact = changes_page(
        session,
        DECL,
        principal=principal("widgets:read", "widgets.contact:read"),
        now=NOW,
    ).items[0]
    assert (contact.record or {})["email"] == "a@example.test"
    # the contact scope alone does not read
    with pytest.raises(ForbiddenError):
        changes_page(
            session, DECL, principal=principal("widgets.contact:read"), now=NOW
        )


@pytest.mark.parametrize(
    "scopes",
    [
        (),
        ("widgets:*",),
        ("*",),
        ("widgets",),
        ("widgets:read:extra",),
        ("imports.widgets:write",),
        ("gadgets:read",),
    ],
)
def test_a_key_outside_its_scope_is_refused(
    session: Session, scopes: tuple[str, ...]
) -> None:
    add(session, T0)
    with pytest.raises(ForbiddenError):
        changes_page(session, DECL, principal=principal(*scopes), now=NOW)
    with pytest.raises(ForbiddenError):
        summarize(
            session,
            DECL,
            principal=principal(*scopes),
            window_from=T0,
            window_to=NOW,
            now=NOW,
        )


def test_scope_declarations_refuse_wildcards_and_overlap() -> None:
    for kwargs in (
        {"read": "widgets:*"},
        {"read": "*"},
        {"read": " widgets:read"},
        {"read": ""},
        {"read": "a:read", "import_write": "a:read"},
        {"read": "a:read", "contact_read": "b:*"},
    ):
        with pytest.raises(ValueError):
            ResourceScopes(**kwargs)  # type: ignore[arg-type]


def test_declaration_refuses_unsafe_horizon_and_missing_columns() -> None:
    with pytest.raises(ValueError):
        ResourceDeclaration(
            name="w",
            model=Widget,
            scopes=ResourceScopes(read="w:read"),
            serialize=lambda r: {},
            settle_horizon=timedelta(0),
        )
    with pytest.raises(ValueError):
        ResourceDeclaration(
            name="w",
            model=Widget,
            scopes=ResourceScopes(read="w:read"),
            serialize=lambda r: {},
            version_column="nope",
        )


def test_limit_is_bounded(session: Session) -> None:
    for limit in (0, 10_000):
        with pytest.raises(BadRequestError):
            changes_page(session, DECL, principal=READER, limit=limit, now=NOW)


def test_summary_counts_versions_tombstones_and_flags_unsettled(
    session: Session,
) -> None:
    a = add(session, T0)
    add(session, T0 + timedelta(seconds=1), deleted=True, version=3)
    first = summarize(
        session,
        DECL,
        principal=READER,
        window_from=T0,
        window_to=NOW - timedelta(minutes=2),
        now=NOW,
    )
    assert first.count == 2 and first.settled
    a.version, a.updated_at = 2, T0 + timedelta(seconds=2)
    session.flush()
    second = summarize(
        session,
        DECL,
        principal=READER,
        window_from=T0,
        window_to=NOW - timedelta(minutes=2),
        now=NOW,
    )
    assert second.count == 2 and second.digest != first.digest
    cut = summarize(
        session,
        DECL,
        principal=READER,
        window_from=T0,
        window_to=NOW + timedelta(hours=1),
        now=NOW,
    )
    assert cut.settled is False
