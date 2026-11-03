# dotmac_starter_mt

A multi-tenant FastAPI starter **template** — clone it, pick the features you
need, register your own entities and settings, brand it, ship it. Tenant
isolation is enforced at three layers:

1. **Routing** — subdomain (or custom domain) resolves to a tenant before any
   request handler runs.
2. **Application** — every service receives `tenant_id` via request state,
   never from a client-supplied payload.
3. **Database** — PostgreSQL Row-Level Security policies fail closed if app
   code forgets to filter.

A single-tenant product is simply a deployment of this app with **one**
tenant row (`POST /platform/tenants`) — not a different codebase, not a
different code path.

For new development, the accepted direction is to use this same foundation
for vendor SaaS, dedicated hosted, self-hosted/on-premise, OEM, and
single-tenant deployments. The foundation is partially implemented rather
than merely planned: the versioned kernel and reference assembly now ship
a validating `ModuleManifest`/`ModuleRegistry`, manifest-owned permission and
audit-action catalogues with enforced consumers, capability/profile contracts,
tenant-local entitlements, and a WS8 licence receiver alongside the legacy
`FeatureManifest`, `DISABLED_FEATURES`, and `WEB_ENABLED`. Runtime provider
selection and the complete effective-capability lifecycle remain follow-on work.

The strategic end state is broader than a reusable starter template:
`dotmac_erp`, `dotmac_crm`, and `dotmac_sub` are transitional source monoliths
being recomposed into a universal kernel, shared UI, independently versioned
domain modules, and thin product assemblies. Read
[`docs/PRODUCT_VISION.md`](docs/PRODUCT_VISION.md) for why Starter exists, what
“build once” means, the ownership boundaries, and the path to full adoption.

See [`docs/adr/0001-multi-tenant-architecture.md`](docs/adr/0001-multi-tenant-architecture.md)
for the founding tenancy design, [`docs/adr/0002-starter-consolidation.md`](docs/adr/0002-starter-consolidation.md)
for how this repo became the org's one starter template,
[`docs/adr/0003-unified-deployment-profiles.md`](docs/adr/0003-unified-deployment-profiles.md)
for the accepted deployment-profile and commercial-module decisions,
[`docs/adr/0004-platform-control-plane.md`](docs/adr/0004-platform-control-plane.md)
for the platform control-plane security decisions,
[`docs/adr/0005-forgejo-private-registry.md`](docs/adr/0005-forgejo-private-registry.md)
for kernel artifact distribution, and
[`docs/adr/0006-white-label-product-foundation.md`](docs/adr/0006-white-label-product-foundation.md)
for the package/module/theme/brand/facet boundaries and extraction rule,
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full architecture
reference (including the model provenance/ownership tables), and
[`AGENTS.md`](AGENTS.md) for the canonical agent-facing rules.

## Documentation map

The hierarchy, explicitly — when documents disagree, the higher authority
for its scope wins and the stale one gets fixed:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — **as-built truth**: what
  the system actually does today (model provenance, ownership, transaction
  authority, settings, portal).
- [`docs/MODULE_CATALOG.md`](docs/MODULE_CATALOG.md) — **generated module
  discovery**: every reusable distribution's contract, owner, evidence state,
  persistence plane, release policy and proven/candidate consumers. Generate
  it from the owning metadata with `make module-catalog`; never edit it by hand.
- [`docs/PRODUCT_VISION.md`](docs/PRODUCT_VISION.md) — **strategic north
  star**: why Starter exists and the target ecosystem into which ERP, CRM, and
  Sub are being recomposed. It does not claim as-built behaviour.
- [`docs/adr/`](docs/adr/) — **decisions + status**: why the system is
  shaped this way; each ADR carries its status, and amendments are dated
  notes, never rewritten history.
- [`docs/inventories/`](docs/inventories/) — **dated characterization**:
  cross-repository facts captured at named revisions. Inventories are evidence,
  not mandates, and similarity recorded there does not authorize extraction.
- [`docs/superpowers/plans/`](docs/superpowers/plans/) (and `specs/`,
  `reviews/`) — **non-authoritative intent**: how work was planned; never
  cite a plan as proof of current behavior.
- [`README.md`](README.md) (this file) — **onboarding**: what this is and
  how to run it.
- [`AGENTS.md`](AGENTS.md) — **agent rules**: the canonical, tool-neutral
  hard-rules list with enforcing tests (`CLAUDE.md` indexes it and adds the
  repo map + web-portal specifics).
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — **human dev rules**: gates,
  test-first expectations, migration discipline, PR expectations.
- [`docs/SECURITY.md`](docs/SECURITY.md) — **security posture**: honest
  OWASP ASVS 5.0 L2 mapping, CSP rationale, rate-limit store seam.

## Deployment direction (accepted; implementation planned)

One codebase will compose independent deployment axes—tenancy topology,
operator, connectivity, commercial authority, identity, branding, domains,
locale, currency, legal/tax authority, data residency, UI surface, updates,
and telemetry—through typed deployment profiles, module manifests, and provider
interfaces. A profile name is configuration shorthand, not a value that feature
code branches on. Even a one-tenant deployment keeps the tenant row, tenant
context, composite tenant constraints, and PostgreSQL RLS.

Commercial concerns are deliberately separate:

- **Entitlements** are the common capability-decision layer: what a tenant may
  use, within which dates and limits, and why.
- **Subscriptions** are optional: plans, trials, renewals, grace periods, and
  cancellation. They are needed only when the product has a recurring
  commercial lifecycle.
- **Billing** is optional: payment-provider customers, invoices, payments,
  refunds, and webhooks. Enterprise/manual/ERP invoicing can omit it.
- **Metering** is optional: immutable usage events and quota or billable-usage
  aggregates. It is needed only for quantitative limits or usage pricing.
- **Licensing** is optional: signed offline or delegated grants, commonly for
  commercial on-premise and OEM distribution.

Consequently, a perpetual on-premise install can use entitlements plus a
signed license with no subscription or billing module; an invoiced enterprise
deployment can use subscriptions without in-product billing; and self-service
paid SaaS can install all of them. See the
[`deployment profiles and commercial platform plan`](docs/superpowers/plans/2026-07-18-deployment-profiles-commercial-platform.md)
for the authority model, workstreams, sequencing, and completion gates.

Tenant domains follow the same provider model. The application owns normalized,
verified domain-to-tenant mappings; a profile-selected ingress provider owns
proxy bindings and TLS automation. Nginx is a valid static reverse proxy, but
dynamic customer domains also require a certificate/DNS controller (or a
managed load balancer, Caddy, Traefik, or Kubernetes ingress + cert-manager).
The target workflow verifies DNS ownership before binding or issuing a
certificate and never trusts an arbitrary first-request `Host` header.

The accepted reuse model is also more than cloning. A clone is a snapshot and
does not receive later fixes automatically. The target platform publishes a
versioned kernel and optional modules; each product is a thin
`ProductAssemblySpec` that pins those versions and adds only its domain modules,
providers, brand, policies, and deployment profile. A core fix produces one
release, automated update PRs for maintained products, their profile/lifecycle
tests, and then a staged or customer-approved redeploy. Running deployments
never change silently.

The app remains both API-capable and web-capable. Business rules live in shared
services; JSON routers and the built-in Jinja/HTMX `web` module are parallel
adapters. Keep the working admin portal when useful, use `WEB_ENABLED=false`
for API-only deployments, or build separate SPA/mobile/partner frontends
against versioned OpenAPI contracts and the same authorization/capability APIs.

Existing `dotmac_erp`, `dotmac_crm`, and `dotmac_sub` are transitional source
monoliths, not big-bang rewrite targets; `dotmac_academy_app` joined the same
plan on 2026-08-07 as a discovery target only. Their proven capabilities move
through product-first vertical extraction into shared domain modules, while the
source product becomes that module's first cutover consumer and retires its
local writer. Product assemblies stay in their own repositories — Starter
publishes the platform and module contracts rather than absorbing every product
into one application or database. Backoffice/ERP, Engagement/CRM, and ISP
Operations/Sub normally remain separate deployments and integrate through
versioned APIs/events where one assembly does not install a capability locally.
Other ISP operators should first receive dedicated one-tenant ISP deployments;
shared multi-ISP SaaS follows only after an explicit cross-ISP isolation
program. See the
[`existing product adoption plan`](docs/superpowers/plans/2026-07-18-existing-product-adoption.md).

Do not create a permanent ISP branch or clone from this repository. Continue the
kernel/platform contracts here, adapt the existing `dotmac_sub` as the ISP
assembly, and build vendor accounts/contracts/fleet/licensing in a separate thin
control-plane assembly. Temporary feature branches are normal, but products
consume versioned kernel/module releases so fixes propagate through tested update
PRs rather than source copying.

## Starting a project from this template

```bash
git clone <this-repo> my-app && cd my-app
# Rename: pyproject.toml [tool.poetry].name, IMAGE_NAME defaults in
# Makefile/scripts/deploy.sh, and this README's title.
```

Then, in order:

1. **Pick your features.** Nine assembly features ship today: `tenants`, `auth`,
   `parties`, `rbac`, `settings`, `presentation`, `custom_fields`, `licensing`,
   `web`. The assembly also composes the Template Studio and Ticketing modules
   (see `app/assembly.py` and "What's in this template"
   below). Disable what you don't need via `DISABLED_FEATURES` (fast — see
   "Disabling a feature" below), or delete the package under
   `app/features/` and remove it from `FEATURE_MODULES` in
   `app/features/__init__.py` plus the import-linter independence contract
   in `pyproject.toml` (permanent). Submit the change to Git-hosted PR CI —
   `tests/architecture/test_feature_manifests.py` fails if the
   registry, the import-linter contract, and the `app/features/` directory
   drift apart.
2. **Register your own entities.** `Party` (person/organization) is the
   identity source of truth every shipped feature binds to — don't invent a
   parallel "customer" or "user" table. If your domain needs its own
   entities (orders, devices, tickets, whatever), add them as a new feature
   package following the existing ones as a template (`models.py`,
   `schemas.py`, `service.py`, `router.py`, `feature.py`), and register any
   entity that should carry runtime-defined custom fields in
   `app/features/custom_fields/registry.py`'s `ENTITY_MODELS` (see the
   quick example below).
3. **Register your own settings.** Add a `SettingSpec` to your feature's own
   spec module and call `dotmac_kernel.settings_resolver.register_specs([...])`
   at import time (see `app/features/settings/spec.py` for the pattern). A
   spec with no real reader anywhere in the code fails the
   no-orphan-settings architecture test — wire the `resolve_value(...)`
   call before you ship it.
4. **Brand it.** Deployment-wide identity (name, tagline, colors, support
   email, app URL) is `brand.json` (repo root) + `BRAND_*` env var
   overrides — see `.env.example`. Per-tenant branding overrides on top of
   that live in the `settings/branding/ui_branding` setting, editable at
   `/admin/settings/branding`. Both layers are consumed by the admin portal;
   resolved colours are served as generated token CSS from the tenant-scoped
   `/branding/theme.css` route, never as tenant-authored raw CSS (see "Admin
   portal" below).

To run the app locally, see **Quickstart (dev)** below. Tests run only on
Git-hosted CI; see **Testing and validation** below. The CI test databases
are separate from the dev database used for running the app.

## What's in this template

- `Tenant` + `TenantDomain` for subdomain and custom-domain resolution.
- `Party` (`party_type` person|organization) — the fleet-wide identity
  model, with `PartyPerson`/`PartyOrganization` subtype profile tables. See
  `POST /parties/people`, `POST /parties/organizations`.
- Minimal JWT auth (`/auth/register`, `/auth/login`, `/auth/me`) with
  tenant-bound credentials and sessions.
- Minimal RBAC (`/rbac/roles`, `/rbac/role-grants`) plus a tenant audit-event
  log (`/rbac/audit-events`).
- **Settings-as-data**: a tenant admin API (`GET`/`PUT /settings/{domain}/{key}`)
  backed by a typed spec registry, tenant → platform-default → spec-default
  resolution, secret masking, and audit-on-write. See
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#settings-resolution-order--platform-row-rls-design).
- **Custom fields**: define a field on any registered entity at runtime —
  no migration, no deploy — and set/read its value through a generic
  values API. This is the template's signature capability; see the
  quickstart below.
- **Admin portal**: a server-rendered HTML/HTMX admin UI (Jinja2 + Tailwind
  v4 + Alpine) at `/admin/*` — cookie-based login, dashboard, and CRUD
  screens for parties, RBAC roles/grants/audit, settings (incl. a friendly
  branding editor), and custom fields. Deletable (`DISABLED_FEATURES=web`)
  without losing the JSON API. See "Admin portal quickstart" below and
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#admin-portal-web-ui) for the
  auth flow, branding pipeline, and cross-feature UI composition pattern.
- CSRF middleware, tenant-aware in-memory rate limiting, and request IDs.
- `TenantResolverMiddleware` that parses the host header → `request.state.tenant`.
- `get_db` dependency that runs `SET LOCAL app.current_tenant` for RLS.
- Alembic migrations that create the `app_user`/`platform_api`/`app_admin`
  Postgres roles, apply RLS policies, and seed platform-default settings.
- Cross-tenant isolation tests as canaries for every tenant-scoped table
  (parties, settings, custom fields, RBAC/audit, auth).

CI (lint/type-check/security/import-boundaries, unit, Postgres RLS
integration, Docker build + health gate — see `.github/workflows/ci.yml`)
and a production Dockerfile/compose (`Dockerfile`, `docker-compose.yml`,
`docker-compose.dev.yml`, `scripts/deploy.sh`) are already in place; see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#deploy) for how they fit
together.

## What's NOT here yet

This is intentionally minimal outside the tenancy/governance foundation. To
productionize further, port what your project needs:

- MFA, password reset, account lockout, and production auth hardening
  (phase 2c on this repo's own roadmap; fleet-specific rationale lives in
  `docs/adr/`, not here)
- Product upload routes/UI, a concrete MinIO/S3 adapter, notifications,
  scheduler. The optional `dotmac-files` package now owns dual-plane
  validation, stored-object metadata and repairable physical lifecycle through
  explicit tenant or platform scope, and the optional `dotmac-imports` package
  owns the bulk-import run ledger, source-bound dry-run→apply promotion,
  resumable caller-owned chunks and column mapping (the importing domain keeps
  the field vocabulary, validation and mutation).
  This reference assembly deliberately composes neither (ADR-0022, ADR-0023,
  ADR-0025).
- Manifest-owned declaration catalogues for entity types and jobs, plus
  runtime migration-current/health evidence and
  the effective-capability admin UI described by the module control-plane
  directive. The validating module, permission, and audit-action registries,
  per-module schema/migration ownership gates, tenant-local entitlement grants,
  and their explainable evaluator already exist in the kernel. Manifest-owned
  setting domains and typed feature-flag declarations also exist.
- Runtime selection/wiring of the typed deployment-profile contracts, plus a
  provider registry for SaaS, dedicated, on-premise/air-gapped, OEM, and
  API-only packaging
- Versioned optional-module distribution, automated dependency-update PRs,
  signed images/offline bundles, and the cross-product compatibility matrix
  needed for one fix to propagate safely. The versioned `dotmac-kernel`, thin
  `ProductAssemblySpec`, wheel proof, and exact alpha release path exist today.
- Incremental ERP/subscriber-management assembly adoption, including a dedicated-
  per-ISP path and the separate tenant-safety program required before shared
  multi-ISP SaaS
- End-to-end tenant lifecycle orchestration: onboarding, activation, provider
  jobs, restriction/suspension/recovery, support access, cancellation, export,
  retention/legal hold, provider cleanup, and purge
- Fleet provisioning automation: a durable control-plane workflow, reusable
  OpenTofu infrastructure modules, remote state/locking, cloud-init/Ansible host
  bootstrap, isolated dedicated-ISP Compose deployments, optional Helm/GitOps
  execution at larger scale, activation gates, drift reconciliation, and
  auditable day-two operations. Kubernetes is not required for the first
  dedicated-ISP deployment profile.
- On-prem distribution/IP assurance tiers: signed minimal runtime images and
  offline bundles, build-secret/layer hygiene, digest/provenance verification,
  licence binding, optional compiled high-value modules, and a separately
  threat-modelled attested-appliance path. Customer-controlled root access can
  never be described as guaranteed source secrecy; use vendor-managed dedicated
  hosting when that guarantee is a commercial requirement.
- Tenant-safe fleet support and maintenance: OpenTelemetry logs/metrics/traces,
  readiness and synthetic checks, outbound-only authenticated collectors,
  health-only/local-only/air-gapped modes, SLO-based alerting, support cases and
  SLA clocks, redacted diagnostic bundles, consented just-in-time access with
  session audit, incident communication, version/EOL inventory, backup-restore
  proof, drift detection, and canary maintenance waves. Permanent vendor SSH or
  invisible tenant impersonation are not acceptable support mechanisms.
- Internationalization and global commerce primitives: stable message IDs and
  locale catalogs, RTL/pluralization, exact multi-currency Money/FX snapshots,
  immutable prices/rating, invoicing/collections, versioned tax/jurisdiction
  policy, legal entities, and data residency
- Tenant-domain provisioning: separate platform/tenant hosts, DNS ownership
  verification, ingress reconciliation, TLS issue/renew/revoke, canonical-host
  policy, and production proxy/header trust configuration. The resolver and
  `TenantDomain` model exist today, but no safe write/reconciliation path does.
- Optional subscription, billing, metering, and complete OEM delegation/
  commercial-lifecycle modules. WS8 verification, revocation, applied-state
  contracts, the reference receiver, and tenant-local entitlements exist, but
  they do not imply those optional commercial modules.
- Profile generator, deployment-specific packaging, and CI profile matrix
- A self-service portal facet. The current staff facet requires
  `web.portal.staff.access`; the assembly grants it to `admin` by default.
  Authentication itself no longer hardcodes a role.

Each port follows the pattern already established: add `tenant_id`, write
the cross-tenant isolation test first, port the code, watch the test go
green.

## Quickstart (dev)

Prerequisites for running the application from this checkout:

- Python 3.12 or 3.13 (3.12 matches the production image) and Poetry **2.4.1**,
  as pinned in `pyproject.toml`.
- Rust **1.90.0**, Cargo, and the platform's native compiler/linker tools.
  Several editable path dependencies build native extensions during install;
  their build hooks enforce the Rust version. Python build dependencies are
  declared by the packages; there is no silent Python fallback.
- Docker with Compose v2 for Postgres, Node.js/npm for the Tailwind build
  (the Docker build uses Node 20), and GNU Make for the convenience targets.
- A Bash-compatible shell for the commands below; on Windows, use an
  appropriate Bash environment. The HTTP examples also use `curl` and `jq`.

Install runtime dependencies only on a workstation. Test/development dependency
installation and test execution belong to Git-hosted CI (`CONTRIBUTING.md`).

`docker-compose.yml` is prod-only (requires a published `APP_IMAGE`, no `db`
service) — for local dev, run just the Postgres service from the dev overlay
(`docker-compose.dev.yml`), migrate against it, then run the app directly
with `--reload`:

```bash
poetry install --only main
docker compose -f docker-compose.dev.yml up -d --wait postgres   # DEV_DB_PORT/DEV_POSTGRES_*/DEV_*_PASSWORD overridable
# The dev Postgres init hook (scripts/dev-db-init.sh, runs once per fresh
# volume) creates the SAME three roles production uses — app_user (requests,
# RLS-enforced), platform_api (platform routes), app_admin (migrations,
# BYPASSRLS). No superuser anywhere in the dev flow.
export DATABASE_URL=postgresql+psycopg://app_user:app_user@localhost:5432/starter
export PLATFORM_DATABASE_URL=postgresql+psycopg://platform_api:platform_api@localhost:5432/starter
export MIGRATION_DATABASE_URL=postgresql+psycopg://app_admin:app_admin@localhost:5432/starter
export ENVIRONMENT=dev
poetry run alembic upgrade heads
make css-build   # builds packages/dotmac-kernel/src/dotmac_kernel/static/css/main.css
make dev   # or: poetry run uvicorn app.main:app --reload --port 8000
```

Use `heads` (plural): the reference assembly composes kernel, assembly,
Template Studio, and Ticketing migration lineages. `make migrate` runs the
same upgrade. Export the migration URL explicitly; an explicitly blank
`MIGRATION_DATABASE_URL` is rejected. Migrations use the owner role, while
the running application uses the tenant and platform roles above.

The compiled Tailwind CSS is gitignored. Rebuild after editing
`packages/dotmac-kernel/src/dotmac_kernel/static/css/src/main.css`, or use
`make css-watch`. The separate `dotmac-ui` package ships committed assets.

Row-Level Security is ENFORCED in this flow — `make dev` runs with the same
tenant isolation as production (`tests/test_rls_catalog.py` audits the live
catalog: RLS + FORCE + policy + grants + composite FKs on every table). If
you have a dev volume from before the three-role change, recreate it:
`docker compose -f docker-compose.dev.yml down -v`.

Platform routes require a platform admin (there is no HTTP self-registration
for the control plane — the CLI is the only bootstrap path, behind the same
trust boundary as migrations):

```bash
poetry run python scripts/create_platform_admin.py you@example.com   # prompts for a password
PLATFORM_TOKEN=$(curl -s -X POST http://localhost:8000/platform/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"you@example.com","password":"<the password you set>"}' | jq -r .access_token)
```

In dev, browsers resolve `*.localhost` automatically:

```bash
# Provision two tenants — each atomically gets a login-able OWNER with the
# admin role (registration is policy-closed by default; provisioning is the
# only owner-creation path)
curl -X POST http://localhost:8000/platform/tenants \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $PLATFORM_TOKEN" \
    -d '{"slug":"acme","name":"ACME","owner_email":"admin@acme.com","owner_password":"correcthorsebatterystaple"}'
curl -X POST http://localhost:8000/platform/tenants \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $PLATFORM_TOKEN" \
    -d '{"slug":"widgets","name":"Widgets Inc","owner_email":"admin@widgets.example","owner_password":"correcthorsebatterystaple"}'

# Log in as the ACME owner, then use the tenant-scoped API
ACME_TOKEN=$(curl -s -X POST http://acme.localhost:8000/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"admin@acme.com","password":"correcthorsebatterystaple"}' | jq -r .access_token)
curl -X POST http://acme.localhost:8000/parties/people \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $ACME_TOKEN" \
    -d '{"email":"alice@acme.com","first_name":"Alice","last_name":"A"}'
curl -H "Authorization: Bearer $ACME_TOKEN" http://acme.localhost:8000/parties     # sees Alice
curl http://widgets.localhost:8000/parties  # 401 — and RLS isolates the data regardless
```

## Admin portal quickstart

The portal is server-rendered HTML (Jinja2 + HTMX + Alpine, Tailwind v4
CSS), served from the same FastAPI process as the JSON API — no separate
frontend build/deploy. Continuing from the Quickstart above (Postgres
migrated, at least one tenant provisioned):

```bash
npm ci                 # installs the Tailwind v4 CLI (devDependency, pinned via package-lock.json)
make css-build          # -> packages/dotmac-kernel/src/dotmac_kernel/static/css/main.css (gitignored)
make dev                # poetry run uvicorn app.main:app --reload --port 8000
```

Then, in a browser (dev resolves `*.localhost` automatically, so no
`/etc/hosts` edits needed):

1. Use the tenant's provisioned OWNER account (created atomically by the
   `POST /platform/tenants` call in the Quickstart — it holds the `admin`
   role the portal requires). There is no signup form and self-registration
   (`/auth/register`) is policy-closed by default (`auth.registration_policy`
   setting; a registered user is a plain user with no roles either way).
2. Visit `http://acme.localhost:8000/admin/login` and sign in with the
   owner email/password. The native login form carries a hidden CSRF token;
   later HTMX mutations use the CSRF header-bridge (`static/js/csrf.js`).
3. Land on `http://acme.localhost:8000/admin` — the dashboard, showing
   party/role/active-session counts for this tenant.
4. From the sidebar: **Parties** (list/create/edit/detail, with a
   custom-fields values panel on the detail page — the cross-feature UI
   composition pattern, see ARCHITECTURE.md), **RBAC** (roles, role
   grants, audit log), **Settings** (per-key editor + a friendly branding
   editor at `/admin/settings/branding` for your `name`/`tagline`/`logo_url`/
   `primary_color`/`accent_color` — tenant-supplied CSS is not accepted),
   **Custom Fields**
   (definitions CRUD).
5. `http://widgets.localhost:8000/admin` (a second tenant, its own
   provisioned owner login) sees none of tenant A's data — same RLS
   isolation the JSON API gets, proven end-to-end by
   `tests/test_admin_portal_e2e.py`.

<!-- Screenshot: admin/dashboard.html — dashboard with stat cards -->
<!-- Screenshot: admin/parties/detail.html — party detail + custom-fields values panel -->
<!-- Screenshot: admin/settings/branding.html — branding editor with live preview -->

Screenshots deferred (no CI artifact pipeline for them yet) — the
placeholders above mark where they'd go; capture manually against a
`make dev` instance if you want them for a fork's own docs.

## Quick example: custom fields (zero migrations)

This is the template's signature capability — a tenant admin adds a new
field to an entity at runtime, no Alembic migration or deploy involved, and
uses it immediately. Requires a party to already exist and an admin-role
party bearing a bearer token (see Quickstart above / `/auth/login`) —
abbreviated here as `$TOKEN`:

```bash
# 1. Define a field on the "party" entity — a plain row insert, nothing else.
curl -X POST http://acme.localhost:8000/custom-fields/definitions \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{
      "entity_type": "party",
      "field_code": "eye_color",
      "field_name": "Eye Color",
      "field_type": "TEXT",
      "is_required": false
    }'

# 2. Use it immediately — set the value on an existing party.
curl -X PUT http://acme.localhost:8000/custom-fields/party/<party_id>/values \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"eye_color": "brown"}'

# 3. Read it back.
curl http://acme.localhost:8000/custom-fields/party/<party_id>/values \
    -H "Authorization: Bearer $TOKEN"
# {"eye_color": "brown"}
```

No migration ran between steps 1 and 3 — `eye_color` lives as a key in
`Party.custom_fields` (JSONB), validated against the definition's
`field_type`/`is_required`/etc. on every write. `party` is registered as a
custom-fields entity out of the box
(`app/features/custom_fields/registry.py::ENTITY_MODELS`); register your
own entities there the same way. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#custom-fields-value-flow) for
the full validate → merge → flag_modified write path.

## Disabling a feature

Every feature can be turned off without deleting code, via the
comma-separated `DISABLED_FEATURES` env var (see `.env.example`):

```bash
# Ship without custom fields and without the settings admin API:
export DISABLED_FEATURES="custom_fields,settings"
```

`dotmac_kernel.features.mount_features` skips any feature whose name is in this
set — its routers (both JSON `routers` and HTML `web_routers`, see below)
are never mounted, so its endpoints simply don't exist (404, not a guard
failure). This is the fast path for "starting a project from this template"
step 1 above; delete the package under `app/features/` only once you're
sure you'll never want the feature back (Git-hosted unit/architecture CI checks
whether the registry and import-linter contract agree with what's on disk).

Each feature's `FeatureManifest` (`app/features/<name>/feature.py`)
declares TWO separate router groups — `routers` (its JSON API) and
`web_routers` (its `web.py` admin-portal screens) — plus `nav` (its sidebar
entries). `DISABLED_FEATURES=<name>` still turns off BOTH router groups
together for that one feature (there is no per-router granularity: disabling
`parties`, say, drops its `/admin/parties/*` screens AND `/parties/*` from
the JSON API in one move) — see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#capability-model-manifest-driven-surfaces-2b1-t1-findings-f1--f5)
for the full capability model, including the nav↔routes coherence test and
the optional-slot pattern for embedding one feature's UI inside another's
page.

**`DISABLED_FEATURES=web` is special: it does NOT disable the portal's
login, and it does NOT remove any JSON API.** The `web` feature package
owns exactly one route — `GET /admin`, the dashboard shell — so disabling
it drops only that landing page. `GET`/`POST /admin/login` and
`POST /admin/logout` are owned by the `auth` feature (core, always mounted)
and stay up regardless. Every other feature's own `/admin/*` screens
(parties, RBAC, settings, custom fields) are mounted from THAT feature's
own manifest, independently.

**Real API-only mode: `WEB_ENABLED=false`.** This is a DIFFERENT knob from
`DISABLED_FEATURES` — a whole-portal surface switch, not a per-feature one.
`WEB_ENABLED=false` (see `.env.example`) mounts NO feature's `web_routers`
at all (zero `/admin` routes across every feature) and also drops the
`/static` asset mount — there is no HTML UI left to serve assets to. Every
feature's JSON `routers` keep working completely unchanged. This is the
real "JSON API only, zero portal HTML" deployment path; it composes with
`DISABLED_FEATURES` (e.g. `WEB_ENABLED=false` plus `DISABLED_FEATURES=
custom_fields` ships a pure JSON API with the custom-fields feature removed
entirely).

## Route map

| Prefix | Feature | Notes |
|---|---|---|
| `GET /health` | — | Liveness only, no DB touch, no guard (allowlisted). |
| `POST /platform/auth/login`, `POST /platform/auth/logout` | — (core, `dotmac_kernel.platform_auth`) | Platform-root-domain only; login is pre-auth (host-guarded), logout requires `require_platform_admin`. |
| `POST/GET /platform/tenants`, `GET /platform/tenants/{id}` | `tenants` | Platform-root-domain only, `require_platform_admin` (see ADR-0004); `POST` provisions tenant + owner atomically. |
| `POST /auth/register`, `POST /auth/login`, `GET /auth/me` | `auth` | Tenant-scoped JWT flows. |
| `POST /parties/people`, `POST /parties/organizations`, `GET /parties`, `GET /parties/{id}`, `DELETE /parties/{id}` | `parties` | Identity CRUD for both party types. |
| `POST/GET /rbac/roles`, `POST /rbac/role-grants`, `GET /rbac/audit-events` | `rbac` | Granular `rbac.roles.manage`, `rbac.roles.read`, `rbac.grants.manage`, and `rbac.audit.read` permissions; mapped to `admin` by the assembly. |
| `GET /settings/{domain}`, `PUT /settings/{domain}/{key}` | `settings` | Tenant settings admin API — `require_role("admin")`. |
| `POST/GET /custom-fields/definitions`, `GET/PATCH/DELETE /custom-fields/definitions/{id}`, `GET/PUT /custom-fields/{entity_type}/{entity_id}/values` | `custom_fields` | Field definitions CRUD + values — `require_role("admin")`. |

Every route above (except `/health`) carries a `require_*` guard —
enforced by `tests/architecture/test_route_guards.py`.

### Admin portal routes (`/admin/*`)

The public pre-auth `GET /branding/theme.css` route belongs to the stateless
`presentation` feature. Tenant hosts receive the same brand token overrides on
the login and authenticated surfaces with `private, no-store`; the exact
platform host receives package defaults and unknown hosts fail closed. It is
not owned by Template Studio.

| Prefix | Feature | Notes |
|---|---|---|
| `GET`/`POST /admin/login`, `POST /admin/logout` | `auth` | Cookie login/logout — `/login` is deliberately unguarded (pre-auth); `/logout` is a CSRF-protected POST (was a CSRF-exempt GET), carries `require_tenant` only (no `require_web_auth` — logout always succeeds, even on an expired/foreign-tenant cookie). Both allowlisted with a comment. |
| `GET /admin` | `web` | Dashboard shell — the one route this deletable feature owns. |
| `GET /admin/parties`, `/create`, `POST /admin/parties/{people,organizations}`, `GET /admin/parties/{id}`, `GET`/`POST /admin/parties/{id}/edit`, `POST /admin/parties/{id}/delete` | `parties` | List/detail/create/edit/delete screens. |
| `GET`/`POST /admin/roles`, `/admin/roles/create`, `GET`/`POST /admin/role-grants`, `GET /admin/audit` | `rbac` | Roles, grants, audit log screens. |
| `GET /admin/settings`, `GET`/`POST /admin/settings/{domain}/{key}/edit`, `GET`/`POST /admin/settings/branding` | `settings` | Generic per-key editor + friendly branding editor. |
| `GET`/`POST /admin/custom-fields`, `/create`, `GET`/`POST /admin/custom-fields/{id}/edit`, `POST /admin/custom-fields/{id}/deactivate`, `GET`/`POST /admin/custom-fields/party/{id}/values-panel` | `custom_fields` | Definitions CRUD + the values-panel fragment `parties/detail.html` embeds. |

Authenticated staff pages receive the facet's declared cookie-authentication
profile and `web.portal.staff.access` admission permission. Contract-v1 routes
use `require_web_auth` for the party/roles mapping; that helper no longer
requires a hardcoded role. V2 routes retain their own granular permissions.
See `app/assembly.py` and `dotmac_kernel.web_deps`.

## Testing and validation

Tests and test/development dependency installation run only on Git-hosted CI,
not on workstations or named test hosts. Push a branch and inspect the PR's
checks. Local read-only inspection, editing, formatting, and already-available
pinned static checks are allowed; they do not replace hosted test evidence.
See `CONTRIBUTING.md` and `AGENTS.md` § "Validation before any commit".

The hosted workflow `.github/workflows/ci.yml` owns setup and execution:

- Unit and architecture tests cover behavior and structural contracts; SQLite
  tests cannot prove PostgreSQL Row-Level Security.
- Integration tests use disposable, migrated Postgres databases and separate
  tenant, platform, and migration connections. Cross-tenant canaries exercise
  API and browser isolation.
- `make test-integration` requires `CI=true`, `IDENTITY_REFERENCE_DATABASE_URL`,
  and `IDENTITY_GRAPH_DATABASE_URL`; starting `make test-db-up` alone is
  insufficient. The hosted workflow provisions the additional databases and
  supplies these values. Do not set `CI=true` locally to bypass this boundary.
- Direct pytest invocation without `TEST_DATABASE_URL` can skip database tests;
  skipped tests are not isolation evidence. Use the hosted workflow's complete
  configuration rather than copying an isolated command.

CI also runs the `make check` quality targets, allocation and release-provenance
gates, consumer/artifact and native-compute checks, and a Docker build/health
gate. The workflows are the source of truth for the current job inventory.
All required checks for the current head/base must be green before merge.

## Deployment

Production uses a published image and an external PostgreSQL database.
`docker-compose.yml` supplies no database service and performs no host build.
The image starts `python -m app.runtime --port ...`; startup does not migrate.

On the deployment host, provide Docker Compose v2, Bash, `curl`, PostgreSQL
`pg_dump` compatible with the server, `gzip`, and the shell utilities used by
`scripts/deploy.sh`. Authenticate Docker to the image registry first. Create
the database and the required roles/grants, and make it reachable from both
the host (backup) and the application container. See the dev init script for
the local three-role bootstrap; do not use its development passwords in prod.

Prepare a repository-root `.env` using `.env.example` as a reference. Set
`ENVIRONMENT=prod`, all three database URLs, a real `PLATFORM_ROOT_DOMAIN`,
`TRUSTED_HOSTS`, and real `JWT_SECRET`, `SESSION_HASH_SECRET`, and `CSRF_SECRET`
values. The CSRF secret must contain at least 32 bytes and differ from the
other two. Supply an initial `APP_IMAGE` pin and the registry-qualified
`IMAGE_NAME`. Use shell-compatible assignments: the deploy script sources
this file. It overrides same-named exported variables when sourced.

```bash
./scripts/deploy.sh <published-image-tag>
```

The script verifies the image, writes a compressed `pg_dump` backup, updates
the image pin, pulls, runs `alembic upgrade heads` in a one-off container, and
recreates the app. A failed HTTP health gate restores the previous image when
one was recorded. It does not undo migrations or automatically restore a
backup, so migrations must remain compatible with the previous application.
`/health` is liveness only: success does not prove database readiness or tenant
workflows. Verify those separately after deployment.

Use the default root `.env` with the supplied Compose file. Although the script
accepts `ENV_FILE`, Compose still declares `env_file: .env`, and the script
does not pass `--env-file`; that override alone does not select the container's
configuration. An alternate setup needs its Compose configuration aligned too.

The service publishes on host loopback. Configure your TLS reverse proxy,
tenant DNS, and trusted forwarding headers separately; the supplied Compose
file does not provision DNS or certificates. `APP_PORT` must agree with the
container listener and health probe (default 8000).

## DB roles

```
app_user      — Tenant request role. RLS-enforced. Sets app.current_tenant per request.
platform_api  — Online platform routes. Explicit grants, no RLS bypass. Also
                the only role permitted to write platform-default (NULL-tenant)
                settings rows.
app_admin     — Alembic migrations and offline maintenance only. Bypasses RLS.
```

The `DATABASE_URL` env var should use `app_user`. `PLATFORM_DATABASE_URL`
should use `platform_api`. Migrations use `MIGRATION_DATABASE_URL`
connecting as `app_admin`. Settings are loaded from the environment and from
a local `.env` file through `dotmac_kernel.config.Settings`. This process
configuration differs from domain settings: those resolve from database rows,
assembly profile defaults, and spec fallbacks. A domain spec's `env_var` only
seeds an absent row at startup; changing the environment later does not rotate
or override an existing setting.

See `.env.example` for configuration, including `SEED_ON_STARTUP` (idempotent
feature seeding on boot). Set it `false` when another deployment step runs the
feature seeds. Required-setting validation still calls `seed_settings_from_env`,
so this switch does not make startup read-only. Export `ENVIRONMENT` explicitly
for the API-documentation policy, which reads the process environment directly:
dev/development/local and test/testing/ci publish documentation; production,
staging, unset, blank, and unknown values hide browser documentation and protect
`/openapi.json` with the platform bearer guard on the platform host.

## Middleware notes

- Rate limiting is process-local and LRU-bounded by `RATE_LIMIT_MAX_KEYS`.
  Keys include tenant, client IP, and route template (or a fixed hash bucket
  for unmatched paths). Separate workers/nodes do not share counters; provide
  a shared `RateLimitStore` implementation for aggregate limits. Setting
  `RATE_LIMIT_REDIS_URL` alone does not install one.
- Inbound `X-Request-ID` is ignored by default to prevent log poisoning. Set
  `TRUST_INBOUND_REQUEST_ID=true` only behind a trusted proxy that
  normalizes that header.
- Browser CSRF protection uses signed, expiring, session-bound double-submit
  tokens, supports headers and hidden form fields, and rejects explicit
  cross-site Origin/Referer and Fetch Metadata. Production requires enabled
  CSRF protection and a dedicated strong secret. See `docs/SECURITY.md`.

## License

TBD.
