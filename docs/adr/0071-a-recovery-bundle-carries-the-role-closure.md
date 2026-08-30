# ADR-0071 — A recovery bundle carries the role closure; a dump is a data export

- Status: accepted
- Date: 2026-08-30
- Owner: `dotmac-deployment-foundation` (`recovery.py`, extending `backup.py`)
- Supersedes nothing. Amends ADR-0070 (the facility's scope) and depends on
  ADR-0023 (the two-plane persistence contract).

## Context

`backup.py` already names four verdicts — `completed`, `verified`, `restorable`,
`proved` — and says only `proved` supports a recovery claim. On 2026-08-29
nothing in the fleet could reach it, and the reason was measured rather than
suspected.

A `pg_restore` of Vendor CP's newest production backup into a disposable
`--network none` PostgreSQL 16 container exited **1** with **114 missing-role
errors**: `app_admin` 56, `platform_api` 34, `app_user` 20, `outbox_dispatcher`
2, `platform_outbox_dispatcher` 2. The archive's TOC held 55 ACL entries, 26
POLICY entries and **zero role objects**. `pg_dump --dbname` captures the grants
and the policies; it never captures the roles they name.

The failure is not the dangerous part. That run **left 45 tables, 23 of the 26
policies and 16 RLS-enabled tables** behind — a database that reads as restored.
Every object was owned by the restoring superuser, no role existed, and
therefore no grant and no revocation existed either. Under ADR-0023 the
revocation across the plane boundary **is** the isolation, so an operator who
checks `pg_policies` after such a recovery sees the control and does not have
it. A policy naming a role that is not in the cluster restricts nobody.

The same shape is fleet-wide. Nine dump call sites; none captures a role. Three
of them — including this facility's own Compose provider, whose
`pg_dump_extra_args` defaulted to `("--no-owner", "--no-privileges")` — go
further and strip ownership and ACLs out of the archive, so they discard the
evidence rather than merely omitting it. ERP's `restore_from_backup.py` extracts
only `COPY` blocks and drops DDL and GRANT entirely. One repaired script exists
on an unmerged branch and reaches for `pg_dumpall --globals-only`, which emits
`CREATE ROLE … PASSWORD 'SCRAM-SHA-256$…'` — the right instinct and a flag that
would ship password verifiers to an offsite bucket.

Platform CP cannot issue its first authorization receipt, ERP cannot deploy and
Sub cannot cut over until a database can be *proved* restorable.

## Decision

**A recovery bundle is an immutable, containing artefact, and anything less is a
data export rather than a backup.**

`PostgresRecoveryBundleV1` extends `dotmac_deployment_foundation.backup` — it is
not a new package and it does not independently repair the four scripts. Thirteen
components, every one required, each with its own digest and a statement of what
that digest covers: the custom-format dump; the role and membership closure
**derived from the source catalog**; role attributes and PostgreSQL 16's
per-membership `INHERIT` state; object ownership; default, schema and object
privileges; row- and column-level ACL evidence; RLS `ENABLE` *and* `FORCE`;
extensions and versions; an explicit tablespace decision; migration heads.

**No passwords, no password hashes, no superusers, no secret values.** This is
structural, not a scan: `RoleFact` has no field a verifier could be written
into, and a `superuser=True` fact is refused at construction. Login material is
installed after the restore from the product's approved secret source, which is
also the only place that knows whether a credential rotated since capture.

**A product's declarations are a claim, never a source.** `[database]` in
`deploy/product.toml` declares typed roles, expected schemas and isolation
invariants; `recovery.verify_recovery` compares them against the bundle. Nothing
converts a declaration into role DDL. Step 2 of the restore procedure names the
bundle's `role_closure` as its only admissible input, `restore_plan` refuses a
bundle without it *even when the descriptor names every role*, and no module in
the facility contains role DDL at all.

**Isolation is proven against effective privilege.** `has_table_privilege(...)
OR has_any_column_privilege(...)`, over all seven table privileges, in both
directions. Never `information_schema.table_privileges`.

**Ten ordered restore steps**, of which the fourth is the one a wrapper gets
wrong: refuse any non-zero restore and **destroy the partial target**. The
tenth emits a value-free receipt carrying the restore **wall clock**.

**Retention always preserves the newest PROVED bundle regardless of age**, and
keeps an existing `data_export` until that product has a newer PROVED bundle.

**A rehearsal is also a drift detector.** A restored copy that violates a
declared invariant has either been restored unfaithfully, or restored perfectly
from a production database that is already wrong. Those have opposite remedies,
and the bundle cannot tell them apart by itself. Comparing the restored copy
against the source catalogue does, and it is nearly free because a verification
already holds both: a breach in the restored copy only is a **RESTORE DEFECT**;
a breach in both is **SOURCE DRIFT**. Both still fail the proof — the label
changes where the operator looks, never whether the receipt is PROVED, because
otherwise the cheap repair is to relax the bundle until the check passes.

**Counts are observations, never gates.** The manifest records role, privilege,
policy and RLS counts because a reader wants them. Nothing compares them. A
grant matrix is a good invariant and a poor assertion: `app_admin 315 /
app_user 62 / platform_api 164` changes with every migration, so pinning it
produces a gate that fails on correct work. The gate is the property — the
tenant role cannot reach platform tables, the platform role holds its required
revocations — which is what steps 7 and 8 already say. Privilege *fidelity* is
still checked, as a set difference between source and restored, which is a
different thing from a total.

## Confirmation, on the dataset that failed

A Platform CP rehearsal on 2026-08-30, run by hand against real production data
rather than as a fourth script:

- with cluster globals restored first, `pg_restore` exits **0 with zero errors**
  — against 114 without, on the same dataset;
- 5/5 roles present; **26** policies (the failed restore left 23); 16 tables with
  RLS forced; single `app_admin` ownership;
- `--no-role-passwords`, so no secret material moved; container destroyed;
  production healthy throughout.

That confirms the role-prelude design this bundle is built around. It also
produced the drift case above: the restored copy showed `platform_api` holding
DELETE on `licence_delivery_targets`, and production showed the same — real
drift against ADR-0011 §4 and hard rule 18, from a revocation whose revision has
never run in production. Not a restore defect.

## Alternatives considered

**Fix the nine scripts.** Rejected. It is nine independent repairs with nine
independent regressions, and it produces no artefact anyone can point a gate at.
The Compose provider default is the highest-leverage single site precisely
because it is shared; the rest is a contract, not a script.

**`pg_dumpall --globals-only`, as the unmerged ERP branch has it.** Rejected as
written. It is cluster-wide, it needs superuser, and it emits SCRAM verifiers.
Adopted with `--no-role-passwords`, and only as one component's capture.

**Declare the roles and create them at restore.** Rejected, and this is the
load-bearing rejection. A validator that can create the role it is checking for
can always make its own check pass, and the fleet's declarations are already
short: everything documents three roles and the cluster has five, because the
two outbox dispatchers reach their tables only through `SECURITY DEFINER`
routines. The measured 114 errors name all five. A hand-written list would have
been short by exactly the two nobody thinks about.

**Grade an incomplete bundle rather than refusing it.** Rejected. The whole
failure mode is a partial artefact that reads as complete.

## Product-first reference

`dotmac_workspace` was read before designing (ADR-0006 amendment; `AGENTS.md`
rule 22). **It derives no role closure — nothing in the fleet does**, and that
absence is the finding: membership closure had to be written rather than ported.
Three things were taken:

1. *"A binding is a claim, not a fact."* Workspace's `migration_bindings.py`
   declares database EFFECTS; `require_prerequisites` re-proves every one against
   the live catalog before DDL rather than trusting the Python file. That is the
   relationship between a declared role and the bundle.
2. *Snapshot, then a pure decision* (kernel `migrations/catalog.py`). Everything
   touching Postgres returns parameterised SQL; everything that decides is pure
   over a frozen snapshot. It is why `verify_recovery` needs no database and why
   the mutation matrix is ordinary unit tests.
3. *The direct-grant trap, by counter-example.* Workspace's isolation tests use
   `information_schema.table_privileges`; the kernel's own gate rejects that
   approach in so many words. `EffectivePrivilegeFact` is the correction.

## Consequences

- A product adopting this declares `[database]`, and its Compose backup is then
  refused if it carries `--no-owner`/`--no-privileges`.
- Adoption order: Platform CP first (its restore proof gates the issuer) → ERP
  rebuilds #421 against this contract → Sub after CRM poller containment → SON
  replaces its inherited ERP wrapper with its own descriptor.
- Existing dump files stay labelled `data_export`. None is deleted until its
  product has a newer PROVED bundle.
- The bootstrap cycle is broken by artefact identity, not by a second framework:
  the Foundation `0.3.0a1` candidate wheel is built once in protected CI and
  preserved **by digest** without publication or tag; those exact bytes serve
  the isolated Platform CP restore proof, then Controller-driven Lane 3; the
  identical stored bytes are published without rebuilding; read-back and tagging
  happen only after 16/16 `executed_passed`.

## Enforcement

`tests/unit/test_deployment_foundation_recovery_bundle.py` — the mutation
matrix, each control observed failing; the role-DDL structural guard with its
sensitivity proof *and* a proof that it does not read its own documentation.
`AGENTS.md` rule 43.
