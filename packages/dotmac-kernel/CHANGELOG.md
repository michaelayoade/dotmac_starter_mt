# Changelog — dotmac-kernel

All notable changes to the `dotmac-kernel` distribution. This package follows
[Semantic Versioning](https://semver.org); see `COMPATIBILITY.md` for the
public-surface stability policy. Pre-1.0 (`0.x`, incl. this alpha) the surface is
still settling — a `0.MINOR` bump may carry breaking changes, each called out
here.

## Unreleased

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
