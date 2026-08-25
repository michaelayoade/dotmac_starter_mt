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
on ``parties``, ``party_roles`` (that last one no longer collides at lineage
head — ADR-0019 renamed the kernel's grant to ``party_role_grants`` in migration
``0022``; the chain still passes through the old name in ``0003``, so the
disposition is reduced rather than removed). Sixteen of seventeen duplicate names are
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

from dotmac_kernel.planes import (
    ModulePlane,
    ModulePlaneSelection,
    declared_planes,
    validate_module_plane_selections,
)
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    TENANT_SCOPE_CATALOG_V1,
    validate_prerequisites,
)

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

    `provides` names the logical prerequisites this lineage's revisions supply
    (`dotmac_kernel.prerequisites`). It is a claim about capability, not about
    ordering: an assembly's `PrerequisiteBinding` still names the exact revision,
    and the live verifier still proves the effect against the database. What
    declaring it buys is that the gate can reject a binding pointing at a
    lineage which never claimed to supply the effect at all — catching a
    mis-typed or wishful binding statically, before the database has to.
    """

    owner: str
    prefix: str
    branch_label: str
    db_schema: str | None = None
    legacy_revision_pattern: str | None = None
    provides: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.owner:
            raise NamespaceError("migration owner requires a non-empty `owner`")
        validate_migration_prefix(self.prefix)
        validate_branch_label(self.branch_label)
        validate_prerequisites(self.provides)
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
#
# `0001` supplies both shipped prerequisites — the tenant catalogue plus
# `app_current_tenant_id()`, and the three grantable database roles. It supplies
# a great deal else besides (people, credentials, sessions, RBAC, audit), and
# that is exactly why the prerequisites are declared separately: a module
# needing a foreign-key target must not thereby require an identity estate.
KERNEL_MIGRATION_OWNER: Final[MigrationOwner] = MigrationOwner(
    owner="kernel",
    prefix="k",
    branch_label="kernel",
    db_schema=None,
    legacy_revision_pattern=r"^\d{4}_[a-z0-9_]+$",
    provides=(TENANT_SCOPE_CATALOG_V1.name, MODULE_DATABASE_ROLES_V1.name),
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
# `dotmac-template-studio` — the FIRST allocated installable module (ADR-0006
# names it the first stateful one). Its row lands in the same change as its
# manifest, exactly as the rule above requires. `tstudio` (not `template_studio`)
# because the short code is the permanent physical identity and shorter is
# cheaper to read in every qualified name; `ts` leaves the whole revision-id
# budget for a readable slug.
TEMPLATE_STUDIO_MIGRATION_OWNER: Final[MigrationOwner] = MigrationOwner(
    owner="template_studio",
    prefix="ts",
    branch_label="template_studio",
    db_schema=module_schema("tstudio"),
)

# `dotmac-ticketing` — the second allocated installable module. `tkt` rather
# than `ticketing` because the short code is the permanent physical identity and
# every qualified name pays for its length; `tk` leaves the revision-id budget
# for a readable slug. The row lands in the same change as the module's manifest,
# as the allocation rule above requires.
TICKETING_MIGRATION_OWNER: Final[MigrationOwner] = MigrationOwner(
    owner="ticketing",
    prefix="tk",
    branch_label="ticketing",
    db_schema=module_schema("tkt"),
)

# `dotmac-release-catalog` — the third allocated installable module. `rel` reads
# as "release" at a glance in a live catalog dump, which `rlc` would not; `rl`
# leaves 20 characters of the revision-id budget for a readable slug. The row
# lands in the same change as the module's manifest, as the rule above requires.
#
# Unlike the two above, this module's tables are PLATFORM catalog tables: a
# published artifact is a vendor-wide fact, not a per-tenant one, so they carry
# no `tenant_id` and take grants rather than RLS (hard rule 11's documented
# platform-catalog case). The allocation is identical either way — the ledger
# owns physical identity, not tenancy policy.
RELEASE_CATALOG_MIGRATION_OWNER: Final[MigrationOwner] = MigrationOwner(
    owner="release_catalog",
    prefix="rl",
    branch_label="release_catalog",
    db_schema=module_schema("rel"),
)

# `dotmac-entitlement-allocation` — the fourth allocated installable module, and
# the second vendor-side one. `ealloc` rather than `alloc` because "allocation"
# alone is ambiguous in a fleet that also allocates IPs, ports and stock; `ea`
# leaves 20 characters of the revision-id budget for a readable slug. Platform
# catalog tables again, for the same reason as `mod_rel`: what a contract
# entitles is a vendor-side fact, not a per-tenant one.
ENTITLEMENT_ALLOCATION_MIGRATION_OWNER: Final[MigrationOwner] = MigrationOwner(
    owner="entitlement_allocation",
    prefix="ea",
    branch_label="entitlement_allocation",
    db_schema=module_schema("ealloc"),
)

# `dotmac-application-directory` — the FIFTH allocated installable module, and
# the first allocated for an assembly OTHER than this repository's: its consumer
# is the Tenant Workspace (ADR-0021). `appdir` rather than
# `application_directory` because the short code is the permanent physical
# identity and every qualified name pays for its length; `ad` leaves the
# revision-id budget for a readable slug. The row lands in the same change as
# the module's manifest, as the allocation rule above requires.
#
# An allocation is not a facility. This row adds no kernel behaviour, and nothing
# consumes it but the module it names — which is why it is compatible with
# ADR-0017's moratorium where a new kernel primitive would not be. ADR-0021 §7
# records that reasoning, and records that the moratorium's demand-pulled
# exception was deliberately NOT invoked for the same programme's contracts.
APPLICATION_DIRECTORY_MIGRATION_OWNER: Final[MigrationOwner] = MigrationOwner(
    owner="application_directory",
    prefix="ad",
    branch_label="application_directory",
    db_schema=module_schema("appdir"),
)

# `dotmac-files` — the SIXTH allocated installable module. Stored bytes are a
# reusable optional capability, not a universal kernel primitive. `files` is
# intentionally plain in catalog dumps, while the distinct `fi` prefix keeps
# its independently released Alembic lineage inside the revision-id budget.
FILES_MIGRATION_OWNER: Final[MigrationOwner] = MigrationOwner(
    owner="files",
    prefix="fi",
    branch_label="files",
    db_schema=module_schema("files"),
)

# `dotmac-imports` — the SEVENTH allocated installable module. The durable
# record of a bulk import is a reusable optional capability (ADR-0025), not a
# kernel primitive: most deployments never import a spreadsheet, and the ones
# that do supply their own field vocabulary. `imports` is plain in a catalog
# dump for the same reason `files` is, and the distinct `im` prefix keeps its
# independently released lineage inside the revision-id budget.
#
# Tenant plane only. The audit behind ADR-0025 found no control-plane import
# capability anywhere in the fleet, so the manifest declares no
# `platform_tables` — an allocation records physical identity, and a plane no
# product uses would be declared, not discovered.
IMPORTS_MIGRATION_OWNER: Final[MigrationOwner] = MigrationOwner(
    owner="imports",
    prefix="im",
    branch_label="imports",
    db_schema=module_schema("imports"),
)

# `dotmac-integration` — the EIGHTH allocated installable module, and the first
# allocated PLATFORM-PLANE ONLY: its manifest declares `platform_tables` and an
# empty tenant `tables` tuple (ADR-0023, ADR-0024 §§ 6-7). A connector
# installation, its configuration revisions, its capability bindings and its
# delivery evidence are control-plane facts about the fleet's integrations; no
# product queries them, and none of them belongs to a tenant of a product data
# plane.
#
# `intg` rather than `integration`: the schema prefix is read in catalog dumps
# and log lines, and `mod_integration` spends characters a reader does not need.
# `ig` leaves 20 characters of the revision-id budget for a readable slug.
INTEGRATION_MIGRATION_OWNER: Final[MigrationOwner] = MigrationOwner(
    owner="integration",
    prefix="ig",
    branch_label="integration",
    db_schema=module_schema("intg"),
)

# `dotmac-approvals` — the NINTH allocated installable module, and the first
# allocated for a capability that exists in BOTH planes in production today:
# ERP approves back-office subjects for a tenant, and the vendor control plane
# approves fleet-plan subjects with no tenant at all (ADR-0026 § 5). Both plane
# tuples are therefore populated, which is the case ADR-0023 was written for.
#
# `approvals` is plain in a catalog dump for the same reason `files` and
# `imports` are — a reader of `mod_approvals.approval_requests` needs no
# glossary. The distinct `ap` prefix keeps its independently released lineage
# inside the revision-id budget; `a` alone is the host assembly's.
#
# The row lands in the same change as the module's manifest, migration and
# dossier, as the allocation rule above requires — and deliberately so: an
# earlier revision of ADR-0026 reserved this namespace ahead of the code and was
# withdrawn on review, because a reservation is renumbered at every rebase while
# the alpha train is contended, and a ledger row with no manifest behind it is a
# state this module has no general way to express (ADR-0026 § 8).
APPROVALS_MIGRATION_OWNER: Final[MigrationOwner] = MigrationOwner(
    owner="approvals",
    prefix="ap",
    branch_label="approvals",
    db_schema=module_schema("approvals"),
)

# Allocated in 0.1.0a65 for `dotmac-numbering`, the first enabling owner of the
# ADR-0030 Cloud commerce programme (build-order step 5).
#
# `nu` rather than `num`: the prefix is a revision-id namespace, not the schema
# name, and every allocated prefix on this train is two characters. The schema
# is `mod_numbering` from the owner, which is what a reader of
# `mod_numbering.allocation_receipts` actually needs to see.
#
# Numbering is dual-plane for a reason ADR-0023 cares about: a tenant allocates
# its own invoice series, and the control plane allocates vendor-side series
# that no tenant may read. Both planes are DECLARED, not inferred.
NUMBERING_MIGRATION_OWNER: Final[MigrationOwner] = MigrationOwner(
    owner="numbering",
    prefix="nu",
    branch_label="numbering",
    db_schema=module_schema("numbering"),
)

# `dotmac-web-analytics` — tenant-only first-party website observations. The
# fleet audit found no real platform-plane adopter, so the allocation names one
# stateful module schema and the manifest declares only tenant tables. `wa`
# leaves the revision-id budget readable; `webanalytics` is explicit in catalog
# dumps without tying the physical identity to any adopter website.
WEB_ANALYTICS_MIGRATION_OWNER: Final[MigrationOwner] = MigrationOwner(
    owner="web_analytics",
    prefix="wa",
    branch_label="web_analytics",
    db_schema=module_schema("webanalytics"),
)

MIGRATION_OWNER_LEDGER: Final[tuple[MigrationOwner, ...]] = (
    *HOST_MIGRATION_OWNERS,
    TEMPLATE_STUDIO_MIGRATION_OWNER,
    TICKETING_MIGRATION_OWNER,
    INTEGRATION_MIGRATION_OWNER,
    RELEASE_CATALOG_MIGRATION_OWNER,
    ENTITLEMENT_ALLOCATION_MIGRATION_OWNER,
    APPLICATION_DIRECTORY_MIGRATION_OWNER,
    FILES_MIGRATION_OWNER,
    IMPORTS_MIGRATION_OWNER,
    APPROVALS_MIGRATION_OWNER,
    NUMBERING_MIGRATION_OWNER,
    WEB_ANALYTICS_MIGRATION_OWNER,
)


# ── The composed registry ───────────────────────────────────────────────────


class NamespaceRegistry:
    """The validated set of database namespaces and migration owners.

    Construction is validation: a registry that exists is a composition whose
    owners have distinct schemas, distinct migration prefixes, distinct branch
    labels, and no contested table. Every failure raises a `NamespaceError`
    subclass — fail closed, never a half-namespaced database.
    """

    __slots__ = (
        "_owners",
        "_tables_by_schema",
        "_platform_tables_by_schema",
        "_installed_planes_by_schema",
    )

    def __init__(
        self,
        owners: Iterable[MigrationOwner],
        *,
        tables_by_owner: Mapping[str, Sequence[str]] | None = None,
        platform_tables_by_owner: Mapping[str, Sequence[str]] | None = None,
        installed_planes_by_owner: Mapping[str, Sequence[ModulePlane]] | None = None,
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
        tenant_declared = tables_by_owner or {}
        platform_declared = platform_tables_by_owner or {}
        # Claim BOTH planes in one pass, so "one table, one owning module" is a
        # fact about the schema rather than about a plane. Two modules could
        # otherwise claim the same name in different planes and neither would
        # collide.
        combined: dict[str, Sequence[str]] = {
            owner: (
                *tenant_declared.get(owner, ()),
                *platform_declared.get(owner, ()),
            )
            for owner in {*tenant_declared, *platform_declared}
        }
        self._tables_by_schema: dict[str, frozenset[str]] = self._claim_tables(
            declared, combined
        )
        # The classification, kept separately: `declared_tables` stays the full
        # ownership set the catalog gate checks in both directions, while this
        # is what tells it WHICH contract each table is held to.
        self._platform_tables_by_schema: dict[str, frozenset[str]] = self._claim_tables(
            declared, platform_declared
        )
        schema_of = {o.owner: o.db_schema for o in declared}
        effective_installed = installed_planes_by_owner
        if effective_installed is None:
            # Direct registry construction is a declaration-only view used by
            # namespace tests and tooling. Preserve the historical atomic
            # default: every plane explicitly declared for an owner is present.
            effective_installed = {
                owner: (
                    *((ModulePlane.TENANT,) if tenant_declared.get(owner) else ()),
                    *((ModulePlane.PLATFORM,) if platform_declared.get(owner) else ()),
                )
                for owner in {*tenant_declared, *platform_declared}
            }
        self._installed_planes_by_schema: dict[str, frozenset[ModulePlane]] = {}
        for owner, planes in effective_installed.items():
            schema = schema_of.get(owner)
            if schema is None:
                continue
            self._installed_planes_by_schema[schema] = frozenset(planes)

    # ── Construction from manifests ─────────────────────────────────────────

    @classmethod
    def from_manifests(
        cls,
        manifests: Iterable[AnyManifest],
        *,
        ledger: Sequence[MigrationOwner] = MIGRATION_OWNER_LEDGER,
        host_owners: Sequence[MigrationOwner] = HOST_MIGRATION_OWNERS,
        module_planes: Sequence[ModulePlaneSelection] | None = None,
    ) -> NamespaceRegistry:
        """Build the registry for an installed module set.

        A manifest that declares no `short_code` is stateless: it contributes no
        namespace and no migration owner. A stateful manifest must match its
        ledger row exactly — see the module docstring's immutability layers.
        """
        manifests = tuple(manifests)
        selections = (
            validate_module_plane_selections(manifests, module_planes)
            if module_planes is not None
            else ()
        )
        selection_by_code = {selection.module: selection for selection in selections}

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
        platform_by_owner: dict[str, Sequence[str]] = {}
        installed_planes_by_owner: dict[str, Sequence[ModulePlane]] = {}
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
            platform_by_owner[allocated.owner] = tuple(
                getattr(manifest, "platform_tables", ())
            )
            selection = selection_by_code.get(str(getattr(manifest, "code", "")))
            installed_planes_by_owner[allocated.owner] = (
                tuple(ModulePlane(plane) for plane in selection.planes)
                if selection is not None
                else declared_planes(manifest)
            )
        return cls(
            owners,
            tables_by_owner=tables_by_owner,
            platform_tables_by_owner=platform_by_owner,
            installed_planes_by_owner=installed_planes_by_owner,
        )

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
        """Every table the owning module declares in `schema`, BOTH planes
        (empty if it declares none)."""
        return self._tables_by_schema.get(schema, frozenset())

    def declared_platform_tables(self, schema: str) -> frozenset[str]:
        """The subset of `declared_tables` held to the PLATFORM contract.

        No `tenant_id`, no RLS, granted to the platform roles and REVOKEd from
        the tenant application role — see ADR-0023. Always a subset of
        `declared_tables(schema)`; everything not in it is tenant-scoped.
        """
        return self._platform_tables_by_schema.get(schema, frozenset())

    def tenant_plane_installed(self, schema: str) -> bool:
        """Whether this assembly explicitly installed the tenant plane."""
        return ModulePlane.TENANT in self._installed_planes_by_schema.get(
            schema, frozenset()
        )

    def platform_plane_installed(self, schema: str) -> bool:
        """Whether this assembly explicitly installed the platform plane."""
        return ModulePlane.PLATFORM in self._installed_planes_by_schema.get(
            schema, frozenset()
        )

    def expected_tables(self, schema: str) -> frozenset[str]:
        """The tables that should ACTUALLY exist here, not merely be declared.

        The assembly's plane selection — never prerequisite availability —
        determines this set. Atomic modules default to every declared plane.

        Deliberately a SEPARATE reader from `declared_tables`, which stays the
        full ownership claim that table-ownership and static checks need: who
        owns a name does not change per assembly, only what got built does.
        """
        expected: set[str] = set()
        platform = self.declared_platform_tables(schema)
        tenant = self.declared_tables(schema) - platform
        if self.tenant_plane_installed(schema):
            expected.update(tenant)
        if self.platform_plane_installed(schema):
            expected.update(platform)
        return frozenset(expected)

    def table_owner(self, schema: str, table: str) -> str | None:
        for owner in self._owners:
            if owner.db_schema == schema and table in self.declared_tables(schema):
                return owner.owner
        return None


__all__ = [
    "APPLICATION_DIRECTORY_MIGRATION_OWNER",
    "APPROVALS_MIGRATION_OWNER",
    "ASSEMBLY_MIGRATION_OWNER",
    "FILES_MIGRATION_OWNER",
    "HOST_MIGRATION_OWNERS",
    "HOST_SCHEMA",
    "IMPORTS_MIGRATION_OWNER",
    "INTEGRATION_MIGRATION_OWNER",
    "KERNEL_MIGRATION_OWNER",
    "MAX_IDENTIFIER_LENGTH",
    "MAX_MIGRATION_PREFIX_LENGTH",
    "MAX_REVISION_ID_LENGTH",
    "MIGRATION_OWNER_LEDGER",
    "TICKETING_MIGRATION_OWNER",
    "WEB_ANALYTICS_MIGRATION_OWNER",
    "RELEASE_CATALOG_MIGRATION_OWNER",
    "ENTITLEMENT_ALLOCATION_MIGRATION_OWNER",
    "MODULE_SCHEMA_PREFIX",
    "RESERVED_SCHEMAS",
    "REVISION_SEQUENCE_DIGITS",
    "DuplicateBranchLabelError",
    "DuplicateMigrationPrefixError",
    "DuplicateSchemaError",
    "DuplicateTableOwnerError",
    "HostSchemaClaimError",
    "InvalidMigrationPrefixError",
    "InvalidRevisionIdError",
    "InvalidSchemaError",
    "MigrationOwner",
    "NamespaceAllocationError",
    "NamespaceError",
    "NamespaceRegistry",
    "UnallocatedNamespaceError",
    "module_schema",
    "qualified",
    "revision_id",
    "revision_id_pattern",
    "schema_table_args",
    "validate_branch_label",
    "validate_migration_prefix",
    "validate_schema",
    "validate_short_code",
]
