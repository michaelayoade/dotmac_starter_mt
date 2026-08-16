# Kernel persisted-runtime dependencies — `dotmac_kernel.audit` and settings storage

**As of:** 2026-08-15

**Question this answers.** Kernel `0.1.0a66` published `idempotency_ledger.v1`
and `0.1.0a67` (PR #205, OPEN at the time of writing) publishes
`outbox_relay.v1`. Both exist because a module that CALLS a kernel facility
depends on that facility's TABLES at request time while its manifest declares
only what its own DDL touches — an undeclarable dependency that migrates
cleanly and then fails in production. Two further kernel facilities are
suspected of the same shape and have no owner assigned: `dotmac_kernel.audit`
and the settings storage behind `dotmac_kernel.settings_resolver`.

**This document does not name a prerequisite.** Michael's instruction on
2026-08-15 was explicit: an inventory, not an implementation, and naming waits
until the evidence establishes the exact contract and the real consumers. What
follows records what a contract WOULD have to cover and who WOULD need it.

> **Status addendum — 2026-08-16.** The recommendation below has now been
> implemented as `platform_audit_log.v1` for the platform plane only. Kernel
> migration `0026_platform_audit_log` removes online `UPDATE`/`DELETE` and
> column-level mutation paths; `verify_platform_audit_log` proves the complete
> shape and privilege posture on PostgreSQL. `dotmac-integration` a5 declares
> and verifies it in `ig_0008`; the still-unpublished
> `dotmac-entitlement-allocation` a5 declares it and verifies it in `ea_0003`,
> after the idempotency ledger verification in `ea_0002`. The generic facility guard now maps
> `write_platform_audit_event` to the prerequisite, so future callers must
> declare and verify it. This addendum supersedes only the dated "does not
> name" status; the inventory evidence and the tenant/settings rulings remain.

**Revisions read.** Every citation is against a checked-out worktree of
`origin/main`, never a dirty tree.

| Repository | Revision read | How |
| --- | --- | --- |
| `dotmac_starter_mt` | `1208102017b2307748e57baafb8ee7d3545db22d` (`origin/main`, "chore(release): retire the integration publication row now that a3 is tagged (#207)") | dedicated `git worktree` from that SHA |
| `dotmac_erp` | `696f2d53b9fa2efb9e5777bc08b29049058bab0f` (`origin/main`) | `git show origin/main:<path>` after `git fetch` |
| `dotmac_sub` | `db2e524f8182dd5e3d63f80d26459a82da2f3796` (`origin/main`) | `git show origin/main:<path>` after `git fetch` |
| `dotmac_vendor_control_plane` | `c3a0d1bf5cf760a041034a2d5040129a7ac9cc3a` (`origin/main`) | `git show origin/main:<path>` after `git fetch` |

A pin is dated evidence, never current state. These three SHAs were read on
2026-08-15 and will move.

**In-flight work read but not merged.** PR #205
(`feat(kernel): the outbox relay is a declarable prerequisite (a67)`) at head
`32fa6015ea8d8a0212165fec65d220e10642d0f4`, state OPEN. Its `OUTBOX_RELAY_V1`
spec and `verify_outbox_relay` are read here as a modelling reference only.

**Method.** Read-only. Static reads of the kernel package, every package under
`packages/`, the reference assembly under `app/`, and the test tree. No test was
executed — CI is this fleet's acceptance owner. Where a test is cited it is
cited for what it covers and on which database, never as a passing result.

---

## 1. Headline verdicts

**Kernel audit — a real, undeclared, request-time storage dependency, but only
on the PLATFORM plane, and in only two published modules.**

Two published modules write `platform_audit_events` at request time and neither
declares any prerequisite at all. The tenant plane (`public.audit_events`) has
**zero published module callers**: its only module caller is
`dotmac-template-studio`, which has never been released.

Outside the module scope, one foreign ADOPTER already depends on the platform
audit table at request time — the vendor control plane, **18 invocations across
10 files** — and it reaches that table through a **hand-authored physical
Alembic edge naming a foreign kernel revision**
(`depends_on = "0009_platform_audit_inbox"`,
`dotmac_vendor_control_plane/alembic/versions/v001_vendor_accounts.py:30`). That
is precisely the instrument `dotmac_kernel/prerequisites.py:1-30` was written to
replace, reached for because there is no logical name for the effect. It is the
strongest single piece of evidence in this report.

**Settings storage — no published module consumer exists. Not one.**

Across all eleven packages under `packages/`, there is not a single call into
`resolve_value`, `resolve`, `resolve_many`, `stored_at`, `upsert_by_key`,
`ensure_by_key`, `clear_by_key` or any other settings-storage entry point. Every
grep hit is prose in a docstring. The only module-side settings dependency in the
repository is **transitive and belongs to an unpublished module**: six
`dotmac-template-studio` web routes depend on `require_web_auth`, which warms
branding and display from `domain_settings`.

There IS a real product-side consumer — `dotmac_sub` calls the kernel resolver
at request time (§ 6.3) — but it does so **against Sub's own `domain_settings`
table**, which Sub deliberately converged onto the kernel shape rather than
adopting the kernel's lineage. That is an adapter over a product-owned table,
not an adopter needing the kernel to supply one, and it does not motivate a
prerequisite.

**The most consequential finding is not about either facility.** Two published
modules — `dotmac-integration` and `dotmac-entitlement-allocation` — call
`dotmac_kernel.idempotency.execute_once_platform` at request time and declare
**no `requires` at all**. `idempotency_ledger.v1` already exists, was published
in a66 precisely for this. Integration's omission is known and has a draft fix
in flight (PR #204); **`dotmac-entitlement-allocation`'s has nothing** — no open
or merged PR touches it — even though one grep for `execute_once` across
`packages/` returns both. There is no generic guard tying a caller to a
declaration, only a hand-written per-module test for `dotmac-numbering`. See
§ 8.1.

---

## 2. Scope — which packages are actually published

Eleven packages live under `packages/`. `dotmac-kernel` and `dotmac-ui` are not
modules. Of the remaining nine module-shaped distributions, all but two are
published, measured against the git tag the release workflow writes only after
`verify-registry` installs the exact version
(`docs/inventories/declared-publication-baseline.json`, "The oracle is a git
tag").

| Package | Latest tag | `pyproject` version | Release lane | Published? |
| --- | --- | --- | --- | --- |
| `dotmac-application-directory` | `dotmac-application-directory-v0.1.0a3` | `0.1.0a3` | `release-modules.json` | yes |
| `dotmac-approvals` | `dotmac-approvals-v0.1.0a4` | `0.1.0a4` | `release-modules.json` | yes |
| `dotmac-auth-oidc` | `dotmac-auth-oidc-v0.1.0a1` | `0.1.0a1` | `release-adapters.json` | yes |
| `dotmac-entitlement-allocation` | `dotmac-entitlement-allocation-v0.1.0a4` | `0.1.0a4` | `release-modules.json` | yes |
| `dotmac-files` | `dotmac-files-v0.1.0a2` | `0.1.0a2` | `release-modules.json` | yes |
| `dotmac-integration` | `dotmac-integration-v0.1.0a3` | `0.1.0a3` | `release-modules.json` | yes |
| `dotmac-numbering` | `dotmac-numbering-v0.1.0a2` | `0.1.0a2` | `release-modules.json` | yes |
| `dotmac-release-catalog` | `dotmac-release-catalog-v0.1.0a4` | `0.1.0a4` | `release-modules.json` | yes |
| `dotmac-ticketing` | `dotmac-ticketing-v0.1.0a4` | `0.1.0a4` | `release-modules.json` | yes |
| `dotmac-imports` | **none** | `0.1.0a2` | removed from every lane 2026-08-15 | **no — intended** |
| `dotmac-template-studio` | **none** | `0.2.0a3` | in no lane at all | **no — never released** |

Both unpublished distributions carry a reasoned row in
`docs/inventories/declared-publication-baseline.json`. `dotmac-template-studio`
is described there as "the still-unlisted stand-in the allowlist gate's own
sensitivity proof uses", and in `docs/MODULE_CATALOG.md:57` as `not
allowlisted`, `0.2.0a3`.

**Why this matters here and nowhere else in the report:** the undeclarable
runtime dependency only bites a FOREIGN adopter that installs a wheel and runs
its own lineage. A module nobody can pin cannot reach one. Template Studio is
therefore counted, cited and then set aside — see § 3 bucket 1 and § 9.

Not set aside because it is dormant: `dotmac-template-studio` IS composed into
the reference assembly (`app/assembly.py:40,98`) and its lineage IS in
`alembic.ini`'s `version_locations`. Its audit writes and its settings-warming
routes execute in every starter deployment. They execute correctly, because the
starter runs the kernel lineage and has the tables. Set aside because **no
foreign adopter can pin it**, which is the only place the dependency bites.

---

## 3. `dotmac_kernel.audit` — the three-bucket classification

The facility's public write surface is two functions in
`packages/dotmac-kernel/src/dotmac_kernel/audit.py`:

- `write_audit_event` (line 183) → `db.add`/`db.flush` into `AuditEvent`
  (`__tablename__ = "audit_events"`, line 79) — the **tenant plane**.
- `write_platform_audit_event` (line 251) → `db.add`/`db.flush` into
  `PlatformAuditEvent` (`dotmac_kernel/models_platform.py`,
  `platform_audit_events`) — the **platform plane**.

Both flush. Neither has a no-storage mode. A caller of either needs the table
in the request transaction.

### Bucket 1 — audit writes that REQUIRE storage

**Platform plane — 2 PUBLISHED modules, 2 kernel entry-point invocations, 8
distinct request-time write paths reaching them.**

Counting carefully, because the fleet has been burned by dossiers crediting a
source with dead code. There are two places the kernel function is actually
invoked:

| Module | Published | Kernel invocation | Plane |
| --- | --- | --- | --- |
| `dotmac-integration` | yes (a3) | `packages/dotmac-integration/src/dotmac_integration/operations.py:105` — `write_platform_audit_event(` inside the `record_operation` adapter | platform |
| `dotmac-entitlement-allocation` | yes (a4) | `packages/dotmac-entitlement-allocation/src/dotmac_entitlement_allocation/service.py:357` — `write_platform_audit_event(` | platform |

`record_operation` is an adapter, not a second writer — its own docstring
(`operations.py:94`) says so, and the import is deferred "because the kernel's
audit module reaches persistence, and a top-level import would make this package
unimportable without a configured database". **That deferral is itself
evidence**: the module already knows it has a persistence dependency it has no
way to declare.

The adapter is not dead code. It has **7 real callers**, so the dependency is
reached from seven independent request/command paths:

| File:line | Owning surface |
| --- | --- |
| `…/dotmac_integration/operations.py:230` | repair command |
| `…/operations.py:266` | repair command |
| `…/operations.py:312` | repair command |
| `…/lifecycle.py:285` | ingress endpoint mint/rotate/revoke |
| `…/retention.py:476` | retention sweep |
| `…/retention.py:519` | legal hold |
| `…/retention.py:890` | retention sweep |

`dotmac-entitlement-allocation`'s single invocation sits **inside** the
`execute_once_platform` operation (`service.py:357`, within `_operation`, called
at `:375`), so its audit write and its ledger write share one transaction and
one at-most-once guarantee. That module needs both kernel facilities' tables in
the same request and declares neither.

**Tenant plane — 9 call sites in 1 UNPUBLISHED module.**

All in `dotmac-template-studio`, all passing `actor_party_id=` and no
`actor_type`/`actor_id`:

| File:line | Action written |
| --- | --- |
| `packages/dotmac-template-studio/src/dotmac_template_studio/router.py:80` | `template_studio.template.create` |
| `…/router.py:122` | `template_studio.template.update` |
| `…/router.py:148` | `template_studio.template.delete` |
| `…/router.py:192` | `template_studio.version.create` |
| `…/router.py:221` | `template_studio.version.update` |
| `…/router.py:244` | `template_studio.version.publish` |
| `…/web.py:129` | `template_studio.template.create` |
| `…/web.py:217` | `template_studio.version.create` |
| `…/web.py:254` | `template_studio.version.publish` |

**Published-module callers of the tenant audit plane: zero.**

### Bucket 2 — settings row reads/writes that REQUIRE storage

None in this section; see § 4.

### Bucket 3 — declarations and derivations that do NOT require storage

These are the hits a grep for "audit" returns and that must not be counted:

| What | Where | Why it is not a storage dependency |
| --- | --- | --- |
| `audit_actions=(…)` on a manifest — 9 codes | `packages/dotmac-integration/src/dotmac_integration/manifest.py:67` | A declaration validated against the in-process `AuditActionRegistry`. Declaring a code touches no table. It becomes a storage dependency only where a `write_audit_event` call exists — which for integration it does, on the platform plane. |
| `audit_actions=("entitlement_allocation.staged",)` | `packages/dotmac-entitlement-allocation/src/dotmac_entitlement_allocation/manifest.py:44` | Same. |
| `audit_actions=[…]` | `packages/dotmac-template-studio/src/dotmac_template_studio/manifest.py:122` | Same. |
| `AUDIT_ACTION_PREFIX = "integration"` and the docstring naming `write_audit_event` | `…/dotmac_integration/operations.py:75`, `manifest.py:56`, `__init__.py:42`, `lifecycle.py:254` | Prose and a string constant. |
| Every test reference | `tests/unit/test_integration_operations.py:62`, `tests/unit/test_integration_retention.py:96` | These **monkeypatch** `record_operation` and (at `test_integration_operations.py:491`) the kernel function itself. They deliberately exercise no table. |

A declared audit action with no `write_audit_event` caller is a **legitimate
no-storage mode** for this facility, and it is not hypothetical: a module could
declare its vocabulary in one release and ship the writers in the next. Any
future gate must classify on the CALL, never on the declaration.

### What the tests cover, and on which database

Recorded because a SQLite-only or mock-based test proves nothing about a table
existing, let alone about RLS or grants. No test was executed for this report.

| Test | Database | What it exercises |
| --- | --- | --- |
| `tests/unit/test_integration_operations.py` | in-memory SQLite, `tests/unit` | Monkeypatches `record_operation` (`:62`) and, at `:491`, the kernel function itself. **Exercises no audit table.** |
| `tests/unit/test_integration_retention.py` | in-memory SQLite | Monkeypatches `record_operation` (`:96`). **Exercises no audit table.** |
| `tests/test_entitlement_allocation_canaries.py:390` | **real Postgres** (top-level, needs `make test-db-up`) | `test_two_concurrent_sessions_produce_one_allocation_and_one_audit_event` imports the real `PlatformAuditEvent` (`:397`) and asserts exactly one row. **The only test in this repository that drives a module's audit write against real storage.** |
| `tests/unit/test_template_studio_{renderer,seeding,service}.py` | in-memory SQLite | The renderer, seeding and service only. **Neither `router.py` nor `web.py` — so none of Template Studio's 9 audit writes, and none of its 6 settings-warming routes, is driven by any test.** |

Note what this means for the entitlement-allocation canary: it passes because
the STARTER's integration database runs the kernel lineage and therefore has
`platform_audit_events`. It proves the write works where the table exists. It
is structurally incapable of detecting the failure this report is about, which
occurs only where it does not.

---

## 4. Settings storage — the three-bucket classification

The storage is `public.domain_settings` and `public.domain_setting_history`
(`packages/dotmac-kernel/src/dotmac_kernel/settings_models.py:112` and `:262`).
Every read goes through `_select_row`
(`packages/dotmac-kernel/src/dotmac_kernel/settings_resolver.py:311`), a
`SELECT` against `domain_settings`.

### Bucket 2 — settings row reads/writes that REQUIRE storage

**Direct module callers: ZERO.** An exhaustive grep across every package for
`settings_resolver`, `settings_admin`, `settings_models`, `settings_cache`,
`settings_crypto`, `settings_shadow`, `setting_scopes`, `setting_value_types`,
`resolve_value`, `resolve_with_source`, `resolve_many`, `stored_at`,
`register_specs`, `SettingSpec`, `SettingDomain`, `DomainSetting`,
`domain_settings`, `upsert_by_key`, `ensure_by_key`, `clear_by_key` and
`seed_settings_from_env` returns **eight hits, all of them prose** in
docstrings and comments (`dotmac-auth-oidc/client.py:4,75`,
`dotmac-template-studio/manifest.py:10`,
`dotmac-application-directory/service.py:134`,
`dotmac-integration/destination_binding.py:70`,
`dotmac-entitlement-allocation/service.py:141`,
`dotmac-ticketing/models.py:53`, `dotmac-ticketing/vocabulary.py:13`).

**Transitive module callers: one module, six routes, and it is unpublished.**

`packages/dotmac-template-studio/src/dotmac_template_studio/web.py:28` imports
`require_web_auth`, used at lines 60, 75, 109, 169, 192 and 246. The chain is:

```
require_web_auth                      web_deps.py:128,138
  → get_request_branding(request, db) branding.py → load_branding:222
      → resolve_value(SettingDomain.branding, "ui_branding", default={})
  → get_request_display(request, db)  display.py → load_display:46,57,60
      → resolve_value(SettingDomain.display, "timezone")
      → resolve_value(SettingDomain.display, "date_format")
      → resolve_value(SettingDomain.display, "datetime_format")
```

Each of those `resolve_value` calls reaches `_select_row` and issues a
`SELECT` against `domain_settings` — with one exception, below, that matters a
great deal.

### Bucket 3 — declarations and default-only resolution that DO NOT

There is exactly **one** code path in the resolver that reaches a value without
touching a row, and it is narrower than "resolution terminated in a default":

> `settings_resolver.py:611-618` — `get_spec(domain, key)` raises `KeyError`
> (the spec is **not registered**) AND the caller passed an explicit `default=`.
> The comment on line 615 states it: *"Nothing is cached here: this path never
> touches the database, so there is no read to save."*
> `settings_cache.py:25-26` states the same rule independently.

Everything else — including resolution that ends at the spec default — walks
`chain` at line 635 and issues one `_select_row` per scope in the chain BEFORE
falling back. **A consumer whose value always comes from the spec default still
requires the table.** This is the single most important correction a naive grep
would get wrong in the opposite direction from the one expected: the "default
only, so no storage" intuition is FALSE for a registered spec.

Applying that rule to the one transitive consumer above:

| Call | Registered where | Passes `default=`? | Storage required? |
| --- | --- | --- | --- |
| `branding.load_branding:222` — `ui_branding` | `app/features/settings/spec.py:82` — the **ASSEMBLY**, not the kernel | **yes**, `default={}` | **No**, in an adopter that does not install the starter's `settings` feature. Bucket 3. |
| `display.load_display:46` — `timezone` | `app/features/settings/spec.py:94` — the ASSEMBLY | no | Yes where registered. Where NOT registered it raises `KeyError` — a hard 500, before any table is consulted. |
| `display.load_display:57` — `date_format` | `app/features/settings/spec.py:106` | no | Same. |
| `display.load_display:60` — `datetime_format` | `app/features/settings/spec.py:115` | no | Same. |

That last row is the finding that decides the instrument. **Half of the
settings dependency is not a table at all — it is a Python spec registration
performed by the reference assembly's `settings` feature.** A foreign adopter
that creates a perfectly shaped `domain_settings` and composes
`dotmac-template-studio` still gets `KeyError: No registered setting spec for
display/timezone` on the first portal request. A DDL-shape verifier cannot see
that and cannot prevent it.

Also bucket 3, and the population a grep would wrongly count:

| What | Where | Why not a dependency |
| --- | --- | --- |
| Every `SettingSpec` registered in this repository | `app/features/settings/spec.py:126`, `app/features/auth/spec.py:27`, `app/features/rbac/spec.py:28`, `app/features/custom_fields/spec.py:39` | Assembly code, and registration itself is in-process. |
| `setting_domains=(…)` on any manifest | manifests | A declaration checked against the domain registry. |
| The `SettingDomain` prose in `dotmac-ticketing` | `models.py:53`, `vocabulary.py:13` | Cites the domain registry as a precedent for its OWN closed vocabulary. Touches nothing. |
| `settings_resolver` mention in `dotmac-entitlement-allocation` | `service.py:141` | A comment about deferred imports. |

---

## 4A. The reference assembly — reported separately, and it motivates nothing

**`app/` is not evidence for a prerequisite and is listed here only so it is not
mistaken for some.** The starter assembly runs the kernel lineage
(`alembic.ini` carries it), so `audit_events`, `platform_audit_events`,
`domain_settings` and `domain_setting_history` always exist wherever it runs.
An assembly cannot have the undeclarable dependency this report is about: it is
the thing that answers the dependency, via `app/migration_bindings.py`.

Assembly audit writes — 11 call sites, all tenant plane:
`app/features/settings/web.py:221,317`; `app/features/settings/router.py:50`;
`app/features/rbac/router.py:52,85`; `app/features/rbac/web.py:147,242`;
`app/features/tenants/service.py:168,179`;
`app/features/licensing/router.py:43,89`.

Assembly settings reads/writes that require storage:
`app/features/auth/service.py:92` (`resolve(db, REGISTRATION_POLICY, …)`),
`app/features/custom_fields/service.py:186` (`resolve(db, MAX_PER_ENTITY, …)`),
`app/features/rbac/service.py:190` (`resolve(db, AUDIT_RETENTION_DAYS, …)`),
`app/features/settings/service.py:102` (`upsert_by_key`),
`app/features/settings/seed.py:36` (`ensure_by_key`), plus the branding and
display warming in `dotmac_kernel.web_deps` on every portal request.

The assembly is also where the four `display`/`branding` specs are registered
(`app/features/settings/spec.py:126`) — which is exactly what § 4 identified as
the half of the settings dependency that lives in Python rather than in DDL.

---

## 5. The contract shape a verifier would need

Recorded in the form the inspector reports it, modelled on
`packages/dotmac-kernel/src/dotmac_kernel/migrations/verify.py:363-476`
(`verify_idempotency_ledger`) and PR #205's `verify_outbox_relay`. **These are
descriptions of what exists, not proposed specs.**

### 5.1 Audit — tenant plane, `public.audit_events`

Columns, from `dotmac_kernel/audit.py:78-131` as created by
`20260504_0001_initial_tenant_schema.py:349` and extended by
`20260812_0023_audit_actor_and_forensics.py`:

| Column | Type | Nullable | Length | TZ | Server default |
| --- | --- | --- | --- | --- | --- |
| `id` | `Uuid` | no | — | — | no |
| `tenant_id` | `Uuid` | **no** | — | — | no (FK → `tenants.id` `ON DELETE CASCADE`) |
| `actor_type` | `String` | yes | 32 | — | no |
| `actor_id` | `String` | yes | 120 | — | no |
| `actor_label` | `String` | yes | 160 | — | no |
| `actor_party_id` | `Uuid` | yes | — | — | no — **deliberately NOT a foreign key** |
| `action` | `String` | no | 120 | — | no |
| `entity_type` | `String` | no | 120 | — | no |
| `entity_id` | `String` | yes | 120 | — | no |
| `request_id` | `String` | yes | 120 | — | no |
| `status_code` | `Integer` | yes | — | — | no |
| `is_success` | `Boolean` | yes | — | — | no |
| `ip_address` | `String` | yes | 64 | — | no |
| `user_agent` | `String` | yes | 255 | — | no |
| `details` | `JSON`/`JSONB` | no | — | — | no (ORM `default=dict`) |
| `occurred_at` | `DateTime` | yes | — | **yes** | **yes**, `now()` |
| `created_at` | `DateTime` | no | — | **yes** | **yes**, `now()` |

- **Keys:** primary key on `id`. **No unique constraint at all** — an audit
  trail is append-only and deliberately admits duplicates.
- **Indexes:** `tenant_id`, `actor_type`, `actor_id`, `actor_label`,
  `actor_party_id`, `request_id`, `occurred_at`
  (`20260812_0023…:66` names the five it adds).
- **RLS:** ENABLE + **FORCE** row-level security
  (`20260504_0001…:412`, table listed at `:410`).
- **Grants:** `GRANT SELECT, INSERT ON audit_events TO app_user`
  (`20260504_0001…:435`) and the same pair to `platform_api` (`:443`).
  **No UPDATE, no DELETE — the append-only posture is enforced by grant, not by
  a trigger.** `docs/inventories/migration-collisions.md:446` records this
  independently.
- **Functions:** none.

**The load-bearing check is not a key — it is the grant.** For the idempotency
ledger, `verify_idempotency_ledger` says the unique key is load-bearing because
without it at-most-once silently becomes at-least-once. Audit has no such key.
Its equivalent single point of silent failure is the grant set: a provider
supplying `audit_events` with `UPDATE`/`DELETE` granted to `app_user` produces a
mutable forensic trail that raises nothing, ever.

### 5.2 Audit — platform plane, `public.platform_audit_events`

From `20260730_0009_platform_audit_inbox.py:35-64`:

| Column | Type | Nullable |
| --- | --- | --- |
| `id` | `UUID` | no (pk) |
| `actor_admin_id` | `UUID` | yes |
| `action` | `String(120)` | no |
| `entity_type` | `String(120)` | no |
| `entity_id` | `String(120)` | yes |
| `details` | `JSONB` | no |
| `created_at` | `DateTime(tz)` | no |

- **Keys:** primary key on `id`; **foreign key `fk_platform_audit_events_admin`
  on `actor_admin_id`** (`:59`).
- **Indexes:** `ix_platform_audit_events_actor_admin_id` (`:62`).
- **RLS:** none — by design (`:10`, and
  `docs/inventories/migration-collisions.md:456`).
- **Grants:** `GRANT SELECT, INSERT, UPDATE, DELETE … TO platform_api` and
  `… TO app_admin`; `REVOKE ALL … FROM app_user` (`:96-98`). The revoke IS the
  isolation, per ADR-0023.

**A prerequisite here would drag in a second table.** Unlike
`platform_idempotency_records`, this table carries a real FK to
`platform_admins`. A verifier proving `platform_audit_events` alone would pass
against a database where the FK target does not exist — which cannot happen,
because the FK would have failed at creation, but it does mean the effect a
provider must supply is "platform audit trail **and** the platform admin
catalogue", a materially larger ask than the ledger's two standalone tables.

### 5.3 Settings — `public.domain_settings`

From `settings_models.py:100-202`, created by
`20260717_0002_settings_table.py:54` and reshaped by `…_0016_setting_scope_depth`
and `…_0021_setting_scope_alignment`:

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | `Uuid` | no | pk |
| `tenant_id` | `Uuid` | **YES** | FK → `tenants.id` `ON DELETE CASCADE`; NULL = the platform scope |
| `scope_kind` | `String(40)` | no | server default `'platform'` |
| `scope_id` | `Uuid` | yes | |
| `domain` | `String(120)` | no | open registered string, not an enum |
| `key` | `String(120)` | no | |
| `value_type` | `String(40)` | no | open registered string, not an enum |
| `value_text` | `Text` | yes | encrypted when `is_secret` |
| `value_json` | `JSON`/`JSONB` (`none_as_null=True`) | yes | |
| `is_secret` | `Boolean` | no | |
| `is_active` | `Boolean` | no | |
| `created_at` / `updated_at` | `TimestampMixin` | no | |

- **Check constraints, both load-bearing:**
  `ck_domain_settings_value_alignment` (exactly one of `value_text`/`value_json`
  populated) and `ck_domain_settings_scope_alignment`
  (`(scope_kind='platform' AND tenant_id IS NULL) OR (scope_kind<>'platform' AND
  tenant_id IS NOT NULL)`). The second is the one Sub found during first
  adoption (`settings_models.py:128`), and `20260811_0021` will **adopt a
  product's existing identical constraint rather than duplicate it** — the
  migration verifies `pg_get_constraintdef` matches before adopting (`:65-93`).
- **Unique key:** ONE index, `uq_domain_settings_scope`, over
  `COALESCE(tenant_id, '000…0')`, `scope_kind`,
  `COALESCE(scope_id, '000…0')`, `domain`, `key`. The `COALESCE` is essential:
  `settings_models.py:135-138` records that a plain nullable-column unique
  admits duplicates and "that is how `dotmac_erp` came to hold duplicate global
  settings". The original two partial indexes
  (`uq_domain_settings_platform` / `uq_domain_settings_tenant`,
  `20260717_0002…:112,119`) were the earlier form.
- **Indexes:** `ix_domain_settings_tenant_id`.
- **RLS:** ENABLE + FORCE (`20260717_0002…:134-135`), with **five policies**,
  not one: `domain_settings_read` (SELECT), `domain_settings_write_ins`,
  `_write_upd`, `_write_del`, and `domain_settings_platform_all` restricted to
  NULL-tenant rows (`:140-171`). This is the documented split read/write
  exception recorded at `docs/inventories/migration-collisions.md:447` and
  allowlisted in `tests/test_rls_catalog.py`.
- **Grants:** `SELECT, INSERT, UPDATE, DELETE` to both `app_user` and
  `platform_api` (`:180,182`).

### 5.4 Settings — `public.domain_setting_history`

From `settings_models.py:222-312`. Columns: `id`, `tenant_id` (nullable, no FK
— denormalised so history survives deletion), `domain`, `key`, `setting_id`
(FK → `domain_settings.id` `ON DELETE SET NULL`), `action` (non-native enum
`ck_domain_setting_history_action`, values create/update/delete), `value_before`,
`value_after`, `secret_changed`, `changed_at`, `changed_by_party_id`
(FK → `parties.id` `ON DELETE SET NULL`), `change_reason`, `ip_address(45)`,
`user_agent(500)`, `request_id(128)`. Three named indexes:
`ix_domain_setting_history_lookup` (`tenant_id`,`domain`,`key`),
`…_changed_at`, `…_actor`.

Note the FK to `parties` — a settings prerequisite would drag in the party
identity catalogue the same way the platform audit peer drags in
`platform_admins`.

---

## 6. Adopter-side collisions

Read against the three pins in the header. Paths are relative to
`/Users/michaelayoade/Downloads/management/`.

### 6.1 ERP @ `696f2d53` — both names collide, both are declared blockers

ERP owns `public.audit_events` (`app/models/audit.py:25`, created
`alembic/versions/799a0ecebdd4_initial_schema.py:211`),
`public.domain_settings` (`app/models/domain_settings.py:200`, created
`799a0ecebdd4_initial_schema.py:239`) and `public.domain_setting_history`
(`app/models/domain_settings.py:254`, created
`alembic/versions/20260124_add_domain_setting_history.py:38`).

`docs/PLATFORM_ADOPTION_LEDGER.md` already rules on both, in the terms this
report was asked to check:

> `:498` — `| dotmac_kernel.audit | defer-db | After E6 consolidation |
> write_audit_event targets table audit_events — **collides exactly** with ERP
> public.audit_events (app/models/audit.py:26). No kernel audit table beside
> ERP's four unconsolidated writers |`

> `:496` — `| dotmac_kernel.settings_models | defer-db | After E8 | Kernel
> DomainSetting table name domain_settings **collides exactly** with ERP
> public.domain_settings (different columns: tenant_id vs organization_id, no
> ERP history table on the kernel side) |`

> `:534` — `| domain_settings | **EXACT COLLISION** … | Blocker for kernel
> settings storage until E8 |`
> `:535` — `| audit_events | **EXACT COLLISION** — ERP public.audit_events
> (app/models/audit.py:26), different shape | Blocker for kernel audit table
> until E6+E8 |`

`:503` classifies `dotmac_kernel.models_platform` — the module that owns
`PlatformAuditEvent` — as **`prohibited`**: "ERP has no platform-actor concept."
`:603-610` records that ERP already has **four audit writers across three
tables**, and that `write_audit_event` "would be a fifth writer into a colliding
table name. Blocked until E6 names one writer."

The shape deltas, measured:

- `audit_events`: ERP has **no `tenant_id`** (it has `organization_id` UUID NULL
  FK → `core_org.organization`), **no `actor_label`**, **no `actor_party_id`**
  (it has `actor_person_id` FK → `people.id`), **no `details`** (it has
  `metadata` JSON) and **no `created_at`**. It carries two columns the kernel
  does not: `is_active BOOLEAN NOT NULL` and `organization_id`. `actor_type` is
  a **native PG enum `auditactortype`** where the kernel has `String(32)`.
  `status_code` is `NOT NULL`. **No RLS and no grants anywhere** — a grep of
  ERP's `alembic/**` for `ROW LEVEL SECURITY|GRANT|REVOKE` intersected with
  these table names returns zero hits.
- `domain_settings`: the **entire kernel scope triple is absent** — no
  `tenant_id`, no `scope_kind`, no `scope_id`. ERP scopes by `organization_id`
  plus a two-valued `scope` enum `{GLOBAL, ORG_SPECIFIC}`, a different model
  rather than a rename. Uniqueness is `UNIQUE(domain,key,organization_id)` plus
  a partial global index, not the kernel's COALESCE-sentinel index.
  `value_type` is still a **closed native enum** `{string,integer,boolean,json}`.
- `domain_setting_history`: **the sharpest collision of the three.** Same table
  name, entirely different recording model — ERP stores a ten-column
  before/after matrix (`old_*`/`new_*` × `value_type`/`value_text`/`value_json`/
  `is_secret`/`is_active`) where the kernel stores two rendered text columns plus
  `secret_changed`; ERP's actor is `changed_by_id → public.people.id` where the
  kernel's is `changed_by_party_id → parties.id`; ERP's `action` is a **native**
  PG enum with **uppercase** members where the kernel's is a non-native
  CHECK-backed enum with lowercase members; ERP has no `tenant_id` and no
  `request_id`.

ERP corroborates elsewhere: `docs/architecture/kernel-0001-dispositions.md:23` —
`| audit_events | Incompatible collision and historical tenancy gap; ERP audit
remains authoritative. | Blocked |`, and `:74-80` on running the kernel lineage
installing "a SECOND authority for identity, credentials, sessions, RBAC and
audit".

**ERP consumes neither kernel facility's Python API.** Its complete
`dotmac_kernel` import set outside tests is six files, covering only
`prerequisites`, `money` and `models.Tenant`. ERP's own ~50-call-site
`resolve_value` is `app/services/settings_spec.py` — a same-named function, not
the kernel's. A grep that did not check the import would count those fifty as
kernel consumers.

### 6.2 Sub @ `db2e524f` — same names, and a deliberate convergence in progress

Sub owns `public.audit_events` (`app/models/audit.py:20`),
`public.domain_settings` (`app/models/domain_settings.py:226`) and
`public.domain_setting_history` (`app/models/domain_setting_history.py:74`).

Sub is not standing still: it has been converging its shapes onto the kernel's
by migration, which makes it the more interesting adopter.

- `audit_events` is **near-converged**. `alembic/versions/413_audit_actor_label.py:37`
  added `actor_label`; `alembic/versions/526_audit_events_kernel_r1.py:48-68`
  added `actor_party_id` (no FK, deliberately, `:14-16`), `details` JSONB and
  `created_at`. What remains: **no `tenant_id`** — the one structural gap, and
  the load-bearing one; two extra columns (`metadata` JSON, retained and
  dual-written alongside `details`; `is_active NOT NULL`); `actor_type` as a
  native enum; and `created_at` **nullable with `server_default now()`**,
  deliberate so pre-526 rows stay honestly NULL (`526:8-12`). A verifier
  demanding `created_at NOT NULL` would fail Sub for a choice Sub made on
  purpose.
- `domain_settings` has **adopted the kernel's scope triple and its
  COALESCE-sentinel unique index** (`alembic/versions/507_domain_settings_scope_columns.py:90-99`,
  which dropped the legacy `uq_domain_settings_domain_key`). `507:10-12` states
  the remaining delta was "exactly three columns". Three divergences persist and
  each is deliberate: `scope_kind` server default is **`platform`**, not the
  kernel's `tenant`; the value CHECK is **at-least-one**, not the kernel's
  **exactly-one**, because Sub writes both `value_text` and `value_json` for
  booleans on purpose (`app/models/domain_settings.py:266-273`); and
  `value_type`/`domain` are open registry-validated vocabularies enforced by ORM
  listeners rather than DB enums.
- `domain_setting_history` is **kernel-exact by construction** —
  `alembic/versions/520_domain_setting_history.py:8-10`: *"The shape is
  `dotmac_kernel.settings_models.DomainSettingHistory` exactly."*

Sub's `docs/PLATFORM_ADOPTION_LEDGER.md:585-591` lists all three as collisions,
and `:593-598` adds a hazard the starter's own inventory does not record: **the
colliding Python CLASS names are identical too** (`AuditEvent`, `DomainSetting`,
`DomainSettingHistory`), so "a careless import can therefore shadow a Sub model
even before metadata reaches an engine", and kernel `Base.metadata.create_all`,
autogenerate or composed migrations "must never run against Sub". `:372`
classifies `dotmac_kernel.audit` as `defer-db`: kernel audit "adapts behind
[Sub's writers] after parity, never as a second writer".

Sub backs this with executable guards, which is worth noting for what they cover
and on what: `tests/architecture/test_kernel_table_collisions.py` (static, exact
intersection plus a sensitivity proof),
`tests/architecture/test_audit_writer_surfaces.py` (static, pins the two
sanctioned writers), and `tests/integration/test_kernel_lineage_rehearsal.py`
(the only one of the three that needs a real migrated schema).

### 6.3 Sub consumes the kernel settings resolver at request time

This is the one place either facility has a live product consumer, and it is not
a module:

| Call site | What |
| --- | --- |
| `dotmac_sub/app/services/settings_spec.py:7-9` | imports `SettingDomain`, `resolve_many as kernel_resolve_many`, `resolve_value as kernel_resolve_value` |
| `…/settings_spec.py:5469` | `kernel_resolve_value(...)` |
| `…/settings_spec.py:5544` | `kernel_resolve_many(...)` |
| `dotmac_sub/app/services/settings_kernel_bridge.py:28-31` | `register_specs`, `SettingSpec`, `SettingDomain`, `setting_value_types` |
| `dotmac_sub/app/models/domain_settings.py:388` | `active_setting_value_types` — enforced on every ORM write |
| `…/domain_settings.py:505-506` | `SettingScope`, `settings_cache.invalidate` |
| `dotmac_sub/app/services/kernel_key_provider.py:58,155`, `…/kernel_settings_cache_store.py:159`, `…/domain_settings.py:66` | `settings_crypto`, `settings_cache` install hooks |

Sub imports **no** kernel audit function in `app/`; `dotmac_kernel.audit`
appears only in two test files.

The shape of this dependency is the opposite of the one a prerequisite serves.
Sub supplies its OWN table and converged it onto the kernel's shape so the
kernel's resolver would work over it. Nothing here needs the kernel to provide
storage; what it needs is for the kernel's resolver semantics not to drift away
from the shape Sub converged onto — a compatibility question, not a
prerequisite question.

### 6.4 Vendor CP @ `c3a0d1bf` — the clean adopter, and the missing name

VCP defines **neither** table family. Its 18 tables are all vendor-domain. It
**inherits** the kernel's platform catalogue through composed lineage
(`alembic.ini:5-7`; `src/vendor_cp/migrations.py` composes the kernel package
versions directory).

And it calls the platform audit writer at request time, in production code,
**18 invocations across 10 files** (imports excluded — these are call sites,
counted by direct read at `c3a0d1bf`):

| File | Invocation lines | Count |
| --- | --- | --- |
| `src/vendor_cp/accounts/service.py` | 100 | 1 |
| `src/vendor_cp/allocations/service.py` | 135 | 1 |
| `src/vendor_cp/approvals/service.py` | 96, 154 | 2 |
| `src/vendor_cp/contracts/service.py` | 214 | 1 |
| `src/vendor_cp/offers/service.py` | 123 | 1 |
| `src/vendor_cp/release_evidence/service.py` | 398 | 1 |
| `src/vendor_cp/licensing/projection.py` | 177, 233, 346, 411 | 4 |
| `src/vendor_cp/licensing/revocation.py` | 118, 227 | 2 |
| `src/vendor_cp/licensing/service.py` | 390 | 1 |
| `src/vendor_cp/licensing/transport.py` | 369, 410, 495, 582 | 4 |

`src/vendor_cp/contracts/service.py:5` records the transaction rule: the state
change, the `write_platform_audit_event` and the outbox enqueue share one
transaction. This is not incidental logging; it is inside the unit of work.

**How VCP guarantees the table is there is the finding.**
`alembic/versions/v001_vendor_accounts.py:30` carries
`depends_on = "0009_platform_audit_inbox"`, with the docstring at `:1-19`
explaining that it depends on the kernel head "so the kernel's platform-catalog
roles + tables (`platform_admins`, `platform_audit_events`,
`platform_inbox_records` — the AccountService's audit/idempotency backing) exist
before this runs".

That is a **physical Alembic edge naming a foreign revision** — the exact
construct `dotmac_kernel/prerequisites.py:1-30` opens by calling "a lie in every
assembly that does not run the named lineage", and the construct the whole
logical-prerequisite mechanism exists to remove. VCP reached for it because
there is no logical name for "the platform audit trail exists here". An adopter
has already paid the cost of the missing name, in checked-in code, today.

Two further VCP facts:

- **No collision, either family.** VCP is the control case: the kernel shapes
  run unmodified where nothing pre-existing competes.
- **VCP independently corroborates the settings instrument problem.**
  `tests/migration/test_composed_live_catalog.py:120-126` defines
  `UNMONITORED_SPLIT_SCOPE = {"domain_settings", "feature_flag_overrides",
  "domain_setting_history"}`, with the rationale at `:115-119`: these tables have
  a **nullable `tenant_id`**, so they "sit in neither the tenant nor the platform
  plane" and this assembly deliberately does not audit their RLS/grant contract.
  A composed-catalog gate written by an actual adopter has already concluded that
  the settings tables cannot be expressed in the plane vocabulary.

### 6.5 The starter's own undated inventory, for corroboration

The starter's `docs/inventories/migration-collisions.md` — an **undated**
document with no revision pins in its header, so its ERP/Sub column counts are
of unknown vintage and are cited here as corroboration, not as current state —
already records both tables as same-name/different-shape collisions:

> `audit_events` | erp, starter, sub | **NO** — 3 shared (`action`,`entity_id`,
> `entity_type`); starter 7 …, erp 16 (`organization_id`,`actor_person_id`,
> `metadata_`,`status_code`,…), sub 15 (`actor_label`,`metadata_`,…)
> — `migration-collisions.md:258`

> `domain_settings` | erp, starter, sub | **NO** — 7 shared (`domain`,`key`,
> `value_json`,`value_text`,`value_type`,`is_secret`,`is_active`); starter adds
> `tenant_id`, erp adds `organization_id`+`scope`, sub adds neither
> — `migration-collisions.md:260`

and at `:373` records that both sit in ERP's unnamespaced `public` schema along
with eleven other collision-prone kernel-shaped tables, with **no test enforcing
schema placement**.

**Two of that document's rows are now stale**, which is why it is cited as
corroboration only:

1. Its starter `audit_events` column count (7) predates migration `0023`, which
   added seven forensic columns. The current kernel shape is § 5.1's 17 columns.
   The direction of the finding gets worse, not better — the overlap with ERP's
   and Sub's tables is still three columns against a wider kernel table.
2. Its claim that Sub "adds neither" scope column to `domain_settings` was
   overtaken by Sub migration `507`, which added all three. Sub's settings table
   is now much closer to the kernel's than that row implies; its audit table is
   too, after `413` and `526`.

### 6.6 The collision class, and what it decides

**A shape verifier for a table named `public.audit_events` or
`public.domain_settings`, run in ERP, inspects ERP's own long-standing table of
the same name and fails.** Correctly — but with no path forward, because the
adopter cannot rename its table and the kernel cannot rename its own.
`verify_idempotency_ledger`'s comment (`verify.py:371-375`) names exactly this
hazard for `ref_id` in Sub, but for a table Sub does not otherwise own. Here two
of the fleet's three adopters own the name outright, and both have ruled the
kernel table a blocker in a checked-in ledger.

The platform peer is the half that does NOT collide. Neither ERP nor Sub defines
`platform_audit_events`; ERP's ledger marks the whole platform-actor surface
`prohibited` (`:503`) because ERP has no platform-actor concept, and Sub's ledger
`:627-632` records no clash. VCP inherits it and uses it heavily. **The
collision evidence splits the audit facility cleanly along the plane boundary**,
which is the opposite of the ledger precedent, where the two planes had to be
named together.

---

## 7. Where these facilities DIFFER from the ledger case

The three questions that decide whether a prerequisite is the right instrument.

| | `idempotency_ledger.v1` (the precedent) | Kernel audit | Settings storage |
| --- | --- | --- | --- |
| **Platform peer?** | Yes — two tables, one per plane, and the spec names both "because a consumer cannot take the tenant half without linking code that references the platform table" (`prerequisites.py:203-205`) | **Yes**, but the two planes are genuinely separable: `write_audit_event` and `write_platform_audit_event` are independent entry points with independent callers, and today's published callers use ONLY the platform one. A single both-planes spec would force a tenant-plane requirement on two modules that have no tenant plane at all. | **No — and worse.** There is ONE table serving BOTH scopes through a **nullable `tenant_id`**. ADR-0023's plane gate explicitly refuses nullable `tenant_id`, sentinel tenants and polymorphic scope columns as a plane declaration (hard rule 24/27). The settings table is the documented exception to hard rule 11 and cannot be expressed in the two-plane vocabulary the existing specs use. |
| **Name collides with a real adopter table?** | No — `idempotency_records` is kernel-invented. The nearest hazard was Sub's overloaded `ref_id` COLUMN inside a differently-named table. | **Tenant plane: yes, exactly** — `public.audit_events` in both ERP and Sub, both declared blockers (`dotmac_erp` ledger `:498,:535`; `dotmac_sub` ledger `:372,:585`). **Platform plane: no** — neither product defines `platform_audit_events`, and ERP marks the surface `prohibited` (`:503`). The collision splits along the plane boundary. | **Yes, exactly, on all three tables.** `public.domain_settings` and `public.domain_setting_history` in both ERP and Sub. ERP's history table is the sharpest same-name/different-shape case in the fleet (§ 6.1). ERP's ledger `:496,:534` calls it a blocker until E8. |
| **Legitimate no-storage mode?** | No. A module either calls `execute_once` or it does not; there is no default-satisfied call. | **Yes.** Declaring `audit_actions` without any `write_audit_event` call is a real, reviewable state, and every published module's manifest declares actions the module may not write on both planes. A gate keyed on the declaration would fire on modules with no dependency. | **Yes, twice over.** (a) An unregistered spec with `default=` never reaches the database (`settings_resolver.py:611-618`, `settings_cache.py:25`). (b) More importantly the dependency is **half Python**: the display specs are registered by the assembly's `settings` feature (`app/features/settings/spec.py:94-123`), and an adopter with a perfect `domain_settings` still gets `KeyError` without them. No DDL verifier can see that half. |

A fourth question the commission did not ask, which the adopter evidence
forces:

**Does an adopter already need the name?** For the ledger, the answer was
"`dotmac-numbering` will". For the **platform** audit plane the answer is
already yes, in checked-in code: VCP guarantees the table with
`depends_on = "0009_platform_audit_inbox"`
(`dotmac_vendor_control_plane/alembic/versions/v001_vendor_accounts.py:30`), the
forbidden physical edge, because no logical name exists. For settings the answer
is no — nobody is waiting on the kernel to supply that storage. Sub supplies its
own and converged it (§ 6.2, § 6.3); ERP has ruled the kernel's a blocker; VCP
inherits it and explicitly declines to audit it (§ 6.4).

Two further asymmetries:

- **The ledger's verifier has one load-bearing check; audit's would have a
  different one.** `verify_idempotency_ledger` centres on the unique key
  because that constraint IS the at-most-once guarantee. `audit_events` has no
  unique constraint. Its equivalent is the **grant set** —
  `SELECT, INSERT` and nothing more — which is a privilege posture, not a shape.
  `verify_module_database_roles` already proves this kind of thing is
  checkable, so this is a difference in what to check, not a bar.
- **Both facilities pull in a second catalogue.** `platform_audit_events` FKs
  to `platform_admins`; `domain_setting_history` FKs to `parties` and
  `domain_settings` FKs to `tenants`. The idempotency ledger FKs to nothing.
  A provider satisfying either effect is supplying substantially more of the
  kernel's estate than the ledger asked for.

---

## 8. What this report found that contradicts the existing record

Three items, each with evidence, none of them repaired here.

### 8.1 Two published modules call `execute_once_platform` and declare no prerequisite

`idempotency_ledger.v1` was published in a66 for exactly this failure mode, and
`tests/unit/test_prerequisites.py:67-70` records why: *"`dotmac-numbering`
consumed the ledger at request time with no name to declare, so an adopter
missing it passed every gate and failed in production instead."*

Yet at `1208102`:

| Module | Calls | Manifest `requires` at `1208102` | In-flight fix |
| --- | --- | --- | --- |
| `dotmac-numbering` a2 | `execute_once` / `execute_once_platform` at `…/service.py:481,492` | `requires=(MODULE_DATABASE_ROLES_V1.name, IDEMPOTENCY_LEDGER_V1.name)` (`manifest.py:57`) | — declared |
| `dotmac-integration` a3 | `execute_once_platform` at `…/dotmac_integration/idempotency.py:97` | **absent — the manifest has no `requires` field at all** (`manifest.py:36-93`) | **PR #204, OPEN DRAFT** — `feat(integration): declare the at-most-once ledger it already writes (a4)`. Known, unmerged, unpublished. |
| `dotmac-entitlement-allocation` a4 | `execute_once_platform` at `…/service.py:375` | **absent — no `requires` field** (`manifest.py:35-45`) | **NONE.** No open or merged PR addresses it. |

`dotmac-entitlement-allocation` is the finding. Integration's omission is
already known and has a draft fix in flight; entitlement-allocation's does not,
and it is the module with the tighter coupling — its
`write_platform_audit_event` sits INSIDE the `execute_once_platform` operation
(`service.py:357` within `_operation`, invoked at `:375`), so it needs BOTH
kernel facilities' tables in one transaction and declares neither.

Both undeclared modules are published and pinnable today. Both are
platform-plane modules whose adopting assemblies (`dotmac_integrator`, and the
Vendor CP path ADR-0027's supersession note describes) are precisely the
assemblies that might not run the kernel's own lineage.

**There is no generic guard, and its absence is measurable.** The only
enforcement is a hand-written per-module test,
`tests/architecture/test_numbering_module.py:384-393`, which asserts the string
`execute_once` appears in numbering's source and that numbering declares the
prerequisite. Nothing generalises it — which is why the sweep that found
integration's omission and produced PR #204 did not also find
entitlement-allocation's, even though a single grep for `execute_once` across
`packages/` returns both.

This matters directly to the question this report was commissioned to answer:
**adding a third and fourth named prerequisite does not help if the mechanism
that ties a caller to a declaration is a per-module test somebody has to
remember to write.** The audit facility would inherit exactly the same
enforcement gap on day one.

### 8.2 `dotmac_kernel.audit`'s compatibility rule rests on a false premise

`packages/dotmac-kernel/src/dotmac_kernel/audit.py:142-146`:

> Carries one temporary compatibility rule. **Released `dotmac-template-studio`**
> calls `write_audit_event` with only `actor_party_id` — nine call sites — and
> **a released artifact cannot be edited retroactively.**

The nine call sites are real (§ 3 bucket 1) and they do pass only
`actor_party_id`. But `dotmac-template-studio` **has never been released**: no
`dotmac-template-studio-v*` tag exists in any version, it appears in no release
lane, `docs/MODULE_CATALOG.md:57` marks it `not allowlisted`, and
`docs/inventories/declared-publication-baseline.json` records it as *"Not in any
release lane … so no workflow can publish it and no consumer can pin it."*

The stated justification for the derivation in `resolve_audit_actor` —
irreversibility of a published artifact — does not hold. The nine call sites are
editable in this repository. Whether the derivation should stay on its own
merits is a separate question and is not decided here; what is established is
that the premise recorded in the kernel's own docstring is false.

### 8.3 `migration-collisions.md` is stale on Sub, in the direction that matters

`docs/inventories/migration-collisions.md:260` records that Sub's
`domain_settings` "adds neither" `tenant_id` nor a scope column. At
`db2e524f` Sub has **all three** scope columns and the kernel's
COALESCE-sentinel unique index, added deliberately by
`dotmac_sub/alembic/versions/507_domain_settings_scope_columns.py`. Its
`domain_setting_history` is kernel-exact by construction (`520:8-10`), and its
`audit_events` gained `actor_label`, `actor_party_id`, `details` and
`created_at` in `413` and `526`.

The document is undated and carries no revision pins in its header, so there is
no way to tell from the document itself how far it has drifted. Any future
decision that cites it for adopter shapes should re-measure first. This report
did.

### 8.4 An adopter already works around the missing name with a physical edge

`dotmac_vendor_control_plane/alembic/versions/v001_vendor_accounts.py:30`
carries `depends_on = "0009_platform_audit_inbox"`. `prerequisites.py:1-30`
opens by naming this construct — a physical Alembic edge naming a foreign
revision — as "a lie in every assembly that does not run the named lineage", and
the entire logical-prerequisite mechanism exists to replace it.

The edge is truthful in VCP, which does run the kernel lineage, so nothing is
broken. But the reason it is there is stated in its own docstring: the kernel's
platform catalogue must exist because the AccountService's audit backing needs
it. That is a runtime persistence dependency being expressed as a migration
edge, because no name for it exists. The existing dossiers do not record this.

### 8.5 The two facilities are asymmetric in a way the framing did not anticipate

The commission treated audit and settings as two instances of one shape. They
are not. Audit has real published consumers on one plane and none on the other.
Settings has **no published consumer at all** — its only module consumer is
transitive, unpublished, and half of its dependency is a Python spec registry
rather than a table.

---

## 9. What this report does NOT establish

- **It does not name, propose or reserve any prerequisite name.** No spec, no
  verifier, no `EXTRACTION.toml`, no registration file, no ADR was written or
  edited. § 5's contract shapes are descriptions of tables that already exist.
- **It does not run anything.** No test, no migration, no database. Every
  statement about RLS, grants, constraints and indexes is read from migration
  source, not observed on a live catalogue. A live-catalogue read would be a
  different and stronger evidence class, and this is not it.
- **It does not establish that the `SELECT`s in § 4 execute in any deployment.**
  `dotmac-template-studio` is unpublished; its web routes have no route-level
  test in this repository (`tests/unit/test_template_studio_{renderer,seeding,
  service}.py` cover the renderer, seeding and service only, and none of them
  drives `web.py` or `router.py`). The dependency is established by reading the
  dependency chain, not by observing a request.
- **It does not measure adopter intent.** ERP and Sub owning same-named tables
  proves a collision exists; it does not prove either product intends to adopt
  the kernel's audit or settings storage. Both ledgers say the opposite —
  `defer-db` and `blocker`. `docs/inventories/audit-events-disposition.md` (as of
  2026-08-12) records that audit R1 is "implemented on local integration
  branches, but is not released or deployed", which is a different and much later
  question than the one asked here.
- **It does not verify the cross-repo reads a second time.** § 6 rests on a
  single delegated pass over three repositories at the three pinned SHAs. Line
  numbers were reported with the citations and spot-checked for internal
  consistency, not re-derived independently. One known-stale pointer was found
  inside ERP's own ledger (`:534` cites
  `app/models/domain_settings.py:77`; `DomainSetting` is at `:200` at
  `696f2d53`), which is a reminder that line citations in long-lived documents
  drift.
- **It does not decide whether the vendor control plane's physical edge is a
  defect.** § 6.4 establishes that the edge exists and what it works around. VCP
  runs the composed kernel lineage, so the edge is TRUTHFUL there — it is not
  broken today. What it demonstrates is the absence of a name, not a live fault.
- **It does not survey the other undeclared persisted dependencies it noticed.**
  At least three more kernel facilities have the identical shape and were
  outside the commissioned scope: `dotmac_kernel.deps.require_user_auth` reads
  `auth_sessions`, `parties` and `party_role_grants` (`deps.py:69,81,124`) on
  every guarded route in every module; `dotmac_kernel.flag_models.resolve_flag`
  reads `feature_flag_overrides` (`flag_models.py:123`) and is called by
  `dotmac-template-studio/service.py:57`; and `dotmac_kernel.branding` reads
  `domain_settings`. Whether the answer is four more prerequisites or a
  different instrument entirely is a decision, not an inventory finding.
- **It does not check the private index.** Publication status is measured by git
  tag, following this repository's own stated oracle. A version whose tag step
  failed reads here as unpublished.

---

## 10. Recommendation

**`dotmac_kernel.audit` — the PLATFORM plane warrants a named prerequisite; the
TENANT plane does not, yet.**

Evidence for the platform plane, in ascending order of force:

1. Two published, pinnable modules (`dotmac-integration` a3,
   `dotmac-entitlement-allocation` a4) write `platform_audit_events` at request
   time — two kernel invocations reached from eight independent paths — and
   neither declares anything at all. That is
   the `idempotency_ledger.v1` fact pattern reproduced exactly — a clean
   migration followed by an `UndefinedTable` in the adopter's application.
2. Both are platform-only modules whose target assemblies are exactly the ones
   that may not run the kernel's tenant lineage.
3. **A foreign adopter has already reached for the forbidden instrument in the
   name's absence.** VCP guarantees the table with a hand-authored
   `depends_on = "0009_platform_audit_inbox"`
   (`dotmac_vendor_control_plane/alembic/versions/v001_vendor_accounts.py:30`),
   with a docstring naming the tables it is reaching for, and writes to that
   table 18 times across 10 production files, inside the unit of work. The
   physical-edge-naming-a-foreign-revision is the construct
   `prerequisites.py:1-30` opens by calling "a lie in every assembly that does
   not run the named lineage". `idempotency_ledger.v1` was justified by a
   predicted failure; this is justified by an existing workaround in
   checked-in code.
4. The platform peer is the half that does **not** collide with any adopter
   table, so a shape verifier for it can actually pass somewhere.

Evidence against the tenant plane: zero published callers. Its only caller has
no tag, no release lane, and is documented as the fixture the allowlist gate's
own sensitivity proof uses. Both products that own the name have ruled the
kernel's tenant audit table a blocker in a checked-in ledger. Naming a
tenant-plane requirement today would add a thing every blocked adopter must
supply in exchange for a dependency no installable artifact has — the cost
`tests/unit/test_prerequisites.py:62-64` explicitly warns about.

**This is where the audit facility diverges hardest from the ledger
precedent, and the divergence should be stated before anything is accepted.**
`idempotency_ledger.v1` names both planes in one spec because a consumer
"cannot take the tenant half without linking code that references the platform
table" (`prerequisites.py:203-205`). Audit is the opposite: the two entry
points are independent, today's published callers use only one of them, and the
adopter collision evidence lands entirely on the other. Following the
both-planes precedent here would import a blocker into every adopter for a
dependency none of them has.

One further caveat: the effect a provider would have to supply includes
`platform_admins`, because of `fk_platform_audit_events_admin` — materially
larger than the ledger's two standalone tables.

**Settings storage — does NOT warrant a named prerequisite. Not enough
consumers to justify one, and a prerequisite is the wrong instrument for the
dependency that does exist.**

Evidence: zero direct module consumers across all eleven packages — every grep
hit is prose. The only module-side dependency is transitive, belongs to a module
that has never been released, and is **half a Python spec registration rather
than a table**: the `display` specs live in the reference assembly
(`app/features/settings/spec.py:94-123`), so an adopter with a byte-perfect
`domain_settings` still fails with `KeyError` on the first portal request. A
DDL verifier cannot see that half, so it would certify a database that does not
work.

The adopter evidence points the same way, from three directions:

- **Nobody is waiting on the kernel for this storage.** Sub supplies its own
  table and spent migrations `507`, `512`, `514`, `520` and `523` converging it
  onto the kernel's shape so the kernel's *resolver* would work over it (§ 6.2,
  § 6.3). That is a compatibility relationship, not a provisioning one. ERP has
  ruled the kernel's table a blocker until E8. VCP inherits it and never reads
  it.
- **`domain_settings` cannot be described in the plane vocabulary.** It is a
  single table serving both scopes through a **nullable `tenant_id`** — the
  exact shape ADR-0023's plane gate refuses — and is the documented exception to
  hard rule 11. An adopter's own composed-catalog gate reached this conclusion
  independently: VCP's `test_composed_live_catalog.py:120-126` excludes
  `domain_settings` and `domain_setting_history` from plane auditing precisely
  because their nullable tenant column puts them in neither plane.
- **A shape verifier would refuse Sub's deliberate divergences.** Sub's
  `scope_kind` server default is `platform`, its value CHECK is at-least-one
  rather than exactly-one, and its `created_at` on `audit_events` is nullable —
  each chosen on purpose and documented in the migration that made it. A
  verifier strict enough to be worth having would fail the fleet's most
  converged adopter for its own reasoned choices.

**The finding that should be acted on before either of the above** is § 8.1.
`dotmac-entitlement-allocation` a4 is published, calls `execute_once_platform`,
declares nothing, and has no fix in flight — while its sibling
`dotmac-integration` does (PR #204). The sweep that caught one missed the other,
because the only enforcement is a per-module test somebody has to remember to
write. Naming more prerequisites without closing that is adding vocabulary to a
mechanism that is not being applied — and a platform-audit prerequisite would
inherit the same gap on the day it ships.
