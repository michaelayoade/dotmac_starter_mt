"""D1 — per-module database namespaces and migration lineage identity.

Implements ADR-0006 § "Decision amendment — 2026-08-02", **D1**. This module is
the one authority that answers, for a composed deployment:

- *Which Postgres schema does this module own?* — one immutable ``mod_<short>``
  namespace per **stateful** module. A **stateless** module declares none.
- *Which migration prefix, branch label and lineage root does it own?*
- *Is the composed set of owners coherent?* — no two owners may claim the same
  schema, the same migration prefix, or the same branch label.

## Why this exists (the evidence, not a theory)

``docs/inventories/migration-collisions.md`` verified real cross-repo table-name
collisions: ``starter ∩ ERP`` on ``audit_events``, ``domain_settings``,
``people``, ``person_roles``, ``roles``, ``user_credentials``; ``starter ∩ Sub``
on ``parties``, ``party_roles``. Sixteen of seventeen duplicate names are
**same-name / different-shape**, and three of four repos use no schema
namespacing at all. Unlike a revision-ID clash — which fails loudly when Alembic
loads the script directory — a same-name/different-shape collision fails
*quietly in the dangerous direction*: an ``add_column`` mutates another module's
table and an ORM class mapped to a differently-shaped existing table reads wrong
rows. Schema namespaces make that collision impossible rather than unlikely.

## `public` is a compatibility namespace, not a shared one

``public`` stays the namespace of the **kernel** and the **one host assembly**
that composes it. It is deliberately NOT available to installable modules: an
installable module that could write into ``public`` reintroduces exactly the
collision class above. Extracting ERP or Sub code into a module does not make
its existing ``public`` tables module tables — the extraction's expand/contract
migration must create or adopt the assigned module schema explicitly, and until
that cutover is proven the source product and the candidate module are simply
not composable in one database.

## Never inferred from a display name

A module's schema comes from its registry-allocated ``short_code`` (see
``MIGRATION_OWNER_LEDGER``), never from ``code``, ``name``, a brand, or any
other human-facing string — those are mutable and white-label-variable, and a
namespace that moves is a data-loss event. ``ModuleManifest.db_schema`` is a
read-only derived property precisely so there is no settable schema attribute
anything could rewrite at runtime.

## Full qualification, never `search_path`

Module models, migrations, foreign keys, policies, functions and raw SQL must
name their schema explicitly (``mod_x.thing``), because ``search_path`` is
connection state a pooler, a psql session, or another module can change.
``qualified()`` and ``schema_table_args()`` are the helpers; the composed
migration gate (``dotmac_kernel.migrations.gate``) statically rejects a module
migration that omits ``schema=`` or writes raw SQL that never names its schema.

## Immutability — where it is actually enforced

Four layers, only the last of which is a comment:

1. ``ModuleManifest`` is a frozen dataclass, and ``db_schema`` is a *derived*
   read-only property — there is no attribute to assign.
2. ``MIGRATION_OWNER_LEDGER`` below is the checked-in, kernel-shipped allocation
   record. It is the reason "globally unique" can be true across Dotmac repos at
   all: the kernel is the shared dependency, so allocations are registered once,
   here, and every consumer resolves the same table.
3. ``NamespaceRegistry.from_manifests`` refuses a stateful module whose
   declaration is absent from the ledger (``UnallocatedNamespaceError``) or
   differs from it in any field (``NamespaceAllocationError``). A module cannot
   quietly re-point its own schema or prefix.
4. Changing a ledger row is therefore a visible kernel diff plus a kernel
   release — the same friction as any other protocol break.

Pure and in-memory, like ``capabilities``/``permissions``/``profiles``: no
database, no I/O, no FastAPI app.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:  # avoids a runtime cycle: `modules` imports this module
    from dotmac_kernel.modules import AnyManifest

# ── Identifier budgets (verified, not stylistic) ────────────────────────────

# Alembic stores the applied revision id in `alembic_version.version_num`,
# declared as `Column("version_num", String(32))` in `alembic/ddl/impl.py`
# (alembic 1.18.5). A revision id longer than this does not fail at authoring
# time — it fails at `alembic upgrade`, against a real database, with a value
# too long for type character varying(32). Hence the hard check in
# `revision_id()` and in the composed gate.
MAX_REVISION_ID_LENGTH: Final[int] = 32

# Postgres truncates identifiers at NAMEDATALEN-1 = 63 bytes, silently. A
# schema name that truncates is a schema that collides.
MAX_IDENTIFIER_LENGTH: Final[int] = 63

# The compatibility namespace: the kernel's and the one host assembly's tables.
HOST_SCHEMA: Final[str] = "public"

# Every module schema is `mod_` + its allocated short code. The prefix is what
# makes a module namespace recognisable in a live catalog dump at a glance.
MODULE_SCHEMA_PREFIX: Final[str] = "mod_"

# Schemas no owner may ever claim.
RESERVED_SCHEMAS: Final[frozenset[str]] = frozenset(
    {HOST_SCHEMA, "information_schema", "pg_catalog", "pg_toast", "pg_temp"}
)

# Number of digits in a revision's sequence component. Four is the budget the
# 32-char limit affords alongside a 6-char prefix and a readable slug; it also
# sorts lexicographically for the first 10 000 revisions of a lineage.
REVISION_SEQUENCE_DIGITS: Final[int] = 4

# A prefix longer than this leaves no room for a readable slug inside
# MAX_REVISION_ID_LENGTH (6 + 1 + 4 + 1 = 12 spent, 20 left for the slug).
MAX_MIGRATION_PREFIX_LENGTH: Final[int] = 6

_SHORT_CODE_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{1,20}$")
_MIGRATION_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    rf"^[a-z][a-z0-9]{{0,{MAX_MIGRATION_PREFIX_LENGTH - 1}}}$"
)
_BRANCH_LABEL_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_TABLE_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{0,61}$")
_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


# ── Errors ──────────────────────────────────────────────────────────────────


class NamespaceError(ValueError):
    """Base for every fail-closed D1 namespace/lineage validation error."""


class InvalidSchemaError(NamespaceError):
    """A declared schema name is malformed, over-long, or reserved."""


class InvalidMigrationPrefixError(NamespaceError):
    """A declared migration prefix is malformed or over-long."""


class InvalidRevisionIdError(NamespaceError):
    """A revision id is malformed or exceeds `alembic_version.version_num`."""


class DuplicateSchemaError(NamespaceError):
    """Two owners claimed the same Postgres schema — there is no single owner."""


class DuplicateMigrationPrefixError(NamespaceError):
    """Two owners claimed the same migration prefix, so revision ids can
    collide across independently released lineages."""


class DuplicateBranchLabelError(NamespaceError):
    """Two owners claimed the same Alembic branch label, so `alembic_version`
    rows can no longer be attributed to one owner."""


class DuplicateTableOwnerError(NamespaceError):
    """Two owners claimed the same qualified table."""


class UnallocatedNamespaceError(NamespaceError):
    """A stateful module declares a namespace with no row in the ledger."""


class NamespaceAllocationError(NamespaceError):
    """A stateful module's declaration differs from its immutable ledger row."""


class HostSchemaClaimError(NamespaceError):
    """An installable module tried to own the `public` compatibility schema."""


# ── Name construction / validation helpers ──────────────────────────────────


def module_schema(short_code: str) -> str:
    """The immutable schema name for an allocated module short code.

    The only supported way to build a module schema: the `mod_` form is
    structural here, not a convention a caller can opt out of.
    """
    validate_short_code(short_code)
    schema = f"{MODULE_SCHEMA_PREFIX}{short_code}"
    validate_schema(schema)
    return schema


def validate_short_code(short_code: str) -> None:
    if not _SHORT_CODE_RE.match(short_code):
        raise InvalidSchemaError(
            f"module short code {short_code!r} must match "
            f"{_SHORT_CODE_RE.pattern} (lowercase, 2-21 chars) — it is the "
            "permanent database identity, not a display name"
        )


def validate_schema(schema: str) -> None:
    """A module schema must be `mod_<short>`, in budget, and not reserved."""
    if schema in RESERVED_SCHEMAS:
        if schema == HOST_SCHEMA:
            raise HostSchemaClaimError(
                f"{HOST_SCHEMA!r} is the compatibility namespace of the kernel "
                "and the one host assembly; an installable module may not "
                "claim it (ADR-0006 D1)"
            )
        raise InvalidSchemaError(f"schema {schema!r} is reserved by Postgres")
    if schema.startswith("pg_"):
        raise InvalidSchemaError(f"schema {schema!r} is reserved by Postgres")
    if not schema.startswith(MODULE_SCHEMA_PREFIX):
        raise InvalidSchemaError(
            f"module schema {schema!r} must use the {MODULE_SCHEMA_PREFIX!r} "
            "form — it is registry-assigned, never inferred from a name"
        )
    if len(schema) > MAX_IDENTIFIER_LENGTH:
        raise InvalidSchemaError(
            f"schema {schema!r} is {len(schema)} chars; Postgres truncates at "
            f"{MAX_IDENTIFIER_LENGTH}, and a truncated schema collides"
        )
    validate_short_code(schema[len(MODULE_SCHEMA_PREFIX) :])


def validate_migration_prefix(prefix: str) -> None:
    if not _MIGRATION_PREFIX_RE.match(prefix):
        raise InvalidMigrationPrefixError(
            f"migration prefix {prefix!r} must match "
            f"{_MIGRATION_PREFIX_RE.pattern} — short enough to leave a readable "
            f"slug inside the {MAX_REVISION_ID_LENGTH}-char revision id budget"
        )


def validate_branch_label(label: str) -> None:
    if not _BRANCH_LABEL_RE.match(label):
        raise NamespaceError(
            f"branch label {label!r} must match {_BRANCH_LABEL_RE.pattern}"
        )


def qualified(schema: str, table: str) -> str:
    """`schema.table` — the only form module SQL may use (D1: never rely on
    `search_path`)."""
    return f"{schema}.{table}"


def schema_table_args(schema: str) -> dict[str, str]:
    """`__table_args__` fragment binding a SQLAlchemy model to a module schema.

    Use as ``__table_args__ = (…constraints…, schema_table_args(SCHEMA))`` or,
    with no constraints, ``__table_args__ = schema_table_args(SCHEMA)``. Binding
    the schema on the model is what makes every SELECT/INSERT the ORM emits
    fully qualified without a per-session `search_path`.
    """
    validate_schema(schema)
    return {"schema": schema}


def revision_id(prefix: str, sequence: int, slug: str) -> str:
    """Build a D1 revision id: ``<prefix>_<sequence>_<slug>``.

    Raises rather than truncating when the result would not fit
    `alembic_version.version_num` — a too-long id is a failure that would
    otherwise surface only against a real database mid-deploy.
    """
    validate_migration_prefix(prefix)
    if sequence < 0 or sequence >= 10**REVISION_SEQUENCE_DIGITS:
        raise InvalidRevisionIdError(
            f"revision sequence {sequence} must fit "
            f"{REVISION_SEQUENCE_DIGITS} digits"
        )
    if not _SLUG_RE.match(slug):
        raise InvalidRevisionIdError(
            f"revision slug {slug!r} must match {_SLUG_RE.pattern}"
        )
    rev = f"{prefix}_{sequence:0{REVISION_SEQUENCE_DIGITS}d}_{slug}"
    if len(rev) > MAX_REVISION_ID_LENGTH:
        raise InvalidRevisionIdError(
            f"revision id {rev!r} is {len(rev)} chars; "
            f"alembic_version.version_num is VARCHAR({MAX_REVISION_ID_LENGTH}), "
            "so this fails at `alembic upgrade`, not at authoring time — "
            "shorten the slug"
        )
    return rev


def revision_id_pattern(prefix: str) -> re.Pattern[str]:
    """The strict D1 revision-id shape for one owner's lineage."""
    validate_migration_prefix(prefix)
    return re.compile(
        rf"^{re.escape(prefix)}_\d{{{REVISION_SEQUENCE_DIGITS}}}"
        r"_[a-z0-9]+(?:_[a-z0-9]+)*$"
    )


# ── Migration owners + the allocation ledger ────────────────────────────────


@dataclass(frozen=True, slots=True)
class MigrationOwner:
    """One owner of a migration lineage and (if stateful) a database namespace.

    `owner` is the module `code` for an installable module, or the reserved
    host-owner name (`kernel`, `assembly`). `prefix` and `branch_label` are
    globally unique and immutable; `db_schema` is `None` only for the two host
    compatibility owners, which write into `public`.

    `legacy_revision_pattern` grandfathers the two lineages that predate D1.
    Their revision ids (`0001_initial_tenant_schema`, `a001_adopt_cfd`) are
    already recorded in live `alembic_version` rows, so renaming them to the
    `<prefix>_<sequence>_<slug>` form would break every existing deployment.
    This is the same compatibility carve-out `public` gets, and it is available
    ONLY to host owners — an installable module always uses the strict form.
    """

    owner: str
    prefix: str
    branch_label: str
    db_schema: str | None = None
    legacy_revision_pattern: str | None = None

    def __post_init__(self) -> None:
        if not self.owner:
            raise NamespaceError("migration owner requires a non-empty `owner`")
        validate_migration_prefix(self.prefix)
        validate_branch_label(self.branch_label)
        if self.db_schema is not None:
            validate_schema(self.db_schema)
        elif self.legacy_revision_pattern is None:
            raise HostSchemaClaimError(
                f"module {self.owner!r} owns migrations but declares no module "
                f"schema — {HOST_SCHEMA!r} is not available to installable "
                "modules (ADR-0006 D1)"
            )
        if self.legacy_revision_pattern is not None:
            re.compile(self.legacy_revision_pattern)

    @property
    def is_legacy(self) -> bool:
        """A pre-D1 host lineage keeping its original revision-id format."""
        return self.legacy_revision_pattern is not None

    def revision_pattern(self) -> re.Pattern[str]:
        """The revision-id shape every revision in this lineage must match."""
        if self.legacy_revision_pattern is not None:
            return re.compile(self.legacy_revision_pattern)
        return revision_id_pattern(self.prefix)


# The kernel's own base lineage (`0001_initial_tenant_schema` … ), branch label
# `kernel`, tables in `public`.
KERNEL_MIGRATION_OWNER: Final[MigrationOwner] = MigrationOwner(
    owner="kernel",
    prefix="k",
    branch_label="kernel",
    db_schema=None,
    legacy_revision_pattern=r"^\d{4}_[a-z0-9_]+$",
)

# The one host assembly composing the kernel (`a001_adopt_cfd` … ), branch label
# `assembly`, tables in `public`. Exactly one assembly exists per deployment, so
# this owner is reserved fleet-wide rather than allocated per repository.
ASSEMBLY_MIGRATION_OWNER: Final[MigrationOwner] = MigrationOwner(
    owner="assembly",
    prefix="a",
    branch_label="assembly",
    db_schema=None,
    legacy_revision_pattern=r"^a\d{3}_[a-z0-9_]+$",
)

HOST_MIGRATION_OWNERS: Final[tuple[MigrationOwner, ...]] = (
    KERNEL_MIGRATION_OWNER,
    ASSEMBLY_MIGRATION_OWNER,
)

# ── THE ALLOCATION LEDGER ───────────────────────────────────────────────────
# The checked-in, kernel-shipped record of every allocated namespace/prefix/
# branch label. Adding a row is an allocation; CHANGING one is a protocol break
# (a module's schema is where its data physically lives). `NamespaceRegistry`
# refuses any stateful module whose declaration is absent from — or differs
# from — its row here, which is what makes "immutable" enforceable rather than
# aspirational.
#
# Allocation rules for a new row:
#   * `owner`        = the module's manifest `code`
#   * `db_schema`    = module_schema(<short code>), unique fleet-wide
#   * `prefix`       ≤ 6 chars, unique fleet-wide, never reused after retirement
#   * `branch_label` unique fleet-wide
#
# No installable module is allocated yet: the first stateful module extracted
# under F4 adds its row in the same change that adds its manifest.
MIGRATION_OWNER_LEDGER: Final[tuple[MigrationOwner, ...]] = HOST_MIGRATION_OWNERS


# ── The composed registry ───────────────────────────────────────────────────


class NamespaceRegistry:
    """The validated set of database namespaces and migration owners.

    Construction is validation: a registry that exists is a composition whose
    owners have distinct schemas, distinct migration prefixes, distinct branch
    labels, and no contested table. Every failure raises a `NamespaceError`
    subclass — fail closed, never a half-namespaced database.
    """

    __slots__ = ("_owners", "_tables_by_schema")

    def __init__(
        self,
        owners: Iterable[MigrationOwner],
        *,
        tables_by_owner: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        declared = tuple(owners)
        self._check_unique(declared, "owner", lambda o: o.owner, NamespaceError)
        self._check_unique(
            declared,
            "Postgres schema",
            lambda o: o.db_schema,
            DuplicateSchemaError,
        )
        self._check_unique(
            declared,
            "migration prefix",
            lambda o: o.prefix,
            DuplicateMigrationPrefixError,
        )
        self._check_unique(
            declared,
            "branch label",
            lambda o: o.branch_label,
            DuplicateBranchLabelError,
        )
        self._owners: tuple[MigrationOwner, ...] = declared
        self._tables_by_schema: dict[str, frozenset[str]] = self._claim_tables(
            declared, tables_by_owner or {}
        )

    # ── Construction from manifests ─────────────────────────────────────────

    @classmethod
    def from_manifests(
        cls,
        manifests: Iterable[AnyManifest],
        *,
        ledger: Sequence[MigrationOwner] = MIGRATION_OWNER_LEDGER,
        host_owners: Sequence[MigrationOwner] = HOST_MIGRATION_OWNERS,
    ) -> NamespaceRegistry:
        """Build the registry for an installed module set.

        A manifest that declares no `short_code` is stateless: it contributes no
        namespace and no migration owner. A stateful manifest must match its
        ledger row exactly — see the module docstring's immutability layers.
        """
        # Validate the WHOLE shipped allocation ledger, not just the owners this
        # deployment happens to install. The ledger is what makes prefixes,
        # schemas and branch labels fleet-wide reservations; allowing dormant
        # rows to collide would defer the failure until their first joint
        # composition and make "globally unique" untrue in the meantime.
        ledger_registry = cls(ledger)
        by_owner = {entry.owner: entry for entry in ledger_registry.owners()}
        for host in host_owners:
            allocated_host = by_owner.get(host.owner)
            if allocated_host != host:
                raise NamespaceAllocationError(
                    f"host migration owner {host.owner!r} is absent from or "
                    "contradicts dotmac_kernel.namespaces."
                    "MIGRATION_OWNER_LEDGER"
                )
        owners: list[MigrationOwner] = list(host_owners)
        tables_by_owner: dict[str, Sequence[str]] = {}
        for manifest in manifests:
            # Duck-typed on purpose: a plain `FeatureManifest` has no D1
            # declaration at all and is simply stateless here, so this module
            # never needs a runtime import of `dotmac_kernel.modules`.
            declare = getattr(manifest, "migration_owner", None)
            declared = declare() if callable(declare) else None
            if declared is None:
                continue
            allocated = by_owner.get(declared.owner)
            if allocated is None:
                raise UnallocatedNamespaceError(
                    f"module {declared.owner!r} declares schema "
                    f"{declared.db_schema!r} / prefix {declared.prefix!r} with "
                    "no row in dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER "
                    "— allocate it there (and release the kernel) before the "
                    "module can own a namespace"
                )
            drift = {
                field_name: (getattr(allocated, field_name), got)
                for field_name, got in (
                    ("db_schema", declared.db_schema),
                    ("prefix", declared.prefix),
                    ("branch_label", declared.branch_label),
                )
                if getattr(allocated, field_name) != got
            }
            if drift:
                listed = ", ".join(
                    f"{name}: ledger {want!r} != manifest {got!r}"
                    for name, (want, got) in sorted(drift.items())
                )
                raise NamespaceAllocationError(
                    f"module {declared.owner!r} contradicts its immutable "
                    f"namespace allocation ({listed}) — a module's schema is "
                    "where its data physically lives; changing it is a data "
                    "migration, not an edit"
                )
            owners.append(allocated)
            tables_by_owner[allocated.owner] = tuple(getattr(manifest, "tables", ()))
        return cls(owners, tables_by_owner=tables_by_owner)

    # ── Validation helpers ──────────────────────────────────────────────────

    @staticmethod
    def _check_unique(
        owners: Sequence[MigrationOwner],
        what: str,
        key: Callable[[MigrationOwner], str | None],
        error: type[NamespaceError],
    ) -> None:
        seen: dict[str, str] = {}
        for owner in owners:
            value = key(owner)
            if value is None:
                continue
            if value in seen:
                raise error(
                    f"{what} {value!r} claimed by both {seen[value]!r} and "
                    f"{owner.owner!r} — a namespace has exactly one owner"
                )
            seen[value] = owner.owner

    @staticmethod
    def _claim_tables(
        owners: Sequence[MigrationOwner],
        tables_by_owner: Mapping[str, Sequence[str]],
    ) -> dict[str, frozenset[str]]:
        schema_of = {o.owner: o.db_schema for o in owners}
        claimed: dict[str, str] = {}
        out: dict[str, set[str]] = {}
        for owner_name, tables in tables_by_owner.items():
            schema = schema_of.get(owner_name)
            if schema is None:
                raise HostSchemaClaimError(
                    f"module {owner_name!r} declares tables but owns no module "
                    f"schema — {HOST_SCHEMA!r} is not available to installable "
                    "modules (ADR-0006 D1)"
                )
            for table in tables:
                if not _TABLE_NAME_RE.match(table):
                    raise NamespaceError(
                        f"module {owner_name!r} declares table {table!r}: table "
                        "names are unqualified and lowercase; the module's "
                        "schema qualifies them"
                    )
                key = qualified(schema, table)
                if key in claimed:
                    raise DuplicateTableOwnerError(
                        f"table {key} claimed by both {claimed[key]!r} and "
                        f"{owner_name!r} — one table, one owning module"
                    )
                claimed[key] = owner_name
                out.setdefault(schema, set()).add(table)
        return {schema: frozenset(tables) for schema, tables in out.items()}

    # ── Reading the registry ────────────────────────────────────────────────

    def owners(self) -> tuple[MigrationOwner, ...]:
        """Every migration owner in this composition (host owners included)."""
        return self._owners

    def module_schemas(self) -> tuple[str, ...]:
        """Every registered module schema, sorted — the set the post-migration
        live-catalog gate walks."""
        return tuple(sorted(o.db_schema for o in self._owners if o.db_schema))

    def owner_for_branch(self, label: str) -> MigrationOwner | None:
        """The owner a branch label attributes to — the mechanism that makes an
        `alembic_version` row explainable (ADR-0006 D1, item 5)."""
        for owner in self._owners:
            if owner.branch_label == label:
                return owner
        return None

    def owner_for_prefix(self, prefix: str) -> MigrationOwner | None:
        for owner in self._owners:
            if owner.prefix == prefix:
                return owner
        return None

    def get(self, owner: str) -> MigrationOwner | None:
        for candidate in self._owners:
            if candidate.owner == owner:
                return candidate
        return None

    def declared_tables(self, schema: str) -> frozenset[str]:
        """The tables the owning module declares in `schema` (empty if it
        declares none)."""
        return self._tables_by_schema.get(schema, frozenset())

    def table_owner(self, schema: str, table: str) -> str | None:
        for owner in self._owners:
            if owner.db_schema == schema and table in self.declared_tables(schema):
                return owner.owner
        return None


__all__ = [
    "MAX_REVISION_ID_LENGTH",
    "MAX_IDENTIFIER_LENGTH",
    "MAX_MIGRATION_PREFIX_LENGTH",
    "REVISION_SEQUENCE_DIGITS",
    "HOST_SCHEMA",
    "MODULE_SCHEMA_PREFIX",
    "RESERVED_SCHEMAS",
    "KERNEL_MIGRATION_OWNER",
    "ASSEMBLY_MIGRATION_OWNER",
    "HOST_MIGRATION_OWNERS",
    "MIGRATION_OWNER_LEDGER",
    "MigrationOwner",
    "NamespaceRegistry",
    "module_schema",
    "qualified",
    "schema_table_args",
    "revision_id",
    "revision_id_pattern",
    "validate_schema",
    "validate_short_code",
    "validate_migration_prefix",
    "validate_branch_label",
    "NamespaceError",
    "InvalidSchemaError",
    "InvalidMigrationPrefixError",
    "InvalidRevisionIdError",
    "DuplicateSchemaError",
    "DuplicateMigrationPrefixError",
    "DuplicateBranchLabelError",
    "DuplicateTableOwnerError",
    "UnallocatedNamespaceError",
    "NamespaceAllocationError",
    "HostSchemaClaimError",
]
