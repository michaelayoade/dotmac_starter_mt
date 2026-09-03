"""``PostgresRecoveryBundle.v1`` — the artefact a database can actually come back from.

`backup.py` names four verdicts and says only the fourth, ``PROVED``, supports a
recovery claim. This module is what makes ``PROVED`` reachable, because on
2026-08-29 it was reachable for nothing in the fleet.

## The measurement that started this

A `pg_restore` of the newest production Vendor CP backup into a disposable
`--network none` PostgreSQL 16 container exited **1** with **114 missing-role
errors** — `app_admin` 56, `platform_api` 34, `app_user` 20, `outbox_dispatcher`
2, `platform_outbox_dispatcher` 2. The dump's TOC held 55 ACL entries, 26 POLICY
entries and **zero role objects**, because `pg_dump --dbname` captures GRANTs and
policies and never captures the roles they name.

**The dangerous half is not the failure.** The failure is loud. What that same
run left behind was **45 tables, 23 of the 26 policies and 16 RLS-enabled
tables** — a database that reads as restored. Under ADR-0023 the revocation
across the plane boundary *is* the isolation, so an operator who checks
`pg_policies` after recovery sees policies present and concludes isolation came
back. It did not: every object was owned by the restoring superuser, no role
existed, and therefore no grant and no revocation existed either. A policy that
names a role no longer in the cluster restricts nobody.

So the artefact this module defines is not "a dump plus some extras". It is the
closure of everything a database needs in order to be the same database, and the
control that matters most is :func:`load_manifest` refusing a **plausible
whole** — a lone custom-format dump — rather than refusing an obviously broken
one. Every other refusal here catches a missing piece. That one catches the
shape that actually fools people.

## Where the fleet stands, and what that implies for the default

Nine dump call sites across the fleet, none of which captures a role. Three of
them — including this facility's own `providers/compose_host.py` default of
``--no-owner --no-privileges`` — go further and strip ownership and ACLs out of
the dump as well, so they discard the evidence rather than merely omitting it.
That default is the highest-leverage defect in the set: it is the shared seam
every adopting product inherits, which is why :func:`refuse_identity_stripping`
lives here and is called from the provider.

One repaired script exists on an unmerged branch and reaches for
``pg_dumpall --globals-only``. That is the right instinct and the wrong flag:
`--globals-only` emits ``CREATE ROLE … PASSWORD 'SCRAM-SHA-256$…'``, so adopting
it as written would place password verifiers into an offsite bucket. The bundle
carries roles captured **without** password material (`--no-role-passwords`, and
:class:`RoleFact` has no field one could be written into), and login material is
installed afterwards from the product's approved secret source — restore step 5.

## What the bundle contains, and why each part is not optional

Named in :data:`COMPONENTS`, one digest each, all of them required. The
per-component digest is what lets a receipt say *which* part of the evidence a
reader is trusting, rather than one opaque hash over a tarball.

## What `dotmac_workspace` contributed, including by not having it

Workspace was read first, as the product-first reference (ADR-0006 amendment,
`AGENTS.md` rule 22). **It derives no role closure — nothing in the fleet does**,
and that absence is itself the finding: membership closure is new ground, so
this module had to be written rather than ported. Three things were taken:

1. **"A binding is a claim, not a fact."** Workspace's
   `migration_bindings.py` declares which database EFFECTS its lineage needs;
   `require_prerequisites` re-proves every one against the live catalog before
   any DDL runs, rather than trusting the Python file. That is exactly the
   relationship between a product's declared roles and this bundle: the
   descriptor states a claim, the bundle is the fact, and the claim is never
   allowed to become the source.
2. **Snapshot, then a pure decision.** Everything touching Postgres returns
   parameterised SQL; everything that decides is a pure function over a frozen
   snapshot. That split is why :func:`verify_recovery` needs no database, and
   why every required mutation below is an ordinary unit test.
3. **The direct-grant trap, by counter-example.** Workspace's isolation tests
   assert revocations through ``information_schema.table_privileges``, which
   sees only DIRECT grants — not PUBLIC, not inherited membership, not
   column-level. The kernel's own catalog gate rejects that approach in so many
   words. :class:`EffectivePrivilegeFact` is the correction, and
   :func:`verify_plane_isolation` refuses to answer without it.

Reading Workspace also fixed the size of the problem. Everything declares three
roles; the cluster has **five** — the two outbox dispatchers reach their tables
only through ``SECURITY DEFINER`` routines and therefore hold no table privilege
a naive walk would see. The measured 114 errors name all five. A closure
hand-written from the documented contract would have been short by exactly the
two nobody thinks about.

## What this module does NOT do

It runs no `pg_dump`, opens no socket, reads no credential and creates no role.
It derives the closure, decides what the bundle must contain, orders the restore,
and adjudicates typed evidence the host supplies. That is what makes every
required mutation below an ordinary unit test rather than a disposable-VM
exercise — and a control nobody has watched fail is a control nobody should
trust.

**The validator cannot manufacture a role.** A validator able to create the role
it is checking for can always make its own check pass, so the declarations a
product writes in `deploy/product.toml` are an EXPECTATION compared against the
bundle, never a source the restore can build from. Step 2 of
:data:`RESTORE_PROCEDURE` names :attr:`BundleComponent.ROLE_CLOSURE` as its only
admissible input, :func:`restore_plan` refuses to produce a plan when that
component is absent *even though the descriptor names every role*, and no
function in this package emits role DDL at all.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from enum import Enum
from typing import Any, ClassVar, Final

from .digest import Digest
from .errors import DeploymentError, SpecError
from .secrets_guard import require_no_secrets
from .version import VERSION

__all__ = [
    "COMPONENTS",
    "RECOVERY_RECEIPT_SCHEMA",
    "RECOVERY_BUNDLE_SCHEMA",
    "RESTORE_PROCEDURE",
    "BundleComponent",
    "CatalogEvidence",
    "InvariantBreach",
    "ComponentSpec",
    "DefaultPrivilegeFact",
    "Disposition",
    "ExtensionFact",
    "MembershipFact",
    "OwnershipFact",
    "PolicyFact",
    "PrivilegeFact",
    "RecoveryBundleManifestV1",
    "RecoveryNotProved",
    "RecoveryReceiptV1",
    "RestoreAttempt",
    "RestoreStep",
    "RestoreStepSpec",
    "RlsFact",
    "RoleClosure",
    "RoleFact",
    "TablespaceDecision",
    "build_manifest",
    "classify_invariant_breaches",
    "build_recovery_receipt",
    "derive_role_closure",
    "invariant_breaches",
    "load_manifest",
    "refuse_identity_stripping",
    "restore_plan",
    "EXTERNAL_ONLY_VERIFICATIONS",
    "UNDECLARED_COMPARISONS",
    "VERIFICATION_CHECKS",
    "VERIFICATION_ORDER",
    "verify_recovery",
]

RECOVERY_BUNDLE_SCHEMA: Final = "PostgresRecoveryBundle.v1"
RECOVERY_RECEIPT_SCHEMA: Final = "RecoveryReceipt.v1"

#: Roles PostgreSQL itself owns. They exist in every cluster, are not created by
#: a restore, and must not be counted as missing — but grants TO them are still
#: part of the evidence, so they are excluded from the closure's *carry*
#: obligation rather than from the evidence.
_BUILTIN_ROLE_PREFIX: Final = "pg_"

#: The pseudo-role. `GRANT … TO PUBLIC` names no catalog row.
_PUBLIC: Final = "PUBLIC"


class RecoveryNotProved(DeploymentError):
    """A restore ran and the database that came back is not the one that left.

    Distinct from :class:`~.errors.PreconditionFailed` on purpose: a precondition
    refuses before anything is mutated, whereas this is raised about a target
    that EXISTS and must now be destroyed. Reading one as the other is how a
    partial restore survives.
    """


# ── what the bundle is made of ───────────────────────────────────────────────


class BundleComponent(str, Enum):
    """The parts of one recovery bundle. All of them are required."""

    DATABASE_DUMP = "database_dump"
    ROLE_CLOSURE = "role_closure"
    ROLE_ATTRIBUTES = "role_attributes"
    MEMBERSHIPS = "memberships"
    OBJECT_OWNERSHIP = "object_ownership"
    DEFAULT_PRIVILEGES = "default_privileges"
    SCHEMA_PRIVILEGES = "schema_privileges"
    OBJECT_PRIVILEGES = "object_privileges"
    FINE_GRAINED_ACLS = "fine_grained_acls"
    ROW_SECURITY = "row_security"
    EXTENSIONS = "extensions"
    TABLESPACES = "tablespaces"
    MIGRATION_HEADS = "migration_heads"


@dataclasses.dataclass(frozen=True, slots=True)
class ComponentSpec:
    """One component, and — precisely — what its digest covers.

    ``covers`` is not documentation. A receipt cites component digests, and a
    reader deciding whether to trust a recovery needs to know what each one
    ranges over; "the bundle hashed to X" tells them nothing about which
    property X protects.
    """

    component: BundleComponent
    covers: str
    absent_means: str


COMPONENTS: Final[dict[BundleComponent, ComponentSpec]] = {
    BundleComponent.DATABASE_DUMP: ComponentSpec(
        component=BundleComponent.DATABASE_DUMP,
        covers=(
            "the custom-format archive bytes: schemas, tables, indexes, "
            "constraints, routines, data, and the ACL and POLICY entries "
            "pg_dump does emit"
        ),
        absent_means="there is nothing to restore",
    ),
    BundleComponent.ROLE_CLOSURE: ComponentSpec(
        component=BundleComponent.ROLE_CLOSURE,
        covers=(
            "the transitive set of role NAMES the source catalog actually "
            "requires — every owner, grantee, grantor, policy role and default "
            "privilege holder, closed over membership"
        ),
        absent_means=(
            "the 114-error restore: every GRANT and POLICY names a role the "
            "target does not have"
        ),
    ),
    BundleComponent.ROLE_ATTRIBUTES: ComponentSpec(
        component=BundleComponent.ROLE_ATTRIBUTES,
        covers=(
            "per-role LOGIN/INHERIT/CREATEROLE/CREATEDB/REPLICATION/BYPASSRLS "
            "and connection limit — never a password or verifier"
        ),
        absent_means=(
            "roles come back with default attributes; a NOINHERIT role silently "
            "becomes INHERIT and gains every privilege of every group it is in"
        ),
    ),
    BundleComponent.MEMBERSHIPS: ComponentSpec(
        component=BundleComponent.MEMBERSHIPS,
        covers=(
            "pg_auth_members: member, group, ADMIN option, and the PostgreSQL 16 "
            "INHERIT and SET options, which are per-membership and not derivable "
            "from the member's own rolinherit"
        ),
        absent_means=(
            "a role is present and holds nothing, because what it held came "
            "through a group"
        ),
    ),
    BundleComponent.OBJECT_OWNERSHIP: ComponentSpec(
        component=BundleComponent.OBJECT_OWNERSHIP,
        covers=(
            "owner of every schema, table, sequence, view, routine and type "
            "outside pg_catalog and information_schema"
        ),
        absent_means=(
            "everything is owned by whoever ran the restore, which is how a "
            "superuser-owned database passes a table-count check"
        ),
    ),
    BundleComponent.DEFAULT_PRIVILEGES: ComponentSpec(
        component=BundleComponent.DEFAULT_PRIVILEGES,
        covers=(
            "pg_default_acl: per-owner, per-schema, per-object-kind grants that "
            "apply to objects created AFTER the restore"
        ),
        absent_means=(
            "the restore looks correct and the next migration creates a table "
            "nobody can read"
        ),
    ),
    BundleComponent.SCHEMA_PRIVILEGES: ComponentSpec(
        component=BundleComponent.SCHEMA_PRIVILEGES,
        covers="USAGE and CREATE on every non-system schema, per grantee",
        absent_means=(
            "a role holds table privileges it cannot exercise, because it lost "
            "USAGE on the schema containing them"
        ),
    ),
    BundleComponent.OBJECT_PRIVILEGES: ComponentSpec(
        component=BundleComponent.OBJECT_PRIVILEGES,
        covers=(
            "table, sequence and function ACLs, per grantee and grantor, "
            "including WITH GRANT OPTION"
        ),
        absent_means="the plane boundary, which is expressed as privilege",
    ),
    BundleComponent.FINE_GRAINED_ACLS: ComponentSpec(
        component=BundleComponent.FINE_GRAINED_ACLS,
        covers=(
            "column-level ACLs (pg_attribute.attacl) and the row-security "
            "policies themselves: name, command, permissive flag and the roles "
            "each one names"
        ),
        absent_means=(
            "a column-level revocation vanishes while the table-level grant "
            "survives, so a role reads a column it was specifically denied"
        ),
    ),
    BundleComponent.ROW_SECURITY: ComponentSpec(
        component=BundleComponent.ROW_SECURITY,
        covers=(
            "per-table RLS ENABLE and FORCE state, separately — FORCE is what "
            "binds the owner, and an owner-bypassed policy is a policy that is "
            "present and inert"
        ),
        absent_means=(
            "pg_policies still lists policies and the table owner reads every "
            "tenant's rows"
        ),
    ),
    BundleComponent.EXTENSIONS: ComponentSpec(
        component=BundleComponent.EXTENSIONS,
        covers="installed extension names, versions and schemas",
        absent_means=(
            "a restore into an image without the extension fails partway, or "
            "succeeds against a different version"
        ),
    ),
    BundleComponent.TABLESPACES: ComponentSpec(
        component=BundleComponent.TABLESPACES,
        covers=(
            "the tablespace decision: either the declared tablespaces, or an "
            "explicit portable mapping, or an explicit 'none' — the absence of a "
            "decision is not one"
        ),
        absent_means=(
            "the restore fails on a host whose directory layout differs, and "
            "nobody knew a tablespace was load-bearing"
        ),
    ),
    BundleComponent.MIGRATION_HEADS: ComponentSpec(
        component=BundleComponent.MIGRATION_HEADS,
        covers="the migration heads the source database was actually at",
        absent_means=(
            "the restored database is at an unknown revision and the product "
            "image may be older or newer than its own schema"
        ),
    ),
}

#: Every component is required. A partial bundle is refused rather than graded,
#: because the whole failure this module exists to defeat is a partial artefact
#: that looks complete.
REQUIRED_COMPONENTS: Final[tuple[BundleComponent, ...]] = tuple(BundleComponent)


# ── typed evidence, captured from the source catalog ─────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class RoleFact:
    """One role's identity and attributes.

    **There is no password field, and that is the design.** A bundle is written
    to durable, often offsite storage; a class with nowhere to put a verifier
    cannot carry one out of the cluster by accident, and no reviewer has to
    notice. Login material is installed afterwards from the product's approved
    secret source (restore step 5), which is also the only place that knows
    whether a credential has been rotated since the dump was taken.

    ``superuser`` exists so it can be REFUSED rather than so it can be restored.
    A bundle that recreates a superuser hands whoever holds the artefact the
    cluster; a genuine superuser in the source is a finding to fix at the source.
    """

    name: str
    can_login: bool
    inherit: bool
    superuser: bool
    createrole: bool
    createdb: bool
    replication: bool
    bypassrls: bool
    connection_limit: int = -1

    def __post_init__(self) -> None:
        if not self.name:
            raise SpecError("a role fact needs a name")
        if self.superuser:
            raise SpecError(
                f"role {self.name!r} is SUPERUSER and a recovery bundle refuses to "
                "carry one. Restoring a superuser turns possession of the artefact "
                "into possession of the cluster, and a superuser bypasses every "
                "policy in the bundle, so the isolation the other components prove "
                "would be decorative. Fix the role at the source"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class MembershipFact:
    """One row of ``pg_auth_members``.

    ``inherit_option`` is PostgreSQL 16's per-membership INHERIT, and it is a
    separate fact from :attr:`RoleFact.inherit`. Before 16 a member's own
    ``rolinherit`` decided every membership at once; from 16 each ``GRANT role TO
    member`` carries its own ``WITH INHERIT``. Deriving one from the other is
    right on 15 and wrong on 16 — silently, and in the permissive direction.
    """

    member: str
    role: str
    admin_option: bool = False
    inherit_option: bool = True
    set_option: bool = True

    def __post_init__(self) -> None:
        if self.member == self.role:
            raise SpecError(f"role {self.member!r} cannot be a member of itself")


@dataclasses.dataclass(frozen=True, slots=True)
class OwnershipFact:
    kind: str
    identity: str
    owner: str


@dataclasses.dataclass(frozen=True, slots=True)
class PrivilegeFact:
    """One (grantor, grantee, privilege) on one object.

    ``scope`` carries ``column`` as a first-class value rather than folding a
    column ACL into its table's: a column-level revocation under a table-level
    grant is a NARROWING, and a comparison that only looks at tables sees the
    grant survive and the narrowing disappear.
    """

    scope: str
    identity: str
    grantee: str
    privilege: str
    grantor: str = ""
    grantable: bool = False

    SCOPES: ClassVar[tuple[str, ...]] = (
        "database",
        "schema",
        "table",
        "sequence",
        "function",
        "column",
    )

    def __post_init__(self) -> None:
        if self.scope not in self.SCOPES:
            raise SpecError(
                f"privilege scope {self.scope!r} is not one of {list(self.SCOPES)}"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class EffectivePrivilegeFact:
    """Whether a role EFFECTIVELY holds a privilege — the only admissible
    evidence for a revocation.

    This type exists because the obvious way to check a revocation is wrong, and
    `dotmac_workspace`'s isolation tests use the wrong way while the kernel's own
    catalog gate explicitly rejects it.

    ``information_schema.table_privileges`` lists DIRECT grants to a named
    grantee. It does not see a privilege reaching the role through ``PUBLIC``,
    through a role it is a member of, or through a column-level grant — and each
    of those is still a privilege. A revocation check built on the direct-grant
    set reports "fully revoked" for a role that can read the table, which is a
    control that passes precisely when it should fail.

    The question that matters is "does this role effectively hold it", and in
    PostgreSQL that is ``has_table_privilege(role, oid, priv) OR
    has_any_column_privilege(role, oid, priv)`` for the four privileges that can
    be granted per column, and ``has_table_privilege`` for the other three. Seven
    privileges, not the four DML ones: TRUNCATE empties the table, REFERENCES
    leaks existence and blocks deletes, and TRIGGER attaches code to it.

    :func:`verify_plane_isolation` therefore consumes this and REFUSES to fall
    back on :class:`PrivilegeFact`, because falling back would answer the
    question it was asked with the answer to a different one.
    """

    role: str
    identity: str
    privilege: str
    holds: bool
    scope: str = "table"


@dataclasses.dataclass(frozen=True, slots=True)
class FunctionSecurityFact:
    """A routine's security posture — the path a privilege walk does not see.

    Two of the fleet's five roles reach their tables ONLY through
    ``SECURITY DEFINER`` functions owned by the migration role. A closure that
    walks table ACLs alone reports those dispatchers as holding nothing, and a
    recovery that lost the function's owner or its EXECUTE grant would pass a
    table-privilege comparison and leave the outbox unable to drain.

    ``public_may_execute`` is captured explicitly because a NULL ``proacl`` is
    **not** "nobody has it" — it is the built-in default, and the default for a
    function is EXECUTE to PUBLIC. Reading a NULL ACL as empty would record the
    single most dangerous state as the safest one.
    """

    signature: str
    owner: str
    security_definer: bool
    public_may_execute: bool
    executors: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class DefaultPrivilegeFact:
    """One ``pg_default_acl`` entry — a grant on objects that do not exist yet."""

    owner: str
    schema: str
    object_kind: str
    grantee: str
    privilege: str


@dataclasses.dataclass(frozen=True, slots=True)
class PolicyFact:
    table: str
    name: str
    command: str
    roles: tuple[str, ...]
    permissive: bool = True


@dataclasses.dataclass(frozen=True, slots=True)
class RlsFact:
    """ENABLE and FORCE, kept apart.

    ``relrowsecurity`` and ``relforcerowsecurity`` are two columns because they
    are two facts. A table with policies and ENABLE but not FORCE applies nothing
    to its owner, and the owner is exactly who a restore makes everything belong
    to.
    """

    table: str
    enabled: bool
    forced: bool


@dataclasses.dataclass(frozen=True, slots=True)
class ExtensionFact:
    name: str
    version: str
    schema: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class TablespaceDecision:
    """What the bundle says about tablespaces — explicitly, always.

    ``NONE`` is a decision and is recorded as one. Silence is not: a bundle with
    no tablespace component restores fine on the host it was taken from and
    fails on the replacement host, at which point nobody can tell whether a
    tablespace was ever meant to exist.
    """

    kind: str
    mapping: tuple[tuple[str, str], ...] = ()

    KINDS: ClassVar[tuple[str, ...]] = ("none", "mapped", "declared")

    def __post_init__(self) -> None:
        if self.kind not in self.KINDS:
            raise SpecError(
                f"a tablespace decision is one of {list(self.KINDS)}, got "
                f"{self.kind!r}. There is no fourth option meaning 'nobody looked'"
            )
        if self.kind == "mapped" and not self.mapping:
            raise SpecError(
                "a 'mapped' tablespace decision carries the mapping it claims to "
                "have; an empty mapping is the 'none' decision spelled misleadingly"
            )
        if self.kind != "mapped" and self.mapping:
            raise SpecError(f"a {self.kind!r} tablespace decision carries no mapping")


@dataclasses.dataclass(frozen=True, slots=True)
class CatalogEvidence:
    """Everything read out of one database's catalog, at one moment.

    The same type describes the SOURCE (captured into the bundle) and the
    RESTORED target (read back afterwards). :func:`verify_recovery` compares two
    of them, which is why they must be one type: two shapes would let the two
    sides be read by two queries that quietly disagree.
    """

    roles: tuple[RoleFact, ...] = ()
    memberships: tuple[MembershipFact, ...] = ()
    ownership: tuple[OwnershipFact, ...] = ()
    privileges: tuple[PrivilegeFact, ...] = ()
    effective_privileges: tuple[EffectivePrivilegeFact, ...] = ()
    functions: tuple[FunctionSecurityFact, ...] = ()
    default_privileges: tuple[DefaultPrivilegeFact, ...] = ()
    policies: tuple[PolicyFact, ...] = ()
    row_security: tuple[RlsFact, ...] = ()
    extensions: tuple[ExtensionFact, ...] = ()
    schemas: tuple[str, ...] = ()
    migration_heads: tuple[str, ...] = ()
    tablespaces: TablespaceDecision = dataclasses.field(
        default_factory=lambda: TablespaceDecision(kind="none")
    )

    def role(self, name: str) -> RoleFact | None:
        for fact in self.roles:
            if fact.name == name:
                return fact
        return None

    @property
    def role_names(self) -> frozenset[str]:
        return frozenset(fact.name for fact in self.roles)


# ── the closure ──────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class RoleClosure:
    """Every role this database requires, and why each one is in the set.

    ``because`` is kept because a closure with no provenance is unreviewable: an
    operator looking at a bundle needs to see that `outbox_dispatcher` is present
    because two ACL entries name it, not because someone listed it.
    """

    required: frozenset[str]
    because: tuple[tuple[str, str], ...]
    builtin_referenced: frozenset[str] = frozenset()

    def reason_for(self, role: str) -> str:
        for name, reason in self.because:
            if name == role:
                return reason
        raise SpecError(f"{role!r} is not in this closure")


def _reference_sites(evidence: CatalogEvidence) -> list[tuple[str, str]]:
    """Every place a role NAME appears in the catalog, with the site that named it.

    This is the whole correction to `pg_dump --dbname`. The dump walks OBJECTS
    and emits the ACLs attached to them; nothing walks the ACLs and asks which
    principals they presuppose. This does.
    """
    sites: list[tuple[str, str]] = []
    for owned in evidence.ownership:
        sites.append((owned.owner, f"owns {owned.kind} {owned.identity}"))
    for grant in evidence.privileges:
        sites.append((grant.grantee, f"granted {grant.privilege} on {grant.identity}"))
        if grant.grantor:
            sites.append(
                (
                    grant.grantor,
                    f"granted {grant.privilege} on {grant.identity} to others",
                )
            )
    for default in evidence.default_privileges:
        sites.append(
            (
                default.owner,
                f"owns default privileges in schema {default.schema or '*'}",
            )
        )
        sites.append(
            (
                default.grantee,
                f"holds default {default.privilege} on future {default.object_kind}",
            )
        )
    for policy in evidence.policies:
        for named in policy.roles:
            sites.append((named, f"named by policy {policy.name} on {policy.table}"))
    for membership in evidence.memberships:
        sites.append((membership.member, f"is a member of {membership.role}"))
        sites.append((membership.role, f"has member {membership.member}"))
    # SECURITY DEFINER routines are a role reference a table-ACL walk cannot see:
    # a dispatcher that reaches its tables only through a definer function holds
    # no table privilege at all, so nothing else in this loop would name it.
    for function in evidence.functions:
        sites.append((function.owner, f"owns routine {function.signature}"))
        for executor in function.executors:
            sites.append((executor, f"may EXECUTE {function.signature}"))
    # LAST, deliberately. `derive_role_closure` keeps the FIRST reason it sees,
    # and "declared in the source catalog" is true of nearly every role while
    # explaining nothing. An operator reviewing a bundle needs to see that
    # `outbox_dispatcher` is present because a routine grants it EXECUTE - the
    # dependency that would break if it were dropped - not that somebody listed
    # it. So a declaration is the FALLBACK reason, reached only by a role that
    # genuinely is referenced nowhere else.
    for role in evidence.roles:
        sites.append((role.name, "declared in the source catalog"))
    return sites


def derive_role_closure(evidence: CatalogEvidence) -> RoleClosure:
    """The roles ``evidence`` requires, closed over membership.

    Two passes, and the second is the one a hand-written list always misses. A
    role that appears nowhere in any ACL can still be required, because a role
    that IS named holds what it holds through being a member of it. Restoring
    the named roles and not their groups produces a cluster where every identity
    exists and none of them can do anything — which passes a
    "do the roles exist?" check and fails in production.

    Built-in ``pg_*`` roles and ``PUBLIC`` are recorded separately: grants to
    them are real evidence and must be restored, but they are not objects a
    bundle carries, and counting them as missing would make every bundle fail.
    """
    reasons: dict[str, str] = {}
    builtin: set[str] = set()
    for name, reason in _reference_sites(evidence):
        if not name:
            continue
        if name == _PUBLIC or name.startswith(_BUILTIN_ROLE_PREFIX):
            builtin.add(name)
            continue
        reasons.setdefault(name, reason)

    # Second pass: close over membership until nothing new appears. A membership
    # chain can be arbitrarily deep, and the fixed point is the only correct
    # depth — "one level" is a guess that is right for today's fleet.
    groups_of: dict[str, list[str]] = {}
    for membership in evidence.memberships:
        groups_of.setdefault(membership.member, []).append(membership.role)
    frontier = list(reasons)
    while frontier:
        member = frontier.pop()
        for group in groups_of.get(member, ()):
            if group == _PUBLIC or group.startswith(_BUILTIN_ROLE_PREFIX):
                builtin.add(group)
                continue
            if group not in reasons:
                reasons[group] = f"{member} inherits from it"
                frontier.append(group)

    return RoleClosure(
        required=frozenset(reasons),
        because=tuple(sorted(reasons.items())),
        builtin_referenced=frozenset(builtin),
    )


def _closure_gaps(evidence: CatalogEvidence, closure: RoleClosure) -> list[str]:
    """Roles the closure requires that the evidence carries no definition for.

    This is the 114-error restore, caught at CAPTURE time instead of at recovery
    time. A bundle whose ACLs name a role it does not define is the exact
    artefact the fleet has been producing.
    """
    defined = evidence.role_names
    return [
        f"the bundle requires role {name!r} ({closure.reason_for(name)}) and "
        "carries no definition for it — this is the shape that produced 114 "
        "missing-role errors, and it is being refused at capture rather than "
        "discovered during an incident"
        for name in sorted(closure.required - defined)
    ]


# ── the manifest ─────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class RecoveryBundleManifestV1:
    """The canonical, digest-bearing description of one bundle.

    Immutable by construction: the content is a plain document and the digest is
    taken over its canonical bytes, so a manifest cannot be edited into agreeing
    with a bundle it no longer describes without changing its own identity.
    """

    content: dict[str, Any]

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def sha256_digest(self) -> str:
        return str(Digest.of(self.canonical_bytes()))

    @property
    def product(self) -> str:
        return str(self.content["product"])

    @property
    def postgres_major(self) -> int:
        return int(self.content["postgres_major"])

    @property
    def components(self) -> tuple[BundleComponent, ...]:
        return tuple(
            BundleComponent(code) for code in sorted(self.content["components"])
        )

    def component_digest(self, component: BundleComponent) -> Digest:
        digests = self.content["components"]
        if component.value not in digests:
            raise SpecError(
                f"the manifest carries no digest for {component.value!r}. "
                f"{COMPONENTS[component].absent_means}"
            )
        return Digest.parse(
            str(digests[component.value]), where=f"components.{component.value}"
        )

    @property
    def role_closure(self) -> frozenset[str]:
        return frozenset(str(name) for name in self.content["role_closure"])

    @property
    def migration_heads(self) -> tuple[str, ...]:
        return tuple(str(head) for head in self.content["migration_heads"])

    def to_json(self) -> str:
        return self.canonical_bytes().decode("utf-8")


def build_manifest(
    *,
    product: str,
    environment: str,
    postgres_major: int,
    source_revision: str,
    captured_at_epoch: int,
    evidence: CatalogEvidence,
    component_digests: Mapping[str, str],
) -> RecoveryBundleManifestV1:
    """Build a manifest, refusing anything that is not a whole bundle.

    Three refusals, in the order a capture actually goes wrong:

    1. **A component is missing.** Every entry in :data:`COMPONENTS` must carry a
       digest. This is where "we took a dump" stops being a backup.
    2. **The closure is not covered.** The evidence names roles it does not
       define — the 114-error shape, refused before the artefact is written
       rather than after it is needed.
    3. **A superuser or a password reached the manifest.** The first is refused
       by :class:`RoleFact`; the second cannot be expressed by these types at
       all, and the document is scanned anyway, because the guard is about the
       next field somebody adds.
    """
    missing = [
        component
        for component in REQUIRED_COMPONENTS
        if component.value not in component_digests
    ]
    if missing:
        detail = "; ".join(
            f"{component.value} ({COMPONENTS[component].absent_means})"
            for component in missing
        )
        raise SpecError(
            f"the bundle is missing {len(missing)} of {len(REQUIRED_COMPONENTS)} "
            f"required component(s): {detail}. A recovery bundle is refused when "
            "it is incomplete rather than graded, because an incomplete bundle "
            "restores into a database that LOOKS restored"
        )
    unknown = sorted(set(component_digests) - set(REQUIRED_COMPONENTS))
    if unknown:
        named = [
            item.value if isinstance(item, BundleComponent) else item
            for item in unknown
        ]
        raise SpecError(f"unknown bundle component(s) {named}")

    closure = derive_role_closure(evidence)
    gaps = _closure_gaps(evidence, closure)
    if gaps:
        raise SpecError(
            f"the role closure is not covered by the bundle: {'; '.join(gaps)}"
        )

    content: dict[str, Any] = {
        "schema": RECOVERY_BUNDLE_SCHEMA,
        "foundation_version": VERSION,
        "product": product,
        "environment": environment,
        "postgres_major": int(postgres_major),
        "source_revision": source_revision,
        "captured_at_epoch": int(captured_at_epoch),
        "components": {
            component.value: str(Digest.parse(component_digests[component.value]))
            for component in REQUIRED_COMPONENTS
        },
        "role_closure": sorted(closure.required),
        "role_closure_reasons": dict(closure.because),
        "builtin_roles_referenced": sorted(closure.builtin_referenced),
        "schemas": sorted(evidence.schemas),
        "extensions": sorted(
            f"{fact.name}={fact.version}" for fact in evidence.extensions
        ),
        "tablespaces": {
            "kind": evidence.tablespaces.kind,
            "mapping": [list(pair) for pair in evidence.tablespaces.mapping],
        },
        "migration_heads": sorted(evidence.migration_heads),
        "counts": {
            "roles": len(evidence.roles),
            "memberships": len(evidence.memberships),
            "ownership": len(evidence.ownership),
            "privileges": len(evidence.privileges),
            "default_privileges": len(evidence.default_privileges),
            "policies": len(evidence.policies),
            "rls_tables": sum(1 for fact in evidence.row_security if fact.enabled),
            "rls_forced_tables": sum(
                1 for fact in evidence.row_security if fact.forced
            ),
        },
    }
    require_no_secrets(content, source="recovery bundle manifest")
    return RecoveryBundleManifestV1(content=content)


def load_manifest(payload: str | bytes) -> RecoveryBundleManifestV1:
    """Read a manifest, refusing a bundle that is not whole.

    **This is the control that matters most, and it is not the one that looks
    most important.** Every other refusal in this module catches a missing
    piece. This one catches a *plausible whole*: an operator hands over the
    newest production `.dump`, it is a real custom-format archive, it restores
    45 tables and 23 policies, and it is not a recovery bundle. Refusing it here
    — before a target is created, on the artefact's own shape — is the only
    point at which the difference is still cheap to see.
    """
    try:
        content = json.loads(payload)
    except ValueError as exc:
        raise SpecError(
            f"the bundle manifest is not valid JSON: {exc}. A custom-format "
            "pg_dump archive is not a manifest, and if that is what was supplied "
            "then what is in hand is a database-only dump, not a recovery bundle"
        ) from exc
    if not isinstance(content, dict):
        raise SpecError("a bundle manifest is a JSON object")
    schema = content.get("schema")
    if schema != RECOVERY_BUNDLE_SCHEMA:
        raise SpecError(
            f"expected {RECOVERY_BUNDLE_SCHEMA}, got {schema!r}. A reader of v1 "
            "refuses a document it does not understand rather than interpreting "
            "the fields it recognises"
        )
    digests = content.get("components")
    if not isinstance(digests, Mapping):
        raise SpecError("the manifest carries no component digests")
    missing = [
        component for component in REQUIRED_COMPONENTS if component.value not in digests
    ]
    if missing:
        present = sorted(str(key) for key in digests)
        detail = "; ".join(
            f"{component.value} — without it, {COMPONENTS[component].absent_means}"
            for component in missing
        )
        raise SpecError(
            f"this is not a recovery bundle: it carries {present} and is missing "
            f"{len(missing)} required component(s). {detail}. Note what a "
            "database-only dump DOES do when restored — tables appear, policies "
            "appear, RLS reads as enabled — which is why the refusal is on the "
            "artefact's shape and not on how the restore looks afterwards"
        )
    for component in REQUIRED_COMPONENTS:
        Digest.parse(
            str(digests[component.value]), where=f"components.{component.value}"
        )
    for key in ("product", "postgres_major", "role_closure", "migration_heads"):
        if key not in content:
            raise SpecError(f"the manifest carries no {key!r}")
    return RecoveryBundleManifestV1(content=dict(content))


# ── the restore procedure ────────────────────────────────────────────────────


class RestoreStep(str, Enum):
    FRESH_TARGET = "fresh_target"
    RESTORE_ROLES = "restore_roles"
    RESTORE_OBJECTS = "restore_objects"
    ADJUDICATE = "adjudicate"
    INSTALL_LOGIN_MATERIAL = "install_login_material"
    PROVE_CATALOG = "prove_catalog"
    PROVE_PLANE_ISOLATION = "prove_plane_isolation"
    PROVE_REVOCATIONS = "prove_revocations"
    START_PRODUCT_IMAGE = "start_product_image"
    EMIT_RECEIPT = "emit_receipt"


@dataclasses.dataclass(frozen=True, slots=True)
class RestoreStepSpec:
    """One ordered step, its admissible inputs, and what it refuses.

    ``inputs`` is load-bearing rather than descriptive. Step 2's only admissible
    input is :attr:`BundleComponent.ROLE_CLOSURE`; the product descriptor is
    deliberately absent from it, which is the structural half of "the validator
    cannot manufacture a role".
    """

    step: RestoreStep
    order: int
    what: str
    inputs: tuple[BundleComponent, ...]
    refuses: str


RESTORE_PROCEDURE: Final[tuple[RestoreStepSpec, ...]] = (
    RestoreStepSpec(
        step=RestoreStep.FRESH_TARGET,
        order=1,
        what=(
            "create a fresh, isolated PostgreSQL cluster at the major version the "
            "bundle declares"
        ),
        inputs=(),
        refuses=(
            "a target that is not empty, not isolated, or at a different major "
            "version — a restore across majors is a migration wearing a recovery's "
            "clothes"
        ),
    ),
    RestoreStepSpec(
        step=RestoreStep.RESTORE_ROLES,
        order=2,
        what="create roles and memberships FROM THE BUNDLE and from nothing else",
        inputs=(
            BundleComponent.ROLE_CLOSURE,
            BundleComponent.ROLE_ATTRIBUTES,
            BundleComponent.MEMBERSHIPS,
        ),
        refuses=(
            "any role not present in the bundle. The product descriptor is not an "
            "input here: a validator that can create the role it is looking for "
            "can always make its own check pass"
        ),
    ),
    RestoreStepSpec(
        step=RestoreStep.RESTORE_OBJECTS,
        order=3,
        what="restore objects, ownership, ACLs, policies and data",
        inputs=(
            BundleComponent.DATABASE_DUMP,
            BundleComponent.OBJECT_OWNERSHIP,
            BundleComponent.SCHEMA_PRIVILEGES,
            BundleComponent.OBJECT_PRIVILEGES,
            BundleComponent.FINE_GRAINED_ACLS,
            BundleComponent.DEFAULT_PRIVILEGES,
            BundleComponent.ROW_SECURITY,
            BundleComponent.EXTENSIONS,
            BundleComponent.TABLESPACES,
        ),
        refuses="a dump whose digest is not the one the manifest names",
    ),
    RestoreStepSpec(
        step=RestoreStep.ADJUDICATE,
        order=4,
        what=(
            "refuse any non-zero restore and DESTROY the partial target before "
            "anything else looks at it"
        ),
        inputs=(),
        refuses=(
            "the assumption that a non-zero exit left nothing behind. The measured "
            "case exited 1 and left 45 tables and 23 policies; a wrapper that "
            "checks only the status reports a clean failure and leaves an operator "
            "a database that reads as recovered"
        ),
    ),
    RestoreStepSpec(
        step=RestoreStep.INSTALL_LOGIN_MATERIAL,
        order=5,
        what="install login material from the product's approved secret source",
        inputs=(),
        refuses=(
            "creating a role that the bundle did not carry, and taking any "
            "credential from the bundle — the bundle has none"
        ),
    ),
    RestoreStepSpec(
        step=RestoreStep.PROVE_CATALOG,
        order=6,
        what=(
            "prove ownership, grants, memberships, RLS ENABLE and FORCE, policies, "
            "default privileges, extensions and migration heads against the bundle"
        ),
        inputs=REQUIRED_COMPONENTS,
        refuses="any difference at all, in either direction",
    ),
    RestoreStepSpec(
        step=RestoreStep.PROVE_PLANE_ISOLATION,
        order=7,
        what="prove the tenant role cannot reach a platform table",
        inputs=(BundleComponent.OBJECT_PRIVILEGES,),
        refuses=(
            "a restored database where policies are present and the plane "
            "revocation is not. Under ADR-0023 the revocation IS the isolation "
            "there, so present policies are not evidence of it"
        ),
    ),
    RestoreStepSpec(
        step=RestoreStep.PROVE_REVOCATIONS,
        order=8,
        what="prove every declared revocation the platform role must be under",
        inputs=(BundleComponent.OBJECT_PRIVILEGES, BundleComponent.FINE_GRAINED_ACLS),
        refuses="a privilege the descriptor declares must not exist",
    ),
    RestoreStepSpec(
        step=RestoreStep.START_PRODUCT_IMAGE,
        order=9,
        what=(
            "start the exact product image against the restored database and pass "
            "readiness using the real application roles"
        ),
        inputs=(),
        refuses=(
            "readiness reached as a superuser or as the restoring identity. The "
            "question is whether the APPLICATION can work, and an application "
            "connecting as somebody else answers a different one"
        ),
    ),
    RestoreStepSpec(
        step=RestoreStep.EMIT_RECEIPT,
        order=10,
        what="emit a value-free recovery receipt carrying the restore wall clock",
        inputs=(),
        refuses="a receipt with no duration, and any secret value in it",
    ),
)


def restore_plan(
    spec: Any, manifest: RecoveryBundleManifestV1
) -> tuple[RestoreStepSpec, ...]:
    """The ordered procedure for restoring ``manifest``, or a refusal.

    ``spec`` is the product descriptor and is used ONLY to check that the bundle
    matches the product it claims to be for. It is deliberately not consulted for
    role names: :func:`restore_plan` refuses a bundle with no
    :attr:`BundleComponent.ROLE_CLOSURE` even when the descriptor declares every
    role the database needs, because a plan that could fall back on the
    descriptor would be a plan that can invent its own success.
    """
    for component in REQUIRED_COMPONENTS:
        manifest.component_digest(component)
    declared_product = getattr(spec, "product", None)
    if declared_product and declared_product != manifest.product:
        raise SpecError(
            f"the bundle was captured from {manifest.product!r} and the descriptor "
            f"is for {declared_product!r}. Restoring one product's database under "
            "another product's descriptor would prove nothing about either"
        )
    heads = tuple(
        sorted(getattr(getattr(spec, "migration", None), "expected_heads", ()))
    )
    if heads and heads != tuple(sorted(manifest.migration_heads)):
        raise SpecError(
            f"the bundle is at heads {list(manifest.migration_heads)} and the "
            f"descriptor expects {list(heads)}. A recovery that lands at a "
            "different revision than the image expects is not a recovery"
        )
    return RESTORE_PROCEDURE


# ── step 4: adjudicating the restore itself ──────────────────────────────────


class Disposition(str, Enum):
    """What to do with the target after the restore command returns."""

    PROCEED = "proceed"
    DESTROY = "destroy"


@dataclasses.dataclass(frozen=True, slots=True)
class RestoreAttempt:
    """What the restore command did, and what it left.

    The object counts are here because the exit status alone is not the fact
    that matters. A reader who sees ``exit_status=1`` and stops has learned that
    the restore failed; they have not learned that a database now exists which
    will pass a table count, a policy listing and an RLS check.
    """

    exit_status: int
    tables_present: int = 0
    policies_present: int = 0
    rls_tables_present: int = 0
    missing_role_errors: int = 0
    stderr_excerpt: str = ""
    duration_seconds: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class Adjudication:
    disposition: Disposition
    reasons: tuple[str, ...]

    @property
    def must_destroy(self) -> bool:
        return self.disposition is Disposition.DESTROY


def adjudicate_restore(attempt: RestoreAttempt) -> Adjudication:
    """Decide whether the restored target may be inspected or must be destroyed.

    A non-zero status is always DESTROY, and never "failed, therefore nothing
    happened". The measured Vendor CP restore exited 1 with 114 missing-role
    errors and left 45 tables, 23 of 26 policies and 16 RLS-enabled tables. A
    wrapper that returned on the status alone would report a clean failure and
    leave that database sitting there — and the next person to look at it would
    find policies present and conclude the isolation survived.
    """
    reasons: list[str] = []
    if attempt.exit_status != 0:
        left = (
            f"{attempt.tables_present} table(s), {attempt.policies_present} "
            f"policy/policies and {attempt.rls_tables_present} RLS-enabled table(s)"
        )
        reasons.append(
            f"the restore exited {attempt.exit_status} and left {left} behind. A "
            "partial target is destroyed rather than reported, because the thing "
            "it leaves reads as a recovered database"
        )
        if attempt.missing_role_errors:
            reasons.append(
                f"{attempt.missing_role_errors} missing-role error(s): the archive "
                "names principals the cluster does not have, which is what a "
                "database-only dump always produces"
            )
        return Adjudication(disposition=Disposition.DESTROY, reasons=tuple(reasons))
    if attempt.missing_role_errors:
        reasons.append(
            f"the restore exited 0 with {attempt.missing_role_errors} missing-role "
            "error(s). Zero-with-errors is the worse outcome of the two, because "
            "nothing downstream will look again"
        )
        return Adjudication(disposition=Disposition.DESTROY, reasons=tuple(reasons))
    return Adjudication(disposition=Disposition.PROCEED, reasons=())


# ── steps 6-8: proving the restored database is the same database ────────────


def _ownership_map(evidence: CatalogEvidence) -> dict[tuple[str, str], str]:
    return {(fact.kind, fact.identity): fact.owner for fact in evidence.ownership}


def _privilege_set(evidence: CatalogEvidence) -> set[tuple[str, str, str, str]]:
    return {
        (fact.scope, fact.identity, fact.grantee, fact.privilege)
        for fact in evidence.privileges
    }


def _default_privilege_set(
    evidence: CatalogEvidence,
) -> set[tuple[str, str, str, str, str]]:
    return {
        (fact.owner, fact.schema, fact.object_kind, fact.grantee, fact.privilege)
        for fact in evidence.default_privileges
    }


def _membership_map(
    evidence: CatalogEvidence,
) -> dict[tuple[str, str], MembershipFact]:
    return {(fact.member, fact.role): fact for fact in evidence.memberships}


def _rls_map(evidence: CatalogEvidence) -> dict[str, RlsFact]:
    return {fact.table: fact for fact in evidence.row_security}


def _policy_map(evidence: CatalogEvidence) -> dict[tuple[str, str], PolicyFact]:
    return {(fact.table, fact.name): fact for fact in evidence.policies}


# ── the verification registry ────────────────────────────────────────────────
#
# ONE named checker per comparison, and `verify_recovery` drives the registry
# rather than a hand-written sequence. The point is not tidiness.
#
# `BackupDataset.verify` is a CLOSED vocabulary a descriptor uses to say what a
# dataset must have verified, and `VERIFICATION_EVIDENCE` publishes the same
# seven names across a repository boundary for external receipts to claim. But
# `verify_recovery` never received that list. It computed what it knew how to
# compute, regardless of what was declared — so on the internally executed path
# the declaration and the check were never connected AT ALL, and the fact that
# three of the seven happened to go unperformed was a coincidence rather than
# the defect. A vocabulary with no consumer cannot be wrong, which is exactly
# why nobody noticed it was.
#
# Naming every comparison makes both directions checkable, and both are real:
#
#   * DECLARED AND NOT PERFORMED — a descriptor requires something nothing does.
#     `schema` was the sharpest case: `spec.py` makes it MANDATORY at parse for
#     every postgres dataset, with the refusal text stating exactly why ("a
#     restore that produces an empty database succeeds against every other
#     check"), and nothing compared `CatalogEvidence.schemas`.
#   * PERFORMED AND NOT DECLARABLE — the mirror, and the one nobody would ever
#     notice, because the check works. A descriptor cannot require it and an
#     external receipt cannot claim it, so an externally executed restore can
#     claim every declarable name while never having looked at row security.
#
#: Every comparison this module performs, by name. Adding one without a name is
#: not possible: `verify_recovery` iterates this mapping.


@dataclasses.dataclass(frozen=True, slots=True)
class _Comparison:
    """The inputs every checker receives — one shape, so the registry is uniform."""

    manifest: RecoveryBundleManifestV1
    source: CatalogEvidence
    restored: CatalogEvidence
    isolation: Sequence[Any]


def _check_schema(comparison: _Comparison) -> list[str]:
    """The declared verification `spec.py` makes MANDATORY and nothing performed.

    `CatalogEvidence.schemas` has been captured and serialized into the bundle
    manifest since the contract landed, and no comparison ever read it back. The
    descriptor's own refusal says what that costs: *"a restore that produces an
    empty database succeeds against every other check"* — a role comparison over
    two empty sets passes, an ownership comparison over two empty sets passes,
    and so does every other check here.

    An EXTRA schema is a finding too. A restore that produced more than the
    source is not a restore, and a schema nobody expected is where an old
    tenant's data comes back from.
    """
    source, restored = comparison.source, comparison.restored
    findings: list[str] = []
    mine, theirs = set(source.schemas), set(restored.schemas)
    for name in sorted(mine - theirs):
        findings.append(
            f"schema {name!r} is in the bundle and absent from the restored "
            "database. Every table, policy and grant that lived in it is gone, "
            "and a comparison over what remains cannot see the hole"
        )
    for name in sorted(theirs - mine):
        findings.append(
            f"schema {name!r} exists in the restored database and not in the "
            "source. A recovery that produced MORE than was captured is not a "
            "recovery, and an unexpected schema is where data nobody asked for "
            "comes back"
        )
    return findings


def _check_effective_privileges(comparison: _Comparison) -> list[str]:
    """The declared verification that ran only when invariants happened to exist.

    `EffectivePrivilegeFact` was consumed in exactly one place — `invariant_
    breaches`, which iterates the DECLARED isolation invariants. Two of the
    executor's three prove-steps pass no invariants at all, so they did no
    effective-privilege work whatever; and there was no source-to-restored
    comparison of the effective surface anywhere. What stood in for it was the
    direct-grant diff over `CatalogEvidence.privileges`, and this module's own
    documentation convicts that substitution: answering from direct grants alone
    *"reads as satisfied for a role holding the privilege through PUBLIC,
    through a group, or through a column-level grant. That is the one check that
    would go green exactly when the boundary is broken."*

    So this compares the EFFECTIVE surface itself, independently of whether any
    invariant was declared. An unobserved fact is a finding rather than a pass —
    silence is UNKNOWN, and a restore whose effective privileges were never read
    has not been shown to have kept them.
    """
    source, restored = comparison.source, comparison.restored
    findings: list[str] = []

    def _surface(evidence: CatalogEvidence) -> dict[tuple[str, str, str, str], bool]:
        return {
            (fact.scope, fact.identity, fact.role, fact.privilege): fact.holds
            for fact in evidence.effective_privileges
        }

    mine, theirs = _surface(source), _surface(restored)
    for key in sorted(set(mine) & set(theirs)):
        scope, identity, role, privilege = key
        if mine[key] == theirs[key]:
            continue
        if theirs[key]:
            findings.append(
                f"{role!r} EFFECTIVELY holds {privilege} on {scope} "
                f"{identity!r} after the restore and did not in the source. "
                "This is the escalation a direct-grant comparison cannot see: "
                "the privilege can arrive through PUBLIC, through a group, or "
                "through a column-level grant, and none of those appears as a "
                "grant to this role"
            )
        else:
            findings.append(
                f"{role!r} effectively lost {privilege} on {scope} "
                f"{identity!r}; the source had it"
            )
    for key in sorted(set(mine) - set(theirs)):
        scope, identity, role, privilege = key
        findings.append(
            f"the effective privilege {privilege} for {role!r} on {scope} "
            f"{identity!r} was captured in the source and NOT OBSERVED on the "
            "restored target. An unobserved privilege is unknown, not absent, "
            "and a boundary nobody read is a boundary nobody proved"
        )
    return findings


def _check_roles(comparison: _Comparison) -> list[str]:
    """Role closure and per-role attributes."""
    source, restored = comparison.source, comparison.restored
    manifest = comparison.manifest
    findings: list[str] = []
    for name in sorted(manifest.role_closure - restored.role_names):
        findings.append(
            f"role {name!r} is in the bundle's closure and absent from the "
            "restored database. Every grant and policy naming it is inert"
        )
    for fact in sorted(source.roles, key=lambda role: role.name):
        other = restored.role(fact.name)
        if other is None:
            continue
        if other.inherit != fact.inherit:
            findings.append(
                f"role {fact.name!r} has INHERIT={other.inherit} and the source "
                f"had INHERIT={fact.inherit}. Inheritance decides what a role "
                "holds through its groups, so this is a privilege change wearing "
                "the clothes of an attribute"
            )
        for attribute in (
            "can_login",
            "createrole",
            "createdb",
            "replication",
            "bypassrls",
        ):
            mine = getattr(fact, attribute)
            theirs = getattr(other, attribute)
            if mine != theirs:
                findings.append(
                    f"role {fact.name!r} has {attribute}={theirs} and the source "
                    f"had {mine}"
                )

    return findings


def _check_memberships(comparison: _Comparison) -> list[str]:
    """Memberships, including PostgreSQL 16's per-membership options."""
    source, restored = comparison.source, comparison.restored
    findings: list[str] = []
    source_members = _membership_map(source)
    restored_members = _membership_map(restored)
    for key in sorted(set(source_members) - set(restored_members)):
        findings.append(
            f"membership {key[0]!r} IN {key[1]!r} is missing. The member exists "
            "and holds nothing, which passes a role-existence check"
        )
    for key in sorted(set(restored_members) - set(source_members)):
        findings.append(
            f"membership {key[0]!r} IN {key[1]!r} exists in the restored database "
            "and not in the source — a recovery that grants MORE is still not a "
            "recovery"
        )
    for key in sorted(set(source_members) & set(restored_members)):
        mine, theirs = source_members[key], restored_members[key]
        for attribute in ("admin_option", "inherit_option", "set_option"):
            if getattr(mine, attribute) != getattr(theirs, attribute):
                findings.append(
                    f"membership {key[0]!r} IN {key[1]!r} has "
                    f"{attribute}={getattr(theirs, attribute)} and the source had "
                    f"{getattr(mine, attribute)}. From PostgreSQL 16 this option "
                    "is per-membership and cannot be derived from the member's "
                    "own rolinherit"
                )

    return findings


def _check_ownership(comparison: _Comparison) -> list[str]:
    """Object ownership."""
    source, restored = comparison.source, comparison.restored
    findings: list[str] = []
    source_owners = _ownership_map(source)
    restored_owners = _ownership_map(restored)
    for key in sorted(set(source_owners) - set(restored_owners)):
        findings.append(
            f"{key[0]} {key[1]!r} is absent from the restored database (source "
            f"owner {source_owners[key]!r})"
        )
    for key in sorted(set(source_owners) & set(restored_owners)):
        if source_owners[key] != restored_owners[key]:
            findings.append(
                f"{key[0]} {key[1]!r} is owned by {restored_owners[key]!r} and the "
                f"source owner was {source_owners[key]!r}. Ownership drifting to "
                "the restoring identity is how a database ends up belonging to "
                "whoever ran pg_restore"
            )

    return findings


def _check_direct_privileges(comparison: _Comparison) -> list[str]:
    """The DIRECT grant set, including column-level.

    Kept, and deliberately NOT the answer to `effective_privileges`: it
    catches a lost or added grant precisely, and it cannot see a privilege
    reaching a role through PUBLIC, a group, or a column. Both run."""
    source, restored = comparison.source, comparison.restored
    findings: list[str] = []
    source_privs = _privilege_set(source)
    restored_privs = _privilege_set(restored)
    for scope, identity, grantee, privilege in sorted(source_privs - restored_privs):
        findings.append(f"{grantee!r} lost {privilege} on {scope} {identity!r}")
    for scope, identity, grantee, privilege in sorted(restored_privs - source_privs):
        findings.append(
            f"{grantee!r} holds {privilege} on {scope} {identity!r} and the source "
            "did not grant it. An extra privilege after a recovery is a security "
            "regression, not a rounding error"
        )

    return findings


def _check_default_privileges(comparison: _Comparison) -> list[str]:
    """Default privileges — nothing is wrong until the next migration."""
    source, restored = comparison.source, comparison.restored
    findings: list[str] = []
    source_defaults = _default_privilege_set(source)
    restored_defaults = _default_privilege_set(restored)
    for owner, schema, kind, grantee, privilege in sorted(
        source_defaults - restored_defaults
    ):
        findings.append(
            f"default privilege {privilege} on future {kind} in "
            f"{schema or 'every schema'} for {grantee!r} (owner {owner!r}) is "
            "missing. Nothing is wrong today; the next migration creates a table "
            "the application cannot read"
        )

    return findings


def _check_row_security(comparison: _Comparison) -> list[str]:
    """Policies and row-security state."""
    source, restored = comparison.source, comparison.restored
    findings: list[str] = []
    source_policies = _policy_map(source)
    restored_policies = _policy_map(restored)
    for key in sorted(set(source_policies) - set(restored_policies)):
        findings.append(f"policy {key[1]!r} on {key[0]!r} is missing")
    for key in sorted(set(source_policies) & set(restored_policies)):
        if source_policies[key].roles != restored_policies[key].roles:
            findings.append(
                f"policy {key[1]!r} on {key[0]!r} names roles "
                f"{list(restored_policies[key].roles)} and the source named "
                f"{list(source_policies[key].roles)}"
            )
    source_rls = _rls_map(source)
    restored_rls = _rls_map(restored)
    for table in sorted(source_rls):
        mine = source_rls[table]
        theirs = restored_rls.get(table)
        if theirs is None:
            findings.append(f"table {table!r} carries no row-security state")
            continue
        if mine.enabled and not theirs.enabled:
            findings.append(f"row-level security is not enabled on {table!r}")
        if mine.forced and not theirs.forced:
            findings.append(
                f"row-level security is not FORCEd on {table!r}. The policies are "
                "present and the table owner reads every row, which is the state "
                "that looks correct in pg_policies"
            )

    return findings


def _check_security_definer_routines(comparison: _Comparison) -> list[str]:
    """SECURITY DEFINER routines — the path a privilege walk does not see."""
    source, restored = comparison.source, comparison.restored
    findings: list[str] = []
    source_functions = {fact.signature: fact for fact in source.functions}
    restored_functions = {fact.signature: fact for fact in restored.functions}
    for signature in sorted(source_functions):
        mine = source_functions[signature]
        theirs = restored_functions.get(signature)
        if theirs is None:
            findings.append(
                f"routine {signature} is missing. A role whose only access runs "
                "through a SECURITY DEFINER routine holds no table privilege, so "
                "nothing in the privilege comparison would have reported this"
            )
            continue
        if mine.owner != theirs.owner:
            findings.append(
                f"routine {signature} is owned by {theirs.owner!r} and the source "
                f"owner was {mine.owner!r}. A SECURITY DEFINER routine executes as "
                "its owner, so its owner IS its privilege"
            )
        if mine.security_definer != theirs.security_definer:
            findings.append(
                f"routine {signature} has security_definer="
                f"{theirs.security_definer} and the source had "
                f"{mine.security_definer}"
            )
        if theirs.public_may_execute and not mine.public_may_execute:
            findings.append(
                f"routine {signature} is executable by PUBLIC and was not in the "
                "source. A restored routine with a NULL ACL is not 'granted to "
                "nobody' — the built-in default for a function is EXECUTE to "
                "PUBLIC, and for a SECURITY DEFINER routine that is an escalation"
            )

    return findings


def _check_extensions(comparison: _Comparison) -> list[str]:
    """Installed extensions and their versions."""
    source, restored = comparison.source, comparison.restored
    findings: list[str] = []
    source_ext = {(fact.name, fact.version) for fact in source.extensions}
    restored_ext = {(fact.name, fact.version) for fact in restored.extensions}
    for name, version in sorted(source_ext - restored_ext):
        findings.append(
            f"extension {name} {version} is missing or at a different version"
        )
    return findings


def _check_migration_heads(comparison: _Comparison) -> list[str]:
    """Migration heads."""
    source, restored = comparison.source, comparison.restored
    findings: list[str] = []
    if tuple(sorted(source.migration_heads)) != tuple(sorted(restored.migration_heads)):
        findings.append(
            f"migration heads are {sorted(restored.migration_heads)} and the "
            f"source was at {sorted(source.migration_heads)}"
        )
    return findings


def _check_isolation_invariants(comparison: _Comparison) -> list[str]:
    """Declared isolation invariants, classified as restore defect or drift."""
    return list(
        classify_invariant_breaches(
            source=comparison.source,
            restored=comparison.restored,
            isolation=comparison.isolation,
        )
    )


#: The order findings are reported in. Roles first, then what depends on roles:
#: a missing role makes every downstream difference derivative, and a report
#: leading with 56 missing grants sends the reader to the wrong system.
VERIFICATION_ORDER: Final[tuple[str, ...]] = (
    "roles",
    "memberships",
    "ownership",
    "direct_privileges",
    "effective_privileges",
    "default_privileges",
    "row_security",
    "security_definer_routines",
    "schema",
    "extensions",
    "migration_heads",
    "isolation_invariants",
)

VERIFICATION_CHECKS: Final[dict[str, Callable[[_Comparison], list[str]]]] = {
    "roles": _check_roles,
    "memberships": _check_memberships,
    "ownership": _check_ownership,
    "direct_privileges": _check_direct_privileges,
    "effective_privileges": _check_effective_privileges,
    "default_privileges": _check_default_privileges,
    "row_security": _check_row_security,
    "security_definer_routines": _check_security_definer_routines,
    "schema": _check_schema,
    "extensions": _check_extensions,
    "migration_heads": _check_migration_heads,
    "isolation_invariants": _check_isolation_invariants,
}

#: Declarable in a descriptor and NOT performable here. One member, and it is a
#: statement about this facility rather than about the check: `CatalogEvidence`
#: carries no row counts, nothing in this package can observe one, and a name
#: only an external executor can satisfy should SAY so rather than pass
#: silently. `spec.py` refuses it at parse for a dataset with no external
#: executor — the refusal lives in one place instead of in every descriptor.
EXTERNAL_ONLY_VERIFICATIONS: Final[frozenset[str]] = frozenset({"row_counts"})

#: Performed here and NOT declarable in a descriptor — the mirror defect, and
#: FROZEN DEBT rather than a design.
#:
#: Every one of these is a real check that works. What none of them has is a
#: name a descriptor can require or an external receipt can claim, so an
#: externally executed restore may claim every declarable verification while
#: never having looked at row security or a SECURITY DEFINER routine.
#:
#: Retiring a member means adding it to `BackupDataset.VERIFICATIONS` and to
#: `external_recovery.VERIFICATION_EVIDENCE` — the second of which is read
#: ACROSS A REPOSITORY BOUNDARY by receipt acceptance, so it is a contract
#: change and not a line in this file. That is the stated retirement condition;
#: it is not "someday".
#:
#: Ratcheted in BOTH directions by `test_deployment_foundation_recovery_bundle
#: .py`: growing this set fails, and shrinking it without moving the name into
#: the declarable vocabulary fails too.
UNDECLARED_COMPARISONS: Final[frozenset[str]] = frozenset(
    {
        "direct_privileges",
        "default_privileges",
        "row_security",
        "security_definer_routines",
        "extensions",
        "isolation_invariants",
    }
)


def verify_recovery(
    *,
    manifest: RecoveryBundleManifestV1,
    source: CatalogEvidence,
    restored: CatalogEvidence,
    isolation: Sequence[Any] = (),
) -> tuple[str, ...]:
    """Every way ``restored`` differs from ``source``. Empty means proved.

    Returns findings rather than raising so an operator sees all of them at
    once: a recovery that lost the roles lost the grants and the memberships
    too, and reporting them one per run turns one repair into five.

    Driven by :data:`VERIFICATION_CHECKS` in :data:`VERIFICATION_ORDER` rather
    than by a hand-written sequence, so every comparison this module performs
    has a NAME — which is what makes "declared but never performed" and
    "performed but never declarable" both checkable. See the registry's own
    comment for why a vocabulary nothing consumed could not be wrong.
    """
    comparison = _Comparison(
        manifest=manifest, source=source, restored=restored, isolation=isolation
    )
    findings: list[str] = []
    for name in VERIFICATION_ORDER:
        findings.extend(VERIFICATION_CHECKS[name](comparison))
    return tuple(findings)


@dataclasses.dataclass(frozen=True, slots=True)
class InvariantBreach:
    """One declared invariant that a catalogue does not satisfy.

    Structured rather than a string because the same breach has to be
    recognised in TWO catalogues - the source and the restored copy - and
    matching rendered prose to decide whether they are the same breach is how a
    classifier comes to depend on its own wording.
    """

    code: str
    role: str
    scope: str
    identity: str
    privilege: str
    reason: str

    REASONS: ClassVar[tuple[str, ...]] = ("held", "missing", "unobserved")

    @property
    def key(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.code,
            self.role,
            self.scope,
            self.identity,
            self.privilege,
            self.reason,
        )


def invariant_breaches(
    evidence: CatalogEvidence, isolation: Sequence[Any]
) -> tuple[InvariantBreach, ...]:
    """Every declared invariant ``evidence`` fails to satisfy.

    Pure, and applied to the SOURCE as readily as to the restored copy - which
    is what makes drift distinguishable from an unfaithful restore.
    """
    effective = {
        (fact.scope, fact.identity, fact.role, fact.privilege): fact.holds
        for fact in evidence.effective_privileges
    }
    breaches: list[InvariantBreach] = []
    for invariant in isolation:
        role = str(getattr(invariant, "role", ""))
        scope = str(getattr(invariant, "scope", "table"))
        code = str(getattr(invariant, "code", "invariant"))
        denied = bool(getattr(invariant, "denied", True))
        for identity in tuple(getattr(invariant, "objects", ())):
            for privilege in tuple(getattr(invariant, "privileges", ())):
                key = (scope, identity, role, privilege)
                if key not in effective:
                    reason = "unobserved"
                elif denied and effective[key]:
                    reason = "held"
                elif not denied and not effective[key]:
                    reason = "missing"
                else:
                    continue
                breaches.append(
                    InvariantBreach(
                        code=code,
                        role=role,
                        scope=scope,
                        identity=identity,
                        privilege=privilege,
                        reason=reason,
                    )
                )
    return tuple(breaches)


def _breach_message(breach: InvariantBreach, evidence: CatalogEvidence) -> str:
    if breach.reason == "unobserved":
        return (
            f"{breach.code}: nothing observed whether {breach.role!r} effectively "
            f"holds {breach.privilege} on {breach.scope} {breach.identity!r}. An "
            "invariant with no effective-privilege observation is UNPROVEN, not "
            "satisfied - and the direct-grant list is not a substitute, because "
            "it cannot see a privilege arriving through PUBLIC, through an "
            "inherited membership, or through a column grant"
        )
    if breach.reason == "missing":
        return (
            f"{breach.code}: {breach.role!r} does NOT effectively hold "
            f"{breach.privilege} on {breach.scope} {breach.identity!r}, which the "
            "descriptor requires. A plane whose own role cannot reach its tables "
            "is not isolated, it is broken"
        )
    note = ""
    if breach.identity in {fact.table for fact in evidence.policies}:
        note = (
            " The table does carry a row-security policy, and that is not the "
            "isolation: under ADR-0023 the revocation IS the boundary on this "
            "plane, so an operator reading pg_policies after a recovery sees a "
            "control that is not there"
        )
    return (
        f"{breach.code}: {breach.role!r} effectively holds {breach.privilege} on "
        f"{breach.scope} {breach.identity!r} and the descriptor declares it "
        f"revoked.{note}"
    )


def classify_invariant_breaches(
    *,
    source: CatalogEvidence,
    restored: CatalogEvidence,
    isolation: Sequence[Any],
) -> tuple[str, ...]:
    """Declared invariants the restored copy fails, saying WHICH SIDE is wrong.

    A rehearsal is a drift detector as well as a recovery proof, and the bundle
    cannot tell the two apart by itself: a restored copy that violates a
    declared invariant has either been restored unfaithfully, or been restored
    perfectly from a production database that is already wrong. Those have
    opposite remedies, and an operator who guesses spends the incident debugging
    the wrong system.

    Comparing the restored copy against the SOURCE catalogue separates them, and
    it is nearly free because a verification already holds both:

    - the breach is in the restored copy only -> **RESTORE DEFECT**. The bundle
      or the restore lost something. Fix the recovery path.
    - the breach is in BOTH -> **SOURCE DRIFT**. The restore is faithful and
      production does not match its own declared contract. Fix production; the
      recovery path is exonerated.

    Measured instance, 2026-08-30: a Platform CP rehearsal found `platform_api`
    holding DELETE on a delivery-target table in the restored copy. Checking
    production found the same permission there, so it was real drift - a
    revocation the declared contract requires whose migration has never run -
    and not an unfaithful restore.

    **Both classes still fail the proof.** A faithfully restored database that
    violates its own invariants has not proved isolation; the label changes
    where the operator looks, never whether the receipt is PROVED. Without that,
    the tempting repair is to relax the bundle so the check passes.
    """
    drifted = {breach.key for breach in invariant_breaches(source, isolation)}
    findings: list[str] = []
    for breach in invariant_breaches(restored, isolation):
        message = _breach_message(breach, restored)
        if breach.key in drifted:
            findings.append(
                f"SOURCE DRIFT - {message} The SOURCE catalogue shows the same "
                "breach, so the restore is faithful and the production database "
                "does not match its declared contract. Fix production; do not "
                "relax the bundle to make this pass"
            )
        else:
            findings.append(
                f"RESTORE DEFECT - {message} The source catalogue does NOT show "
                "this breach, so it was introduced by the bundle or the restore"
            )
    return tuple(findings)


def verify_plane_isolation(
    restored: CatalogEvidence, isolation: Sequence[Any]
) -> tuple[str, ...]:
    """Steps 7 and 8 against ONE catalogue, checked on EFFECTIVE privilege.

    Use :func:`classify_invariant_breaches` when both catalogues are in hand -
    it says whether a breach is a restore defect or production drift, which this
    cannot. This remains for the single-catalogue case, such as auditing a live
    database with no bundle.

    **It refuses to answer from the direct-grant set.** Every invariant must be
    covered by an :class:`EffectivePrivilegeFact`, and an uncovered invariant is
    a finding rather than a pass. That is not fastidiousness: a revocation
    checked against direct grants alone reads as satisfied for a role holding
    the privilege through ``PUBLIC``, through a group it inherits, or through a
    column-level grant - so the check would go green exactly when the boundary
    is broken, which is worse than having no check because it is reported as
    one.
    """
    return tuple(
        _breach_message(breach, restored)
        for breach in invariant_breaches(restored, isolation)
    )


# ── step 10: the receipt ─────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class RecoveryReceiptV1:
    """The value-free record of one proved recovery."""

    content: dict[str, Any]

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def sha256_digest(self) -> str:
        return str(Digest.of(self.canonical_bytes()))

    @property
    def proved(self) -> bool:
        return bool(self.content["proved"])

    @property
    def restore_duration_seconds(self) -> int:
        return int(self.content["restore_duration_seconds"])

    @property
    def bundle_digest(self) -> str:
        return str(self.content["bundle_digest"])

    @property
    def findings(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.content["findings"])

    def to_json(self) -> str:
        return self.canonical_bytes().decode("utf-8")


def build_recovery_receipt(
    *,
    manifest: RecoveryBundleManifestV1,
    adjudication: Adjudication,
    findings: Sequence[str],
    restore_duration_seconds: int,
    readiness_role: str,
    readiness_passed: bool,
    image_digest: str,
    proved_at_epoch: int,
) -> RecoveryReceiptV1:
    """Emit the receipt, refusing one without a wall clock.

    ``restore_duration_seconds`` is required and is not decoration. For a
    recovery bundle it is the number that decides whether the procedure is
    usable at all: a bundle proved to restore in twenty minutes and a bundle
    proved to restore in six hours are both PROVED and are different facts, and
    only one of them can be part of a recovery-time claim. A receipt that omits
    it lets a dashboard show a green tick against a procedure nobody could
    execute inside an outage.

    The receipt carries names, counts, digests and durations — never a value.
    """
    if restore_duration_seconds < 0:
        raise SpecError("a restore duration cannot be negative")
    proved = not adjudication.must_destroy and not findings and readiness_passed
    content: dict[str, Any] = {
        "schema": RECOVERY_RECEIPT_SCHEMA,
        "foundation_version": VERSION,
        "product": manifest.product,
        "postgres_major": manifest.postgres_major,
        "bundle_digest": manifest.sha256_digest(),
        "component_digests": dict(manifest.content["components"]),
        "proved": proved,
        "disposition": adjudication.disposition.value,
        "restore_duration_seconds": int(restore_duration_seconds),
        "readiness_role": readiness_role,
        "readiness_passed": bool(readiness_passed),
        "image_digest": image_digest,
        "roles_restored": len(manifest.role_closure),
        "findings": list(findings) + list(adjudication.reasons),
        "proved_at_epoch": int(proved_at_epoch),
    }
    require_no_secrets(content, source="recovery receipt")
    return RecoveryReceiptV1(content=content)


# ── the capture-side default that was silently discarding the evidence ───────

#: `pg_dump` flags that remove from the archive the very things a recovery
#: bundle exists to carry. Named rather than pattern-matched: this is a closed
#: set of published flags, and an entropy-style heuristic here would refuse a
#: legitimate one.
IDENTITY_STRIPPING_ARGS: Final[frozenset[str]] = frozenset(
    {"--no-owner", "-O", "--no-privileges", "-x", "--no-acl", "--no-security-labels"}
)

#: The flag that keeps role capture from becoming a credential export.
#: `pg_dumpall --globals-only` emits `CREATE ROLE … PASSWORD 'SCRAM-SHA-256$…'`,
#: so a role capture without this writes password verifiers into whatever bucket
#: the artefact is shipped to.
REQUIRED_ROLE_CAPTURE_ARG: Final = "--no-role-passwords"


def refuse_identity_stripping(args: Iterable[str], *, where: str) -> None:
    """Refuse dump arguments that discard ownership or ACLs.

    This facility's own Compose provider defaulted to
    ``("--no-owner", "--no-privileges")``, so every adopting product inherited a
    dump with no ownership and no grants in it. That default was not a small
    misconfiguration: it is upstream of the whole bundle, because a component
    cannot record evidence the capture threw away.

    ``--no-owner`` and ``--no-privileges`` are legitimate for a staging *sync*
    into a template database that supplies its own roles. They are never
    legitimate for a recovery bundle, and the difference is exactly what this
    refusal makes explicit.
    """
    offending = sorted(set(args) & IDENTITY_STRIPPING_ARGS)
    if offending:
        raise SpecError(
            f"{where}: {offending} would strip ownership and/or privileges out of "
            "the dump. A recovery bundle carries ownership and ACL evidence, and "
            "these flags delete it at capture time — after which no downstream "
            "check can notice, because the evidence never existed. If this is a "
            "staging sync into a template database rather than a recovery "
            "capture, it is not a recovery bundle and must not be labelled one"
        )
