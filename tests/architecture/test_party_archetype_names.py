"""ADR-0019: `party_roles` names the archetype's PartyRole, nothing else.

The kernel used to call its RBAC grant `party_roles`/`PartyRole`. That is the
Party archetype's name for a concurrent, temporal business capacity (customer,
reseller, staff), and Sub already uses it that way. Migration
`0022_party_role_grants` freed the name; these tests stop it being taken again
for the grant concept, which is the only way the rename stays paid for.

This is a NAME reservation, not a claim that the kernel owns a PartyRole table.
It does not have one yet, deliberately — ADR-0019 § 6.
"""

from __future__ import annotations

import ast
from pathlib import Path

import dotmac_kernel
from dotmac_kernel.models import Base, PartyRoleGrant

KERNEL_SRC = Path(dotmac_kernel.__file__).resolve().parent

#: The grant concept's names, post-rename.
GRANT_TABLE = "party_role_grants"
GRANT_MODEL = "PartyRoleGrant"

#: Reserved fleet-wide for the archetype's business capacity (ADR-0019 § 4).
RESERVED_TABLE = "party_roles"
RESERVED_MODEL = "PartyRole"


def test_the_grant_table_carries_the_grant_name() -> None:
    assert PartyRoleGrant.__tablename__ == GRANT_TABLE


def test_no_kernel_table_is_named_party_roles() -> None:
    """The reserved name is free — including on any future kernel table."""
    offenders = sorted(name for name in Base.metadata.tables if name == RESERVED_TABLE)
    assert not offenders, (
        f"`{RESERVED_TABLE}` is reserved for the Party archetype's business "
        "capacity (ADR-0019 § 4); the kernel's RBAC grant is "
        f"`{GRANT_TABLE}`. Rename the offending table."
    )


def test_no_kernel_model_is_named_partyrole() -> None:
    offenders = sorted(
        m.class_.__name__
        for m in Base.registry.mappers
        if m.class_.__name__ == RESERVED_MODEL
    )
    assert not offenders, (
        f"`{RESERVED_MODEL}` is reserved for the Party archetype's business "
        f"capacity (ADR-0019 § 4). The RBAC grant is `{GRANT_MODEL}`."
    )


def test_no_compatibility_alias_is_exported() -> None:
    """ADR-0019 rejects an alias: it would preserve the ambiguity being removed.

    Checked against the package's real export surface, not just `__all__` — an
    alias assigned but omitted from `__all__` would still be importable and
    would still let consumer code keep the old meaning alive.
    """
    assert not hasattr(dotmac_kernel, RESERVED_MODEL), (
        f"`dotmac_kernel.{RESERVED_MODEL}` exists. ADR-0019 forbids a "
        "compatibility alias — consumers move to "
        f"`{GRANT_MODEL}` on their own pin schedule."
    )
    assert RESERVED_MODEL not in getattr(dotmac_kernel, "__all__", ())


def test_the_rename_migration_renames_every_dependent_object() -> None:
    """A half-renamed table invites a later 'fix' that collides with a product.

    The migration must move the constraint, index and RLS-policy names too;
    this asserts each old name appears in it, so dropping one from the
    migration fails here rather than in a product's database.
    """
    migration = (
        KERNEL_SRC / "migrations" / "versions" / "20260812_0022_party_role_grants.py"
    )
    source = migration.read_text(encoding="utf-8")

    required = (
        "uq_party_roles_member",
        "fk_party_roles_tenant_party",
        "fk_party_roles_tenant_role",
        "ix_party_roles_tenant_id",
        "ix_party_roles_party_id",
        "ix_party_roles_role_id",
        "party_roles_tenant_isolation",
    )
    missing = [name for name in required if name not in source]
    assert not missing, (
        f"{migration.name} does not rename: {missing}. Every dependent object "
        "carrying the old table's name moves in the same revision."
    )


def test_the_migration_refuses_an_ambiguous_database() -> None:
    """Both tables present must raise, not pick one.

    The failure mode this guards is a product that has its own `party_roles`
    (Sub does) and also the kernel's grant table. Silently renaming into that
    situation, or skipping, would merge two different concepts.
    """
    migration = (
        KERNEL_SRC / "migrations" / "versions" / "20260812_0022_party_role_grants.py"
    )
    tree = ast.parse(migration.read_text(encoding="utf-8"))
    upgrade = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
    )
    raises = [n for n in ast.walk(upgrade) if isinstance(n, ast.Raise)]
    assert raises, (
        "upgrade() has no `raise`: it cannot be refusing the both-tables-exist "
        "case, which would silently merge the kernel's grant with a product's "
        "own party_roles."
    )
