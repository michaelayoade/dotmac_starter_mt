"""Shared synthetic fixtures for the dotmac-ipam 0.2.0a1 tests.

Starter is a PUBLIC repository: every address here is from an RFC 5737 / 3849
documentation range (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24,
2001:db8::/32); every id is a fresh random UUID. No estate data.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID, uuid4

from dotmac_ipam import (
    AddBlock,
    AddressFamily,
    CreatePool,
    PoolPurpose,
    command_scope,
    create_pool,
)
from dotmac_ipam.models import (
    ALL_MODELS,
    IpPool,
    IPv4Address,
    IPv6Address,
)
from dotmac_kernel.models import Base, Tenant
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

SITE = UUID("00000000-0000-4000-8000-0000000000a1")


def make_session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_ipam": None}},
    )
    Base.metadata.create_all(
        engine, tables=[Tenant.__table__, *(m.__table__ for m in ALL_MODELS)]
    )
    return Session(engine)


def seed_tenant(db: Session) -> UUID:
    row = Tenant(slug=f"tenant-{uuid4().hex[:8]}", name="Tenant")
    db.add(row)
    db.flush()
    return row.id


def seed_pool(
    db: Session,
    tenant: UUID,
    *,
    cidr: str = "192.0.2.0/24",
    name: str | None = None,
    purpose: PoolPurpose = PoolPurpose.CUSTOMER,
    family: AddressFamily = AddressFamily.IPV4,
    gateway: str | None = None,
    allow_network_broadcast: bool = False,
    delegation: int | None = None,
    blocks: Iterable[str] = (),
    site: UUID | None = SITE,
) -> UUID:
    snapshot = create_pool(
        db,
        tenant_id=tenant,
        command=CreatePool(
            name=name or f"pool-{uuid4().hex[:6]}",
            cidr=cidr,
            purpose=purpose,
            ip_version=family,
            site_ref=site if purpose is PoolPurpose.CUSTOMER else None,
            gateway=gateway,
            allow_network_broadcast=allow_network_broadcast,
            delegation_prefix_length=delegation,
        ),
    )
    from dotmac_ipam import add_block

    for block in blocks:
        add_block(
            db, tenant_id=tenant, command=AddBlock(pool_id=snapshot.id, cidr=block)
        )
    return snapshot.id


def seed_v4(
    db: Session, tenant: UUID, pool: UUID, *addresses: str, **fields
) -> list[UUID]:
    """Materialise address rows (inventory import path), inside a command scope."""
    ids: list[UUID] = []
    with command_scope():
        for text in addresses:
            row = IPv4Address(tenant_id=tenant, address=text, pool_id=pool, **fields)
            db.add(row)
            db.flush()
            ids.append(row.id)
    return ids


def seed_v6(db: Session, tenant: UUID, pool: UUID, *addresses: str) -> list[UUID]:
    ids: list[UUID] = []
    with command_scope():
        for text in addresses:
            row = IPv6Address(tenant_id=tenant, address=text, pool_id=pool)
            db.add(row)
            db.flush()
            ids.append(row.id)
    return ids


def get_pool_row(db: Session, pool_id: UUID) -> IpPool:
    row = db.get(IpPool, pool_id)
    assert row is not None
    return row
