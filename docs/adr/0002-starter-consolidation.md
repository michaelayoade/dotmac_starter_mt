# ADR 0002 — Starter Consolidation

**Status:** Accepted (amended 2026-07-18 — archive decision reversed)
**Date:** 2026-07-17
**Supersedes:** N/A (extends ADR 0001, does not replace it)
**Successor of:** ADR 0001 — Multi-Tenant Architecture

## Context

Three related repos existed with overlapping purpose:

- `dotmac_starter` — a single-tenant FastAPI starter with a fuller feature
  set (auth hardening, billing, notifications, web UI) but no tenancy.
- `dotmac_starter_mt` (this repo) — the multi-tenant foundation from ADR
  0001: RLS, three DB roles, tenant resolver, cross-tenant isolation
  canaries — but a minimal feature set.
- `dotmac_sub` — the org's largest product, single-tenant, carrying ~848
  service files of ISP-domain logic (RADIUS, PostGIS, OLT/GenieACS) but also
  the org's most battle-tested infrastructure and engineering discipline
  (CI matrix, import-linter contracts, architecture governance tests,
  deploy.sh with backup/migrate/health-gate/rollback, structured errors,
  observability middleware).

Maintaining three starters meant infra fixes, security patches, and pattern
improvements had to be ported by hand across repos, or drifted. The full
design is recorded in
`docs/superpowers/specs/2026-07-17-starter-consolidation-design.md`.

## Decision

Consolidate into **one repo: `dotmac_starter_mt`**, multi-tenant always. A
single-tenant deployment is simply a deployment with one tenant row — not a
different code path. `dotmac_starter` is retired (archived with a pointer
README) once phase 2 (auth/RBAC/audit feature parity) lands.

Three sources of truth feed the consolidation, per the spec:

| Source | Contributes |
|---|---|
| `dotmac_starter_mt` | The tenancy foundation (already built, kept as-is): RLS, three DB roles, tenant resolver, isolation canaries. |
| `dotmac_sub` | Infrastructure & engineering discipline, ported near-verbatim: CRUD/UoW/query helpers, structured logging, observability middleware, error envelope, import-linter + architecture-test governance, CI matrix, Dockerfile + immutable prod compose + `deploy.sh`, versioning. |
| `dotmac_starter` | Features, rewritten tenant-scoped as modular feature packages — not copied line-for-line. |

Features are organized as self-contained packages under `app/features/<name>/`
(models/schemas/service/router + a `feature.py` manifest), registered in a
central `FEATURE_MODULES` list and mounted through `app.core.features`.
Import-linter contracts enforce that features never import each other and
that core never imports features.

### Deviation: six models live in core, not "one model per feature package"

The spec's stated aim (§ "Docs & agent conventions") was a `CLAUDE.md` that
codifies "the layered layout, feature-package rules... tenancy invariants,"
implicitly assuming each feature package fully owns its models. Execution
surfaced a structural conflict: `app.core.deps` (the `require_tenant`,
`require_user_auth`, `require_role` guards used by every protected route)
and `app.core.middleware.tenant` (the resolver that runs before any feature
code) both need to query tenancy/identity data directly — and core is
forbidden from importing features (that's the whole point of the
import-linter boundary; if core imported features, the independence
contract between features would be meaningless, since core is on every
request path).

The resolution: `Tenant`, `TenantDomain`, `Person`, `Role`, `PersonRole`, and
`AuthSession` live in `app/core/models.py`, and `AuditEvent` (written from
every feature) lives in `app/core/audit.py`. This is a deliberate deviation
from "models per feature package" — the rule that actually governs
placement is **models queried by core (deps/middleware) live in core;
feature-local models live in the feature**. Everything not needed outside
its own feature stays local: e.g. `UserCredential` (auth's password hash
storage) lives in `app/features/auth/models.py` because only
`app.features.auth.service` touches it, referencing `people`/`tenants` by
string-form FK, no import required. This rule is codified in `CLAUDE.md`.

## Consequences

- `dotmac_starter` stays frozen (no new features) until this repo reaches
  feature parity through phase 2 (auth hardening: MFA/TOTP, refresh
  rotation, lockout, API keys; settings-as-data; branding), then is
  archived with a README pointing here.
- `dotmac_sub` is unaffected — it remains the org's production ISP platform
  and continues to serve as the infra pattern source for future ports, but
  its domain logic (RADIUS/PostGIS/OLT) is explicitly out of scope for this
  starter (see the spec's Non-goals).
- New apps built from this template start multi-tenant by default; a
  single-tenant product provisions exactly one tenant and never touches the
  multi-tenant code paths beyond that.
- Six models are exceptions to "each feature owns its models" — any new
  cross-cutting need (core code needing to query a new field/table) should
  default to *not* adding another core model; first check whether the need
  can be met via the existing core models, a UUID/FK reference, or a
  core-level interface, before growing the core model surface further.

## References

- `docs/superpowers/specs/2026-07-17-starter-consolidation-design.md` — full design
- `docs/adr/0001-multi-tenant-architecture.md` — founding tenancy decision (unchanged)
- `CLAUDE.md` — layout and hard-rules summary for agents
- `docs/ARCHITECTURE.md` — expanded architecture reference

## Amendment 2026-07-18 — dotmac_starter is NOT archived

Michael (2026-07-18): "we should not retire the dotmac_starter single tenant. this can be
saas starter infrastructure while that can still be useful for simple apps."

Revised positioning:
- **dotmac_starter_mt** — the SaaS starter: multi-tenant infrastructure, RLS, platform
  control plane, module/plugin architecture (see
  `docs/superpowers/reviews/2026-07-18-module-control-plane-directive.md`).
- **dotmac_starter** — remains available as the simple single-tenant starter for simple
  apps. Not archived, not retired. It stays feature-frozen relative to this repo (new
  capability work lands here); maintenance scope for it (backports, security fixes) is
  decided case-by-case.

The "archive after core parity / after 2c" milestones elsewhere in this repo's docs are
void; superseded by this amendment.
