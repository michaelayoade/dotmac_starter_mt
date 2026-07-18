# Capability Hardening Implementation Plan (impact preview, granular RBAC, list_query, WCAG, no-orphan codes)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking. Tasks respond 1:1 to Michael's 2026-07-18 verification checklist ("Impact preview + confirm on action, granular rbac, list_query + Carbon/WCAG ui sot no orphan codes"); the verified evidence is in `docs/superpowers/phase2-backlog.md` § "Verified gaps (2026-07-18 capability sweep)".

**Goal:** Close the five verified gaps: canonical code registries with no-orphan governance; a unified list-query envelope; computed impact previews on destructive actions; permission-code RBAC; WCAG-grounded UI SoT.

**Architecture:** Extend the two proven SoT mechanisms this repo already has — the **feature manifest** (which becomes the declaration point for a feature's audit actions and permission codes, exactly as it already declares routers/nav) and the **no-orphan governance test** (settings precedent, extended to audit actions, permission codes, and error codes). List queries unify on a `ListParams`/`ListResult` pair in `app/core/query.py` ported from dotmac_erp's `finance/platform/list_helpers.py` shape. Impact preview composes ERP's `can_delete → (bool, reason)` guard idiom with a net-new htmx preview-fragment endpoint per destructive action. RBAC ports ERP's permission-code shape (`Permission`/`RolePermission` tables, namespaced codes, read/manage pairs, admin bypass) at starter scale. UI SoT is Tailwind v4 tokens + a documented WCAG 2.2 AA target — **NOT IBM Carbon** (verified absent across the fleet; adopting Carbon would be a separate migration decision for Michael).

**Tech Stack:** existing stack only (FastAPI, SQLAlchemy 2, Alembic, Jinja2/HTMX, Tailwind v4). No new dependencies.

## Global Constraints

- Branch `capability-hardening` off main **after the display-settings plan merges** (baselines then: v0.7.0, ~450 unit+arch, 43+ integration). Integration via `TEST_DB_PORT=5437` (5433/5434 are production — never touch). PR-to-green-then-merge finish; force CI with `gh workflow run ci.yml --ref capability-hardening`.
- USER RULES (verbatim standing): "everything by config, no hardcoding" — page sizes, clamps, and limits become `SettingSpec`s or documented env knobs, never per-file literals; SoT rubric — one named owner per registry (manifest for feature-declared codes, core for cross-cutting sets); template framing — every new mechanism is documented as an extension point, no fleet narrative outside ADRs.
- Every new `SettingSpec` key needs a real reader before the no-orphan-settings gate passes (allowlist stays EMPTY).
- Every new tenant-scoped table ships RLS (ENABLE/FORCE + policy) in its creating migration, with a Postgres isolation canary (CLAUDE.md hard rule).
- All existing governance suites green every commit; new governance tests must be shown RED against a deliberate violation before trusting them (sensitivity proof, same standard as the nav-coherence fix in 2b.1).
- Version: **0.8.0** (minor — additive tables/registries; `require_role("admin")` call sites migrate to `require_permission`, but `require_role` itself remains for compatibility).
- API compatibility: list endpoints keep accepting bare `limit`/`offset` (existing contract); the envelope response shape is additive per endpoint and called out in CHANGELOG when it changes a response model.

## File Structure

- Task 1: `app/core/audit.py` (action registry), `app/core/features.py` (manifest `audit_actions`), `app/core/authz.py` (NEW — `ADMIN_ROLE_SLUG`), `app/core/errors.py` (canonical code set), `tests/architecture/test_no_orphan_codes.py` (NEW).
- Task 2: `app/core/query.py` (`ListParams`/`ListResult`/`paginate`), all feature routers/services with list endpoints, `app/features/settings/spec.py` (page-size settings), `tests/unit/test_list_query.py` (NEW).
- Task 3: `app/core/impact.py` (NEW — `DeletionImpact`), `parties`/`custom_fields` services + web + templates, `tests/unit/test_impact_preview.py` (NEW), `tests/architecture/test_web_conventions.py` (destructive-control governance).
- Task 4: `app/core/models.py` (`Permission`, `RolePermission`), migration, `app/core/deps.py` (`require_permission`), `app/core/features.py` (manifest `permissions`), every feature `feature.py` + `router.py`, `app/features/rbac` (grant UI/API), `tests/test_permission_isolation.py` (NEW, Postgres).
- Task 5: `templates/base.html`, `templates/layouts/admin.html`, `templates/components/table_macros.html`, `static/css/src/main.css`, `docs/DESIGN.md` (NEW), `tests/architecture/test_web_conventions.py` (a11y governance).
- Task 6: docs + CHANGELOG + bump + final review + PR.

---

### Task 1: Canonical code registries + no-orphan-codes governance

**Files:**
- Modify: `app/core/audit.py`, `app/core/features.py`, `app/core/deps.py`, `app/features/auth/service.py`, `app/core/web_deps.py`, each `app/features/<f>/feature.py` that writes audit events (`settings`, `rbac`)
- Create: `app/core/authz.py`, `tests/architecture/test_no_orphan_codes.py`

**Interfaces:**
- Produces: `FeatureManifest.audit_actions: Sequence[str] = ()`; `app.core.audit.register_audit_actions(actions)` / `registered_audit_actions()`; `write_audit_event` raises `ValueError` on unregistered action; `ADMIN_ROLE_SLUG = "admin"` in `app/core/authz.py`; `CANONICAL_ERROR_CODES` frozenset in `app/core/errors.py`. Task 4 reuses the manifest-declaration + governance-test pattern for permission codes.

- [ ] **Step 1: Failing governance tests** — `tests/architecture/test_no_orphan_codes.py`:

```python
"""No-orphan-codes governance (extends the no-orphan-settings precedent).

Three registries: audit actions (manifest-declared), the admin role slug
(single constant), canonical error codes. Each check has two directions:
every declared code has a consumer/writer, and every literal used is
declared.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.core.errors import CANONICAL_ERROR_CODES
from app.features import FEATURE_MODULES
from app.core.features import load_manifests

_APP = Path("app")


def _source_of(feature: str) -> str:
    return "\n".join(
        p.read_text() for p in (_APP / "features" / feature).rglob("*.py")
    )


def test_every_declared_audit_action_has_a_writer_in_its_feature() -> None:
    for manifest in load_manifests(FEATURE_MODULES):
        source = _source_of(manifest.name)
        orphans = [
            a
            for a in manifest.audit_actions
            if f'"{a}"' not in source and f"'{a}'" not in source
        ]
        assert not orphans, (
            f"{manifest.name} declares audit actions with no writer: {orphans}"
        )


def test_every_write_audit_event_literal_is_declared() -> None:
    declared = {
        a for m in load_manifests(FEATURE_MODULES) for a in m.audit_actions
    }
    pattern = re.compile(
        r"write_audit_event\(\s*[^)]*?action=[\"']([^\"']+)[\"']", re.S
    )
    undeclared = []
    for path in _APP.rglob("*.py"):
        for action in pattern.findall(path.read_text()):
            if action not in declared:
                undeclared.append(f"{path}: {action}")
    assert not undeclared, f"Audit actions used but not manifest-declared: {undeclared}"


def test_admin_role_slug_has_a_single_owner() -> None:
    # The literal "admin" as a role slug may appear only in app/core/authz.py
    # (its definition). Everything else imports ADMIN_ROLE_SLUG.
    offenders = []
    for path in _APP.rglob("*.py"):
        if path.as_posix() == "app/core/authz.py":
            continue
        text = path.read_text()
        for m in re.finditer(r"require_role\(\s*[\"']admin[\"']\s*\)", text):
            offenders.append(f"{path}: {m.group(0)}")
    assert not offenders, f"Hardcoded admin role slug (use ADMIN_ROLE_SLUG): {offenders}"


def test_envelope_codes_are_canonical() -> None:
    pattern = re.compile(r"(?:envelope|_envelope)\(\s*[\"']([a-z_]+)[\"']")
    unknown = []
    for path in _APP.rglob("*.py"):
        for code in pattern.findall(path.read_text()):
            if code not in CANONICAL_ERROR_CODES:
                unknown.append(f"{path}: {code}")
    assert not unknown, f"Error codes outside CANONICAL_ERROR_CODES: {unknown}"
```

Run → RED (`CANONICAL_ERROR_CODES` missing, `audit_actions` attribute missing, hardcoded `require_role("admin")` sites everywhere).

- [ ] **Step 2: `app/core/authz.py`**

```python
"""Authorization constants: the one place role slugs are spelled.

ADMIN_ROLE_SLUG is the bootstrap role every fresh tenant gets (auth
register auto-grants it to the first user) and the portal gate's required
role until finer-grained portal roles land. Governance:
tests/architecture/test_no_orphan_codes.py::test_admin_role_slug_has_a_single_owner.
"""
ADMIN_ROLE_SLUG = "admin"
```

Replace every `require_role("admin")` with `require_role(ADMIN_ROLE_SLUG)` (all router call sites listed in the backlog entry), and the literals in `app/features/auth/service.py` (~lines 268, 271) and the portal gate in `app/core/web_deps.py`.

- [ ] **Step 3: Manifest field + audit registry** — `FeatureManifest` gains `audit_actions: Sequence[str] = ()` (same style as `nav`). In `app/core/audit.py`:

```python
_REGISTERED_ACTIONS: set[str] = set()


def register_audit_actions(actions: Sequence[str]) -> None:
    _REGISTERED_ACTIONS.update(actions)


def registered_audit_actions() -> frozenset[str]:
    return frozenset(_REGISTERED_ACTIONS)
```

`write_audit_event` gains, first line: `if action not in _REGISTERED_ACTIONS: raise ValueError(f"Unregistered audit action {action!r} — declare it in the feature's FeatureManifest.audit_actions")`. Wire registration where manifests are loaded in `app/main.py` (same place `install_surface_globals` runs): `for m in manifests: register_audit_actions(m.audit_actions)`. Declare existing actions: `settings` manifest → `("settings.update",)`; `rbac` manifest → `("role.create", "role.grant")`. Unit tests that call `write_audit_event` directly must register actions in a fixture (add an autouse registration of all manifest actions next to `_default_surface_globals` in `tests/unit/conftest.py`).

- [ ] **Step 4: `CANONICAL_ERROR_CODES`** — in `app/core/errors.py`, derived from what exists (SoT: one set, `_STATUS_SLUGS` folds in):

```python
CANONICAL_ERROR_CODES = frozenset(_STATUS_SLUGS.values()) | frozenset(
    {"internal_error", "csrf_failed", "http_error", "tenant_not_resolved"}
)
```

(Verify the actual middleware-emitted codes by grepping `envelope(`/`_envelope(` across `app/` — the set must equal reality, the test proves it.)

- [ ] **Step 5: Sensitivity proofs** — temporarily add a bogus `write_audit_event(action="bogus.action", ...)` string to a feature source and a `require_role("admin")` literal; both tests must FAIL; revert. Record output in the report.
- [ ] **Step 6: Full gates** — unit+arch green, `make check` clean.
- [ ] **Step 7: Commit** — `feat(core): canonical code registries — manifest audit actions, ADMIN_ROLE_SLUG, error-code set + no-orphan governance`

### Task 2: Unified list_query — ListParams/ListResult + settings-driven page sizes

**Files:**
- Modify: `app/core/query.py`, `app/features/{parties,rbac,custom_fields,tenants,settings}/` routers/services/web, `app/features/settings/spec.py`
- Create: `tests/unit/test_list_query.py`

**Interfaces:**
- Consumes: existing `apply_pagination`/`apply_ordering`/`escape_like`.
- Produces: `ListParams.from_query(page, limit, q, sort, *, max_limit, default_limit)`, `ListResult(items, total, page, limit)` with `total_pages/has_next/has_prev/pagination_context()`, `paginate(db, stmt, params, *, count_column=None) -> ListResult`. Settings keys `"default_page_size"`/`"max_page_size"` (domain `display`).

- [ ] **Step 1: Failing tests** — `tests/unit/test_list_query.py` covering: page/limit clamping (limit clamped to `[1, max_limit]`, page min 1); `-` prefix sort parsing → `(sort_by, "desc")`; sort key not in allowlist → `BadRequestError`; `paginate` returns correct `total` independent of page slice; `count_column` distinct-count on a joined statement; `pagination_context()` preserves `q`+`sort` in querystrings. Write the concrete tests against an in-memory model (reuse `unit_engine` fixture pattern).
- [ ] **Step 2: Implement in `app/core/query.py`** — port the ERP `list_helpers.py` shape (dataclasses, `from_query` classmethod, `paginate` doing `select(func.count()).select_from(stmt.subquery())` or `count_column` override). Wire `apply_ordering` INSIDE `paginate` when `params.sort_by` is set (this gives the dead helper its consumer; if after refactor no endpoint exposes sort, DELETE `apply_ordering` instead — a helper with no caller is an orphan; expose sort on parties + roles lists so it lives).
- [ ] **Step 3: Settings-driven page sizes** — two `SettingSpec`s in the `display` domain: `default_page_size` (integer, default 20, min 5 max 100), `max_page_size` (integer, default 100, min 10 max 500). Readers: web index routes resolve them per request instead of the `PAGE_SIZE=20`/`ROLES_PAGE_SIZE`/`AUDIT_PAGE_SIZE` constants (delete those constants). API routes keep explicit `limit=Query(...)` but clamp via `max_page_size`.
- [ ] **Step 4: Endpoint refactor** — every list endpoint returns/consumes through `paginate`: parties (`list_parties`/`search_parties`), rbac roles + audit events, custom-fields definitions (**push limit/offset into the service query — remove the in-Python `definitions[offset:offset+limit]` slice**), tenants (**`GET /tenants` gains limit/offset — closes the unbounded list**). `GET /settings/{domain}` stays spec-bounded (documented exception — the registry is small by construction; note in the module docstring). Existing `limit`/`offset` API params keep working (map to `ListParams`).
- [ ] **Step 5: Full gates + integration** (Postgres canaries unaffected but run them — service query changes touch RLS paths).
- [ ] **Step 6: Commit** — `feat(core): unified list-query envelope; settings-driven page sizes; tenants list bounded`

### Task 3: Impact preview + confirm on destructive actions

**Files:**
- Create: `app/core/impact.py`, `templates/components/_impact_confirm.html`, `tests/unit/test_impact_preview.py`
- Modify: `app/features/parties/{service,web}.py`, `app/features/custom_fields/{service,web}.py`, their templates, `tests/architecture/test_web_conventions.py`

**Interfaces:**
- Produces: `DeletionImpact(allowed: bool, reason: str | None, effects: list[ImpactLine])` with `ImpactLine(label: str, count: int)`; service methods `party_deletion_impact(db, tenant, party_id)`, `field_deactivation_impact(db, tenant, field_id)`; web routes `GET /admin/parties/{id}/delete-preview`, `GET /admin/custom-fields/{id}/deactivate-preview` returning the `_impact_confirm.html` fragment.

- [ ] **Step 1: Failing tests** — service level: party with 2 role grants → impact effects `[("Role grants", 2)]`; custom field with values on 3 parties → `[("Parties holding a value", 3)]`; impact of a missing id → `NotFoundError`. Web level: preview fragment renders counts; the destructive POST still works after preview. Governance (RED first): extend `test_web_conventions.py` — every template control targeting a route whose path ends `/delete` or `/deactivate` via `hx-post` must either carry `hx-confirm` or be inside a fragment rendered by a `*-preview` route (grep-based; exact mechanics: collect `hx-post` values matching `/(delete|deactivate)$`, require sibling `hx-confirm=` within the same tag, allowlist controls inside `_impact_confirm.html`).
- [ ] **Step 2: `app/core/impact.py`** — the two dataclasses + docstring naming this THE extension pattern: a feature computing impact owns the counting queries in its own `service.py` (thin-wrapper rule applies; core owns only the shape). ERP provenance note: guard half ports `bulk_actions.can_delete`; the preview endpoint half is starter-original.
- [ ] **Step 3: Services** — `party_deletion_impact` counts `PartyRole` rows (the DB-cascade that today deletes silently) + whether `custom_fields` JSONB is non-empty; `field_deactivation_impact` counts entities holding a value for the field code (JSONB containment query — on SQLite unit DB use the JSON1 `json_extract` path only if trivially portable; otherwise count in the service via the registered entity model's column, which works on both). `allowed` stays True for both today (no blocking rules yet) — the guard slot exists for projects.
- [ ] **Step 4: Web wiring** — delete/deactivate buttons become two-step: button `hx-get`s the preview fragment into a container (`hx-target`); the fragment shows effects list + a confirmed `hx-post` (with `hx-confirm` retained as belt-and-braces). Update the two existing `hx-confirm` sites to this pattern.
- [ ] **Step 5: Gates + sensitivity proof** (governance test catches a bare destructive control added to a template — prove by temporary mutation, record, revert).
- [ ] **Step 6: Commit** — `feat(web): computed impact preview + confirm on destructive actions (parties, custom fields)`

### Task 4: Granular RBAC — permission codes (manifest-declared, ERP shape)

**Files:**
- Modify: `app/core/models.py`, `app/core/deps.py`, `app/core/features.py`, every `app/features/<f>/feature.py` + `router.py`, `app/features/rbac/{service,router,web}.py` + templates, `app/features/auth/service.py`
- Create: `alembic/versions/<next>_permission_codes.py`, `tests/test_permission_isolation.py`, unit tests in `tests/unit/test_permissions.py`

**Interfaces:**
- Consumes: Task 1's manifest-declaration + governance pattern, `ADMIN_ROLE_SLUG`.
- Produces: `Permission` (id, code unique per tenant? — NO: global catalog table `permissions(id, code unique, label)`, platform-owned, not tenant-scoped) + `RolePermission` (tenant-scoped join `role_id → permission_id`, RLS via role's tenant); `FeatureManifest.permissions: Sequence[str] = ()` with codes namespaced `<feature>.<read|manage>`; `require_permission(code)` dependency (admin-role bypass); seeding: every manifest code upserted into `permissions` at startup (same seam as audit-action registration); `ADMIN_ROLE_SLUG` role implicitly passes all checks (bypass, not row-explosion).

- [ ] **Step 1: Design constraints (read first)** — codes: `parties.read`, `parties.manage`, `rbac.read`, `rbac.manage`, `settings.read`, `settings.manage`, `custom_fields.read`, `custom_fields.manage`, `tenants.read`, `tenants.manage` — declared by each feature's manifest, dot-namespaced to match audit actions (NOT ERP's colons; one convention per repo, dots already won via audit actions — document the deviation + ERP provenance in the ADR note). `require_permission(code)`: resolves the party's roles (existing `PartyRole` join), passes if any role is `ADMIN_ROLE_SLUG` (bypass) or holds the code via `RolePermission`. Wildcards: NOT ported (YAGNI at 10 codes; note in backlog for when a project grows codes).
- [ ] **Step 2: Failing tests** — unit: `require_permission` grants/denies by role-permission row; admin bypass; unknown code raises at guard-declaration time (fail loud at import, not per-request — check against the registered manifest codes). Governance (extend `test_no_orphan_codes.py`, same two directions as audit actions): every manifest-declared permission code appears in a `require_permission("...")` literal within its feature; every `require_permission` literal is manifest-declared. Postgres canary `tests/test_permission_isolation.py`: tenant A's role grant of `parties.manage` invisible to tenant B (RLS on `role_permissions` via EXISTS-join through `roles`, subtype-table pattern from `party_persons`).
- [ ] **Step 3: Models + migration** — `permissions` (no tenant_id — platform catalog, read-only to tenants; NO RLS needed but document why: rows are code metadata, not tenant data) and `role_permissions` (RLS EXISTS-join through `roles.tenant_id`, FORCE, policy in same migration). Seed permissions from manifests in the startup seed seam (idempotent `ensure_`-style upsert on a dedicated platform session — the `ensure_by_key` precedent; its docstring warning applies verbatim).
- [ ] **Step 4: Guards** — `require_permission` in `app/core/deps.py` reusing `authenticate_request`'s output; convert every feature router's `require_role(ADMIN_ROLE_SLUG)` to the feature's `.read` (GET) / `.manage` (mutating) code. The PORTAL gate (`require_web_auth`) keeps requiring `ADMIN_ROLE_SLUG` — portal role loosening remains phase 3 (this task builds the layer it needs; do not deliver the loosening here).
- [ ] **Step 5: RBAC admin surface** — grant/revoke permission-to-role: service + `PUT /rbac/roles/{id}/permissions` (audit action `role.permissions.update` — declare it in the rbac manifest) + roles web page gains a permissions checklist. Auth register unchanged (first user gets `ADMIN_ROLE_SLUG`, which bypasses).
- [ ] **Step 6: Full gates + integration + sensitivity proofs** (both governance directions, temporary-mutation proof each).
- [ ] **Step 7: Commit** — `feat(rbac)!: permission-code authorization — manifest-declared codes, role_permissions, require_permission` (the `!` is for the router guard semantics change: non-admin roles can now be granted API access — CHANGELOG explains).

### Task 5: WCAG 2.2 AA pass + design-token SoT doc (not Carbon)

**Files:**
- Modify: `templates/base.html`, `templates/layouts/admin.html`, `templates/components/table_macros.html`, `static/css/src/main.css`, `tests/architecture/test_web_conventions.py`
- Create: `docs/DESIGN.md`

**Interfaces:** Produces `docs/DESIGN.md` as the named UI SoT (tokens + conformance target + component conventions); a11y governance tests.

- [ ] **Step 1: Failing governance tests** — extend `test_web_conventions.py`: (a) every `<button>`/`<a>` whose visible content is only an SVG (regex: tag body contains `<svg` and no non-tag text) must carry `aria-label=`; (b) `templates/base.html` must contain a skip link (`href="#main-content"`) and the admin layout a `<main id="main-content"`. RED against current templates (the icon-only delete button at `table_macros.html:130-137` is the known offender for (a)).
- [ ] **Step 2: Template fixes** — skip link as first body child in `base.html` (`sr-only focus:not-sr-only` classes); `id="main-content"` + `tabindex="-1"` on `<main>`; `aria-label="Delete {{ name }}"` on the icon-only delete button (keep `title`); sweep the other 8 `role=` files for icon-only controls the new test flags.
- [ ] **Step 3: CSS** — in `main.css`: `@utility` (or plain classes) for `sr-only`/`focus:not-sr-only` if Tailwind v4 preflight doesn't already ship them (verify in compiled CSS — grep-proof, the Tailwind-v4-config-inertness lesson applies: verify in OUTPUT, not config); global `:focus-visible` ring style consistent with the primary token (`--color-primary-600`), `@media (prefers-reduced-motion: reduce)` disabling the three `--animate-*` tokens.
- [ ] **Step 4: `docs/DESIGN.md`** — the UI SoT: token inventory (from `@theme` — fonts, primary/accent scales, animations) with "tokens are the only color/font source — no raw hex in templates" rule; WCAG 2.2 AA as the stated conformance target with the checklist (focus-visible, skip link, labels, aria-live toasts, reduced-motion, contrast ≥4.5:1 for text tokens — verify primary-600-on-white and note the measured ratios); explicit "Carbon not used" note pointing fleet-wide verification (backlog entry) so future contributors don't import a second design system. CLAUDE.md gets a two-line pointer.
- [ ] **Step 5: Gates + visual smoke** — `make css-build` then grep compiled CSS for the focus-visible rule + sr-only; unit+arch green.
- [ ] **Step 6: Commit** — `feat(web): WCAG 2.2 AA pass — skip link, focus-visible, icon-button labels; docs/DESIGN.md as UI SoT`

### Task 6: Docs + v0.8.0 + final review + PR merge

- [ ] **Step 1:** CLAUDE.md (new hard rules one line each + governance test names: audit actions manifest-declared; ADMIN_ROLE_SLUG single owner; permission codes; destructive-control confirm/preview; icon-button aria-label), ARCHITECTURE.md (registries section; permission model + why `permissions` is a platform catalog; impact-preview pattern; list-query envelope; ownership table rows), CHANGELOG 0.8.0 (call out the router guard semantics change + any list response model changes), backlog reconciliation (strike the five delivered gap entries with resolution notes; keep wildcards + portal-loosening as tracked lines).
- [ ] **Step 2:** `make bump-version part=minor` → 0.8.0; full gates + docker smoke.
- [ ] **Step 3:** Final whole-branch review (most capable model) scoped to: the five checklist items each genuinely closed with sensitivity-proven governance; SoT rubric (one owner per registry, no parallel lists); RLS correctness of `role_permissions`; no hardcoded page sizes/roles/codes remain (grep sweep).
- [ ] **Step 4:** PR → force CI → green → merge → sync main → ledger + knowledge-server memory update.

## Completion criteria

- Each checklist item has: the mechanism, at least one real consumer, and a sensitivity-proven governance test that catches regression (audit-action orphans both directions, permission-code orphans both directions, admin-slug single owner, canonical error codes, destructive-control confirm/preview, icon-only aria-label, unbounded-list prevention via the paginate refactor).
- A project built from the template can: grant a non-admin role real API access via permission codes; see computed impact before destroying data; page/search/sort any list through one envelope; meet WCAG 2.2 AA on the shipped components.
- Suites green; PR merged; v0.8.0.
