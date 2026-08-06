# F0 — Migration & Table Collision Inventory

**Scope:** input to designing a deploy-time Alembic orchestrator that composes
kernel + assembly + module migration locations without collisions.

**Repos analysed** (all paths below are relative to `/Users/michaelayoade/Downloads/management/`):

| key | repo | role |
|---|---|---|
| `starter` | `dotmac_starter_mt` | reference assembly + `dotmac-kernel` package |
| `erp` | `dotmac_erp` | ERP data plane |
| `sub` | `dotmac_sub` | ISP/subscriber data plane |
| `vcp` | `dotmac_vendor_control_plane` | vendor control plane (kernel consumer) |

**Method:** AST parse of every `alembic/versions/*.py` (`revision` /
`down_revision` / `branch_labels` / `depends_on` / `op.create_table`) and of
every module containing `__tablename__` or `create_table`. Excluded:
`.venv`, `site-packages`, `node_modules`, `dotmac_sub/.claude/worktrees/*`
(20 throwaway branch checkouts of the same repo), and
`dotmac_sub/alembic/versions_archive/` (155 pre-squash migrations, not on the
live lineage — noted separately where relevant).

Read-only characterization. No file was modified; no migration was executed.

---

## 1. Alembic topology per repo

### 1.1 Summary

| repo | `alembic.ini` | `env.py` | version locations | revisions | heads | branch labels | `depends_on` | merge revs |
|---|---|---|---|---|---|---|---|---|
| starter | `dotmac_starter_mt/alembic.ini` | `dotmac_starter_mt/alembic/env.py` | **2** (declared in `alembic.ini`) | **15** (12 kernel + 3 assembly) | **2** | **yes** (`kernel`, `assembly`) | **yes** (1) | 0 |
| erp | `dotmac_erp/alembic.ini` | `dotmac_erp/alembic/env.py` | 1 (implicit `alembic/versions`) | **372** | 1 | no | yes (1) | 52 |
| sub | `dotmac_sub/alembic.ini` | `dotmac_sub/alembic/env.py` | 1 (implicit `alembic/versions`) | **504** | 1 | no | no | 23 |
| vcp | `dotmac_vendor_control_plane/alembic.ini` | `dotmac_vendor_control_plane/alembic/env.py` | **2** (composed **programmatically**) | **10** vendor (+ the same 12 kernel revisions, installed) | **2** (`kernel` + `vendor`) | **yes** (`vendor`) | **yes** (1) | 0 |

**Total distinct revision files across the four repos: 901**
(kernel 12, starter-assembly 3, erp 372, sub 504, vcp 10).
VCP re-uses the *same* 12 kernel revisions from
`dotmac_vendor_control_plane/.venv/.../dotmac_kernel/migrations/versions` — byte-identical
filenames to the starter's kernel dir, so they are counted once.

**Version table:** every repo uses the Alembic default `alembic_version`.
No repo sets `version_table`. ERP additionally pins
`version_table_schema="public"` and `include_schemas=True`
(`dotmac_erp/alembic/env.py:59-60,78-79`). Sub pre-creates the table itself
(`dotmac_sub/alembic/env.py:77-84`, `ensure_alembic_version_table`).
**Consequence for the orchestrator: all composed lineages share one
`alembic_version` table; a multi-head topology is represented as multiple
rows in it.**

**Deploy entrypoints (all use `upgrade heads`, plural — already multi-lineage aware):**

- starter: `dotmac_starter_mt/scripts/deploy.sh:169` — `alembic upgrade heads` in a one-off container
- erp: `dotmac_erp/scripts/deploy.sh:93` — `alembic upgrade heads`
- sub: `dotmac_sub/scripts/deploy.sh:296,558` — `alembic upgrade heads`
- vcp: `dotmac_vendor_control_plane/scripts/migrate.py:32-36` — `command.upgrade(make_alembic_config(url), "heads")`

(Note the drift: `dotmac_erp/Makefile:47,106` and `dotmac_sub/Makefile:105`
still use singular `upgrade head` for the dev path — safe only while those
repos have exactly one head.)

### 1.2 Starter — the reference two-lineage design (what the orchestrator must generalize)

Declared statically in `dotmac_starter_mt/alembic.ini:4-11`:

```ini
file_template = %%(year)d%%(month).2d%%(day).2d_%%(rev)s_%%(slug)s
version_locations = %(here)s/packages/dotmac-kernel/src/dotmac_kernel/migrations/versions %(here)s/alembic/versions
path_separator = space
```

**Kernel lineage** — `packages/dotmac-kernel/src/dotmac_kernel/migrations/versions/`,
12 revisions, strictly linear:

| revision | down_revision | branch_labels | depends_on |
|---|---|---|---|
| `0001_initial_tenant_schema` | `None` (base) | **`("kernel",)`** | `None` |
| `0002_settings_table` | `0001_initial_tenant_schema` | – | – |
| `0003_party_identity` | `0002_settings_table` | – | – |
| `0004_custom_fields` | `0003_party_identity` | – | – |
| `0005_single_email_authority` | `0004_custom_fields` | – | – |
| `0006_display_setting_domain` | `0005_single_email_authority` | – | – |
| `0007_platform_identity` | `0006_display_setting_domain` | – | – |
| `0008_outbox_inbox` | `0007_platform_identity` | – | – |
| `0009_platform_audit_inbox` | `0008_outbox_inbox` | – | – |
| `0010_tenant_entitlements` | `0009_platform_audit_inbox` | – | – |
| `0011_outbox_relay_leasing` | `0010_tenant_entitlements` | – | – |
| **`0012_platform_outbox`** (head) | `0011_outbox_relay_leasing` | – | – |

**Assembly lineage** — `dotmac_starter_mt/alembic/versions/`, 3 revisions,
a **second independent base**:

| revision | down_revision | branch_labels | depends_on | file |
|---|---|---|---|---|
| `a001_adopt_cfd` | **`None` (second base)** | **`("assembly",)`** | **`"0007_platform_identity"`** | `alembic/versions/a001_adopt_custom_field_definitions.py:42-45` |
| `a002_applied_licences` | `a001_adopt_cfd` | `None` | `None` | `alembic/versions/a002_tenant_applied_licences.py` |
| **`a003_revocation_lists`** (head) | `a002_applied_licences` | `None` | `None` | `alembic/versions/a003_tenant_revocation_lists.py:32-35` |

The design in one sentence: **two independently-owned linear lineages, each
with its own base and its own branch label, joined only by a `depends_on`
ordering pin from the dependent lineage's base into a specific kernel
revision** — not by `down_revision`. This keeps the kernel lineage
advanceable without rewriting assembly scripts, and keeps `assembly@base` /
`assembly@head` addressable for branch-aware `stamp` operations.

**Enforced by** `dotmac_starter_mt/tests/test_migration_split_rehearsals.py`
(7 rehearsals against a real scratch Postgres):

- `test_rehearsal_7_expected_heads_per_lineage` (line 415) pins the head set to
  exactly `{"0012_platform_outbox", "a003_revocation_lists"}` **and** asserts
  `kernel@head` / `assembly@head` resolve to those revisions — i.e. the branch
  labels are load-bearing, not decorative.
- `test_rehearsal_6_runtime_rollback` (line 380) contains the negative control
  that motivates the whole design: running the *old* migrator with
  kernel-only `version_locations` against a DB that has recorded the assembly
  head raises `alembic.util.exc.CommandError` ("cannot locate"). Rollback is
  therefore a **branch-aware `stamp assembly@base`** (tables preserved), never a
  downgrade.
- `test_rehearsal_3_existing_v08_adoption` / `test_rehearsal_4_adoption_drift_guard`
  cover adopting a pre-split database.

### 1.3 VCP — the same design, composed at runtime instead of statically

`dotmac_vendor_control_plane/alembic.ini:5-7` deliberately **omits**
`version_locations`; it is built in
`dotmac_vendor_control_plane/src/vendor_cp/migrations.py:27-29`:

```python
def composed_version_locations() -> str:
    return f"{kernel_versions_dir()} {VENDOR_VERSIONS}"
```

resolved through the kernel's public `dotmac_kernel.migrations.versions_dir()`
because the kernel is an installed wheel (`dotmac-kernel = "0.1.0a8"`,
`pyproject.toml:18`), not a fixed repo path. `alembic/env.py:40-41` fills it in
if absent, so the raw `alembic` CLI still composes correctly.

Vendor lineage: `v001_vendor_accounts` … `v010_delivery_hardening`, linear,
with `branch_labels = ("vendor",)` and
`depends_on = "0009_platform_audit_inbox"` on the base — **structurally
identical to the starter's assembly lineage, only the label and the pinned
kernel revision differ.** Verified by
`dotmac_vendor_control_plane/tests/migration/test_vendor_migration_rehearsals.py`
(`test_two_head_topology:169`, `test_upgrade_from_kernel_only:261`,
`test_kernel_advance_keeps_vendor_head_independent:281`).

**This is the generalization target: `depends_on`-pinned, branch-labelled,
independently-based lineages composed via `version_locations`, resolved
through a package-provided `versions_dir()` rather than a path.**

### 1.4 ERP and Sub — single-lineage monoliths, not composable as-is

Neither declares `version_locations`, neither uses branch labels. Both grew
by branch-and-merge inside one directory:

- ERP: 372 revisions, base `799a0ecebdd4`, head `20260723_driver_fleet_rbac`,
  **52 merge revisions** (multi-parent `down_revision` tuples), one `depends_on`
  (`add_banking_categorization` → `add_banking_schema`).
- Sub: 504 revisions, base `001_squashed`, head
  `462_ont_reconcile_eligibility_holds`, **23 merge revisions**, no
  `depends_on`, plus 155 archived pre-squash revisions in
  `dotmac_sub/alembic/versions_archive/`.

Sub pins its single head in tests
(`dotmac_sub/tests/test_billing_run_launch_evidence_migration.py:37`,
`dotmac_sub/tests/integration/test_migrations_423_to_head.py:389`). ERP has no
equivalent head-set guard found.

---

## 2. Revision-ID collision analysis

### 2.1 Totals and conventions

**901 revision IDs collected.**

| location | n | convention breakdown |
|---|---|---|
| `starter/kernel` | 12 | 100% `NNNN_slug` (4-digit zero-padded, e.g. `0007_platform_identity`) |
| `starter/assembly` | 3 | 100% `aNNN_slug` (e.g. `a001_adopt_cfd`) |
| `vcp` | 10 | 100% `vNNN_slug` (e.g. `v006_licences`) |
| `erp` | 372 | 287 `YYYYMMDD_slug`; 56 bare slug (`add_sync_tables`, `create_staging_tables`); 29 hash-12 (`2c732d9afaa6`) |
| `sub` | 504 | 496 `NNN_slug` (3-digit, `001`…`462`); 6 hash-12; 2 bare slug |

Four *different* conventions are in play, and two repos mix three conventions
internally. Only the kernel/assembly/vendor set uses a deliberate
**per-owner alphabetic namespace prefix** (`<none>` / `a` / `v`).

### 2.2 Actual duplicates

**ZERO actual duplicate revision IDs across all four repos.** Also zero
intra-repo duplicates.

Near-misses that do **not** collide today, by luck rather than design:

- Kernel `0001_initial_tenant_schema` vs Sub `001_squashed` — different
  zero-padding width (4 vs 3) is the *only* thing separating the two numeric
  namespaces. Literal-prefix overlap across locations: **0**. Had the kernel
  chosen 3 digits, 11 of its 12 revisions would sit inside Sub's occupied
  `001`–`462` prefix range.
- Slug-body collisions (same descriptive slug under different prefixes): **0**.

### 2.3 Risk assessment for a composed module system

| convention | collision probability for two independently-developed modules | severity |
|---|---|---|
| `NNNN_slug` / `NNN_slug` (kernel, sub) | **Near-certain.** Every module author independently starts at `0001_initial`/`001_initial`. This is the single highest-probability collision in the whole inventory. | critical |
| bare slug (erp: 56 revisions) | **High.** `add_sync_tables`, `create_staging_tables`, `add_rbac_tables`, `create_expense_tables` are exactly the names a second module would pick. | high |
| `YYYYMMDD_slug` (erp: 287) | **Moderate.** Two modules touched on the same day with the same slug (`20260124_rbac_tables`) is plausible in a monorepo-ish org. | medium |
| `aNNN_` / `vNNN_` owner-prefixed | **Low, but only by convention** — nothing prevents a second module from also claiming `a`. There is no registry of claimed prefixes. | low-medium |
| hash-12 (erp 29, sub 6) | **Negligible** (2^48 space). | none |

**What happens on an actual collision.** Alembic loads all
`version_locations` into one `ScriptDirectory`. Two files declaring the same
`revision` string produce
`alembic.util.exc.CommandError: Duplicate revision identifier '<id>'`
at *config load* — i.e. **`alembic upgrade heads`, `current`, `history` and
`stamp` all fail before touching the database.** That is a hard, loud,
deploy-blocking failure, not silent corruption — good. But:

1. It is discovered at **deploy time**, on the target host, after the image is
   built and pulled — the worst possible place to find it.
2. It is **unrecoverable without a code change**: the operator cannot
   `upgrade`, cannot `stamp`, and cannot `downgrade` past it, because every
   Alembic command needs the same `ScriptDirectory`. If the collision is
   introduced by *enabling a module* on a live deployment, the database is
   frozen against all migration activity until one module ships a renamed
   revision.
3. The `alembic_version` table has no module attribution, so after the fact
   there is no way to tell which lineage recorded which row.

**A subtler, non-erroring failure mode:** if module A's revision `X` is
removed/renamed while `X` is still recorded in `alembic_version`, Alembic
raises "Can't locate revision identified by 'X'". This is the exact failure
`test_rehearsal_6_runtime_rollback`
(`dotmac_starter_mt/tests/test_migration_split_rehearsals.py:380-405`)
reproduces as a negative control — it is the rollback/module-disable path, and
it is already known to bite.

---

## 3. Table-name collision analysis

**991 distinct schema-qualified table names** across the four repos
(starter 24, erp 423, sub 564, vcp 18).

### 3.1 Cross-repo duplicate table names — 17 bare names, 15 of them in `public`

Shape comparison is over ORM `mapped_column`/`Column` attribute names on the
owning model class. **Not one of the 17 pairs has the same shape**, with the
single exception of `role_permissions`.

| table | repos | same shape? | definitions (file:line) |
|---|---|---|---|
| **`user_credentials`** | erp, starter, sub | **NO** — 1 shared col (`password_hash`); starter has 3 cols (`party_id`,`tenant_id`,`password_hash`), erp 13 (`person_id`,`username`,…), sub 16 (`subscriber_id`,`system_user_id`,`reseller_user_id`,`radius_server_id`,…) | `dotmac_starter_mt/packages/dotmac-kernel/src/dotmac_kernel/models.py:292` (mig `…/versions/20260504_0001_initial_tenant_schema.py:185`, rebuilt `20260717_0003_party_identity.py:365`) · `dotmac_erp/app/models/auth.py:46` (mig `alembic/versions/799a0ecebdd4_initial_schema.py:69`) · `dotmac_sub/app/models/auth.py:41` |
| **`audit_events`** | erp, starter, sub | **NO** — 3 shared (`action`,`entity_id`,`entity_type`); starter 7 (`tenant_id`,`actor_party_id`,`details`,`created_at`), erp 16 (`organization_id`,`actor_person_id`,`metadata_`,`status_code`,…), sub 15 (`actor_label`,`metadata_`,…) | `…/dotmac_kernel/audit.py:22` (mig `20260504_0001_initial_tenant_schema.py:350`) · `dotmac_erp/app/models/audit.py:25` (mig `799a0ecebdd4_initial_schema.py:211`) · `dotmac_sub/app/models/audit.py:20` |
| **`roles`** | erp, starter, sub | **NO** — 1 shared (`name`); starter 3 (`tenant_id`,`slug`,`name`), erp/sub 6 (`id`,`description`,`is_active`,`created_at`,`updated_at`,`name`) | `…/dotmac_kernel/models.py:238` (mig `20260504_0001_initial_tenant_schema.py:274`) · `dotmac_erp/app/models/rbac.py:16` (migs `799a0ecebdd4_initial_schema.py:164`, `20260124_add_rbac_tables.py:29`) · `dotmac_sub/app/models/rbac.py:20` |
| **`domain_settings`** | erp, starter, sub | **NO** — 7 shared (`domain`,`key`,`value_json`,`value_text`,`value_type`,`is_secret`,`is_active`); starter adds `tenant_id`, erp adds `organization_id`+`scope`, sub adds neither | `…/dotmac_kernel/settings_models.py:56` (mig `20260717_0002_settings_table.py:55`) · `dotmac_erp/app/models/domain_settings.py:76` (mig `799a0ecebdd4_initial_schema.py:235`) · `dotmac_sub/app/models/domain_settings.py:53` |
| **`parties`** | starter, sub | **NO** — 2 shared (`display_name`,`party_type`); starter has `tenant_id`,`email`,`custom_fields`,`is_active`; sub has `status`,`merged_into_party_id`,`merge_reason`,`data_classification`,`metadata_` | `…/dotmac_kernel/models.py:129` (mig `20260717_0003_party_identity.py:285`) · `dotmac_sub/app/models/party.py:158` (mig `dotmac_sub/alembic/versions/349_party_role_foundation.py:25`) |
| **`party_roles`** | starter, sub | **NO** — 1 shared (`party_id`); starter is a 3-col join (`tenant_id`,`party_id`,`role_id`), sub is an 11-col temporal assignment (`role_key`,`role_type`,`valid_from`,`valid_until`,`status`,`source`,…) | `…/dotmac_kernel/models.py:261` (mig `20260717_0003_party_identity.py:442`) · `dotmac_sub/app/models/party.py:236` (mig `349_party_role_foundation.py:90`) |
| **`people`** | erp, starter | **NO** — starter's is a *dropped legacy* table (created `20260504_0001_initial_tenant_schema.py:154`, replaced by `parties` in `20260717_0003_party_identity.py:94`); erp's `Person` is a live 30-col model | `dotmac_erp/app/models/person.py:49` (mig `799a0ecebdd4_initial_schema.py:24`) · starter migrations only |
| **`person_roles`** | erp, starter | **NO** — same story: starter's is legacy (`20260504_0001…:302`, superseded `20260717_0003…:198`); erp's is live | `dotmac_erp/app/models/rbac.py:85` (migs `799a0ecebdd4_initial_schema.py:198`, `20260124_add_rbac_tables.py:113`) |
| **`sessions`** | erp, sub | **NO** — 11 shared; erp keys on `person_id`, sub on `subscriber_id`/`system_user_id`/`reseller_user_id`/`device_id` | `dotmac_erp/app/models/auth.py:189` (mig `799a0ecebdd4_initial_schema.py:122`) · `dotmac_sub/app/models/auth.py:214` |
| **`api_keys`** | erp, sub | **NO** — 9 shared; erp `person_id`, sub `subscriber_id`+`system_user_id` | `dotmac_erp/app/models/auth.py:220` (mig `799a0ecebdd4_initial_schema.py:148`) · `dotmac_sub/app/models/auth.py:268` |
| **`mfa_methods`** | erp, sub | **NO** — 13 shared; erp `person_id`, sub adds `failed_attempts`,`locked_until`,3 owner FKs | `dotmac_erp/app/models/auth.py:146` (mig `799a0ecebdd4_initial_schema.py:90`) · `dotmac_sub/app/models/auth.py:114` |
| **`permissions`** | erp, sub | **NO** — 6 shared, sub adds `is_ui_assignable` (near-identical) | `dotmac_erp/app/models/rbac.py:40` (migs `799a0ecebdd4_initial_schema.py:175`, `20260124_add_rbac_tables.py:58`) · `dotmac_sub/app/models/rbac.py:44` |
| **`role_permissions`** | erp, sub | **YES — identical** (`id`,`role_id`,`permission_id`) | `dotmac_erp/app/models/rbac.py:63` (migs `799a0ecebdd4_initial_schema.py:186`, `20260124_add_rbac_tables.py:87`) · `dotmac_sub/app/models/rbac.py:82` |
| **`scheduled_tasks`** | erp, sub | **NO** — 11 shared; erp stores cron as 5 columns (`cron_minute`,`cron_hour`,…), sub as one `cron_expr` string | `dotmac_erp/app/models/scheduler.py:23` (mig `799a0ecebdd4_initial_schema.py:260`) · `dotmac_sub/app/models/scheduler.py:18` |
| **`offer_versions`** | sub, vcp | **NO — ZERO shared columns.** sub: 21-col ISP catalog offer version (`offer_id`,`billing_cycle`,`sla_profile_id`,…). vcp: 5-col vendor licence offer (`offer_code`,`version`,`capability_codes`,`amount`,`currency_code`) | `dotmac_sub/app/models/catalog.py:585` · `dotmac_vendor_control_plane/src/vendor_cp/offers/models.py:26` |
| **`bank_accounts`** | erp, sub | **NO** — 4 shared; erp 36 cols (GL/Mono/reconciliation), sub 12 cols (tokenized payment method). **Defused today by ERP's `banking` schema.** | `dotmac_erp/app/models/finance/banking/bank_account.py:61` (schema `banking`) · `dotmac_sub/app/models/billing.py:1013` |
| **`project_template_task_dependency`** | erp, sub | **NO** — 4 shared; erp PK `dependency_id`+`created_at`, sub PK `id`. **Defused today by ERP's `pm` schema.** | `dotmac_erp/app/models/pm/project_template_task.py:89` (schema `pm`, mig `20260125_add_project_templates.py:115`) · `dotmac_sub/app/models/project.py:389` (mig `dotmac_sub/alembic/versions/244_phase3_expand_b_tables.py:368`) |

**Score: 17 cross-repo duplicate bare names; 16 of 17 are same-name /
different-shape (the dangerous case); 15 of 17 collide even after schema
qualification.** Only `role_permissions` is shape-identical, and only
`bank_accounts` + `project_template_task_dependency` are saved by ERP's
non-public schemas.

The concentration is telling: **the collisions are almost entirely in the
cross-cutting/kernel-shaped domain** — identity (`parties`, `party_roles`,
`people`, `person_roles`, `user_credentials`), authn (`sessions`, `api_keys`,
`mfa_methods`), authz (`roles`, `permissions`, `role_permissions`), settings
(`domain_settings`), audit (`audit_events`), scheduling (`scheduled_tasks`).
These are exactly the tables a kernel owns, and exactly the tables every
independently-grown product re-invented with an incompatible shape. Any module
system that composes ERP or Sub onto the kernel is composing **13 direct,
different-shape conflicts on the kernel's own tables.**

### 3.2 Generic names likely to collide with a FUTURE module

Exact generic single-word table names already claimed (all in `public`, all
unqualified — first module to claim wins):

| name | claimed by |
|---|---|
| `addresses` | sub |
| `invoices` | sub |
| `payments` | sub |
| `organizations` | sub |
| `subscriptions` | sub |
| `notifications` | sub |
| `customers` | erp |

Generic *tokens* already heavily used, i.e. the namespace pressure a new module
walks into (count = distinct table names in that repo containing the token):

| token | erp | starter | sub |
|---|---|---|---|
| `log` | 6 | – | 25 |
| `event` | 8 | 4 | 19 |
| `inbox` | – | 2 | 18 |
| `file` | 3 | – | 15 |
| `template` | 11 | – | 7 |
| `task` | 6 | – | 10 |
| `tag` | 8 | – | 8 |
| `document` | 7 | – | 3 |
| `job` | 5 | – | 6 |
| `contact` | – | – | 6 |
| `note` | – | – | 5 |
| `comment` | 3 | – | 4 |
| `message` | – | – | 4 |
| `import`/`export` | – | – | 6 |
| `sequence` | 2 | – | 3 |
| `attachment` | 3 | – | 2 |
| `setting` | 2 | 1 | 2 |
| `audit` | 6 | 2 | 1 |
| `outbox` | 1 | 2 | – |
| `address` | – | – | 3 |
| `categor*` | 5 | – | 1 |
| `webhook` | 1 | – | 1 |

Highest-risk *unclaimed* generic names a future module will reach for:
`settings`, `documents`, `templates`, `attachments`, `notes`, `tags`,
`events`, `jobs`, `tasks`, `files`, `comments`, `logs`, `messages`, `webhooks`,
`imports`, `exports`, `sequences`, `users`, `accounts`, `items`, `products`,
`reports`, `schedules`. **None of these bare names is currently taken and none
is currently protected.**

---

## 4. Schema-owner / namespace analysis

### 4.1 ERP: real, extensive Postgres-schema namespacing

**ERP is the only repo with meaningful namespacing.** 374 of its 423 distinct
tables live in a non-`public` schema — **38 distinct schemas**:

`hr` (84), `public` (83), `perf` (48), `expense` (34), `inv` (33), `payroll` (30),
`ar` (27), `training` (22), `sync` (19), `banking` (17), `tax` (16), `ipsas` (16),
`automation` (16), `ap` (15), `fa` (15), `forms` (14), `pm` (13), `gl` (13),
`support` (12), `proc` (12), `core_org` (12), `leave` (10), `rpt` (9),
`platform` (9), `attendance` (8), `recruit` (8), `scheduling` (8), `payments` (8),
`fleet` (7), `audit` (6), `cons` (6), `lease` (5), `core_fx` (4),
`core_config` (3), `settings` (2), `people` (2), `migration` (2), `common` (1),
`exp` (1).

Mechanism: `__table_args__ = {"schema": "<name>"}` on the model (39 model
files), with the schema created inside the migration via
`op.execute("CREATE SCHEMA IF NOT EXISTS <name>")` — e.g.
`dotmac_erp/alembic/versions/create_project_management_tables.py:25`,
`20260203_create_procurement_schema.py:27`,
`create_support_schema.py:23`, `add_saga_execution_tables.py:47`,
`20260209_add_settings_bank_directory.py:50`. Postgres ENUM types are also
schema-scoped (`Enum(TaskStatus, name="task_status", schema="pm")`).
`dotmac_erp/alembic/env.py:59-60,78-79` sets `include_schemas=True` and
`version_table_schema="public"`.

Caveats: the scheme is **not uniform** — 83 tables remain in `public`,
including every one of ERP's 13 collision-prone identity/authz tables
(`user_credentials`, `roles`, `permissions`, `sessions`, `api_keys`,
`mfa_methods`, `audit_events`, `domain_settings`, `people`, `person_roles`,
`scheduled_tasks`, `role_permissions`, `customers`). Six models explicitly
declare `"schema": "public"`. There is also naming inconsistency
(`expense` vs `exp`, `common`, `migration`) suggesting ad-hoc growth rather
than a governed registry. **No test was found enforcing schema placement.**

### 4.2 Starter, Sub, VCP: no namespacing at all

- **starter**: 24/24 tables in `public`. No `__table_args__` schema, no
  `CREATE SCHEMA`. The only `search_path` references are the deliberate
  `SET search_path = ''` hardening on `SECURITY DEFINER` functions
  (`…/versions/20260731_0011_outbox_relay_leasing.py:80,108`,
  `…/20260731_0012_platform_outbox.py:135,163`).
- **sub**: 564/564 tables in `public`. Zero `CREATE SCHEMA`.
- **vcp**: 18/18 tables in `public`. Zero `CREATE SCHEMA`.

### 4.3 Table-prefix conventions

There is **no enforced table-prefix convention in any repo.** Observable
*informal* prefixes only:

- starter uses `platform_*` (5 tables: `platform_admins`, `platform_sessions`,
  `platform_audit_events`, `platform_inbox_records`, `platform_outbox_events`)
  and `tenant_*` (`tenants`, `tenant_domains`, `tenant_entitlement_grants`,
  `tenant_applied_licences`, `tenant_revocation_lists`) — but these encode a
  *security class*, not an owning module, and nothing enforces them.
- vcp uses `licence_*` for 8 of its 18 tables — again informal.

### 4.4 Verdict

**Stated plainly: three of the four repos use no namespacing whatsoever — no
non-public schema, no table prefix, no naming registry. ERP has a real but
incomplete and ungoverned schema scheme that specifically excludes the very
tables that collide. Cross-repo table-name collision avoidance today rests
entirely on convention, and the evidence in §3.1 is that convention has
already failed 17 times.**

---

## 5. RLS / grant coverage per table

### 5.1 Starter — the strict rule, enforced dynamically

Rule (from `dotmac_starter_mt/CLAUDE.md` and `AGENTS.md` hard rule 11):
*tenant-scoped tables get `tenant_id NOT NULL` + composite uniques + RLS in
the same migration.* Enforced by
**`dotmac_starter_mt/tests/test_rls_catalog.py`**, which is explicitly not a
fixture list — it derives expectations from live `pg_class`, `pg_policies`,
`pg_constraint` and `information_schema` plus `Base.metadata`, so a future
table that forgets any of it fails CI without anyone updating a test. Backed
by 12 per-feature cross-tenant isolation canaries
(`tests/test_cross_tenant_isolation.py`, `test_party_isolation.py`,
`test_settings_isolation.py`, `test_custom_fields_isolation.py`,
`test_entitlements_isolation.py`, `test_licensing_receiver_isolation.py`,
`test_licensing_revocation_isolation.py`, `test_messaging_isolation.py`,
`test_rbac_audit_isolation.py`, `test_web_auth_isolation.py`,
`test_conflict_rls_context.py`, `test_platform_auth_denies.py`) and by
grant-specific tests (`test_outbox_dispatcher_grants.py`,
`test_platform_outbox_dispatcher_grants.py`).

All 24 starter tables:

| table | class | RLS | notes / evidence |
|---|---|---|---|
| `tenants` | platform catalog (readable) | **no RLS** | `_PLATFORM_READABLE`; `GRANT SELECT … TO app_user, platform_api` (`20260504_0001…:430`), INSERT/UPDATE/DELETE to `platform_api` only (`:437`) |
| `tenant_domains` | platform catalog (readable) | **no RLS** | same as above |
| `parties` | tenant-scoped | **yes** | ENABLE+FORCE+policy, `20260717_0003_party_identity.py:242,500` |
| `party_persons` | subtype | **yes** (EXISTS-join policy) | `_SUBTYPE_TABLES`, `20260717_0003…:496,514,540`; no own `tenant_id`, inherits via FK to `parties` |
| `party_organizations` | subtype | **yes** (EXISTS-join policy) | same |
| `party_roles` | tenant-scoped | **yes** | `_STANDARD_TENANT_TABLES`, `20260717_0003…:490-501` |
| `roles` | tenant-scoped | **yes** | `20260504_0001…:412` loop |
| `user_credentials` | tenant-scoped | **yes** | `20260504_0001…:412` loop; rebuilt `20260717_0003…:365,501` |
| `auth_sessions` | tenant-scoped | **yes** | `20260504_0001…:412`; rebuilt `20260717_0003…:402,501` |
| `audit_events` | tenant-scoped | **yes** | `20260504_0001…:412`; `GRANT SELECT, INSERT` only to `app_user`/`platform_api` (`:435,443`) — append-only by grant |
| `domain_settings` | **documented exception** | **yes**, split read/write policy pair | `_SPLIT_POLICY_EXCEPTION`; nullable `tenant_id` for platform-default rows; `20260717_0002_settings_table.py:134`, grants `:180,182` |
| `custom_field_definitions` | tenant-scoped | **yes** | assembly `a001_adopt_custom_field_definitions.py:188` |
| `tenant_entitlement_grants` | tenant-scoped | **yes** | `20260731_0010_tenant_entitlements.py:69` |
| `tenant_applied_licences` | tenant-scoped | **yes** | assembly `a002_tenant_applied_licences.py:117` |
| `tenant_revocation_lists` | tenant-scoped | **yes** | assembly `a003_tenant_revocation_lists.py:110` |
| `inbox_records` | tenant-scoped | **yes** | `20260730_0008_outbox_inbox.py:30,139` |
| `outbox_events` | tenant-scoped | **yes** | same |
| `platform_admins` | **platform catalog (private)** | **no RLS** — by design | `_PLATFORM_PRIVATE`; `20260730_0007_platform_identity.py:35,95-97` — GRANT to `platform_api`+`app_admin`, `REVOKE ALL … FROM app_user` |
| `platform_sessions` | platform catalog (private) | **no RLS** — by design | same |
| `platform_audit_events` | platform catalog (private) | **no RLS** — by design | `20260730_0009_platform_audit_inbox.py` |
| `platform_inbox_records` | platform catalog (private) | **no RLS** — by design | same |
| `platform_outbox_events` | platform catalog (private) | **no RLS** — by design | `20260731_0012_platform_outbox.py:40,105-107`; plus `SECURITY DEFINER` claim/settle functions with `SET search_path=''`, `REVOKE ALL … FROM PUBLIC`, `GRANT EXECUTE … TO platform_outbox_dispatcher` (`:188-197`) |
| `people` | **dropped legacy** | n/a | created `20260504_0001…:154`, dropped/replaced in `20260717_0003…:94` |
| `person_roles` | **dropped legacy** | n/a | created `20260504_0001…:302`, dropped/replaced in `20260717_0003…:198` |
| `alembic_version` | infra | **no RLS** — by design | `_INFRA_TABLES`, invisible to app roles |

**Documented exceptions: exactly two classes** — `domain_settings`
(nullable `tenant_id`, split read/write policies) and the platform catalog
tables (`_PLATFORM_READABLE` ∪ `_PLATFORM_PRIVATE`, grants-not-RLS). Both are
allowlisted with an inline reason in `test_rls_catalog.py:37-63`.

### 5.2 ERP — an equivalent rule exists in spirit only, and is enforced by nothing

ERP scopes by `organization_id` (present across ~240 model files). RLS exists
but is **partial and applied retroactively by sweep migrations**, not
per-table in the creating migration:

- `dotmac_erp/alembic/versions/add_rls_policies.py` (lines 32, 97-155) queries
  `information_schema` for tables carrying `organization_id` in a hardcoded
  `IFRS_SCHEMAS` list, then loops `ENABLE`/`FORCE ROW LEVEL SECURITY` + 4
  policies (select/insert/update/delete) per table.
- `dotmac_erp/alembic/versions/add_hr_rls_policies.py` (lines 24, 46-100) does
  the same for `HR_SCHEMAS = ["hr"]`.
- Per-schema creation migrations apply RLS inline for their own tables:
  `create_payroll_tables.py:559`, `create_expense_tables.py:569`,
  `create_leave_attendance_tables.py:583,602`,
  `create_performance_tables.py:700`, `create_recruit_training_tables.py:807,815`,
  `20260203_create_ipsas_schema.py:899,908`,
  `20260203_create_procurement_schema.py:578,588,603,618`,
  `20260123_add_paye_tax_tables.py:231`, `20260123_add_expense_limit_tables.py:689`,
  `20260606_add_learning_assessment_tables.py:78`, `add_audit_schema.py:152`,
  `add_saga_execution_tables.py:108`, `make_person_org_required.py:116`.

Only **15 of 372** ERP migrations touch RLS at all, and the two sweeps are
**point-in-time**: they introspect the catalog when they run, so any
`organization_id` table created *after* the sweep migration silently gets no
RLS. Critically, ERP's `public`-schema identity/authz tables (`people` aside)
are outside both `IFRS_SCHEMAS` and `HR_SCHEMAS`.

**No dynamic catalog-audit test equivalent to `test_rls_catalog.py` was found
in `dotmac_erp/tests`.** Enforcement of the ERP tenancy boundary is therefore
application-level (`get_db_with_org` / org-priming deps — see
`dotmac_erp/tests/api/test_deps_get_db_with_org.py`,
`tests/web/test_deps_org_priming.py`, `tests/db/test_session_context.py`,
`tests/api/test_no_local_get_db_proliferation.py`), not database-level.

### 5.3 Sub — no tenancy rule at all, by design

`grep tenant_id dotmac_sub/app/models/` → **0 matches across 0 files.**
`grep "ROW LEVEL SECURITY" dotmac_sub --include=*.py` → **0 matches.**

Sub is a **single-tenant data plane**: it is one ISP operator's own database.
There is no tenant column and no RLS, so there is no rule to enforce and no
equivalent test. In the composed model this matters: an ISP operator is a
*tenant of the platform*, but its subscribers are product-domain
parties/customers inside Sub — Sub's 564 tables carry no tenant discriminator
and cannot be RLS-composed onto a kernel-tenant database without a schema
change.

### 5.4 VCP — grants-not-RLS, deliberately and consistently

All 18 VCP tables follow the kernel's **platform-catalog** pattern: no
`tenant_id`, no RLS, GRANTed to `platform_api`/`app_admin` and REVOKEd from
`app_user`. Documented in every model module docstring
(`src/vendor_cp/accounts/models.py:4`, `contracts/models.py:4`,
`allocations/models.py:10`, `approvals/models.py:3`, `offers/models.py:3`,
`licensing/models.py:20`). Enforced by
`tests/migration/test_vendor_migration_rehearsals.py::test_platform_role_access_and_tenant_role_denial:194`.
This is correct: a vendor account is not tenant data.

### 5.5 Cross-repo summary

| repo | tenancy discriminator | DB-level RLS | rule enforced by |
|---|---|---|---|
| starter | `tenant_id NOT NULL` | **yes, all tenant tables + FORCE** | `tests/test_rls_catalog.py` (dynamic catalog audit) + 12 isolation canaries |
| erp | `organization_id` | **partial** (15/372 migrations; 2 point-in-time sweeps; `public` identity tables excluded) | nothing at DB level; app-level session/dep tests only |
| sub | **none** | **none** | n/a — single-tenant by design |
| vcp | none (platform catalog) | **none, by design**; grants + REVOKE instead | `test_vendor_migration_rehearsals.py::test_platform_role_access_and_tenant_role_denial` |

---

## 6. Findings — collision risks a module system must eliminate, ranked

### R1 — CRITICAL: revision-ID namespace is unallocated; the highest-probability convention is the most-used one

**Evidence:** §2.1 — kernel uses `0001`…`0012`, Sub uses `001`…`462`. The two
numeric namespaces are separated only by zero-padding width (4 vs 3), which is
an accident. 56 ERP revisions are bare slugs (`add_sync_tables`,
`add_rbac_tables`, `create_expense_tables`) — precisely the names a second
module picks. No repo has a test asserting revision-ID uniqueness across
composed locations.
**Impact:** a duplicate ID raises `CommandError: Duplicate revision
identifier` at `ScriptDirectory` load, which blocks `upgrade`, `stamp`,
`current`, `history` **and** `downgrade` simultaneously — discovered on the
deploy host, unrecoverable without a code change, and if triggered by enabling
a module on a live system it freezes all migration activity on that database.
**Must eliminate:** allocate every module an owner-scoped revision prefix from
a registry (the `a`/`v` prefixes in `starter/assembly` and `vcp` are the
prototype, but are today unregistered convention), and add a composed-load
uniqueness check that runs in CI, not at deploy.
**Paths:** `dotmac_starter_mt/alembic.ini:10`,
`dotmac_vendor_control_plane/src/vendor_cp/migrations.py:27-29`,
`dotmac_starter_mt/tests/test_migration_split_rehearsals.py:415`.

### R2 — CRITICAL: 16 same-name / different-shape table collisions already exist, concentrated on exactly the kernel's own tables

**Evidence:** §3.1 — 17 cross-repo duplicate bare names, 15 still colliding
after schema qualification, and only `role_permissions` shape-identical. The
worst: `user_credentials` (3 repos, 1 shared column), `audit_events`
(3 repos, 3 shared of 16/15/7), `roles` (3 repos, 1 shared column), `parties`
and `party_roles` (starter vs sub, 2 and 1 shared columns), `offer_versions`
(sub vs vcp, **zero** shared columns).
**Impact:** unlike a revision-ID clash, this fails *quietly* in the dangerous
direction. `op.create_table("roles", …)` against an existing `roles` raises
`DuplicateTable` — recoverable. But a module that ships `op.add_column` /
`op.alter_column` against a name it believes it owns will happily mutate
another module's table; and an ORM class mapped to an existing
differently-shaped table produces runtime `UndefinedColumn` errors, or worse,
silently reads the wrong rows. Composing ERP or Sub onto the kernel means
**13 direct conflicts on kernel-owned identity/authz/audit/settings tables.**
**Must eliminate:** a per-module table namespace (schema or enforced prefix)
plus a build-time check that no module declares a table already owned by
another manifest.
**Paths:** `packages/dotmac-kernel/src/dotmac_kernel/models.py:129,238,261,292`,
`packages/dotmac-kernel/src/dotmac_kernel/audit.py:22`,
`packages/dotmac-kernel/src/dotmac_kernel/settings_models.py:56`,
`dotmac_erp/app/models/auth.py:46,146,189,220`, `dotmac_erp/app/models/rbac.py:16,40,63,85`,
`dotmac_sub/app/models/party.py:158,236`, `dotmac_sub/app/models/auth.py:41,114,214,268`,
`dotmac_vendor_control_plane/src/vendor_cp/offers/models.py:26`.

### R3 — HIGH: no namespacing exists in 3 of 4 repos, so collision avoidance is 100% convention

**Evidence:** §4 — starter 24/24, sub 564/564, vcp 18/18 tables in `public`;
no `CREATE SCHEMA`, no enforced prefix, no registry. ERP is the sole
exception (374/423 non-public across 38 schemas) but its scheme is ungoverned
(inconsistent names `expense`/`exp`/`common`/`migration`, 6 models pinning
`"schema": "public"` explicitly, no enforcing test) and it specifically leaves
every collision-prone identity table in `public`.
**Impact:** every generic name in §3.2 (`settings`, `documents`, `templates`,
`attachments`, `notes`, `tags`, `events`, `jobs`, `tasks`, `files`,
`comments`, `logs`, `messages`, `webhooks`, `imports`, `exports`,
`sequences`) is unclaimed *and* unprotected — first module to land wins, and
the second one's migration fails on the target host.
**Must eliminate:** decide the namespace mechanism (Postgres schema per module
vs enforced table prefix) and make it a manifest-declared, test-enforced
property. Note the interaction with RLS: policies, `SECURITY DEFINER`
functions with `SET search_path=''`, and the composite-FK checks in
`test_rls_catalog.py` all currently assume `public` and would need to become
schema-aware.
**Paths:** `dotmac_erp/alembic/versions/20260203_create_procurement_schema.py:27`,
`dotmac_erp/app/models/pm/time_entry.py:56`, `dotmac_erp/alembic/env.py:59-60`,
`dotmac_starter_mt/tests/test_rls_catalog.py:74-100` (all queries hardcode
`nspname='public'` / `table_schema='public'`).

### R4 — HIGH: the RLS invariant is repo-local, so composing modules composes three incompatible tenancy models

**Evidence:** §5.5 — starter enforces `tenant_id NOT NULL` + FORCE RLS
dynamically (`tests/test_rls_catalog.py`); ERP has partial, point-in-time
sweep RLS on `organization_id` with no catalog audit; Sub has **zero**
`tenant_id` columns and zero RLS; VCP is deliberately grants-not-RLS.
**Impact:** the starter's catalog audit is the only thing that makes "a new
table cannot forget RLS" true, and it only sees tables in *its* database and
*its* `Base.metadata`. A module contributed from ERP or Sub brings tables that
would fail that audit — or, if the audit is not extended to module locations,
lands tenant-visible tables with no policy at all. ERP's sweep pattern is
independently fragile: any `organization_id` table created after
`add_rls_policies.py`/`add_hr_rls_policies.py` ran silently gets no RLS.
**Must eliminate:** make the RLS/grants contract a property of the module
manifest that the orchestrator verifies against the live catalog *after*
composing all locations, extending the starter's dynamic audit rather than
duplicating it per module.
**Paths:** `dotmac_starter_mt/tests/test_rls_catalog.py`,
`dotmac_erp/alembic/versions/add_rls_policies.py:97-155`,
`dotmac_erp/alembic/versions/add_hr_rls_policies.py:46-100`.

### R5 — MEDIUM: one shared, unattributed `alembic_version` table

**Evidence:** §1.1 — no repo sets `version_table`; all use the default.
Multi-head topologies are represented as multiple rows.
**Impact:** with N composed module lineages the table holds N rows with no
column identifying which module owns which. Disabling or rolling back a module
requires the operator to know that `a002_applied_licences` belongs to the
assembly and to run a branch-aware `stamp assembly@base` — the exact procedure
`test_rehearsal_6_runtime_rollback` documents. That knowledge is currently in
a test docstring, not in the data. It also means module-removal produces
"Can't locate revision identified by 'X'", freezing all migration commands.
**Must eliminate:** either per-module `version_table`s, or a module→branch-label
registry the orchestrator consults, plus a supported "disable module" path that
stamps rather than downgrades.
**Paths:** `dotmac_starter_mt/tests/test_migration_split_rehearsals.py:380-412`,
`dotmac_sub/alembic/env.py:77-84`, `dotmac_erp/alembic/env.py:60`.

### R6 — MEDIUM: `depends_on` is the only ordering primitive between lineages, and it pins a single revision

**Evidence:** §1.2/§1.3 — `a001_adopt_cfd` pins `depends_on =
"0007_platform_identity"`; `v001_vendor_accounts` pins `"0009_platform_audit_inbox"`.
Only 2 of 901 revisions use `depends_on` for cross-lineage ordering (ERP's one
use is intra-lineage).
**Impact:** the pin expresses "not before kernel 0007", but there is no
"requires kernel ≥ 0007 and < 0013" and no way to express a module→module
dependency at all. Two modules that both extend the same kernel table have no
declared ordering between them; Alembic will interleave them arbitrarily.
**Must eliminate:** a manifest-level module dependency + kernel-version-range
declaration that the orchestrator translates into `depends_on` pins and
validates before composing.
**Paths:** `dotmac_starter_mt/alembic/versions/a001_adopt_custom_field_definitions.py:42-45`,
`dotmac_vendor_control_plane/alembic/versions/v001_vendor_accounts.py`.

### R7 — LOW: dev-path `head` vs deploy-path `heads` drift

**Evidence:** `dotmac_erp/Makefile:47,106` and `dotmac_sub/Makefile:105` use
singular `alembic upgrade head`, while their `scripts/deploy.sh` use `heads`.
**Impact:** harmless today (both repos have one head), but the instant either
repo gains a second lineage the dev path silently applies only one branch,
producing local databases that diverge from production.
**Must eliminate:** the orchestrator should be the only entrypoint, for dev and
deploy alike — as VCP already does via `scripts/migrate.py` /
`make_alembic_config`.

---

## Appendix — reference design, distilled

The pattern the orchestrator should generalize, already proven twice
(starter/assembly and vcp/vendor) and covered by rehearsal tests in both repos:

1. **One version location per owner.** Kernel ships its versions dir as package
   data and exposes it through a public API (`dotmac_kernel.migrations.versions_dir()`),
   never a path.
2. **Each owner's lineage has its own base** (`down_revision = None`) and its own
   **`branch_labels`** — labels are load-bearing, they make `<label>@head` and
   `<label>@base` addressable for stamping.
3. **Cross-lineage ordering is `depends_on`, never `down_revision`** — so the
   kernel can advance without any module rewriting a script.
4. **Deploy runs `upgrade heads` (plural)** through a single composed entrypoint
   shared by CLI, deploy, and tests, so composition can never diverge between
   them (`vendor_cp/migrations.py:make_alembic_config` is the cleanest example).
5. **Rollback/disable is a branch-aware `stamp <label>@base`, not a downgrade** —
   preserves data, un-records the branch, restores compatibility with an older
   migrator.
6. **Pin the expected head set in a test** (`test_rehearsal_7_expected_heads_per_lineage`)
   so an accidental extra head fails CI rather than deploy.

What the reference design does **not** yet solve, and the orchestrator must
add: revision-ID prefix allocation (R1), table-name namespacing (R2/R3),
composed RLS verification (R4), per-module version attribution (R5), and
module-to-module dependency declaration (R6).
