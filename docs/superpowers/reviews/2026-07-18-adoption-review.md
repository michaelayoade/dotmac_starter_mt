# Adoption review (Michael, 2026-07-18) — authoritative roadmap input

Recorded verbatim from Michael's review of the starter at v0.6.1 + display-settings branch.
Spot-verified 2026-07-18 (all confirmed): `require_platform` stub (`app/core/deps.py:25`,
docstring says "stubbed here"); `_is_platform_path` allows `/platform/*` on ANY host
(`app/core/middleware/tenant.py:133` — `path.startswith("/platform/")` unconditional);
`AUTH_GUARD_NAMES` counts `require_platform` as an auth tier
(`tests/architecture/test_route_guards.py:106` region); README dev quickstart runs
superuser (`README.md:138`); rate limiter process-local by its own docstring
(`app/core/middleware/rate_limit.py:1`).

Sequencing decided from this review:
1. **control-plane-security plan** (items 1–5 below) — next execution phase, before wider adoption.
2. **capability-hardening plan** (already written) — amended, runs after control-plane work.
3. Runtime/delivery standards + starter ergonomics — subsequent plans.
The "stronger SoT rule" is folded into the spec's SOT-complete criteria (see spec amendment
2026-07-18) and the fleet knowledge base.

---

## Fix before wider adoption

### 1. Secure the platform control plane

`require_platform()` is explicitly a stub: it only checks that no tenant was resolved; it
authenticates nobody (`app/core/deps.py:25`). Worse, unknown hosts may access `/platform/*`
(`app/core/middleware/tenant.py:133`), while the architecture test incorrectly counts
`require_platform` as an authentication guard (`tests/architecture/test_route_guards.py:106`).

Before reuse:
- Require an independent platform-admin identity, ideally OIDC or tightly controlled service
  credentials.
- Permit platform routes only on the exact configured platform host.
- Provision tenant, owner, bootstrap role, and audit event atomically.
- Audit every platform mutation.
- Add deny-by-default platform permission tests.
- Bound and paginate tenant listing.

This is the most important gap.

### 2. Make RLS active during normal development

The recommended quickstart runs the app as the Postgres superuser, explicitly bypassing RLS
(`README.md:138`). That makes local behavior materially different from production.

The default dev environment should automatically create and use `app_user`, `platform_api`,
and `app_admin`. Add a dynamic Postgres catalog test that enumerates every ORM table and
verifies:
- Tenant tables have the correct `tenant_id` or tenant-derived subtype relationship.
- RLS is enabled and forced.
- The expected policy exists.
- Request roles do not have BYPASSRLS.
- Composite foreign keys prevent cross-tenant references.
- Every mapped model is visible to Alembic metadata.

This is stronger than relying on contributors to remember a new isolation canary.

### 3. Restore documentation as a trustworthy authority

ADR 0001 remains "Accepted" while describing the old Person model and nonexistent
audit-writer, Redis rate limiter, background-task, WebSocket, file-storage, and settings
designs (`docs/adr/0001-multi-tenant-architecture.md:21,279`). It even says
`domain_settings` does not exist.

Define an explicit documentation hierarchy:
- ARCHITECTURE.md: current as-built truth.
- ADRs: decisions and status — accepted, amended, superseded.
- Roadmaps/plans: non-authoritative future intent.
- README.md: onboarding derived from the current architecture.
- CONTRIBUTING.md: human development rules.
- Repo-local AGENTS.md: tool-neutral agent rules.
- CLAUDE.md and other agent adapters should point to, not duplicate, the canonical rules.

Archive implementation plans from downstream-generated projects.

### 4. Choose one transaction/session authority

The real request path uses `get_db()`, which installs RLS context and owns commit/rollback
(`app/core/db.py:46`). `get_uow()` creates another session and transaction boundary without
tenant context (`app/core/unit_of_work.py:176`).

Either remove the unused UoW or make one tenant-aware session factory serve HTTP, tasks, CLI
operations, and tests. The contract should be:
- Boundary owns commit/rollback.
- Services only mutate and flush.
- Expected conflicts use savepoints.
- No route, task, or service constructs an ad hoc session.

Unused "future" abstractions should not ship in the starter.

### 5. Finish the security baseline

The current auth is intentionally minimal. For a default production starter, add:
- Explicit registration policy: closed, invite-only, or open — never implicit.
- Remove the concurrency-sensitive "first registrant becomes admin" bootstrap.
- Password reset, verified email, lockout/backoff, MFA, session/device management, key
  rotation, and refresh-token rotation.
- Constant-work login failure handling.
- Security headers and a documented CSP.
- A distributed rate-limit backend in production; the current in-memory, raw-path-keyed
  implementation is process-local and unbounded (`app/core/middleware/rate_limit.py:1`).
- Prefer Argon2id for new password stores; OWASP currently recommends modern adaptive
  hashing and prefers Argon2id where available. (OWASP Password Storage Cheat Sheet:
  https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)

Use OWASP ASVS 5.0 Level 2 as the documented verification target
(https://owasp.org/www-project-application-security-verification-standard/).

## Next architectural layer

The existing `docs/superpowers/plans/2026-07-18-capability-hardening.md` is directionally
right. Complete it after the control-plane work:
- Permission-code RBAC rather than role-name authorization.
- Manifest-declared permission and audit-action registries.
- A single admin-role constant.
- Canonical error-code registry.
- Unified pagination/search/sort envelope.
- Computed destructive-action impact previews.
- WCAG 2.2 AA design-system contract.
- Stronger no-orphan tests using AST/runtime inspection instead of string occurrence.

Also add:
- API versioning and an OpenAPI contract snapshot/diff gate. Use an explicitly pinned
  OpenAPI specification (https://spec.openapis.org/oas/), rather than silently changing
  generated contracts.
- Standard list responses containing items, page, limit, total, and navigation metadata.
- Idempotency keys for provisioning and externally retried mutations.
- Optimistic concurrency through version fields or ETags for contested resources.
- Adopt RFC 9457 Problem Details (https://www.rfc-editor.org/rfc/rfc9457.html), or
  explicitly document and freeze the existing custom error envelope.
- Move audit emission into service-owned mutation paths so API and web routes cannot drift.
- Define lifecycle/state-transition services for tenants, users, sessions, custom fields,
  and roles.

## Runtime and delivery standards

Add these before calling the generated app production-ready:
- Lazy engine creation and database `statement_timeout`, `lock_timeout`, and
  idle-transaction timeout.
- Separate `/health` liveness and `/health/ready` dependency/migration readiness.
- Metrics and tracing following OpenTelemetry semantic conventions
  (https://opentelemetry.io/docs/specs/semconv/).
- Structured logs containing route template, actor, tenant, request ID, trace ID, and
  sanitized error class.
- Non-root Docker user, read-only filesystem compatibility, dropped capabilities, and
  no-new-privileges (`Dockerfile:32`).
- Dependency and container vulnerability scanning, secret scanning, SBOM generation,
  pinned CI actions, coverage threshold, migration compatibility tests, and Python 3.13
  CI — or restrict the package declaration to 3.12.
- Build provenance targeting SLSA Build practices (https://slsa.dev/spec/v1.2/).

## Starter-template ergonomics

Manual cloning and renaming is too fragile for an organization-wide template. Add a
bootstrap command or Copier-style generator that:
- Sets project/package/image names and initial version.
- Selects features.
- Generates secrets and environment files.
- Removes template-only plans/history.
- Creates the initial ADR and ownership ledger.
- Runs migrations and a boot smoke test.
- Verifies that disabled or deleted feature combinations still import and start.

CI should generate at least one derived application from scratch and test it. That is the
only reliable proof that this remains a starter rather than merely a functioning
application.

## The stronger SoT rule

Formalize SoT as more than "one file contains the value":

For every concept, name exactly one:
- Definition authority.
- Mutation owner.
- Read/formatting owner.
- Transaction owner.
- Authorization policy.
- Projection freshness rule.
- Drift detector.
- Idempotent repair path.
- Governance test.

Duplication is acceptable only when mechanically derived or protected by a bidirectional
coherence test. Anything with zero consumers should be deleted until a real use case
exists.

Keep billing, queues, storage, notifications, WebSockets, and product-specific workflows
optional. The starter should ship the secure contracts those features must follow — not
unused implementations of all of them.
