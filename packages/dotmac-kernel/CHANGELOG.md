# Changelog — dotmac-kernel

All notable changes to the `dotmac-kernel` distribution. This package follows
[Semantic Versioning](https://semver.org); see `COMPATIBILITY.md` for the
public-surface stability policy. Pre-1.0 (`0.x`, incl. this alpha) the surface is
still settling — a `0.MINOR` bump may carry breaking changes, each called out
here.

## UNRELEASED — prototype, not published

**This is not a release and must not be published.** The work below sits on
branch `docs/omni-inbox-sources` as **audit evidence** for the fleet
decomposition matrix, not as a declared kernel owner.

Directive 2026-08-12: *"do not publish a41, do not prioritize the rename, and do
not declare a kernel owner yet. First place its constituent capabilities in the
fleet matrix and identify their current authorities, first cutovers, dependencies
and retirement gates."*

`docs/superpowers/plans/2026-08-12-fleet-decomposition-matrix.md` decomposes this
work into **eight** separate capabilities (rows I1–I8), several of which do NOT
belong in the kernel. Treat every "Owner of:" claim in the modules below as a
**proposal under adjudication**.

### Prototyped (not owned, not released)
- **`dotmac_kernel.inbound` + `.inbound_models` — an inbound seam.** The
  communications stack was entirely OUTBOUND; there was no webhook-receiver
  contract, no model for "this tenant connected this mailbox", and nowhere for
  per-connector credentials. Two tables (migration `0022_inbound_seam`):
  `connected_accounts`, the registry every `account_scope` downstream comes from,
  and `inbound_observations`, the durable normalized provider fact.
  `admit()` **delegates at-most-once to `idempotency.execute_once`** (scope
  `"inbound"`) rather than re-deciding it — hard rule 21 — and owns only the
  payload that makes consequences re-derivable after a parsing fix.
  `InboundReceiver` is a `Protocol` and the kernel ships **no clients**: SMTP,
  IMAP and Meta stay product dependencies, exactly as `delivery_providers` does
  for sending. `verify` is separate from `parse` because a signature is over raw
  bytes and re-serialising defeats it.
  `credential_name` holds a NAME resolved through `secret_sources`, never a
  secret value (ADR-0009).
- **`dotmac_kernel.channels` — the one channel registry.** The kernel knew about
  channels in three places and validated them in none:
  `consent.register_numeric_channels` (a real per-channel behaviour registry, for
  one facet), `channel_policy` (an open string), and
  `delivery_providers.OutboundMessage.channel` (a bare `str`). A fourth was about
  to appear in a module — and modules may not import each other, so no module can
  be the source for consent, channel policy and delivery alike. One `ChannelSpec`
  with four traits: `address_form`, `transport`, `thread_identity`,
  `message_id_scope`.
- `AddressForm` has **three** values (`EMAIL`/`PHONE`/`OPAQUE`), not two. A
  two-value addressable/opaque split collapses email and phone and would have
  lost the numeric distinction — reintroducing the punctuation-dodge bug
  `register_numeric_channels` exists to prevent.

### Changed
- `consent.normalize_address` reads `channels.address_form_for` instead of a
  private `_NUMERIC_CHANNELS` set. `sms` and `whatsapp` ship pre-declared as
  `PHONE`, and an undeclared channel still lowercases, so **normalisation is
  byte-identical to a40** for every existing deployment.
- `consent.register_numeric_channels` is now a thin ADAPTER over the registry —
  the same relationship `messaging.process_once` has to `idempotency`. It stays
  in the published surface and keeps working; it can only say "phone", so prefer
  `channels.register_channels(...)`. Re-declaring a channel as numeric when it is
  already declared with a different address form now raises rather than silently
  disagreeing.
- `consent._reset_registries_for_tests(numeric=...)` now clears the channel
  registry's defaults when an explicit set is passed, so a caller restoring an
  exact prior set no longer finds `sms`/`whatsapp` re-added underneath it.

### Added
- `INBOX_MIGRATION_OWNER` in `MIGRATION_OWNER_LEDGER` — owner `inbox`, schema
  `mod_ibx`, migration prefix `ib`, branch label `inbox`. `dotmac-inbox`
  0.1.0a1 cannot be composed by an earlier kernel:
  `NamespaceRegistry.from_manifests` raises `UnallocatedNamespaceError` for a
  stateful module with no ledger row, which is why that package floors on this
  release rather than merely preferring it.

## 0.1.0a40 — 2026-08-11

An adoption-demanded database invariant fix. Sub's S7 migration rehearsal found
that raw SQL could create a `domain_settings` row with
`scope_kind='tenant'` and `tenant_id IS NULL`: stored, but unreachable by the
resolver.

### Fixed
- **Coherent database default.** `scope_kind` now defaults to `platform` at the
  database boundary, matching nullable `tenant_id`. ORM writes keep the
  context-aware default and still derive tenant scope when a tenant is named.
- **Database enforcement.** Migration `0021_setting_scope_alignment` repairs
  the exact tenant/NULL shape created by the old default, refuses ambiguous
  rows, and adds `ck_domain_settings_scope_alignment`.
- **Product adoption.** If a product already carries the exact CHECK and
  platform default (Sub migration 514), migration 0021 verifies and adopts the
  existing invariant. Its ownership marker makes downgrade preserve the
  product-owned predecessor instead of deleting it.

## 0.1.0a39 — 2026-08-11

Namespace allocation for the second installable module. Additive; no behaviour
change to any existing surface.

### Added
- `TICKETING_MIGRATION_OWNER` in `MIGRATION_OWNER_LEDGER` — schema `mod_tkt`,
  revision prefix `tk`, branch label `ticketing`. Allocated for
  `dotmac-ticketing`, whose manifest lands in the same change as the allocation
  rule requires. Without this row `NamespaceRegistry.from_manifests` refuses the
  module at boot, which is why the module's kernel floor is this release.

## 0.1.0a38 — 2026-08-11

Academy's assembly adoption closes two composition gaps. Additive; no migration.

### Fixed
- Permission and capability declaration validation now expands FastAPI 0.140's
  lazy included-router contexts. Included product routes can no longer bypass
  the boot-time walker simply because they are not materialized as `APIRoute`
  objects in `app.routes`. The wheel-consumer gate pins FastAPI 0.140.13 and
  proves both undeclared guard types still fail closed; the existing floor gate
  continues to cover FastAPI 0.111.

### Added
- **Explicit product surface control.**
  `ProductAssemblySpec.platform_surface_enabled=False` omits both kernel
  platform routers without a product deleting already-mounted FastAPI routes.
- **Product startup lifecycle.** `startup_checks` declares product validation
  with the kernel's warning-in-development/fatal-in-production semantics;
  ordered sync or async `startup_hooks` run inside the FastAPI lifespan and
  fail startup if initialization is incomplete.
- **`ProductSecurityPolicy`.** A product can declare a default CSP plus COOP
  and CORP values while the kernel remains the single security-header writer.
  The environment CSP still wins over the product default, and policy values
  reject newline/header injection at assembly construction.

## 0.1.0a37 — 2026-08-11

Review hardening for consent and provider delivery. This amends the unpublished
kernel migration `0020` before its first product cutover.

### Fixed
- Provider callback idempotency is now
  `(tenant_id, provider, provider_message_id, status)`, preserving real
  `accepted` → `delivered`/`bounced` transitions while deduplicating repeated
  copies of one status. Concurrent duplicates converge through a savepoint
  instead of leaking `IntegrityError` or rolling back tenant context.
- Consent canonicalises channel identity as well as the address, so spelling a
  channel as `Email` cannot bypass an `email` suppression. Concurrent duplicate
  suppressions likewise converge on the canonical row.
- Every outbound message now carries a required stable `dispatch_id`. A
  committed receipt short-circuits an outbox settlement retry before the
  provider is called again, a request fingerprint rejects reuse for different
  content, and adapters can forward `message.idempotency_key` to providers that
  support idempotent requests.
- `OutboundMessage.category` is now required and rejects blank values, closing
  the path where an omitted category silently defaulted to transactional and
  bypassed a marketing suppression.

### Breaking
- Construct `OutboundMessage` with both `dispatch_id=<UUID>` and a non-empty
  `category`. The product/outbox creates the dispatch id once and reuses it on
  every attempt.

## 0.1.0a36 — 2026-08-10

The provider seam and the channel-policy reader (ADR-0006 § 5c/5d). Additive; no
migration.

### Added
- **`dotmac_kernel.delivery_providers`** — a `DeliveryProvider` Protocol, an
  `OutboundMessage`/`ProviderResult` pair, and `send()`, the ONE send path.
  `send` asks consent first and returns `Suppressed` **without calling the
  provider** — no network request, no cost, no receipt to reconcile — then
  records the receipt, which closes the bounce→consent loop.

  Routing every send through one function is what makes "ask consent first"
  structural rather than a convention. Sub's history is what a convention buys:
  marketing eligibility lived inside the campaign segment filter, so the answer
  depended on who was asking.

  The kernel ships the Protocol and **no client** — SMTP, Twilio and Meta Cloud
  API are product dependencies, mirroring ADR-0009's `SecretSource` seam.

- **`dotmac_kernel.channel_policy`** — `make_spec()` builds the `SettingSpec` a
  product registers in a domain it owns, `validate_policy_document` is its
  write-time validator, and `resolve_channels()` reads it: event → category →
  default → the caller's fallback.

  **No table and no migration.** The channel-policy dossier found this § 5c owner
  dissolves into the settings facility the kernel already has; what was missing
  was a typed reader. Sub's legacy per-event shadow setting is deliberately not
  ported.

  A malformed stored document degrades to the caller's fallback on READ, because
  resolution happens on the send path and one operator's typo must not become a
  total delivery outage. The loud failure is on WRITE, where the operator is
  present to see it.

## 0.1.0a35 — 2026-08-10

Delivery receipts, and the loop that keeps the consent ledger honest (ADR-0006
§ 5c). Additive; carries kernel migration `0020`.

### Added
- **`dotmac_kernel.delivery`** and **`delivery_models`** —
  `communication_deliveries` records what the PROVIDER said about an outbound
  message (its id, verdict, response code, when), and `record_receipt` is the
  only writer.
- **The bounce→consent feedback loop.** A `bounced` or `complaint` receipt
  suppresses the address with scope `all`, in the SAME transaction as the
  receipt. **This loop exists in neither product**: verified in `dotmac_sub` at
  `5d6f115b7`, `DeliveryStatus.bounced` is declared and never assigned,
  `SuppressionReason.bounce`/`.complaint` have zero call sites, and the campaign
  unsubscribe link is the only writer of a suppression anywhere. Sub's ledger is
  unsubscribe-only in practice, so the `all` scope that protects transactional
  delivery is never populated by anything automated. A consent ledger nothing
  writes to answers "yes, send" forever.
- **Idempotent provider callbacks.** A partial unique index on
  `(tenant_id, provider, provider_message_id) WHERE provider_message_id IS NOT
  NULL` makes an at-least-once webhook safe to redeliver, while still allowing
  receipts for synchronous failures that never got an id.

### Deliberately NOT added
- **A queue.** Sub's `Notification` table is `dotmac_kernel.messaging`'s
  `OutboxEvent` built a second time — status/attempts/backoff/lease-reclaim/
  dead-letter all appear twice. Porting it would install the duplicate
  permanently; ADR-0014 already says non-transactional effects belong in the
  outbox. Evidence: `docs/inventories/delivery-outbox-sources.md`.
- **Provider clients.** SMTP, Twilio and Meta Cloud API clients are product
  dependencies, as ADR-0009 ships a `SecretSource` seam and no store client.

### Note for adapter authors
Only `bounced` and `complaint` suppress. A SOFT bounce — mailbox full,
greylisted — must be recorded as `failed`, never `bounced`, or a full inbox
permanently stops that customer's invoices. The kernel cannot classify that
("5.1.1 user unknown" is permanent, "4.2.2 mailbox full" is not, and every
provider spells them differently), so the adapter classifies and this module acts
on the classification.

## 0.1.0a34 — 2026-08-10

The do-not-contact ledger gets one owner (ADR-0006 § 5c). Additive; carries
kernel migration `0019`.

### Added
- **`dotmac_kernel.consent`** — the one service that answers *may we send
  `<category>` to `<address>` on `<channel>`?*, and
  **`dotmac_kernel.consent_models`** with the tenant-scoped
  `communication_suppressions` table behind it. Ported from
  `dotmac_sub:app/services/communication_eligibility.py`, the fleet's only
  qualifying implementation, with its 18 behaviour tests
  (`tests/unit/test_consent.py`). ERP has no consent implementation at all while
  sending invoices and offer letters by email — evidence in
  `docs/inventories/consent-suppression-sources.md`.

  The rule that carries: **an unsubscribe is a refusal of marketing, not
  permission to stop sending someone their invoice.** A suppression is scoped
  `marketing` or `all`; only bounces, complaints and erasure set `all`. An
  unknown category is treated as TRANSACTIONAL, because the failure mode of that
  default is an unwanted promo and the failure mode of the opposite default is
  an unsent invoice.

  Suppression escalates (`marketing` → `all`) and never de-escalates, so a hard
  bounce cannot be downgraded by a later unsubscribe click.
  `unsuppress_marketing` refuses to clear an `all`-scoped row, so campaign
  administration is not authority to lift a bounce.

- **`register_marketing_categories()` / `register_numeric_channels()`** — the
  product declares its vocabulary, the kernel owns the rule (ADR-0008). Sub's
  hardcoded `{"marketing", "campaign", "promotion"}` is a product's words; a
  deployment that declares nothing gets a ledger where only `all` bites, which
  is the safe direction.

- **Migration `0019_communication_consent`** — `tenant_id NOT NULL`, a composite
  unique including it, RLS ENABLEd and FORCEd, an isolation policy and the
  online-role grants, all in the one migration. Sub's source table has none of
  this (it is single-tenant); a consent ledger is the worst table to leak, since
  a cross-tenant read exposes who complained and a cross-tenant write can
  silence another tenant's invoices. Proven in `tests/test_consent_isolation.py`.

### Deliberately not ported
Sub's four `*_committed` wrappers (`suppress_committed` and siblings) call
`db.commit()`. `dotmac_kernel.db` is the one transaction authority, and a service
that manages its own transaction cannot compose into a caller's unit of work.

## 0.1.0a33 — 2026-08-10

At-most-once execution gets one owner (ADR-0014). **Breaking** (alpha window)
and carries kernel migration `0018`.

### Added
- **`dotmac_kernel.idempotency`** — `execute_once` / `execute_once_platform` run
  an effect at most once per `(tenant_id, scope, key)` and replay the recorded
  result otherwise. `fingerprint_of(payload)` builds the stable request digest;
  a key replayed with a DIFFERENT fingerprint raises `IdempotencyConflict` (a
  `ConflictError`, so existing HTTP mapping gives 409) instead of silently
  returning someone else's result. `IdempotentOutcome.replayed` lets a caller
  tell a replay from a first execution. `purge_expired` applies retention.
- **`dotmac_kernel.idempotency_models`** — `IdempotencyRecord`,
  `PlatformIdempotencyRecord`, `IdempotencyStatus`, `INBOX_SCOPE`.
- **`dotmac_kernel.deps.idempotency_key`** — the `Idempotency-Key` header
  dependency. Optional by design: which routes REQUIRE a key is the product's
  decision; the kernel owns the header's spelling and its length limit.

### Changed — BREAKING
- `InboxRecord` → `IdempotencyRecord`, `PlatformInboxRecord` →
  `PlatformIdempotencyRecord`, and both moved from `dotmac_kernel.messaging
  .models` to `dotmac_kernel.idempotency_models`. Tables renamed
  (`inbox_records` → `idempotency_records`, `platform_inbox_records` →
  `platform_idempotency_records`), columns renamed (`command_id` → `key`,
  `command_type` → `operation`), columns added (`scope`, `fingerprint`,
  `expires_at`), recorded status `'processed'` → `'executed'`.
- `messaging.process_once` / `process_once_platform` / `ProcessOutcome` keep
  their exact signatures and behaviour — they are now thin adapters over
  `idempotency`, writing `scope="inbox"`. Consumers of those functions need no
  source change; a consumer importing the RECORD MODELS does.

### Migration
- `0018_idempotency_one_owner` — RENAMEs both ledgers rather than
  create-and-copy, so no dedup marker is lost in the window (a lost marker means
  a processed command re-executing). Backfills `scope='inbox'`, widens the
  tenant unique to `(tenant_id, scope, key)`, and re-points the RLS policy,
  FK and index names.

### Why
Six idempotency mechanisms existed across the fleet
(`docs/inventories/idempotency-sources.md`): Sub's shared ledger overloads one
untyped column with three incompatible meanings across 26 hand-rolled call
sites; ERP reserves a placeholder BEFORE the effect, so a died request is
replayed as "in progress" for 24h with no recovery path; Sub's task retry key
embeds a timestamp, making the retry itself non-idempotent. The kernel already
had the correct primitive and had filed it under "inbox", where nobody found it.

## 0.1.0a32 — 2026-08-10

Two things an assembly could not get from the public surface. No migration; the
new setting defaults to the existing behaviour.

### Added
- **`resolver_session()`** — an UNSCOPED session on the main engine, for
  deciding which tenant to scope to. The one legitimate reason to run without a
  scope, and the one thing the surface had no name for: this package's own
  `TenantResolverMiddleware` reached for `SessionLocal` while the public-surface
  test forbade consumers the same import. That is not a rule with an exception,
  it is a missing primitive.

  Read-only by construction (always rolls back, never commits). It RESETs the
  tenant setting before yielding: a scope inherited from a pooled connection
  would filter the resolver's own lookup, and because RLS fails closed the
  symptom would be a valid host resolving to no tenant.

- **`TENANCY=single`**, enforced at startup. ADR-0003 makes a dedicated
  one-tenant deployment per ISP the safe default, and nothing enforced it: a
  deployment that acquired rows for a second tenant — restored backup, migration
  rehearsal, a shared database someone meant to split — would serve them to
  anyone who knew the host, with no error state, because the host resolved fine.

  Under `single` the lifespan asserts exactly one tenant row exists and binds to
  it; two rows, or none, is a startup failure (fatal in production, a warning
  elsewhere, matching the existing config checks). An unreachable database is
  not a tenancy verdict and returns no error.

- **`dotmac_kernel.tenancy`** — the binding that check produces, read by the
  resolver to refuse a tenant created *after* startup.

### Two decisions worth stating

**The deployment declares the mode, not the identity.** `TENANCY=single` says
one tenant lives here; it does not name which. The tenant is already a row, and
naming it in configuration would be a second source of truth that can drift —
with a typo taking the deployment down for no reason. The binding is discovered
from the database, so it cannot disagree with it.

**The assertion belongs at startup, not per request.** Refusing a wrong host is
a symptom-level control that fires only if somebody tries. Refusing to boot with
two tenant rows catches the hazard itself, at deploy time. The per-request gate
is the second half only.

`multi` remains the default: safe-by-default would be `single`, but flipping it
would change every existing deployment at once. Declare it explicitly first.

### Why now

`dotmac_academy_app` had implemented the lockdown privately, which is the only
reason it was noticed — and it was why adopting the kernel's middleware would
have been a downgrade. That generalises: **a shared component must be a superset
of what it replaces, or adoption silently removes a control.** The assembly's
tests cannot catch it, because they do not know the kernel exists.

## 0.1.0a31 — 2026-08-10

Widen the FastAPI ceiling so a consumer on a newer web stack can install the
kernel at all. No migration, no API change.

### Changed
- **`fastapi` moves from `>=0.111,<0.116` to `>=0.111,<0.141`.** The ceiling was
  set when every assembly sat on 0.111. `dotmac_academy_app` is on 0.140, and
  resolution failed outright — a kernel that cannot be installed beside a
  consumer is not a shared kernel, whatever its API looks like.

  The floor stays 0.111 because erp and sub pin it exactly. Both ends of the
  range are exercised: `kernel-floors` installs the declared floor and proves it
  is real, and the main matrix resolves to the newest allowed.

### Why the ceiling was wrong

An upper bound is a claim about compatibility, and this one had become a claim
about the fleet's habits instead. Nothing in the kernel needed `<0.116`; it was
where everyone happened to be. That is the kind of constraint that silently
decides who is allowed to adopt the platform.

## 0.1.0a30 — 2026-08-10

`tenant_session_by_slug`. No migration, no breaking change.

### Added
- **`tenant_session_by_slug(slug)`** — yields `(db, tenant)`. Every assembly's
  CLI needs the same two steps: look a tenant up by the slug an operator typed,
  then act as that tenant. Each one solving it privately means each one reaching
  for `SessionLocal`, which the public-surface test forbids — so the kernel owes
  them the entry point.

  The lookup and the scope share one session deliberately: `tenants` is not
  tenant-scoped, so the pre-scope query is legal, the returned `Tenant` is still
  attached when the caller gets it, and no second connection is taken to resolve
  a name. It raises `NotFoundError` rather than yielding `None`, because a CLI
  handed a `None` tends to carry on and print an empty report.

### Why now

That "legal unscoped lookup" is the trap. `dotmac_academy_app` resolved a tenant
by slug, forgot the scope, and got a working query followed by silence — its
`audit-banks` printed `TOTAL 0 0` against 333 banks. The lookup succeeding is
precisely what made the omission invisible, so the fix is to stop asking callers
to remember the second step at all.

## 0.1.0a29 — 2026-08-10

Fixes a defect in `tenant_session` as shipped in 0.1.0a28. No migration.

### Fixed
- **The tenant scope now survives a commit inside `tenant_session`.** It was
  applied with `SET LOCAL`, which is transaction-scoped, so the first
  `db.commit()` inside the block discarded it. `expire_on_commit` then reloads
  attributes on the next statement, that statement runs unscoped, RLS fails
  closed, and a row the session itself just wrote comes back as
  `ObjectDeletedError`. Every intended caller — a CLI loop, a worker draining a
  queue — commits more than once, so the boundary failed precisely for the
  callers it was added for.

  `set_tenant` takes `transaction_local` (default `True`). The two values are
  not interchangeable: `get_db` needs `True`, because its session is pooled and
  a scope outliving the transaction would be inherited by the next request to
  borrow that connection. `tenant_session` needs `False`, and now issues
  `RESET app.current_tenant` before returning the connection to the pool — a
  session-level setting that survived would be the very cross-tenant leak
  `SET LOCAL` exists to prevent. The reset never masks the caller's exception.

### Why it was missed

`dotmac_academy_app` found it within minutes of running `load-banks` against
production against the equivalent fix in its own fork. The 0.1.0a28 tests
asserted the scope was applied and did not widen; none of them committed. A
boundary's contract includes how long it lasts, and that is now pinned.

## 0.1.0a28 — 2026-08-09

A tenant-scoped session boundary for code outside the request cycle. No
migration, no breaking change.

### Added
- **`tenant_session(tenant_id)`** — the tenant-scoped sibling of
  `platform_session`, for CLI commands, jobs and workers that act as one tenant
  rather than as the platform. Applies the RLS scope before yielding, so there
  is no window in which a query can run unscoped, and commits or rolls back on
  the same owned-boundary contract as the other session helpers.
- **`set_tenant(db, tenant_id)`** — the one writer of `app.current_tenant`,
  split out of `get_db` so the scope has a name. `get_db` now calls it.

### Why

`platform_session` already existed for non-request platform work; there was no
equivalent for non-request *tenant* work, so callers reached for `SessionLocal`
directly — which the public-surface test forbids precisely because it skips the
scope. The failure is silent by construction: RLS fails **closed**, so an
unscoped session returns zero rows instead of raising, and a caller cannot
distinguish an empty tenant from an invisible one.

Found in `dotmac_academy_app`, which carries a fork of this module. Its
`audit-banks` command printed `TOTAL 0 0` — a clean estate — against a
production database holding 333 question banks and 3,210 questions for the
tenant it had been asked about. Its `load-banks` command was blind the same way
and had silently deployed nothing for 37 commits. Every assembly running CLI or
batch work through a bare `SessionLocal` has the same exposure.

## 0.1.0a27 — 2026-08-09

A `list` setting value type. No migration — `0.1.0a15` already replaced the
CHECK constraint that named types with one that names only the invariant, so a
new JSON-stored type needs no schema change.

### Added
- **`list` — an ordered JSON array.** `json` is an OBJECT type and stays one:
  its `to_storage` rejects anything that is not a `dict`, and a spec whose
  default is a list is refused at registration rather than resolving to a value
  its own type forbids. That left a sequence setting — audited HTTP methods,
  excluded path prefixes, preset amounts — with nowhere correct to live, so
  products encoded them as comma-separated strings each reader re-split.

  Widening `json` to accept arrays would have been the cheaper fix and the
  wrong one: a reader could no longer know whether to expect `.get(...)` or
  `[0]`, which is the ambiguity a value type exists to remove.

  A `tuple` is accepted and stored as an array, because declarations here are
  frozen dataclasses holding tuples. `str`, `bytes` and `set` are refused
  explicitly — the first two are sequences, so a coercing implementation would
  store `"abc"` as `["a", "b", "c"]`; a `set` is refused because order is part
  of the value. On read, a value stored as JSON text is still parsed, so a row
  written before this type existed does not degrade every reader to the
  default.

  This is a kernel built-in rather than a product registration on purpose. The
  value-type registry is open, so a product *could* declare its own `list` —
  and then a second product declares an incompatible one and the fleet's
  vocabulary forks, which is the failure ADR-0008 exists to prevent. A shape
  every product needs belongs to the kernel.

### Changed
- `json`'s `to_storage` now rejects a value the column cannot serialise,
  instead of letting the write fail at flush as a driver error naming a column.
  A value that was previously accepted here always failed a moment later.
## 0.1.0a26 — 2026-08-09

ADR-0013: a module declares the question, the deployment declares the answer.
No migration.

### Changed — BREAKING (API)
- **`ProductAssemblySpec.settings_overrides` becomes `setting_defaults`**, and
  its semantics are corrected with its name. It was documented as "applied on
  top of env/defaults" — i.e. winning over stored values, which is the defect
  ADR-0011 removed from `env_var`. Nothing read it, so both are fixed together.
  A profile default now LOSES to every stored row and WINS over the module's
  fallback:

      scope chain  ->  profile default  ->  spec fallback

### Added
- **Per-deployment setting defaults.** A module declares what a setting IS —
  type, constraints, `inherits`, `is_secret`, all properties of its reader. A
  deployment declares what its value should be when nothing else supplies one,
  because that varies by region, regime and topology and was otherwise
  hardcoded in module source where a deployment could not reach it.

  `dotmac_erp` shows the cost of having nowhere to put it: eight caller
  fallbacks in `auth_flow`, four disagreeing with their spec, three of those
  security properties (`refresh_cookie_secure`, `samesite`, `path`).

  Validated at startup — a default for a key no module declares is rejected (a
  profile supplies answers, it cannot invent questions), as is one its own spec
  rejects. Provenance reports `"profile"`.

- **`ProductAssemblySpec.tenancy`** — single- or multi-tenant, declared rather
  than inferred. **Nothing branches on it**; ADR-0003 forbids that. It makes the
  intent checkable (a `single` deployment growing a second tenant is a
  misconfiguration nothing currently notices) and answerable (ERP has six
  identifier settings that cannot be marked non-inheriting because nobody knows
  whether its rows are global or per-organisation).

## 0.1.0a25 — 2026-08-09

`stored_at` — what is persisted at one scope, for editors. No migration.

### Added
- **`stored_at(db, domain, key, *, scope)` returning `StoredSetting | None`.**
  An editor and a reader ask different questions, and the settings admin
  surface had only the reader's answer.

  Resolution walks the chain and degrades, which produces two bugs in any
  screen built on it. An inherited value shown in an edit box becomes an
  **accidental override** on save, with nothing on screen to warn the operator.
  And a stored value that fails its spec **degrades to the default** and
  reports `source="default"`, so the screen shows something healthy while the
  bad row persists — unshowable and therefore unfixable.

  `stored_at` never walks the chain: `None` means "no override here", which is
  what an editor must distinguish from "the inherited value happens to match".
  `raw` is the value as stored, uncoerced, so an operator can see and repair
  what the spec rejects — and `valid`/`error` say why it was rejected.

  **A secret never returns its stored value.** Returning a `StoredSetting` at
  all already answers the form's only question: whether a value is set.

  A row whose spec is gone (a retired setting, or a module no longer installed)
  is still returned, marked invalid — it is precisely the row an operator may
  want to delete, so hiding it would strand it.

### Changed
- Spec validation is extracted to one function used by both resolution and
  `stored_at`, so an admin screen and the resolver cannot disagree about
  whether a value is usable. Resolution behaviour is unchanged.

## 0.1.0a24 — 2026-08-09

A setting declares whether it inherits (ADR-0012). No migration.

### Added
- **`SettingSpec.inherits`** (default `True`). `False` reads the setting at the
  asked-for scope and nowhere else, so a less-specific row cannot answer for it.

  A fallback is the claim that a less-specific value is a valid answer. For a
  timezone or a threshold it is; for a value that IDENTIFIES something owned by
  one scope it is not — there is no "default GL account", and inheriting one
  means posting to another tenant's books.

  Found in `dotmac_erp`, which reads general-ledger account ids two ways:
  `fx_revaluation` hand-writes an organisation-only query to avoid exactly this,
  while `payment_service` reads a structurally identical account id through the
  resolver and inherits the fallback. Same data, opposite safety, decided by
  which author thought of it. ERP has eight such settings and guards one.

  Pairs with `required_at` to state "must be set here, no fallback, fail
  loudly". Honoured by both the single-key and bulk paths, and part of a spec's
  fingerprint, so two declarations differing only in this are a conflict.

### Unchanged
- The default preserves existing behaviour exactly; every current spec inherits.

## 0.1.0a23 — 2026-08-08

An adoption path for products that already have settings. No migration.

### Added
- **`dotmac_kernel.settings_shadow`** — run a product's own resolver and the
  kernel's side by side and record where they disagree, so a cutover is gated
  on evidence rather than confidence. ADR-0003 requires adapters, shadow tests
  and one-writer cutovers; for settings the kernel supplied none of it.

  `ShadowPhase` moves one way only — legacy served, then kernel served with the
  legacy still compared, then legacy no longer called. There is deliberately no
  "serve whichever is non-null" mode: that is a third answer belonging to
  neither system.

  `compare_one` / `sweep` / `sweep_scopes` never raise (a shadow phase that can
  crash a request is worse than the drift it looks for) and **never report a
  value** — only domain, key, scope and type names, because a settings table
  holds credentials and a divergence report is exactly what gets pasted into a
  ticket.

- **`ADOPTION.md`** — the five-phase recipe, the ADR-0009 decision that must
  come first, and the traps: platform-only sweeps prove nothing about tenant
  overrides, and representation differences (`Decimal("5")` vs `5`) are not
  drift and are not reported, because a report that cries wolf gets ignored.

## 0.1.0a22 — 2026-08-08

The settings read API is typed. No migration.

### Changed — BREAKING (typing)
- **`SettingSpec` is generic in the Python type its values resolve to** —
  `SettingSpec[int]`, with `default: T`. A spec whose default does not fit its
  parameter is now a type error at the declaration.
- **`resolve_value` returns `object`, not `Any`**; `resolve_many` returns
  `dict[str, object]`; `resolve_with_source` returns
  `tuple[object, SettingSource]`. These are the DYNAMIC path — keys chosen at
  runtime — so a caller must narrow. Nothing about resolution changed; only
  what the type checker will now insist you acknowledge.

  `Any` is not a weaker annotation, it is the absence of one, and it is
  contagious: a value typed `Any` silences checking in every expression it
  reaches. Removing it immediately surfaced a real unchecked call site in the
  reference assembly, where a resolved value went straight into
  `timedelta(days=...)`.

### Added
- **`resolve(db, spec, *, tenant_id=..., scope=...) -> T`** — the read a
  product should write. The spec is the key, so the declared type travels with
  it and every call site is checked.

### Why this had to land before any product cuts over
Fixing it afterwards would have meant migrating call sites twice — roughly 109
in `dotmac_sub` and ~331 in `dotmac_erp`.

## 0.1.0a21 — 2026-08-08

ADR-0009: a secret is held, never dereferenced. No migration.

### Added
- **`dotmac_kernel.secret_sources`** — a place to PUT secret material a product
  resolved from somewhere the kernel knows nothing about. `SecretSource` is a
  one-method protocol; `install_secret_source` loads it once at startup and
  `get_secret`/`require_secret` are dict lookups afterwards. Same semantics as
  `KeyProvider`: explicit `refresh_secrets()` for rotation, a failed refresh
  keeps the working set, a failing source raises rather than starting degraded,
  and names are logged but never values.

  The module performs no I/O and imports nothing that could — enforced, since
  the module that holds secrets must not also be able to fetch them.

### Decided (ADR-0009)
- **Nothing on the settings resolution path reaches a network**, for a value or
  a key. A value that cannot be held is not a setting. A row whose value merely
  looks like a store reference (`bao://...`) resolves to that string; the kernel
  does not recognise the scheme, does not fetch it, and does not fail.

  The rejected alternative — resolution *timing* as a kernel contract, with
  product-declared secret classes — would have baked one organisation's policy
  vocabulary into the kernel (the mistake ADR-0008 exists to prevent) and made
  an operational property negotiable per product. It also means a compliance
  ruling about where secrets may live is no longer a kernel input: it becomes a
  per-secret product question, answerable either way with no kernel change.

### Enforcement
- `tests/unit/test_secret_sources_no_network.py` — resolving a real encrypted secret,
  and a bulk read, with `socket.socket`/`socket.create_connection` patched to
  raise. Includes a sensitivity proof that the patch fires.
- `tests/architecture/test_secrets_are_held.py` — no module on the resolution
  path imports anything that could open a socket, with a sensitivity proof that
  the detector catches a planted import.

## 0.1.0a20 — 2026-08-08

A seam for supplying settings encryption keys from a secret store, and a fix
for key material appearing in reprs. No migration.

### Added
- **`KeyProvider`** — a structural protocol (one method, `load_keys`) for
  deployments whose encryption keys live in a secret store rather than the
  environment. `install_key_provider(provider)` loads it ONCE and holds the
  result; `refresh_keys()` re-reads it; `clear_key_provider()` falls back to
  the environment.

  Reading KEYS from a store is safe where reading VALUES from one is not:
  settings resolution is a per-request read path, so putting a store on it
  turns that store's outage into a total outage — but a key fetched at startup
  is already in the process, and the same outage an hour later is invisible.
  Rotation is therefore explicit rather than a TTL, and a provider that fails
  raises at install rather than letting a process start with no keys and
  silently degrade every secret to its spec default. There is no
  degraded-start option, deliberately.

  The kernel ships no provider and no secret-store client; the implementation
  and its dependency stay in the product.

### Fixed
- **Key material no longer appears in `EncryptionKey` / `Keyring` reprs.** The
  default dataclass repr printed `material=<the key>`, and a repr is reached
  from places nobody audits — an exception traceback, a debug log, a failed
  assertion. Key ids and statuses are still shown, so a keyring stays
  debuggable. A `KeyProvider` failure likewise reports only the exception TYPE,
  because a store client's error can quote the payload it choked on.

### Unchanged
- With no provider installed, keys come from the environment exactly as before,
  re-read on each use so a rotated variable still takes effect.

## 0.1.0a19 — 2026-08-08

Settings change actor, write-time spec enforcement, and environment variables as
a bootstrap rather than a resolution-time fallback. Migration `0017`.

### Changed — BREAKING (behaviour)
- **`SettingSpec.env_var` no longer participates in resolution.** It is read
  once at startup by the new `seed_settings_from_env`, which creates the
  platform row when none exists and never overwrites one that does. Nothing
  consults the environment at read time, and `SettingSource` no longer has an
  `"env"` member.

  A live env fallback makes the resolved value depend on which process asked,
  leaves it with no history and no owner, and makes a database restore fail to
  reproduce it. `create_app` calls the seed in its lifespan just before
  `validate_required_settings`, so a required setting configured by environment
  still counts as configured — but a deployment that relied on an environment
  variable silently overriding a *missing* row now gets a real, visible,
  editable row instead.
- **A write to a spec'd key is validated against its spec** and rejected with
  `BadRequestError` if the spec would reject it. A value that only the old,
  unvalidated write path could store now fails at the write. Values are also
  canonicalised on the way in (a form's `"30"` stores as `30`).
- **`register_specs` raises on a duplicate key** (`DuplicateSettingSpecError`)
  when two modules declare the same `(domain, key)` with different definitions,
  and on a spec whose own `default` its own constraints reject
  (`InvalidSpecDefaultError`). Both previously registered silently.

### Added
- **`SettingChangeContext`** — a frozen actor record (`actor_party_id`,
  `actor_kind`, `reason`) accepted by the write functions and stored on the
  history row. Migration `0017` adds the three columns. A history row that
  records what changed but not who changed it cannot answer the question it
  exists to answer, and requiring a join to audit to name the actor costs an
  adopting product a capability it already has.
- **`seed_settings_from_env(db)`** — the bootstrap described above. Idempotent
  and one-way; an empty variable is treated as unset.
- **`clear_by_key`** — removes a row so resolution falls through to the next
  scope. `upsert_by_key(None)` stored a null; it did not un-set.

### Changed
- `resolve_many` consults the read cache, so a screen resolving many keys no
  longer issues one query per key.

## 0.1.0a18 — 2026-08-08

Per-scope requirements, per-tenant encryption keys, and history retention. No
migration.

### Changed — BREAKING (API)
- **`SettingSpec.required` becomes `required_at`**, naming the SCOPE KIND at
  which a setting must be configured. A bool could only ever express the
  deployment case, so "every tenant must set a billing contact" had no way to be
  stated. `required_at="platform"` is the old behaviour.
- **`Keyring.active` is a method taking an optional tenant**, not a property.

### Added
- **`missing_required_settings(db, scope=...)`** — what is unconfigured AT ONE
  SCOPE. Startup still checks only the deployment's own prerequisites: a tenant
  that does not exist yet cannot be missing anything, and enumerating every
  tenant at boot would make startup cost grow with the customer count. Callers
  ask for a scope when it matters — provisioning a tenant, opening a site.
- **Per-tenant encryption keys (BYOK)** — a keyring entry may name a
  `tenant_id`. That tenant's writes use its own key; everything else uses the
  deployment key, so the common case is unchanged. Possible without a format
  change because `enc:<key_id>:<token>` already names the key that wrote a
  value — the same property that made rotation possible. At most one active key
  PER OWNER. `reencrypt_secrets` rewrites each row onto ITS owner's key.
  Decryption refuses a ciphertext naming another tenant's key: RLS should make
  that unreachable, which is why it is worth asserting.
- **`prune_setting_history`** — `DomainSettingHistory` had no retention and grew
  for the life of the deployment. Append-only is about who may rewrite history,
  not about keeping it forever. A function a caller schedules, not something the
  write path does: pruning inside a write would make an ordinary change
  occasionally do unbounded work.

## 0.1.0a17 — 2026-08-08

Bulk settings reads, and a setting change that announces itself. No migration.

### Added
- **`resolve_many`** — resolves many keys of one domain at one scope in ONE
  QUERY PER LEVEL of the chain, rather than up to one per key per level. A
  settings screen goes from ~40 queries to 2. `keys=None` means the whole
  domain, which is what such a screen actually wants. Precedence, coercion and
  the degrade-to-default rule are shared with the single-key path (`_finish`)
  deliberately: two implementations would drift, and a page reading in bulk
  would quietly disagree with the same settings read one at a time.
- **`settings.changed` outbox events** — a setting change was invisible to
  anything holding derived state, and the kernel already had an outbox it was
  not using. Tenant-scoped writes use `enqueue_event`, platform writes
  `enqueue_platform_event`. **The value is never in the payload**: a subscriber
  resolves it, so there is one reader of the value and no secret travels through
  a delivery pipeline with its own retention. A failed enqueue is logged and
  swallowed — a notification that cannot be sent must not roll back the change
  it describes. OFF unless `SETTINGS_CHANGE_EVENTS` is set, because an event
  with no relay running is a row that accumulates forever.

## 0.1.0a16 — 2026-08-08

Settings hierarchies gain arbitrary depth, and `tenant_id` stops carrying two
meanings. Migration `0016`. **No RLS policy is touched.**

### Changed — BREAKING (database, API)
- **`tenant_id` now carries isolation and only isolation.** It used to mean both
  "which tenant owns this row" (what RLS keys on) and "how specific this value
  is" (what resolution walks), which capped the hierarchy at platform-or-tenant
  because there was nowhere to put a third level.
- **`scope_kind` (NOT NULL) and `scope_id` carry precedence**, always within the
  tenant above. `NULL` never means "some level": meaning-by-absence is the
  convention that let `dotmac_erp` hold duplicate global settings.
- **The two partial unique indexes become ONE** over `COALESCE`d columns.
  Postgres treats every NULL as distinct inside a unique index, so a nullable
  column in one admits duplicates; coalescing removes the NULL and closes the
  bug class.
- `SettingSource` widens from a closed `Literal` to `str` — it now reports the
  scope kind that won. The kernel's own two still render as `"tenant"` and
  `"platform"`, so nothing visible changes today.
- Cache, resolver and writer signatures accept `scope=`; `tenant_id=` remains
  the shorthand for the common case. Passing both raises.

### Added
- **`dotmac_kernel.setting_scopes`** — `SettingScope` (which refuses to
  construct a non-platform scope without a tenant, so isolation stays a stored
  fact), `ScopeKindSpec`/`ScopeKindRegistry` (the seventh declaration registry,
  ranked), and `resolution_chain`. A product declares `site` and `user` with
  ranks and the resolver walks user → site → tenant → platform → env → default
  with no edit to the resolver.

## 0.1.0a15 — 2026-08-08

Setting value types become open and registered, and each type owns its own
encoding. Migration `0015`.

### Changed — BREAKING (database)
- **`SettingValueType` is an open registered string, not a four-member enum**;
  `ck_domain_settings_value_type` is dropped. The same closed-list defect `0014`
  removed from `domain`, one column across.
- **The value-alignment CHECK no longer names types.** It permitted `value_json`
  only when `value_type = 'json'`, so a new JSON-stored type could not be
  written at all. It now states the invariant that actually holds: exactly one
  value column is populated.
- **`value_json` stores SQL NULL, not the JSON text `null`.** SQLAlchemy's JSON
  type serialises Python `None` as JSON null unless `none_as_null=True`, so "no
  JSON value" was indistinguishable from "a JSON null value" and every
  `value_json IS NULL` predicate silently never matched. Migration `0015`
  backfills rows already written that way.

### Added
- **`dotmac_kernel.setting_value_types`** — `ValueTypeSpec` owns BOTH directions
  of a type's encoding (`from_storage`/`to_storage`) as a matched pair, replacing
  three separate if-ladders in the resolver that each knew part of what a type
  meant. Adding a type is one declaration; a single round-trip test covers every
  type at once. The sixth declaration registry (ADR-0008), declared on manifests
  as `setting_value_types`.
- **`money` as a first-class value type**, stored as
  `{"amount": "<decimal string>", "currency": "<ISO-4217>"}` over
  `dotmac_kernel.money`. The amount is a STRING because JSON numbers are IEEE
  doubles in most parsers, and exactness is the point; a bare number is rejected
  because it cannot name its currency. This is ADR-0003's exact-Money rule
  finally expressible as a setting.
- Read stays tolerant and write strict: `from_storage` returns `None` for
  anything unreadable (the resolver degrades to the spec default), while
  `to_storage` raises so a caller can still be told it is wrong.

## 0.1.0a14 — 2026-08-08

Settings subsystem re-based on the products' proven implementation: open setting
domains, richer specs, at-rest encryption with a rotatable keyring, change
history, and a scope-safe read cache. Migration `0014`.

### Changed — BREAKING (database)
- **`SettingDomain` is an open registered string, not a five-member enum**, and
  `domain_settings.domain` is a plain `String(120)` — migration `0014` drops the
  `ck_domain_settings_domain` CHECK. A kernel that enumerates its consumers'
  setting domains needs a migration every time a product invents one: this repo
  declares five, `dotmac_erp` runs twenty-one. Kernel-owned domains stay bound
  as class attributes (`SettingDomain.branding`), so existing call sites are
  unchanged; a product constructs its own (`SettingDomain("payroll")`).
  Downgrade is lossy — rows outside the original five cannot satisfy a restored
  constraint.

### Added
- **`dotmac_kernel.settings_cache`** — a read cache for resolved settings whose
  keys carry their scope by construction (`dotmac_kernel.cache.cache_key`, whose
  `scope` is keyword-only with no default). Invalidation happens at the write,
  not on a TTL: a tenant write drops that tenant's entry, a platform write drops
  EVERY scope's entry for that setting, because a tenant without a row of its
  own inherits the platform value. Secrets are never cached — encrypting at rest
  and then putting the plaintext in a shared store gives most of that back.
  **OFF unless a store is installed**, and `create_app` installs none: a delete
  only reaches the process that performs it, so a per-process cache under
  multiple workers would make a setting change appear not to take effect on some
  requests. A single-process deployment installs `MemoryCache()`; a multi-worker
  one installs a shared store.
- **`DomainSettingHistory`** (migration `0014`) — one row per value transition,
  answering "what was this before" which `AuditEvent` cannot: the audit trail
  records that a change happened, not what the value was. The two are split
  deliberately — this table does NOT record the actor, because
  `write_audit_event` is the one writer of who-did-what and duplicating it here
  would create a second authority that drifts. **A secret's value is never
  recorded**: `value_before`/`value_after` stay NULL and `secret_changed` marks
  the transition, so rotating a compromised credential does not leave it
  readable in the table that explains the rotation. Append-only, enforced twice
  (no UPDATE/DELETE policy and no UPDATE/DELETE grant); tenancy and the RLS
  split mirror `domain_settings`, with the isolation canary the
  nullable-tenant exception requires.
- **`dotmac_kernel.settings_crypto`** — at-rest Fernet encryption of any setting
  whose spec sets `is_secret`. `is_secret` previously only masked the value in
  the admin API, which protects the screen and nothing else: a dump, a replica
  or a backup still carried the plaintext. Applied at the three call sites in
  `settings_resolver` (one reader, two writers). **Fail closed on write, tolerant
  on read** — writing a secret with no usable key raises
  `SettingsEncryptionError` rather than storing plaintext, while reads pass
  legacy plaintext through and degrade an undecryptable value to the spec
  default. Key from `SETTINGS_ENCRYPTION_KEY` or a file named by
  `SETTINGS_ENCRYPTION_KEY_FILE`, or a rotatable keyring in
  `SETTINGS_ENCRYPTION_KEYS`; the kernel never fetches a secret over the
  network, because settings resolution is a per-request read path, and every
  secret manager can render to an env var or a file. The stored form is
  `enc:<key_id>:<token>` — **the key id is in the ciphertext because a Fernet
  token does not carry one**, and without it rotation would silently substitute
  spec defaults for credentials the new key cannot read. Keyring statuses match
  `licensing`'s (`active` encrypts and decrypts, exactly one; `retired`
  decrypts only; `revoked` decrypts nothing), and `reencrypt_secrets` is the
  idempotent, resumable second half of a rotation. `enc:` prefix
  and scheme match `dotmac_erp`'s. Needs the new `settings-crypto` extra —
  `cryptography` stays optional and is imported lazily, as `licensing` does.
- **`SettingSpec.env_var`, `.required`, `.description`**, each with a real
  consumer. `env_var` is read in the ONE resolver, BELOW both rows and ABOVE the
  spec default: an env var is deployment-scoped so it must not beat a stored
  row, but it is a real operator decision so it must beat a shipped default —
  new `SettingSource` value `"env"`. `required` is checked at startup by
  `validate_required_settings`, which `create_app` runs after seeds, fatal in
  production and a warning elsewhere. `description` renders on the settings list
  and editor.
- **`dotmac_kernel.setting_domains.SettingDomainRegistry`** — the fifth manifest
  declaration registry, alongside permissions, capabilities, audit actions and
  feature flags. A module declares `setting_domains` on its manifest;
  `create_app` installs the registry from the INSTALLED module set; the settings
  write path rejects an undeclared domain (`UndeclaredSettingDomainError`), and
  the settings admin API 404s an unknown domain in a URL. Not-installed raises
  `SettingDomainsNotInstalledError`, distinct from installed-and-empty.
- `setting_domains` on `FeatureManifest` and `ModuleManifest`, carried through by
  `ModuleManifest.from_feature`.
- **ADR-0008** makes the registry shape the standard: a kernel-level vocabulary
  whose members belong to modules is declared on manifests and validated by a
  registry — never a kernel enum, a fixed list, or a CHECK constraint.

## 0.1.0a13 — 2026-08-07

Seventeenth alpha. **Module control-plane directive step 6: the platform
administration surface** — the operable half of steps 4 and 5. No migration.

### Added
- **`/platform/*` HTML surface** (`dotmac_kernel.platform_web`): module
  inventory, deployment-scope feature-flag overrides, and per-tenant
  entitlements. It lives in the kernel because everything it administers is
  kernel-owned — the module registry, the flag catalogue and its overrides, the
  capability catalogue and the grant store. The tenant portal is the opposite
  case (its screens are the assembly's features), which is why that one composes
  from feature `web_routers` and this one does not.
- **`require_platform_web_auth`** — the platform plane's cookie guard, reading
  its own cookie and handing the token to `authenticate_platform_request`, the
  SAME seam the bearer guard uses. Any tightening of platform token validation
  lands once and both surfaces get it.
- `WebAuthRedirect` gained `login_path`, so one redirect concept serves two
  front doors rather than a second exception and handler drifting apart.

### Notes
- The two planes never share a guard, a cookie, or a layout, and the surface
  404s off the platform host — it does not appear to exist on a tenant's domain.
- The entitlement screens are PER-TENANT rather than one fleet-wide matrix, and
  that is forced by the data model rather than chosen for looks:
  `tenant_entitlement_grants` carries only a `tenant_id = app_current_tenant_id()`
  policy, and `platform_api` never sets a tenant context — so it reads nothing
  without one. The screens use the `provision_tenant` idiom (set the context for
  the transaction), which makes per-tenant the only coherent shape.
- Module enable/disable is deliberately absent. A module's tables and migrations
  are part of the image; ADR-0003 restricts the admin UI to enabling
  already-installed, migrated, dependency-complete code, and a toggle here would
  imply data can be switched off.
- Routes read `await request.form()` rather than declaring `Form(...)` params:
  FastAPI's `Form()` requires `python-multipart` at route-definition time, and
  the kernel deliberately does not depend on it. Declaring one would break
  `import dotmac_kernel` for every clean consumer.

Sixteenth alpha. **Module control-plane directive step 5: typed feature flags**,
plus the cache-key convention they are the first consumer of. Migration `0013`
(kernel head advances from `0012`).

### Added
- **`dotmac_kernel.cache`** — the ONE place a cache key is built. Scope is a
  TYPE (`TenantScope` / `PlatformScope`), not a `tenant_id: UUID | None`
  parameter: with a nullable parameter, "I forgot the tenant" and "this is
  deliberately deployment-wide" produce the SAME key, and the platform entry
  silently becomes the bucket every unscoped read lands in. Omitting `scope=` is
  a `TypeError`; `t=<uuid>` and the literal `platform` are structurally
  different, so no tenant identifier can occupy the platform entry. `version=`
  retires a whole generation of entries without a delete sweep.

  Landed before the first tenant-keyed cache deliberately — the entitlement
  guard added in `0.1.0a13` was the first request-time consumer of tenant-scoped
  state, which closed the window in which this could be added for free.
- **`dotmac_kernel.flags`** — `FeatureFlagSpec` (code, value_type, default,
  owner, description, allowed_scopes, expires_on, operational) and `evaluate`,
  which returns a `FlagEvaluation`, never a bare bool: "it was on" is useless in
  an incident, "it was on because tenant override <rule> set it against a
  default of off" is not.

  Precedence, highest first: **kill switch** (outranks everything, including a
  rollout — the person turning a feature off at 3am must not have to unwind
  every override first, and it forces OFF rather than back-to-default, or a
  default of True would make it a no-op), tenant override, tenant rollout,
  platform override, platform rollout, declared default.

  Rollouts hash `(flag code, subject)`: deterministic, so a tenant does not flip
  between requests, and salted by code so two flags at 50% do not select the
  same half of the fleet.
- **`feature_flag_overrides`** (migration `0013`) — deployment- and tenant-scope
  overrides. Nullable `tenant_id` following `domain_settings`, the documented
  exception to hard rule 11, with the same asymmetric RLS: read own-or-platform,
  write own-only, plus a `platform_api`-only policy for the NULL-tenant rows.
  Two PARTIAL unique indexes, because Postgres treats NULL as distinct from
  every other NULL.
- **`resolve_flag(db, code, tenant_id=...)`** — the one entry point a service
  calls. It loads the overrides in scope, derives the invalidation version from
  them, and evaluates through the scoped cache, so a caller cannot evaluate
  against another tenant's overrides or skip the version.
- **`ModuleManifest.feature_flags` / `FeatureManifest.feature_flags`** — flags
  are declared by their owning module, like permissions and capabilities.

### Governance
Flag codes and permission/capability codes are DISJOINT namespaces — the
executable form of "flags cannot grant permissions". Every declared flag has a
real consumer, every flag has an owner, and an expired flag fails the BUILD
rather than production: an expiry must never take a feature down for users, it
must force a decision in CI.

Fifteenth alpha. Closes **module control-plane directive step 4**: tenant
entitlements become an ENFORCED request-time decision instead of a store with no
consumer. No migration in the kernel; the reference assembly ships `a004` to
backfill grants for the capabilities it begins gating.

### Added
- **`dotmac_kernel.deps.require_capability(code)`** — the tenant-entitlement
  guard, and the counterpart to `require_permission`. A permission asks "may
  this ACTOR do it?"; a capability asks "does this TENANT have the feature at
  all?". Both compose on a route and neither substitutes for the other.

  The decision is local and explainable — a pure read of the grant store. A
  request-time check never calls a payment provider and never validates a
  licence over the network (ADR-0003), which is why the signed-licence receiver
  PROJECTS into grants rather than being consulted per request. Denials carry
  the stable reason code (`not_granted` / `revoked`) so an operator can tell
  "never had it" from "had it and lost it" without reading the database. The
  admitting decision is RETURNED, so a route needing the grant's `limits` reads
  them from the same decision that let it in.
- **`install_capabilities` / `active_capabilities`** — the process-active
  capability catalogue, the same shape as the permission and audit-action
  seams, installed by `create_app` from the INSTALLED module set. Empty means
  deny: an uninstalled catalogue must not silently entitle every tenant.
- **Boot-time validation of referenced capability codes.** A mounted route
  referencing an undeclared code fails the BOOT. Left to request time it would
  deny every tenant forever on a route that looks correctly wired — which reads
  as an operations problem in the grant table rather than a typo in a
  declaration.
- **`CapabilitySpec`** — a capability is now a typed declaration
  (`code`, `description`, `default_granted`) rather than a bare string.
  `default_granted` answers the question enforcement forces: what does a newly
  provisioned tenant get? It is per-capability and per-product — a self-hosted
  deployment bundles a feature and expects it to work on day one, a SaaS
  deployment sells the same one default-off — and it is a DECLARATION the
  provisioning service applies, never a plan-name or payment branch in a route.

### Changed
- **`ModuleManifest.capabilities` / `FeatureManifest.capabilities` accept
  `str | CapabilitySpec`.** A bare string still works and means
  `default_granted=True` — exactly what those declarations meant before
  enforcement existed — so no existing manifest changes behaviour. Consumers
  that iterate the field now receive whatever was declared; use
  `CapabilitySpec.coerce` (or the catalogue) to normalise.
- `create_app`'s route walker is one function over both declaration kinds
  rather than a permission-specific copy.

Fourteenth alpha. Opens the two seams the FIRST STATEFUL MODULE needs, and makes
its namespace allocation (ADR-0006 D1 / M1). Additive only; no migration, kernel
head stays `0012`.

### Added
- **`ProductAssemblySpec.packaged_template_dirs`** + **`dotmac_kernel.templating
  .compose_templates`** — template directories belonging to installed packages
  (an installable module's admin screens, a packaged theme), layered UNDER the
  assembly's own directory and OVER the kernel's, in declaration order. This is
  what lets a stateful module ship a `/admin/...` surface at all: a module is a
  pip-installed package, so its Jinja files are package data outside any
  assembly's template root, and the single ChoiceLoader could previously hold
  exactly one assembly directory.

  `compose_templates` is now the ONE loader authority and `create_app` calls it
  unconditionally; `use_assembly_templates` is retained as the published
  single-layer spelling and delegates to it. Two independent setters would each
  have had to guess what the other installed, and the last caller would silently
  drop the other's layer. A consequence worth knowing: an empty composition
  RESETS to kernel-only, so a second `create_app` in one process no longer
  inherits a previous spec's override.
- **`mod_tstudio` ledger allocation** — `TEMPLATE_STUDIO_MIGRATION_OWNER` in
  `dotmac_kernel.namespaces`, the first installable module in
  `MIGRATION_OWNER_LEDGER` (owner `template_studio`, prefix `ts`, schema
  `mod_tstudio`). Per D1's own rule, the row lands in the same change as the
  module's manifest.

### Changed
- **`dotmac_kernel.testing.create_test_engine` attaches module schemas.** A
  stateful module's models are bound to `mod_<short_code>`, so the ORM emits
  fully qualified `mod_x.thing` — which plain SQLite rejects. Each distinct
  schema in `Base.metadata` is now ATTACHed as its own in-memory database before
  `create_all`. Deliberately ATTACH and not a `schema_translate_map`: translating
  the schema away would make the unit lane exercise unqualified SQL no deployment
  runs, hiding precisely the qualification defects D1's gate exists to catch.

Thirteenth alpha. Adds the **presentation-package composition slots** an assembly
needs to adopt a shared design system (ADR-0006 U1). Additive only; no migration,
kernel head stays `0012`.

### Added
- **`ProductAssemblySpec.packaged_static_dirs`** — static directories belonging
  to installed presentation packages (a `dotmac-ui` release, a `dotmac-theme-*`),
  layered UNDER the assembly's own `assembly_static_dir` and OVER the kernel's,
  in declaration order. Kept separate from `assembly_static_dir` because they are
  different authorities: that one is the product's own source, these are
  versioned package data the product composes and must not edit. First match
  still wins, so a product can shadow one file from a shipped design system
  without vendoring the rest of it.
- **`ProductAssemblySpec.stylesheets`** + **`dotmac_kernel.templating
  .install_stylesheets`** — extra stylesheet URLs rendered into every page's
  `<head>` after the kernel's own, exposed to templates as the process-static
  `extra_stylesheets` Jinja global (default `()`, so a render that never called
  the installer degrades rather than raising). `create_app` installs them, and
  installs `()` when `web_enabled` is False: an API-only deployment has no
  `<head>` to advertise a stylesheet for.

**The kernel deliberately does not know what those URLs and directories are
for.** ADR-0006 § 2 fixes the dependency direction as `assembly → module →
dotmac-ui → dotmac-kernel`; a kernel that reached forward into the presentation
system would make the UI package un-releasable independently. So these are
anonymous slots an assembly fills — the kernel never imports, names, or resolves
a presentation package, and a new import-linter contract ("Kernel must not import
the UI package") holds that. URLs rather than paths for `stylesheets` because the
assembly, not the kernel, owns the mapping from a package's static directory to a
URL.

## 0.1.0a12 — 2026-08-06






Twelfth alpha. Adds **per-module Postgres schema namespaces and registered
Alembic migration prefixes** — D1 of the white-label foundation programme
(ADR-0006 § "Decision amendment — 2026-08-02"), the last blocker for stateful
module composition. No migration; the kernel head stays `0012` and every
existing revision id is unchanged.

### Added
- **`dotmac_kernel.namespaces`** — the D1 authority. One immutable
  `mod_<short_code>` Postgres schema per STATEFUL module, plus an immutable,
  globally unique short migration prefix and branch label per migration owner.
  `NamespaceRegistry` is construction-is-validation, like `PermissionCatalogue`
  before it: it refuses a composition with a duplicate schema claim
  (`DuplicateSchemaError`), duplicate migration prefix
  (`DuplicateMigrationPrefixError`), duplicate branch label
  (`DuplicateBranchLabelError`) or contested table (`DuplicateTableOwnerError`).

  This answers verified evidence, not a hypothetical:
  `docs/inventories/migration-collisions.md` found `starter ∩ ERP` colliding on
  `audit_events`, `domain_settings`, `people`, `person_roles`, `roles`,
  `user_credentials` and `starter ∩ Sub` on `parties`, `party_roles` — sixteen
  of seventeen duplicate names same-name/**different-shape**, the failure mode
  that corrupts quietly rather than erroring loudly.

- **`MIGRATION_OWNER_LEDGER`** — the checked-in, kernel-shipped allocation
  record, and the reason "globally unique" can be true across Dotmac repos: the
  kernel is the shared dependency, so allocations are registered once. A
  stateful module absent from it (`UnallocatedNamespaceError`) or contradicting
  it (`NamespaceAllocationError`) cannot be registered. Ships with the two host
  owners only — `kernel` and `assembly`, both writing to `public`.

- **`ModuleManifest` D1 declaration fields** — `short_code`,
  `migration_prefix`, `migration_branch`, `tables`. `db_schema` is a *derived,
  read-only* property built only through `module_schema()`, so a namespace can
  never be inferred from a display name and there is no settable attribute to
  re-point at runtime. A manifest is either fully stateful or fully stateless;
  a half-declaration (tables with no schema) is rejected, because its tables
  would land in `public`.

- **`ModuleRegistry` now assigns namespaces.** It builds the `NamespaceRegistry`
  during construction and exposes it via `namespaces()`; `inventory_payload()`
  gained a `migration_owners` block and each inventory row a `db_schema` /
  `migration_branch`, so any `alembic_version` row is explainable from the
  inventory alone.

- **`dotmac_kernel.migrations.gate`** — the composed CI gate. Statically (AST,
  no imports, no database) loads every selected version location and rejects
  duplicate revisions, unregistered/duplicate prefixes, duplicate branch
  labels, duplicate schema claims and duplicate table ownership; it also
  enforces one lineage root per owner, `down_revision` never crossing owners
  (cross-lineage ordering is `depends_on`), revision ids inside
  `alembic_version.version_num`'s **VARCHAR(32)**, and module DDL that names
  its schema instead of relying on `search_path`. Locations are attributed to
  owners through their lineage root's branch label, so there is no second
  location→owner map to drift from `alembic.ini`.

- **`dotmac_kernel.migrations.catalog`** — the post-migration live-catalog
  contract, applying the kernel RLS/grant rules across every registered module
  schema: RLS ENABLEd + FORCEd, a policy present, `tenant_id NOT NULL` (or the
  EXISTS-join subtype pattern), unique constraints that include `tenant_id`,
  composite tenant FKs, manifest-vs-live table ownership, and no module table
  squatting in `public`. Split into parameterised SQL builders plus a pure
  `audit_snapshot` decision function, so the contract is fully unit-testable
  without Postgres.

- **`revision_id(prefix, sequence, slug)`**, `qualified()`,
  `schema_table_args()` — the helpers that make the `<prefix>_<sequence>_<slug>`
  format and full schema qualification the path of least resistance.
  `revision_id` raises rather than truncating past 32 characters, a limit that
  otherwise surfaces only mid-deploy against a real database.

### Enforcement hardening
- The allocation ledger is validated as a whole even when an allocated module
  is not installed, so fleet-wide schema/prefix/branch uniqueness cannot hide
  a collision in two dormant rows.
- The static migration scanner follows local `upgrade()` helpers, understands
  typed Alembic metadata and `module_schema()` constants, checks imperative and
  inline foreign keys, treats an empty `tables` declaration as owning nothing,
  and rejects a qualified DDL write aimed at another module's schema.
- The host `public`-schema audit now consumes the kernel catalog's canonical
  UNIQUE-constraint query and enforces the composite-unique rule it already
  claimed under hard rule 11; its sensitivity canary covers the failure path.

### Compatibility
- **`public` is a compatibility namespace, not a shared one.** It remains the
  kernel's and the one host assembly's, and is explicitly unavailable to
  installable modules (`HostSchemaClaimError`).
- **The `kernel` and `assembly` lineages are grandfathered.** Their revision
  ids (`0001_initial_tenant_schema`, `a001_adopt_cfd`, …) are already recorded
  in live `alembic_version` rows, so they keep their original format via
  `MigrationOwner.legacy_revision_pattern` and are exempt from the strict
  `<prefix>_<sequence>_<slug>` and `schema=` rules. Every installable module
  gets the strict rules.
- Purely additive: no existing public name changed, and every existing manifest
  (including a plain `FeatureManifest`) is stateless under D1 and validates
  unchanged.

## 0.1.0a11 — 2026-08-06






> **Amended 2026-08-03.** `active_audit_actions()` no longer defaults to an
> empty registry. NOT INSTALLED and INSTALLED-AND-EMPTY are now different
> states: the former raises `AuditActionsNotInstalledError` (a configuration
> error), the latter still rejects each write as undeclared. The original
> default told a process that builds no app — a worker, a Celery task, a CLI, a
> migration helper — that its perfectly well-declared action "is not declared by
> any installed module", pointing the reader at the manifests when the actual
> fault was that the vocabulary was never loaded. `dotmac_kernel.permissions`
> deliberately keeps its empty default: an uninstalled permission catalogue
> DENIES, which is the safe answer for an authorization check, whereas an
> uninstalled audit registry would reject every write inside the caller's
> transaction and turn a wiring mistake into a failed business operation.

Eleventh alpha. Adds the **manifest permission and audit-action declarations**
together with the catalogues that consume them — step 3 of the module
control-plane program
(`docs/superpowers/reviews/2026-07-18-module-control-plane-directive.md`; F2 of
the white-label foundation programme, ADR-0006). No migration; the kernel head
stays `0012`.

### Added
- **`dotmac_kernel.permissions`** — `PermissionSpec` (a permission a module
  declares it OWNS: `code`, `description`, and `default_roles`, the role slugs
  whose holders satisfy it) and `PermissionCatalogue`, the one authority on "is
  this permission real, and who may hold it?". Sibling of `CapabilityCatalogue`
  by design — same shape, same fail-closed posture, same invariant: a code has
  exactly one owning module (`DuplicatePermissionError`) and may never be
  invented at a reference site (`UndeclaredPermissionError`).

  The two catalogues gate different things and must not be conflated:
  capability answers "is this TENANT entitled?", permission answers "does this
  ACTOR hold it?". `default_roles` is the code-declared default binding — the
  same relationship `SettingSpec.default` has to a `domain_settings` row, not a
  second authority; tenant-configurable role→permission grants are a later step
  that layers over it.

- **`dotmac_kernel.deps.require_permission(code)`** — the guard that consumes the
  catalogue, and the reason the field is not inert. It resolves the declared spec
  at request time and requires the actor to hold one of its `default_roles` (403
  otherwise): a strict generalisation of `require_role`, which remains supported
  as the raw role check underneath. Both now share one `_holds_any_role` query,
  so a fix to the tenant scoping or the join lands once for both.

- **`create_app` fails the BOOT on an undeclared permission reference.** The
  dependency `require_permission` returns carries its code; after mounting,
  `create_app` walks every mounted route and raises `UndeclaredPermissionError`
  naming the route and the code. A typo therefore stops the boot instead of
  surfacing as a mystery 403 on the first request that reaches that route. Scoped
  to MOUNTED routes: importing a module's routers without mounting them cannot
  fail an assembly for a code it never exposes.

- **`dotmac_kernel.audit_actions`** — `AuditActionRegistry`, the same catalogue
  shape for the audit trail's vocabulary (`DuplicateAuditActionError`,
  `UndeclaredAuditActionError`). An action is a bare code, not a spec: unlike a
  permission it carries no binding, because the trail records and decides nothing.

- **`write_audit_event` validates its `action`** against the active registry
  BEFORE adding anything to the session, so a rejected write leaves no partial
  state. `audit_events.action` was free text, so any typo (`"role.grant"` vs
  `"role.granted"`) silently produced a second, near-identical action nobody would
  ever query for — and an audit trail missing events you believe you are reading
  is worse than one that is obviously empty. `write_platform_audit_event` is
  deliberately NOT validated the same way: platform actions are written by the
  kernel's own control plane, which has no module manifest to declare them on.

- **`install_permissions` / `install_audit_actions`** (+ `active_permissions` /
  `active_audit_actions`) — the process-active install, the same pattern
  `install_surface_globals` already uses, because a request-time guard and a
  service-time writer cannot be handed a catalogue as an argument without
  threading it through every call site. `create_app` installs both from the
  INSTALLED module set (not the enabled subset: disabling a module must not turn
  a real code into an undeclared one). Permissions default to an EMPTY catalogue
  so an uninstalled authorization catalogue denies safely. Audit actions require
  an explicit installation; a missing installer raises
  `AuditActionsNotInstalledError`, while an installed-empty registry rejects
  every action as undeclared.

### Changed
- **`FeatureManifest` and `ModuleManifest` gained `permissions` and
  `audit_actions`.** Both default to `()`, and `ModuleManifest.from_feature`
  carries them across unchanged — a feature manifest declares them exactly like a
  module manifest, so a package does not have to migrate in order to declare.

### Compatibility
- **No breaking change to a public signature.** Every existing name keeps
  working; `require_role` is untouched and remains supported.
- **One behavior change to be aware of when adopting:** `write_audit_event` now
  REJECTS an action no installed module declares. An assembly upgrading to this
  version must add each action it writes to the writing module's manifest
  (`audit_actions=(...)`), and a consumer that builds an app WITHOUT `create_app`
  must call `install_audit_actions` itself — a missing installer is a distinct
  configuration error and an explicitly empty registry rejects everything.
- The directive's remaining manifest fields (`settings`, `feature_flags`,
  `entity_types`, `health_checks`) are still deliberately NOT added. Same reason
  as in `0.1.0a10`: each lands with the registry code that derives behavior from
  it.

## 0.1.0a10 — 2026-08-06






Tenth alpha. Adds the **module manifest and registry** — step 2 of the module
control-plane program (`docs/superpowers/reviews/2026-07-18-module-control-plane-directive.md`,
authorized by ADR-0003 and constrained by ADR-0006). No migration; the kernel
head stays `0012`.

### Added
- **`dotmac_kernel.modules`** — `ModuleManifest`, the versioned expansion of
  `FeatureManifest` (`code`, `version`, `contract_version`, `dependencies` on
  top of the existing router/nav/capability/seed surface), and `ModuleRegistry`,
  the one authority on whether an installed module set is coherent.

  Construction IS validation, fail-closed on four independent checks, each with
  its own named error under a shared `ModuleRegistryError` base: a duplicate
  `code` (`DuplicateModuleError`), a `contract_version` outside
  `SUPPORTED_MODULE_CONTRACT_VERSIONS` (`ModuleContractVersionError`), a
  dependency on a code that is not installed (`MissingModuleDependencyError`),
  and a dependency cycle (`ModuleDependencyCycleError`, whose message names the
  actual path rather than merely asserting one exists).

  `startup_order()` is a pure function of (declaration order, dependency edges):
  dependencies first, **declaration order as the tiebreak**. Declaration order,
  not alphabetical, is load-bearing — an assembly's module list is a deliberate
  mount order and route matching is first-match-wins, so adopting the registry
  must not silently reorder an assembly whose modules declare no dependencies.
  For every `FeatureManifest` shipping today that means the order is provably
  identical to before.

  `enabled_codes`/`enabled_order` make "deployment-enabled" one definition
  instead of three, and `enabled_order` fails closed when an enabled module
  depends on one that is NOT enabled: installed is not sufficient, and disabling
  a module something else needs is a misconfiguration that belongs at startup,
  not in a mystery 500.

  `inventory()` / `inventory_payload()` expose the installed-module/version
  inventory (`ModuleInventoryEntry`: code, version, contract version,
  dependencies, core, enabled) for health and diagnostics — sorted by code so
  two deployments' inventories are diffable.

- **`create_app` validates modules before mounting anything.** `spec.modules` is
  built into a `ModuleRegistry` first, so an incoherent set stops the boot
  rather than producing a half-mounted app, and surface globals, mounting, and
  seeds all walk that single deterministic order. The validated registry and its
  inventory are published on `app.state.module_registry` /
  `app.state.module_inventory`.

  Public `/health` is deliberately unchanged: it stays DB-free liveness and
  discloses nothing about what is installed. The kernel ships the inventory
  CONTRACT; the authenticated platform diagnostics surface is the control
  plane's own program step and composes `inventory_payload()`.

### Changed
- **`ProductAssemblySpec.modules` accepts `ModuleManifest` and `FeatureManifest`,
  freely mixed** (`AnyManifest`), which is what makes migrating an assembly's
  feature packages incremental. Same widening for `mount_features`,
  `install_surface_globals`, and `CapabilityCatalogue.from_manifests`.

### Compatibility
- **No breaking change.** `FeatureManifest` and every existing consumer keep
  working untouched, two ways: the registry adapts a feature automatically
  (`ModuleManifest.from_feature`, which carries every field across and invents
  neither a version nor a dependency — an unversioned module records the
  `UNVERSIONED` sentinel `"0.0.0"`), and `ModuleManifest` exposes read-only
  `name`/`routers` aliases for `code`/`api_routers` so manifest-walking code
  needs no call-site change.
- The directive's `permissions`, `settings`, `feature_flags`, `audit_actions`,
  `entity_types`, and `health_checks` manifest fields are deliberately NOT added
  yet. They belong to later program steps, and the same directive requires CI to
  fail when "a declaration has no consumer" — each lands with the registry code
  that derives behavior from it.
- The `kernel-floors` job now constructs a module graph, asserts its order,
  serializes the inventory, and proves a missing dependency fails closed at the
  floor.

## 0.1.0a9 — 2026-08-03






Ninth alpha. Adds the **applied-state envelope** — the structure a deployment
signs to prove WHO is reporting what it has applied, unblocking the WS8
production-readiness gate (ADR-0007). No migration; the kernel head stays
`0012`.

### Added
- **Applied-state envelope** (`dotmac_kernel.licensing`, ADR-0007) — the
  structure a DEPLOYMENT signs to prove who is reporting, the mirror of the
  licence envelope the vendor signs to prove what was issued. Deliberately a
  separate structure: the two travel in opposite directions under different key
  custody, so one trust structure covering both would let either party's key
  speak for the other.

  Fully typed and immutable: `AppliedStateEnvelope` (with `to_wire`/`from_wire`
  for transport), `DeploymentVerificationKey` (carrying the `deployment_ref` a
  `key_id` resolves to), `VerifiedAppliedState` (exposing the PROVEN
  `deployment_ref` plus `claim_matches_proof`), the `DeploymentSigner`
  protocol, `seal_applied_state` / `verify_applied_state`, and
  `APPLIED_STATE_ENVELOPE_SCHEMA` (`dotmac-applied-state-envelope/1`).

  The signature covers the EXACT payload bytes, carried as bytes on the
  envelope, so nothing re-serialises a payload to verify it.

  **`key_id` is signed, not merely carried.** Because `key_id` resolves to a
  deployment identity, leaving it unsigned made that identity forgeable:
  registering the SAME public key under a second `key_id` mapping to another
  deployment, then replaying a captured report with `key_id` swapped, verified
  successfully and attributed the report to the attacker's deployment. Found by
  review against the first implementation and now pinned by a canary. Note the
  weaker check that misses it — substituting a *different* key fails trivially,
  because its signature does not verify; only identical material under two ids
  exposes the hole. `applied_state_signing_input(key_id, payload)` is therefore
  canonical and length-delimited, since plain concatenation would let `("a",
  "bc")` and `("ab", "c")` share signing bytes.

  Two domains, not one: the possession challenge is signed by the same key over
  a vendor-chosen nonce, which would otherwise be a forgery oracle for reports.
- **`DeploymentPossessionChallenge`** — the typed, versioned
  (`dotmac-deployment-challenge/1`) proof-of-possession that moves a registered
  key from `pending` to `active`. Binds `challenge_id`, `key_id`,
  `deployment_ref`, a nonce with a minimum length, and a timezone-aware
  `expires_at` into its signing input, so a response is evidence for exactly one
  registration and cannot be carried to another or silently extended.
  `verify_possession` checks expiry BEFORE the signature — "expired" and "bad
  signature" send an operator to different places.
- **`DeploymentPossessionResponse` + `VerifiedDeploymentPossession`** — the
  answer is typed and versioned too (`dotmac-deployment-possession-response/1`),
  and `verify_possession` returns a value instead of `None`. A bare signature
  was cryptographically sufficient — every binding is in the challenge — but it
  carried no schema, so it could not be versioned, told apart from any other
  signature, or read by a consumer holding only the wire form; a naked
  signature between two planes is the one place Dotmac's typed-contract
  standard would have been broken.

  The response carries ONLY `challenge_id`, `key_id` and the signature, and
  `from_wire` REJECTS one that echoes the nonce, deployment or expiry — the
  issuer's stored challenge is authoritative for those, and a field accepted
  and ignored today is a field something reads tomorrow. The two identifiers
  are routing, not authority: verification requires them to match the stored
  record, and a response naming another challenge or key is a MISMATCH rather
  than a signature failure. `answer_possession_challenge` builds one on the
  receiver side (refusing to sign for a key the signer does not hold).
  `VerifiedDeploymentPossession` returns the proven `deployment_ref`, the
  `key_id` to activate and the `challenge_id` to consume — the vendor does both
  atomically; the kernel retires nothing.
- **`DeploymentSigner` owns BOTH halves of a deployment's identity** (renamed
  from `AppliedStateSigner`, which no longer described it). It carries
  `deployment_ref` alongside `key_id`, and both `seal_applied_state` and
  `answer_possession_challenge` verify both BEFORE calling `sign`.

  Checking `key_id` alone was a demonstrated defect: a challenge naming the
  signer's own key but a foreign `deployment_ref` was signed happily, minting
  a portable signed statement that this key proved possession for another
  deployment. The verifier refuses to activate on it, but ADR-0007 §1 sells
  these signatures as evidence any third party can check, so the artifact must
  never be produced. The rule the protocol now makes expressible: never sign a
  statement you cannot attest to. Refusal happens before `sign` — a signature
  discarded afterwards has still been computed — and a canary drives both
  paths with a signer that raises if invoked at all.

  The kernel owns the contract, the serialization and the conformance vectors.
  It owns NO production key custody and ships no production signer, exactly as
  it ships none for licences.
- **`FakeDeploymentSigner`** (`dotmac_kernel.testing`) — the deployment-side
  counterpart to `FakeLicenceSigner`, ephemeral in-memory Ed25519. Includes
  `sign_raw`, which exists so a canary can prove the domain and key-id bindings
  are load-bearing rather than decorative.

## 0.1.0a8 — 2026-08-02






Eighth alpha. Adds the **receiver-applied-state contract** — the cross-plane
value object a deployment uses to report what it is actually running. No
migration; the kernel head stays `0012`.

### Added
- **`ReceiverAppliedState`** (`dotmac_kernel.licensing`) + `applied_state_payload`
  / `parse_applied_state`, `APPLIED_STATE_SCHEMA`
  (`dotmac-licence-applied-state/1`) and the `UNKNOWN_DIGEST` sentinel.
  Carries the deployment ref, licence id/version/digest, **keyring
  generation**, **applied revocation-list version**, an `observed_at`
  timestamp, and a `report_id` idempotency key (delivery is at-least-once and
  identical content may legitimately repeat — a NEW `report_id` with identical
  content is a new observation, not a replay). Every field is a **claim**:
  authentication and proof happen at the vendor plane, which verifies who sent
  the report and matches the digest against what it issued; the report itself
  proves nothing. `revocation_list_version` of `None` means no list imported —
  deliberately distinct from version 0.

  Both outcomes are first-class: `status="applied"` requires a real committed
  identity (`licence_version >= 1`, a real digest), while `status="rejected"`
  requires a `reason` and stays representable when the envelope never
  validated (`licence_version=0`, `digest=UNKNOWN_DIGEST` — the encoding the
  reference receiver's rejected acknowledgements already carry). The timestamp
  is `observed_at`, not "applied_at", because a rejected attempt applied
  nothing. `.acknowledgement` subsumes the narrower `LicenceAcknowledgement`
  for both statuses, so the existing ack path keeps working unchanged.

  Validation is strict, fail-closed (`MalformedAppliedStateError`) and lives
  in ONE place (`__post_init__`): direct construction and parsing give
  identical guarantees, so a producer can never build-and-serialise a report
  the other plane would reject. Unknown fields are ignored so a newer receiver
  cannot break an older vendor.

  This closes the channel three WS8 gaps depended on: acknowledgements the
  vendor can authenticate, keyring-uptake lag, and revocation-application lag
  — none of which a vendor can infer, because "we published it" says nothing
  about what a deployment holds.

### Changed
- **Dependency floors widened** to `fastapi>=0.111,<0.116`,
  `pydantic>=2.7.4,<3.0`, `pydantic-settings>=2.2,<3.0`, `cryptography>=42`,
  and **`python>=3.11`** (was `>=3.12`). Every floor matches a real consumer
  pin: fastapi 0.111.0 / pydantic 2.7.4 / cryptography 42.0.8 are `dotmac_sub`'s
  production versions, and both products declare `python>=3.11`. Nothing in the
  kernel needs 3.12 (`StrEnum` and `datetime.UTC` are 3.11; no PEP 695
  generics), so the 3.12 floor would have forced an interpreter upgrade in two
  products to consume contracts that do not require one. The previous
  `^0.115`/`^2.9` floors were driven by the kernel's own app/runtime modules —
  which product assemblies' architecture guards forbid them from importing —
  and so excluded dotmac_sub/dotmac_erp (fastapi 0.111.0 / pydantic 2.7.4)
  from consuming contracts that never touch FastAPI. A lowered floor is a
  support claim, so it is proven, not asserted: the required `kernel-floors`
  CI job (`scripts/kernel_floor_check.sh`) installs the built wheel into a
  clean venv with the floor versions pinned exactly and constructs each
  supported contract with no `DATABASE_URL` present. See COMPATIBILITY.md
  "Dependency floors" for the scope of the claim.
- **Optional `cryptography` floor lowered to `>=42`** — every Ed25519 API the
  kernel uses predates 42, and the floor probe signs and verifies a licence and
  a revocation list on 42.0.8
  (dotmac_sub's exact pin); the floor-proof job pins that version.
- **Extras split**: `[testing]` now pulls only `httpx`; `cryptography` moves
  exclusively to `[licensing]`. The ordinary fakes/harness/provisioning kit
  never touches cryptography, and a product consuming only the test kit must
  not inherit the licensing crypto stack. `FakeLicenceSigner` needs
  `[testing,licensing]`.

### Fixed
- **`dotmac_kernel.testing` no longer needs `DATABASE_URL` to import** (a7
  release defect): `harness` imported `dotmac_kernel.deps` at module scope,
  building the SQLAlchemy engine at import time, so even the fakes were
  unreachable without a database. The deps import moved inside
  `assembly_test_client`, the only helper that builds a real app.
- **`dotmac_kernel.profiles` added to `SUPPORTED_MODULES`** (a7 release
  defect): the WS1 registry was exported top-level but its submodule was
  undocumented, making the import path COMPATIBILITY.md documents technically
  unsupported.
- `tests/unit/test_tenant_middleware.py`'s fake ASGI client now sends
  `http.disconnect` after its request message. Starlette 0.37 (the
  fastapi-0.111 floor) awaits the disconnect and raises on a fake that
  replays `http.request` forever — the old fake made the full suite lie at
  the floor. Harness fidelity only; no middleware behavior change.

## 0.1.0a7 — 2026-08-01






Seventh alpha. Adds **WS8 signed-licence verification** — the kernel slice of
signed/versioned licence delivery (design brief:
`docs/superpowers/reviews/2026-08-01-ws8-signed-licence-design.md`). The kernel
**verifies only**: issuance and private-key custody stay in the vendor control
plane; a product data plane verifies a delivered envelope, projects the
verified capabilities into its OWN local WS2 grants, and acknowledges the
applied version + digest. No migration — the kernel head stays `0012` (the
receiver owns its durable applied/revocation state).

### Added
- **WS8 — signed-licence verification** (`dotmac_kernel.licensing`,
  submodule-only). DSSE-style `dotmac-licence-envelope/1` (Ed25519 signatures
  over the exact payload **bytes**; payload parsed only after a signature
  verifies; `payload_digest` = sha256 of those bytes is the replay/ack
  identity). `LicenceKeyRing` with `active`/`retired`/`revoked` rotation
  states (retired still verifies, revoked never does, unknown keys and
  duplicate ids fail closed). `verify_licence(...)` — fail-closed, offline,
  deterministic (injected clock): contractual check order envelope → signature
  → parse → revocation → deployment binding (optional + `require_binding`) →
  validity (`valid`/`in_grace` grace window; absent `expires_at` = perpetual)
  → replay/rollback vs the receiver's `AppliedLicence` (stale version
  rejected; same version+digest = idempotent reapply; same version, different
  digest = hard conflict). `verify_revocation_list(...)` — signed, monotonic
  `dotmac-licence-revocation/1` over the same envelope. Shared
  `LicenceAcknowledgement` value object; `LicenceError` subclass names are the
  stable rejection reasons.
- **Test signer** (`dotmac_kernel.testing.licensing.FakeLicenceSigner`) —
  ephemeral, per-instance, in-memory Ed25519 signer for vendor-plane and
  product tests; the only private key anywhere in the kernel.
- **`licensing` extra** — `pip install dotmac-kernel[licensing]` pulls
  `cryptography`; the module imports it lazily, so everything but signature
  verification works without it and verification without it raises
  `VerificationUnavailableError` (fail closed). The `testing` extra now also
  includes `cryptography` for the fake signer.

### Fixed
- `COMPATIBILITY.md`/`README.md` no longer hardcode a "current version" (both
  had drifted, still claiming `0.1.0a1`); they now point at `pyproject.toml` /
  this changelog.

## 0.1.0a6 — 2026-07-31






Sixth alpha. Adds the **platform outbox + platform relay** — the tenant-free peer
of the tenant outbox/relay, so a platform-scoped owner (e.g. a vendor
ContractService) can emit a durable control-plane event ATOMICALLY with its state
change and have it delivered out-of-band. A SEPARATE table and a SEPARATE
dispatcher role; the a5 leasing/backoff/dead-letter engine is reused, never
combined with the tenant table. Advances the kernel migration head to `0012`.

### Added
- **Platform outbox storage + write side** (`dotmac_kernel.messaging`).
  `PlatformOutboxEvent` — a PLATFORM catalog table (**no `tenant_id`, no tenant FK,
  no RLS**; GRANTed to `platform_api`/`app_admin`, REVOKEd from `app_user`) carrying
  the relay lease columns. `enqueue_platform_event(db, *, event_type, payload,
  correlation_id)` flushes a `pending` row into the caller's platform transaction —
  the same atomic guarantee as `enqueue_event`, tenant-free.
- **Platform relay security + functions** (kernel migration `0012`). A dedicated
  **`platform_outbox_dispatcher`** role (LOGIN, **not** BYPASSRLS/superuser, **no
  table privilege**), DISTINCT from both `platform_api` and the tenant
  `outbox_dispatcher`, may only `EXECUTE` two hardened, schema-qualified
  `SECURITY DEFINER` functions owned by `app_admin` — `claim_platform_outbox_batch`
  (atomic `FOR UPDATE SKIP LOCKED` claim incl. stale-lease reclaim) and
  `settle_platform_outbox_event`.
- **Platform relay behavior** (`dotmac_kernel.messaging.platform_relay`). Typed
  `claim_platform_batch` / `record_success` / `record_failure` and
  `ClaimedPlatformEvent` (no `tenant_id`). REUSES the tenant relay engine —
  `RelayPolicy`, `FailureOutcome`, and the backoff policy are imported, not
  duplicated.
- **Platform relay worker** (`dotmac_kernel.messaging.platform_worker`). Strict
  connection separation adapted to the platform plane: the `platform_outbox_dispatcher`
  connection only claims/settles; delivery runs on a SEPARATE `platform_api`
  session (the identity `process_once_platform` consumers use) with NO tenant
  context. `PlatformDeliveryTransport` protocol + `LoggingPlatformTransport`;
  `run_once`/`run_forever`; `scripts/run_platform_relay.py` entrypoint. At-least-once
  with one active claim per lease; consumers dedupe via `process_once_platform`.

## 0.1.0a5 — 2026-07-31






Fifth alpha. Completes **WS3 slice 2 — the outbox relay**: the leasing
schema + `outbox_dispatcher` security boundary (SECURITY DEFINER claim/settle,
EXECUTE-only), the typed relay behavior (claim/success/failure + retry/backoff/
dead-letter), and the polling worker (strict dispatcher/tenant connection
separation, clean shutdown). Advances the kernel migration head to `0011`.

### Added
- **WS3 relay leasing schema + security** (slice 2, PR 1; kernel migration
  `0011`). `outbox_events` gains lease columns (`leased_by`/`leased_at`) + a
  stale-lease index and the `OutboxStatus` vocabulary gains `claimed`/`dead`. A
  dedicated **`outbox_dispatcher`** role (LOGIN, **not** BYPASSRLS/superuser, **no
  table privilege**) may only `EXECUTE` two hardened, schema-qualified
  `SECURITY DEFINER` functions owned by `app_admin` — `claim_outbox_batch`
  (atomic `FOR UPDATE SKIP LOCKED` claim incl. stale-lease reclaim) and
  `settle_outbox_event` (records an outcome only for a row the caller holds a live
  lease on). The dispatcher's cross-tenant reach is confined to those two
  functions; a direct table read/write is `permission denied`.
- **WS3 relay behavior** (slice 2, PR 2; `dotmac_kernel.messaging.relay`). Typed
  operations over the claim/settle functions: `claim_batch` (leases a batch as
  `ClaimedEvent`s, each carrying `tenant_id`), `record_success` (→ `sent`), and
  `record_failure` (increments attempts, then backs off `pending` or dead-letters
  `dead` at `max_attempts`) — the retry/backoff/dead-letter **policy**
  (`RelayPolicy`) lives here; the SQL functions stay mechanical. Receives a
  dispatcher-bound `Session` and only executes (the worker owns the transaction).
  At-least-once, one active claim per lease.
- **WS3 relay worker** (slice 2, PR 3; `dotmac_kernel.messaging.worker` +
  `scripts/run_relay.py`). The separate polling process: `run_once`/`run_forever`
  claim a batch and deliver each event through a `DeliveryTransport` (Protocol;
  `LoggingTransport` is a reference). **Strict connection separation** — the
  dispatcher connection only claims/settles; each delivery's product reads use a
  **separate tenant-scoped connection** whose RLS context is restored to the
  event's own tenant. Clean SIGTERM/SIGINT shutdown. The worker module receives
  session factories and never builds an engine (the entrypoint script does).

Fourth alpha. Adds WS2 tenant entitlements (the data-plane's grant store +
explainable evaluator). Advances the kernel migration head to `0010`.

### Added
- **WS2 — tenant entitlements** (`dotmac_kernel.entitlements`). The data-plane's
  single entitlement authority: `TenantEntitlementGrant` (tenant-scoped,
  RLS-protected; kernel migration `0010`) is the grant store a commercial
  allocation projects into; `grant_entitlement(...)` writes a grant and REQUIRES
  the capability code be **declared** (WS1 — validated against a
  `CapabilityCatalogue`, never invented by a row); `is_entitled(...)` is the
  explainable, purely-local evaluator (`EntitlementDecision` with a stable
  `reason`) — it never calls a payment/licence provider. Allocation (what a tenant
  is entitled to) stays vendor-owned; this is only evaluation (whether a request
  is allowed). No parallel `tenant_module_entitlements` table.
- Kernel migration head advanced to `0010_tenant_entitlements`; the assembly
  lineage (`a001`) still pins an older head, so a fully-migrated database now
  reports `{0010, a001}`.

## 0.1.0a3 — 2026-07-31






Third alpha. Adds the WS1 capability catalogue + deployment-profile registry
(pure in-memory contracts). Additive over `0.1.0a2` — no breaking changes, no new
migrations (the kernel head stays `0009`).

### Added
- **WS1 — capability catalogue + deployment-profile registry** (pure, in-memory
  code contracts; no database, no fleet state). They *describe*, never *grant* or
  *deploy*.
  - `dotmac_kernel.capabilities` — `CapabilityCatalogue.from_manifests(...)` over
    a module's declared `FeatureManifest.capabilities` codes (e.g.
    `"inventory.use"`); `is_declared`/`require`/`owner`/`codes`. Fails closed on a
    duplicate code. A capability code may only be *referenced* by a grant/profile,
    never invented outside a manifest — the catalogue does not grant entitlement.
  - `dotmac_kernel.profiles` — `DeploymentProfileSpec` (frozen, **versioned**
    declaration over independent axes: required/forbidden modules, one provider
    per seam, locale/currency/legal/residency) + `DeploymentProfileRegistry`
    (unique `code`, `is_valid_code`, deterministic fail-closed `validate(...)`
    returning a `ProfileValidationReport.render()`). `(code, version)` is the
    stable identifier; the effective set changes only via an explicit version
    bump. A profile describes desired composition, not a fleet deployment.
  - New optional `FeatureManifest.capabilities` field (defaults `()`), the single
    declaration point for capability codes — forward-compatible with the eventual
    `ModuleManifest` expansion.

## 0.1.0a2 — 2026-07-30






Second alpha. Adds exact money/FX value objects and platform-scoped audit +
idempotency primitives, corrects the vendored font weights, and advances the
kernel migration head to `0009`. Additive over `0.1.0a1` — no breaking changes
to the `0.1.0a1` public surface.

### Fixed
- **Vendored font weights are now the real distinct weights.** Every Outfit and
  Plus Jakarta Sans weight had shipped as a byte-for-byte copy of the 400 file,
  so bold/semibold text silently rendered at weight 400. Re-vendored per-weight
  `woff2` (Latin subset, from `@fontsource`) for Outfit 400–800 and Plus Jakarta
  Sans 400–700. `tests/architecture/test_vendored_fonts.py` guards against
  byte-identical weights recurring, and the release inspection no longer needs to
  ignore the `check-wheel-contents` duplicate-file warning. (Latin subset covers
  the admin portal; extended glyphs such as ₦ via `latin-ext` are a follow-up if
  UI review needs them.)

### Added
- **`dotmac_kernel.money`** — exact money + FX primitives (WS4). `Money`
  (currency + quantized `Decimal`, never `float`; add/subtract/multiply,
  comparison, and `allocate`/`split` that distribute the rounding remainder so
  parts sum back exactly), `Currency` (ISO-4217 code + minor units) with a small
  registry + `currency(code)` lookup, and `ExchangeRate` (immutable, timestamped,
  sourced snapshot; `convert` applies it with explicit rounding). Pure values —
  import-safe and re-exported at the top level.
- **`dotmac_kernel.messaging`** — transactional outbox/inbox + idempotent command
  envelope (WS3, slice 1). `CommandEnvelope` + `process_once` process a command
  at most once per `(tenant_id, command_id)` (the `inbox_records` ledger replays
  a duplicate's result); `enqueue_event` writes an `outbox_events` row in the
  caller's transaction so an event is persisted iff the state change commits.
  Both tables are tenant-scoped with RLS (kernel migration `0008`). Submodule-only
  (pulls in the DB transaction authority). The outbox relay/dispatcher is a
  planned slice 2.
- **Platform-scoped audit + idempotency** — the platform-level counterparts to
  the tenant-scoped audit/inbox, for platform actors operating on platform-level
  resources (no tenant context): `write_platform_audit_event` +
  `PlatformAuditEvent` (top-level, import-safe) record a platform audit trail
  keyed to a `PlatformAdmin`; `dotmac_kernel.messaging.process_once_platform` +
  `PlatformInboxRecord` process a platform command at most once per `command_id`
  ALONE (globally unique, not per-tenant). Both back onto PLATFORM catalog tables
  (kernel migration `0009`): no `tenant_id`, no RLS, GRANTed to
  `platform_api`/`app_admin` and REVOKEd from `app_user`. Enables a
  platform-level assembly (e.g. the vendor control plane) to get the same
  idempotent-command + audit guarantees the tenant surface already has.
- Kernel migration head advanced to `0009_platform_audit_inbox`; the assembly
  lineage (`a001`) pins an older kernel head, so a fully-migrated database reports
  two lineage heads (`{0009, a001}`) and the assembly rollback stamp targets
  `assembly@base` (branch-aware) rather than `kernel@head`.

## 0.1.0a1 — 2026-07-30






First published release — the **alpha** of the DotMac platform kernel extracted
from the reference assembly (`dotmac_starter_mt`). `pip install --pre
dotmac-kernel` (prerelease; `pip` ignores it without `--pre`).

This is a prerelease of a **not-yet-stable** public API. It exists to prove the
real publish/consume path end-to-end (the reference repo is its own first
consumer) and to unblock downstream adoption against a pinned artifact.

### Public surface (see `COMPATIBILITY.md` for the authoritative list)

- **App composition** — `ProductAssemblySpec` (frozen) + `create_app(spec) ->
  FastAPI` (`dotmac_kernel.app_factory`, re-exported lazily at top level so
  `import dotmac_kernel` stays DB-free). A product assembles a pinned kernel +
  its own feature modules/branding/providers instead of forking kernel files.
- **Multi-tenant foundation** — config (`Settings`/`validate_settings`), the RLS
  `db` transaction authority (`get_db`/`get_platform_db`/`conflict_savepoint`),
  identity/tenancy models (`Party`/`Tenant`/`Role`/`AuthSession`/
  `UserCredential`/…), platform-actor catalog (`PlatformAdmin`/`PlatformSession`),
  route guards (`deps`/`web_deps`), the middleware stack (CSRF, tenant resolver,
  rate limit, security headers, observability), errors, audit write-side, CRUD,
  templating, settings resolver/admin, and the features registry.
- **Provisioning provider contract** — `dotmac_kernel.providers.provisioning`: a
  product-neutral `ProvisioningProvider` Protocol (`plan`/`apply`/`observe`/
  `cancel`) with typed frozen results, a status vocabulary, and a stable
  retryable/terminal error hierarchy. A contract, not a runner — concrete
  providers live outside the kernel.
- **Testing kit** — `dotmac_kernel.testing` (supported public API; HTTP helper
  behind the `testing` extra): `create_test_engine`/`isolated_session`/
  `assembly_test_client`, deterministic fakes (`FakeClock`, `FakeSeeder`,
  `InMemoryRateLimitStore`, `fake_branding`), and `FakeProvisioningProvider` +
  `check_provisioning_provider_contract` (the reusable provider-conformance suite).

### Packaging

- src layout (`src/dotmac_kernel/`), `poetry-core` build backend, Python
  `>=3.12,<3.14`.
- Runtime deps: `fastapi`, `sqlalchemy`, `pydantic[email]`, `pydantic-settings`,
  `jinja2`, `argon2-cffi`. The `email` extra is required — the public
  `create_app` mounts `platform_auth`'s `EmailStr` field. `psycopg` (DB driver)
  and `uvicorn` are deliberately consumer/deploy-supplied, not kernel deps.
- Optional `testing` extra adds `httpx` for `assembly_test_client`.
- Ships templates, static (incl. vendored fonts and the compiled
  `static/css/main.css`), and the kernel base Alembic migrations (`0001`–`0007`)
  as package data, resolved by package path — never CWD.
- Governance: `COMPATIBILITY.md` + `SUPPORTED_MODULES`/`INTERNAL_MODULES` +
  per-module `__all__` define the supported surface; an external `consumer-boot`
  proof installs the wheel into a clean venv and boots a public-imports-only
  consumer.
