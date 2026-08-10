# Tenancy characterization — ERP (E8) and Sub (S7)

**As of:** 2026-08-10
**Starter:** `21b735c` (kernel `0.1.0a33`)
**ERP:** `fed1bc7e` (`origin/main`)
**Sub:** `d05262798` (`origin/dev`)

Step 1 of Phase 4 in `docs/superpowers/plans/2026-07-18-existing-product-adoption.md`:
*catalog every organization-scoped, global, subtype, projection and integration
table*. Written because E8/S7 is the single gate on **all** kernel persistence
adoption in both products — `idempotency`, `audit`, `messaging` storage and
`models` are each classified `defer-db` behind it in their own adoption ledgers.

An inventory is not a mandate. Nothing here authorises a schema change.

## The headline: the two products are not at the same stage

The adoption plan (2026-07-18) treats ERP and Sub as parallel tenancy programs.
They are not, and have not been for some time.

| | ERP (E8) | Sub (S7) |
|---|---|---|
| Tenancy model today | `organization_id`, application- **and** RLS-enforced | one operator tenant, **already provisioned at boot** |
| `tenant_id` in the model layer | 0 of 398 tables | 1 table (`domain_settings`, arrived 2026-08-09) |
| Deciding ADR | none written | **ADR-0009, `proposed`**, owner Michael |
| Kernel `Tenant` table | absent, unplanned | absent, but ADR-0009 admits `Tenant`/`TenantDomain` |
| Realistic gate | a migration program | **an ADR acceptance** |

**Sub's gate is a decision; ERP's is a program.** They should stop being
sequenced as one workstream.

## Sub — S7 is substantially built, awaiting acceptance

`docs/adr/0009-operator-tenant-bridge.md` ("The operator is a tenant") is dated
2026-08-09 and still `proposed`. Its decision: Sub provisions exactly one
tenant, that tenant is the ISP operator, `dotmac_kernel.models.Tenant` is the
authoritative record, and the ledger's import allowlist is amended to admit
**exactly two** model classes — `Tenant` and `TenantDomain` — and nothing else.

It is not merely written. It is wired:

- `app/main.py:448` calls `provision_operator_tenant(db)` at boot.
- `app/services/operator_tenant.py` owns identity, idempotent provisioning and
  retrieval — deliberately no update, no delete, no "list tenants".
- `app/models/domain_settings.py:426` already stamps `tenant_id` from
  `operator_tenant_id()`.

What triggered it is worth recording: migration `507_domain_settings_scope_columns`
added the kernel's scope columns and defaulted every row to **platform** scope.
ADR-0009 says that default is wrong and was chosen without a decision —
`platform` is the deployment-wide fallback *beneath* tenant, so an operator's
settings sitting there is a mis-scoping, not a topology choice.

Sub has **zero** `ROW LEVEL SECURITY` in its migrations. That is consistent with
one tenant, but it means any kernel table Sub adopts arrives with RLS that has
never been exercised in that codebase.

## ERP — E8 is open, but it is not greenfield

### The catalog

398 tables across 37 PostgreSQL schemas, from 343 model files.

| | count |
|---|---|
| tables total | 398 |
| with `organization_id` | 303 |
| without | 95 |
| with `tenant_id` | **0** |

Largest schemas: `hr` 55, `public` 31, `perf` 24, `inv` 21, `ar` 19,
`training` 19, `fa` 15, `payroll` 15, `expense` 14.

### The 95 without `organization_id` split cleanly

- **87 have a foreign key to a parent table** and inherit scope through it —
  the same shape the kernel already handles for `PartyPerson`/`PartyOrganization`
  with an `EXISTS`-based policy joined through the FK. No new mechanism needed.
- **8 are standalone** (no org column, no FK) and each needs an explicit
  global-or-scoped ruling:

  | table | reading |
  |---|---|
  | `core_fx.currency` | global reference |
  | `core_org.bank_directory` | global reference |
  | `core_org.pfa_directory` | global reference |
  | `public.infrastructure_alert` | operational, global |
  | `public.infrastructure_health_status` | operational, global |
  | `public.scheduled_tasks` | operational, global |
  | `public.permissions` | **name collides with a kernel concept** |
  | `public.roles` | **collides exactly with kernel `roles`** — already recorded in ERP's ledger |

  Only two of the eight are contentious, and both are the identity/RBAC
  collisions the ledger already classifies `prohibited`.

### Isolation is already two layers, and they can disagree

ERP does not rely on application filters alone. `app/db/session_context.py`
composes two independent mechanisms:

1. **A SQLAlchemy ORM listener** keyed on `session.info["organization_id"]`.
2. **PostgreSQL RLS** keyed on the `app.current_organization_id` GUC, with an
   `app.bypass_rls` escape and a `get_current_organization_id()` SQL function.

Its own docstring records the hazard: priming only the ORM layer on an
RLS-enabled schema causes **silent zero-row reads**. `prime_session` is
therefore documented as not-for-Celery, with `session_for_org` as the entry
point that sets both.

This is materially different from the 2026-07-18 plan's premise that ERP is
"application-enforced `organization_id`". E8 is finishing an in-flight RLS
migration, not starting one.

### The concrete divergence E8 must decide

| | ERP | kernel |
|---|---|---|
| GUC | `app.current_organization_id` | `app.current_tenant` |
| accessor | `get_current_organization_id()` | `app_current_tenant_id()` |
| bypass | `app.bypass_rls` = `'true'` | none — `FORCE` and least-privilege roles |
| policy name | `<table>_tenant_isolation` | `<table>_tenant_isolation` |

The policy naming already matches. The GUC, the accessor and — most
significantly — the **existence of a bypass switch** do not. A session-settable
`app.bypass_rls` is not something the kernel's model has, and reconciling it is
a security decision, not a rename.

### RLS coverage is unmeasurable from source, and that is the finding

`alembic/versions/add_rls_policies.py` enables RLS by querying
`information_schema` **at migration time** for tables carrying
`organization_id` across a fixed 16-schema list, then looping. Fourteen other
migrations apply RLS the same loop-driven way.

Two consequences:

1. **It is a point-in-time sweep.** A table created after a sweep gets nothing;
   the sweep never re-runs. The model layer now spans 37 schemas against that
   migration's list of 16.
2. **Static analysis cannot determine current coverage.** Which tables are
   protected depends on what existed when each sweep ran. I attempted a
   source-derived coverage number and discarded it as unreliable rather than
   publish it.

The only sound measurement is against a live migrated database
(`pg_class.relrowsecurity`, `pg_policies`). **ERP has no such test.** The
starter's `tests/test_rls_catalog.py` audits the live catalog and fails when a
tenant-scoped table lacks RLS; ERP has no equivalent and no `pg_catalog`
introspection anywhere in `app/` or `tests/`.

So today ERP cannot answer *"which of my 303 org-scoped tables are actually
protected?"* without someone opening a psql session.

## What this implies for sequencing

1. **Split the workstream.** Sub's S7 is an acceptance decision on ADR-0009
   plus finishing the `domain_settings` scope correction. ERP's E8 is a
   multi-slice migration program. Running them as one item has been hiding how
   close Sub is.
2. **ERP's first slice should be the live-catalog RLS audit, not a migration.**
   You cannot stage, shadow or verify a tenancy cutover whose current state you
   cannot measure, and every later slice needs the same instrument to prove it
   did no harm. It is also the cheapest slice and lands on the existing
   `organization_id` model, changing no behaviour.
3. **The GUC/bypass reconciliation deserves its own decision** before any table
   family moves. It is the one place where ERP's model and the kernel's are not
   merely differently named but differently shaped.
4. **The 8 standalone tables need rulings**, and 6 of them are trivially global.
   The two that are not — `roles`, `permissions` — are identity collisions the
   ledger already classifies `prohibited`, so they are out of E8's scope.

## Not established here

- **Actual RLS coverage in ERP** — needs a live migrated database; see above.
- **Composite-FK coverage.** Whether ERP's foreign keys carry `organization_id`
  alongside the parent id determines whether a child row can reference a parent
  in another organization. Not measured; it needs the same live catalog.
- **Which of the 87 FK-inheriting tables reach a parent that is itself
  unscoped** — a chain ending at one of the 8 standalone tables inherits
  nothing.
- **Worker, task and export paths.** This pass read models and session
  plumbing, not Celery entry points. The `prime_session` docstring's warning
  implies these are the known-risky callers.
- **Sub's `platform`-scoped `domain_settings` rows** — how many exist and what
  correcting them costs.
