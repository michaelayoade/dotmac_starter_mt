# Kernel-restatement sweep (adoption reconnaissance)

**As of:** 2026-08-10
**Sub:** `origin/dev` after the settings cutover
**ERP:** `origin/main` local checkout

A restatement is a fact `dotmac_kernel` owns, expressed a second time inside a
product. It is not duplication of code — the implementations differ, which is
what makes it survive review — it is duplication of a DECISION, and it drifts
in exactly one direction: the kernel changes and the product's copy silently
stops agreeing.

This inventory is the adoption mirror of `module-extraction-sources.md`. That
document asks *which product code should become shared*. This one asks *which
product code already answers a question the shared code answers*, and it is
meant to be run BEFORE a cutover rather than discovered during one.

## Why it exists

Sub's settings cutover surfaced seven CI failures across eight push cycles.
They presented as unrelated — a duplicate spec, a type error, a schema
validation error, two mocked-session failures, two query budgets — and were one
root cause seven times:

| restated fact | how it surfaced |
|---|---|
| the value-type vocabulary (native `settingvaluetype` enum) | a kernel-declared type could not be stored at all |
| `ValueTypeSpec.storage` (a CHECK naming `value_type = 'json'`) | blocked the column conversion; PostgreSQL gate only |
| `ValueTypeSpec.storage` AGAIN, in `DomainSettingUpdate`/`Create` | array settings unwritable through the API |
| the settings cache key model | no scope segment — the cross-tenant leak the kernel docstring names ERP for |
| invalidation policy | ten call sites in six modules, no owner |
| the resolver's session API (`db.query` in test doubles) | a `MagicMock` returned a mock rather than failing |
| "which duplicate spec wins" (first-match vs the kernel's dict) | two settings declared twice, live for weeks |

Every one of those was findable by a sweep before the first push. None was
found by reading call sites, which is what the pre-cutover recon did.

## The sweep

For each fact the kernel owns, ask: *where does the product answer this
itself?* The four that matter for settings adoption:

1. **A vocabulary** — a native enum or CHECK enumerating value types, setting
   domains, scopes, or secret sources.
2. **A storage decision** — anything comparing a value type to a literal name
   to choose a column, a widget, or a coercion.
3. **A key or cache policy** — a settings cache key format, TTL, or
   invalidation rule.
4. **A data-access shape** — test doubles or fakes built for the product's own
   session calls, which pass vacuously once the kernel's differ.

## `dotmac_sub` — after the cutover

| fact | state |
|---|---|
| value-type vocabulary | **closed** — migration `512` removed the native enum; the kernel registry is the authority, enforced at the model write boundary |
| storage decision | **one expression** — `app/schemas/settings.py::_stores_as_json`, which asks `ValueTypeSpec.storage`. Guarded by `tests/architecture/test_kernel_owned_facts_have_one_expression.py` |
| coercion / rendering by type name | **open, bounded** — 3 sites in 2 modules, recorded as a shrink-only baseline in that same test. `ValueTypeSpec.from_storage`/`to_storage` own this; Sub has not moved coercion yet |
| cache key + invalidation | **open** — key is `settings:{domain}:{key}` with NO scope segment, and ten invalidation call sites across six modules. Owned by the settings-cache slice |
| data-access shape in fakes | **partially closed** — three scheduler tests and one `_FakeSession` fixed; any test that mocks a session by shape remains exposed |

## `dotmac_erp` — before adoption

ERP has **not** adopted the kernel (zero dependency, no import-linter). Its
settings subsystem is the nearest to a cutover, so it is swept first.

| fact | state | evidence |
|---|---|---|
| setting-domain vocabulary | **already repaired** — open `str` subclass with `SettingDomainType`, native enum retired | `app/models/domain_settings.py:44,160` |
| settings cache key | **already repaired** — carries an organization scope segment, with the platform-wide scope named separately | `app/services/settings_cache.py:317` |
| **value-type vocabulary** | **open** — still a closed `enum.Enum` | `app/models/domain_settings.py:37` |
| **storage decision** | **open** — `CHECK (value_type = 'json' AND value_text IS NULL)` | `app/models/domain_settings.py:207` |
| **branch on a type name** | **open — 29 sites across 9 modules** | see below |
| **resolution chain** | **open** — ERP's own `resolve_value` with an `organization_id` scope parameter and 116 specs | `app/services/settings_spec.py:1124` |

Branch-on-value-type sites, by module:

| module | sites |
|---|---|
| `app/services/settings_spec.py` | 7 |
| `app/services/people/hr/employee_filter_contract.py` | 6 |
| `app/services/people/hr/employee_filter_engine.py` | 5 |
| `app/services/domain_settings.py` | 4 |
| `app/services/admin/web.py` | 3 |
| `app/services/settings_api.py` | 1 |
| `app/services/pm/sla_service.py` | 1 |
| `app/services/admin/web/organization_settings.py` | 1 |
| `app/services/admin/web/common.py` | 1 |

The two HR modules are the interesting entry: eleven of the twenty-nine sites
are in employee filtering, which is not a settings surface at all. `SettingValueType`
has been borrowed as a general "what kind of value is this" vocabulary. That is
a distinct decision from settings storage and should NOT follow the settings
cutover into the kernel registry — it needs its own type, or the filter engine
needs its own vocabulary. Sweeping first is what makes that visible; discovering
it mid-cutover would have looked like a blocker.

ERP's broader adoption surface — nine parallel implementations of kernel
concerns including a second, incompatible licensing scheme — is in
`erp-vendor-surfaces.md` § 8 and is not repeated here.

## `dotmac_vendor_control_plane`

Swept and clean: it restates none of the four. VCP was built FROM the kernel
and consumes `db`, `features`, `licensing`, `messaging`, `platform_auth`,
`providers.provisioning`, `entitlements`. Its only adoption problem is a stale
pin (`0.1.0a9` against `0.1.0a28`), which is a different class of debt.

It is worth stating as the counter-example: a product that never restated a
kernel fact has no restatement backlog, and its cutovers are version bumps.
