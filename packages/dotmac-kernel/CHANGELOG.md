# Changelog — dotmac-kernel

All notable changes to the `dotmac-kernel` distribution. This package follows
[Semantic Versioning](https://semver.org); see `COMPATIBILITY.md` for the
public-surface stability policy. Pre-1.0 (`0.x`, incl. this alpha) the surface is
still settling — a `0.MINOR` bump may carry breaking changes, each called out
here.

## Unreleased

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
- Kernel migration head advanced to `0008_outbox_inbox`; the assembly lineage
  (`a001`) now pins an older kernel head, so a fully-migrated database reports
  two lineage heads (`{0008, a001}`) and the assembly rollback stamp targets
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
