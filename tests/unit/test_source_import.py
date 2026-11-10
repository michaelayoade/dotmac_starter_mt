"""Idempotent import verdicts (ADR-0079) on the kernel idempotency ledger."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from dotmac_kernel.exceptions import BadRequestError, ForbiddenError
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.machine_auth import MachinePrincipal
from dotmac_kernel.models import Tenant
from dotmac_kernel.resource_api import ResourceDeclaration, ResourceScopes
from dotmac_kernel.source_import import (
    ImportBlocked,
    ImportRefused,
    ImportVerdict,
    flat_id_map,
    import_record,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.unit.test_resource_api import Widget

DECL = ResourceDeclaration(
    name="invoices",
    model=Widget,
    scopes=ResourceScopes(read="invoices:read", import_write="imports.invoices:write"),
    serialize=lambda r: {},
    stamped_by_trigger=True,
    settle_horizon=timedelta(seconds=5),
)


def key(tenant: Tenant, *scopes: str, app: str = "sub") -> MachinePrincipal:
    return MachinePrincipal(
        credential_id=uuid.uuid4(),
        tenant_id=tenant.id,
        label="imp",
        application=app,
        scopes=frozenset(scopes),
    )


class Effect:
    def __init__(self) -> None:
        self.calls = 0
        self.seen: list[frozenset[int]] = []

    def __call__(self, session: Session, seen: frozenset[int]) -> dict[str, object]:
        self.calls += 1
        self.seen.append(seen)
        return {"local": "r1"}


def run(
    db,
    tenant,
    effect,
    *,
    ref="INV-1",
    version=1,
    content=None,
    app="sub",
    scopes=("imports.invoices:write",),
):
    return import_record(
        db,
        DECL,
        principal=key(tenant, *scopes, app=app),
        external_ref=ref,
        version=version,
        content=content or {"total": "10.00"},
        apply=effect,
    )


def test_created_then_replay_is_a_noop(db: Session, tenant_row: Tenant) -> None:
    effect = Effect()
    first = run(db, tenant_row, effect)
    assert first.verdict is ImportVerdict.CREATED and first.writes
    again = run(db, tenant_row, effect, content={"total": "10.00"})
    assert again.verdict is ImportVerdict.UNCHANGED and not again.writes
    assert again.result == {"local": "r1"}
    assert effect.calls == 1


def test_same_key_changed_digest_is_refused_and_writes_nothing(
    db: Session, tenant_row: Tenant
) -> None:
    effect = Effect()
    run(db, tenant_row, effect)
    result = run(db, tenant_row, effect, content={"total": "11.00"})
    assert result.verdict is ImportVerdict.CONFLICT and result.wire == "conflict"
    assert effect.calls == 1
    rows = db.execute(select(IdempotencyRecord)).scalars().all()
    assert len(rows) == 1


def test_new_version_updates_and_lower_version_is_stale(
    db: Session, tenant_row: Tenant
) -> None:
    effect = Effect()
    run(db, tenant_row, effect, version=2)
    assert (
        run(db, tenant_row, effect, version=1, content={"x": 1}).verdict
        is ImportVerdict.STALE
    )
    assert effect.calls == 1
    assert (
        run(db, tenant_row, effect, version=3, content={"x": 3}).verdict
        is ImportVerdict.UPDATED
    )
    assert effect.seen[-1] == frozenset({2})
    # replaying an already-imported lower version is still a no-op, not stale
    assert run(db, tenant_row, effect, version=2).verdict is ImportVerdict.UNCHANGED


def test_source_comes_from_the_key_not_the_body(
    db: Session, tenant_row: Tenant
) -> None:
    effect = Effect()
    run(db, tenant_row, effect, content={"source_system": "erp", "n": 1}, app="sub")
    other = run(
        db, tenant_row, effect, content={"source_system": "erp", "n": 1}, app="erp"
    )
    assert other.verdict is ImportVerdict.CREATED
    scopes = {r.scope for r in db.execute(select(IdempotencyRecord)).scalars()}
    assert scopes == {"import:sub:invoices", "import:erp:invoices"}


def test_references_that_share_a_prefix_do_not_collide(
    db: Session, tenant_row: Tenant
) -> None:
    effect = Effect()
    run(db, tenant_row, effect, ref="a@1", version=5)
    assert (
        run(db, tenant_row, effect, ref="a", version=1).verdict is ImportVerdict.CREATED
    )
    run(db, tenant_row, effect, ref="a%", version=7)
    assert (
        run(db, tenant_row, effect, ref="ab", version=1).verdict
        is ImportVerdict.CREATED
    )


def test_blocked_and_refused_record_nothing_and_retry_redrives(
    db: Session, tenant_row: Tenant
) -> None:
    def blocked(session: Session, seen: frozenset[int]):
        raise ImportBlocked

    def refused(session: Session, seen: frozenset[int]):
        raise ImportRefused("bad_currency")

    assert run(db, tenant_row, blocked).wire == "blocked:predecessor"
    r = run(db, tenant_row, refused)
    assert r.wire == "refused:bad_currency"
    assert db.execute(select(IdempotencyRecord)).scalars().all() == []
    assert run(db, tenant_row, Effect()).verdict is ImportVerdict.CREATED
    with pytest.raises(ValueError):
        ImportRefused("Has Space")


@pytest.mark.parametrize(
    "scopes",
    [(), ("invoices:read",), ("imports.other:write",), ("imports.*:write",), ("*",)],
)
def test_a_key_without_the_exact_import_scope_is_refused(
    db: Session, tenant_row: Tenant, scopes
) -> None:
    effect = Effect()
    with pytest.raises(ForbiddenError):
        run(db, tenant_row, effect, scopes=scopes)
    assert effect.calls == 0


def test_inputs_are_validated(db: Session, tenant_row: Tenant) -> None:
    effect = Effect()
    for kwargs in (
        {"version": 0},
        {"version": True},
        {"ref": ""},
        {"ref": "x" * 300},
        {"app": "Bad:Name"},
    ):
        with pytest.raises(BadRequestError):
            run(db, tenant_row, effect, **kwargs)
    assert effect.calls == 0


def test_a_key_that_also_reads_is_refused_by_import(
    db: Session, tenant_row: Tenant
) -> None:
    effect = Effect()
    for extra in ("invoices:read",):
        with pytest.raises(ForbiddenError):
            run(db, tenant_row, effect, scopes=("imports.invoices:write", extra))
    assert effect.calls == 0


def test_the_stored_result_is_a_small_flat_id_map() -> None:
    assert flat_id_map({"local_id": "r1", "n": 2}) == {"local_id": "r1", "n": 2}
    assert flat_id_map(None) == {}
    for bad in (
        {"nested": {"a": 1}},
        {"lst": [1]},
        {"Bad-Key": "x"},
        {"v": "x" * 201},
        {"v": 1.5},
        {"v": True},
        {f"k{i}": 1 for i in range(17)},
    ):
        with pytest.raises(ValueError):
            flat_id_map(bad)


def test_a_payload_in_the_result_is_refused_and_nothing_is_recorded(
    db: Session, tenant_row: Tenant
) -> None:
    def leaky(session: Session, seen: frozenset[int]):
        return {"customer": {"email": "a@example.test"}}

    with pytest.raises(ValueError):
        run(db, tenant_row, leaky)
    assert db.execute(select(IdempotencyRecord)).scalars().all() == []


def test_current_version_callback_replaces_the_ledger_scan(
    db: Session, tenant_row: Tenant
) -> None:
    effect = Effect()
    result = import_record(
        db,
        DECL,
        principal=key(tenant_row, "imports.invoices:write"),
        external_ref="INV-9",
        version=2,
        content={"a": 1},
        apply=effect,
        current_version=lambda session: 5,
    )
    assert result.verdict is ImportVerdict.STALE and effect.calls == 0
    result = import_record(
        db,
        DECL,
        principal=key(tenant_row, "imports.invoices:write"),
        external_ref="INV-9",
        version=6,
        content={"a": 1},
        apply=effect,
        current_version=lambda session: 5,
    )
    assert result.verdict is ImportVerdict.UPDATED and effect.seen[-1] == frozenset({5})
