# ADR-0066: The database runtime is a facility products instantiate, not a singleton they share

> **Number allocation, 2026-08-26.** `0065` is allocated on the unmerged
> `feat/mobile-contract-inventory` branch. This record takes `0066` under the
> rule ADR-0061 recorded: the earlier record keeps the number.

- Status: Accepted
- Date: 2026-08-26
- Deciders: Michael
- Supersedes: none
- Extends: ADR-0006's product-first extraction amendment (`AGENTS.md` rule 22 —
  shared capabilities are ported from a production implementation, never
  invented in the kernel), ADR-0024 § 2 (a module is independently released and
  installed LOCALLY per application, never shared persistence)
- Related: ADR-0017 (adoption is the scarce resource — every kernel persistence
  facility is `defer-db` in both products' ledgers), ADR-0018 (an exemption
  states an enforceable premise, and a guard carries a sensitivity proof),
  ADR-0023 (a dual-plane module declares two persistence planes),
  `docs/ARCHITECTURE.md` § "Transaction authority",
  `packages/dotmac-kernel/EXTRACTION.toml`

## Context

"Build once" has two readings, and `dotmac_kernel.db` had been living the wrong
one. The right reading is that the reusable implementation is WRITTEN once and
INSTANTIATED independently by each product. The reading the module actually
encoded was that every application shares one running database and one session
factory — because the behaviour and the instance were the same object.

The behaviour in that module is real and hard-won. It owns the transaction
boundary, the RLS tenant priming, the pooled-connection discipline and a set of
fail-closed traps that exist because someone hit them: `conflict_savepoint`
(finding F3 — a bare `db.rollback()` discards the transaction's `SET LOCAL`
along with everything else), `resolver_session` (a resolver that inherits a
pooled connection's scope resolves a valid host to no tenant at all), and
`tenant_session` (a `dotmac_academy_app` audit command that opened a bare
`SessionLocal` and reported a clean estate against a database holding 333
question banks). None of that is starter-specific. All of it is what an
adopting product needs and does not have.

But all of it was welded to two module-level `create_engine` calls reading the
kernel's own `DATABASE_URL`, and to priming exactly one Postgres setting. A
product with its own deployment configuration, its own credentials and its own
legacy tenant GUC could not adopt the behaviour. It could only reimplement it —
and reimplementation is how a second session factory, a second commit boundary
and a second RLS-priming path grow inside an adopting product, each one a place
where the discipline written down in `db.py`'s docstrings has to be remembered
again rather than inherited.

That is not a hypothetical. ADR-0017 measured the gap already: `db` is
`defer-db` in both products' adoption ledgers, and ERP's goes further and
classifies `dotmac_kernel.db` as PERMANENTLY prohibited — not "later", but
never, for exactly this reason. A module that hardcodes one deployment's DSN
and one deployment's tenancy identity is not adoptable by a deployment that has
neither. The kernel had been shipping a facility whose most valuable content
was unreachable.

## Decision

### 1. The runtime is a class; `dotmac_kernel.db` is one instance of it

`dotmac_kernel.session_runtime.DatabaseRuntime` holds the implementation: the
engines, the session factories, the tenant scope, and every boundary
(`request_session`, `platform_request_session`, `platform_session`,
`tenant_session`, `tenant_session_by_slug`, `resolver_session`, `tenant_scope`).
It constructs no engine at import, reads no settings, and imports no web
framework. A product builds one per deployment in its own composition root,
supplying its DSNs, its credentials and its own `tenant_lookup` — the callable
that answers "which table says who the tenants are", which is ERP's
`Organization` and not the kernel's `Tenant`.

`dotmac_kernel.db` becomes the reference assembly's single instance of that
class, built from the kernel's `Settings` at import, with every public name
unchanged and still bound once at module scope. The starter goes first on
purpose: if the reference assembly cannot be expressed as one instance of the
runtime, no adopting product can be either.

The seam is framework-free deliberately. `request_session` takes a tenant id,
not a `Request`; the four lines that read tenancy off request state and carry
the annotation FastAPI needs in order to inject one stay in the product
(`dotmac_kernel.db.get_db` is the reference version of those four lines). That
is what lets a product with its own request pipeline adopt the transaction
discipline without adopting a router stack.

### 2. The eager owner stays eager

`dotmac_kernel.db` still builds its engines at module scope, so importing it
still requires a parseable `DATABASE_URL`. Making it lazy was considered and
rejected, and the reason is a guard rather than a preference.

`tests/architecture/test_kernel_imports_without_a_database.py` and
`test_packages_import_without_a_database.py` assert that a bare
`import dotmac_kernel` does not drag engine construction in behind it. The only
reason those guards can tell a module-level `from dotmac_kernel.db import ...`
from a deferred one is that ENTERING the owner costs a DSN. That inverted
assertion — "importing the owner must FAIL" — is the sensitivity proof ADR-0018
requires: it is what keeps the package-root guards honest. A lazy owner would
leave both of them passing for the wrong reason, over a package that had
quietly started constructing engines at import.

So the two modules have deliberately opposite import contracts, and they are
asserted together, as a pair, because neither statement means anything alone.

### 3. `app.current_tenant` is a schema contract, not a deployment knob

The canonical tenant setting name is NOT configurable on the runtime.

Every composed module lineage in the fleet writes its RLS policies as
`tenant_id = public.app_current_tenant_id()`, and that function reads
`current_setting('app.current_tenant', true)`.
`dotmac_kernel/migrations/verify.py` pins those exact semantics as a
prerequisite marker, checked against the live catalog before a requiring
migration runs any DDL. The name is therefore baked
into shipped migrations across repositories; it is a cross-repository schema
contract, and a deployment does not get to rename it any more than it gets to
rename a column.

The failure mode is what makes this worth a rule rather than a convention. A
runtime that primed some other name would produce a database in which every
composed module's policy silently matches nothing — and RLS fails CLOSED, so
the symptom is zero rows, not an error. Nothing raises. The application reports
an empty tenant, the operator reads it as an empty tenant, and the same
indistinguishability that made the `audit-banks` bug survive 37 commits applies
to the entire estate at once.

What IS configurable is `legacy_tenant_settings`, and it ADDS rather than
replaces: additional names primed alongside the canonical one, in the same
statement and with the same value, for tables a product has not yet moved onto
a composed module lineage. ERP's `app.current_organization_id` is the motivating
case, and its dual-GUC prime in `app/rls.py` is why the field exists at all.
One statement, not two, so a scope is never half-applied — a failure between two
separate statements would leave the canonical setting armed and a legacy one
stale, which reads as a working scope over the wrong rows.

This set is a RATCHET, not a feature. A product declares it, exposes it through
the `legacy_tenant_settings` property, and asserts in its OWN architecture test
that the count only ever falls. Each table moved onto a composed lineage brings
it closer to empty, and empty is the finished state. It must not become a
general "extra GUCs" facility holding names nobody is retiring; the runtime
refuses the canonical name as a legacy entry precisely so it can never be
spelled as though it were optional.

### 4. The tenant-scope mechanic is ported, not invented

`tenant_scope` — a transaction-local prime plus an `after_begin` listener that
re-arms every subsequent transaction — is `dotmac_erp`'s production
implementation (`app/db/session_context.py::tenant_scope_for_session` over
`app/rls.py`), ported under `AGENTS.md` rule 22 and recorded in
`packages/dotmac-kernel/EXTRACTION.toml` with its source commit. The kernel did
not design it.

ERP is the only source with a scope that survives a commit WITHOUT also
surviving the session. The kernel had the two weaker halves separately, and each
carried the other's hazard:

- A **session-level** setting (`SET`) survives commits, which is why it was
  reached for — but it also survives the session, riding the pooled connection
  out to whoever borrows it next. That hazard was real enough that the old code
  carried a reset-and-commit dance in a `finally`, and such a dance is only ever
  as good as the process surviving to run it.
- A **transaction-local** setting (`SET LOCAL`) cannot leak, because nothing
  outlives the transaction — but on its own it dies at the first commit inside
  the block, leaving the rest of a CLI loop or a worker drain running unscoped
  against a fail-closed policy. `expire_on_commit` then reloads an attribute on
  the next statement, and a row the session itself just wrote comes back as an
  `ObjectDeletedError`.

Re-arming on `after_begin` takes the safe half of each: every transaction is
scoped, and no transaction leaves a trace on the connection. There is nothing to
reset, so there is no reset that a dying process can skip. This is also the seam
for a product that owns its own session lifecycle and wants the kernel's scope
discipline without the kernel's session factory.

## What this does NOT change

Recorded so the record is not read as authorising more than it decided.

**No `ProductAssemblySpec` field was added.** The spec has no database slot
today, and adding one would be a declaration with zero consumers — the shape
this repository deletes rather than keeps (`UnitOfWork` was removed under
exactly that rule, and `test_session_authority.py` exists to stop it growing
back). A product constructs its runtime in its own composition root and passes
the bound methods where its framework wants callables; the bound methods are
built once at construction, so their identity is stable and
`dependency_overrides` keyed on them works. If an assembly ever genuinely needs
`create_app` to own engine construction, that is a field with a real consumer
and its own decision — not a slot added in advance.

**`create_app` is untouched.** Application construction, module registry order,
startup checks and lifespan hooks are exactly as they were.

**The public surface of `dotmac_kernel.db` is unchanged**, still module-level
and still bound once, so dependency identity and the existing monkeypatch seams
in the unit tests hold. `tenant_session` and `tenant_session_by_slug` keep their
observable contract; only the mechanic underneath them changed (§ 4).

**No migration, no schema change, no new setting, no new env knob.** Nothing in
the database moves, and `app.current_tenant` means what it has always meant.

## Consequences

- The kernel can now be adopted for its transaction and RLS discipline WITHOUT
  adopting its configuration, its credentials or its tenancy table. That is the
  specific blockage ADR-0017 measured, and this removes the part of it that was
  the kernel's own doing. It does not remove the tenancy-boundary gate (E8/S7),
  which is a product program and remains the binding constraint.
- ERP's ledger classification of `dotmac_kernel.db` stays correct and should
  stay: that module is the STARTER's instance and is not adoptable by anything
  else. What becomes adoptable is `dotmac_kernel.session_runtime`, which is a
  separate ledger entry with a separate answer.
- A product that adopts gets the fail-closed traps by inheritance instead of by
  rediscovery. That is the whole return on the change; a second commit boundary
  that a product wrote itself is a second place F3 has to be found again.
- The transaction authority is now two files. That is a cost, argued in
  "Enforcement" below, and it is the price of "build once, instantiate per
  product": the behaviour has to live somewhere a second deployment can
  construct, and a module that constructs a class cannot also be forbidden from
  constructing its sessions.
- `legacy_tenant_settings` introduces a compatibility surface that could rot.
  The mitigation is that the ratchet belongs to the ADOPTING product's own
  architecture test — the kernel cannot assert a count it does not own — and
  that the starter's own instance declares an empty tuple and must keep it
  empty, having no pre-lineage tables to be compatible with.

## Enforcement

`tests/architecture/test_session_authority.py` now names TWO authorities:
`dotmac_kernel/session_runtime.py` holds the implementation, and
`dotmac_kernel/db.py` is the reference assembly's single instance of it. **That
is one authority in two files, not two authorities.** The contract still forbids
what it always forbade — a THIRD place growing its own session factory —
and `test_every_declared_authority_still_exists` fails loudly if either declared
path is renamed or deleted, so the exclusion cannot silently widen into a hole.
The call allowlist remains EMPTY, and `test_allowlist_is_still_needed` keeps a
stale entry from surviving; the AST checker keeps its sensitivity self-test.

`tests/architecture/test_session_runtime_is_engine_free.py` asserts § 2's paired
import contracts in a subprocess with `DATABASE_URL` REMOVED rather than blanked
— a parseable-but-absent DSN would let a lazy engine succeed and hide the very
defect being tested. It also asserts that importing the runtime does not pull
`dotmac_kernel.db` in behind it, so a product instantiating its own runtime
cannot end up holding the reference assembly's engines as a side effect.

`tests/unit/test_session_runtime.py` pins § 3: the canonical setting is always
primed and always first, legacy names are primed alongside it in ONE statement,
declaring the canonical name as legacy is refused, a name outside the plain
Postgres identifier grammar is refused (setting names are interpolated into SQL
because `set_config` cannot bind them), and the `after_begin` listener is
installed and removed with the block, including when the block raises.

`tests/test_tenant_session_scope.py` is the Postgres canary for the behaviour
itself, because RLS is the whole point and SQLite cannot enforce it. It asserts
the scope survives a commit inside the block, is applied before the caller gets
the session, does not widen to another tenant, leaves nothing on the connection,
and — the test that documents the danger rather than the fix —
`test_a_bare_session_is_blind_not_loud`, which asserts that the BROKEN path is
silent.

`packages/dotmac-kernel/EXTRACTION.toml` records the ERP port with its source
files and commit, which is rule 22's evidence requirement; `COMPATIBILITY.md`
carries the adopting-product instructions and the two rules that travel with the
runtime.

## Alternatives rejected

**Leave `db.py` as it is and let products copy it.** This is the status quo, and
`dotmac_academy_app` already carries a fork of it. A fork is a second writer of
the same discipline that stops receiving fixes on the day it is taken; F3 was
found once and would have to be found again in every copy.

**Make `db.py` lazy so importing it is free.** It reads as a strict improvement
and is not. Two package-root import guards depend on entering the owner costing
a DSN in order to distinguish a module-level import from a deferred one (§ 2).
Laziness would leave them green over a package that had started constructing
engines at import — a guard passing for the wrong reason is worse than no guard,
because it is also a claim.

**Make the tenant setting name configurable.** It looks like the same
generalisation as the DSN and is categorically different: the DSN is a
deployment fact, the setting name is a contract compiled into shipped
migrations. A deployment that renamed it would get a database where every policy
matches nothing, reported as zero rows rather than as an error (§ 3).

**Add a `database` field to `ProductAssemblySpec` now.** A declaration with no
consumer is the shape this repository deletes, and the runtime needs no help
from `create_app` to be constructed in a composition root.
