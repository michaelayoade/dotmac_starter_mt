# Kernel-restatement sweep (adoption reconnaissance)

**As of:** 2026-08-10 — RE-DERIVED by parsing. The first version was grepped
and its numbers were wrong; see "How these numbers are produced".
**Sub:** `feat/settings-cache-through-kernel`, after the cutover and cache slice
**ERP:** `origin/main` local checkout
**Tool:** `scripts/restatement_sweep.py`

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

## How these numbers are produced

`scripts/restatement_sweep.py`, by parsing. The first version of this document
was grepped, and every number in it was wrong in one direction or the other:

- it reported 29 branch-on-value-type sites in ERP. There are **42**;
- it reported eleven of them in HR employee filtering, and concluded
  `SettingValueType` had been borrowed as a general value vocabulary. **There
  are two, and neither is a setting.** HR's `value_type` is
  `Literal["uuid","date","string","enum","bool"]`, an unrelated filter-field
  type sharing an attribute name;
- it could not see setting specs declared through a
  `build_*_specs(setting_spec)` callable at all, which is how a duplicate spec
  was registered in Sub and broke every test at import.

Over-reporting is the same failure. The first parsing run counted 139 "local
cache keys" in Sub, nearly all permission codes like `"settings:manage"`, and
counted one `query().filter().filter()` chain as three reads. Both detectors
were tightened and re-checked against what they matched.

Run it as `python scripts/restatement_sweep.py <repo>/app <label>`.

## `dotmac_erp` — before adoption

ERP has **not** adopted the kernel (zero dependency, no import-linter). Numbers
as of 2026-08-10, parsed.

| fact | state | evidence |
|---|---|---|
| setting-domain vocabulary | **already repaired** — open `str` subclass, native enum retired | `app/models/domain_settings.py:44` |
| settings cache key | **already repaired** — carries an organization scope segment | `app/services/settings_cache.py:317` |
| **value-type vocabulary** | **open** — still a closed `enum.Enum` | `app/models/domain_settings.py:37` |
| **storage decision** | **open** — `CHECK (value_type = 'json' AND value_text IS NULL)` | `app/models/domain_settings.py:207` |
| **branch on a value type** | **open — 42 sites in 10 modules** | below |
| **parallel readers** | **open — 77 statements in 18 modules** query the settings table outside the modules that own it | below |
| **locally built cache keys** | **1 outside its own cache module** (`module_settings_web.py`) | — |
| resolution chain | ERP's own `resolve_value` with an `organization_id` scope, 115 specs in one file | `app/services/settings_spec.py:1124` |

Branch-on-value-type, by module: `module_settings_web` 10, `admin/web` 9,
`admin/web/organization_settings` 7, `settings_spec` 7, `domain_settings` 4,
then five modules with one each.

Parallel readers, by module: `admin/web/organization_settings` 10, `admin/web`
10, `feature_flag_service` 8, `module_settings_web` 6, `people/hr/invite_attachment`
6, `settings_cache` 5, then twelve more.

`module_settings_web.py` is the module to look at first: highest on both lists,
and the shape of Sub's `provisioning_settings` — its own reads, its own
branching, its own key.

## `dotmac_sub` — after the cutover and the cache slice

| fact | state |
|---|---|
| value-type vocabulary | **closed** — migration `512`; the kernel registry is the authority, enforced at the model write boundary |
| storage decision | **one expression** — `app/schemas/settings.py::_stores_as_json`, guarded by `tests/architecture/test_kernel_owned_facts_have_one_expression.py` |
| settings cache | **closed** — the kernel owns key, scope, TTL and invalidation; Sub supplies a `CacheStore`. **0 locally built cache keys**, against ERP's 1 and Sub's own prior 3 modules |
| invalidation | **one owner** — a `before_flush`/`after_commit` pair on the model, replacing ten call sites in six modules |
| branch on a value type | **open — 21 sites in 8 modules**, 11 of them in `settings_spec` itself; 3 recorded as a shrink-only baseline |
| **parallel readers** | **open — 97 statements in 31 modules.** Three were closed (`smart_defaults`, `module_manager`, `provisioning_settings`) and that is 3 of 34. This is the largest remaining restatement in either product and was not visible until the sweep parsed. |

The parallel-reader number is the one to carry forward. It is larger in Sub
than in ERP despite Sub being the product that adopted, because a cutover moves
the RESOLVER and leaves every module that never used it.

## `dotmac_vendor_control_plane`

Swept and clean: it restates none of the four. VCP was built FROM the kernel
and consumes `db`, `features`, `licensing`, `messaging`, `platform_auth`,
`providers.provisioning`, `entitlements`. Its only adoption problem is a stale
pin (`0.1.0a9` against `0.1.0a28`), which is a different class of debt.

It is worth stating as the counter-example: a product that never restated a
kernel fact has no restatement backlog, and its cutovers are version bumps.
