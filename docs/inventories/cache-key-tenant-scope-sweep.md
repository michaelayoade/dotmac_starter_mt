# Cache-key tenant-scope sweep — dotmac_erp / dotmac_sub / dotmac_crm (+ dotmac_starter_mt kernel)

**Read-only audit. No file in any repo was modified.**
Audit date: 2026-08-03.

Repo states audited (working trees as found):

| Repo | Branch at audit | Note |
|---|---|---|
| `dotmac_erp` | `fix/self-service-upload-form-rows-serialization` (`cab1c98d`) | the confirmed defect is **still live** here and on `origin/main` — see §1.1 |
| `dotmac_sub` | (as checked out) | single-operator; scoping boundaries are reseller / subscriber / user |
| `dotmac_crm` | (as checked out) | declared single-tenant; boundaries are customer / reseller / vendor / agent |
| `dotmac_starter_mt` | `main` (`1df5f4a`) | multi-tenant by construction (`tenant_id` + RLS) |

## The rule being tested

A correct tenant-scoped query behind a tenant-blind cache is a cross-tenant leak. Four
properties make a cache safe:

1. **Required scope argument** — not optional-defaulting-to-None.
2. **Scope in the cache key.**
3. **A separately *named* global/platform read path** — so an unscoped read can only happen on purpose.
4. **Scope-correct invalidation** — a write must not serve stale data to its own tenant, nor clobber another's entry.

## Coverage

| Repo | Distinct cache constructs examined | Confirmed cross-tenant/scope data leaks | Adjacent / latent findings |
|---|---|---|---|
| `dotmac_erp` | ~70 | **2** | 5 |
| `dotmac_sub` | ~90 | 0 | 4 |
| `dotmac_crm` | ~75 | 0 | 4 |
| `dotmac_starter_mt` (assembly + kernel) | 6 | 0 | 3 (all forward-looking) |

Categories swept in every repo: Redis clients and wrappers; `functools.lru_cache` /
`functools.cache` / `cached_property` / custom memo decorators; module-level dicts and
globals; request-scoped (`request.state`, `ContextVar`, `Session.info`) memoization;
per-instance service caches; ORM/query-level caches; DB-table ("materialized") caches;
idempotency stores; HTTP response caching (`Cache-Control`/`ETag`/`Last-Modified`/`Vary`,
Python + templates + nginx); Jinja bytecode / fragment caches; rate-limit stores;
disk/file caches; client-side JS caches.

---

# 1. dotmac_erp — the multi-tenant repo, and the only one with confirmed leaks

ERP is genuinely multi-tenant (`organization_id` on ~1016 files' worth of models; RLS on a
subset of tables; an ORM-level org filter listener at `app/db/org_listener.py:91-100` that
injects `cls.organization_id == session.info["organization_id"]` and raises
`MissingOrgContextError` when unset). Redis is configured in production
(`docker-compose.yml:18,74,106,140`) and shared by all gunicorn workers
(`gunicorn.conf.py:23`, `cpu_count()*2+1`) plus Celery workers — so a process-wide cache
entry is in practice a **deployment-wide** entry.

## 1.1 LEAK #1 — `app/services/settings_cache.py` (the confirmed instance; still live)

**File:line:** `/Users/michaelayoade/Downloads/management/dotmac_erp/app/services/settings_cache.py:262`
(key builder), `:310` (`get_setting_value`), `:359` (`get_domain_settings`), `:400`
(`invalidate_setting`), `:422` (`invalidate_domain`).

**What it caches:** resolved `DomainSetting` values, in a process-global `InMemoryCache`
(`:225 _inmemory_cache = InMemoryCache()`) **and** in shared Redis via `cache_service`.

**Is the data tenant-scoped:** yes. `app/models/domain_settings.py:96` —
`organization_id: Mapped[uuid.UUID | None]`, unique on `("domain","key","organization_id")`
(`:80`), with the inline comment `"NULL = global setting, UUID = org-specific setting"`
(`:100`). `domain_settings` is **not** under RLS (grep over `alembic/` for
`ENABLE ROW LEVEL SECURITY` returns six tables; `domain_settings` is not among them) — the
only scoping is the ORM org-filter listener.

**Exact key construction** (`:262-266`, verbatim):

```python
if key:
    return f"settings:{domain.value}:{key}"
return f"settings:{domain.value}:_all"
```

No organization component.

**Verdict: LEAK.** Evidence:

- The read function's signature is `get_setting_value(self, db, domain, key, default=None)` —
  it takes **no organization argument at all**, neither required nor optional.
- The DB query (`:340-346`) filters only `domain`/`key`/`is_active`. The org predicate is
  injected implicitly by the ORM listener, so the *value fetched* is org-specific while the
  *cache key* is not.
- Therefore the first organization to read a setting populates
  `settings:<domain>:<key>`, and every other organization on that worker (and, via Redis,
  in the whole deployment) is served that value for the domain's TTL (`:33-69`: 60s for
  `features`/`auth`, 300s default, 600s for `audit`/`payments`).
- Invalidation shares the same untenanted key (`:400`, `:422`), so a write by org A also
  purges org B's entry, and no per-org invalidation is possible.

**Concrete cross-tenant consequences**, in descending severity, each traced to a real call site:

| Setting | Read at | What crosses, to whom |
|---|---|---|
| `payroll.payroll_rounding_account_id` | `app/services/people/integrations/payroll_gl_adapter.py:553` | Org A's **GL account UUID** is used to balance Org B's payroll journal. A foreign-org account id posted into another org's ledger — financial data corruption, not just disclosure. |
| `inventory.inventory_default_warehouse_id` | `app/services/finance/ar/sales_order.py:917` | Org A's **warehouse UUID** is reserved against Org B's confirmed sales order. |
| `payroll.auto_post_gl_on_approval` | `app/services/people/payroll/payroll_service.py:1048` | Org A's automation policy governs whether Org B's payroll auto-posts to the books on approval — a control decision taken with another tenant's configuration. |
| `procurement.procurement_threshold_{direct,selective,ministerial}_max` | `app/services/procurement/thresholds.py:46,52,58` | Org A's **procurement approval limits** become Org B's. Wrong approval authority; purchases pass or fail thresholds they should not. Note `_get_thresholds(db=None, organization_id=None)` accepts an org and then **never passes it** into `get_cached_setting` — the call site is itself the property-1 violation. |
| `email.email_header_html` / `email_footer_html` / `email_header_text` / `email_footer_text` | `app/services/people/payroll/payslip_email.py:28-31` | Org A's letterhead/footer is rendered into **payslip emails sent to Org B's employees** — disclosure to parties outside both tenants. |
| `email.email_logo_url`, `reporting.report_logo_url` | `app/services/admin/settings_web.py:273,276` | Org A's logo on Org B's emails and reports. |
| `inventory.inventory_valuation_mode`, inventory reservation flags | `app/services/inventory/transaction.py:1233`, `stock_reservation.py:74,83`, `reorder.py:102` | Org A's valuation/reservation policy applied to Org B's stock movements. |

**Sequence that triggers it:** any request by Org A that reads one of the above settings →
entry written under `settings:<domain>:<key>` → within the TTL, any request by Org B that
reads the same setting is served Org A's value. No special ordering, no race; the first
reader wins for every reader.

`get_domain_settings` (`:359`, the bulk path) has the identical defect but **has no callers**
in `app/` (grep-verified) — latent, not currently exercised.

**Fix status — important:** the fix exists on the **unmerged** branch
`security/settings-cache-org-scope`, commit `a88c05d5` *"fix(security): scope the settings
cache to one organization"* (14 files, +894/−97, incl. `tests/services/test_settings_cache_org_scope.py`
with 18 tests, 14 of which fail against the pre-fix logic). It is **not an ancestor of
`origin/main` and not an ancestor of the audited `HEAD`** (verified with
`git merge-base --is-ancestor`). The leak is live in the repo as it stands. The fixed shape
is the reference for everything in §5:

```python
# a88c05d5, settings_cache.py:293-297, :321-338
def _scope_segment(organization_id):        # named platform scope
    if organization_id is None:
        return "platform"
    return f"org={organization_id}"

def _require_organization(organization_id): # required scope, raises rather than degrading
    if organization_id is None:
        raise ValueError("settings_cache requires an explicit organization_id. Use the ...")
```

producing `settings:<domain>:k=<key>:org=<uuid>` vs `settings:<domain>:k=<key>:platform` —
structurally different segment forms, so no tenant identifier can collide with the platform
scope, and the `k=` prefix stops a setting literally named `all` colliding with the
bulk-domain entry. Reading the platform row is a separately named path
(`get_global_setting_value` / `get_global_domain_settings` / `get_global_cached_setting`).
All four properties satisfied.

## 1.2 LEAK #2 — `app/main.py:596 _load_audit_settings` (same class, security-control consequence)

**File:line:** `/Users/michaelayoade/Downloads/management/dotmac_erp/app/main.py:596`
(function), `:221-224` (`_AUDIT_SETTINGS_CACHE`, `_AUDIT_SETTINGS_CACHE_AT`,
`_AUDIT_SETTINGS_CACHE_TTL_SECONDS = 30.0`, `_AUDIT_SETTINGS_LOCK`).

**What it caches:** the audit-middleware configuration — `enabled`, `methods`, `skip_paths`,
`read_trigger_header`, `read_trigger_query`.

**Is the data tenant-scoped:** yes — these live in `domain_settings` under
`SettingDomain.audit`, the same `organization_id`-scoped table as §1.1, and are writable per
organization through `app/services/settings_api.py:223 _upsert_domain_setting(db, SettingDomain.audit, key, payload)`.

**Exact key construction:** **there is no key.** It is a bare module global:

```python
def _load_audit_settings(db: Session):
    global _AUDIT_SETTINGS_CACHE, _AUDIT_SETTINGS_CACHE_AT
    ...
    if not db.info.get("organization_id") and settings.default_organization_id:
        prime_tenant_context(db, UUID(settings.default_organization_id))
    with allow_cross_org(db):
        rows = list(db.scalars(
            select(DomainSetting)
            .where(DomainSetting.domain == SettingDomain.audit)
            .where(DomainSetting.is_active.is_(True))
        ).all())
    ...
    values = {row.key: row for row in rows}
```

**Verdict: LEAK.** Evidence — this is worse than a missing key component, because the query
*explicitly opts out* of scoping:

- `with allow_cross_org(db)` disables the ORM org filter entirely, so the `select` returns
  the `audit` rows of **every** organization. Priming the default org beforehand has no
  effect on the query inside the block.
- `values = {row.key: row for row in rows}` is a dict comprehension over an unordered result
  set — for a given key such as `enabled`, whichever org's row appears last wins, arbitrarily.
- The winner is then stored in one process-global slot and returned to every request on that
  worker for 30 seconds, with no invalidation path other than TTL expiry.

**Concrete cross-tenant consequence:** if **any** organization sets `audit.enabled` to false,
or adds a broad prefix to `audit.skip_paths`, the audit middleware may stop recording
mutations **for every organization on that worker** for up to 30s at a time, refreshing
into the same arbitrary choice. The crossing artefact is an audit-suppression policy, and
the affected party is every other tenant's audit trail — a compliance/forensics control,
silently governed by another tenant's setting. Same defect shape as §1.1, different blast
surface.

## 1.3 dotmac_erp — cross-tenant *availability* (not disclosure)

**`app/middleware/rate_limit.py:290-294` and `:372-373`** — verbatim:

```python
return f"{get_client_ip(request)}:{request.url.path}"
```

and the Redis variant `:178 redis_key = f"ratelimit:{key}"`. **No `organization_id`.**
Verdict: not a data leak, but a genuine cross-tenant defect — two tenants' users behind one
corporate NAT or one proxy egress IP share a single bucket on the same path, so tenant A's
traffic exhausts tenant B's allowance. Contrast with the starter kernel, which already does
this correctly (§4.5).

## 1.4 dotmac_erp — verified SAFE (the ones worth stating, because they look risky)

- **`app/services/cache.py:312-337 CacheKeys`** — every builder takes `org_id` as a
  **required positional** and emits `f"org:{org_id}:…"`. `invalidate_org` /
  `invalidate_dashboard` (`:286`, `:298`) likewise. **SAFE**, and this is ERP's own correct
  in-house convention — `settings_cache` simply never adopted it.
- **`app/services/finance/platform/org_context.py:35,92,147`** (currency settings, two-tier
  request-cache + Redis) — `organization_id` required on all three read paths, key
  `f"org:{org_id}:currency"` in both tiers. **SAFE.**
- **`app/web/deps.py:179` + `app/services/finance/branding.py:517`** (branding CSS) —
  `CacheKeys.org_branding_css(org_id)`, invalidated on every branding create/update/delete.
  **SAFE**, all four properties.
- **`app/services/auth_dependencies.py:43,48`** — `f"session:{session_id}:valid"` /
  `":revoked"`. `session_id` is a globally unique UUID and the cached payload is
  cross-checked against `person_id` before use (`:107`); a hit still re-queries the DB
  (`:122`). **SAFE.**
- **`app/api/idempotency.py:44` + `app/services/finance/platform/idempotency.py:41`** —
  `organization_id` is a **required keyword** and appears as an explicit `WHERE` predicate
  (`idempotency.py:69-75`). **SAFE** (an org-blind idempotency store would replay tenant A's
  response body to tenant B).
- **`app/services/coach/insight_engine.py:120`** —
  `f"coach:llm:{backend}:{model}:{tier}:{digest}"` where `digest = sha256(system_prompt + user_prompt)`.
  The prompts are built from organization financial context. **SAFE by content-addressing**:
  the cached value is a pure function of the hashed input, so a key collision requires
  byte-identical prompts, in which case the answer is correct for both readers.
  *NEEDS-JUDGEMENT (low):* identifiers are anonymised to positional codes
  (`context_builder.py:47-71`, `EMP-001`, `CUST-001`, assigned in first-seen order per
  `ContextBuilder` instance) and the anonymisation map is **not** part of the key. For a
  trivial/zero-state context two orgs can collide; the returned text would then be
  de-anonymised through the *reading* org's map, mis-attributing a generic insight to the
  wrong entity. Question for the owner: is any de-anonymisation applied to the cached
  response, and should the org id be added to the key purely to make that impossible?
- **`app/i18n.py:22 @lru_cache(maxsize=16) _load_locale(locale)`** — static JSON file
  content keyed by locale. **SAFE.**
- **~25 per-instance caches** in the import/export and sync services
  (`finance/import_export/*`, `people/hr/import_export.py`, `fleet/import_export.py`,
  `pm/import_export.py`, `dotmac_sub/sync/_base.py:126-140`, `crm/sync/base.py:107`,
  `audit_info.py:49`) — keys are names/codes/external ids with **no org prefix**, but every
  one lives on a service instance built around a single org-primed `Session`, so the org
  filter applies and the object never outlives the run. **SAFE**, though the pattern has zero
  margin: promoting any of these to a module-level dict would immediately become a leak.
- **`app/services/finance/cache_invalidation.py`** — all methods take `organization_id`
  required; **has no callers anywhere in `app/`**. Not a leak; dead code that means the
  dashboard/org-context invalidation hooks it defines are never fired.
- **HTTP responses** — ERP emits **no positive `Cache-Control` anywhere**; the only
  directives are suppressions (`auth_web.py:44`, `web/notifications.py:270`,
  `people/discipline/web/discipline_web.py:271,332`). `/static` is validator-cached by
  Starlette defaults. **SAFE.**
- **Jinja** — plain `Jinja2Templates(directory="templates")` (`app/templates.py:23`), no
  bytecode cache, no `{% cache %}`. **SAFE.**

## 1.5 dotmac_erp — adjacent (correctness, not tenancy)

- **`app/services/people/payroll/working_days_calculator.py:178`** —
  `cache_key = organization_id`, but the cached `HolidayCalendar` depends on
  `period_start`/`period_end`, which are **not in the key** (`:154`). Same org, wrong period.
  Acknowledged in a comment at `:177`.
- **`static/js/fx-rate-lookup.js:19-21`** — `` `${currency}:${date}` ``, page-lifetime, no TTL
  and no reset. Scoped in practice to one tab/one session; would become a leak only if an
  in-page organization switcher existed without a reload. No such switcher was found.
- **`app/services/finance/automation/entity_registry.py:171`**, `app/licensing/state.py:37`,
  `app/licensing/fingerprint.py:17` — code objects / deployment-level license state. SAFE.

---

# 2. dotmac_sub — no confirmed leaks; one dangerous export

Sub has no `organization_id`/`tenant_id` column; its `DomainSetting`
(`app/models/domain_settings.py:53-56`) is unique on `("domain","key")` — genuinely
**single-scope**. Its real scoping boundaries are `reseller_id` (88 files), subscriber/customer,
and per-user.

## 2.1 The settings cache is safe *here* and unsafe *anywhere else*

`/Users/michaelayoade/Downloads/management/dotmac_sub/app/services/settings_cache.py:41-43`:

```python
def _cache_key(domain: str, key: str) -> str:
    """Build the Redis cache key."""
    return f"{SettingsCache.PREFIX}{domain}:{key}"
```

**Verdict: SAFE in `dotmac_sub`** — the backing table has exactly one row per
`(domain, key)`, so there is no scope for the key to omit. This is the honest verdict, and
it is also exactly why this file is a hazard: see §4.6. The same applies to
`app/services/module_manager.py:90,102` — the module-availability cache is
`SettingsCache.get("modules", "states")`, one global slot, correct here and only here.

## 2.2 Verified SAFE (scoped correctly, worth recording)

- **`app/services/auth_cache.py:32-43`** — `app_cache.cache_key(_AUTH_NAMESPACE, "claims", principal_type, principal_id)`;
  both scoping args **required positional**. Session context (`:36`) is keyed by `session_id`
  and cross-checked against `principal_id`/`principal_type` at the call site
  (`app/web/auth/dependencies.py:113-116`). Rich invalidation (9 call sites). **SAFE.**
- **`app/services/brand_profiles.py:150-163`** — `cache_key = (scope_type, scope_id)`, stored
  in `db.info` (one DB session = one request). `BrandProfile` is genuinely scope-typed
  (`app/models/branding.py:27-32`: `platform` / `reseller` / `organization` with a CHECK
  constraint), and the scope is in the key. **SAFE**, all four properties.
- **`app/services/web_billing_overview.py:229-238`** — key
  `(normalized_partner, normalized_location, normalized_period)`. `partner_id` is optional
  (`:261`), but the sole caller (`app/web/admin/billing_invoices.py:74`) is an admin route
  gated on `billing:invoice:read` and passes a user-supplied filter, not a scope guard; the
  `None` bucket is the legitimate all-partners aggregate and it has its own key.
  **SAFE** — this looked like the closest match to the defect class in sub and is not one.
- **`app/services/web_admin.py:160 get_sidebar_stats(db)`** — one process-global slot,
  60s TTL, holding operator-wide counts (`service_orders`, `notifications_unread`,
  `pending_location_requests`) plus the platform brand. Consumed by the **reseller** portal
  (`app/web/reseller/branding.py:68`) and by **unauthenticated** auth pages
  (`app/web/portal_branding.py:36`). Verified: no reseller or auth template renders those
  counts (grep over `templates/reseller/`, `templates/auth/`, `templates/portal/` for
  `service_orders|notifications_unread|pending_location_requests` returns nothing) — only the
  brand fields are used, and the per-reseller brand overlay is applied **per request** from
  `request.state.reseller_brand` and never written back into the cache
  (`app/web/reseller/branding.py:82-94`). **SAFE** (over-fetching, not disclosure).
- **`app/services/crm_client.py:545-547`** — `f"crm:resp:{sha256(json.dumps([path, params]))}"`.
  Content-addressed over the full request; sub calls CRM with a single service credential, so
  the response is operator-level, not per-actor. **SAFE**, no invalidation path (TTL 30–60s only).
- **`app/services/customer_portal_session.py:37`, `reseller_portal.py:104`,
  `session_store.py:53-176`** — session payloads keyed by unguessable session token, with
  per-principal index sets and revoke-all epochs. **SAFE.**
- **`functools` memos** — `version.py:10`, `branding_config.py:94` (deployment-static
  `brand.json`+env, with `reset_brand_cache()`), `object_storage.py:285`, `secrets.py:106`
  (keyed on url+token+namespace+TTL bucket, with `cache_clear()`),
  `network/parsers/loader.py:61`, `owner_commands.py:156`. All **CFG/deployment-static**. **SAFE.**
- **Device/network caches** — `network/olt_read_cache.py:109-118` (`f"olt:{olt_id}:{operation}:{params}"`),
  `core_router_metrics.py:71`, `olt_dependency_preflight.py:52`, `enforcement.py:337` — all
  keyed by device id. **SAFE.**

## 2.3 Adjacent / latent findings

1. **`app/web/public/branding.py:126` — the highest-value latent instance of the defect class.**
   `GET /branding/theme.css` returns brand CSS with `headers={"Cache-Control": "public, max-age=300"}`
   and **no `Vary`**, built from `resolve_brand(db)` called at `:94` with **no scope argument**
   (so: platform brand only, today). `resolve_brand`'s signature
   (`app/services/brand_profiles.py:187`) is
   `resolve_brand(db, *, subscriber_id=None, reseller_id=None, organization_id=None)` — three
   optional scope arguments defaulting to "no scope", and `BrandProfile` already carries
   reseller and organization scopes. The moment anyone makes this endpoint reseller-aware
   (the obvious next feature, given the reseller portal already overlays a brand per request),
   a `public, max-age=300` response with no `Vary` becomes a shared-cache cross-reseller leak
   — the CDN/proxy becomes the tenant-blind cache. Same for
   `/branding/manifest.webmanifest` (`:185`, `public, max-age=3600`) and
   `/branding/assets/{file_id}` (`:209`, `public, max-age=3600`, no auth check).
   **Recommendation:** add `Vary: Cookie` (or make it explicitly scope-keyed in the path) in
   the *same change* that ever adds a scope argument there.
2. **`app/services/enforcement.py:302-303 _COA_SECRET_CACHE`** — RADIUS shared secrets keyed
   by NAS id with **no TTL and no invalidation** (comment at `:299-301` acknowledges it).
   Correctly keyed; the finding is credential-rotation drift, not tenancy.
3. **`app/services/ticket_mentions.py:23`** — the staff-mention directory is cached globally
   with a 30s TTL, and `limit` (`:96`, default 200) is **not** part of the key: the first
   caller's `limit` fixes the list for everyone for 30s. Operator-global data, so not a leak —
   a correctness bug.
4. **nginx `nginx/selfcare.dotmac.io.conf:86-90`** — `location /uploads/ { alias …; expires 7d; add_header Cache-Control "public"; }`
   serves the on-disk `uploads/` tree (which contains `legal/` and `system_exports/`)
   unauthenticated with a public 7-day cache directive. **Out of class** (an authorization
   gap, not a cache-key defect) and unverified against the live host's actual config —
   flagged for separate follow-up, not counted in the leak list.

---

# 3. dotmac_crm — no confirmed leaks; one fragile-by-construction key

CRM's `CLAUDE.md:11-12` declares it single-tenant. `organization_id` here denotes a *customer*
organization (a CRM account), not a tenant. The scoping boundaries that matter are agent /
team / vendor / reseller.

## 3.1 The one that needed real verification — and passes today

**`app/services/crm/inbox/listing.py:126-148` and `app/web/admin/crm_inbox_conversations.py:130-148`.**
The inbox conversation list (and, at `:176`, the **fully rendered HTML** of it) is cached in a
per-process dict keyed by a JSON dump of the filter params. The acting agent is folded into
the key **conditionally**:

```python
actor_sensitive_assignment = assignment_filter in {"assigned", "assigned_to_me", "mine", "my_team"}
...
"assigned_person_id": assigned_person_id if actor_sensitive_assignment else None,
```

**Verdict: SAFE today, fragile by construction.** Evidence — I read the query builder to check
whether the conditional is exhaustive. `app/services/crm/inbox/queries.py:147-205` uses
`assigned_person_id` in exactly two branches: `if assignment_filter in ("assigned", "assigned_to_me", "mine")`
(`:147`) and `elif assignment_filter == "my_team"` (`:191`). Every other branch
(`unassigned` `:167`, `team_assigned` `:176`, `unreplied` `:186`, `needs_attention` `:188`)
is actor-independent. So the cache's actor-sensitive set is currently an exact superset of the
query's actor-sensitive branches, and no agent can be served another agent's list.

The fragility: that literal set is **duplicated in four places** with two different spellings —
`app/services/crm/inbox/listing.py:127`, `app/web/admin/crm_inbox_conversations.py:129`
(both `{"assigned","assigned_to_me","mine","my_team"}`) versus
`app/services/crm/inbox/queries.py:147` and `:658`
(both `("assigned","assigned_to_me","mine")`, with `my_team` handled by a separate `elif`).
Adding one new actor-dependent `assignment` branch to `queries.py` without touching both cache
sites turns this into a live cross-agent leak of conversation lists *and* pre-rendered HTML
containing customer PII, with no test failing. See §5.3 for the fix.

## 3.2 Verified SAFE

- **`app/services/settings_cache.py:50`** — `f"{PREFIX}{domain}:{key}"`; same single-scope
  reasoning as sub. **SAFE here.**
- **`app/services/auth_cache.py:59,80,100`** — `f"{AUTH_CACHE_PREFIX}session:{session_id}"`;
  `session_id` required, invalidated from 5+ sites incl. a per-person bulk purge
  (`auth_flow.py:420`). **SAFE.**
- **`app/services/web_admin.py:121-127 _SIDEBAR_STATS_CACHE`** — key
  `f"{person_id}|{','.join(sorted(permissions))}"`. I checked the degenerate case: when
  `current_user` is `None` the key becomes `"|"`, but a real user always contributes a
  `person_id`, so the `"|"` bucket only ever holds the no-user result. Person **and**
  permission set are both in the key. **SAFE** (unbounded dict — see §3.3).
- **`app/web/admin/reports.py:1603-1621 _ONLINE_LAST_24H_ROWS_CACHE`** — key is the full
  filter tuple including a sorted `subscriber_scope`. Route is admin-only; `subscriber_ids`
  is a user-supplied filter, in the key. **SAFE.**
- **`app/services/revenue_service_report.py:1011`** — `key = (year, month)`; the only caller
  is the admin route `app/web/admin/reports.py:6214`. All viewers of that report are one
  scope. **SAFE.**
- **`app/services/dotmac_erp/cache.py:90`** — `f"{PREFIX}{entity_type}:{entity_id}"`; both
  required positional; that pair *is* the scope. **SAFE.**
- **`app/services/crm/inbox/page_context.py:246`** — `f"inbox_detail:macros:v1:{current_agent_id or 'shared'}"`.
  Agent in the key, and the loader branches on the same value (`:252-257`). **SAFE** — and note
  this is the good pattern: a *named* `'shared'` scope rather than an empty segment.
- **`app/web/admin/system.py:123-124` API-key flash** — `f"api_key_flash:{token}"` with
  `token = secrets.token_urlsafe(32)`, 60s TTL, single-use consume, and `person_id` required
  at read (`:141-143`). **SAFE.**
- **`app/main.py:592-604 static_cache_middleware`** — the only `public` directive in the repo,
  gated on `path.startswith("/static/")`; no dynamic route can receive it. **SAFE.**
- **`app/version.py:17`** — the repo's only `lru_cache`. Version string. **SAFE.**
- **Jinja** — ~30 independent `Jinja2Templates` instances, each with its own default compiled-
  template LRU; no `bytecode_cache`, no `{% cache %}`. **SAFE** (wasteful, not unsafe).

## 3.3 Adjacent

1. **`app/middleware/widget_rate_limit.py:75`** — `key = f"session_create:{ip_address}"`, while
   the *limit* is read per-widget from `config.rate_limit_sessions_per_ip`
   (`app/api/crm/widget_public.py:267-270`). Cross-**account** availability contention: two
   widget configs belonging to different accounts, sharing one visitor IP, share one bucket,
   and the lowest configured limit throttles all of them. Also `ip_address or "unknown"`
   collapses all IP-less clients into one bucket.
2. **`app/middleware/api_rate_limit.py:96-107`** — the key function prefers
   `request.state.user_id`, but **`request.state.user_id` is set nowhere in the codebase**
   (verified: grep for `state.user_id` across `app/` and `platform_app/` returns nothing —
   the auth layers set `state.auth`, `state.user`, `state.actor_id`). So every authenticated
   API caller falls through to `f"ip:{ip}"` and shares one 100-req/60s bucket per NAT.
   Compounding: `_get_client_ip` (`:261-266`) trusts `X-Forwarded-For` unconditionally, so the
   bucket is attacker-selectable. Separately, `WebhookRateLimitMiddleware` (`:269`) inherits
   `API_PREFIXES` and short-circuits at `:145` for any non-`/api` path, making it **inert** for
   `/webhooks/crm/*`.
3. **`app/services/auth_flow.py:45-46,164 _JWT_SETTINGS_CACHE`** — JWT secret and algorithm
   cached behind a permanent `_JWT_SETTINGS_CACHED = True` latch: **no TTL, no invalidator**.
   Rotating the JWT secret requires a process restart, and a rotation that isn't followed by a
   restart silently keeps signing with the old secret.
4. **Unbounded dicts with no eviction** — `web_admin.py:8`, `revenue_service_report.py:32,994`,
   `billing_risk_reports.py:42`, `web/admin/reports.py:1595`, `vendor_portal.py:22`,
   `crm/inbox/cache.py:28,29` (the per-key `Lock` dict is never pruned), `crm/inbox/queue.py:154`,
   `crm/inbox/whatsapp_templates.py:17`, `platform_app/app/api/auth.py:34`,
   `middleware/api_rate_limit.py:37`, `middleware/widget_rate_limit.py:27`. Memory-growth risk.

---

# 4. dotmac_starter_mt — the decision-relevant answer

**Question:** does the kernel currently ship any cache that a module could inherit this defect
from, given that the next programme step adds module-availability, entitlement and health
caches to it?

**Short answer: no — the kernel ships no cross-request data cache today, and the one
tenant-keyed cache it does ship is already correct. But the convention must still land before
WS1/WS2 caches are written, because the kernel currently offers no key-building helper at all,
and the guidance it *does* ship points at two structurally tenant-blind implementations.**

## 4.1 Complete kernel + assembly inventory (6 constructs)

The assembly (`app/`) contains **zero** caches — a `grep` for
`lru_cache|functools\.cache|cached_property|Cache-Control|max-age|redis|_CACHE|TTLCache`
across `app/`, `templates/` and `static/` returns nothing. Everything below is in
`packages/dotmac-kernel/src/dotmac_kernel/`.

| # | File:line | Caches | Data scope | Exact key | Verdict |
|---|---|---|---|---|---|
| 1 | `branding.py:153` `@lru_cache(maxsize=1) get_brand()` | deployment brand dict: `_DEFAULTS` < `brand.json` < `BRAND_*` env | **deployment-static config**, no tenant data | zero-arg (empty tuple) | **SAFE** — `reset_brand_cache()` at `:172`. But see §4.3(b): this is the exact *shape* a module would copy. |
| 2 | `templating.py:178` `@lru_cache(maxsize=256) _asset_version(path)` | sha256 of a file under the kernel's packaged `static/` | non-tenant | the `path` argument | **SAFE** |
| 3 | `templating.py:212` `templates.env.globals["brand"] = get_brand()` | import-time Jinja global | non-tenant (static brand) | n/a | **SAFE** — `render()` overrides `brand` from `request.state.branding` per request; the fallback is the deployment brand, never another tenant's. |
| 4 | `branding.py:250 get_request_branding` / `display.py:68 get_request_display` | per-tenant branding dict / tenant timezone+formats | **tenant-scoped** | memoized on `request.state.branding` / `.display` | **SAFE by construction** — request-scoped; one request has exactly one `request.state.tenant`. |
| 5 | `middleware/rate_limit.py:145 _rate_limit_key` | sliding-window counters (`MemoryStore`, LRU-capped) | tenant-partitioned | `f"rate_limit:{tenant_key}:{client_ip}:{_path_bucket(request)}"` with `tenant_key = str(tenant.id) if tenant is not None else "platform"` | **SAFE — and it is already the target pattern**: scope is the first segment, the platform scope is a *named literal*, and there is no silent "no tenant" bucket. Contrast ERP §1.3, which has no tenant segment at all. |
| 6 | `settings_resolver.py:69 _REGISTRY: dict[(SettingDomain, str), SettingSpec]` | code-declared `SettingSpec`s | non-tenant (code) | `(domain, key)` | **SAFE** |

Also checked and **not** caches: `licensing.py:166 LicenceKeyRing._keys` (a closed-world trust
store built from `LICENCE_VERIFICATION_KEYS`, fails closed on duplicates);
`middleware/tenant.py:100 _resolve` (a fresh `SessionLocal()` DB read of host→tenant on
**every** request — deliberately uncached).

## 4.2 What the kernel does right already

- `resolve_value` / `resolve_with_source` / `upsert_by_key` / `ensure_by_key`
  (`settings_resolver.py:242,157,356,398`) all take `tenant_id: UUID | None` as a
  **keyword-only argument with no default** — property 1 is satisfied at the source.
- `is_entitled` (`entitlements.py:121`) and `grant_entitlement` (`:79`) take
  `tenant_id: UUID` as a **required keyword**.
- The rate-limit key (§4.1 #5) is a working demonstration of properties 1–3.

## 4.3 Why the convention must still land first — three concrete reasons

**(a) The kernel's own docstring instructs the next implementer to port a tenant-blind cache.**
`settings_resolver.py:13-15`, verbatim:

> `No caching here: phase 1 has no Redis. Backlog: a Redis-backed settings cache lands in phase 3 (see `dotmac_sub:app/services/settings_cache.py` for the shape to port when that lands).`

That named file's key builder is, verbatim (§2.1):
`return f"{SettingsCache.PREFIX}{domain}:{key}"` — **no scope component**. It is correct in
`dotmac_sub` only because sub's `domain_settings` is unique on `("domain","key")`. The kernel's
`DomainSetting` is `tenant_id`-scoped with the partial-unique pair
`uq_domain_settings_platform` / `uq_domain_settings_tenant`. Porting "the shape" verbatim
reproduces ERP §1.1 exactly, in the package every future product inherits.
The same hazard applies to the **module-availability** cache specifically: sub's is
`app/services/module_manager.py:90` → `SettingsCache.get("modules", "states")`, a single global
slot — and "module availability cache" is precisely what WS1 adds.
`docs/superpowers/phase2-backlog.md:229-235` repeats the same pointer.
**This pointer must be corrected before anyone follows it.**

**(b) Property 3 is not yet satisfied anywhere in the kernel.** `resolve_value(..., tenant_id=None)`
is the platform path *through the same function* as the tenant path
(`settings_resolver.py:254`: "The tenant lookup is skipped entirely when `tenant_id is None`").
While there is no cache, that is merely a design choice. The moment a cache exists, `None`
becomes a legitimate-looking key value and a caller that forgets to thread a tenant through
silently reads and *populates* the platform entry. The ERP fix's split — `get_setting_value`
(raises on `None`) versus a separately named `get_global_setting_value` — is the shape to adopt,
and it is cheaper to adopt now, before there are call sites to migrate.

**(c) Module availability is deployment-scoped *today* and becomes tenant-scoped in WS2.**
`ModuleRegistry(spec.modules)` is built once per application in `app_factory.py:104`, and
`enabled_codes(disabled)` (`modules.py:386`) takes a deployment-level disabled set. A
process-wide memo over that is harmless. WS2 makes availability a function of
`is_entitled(db, tenant_id=..., capability_code=...)` — at which point the identical memo is a
leak. `docs/superpowers/plans/2026-07-18-deployment-profiles-commercial-platform.md:207` already
lists "cache versioning and invalidation" as WS2 scope, and `:229` requires tests covering
"cache invalidation". `is_entitled` currently has **no request-time callers**
(`app/features/custom_fields/feature.py:27` notes enforcement is "future, contract-gated work") —
so the window to land the convention before the first cached entitlement check is still open.

---

# 5. Enforcement proposal

## 5.0 What a static check can and cannot do

**Can**, reliably:

- Force all cache keys through one helper (ban raw string/f-string key construction at
  `cache.get/set/delete` boundaries).
- Force the helper's scope parameter to be keyword-only with **no default**.
- Ratchet: fail on any *new* unscoped key while grandfathering the legitimate global ones.
- Catch the specific ERP shape: a function that both queries a scoped model and writes to a
  cache, but takes no scope argument.

**Cannot**:

- Verify the scope *passed* is the *right* one — `cache_key(scope=other_tenant_id)` type-checks.
- Verify invalidation is scope-correct (that a write purges its own scope and only its own).
- See through an opaque params blob: CRM's `build_inbox_list_key(params: dict[str, Any])`
  (§3.1) is a dict at the call boundary; a linter sees `dict`, not whether the actor is inside it.
- Catch HTTP/CDN-level scope loss (`Cache-Control: public` without `Vary` — §2.3(1)).

So each repo needs **a structural gate plus a behavioural test**. The behavioural template
already exists: `a88c05d5`'s `tests/services/test_settings_cache_org_scope.py` — 18 tests, 14 of
which fail against the pre-fix logic. That "prove the test fails without the fix" property is
what makes it worth writing.

## 5.1 dotmac_erp — highest priority, mechanism already half-built

1. **Merge `security/settings-cache-org-scope` (`a88c05d5`).** It is the fix for §1.1 and the
   reference implementation for everything below. It is not on `origin/main`.
2. **Fix §1.2 in the same pass** — `app/main.py:596` needs either an explicit
   `DomainSetting.organization_id.is_(None)` predicate (making it a genuine platform-level read
   through a *named* global path, which is almost certainly the intent) or a per-org key. As it
   stands the `allow_cross_org` block has no scoping authority at all.
3. **Add `tests/architecture/test_cache_key_scope.py`** — ERP already has a
   `tests/architecture/` package (`test_webhook_org_attribution.py`, `test_sot_registry_liveness.py`,
   … — the culture is there). AST-walk `app/`: for every call to
   `cache_service.{get,set,delete,delete_pattern}` / `_inmemory_cache.*` / `settings_cache.*`,
   resolve the key expression; require that it either (i) is produced by a `CacheKeys.*` /
   `RequestCacheKeys.*` builder (all of which already take a required `org_id`), or (ii) contains
   an `organization_id`/`org_id` name, or (iii) appears in an explicit
   `PLATFORM_SCOPED_KEYS` allowlist with a mandatory inline comment giving the reason —
   the same allowlist-with-a-reason pattern ERP/starter already use for route guards.
4. **Promote `.claude/hooks/check-multitenant.py` to a CI gate for `app/services/**`.** It is
   currently a PostToolUse hook exiting 2 (an advisory warning to the agent), and it is
   *query*-shaped: `_has_select_call(node) and not _has_org_id_ref(node)`. That means it would
   have flagged §1.1's `get_setting_value` and was evidently overridden — and, more importantly,
   it **cannot catch the general defect at all**, because a function that queries *with* a
   correct org filter and then caches under an org-blind key passes it cleanly. Extend it with a
   second rule: *a function that writes to a cache must reference an org identifier in the key
   expression*.

## 5.2 dotmac_sub — a ratchet, not a rule

Sub has by far the strongest architecture-test culture in the fleet (~180 files under
`tests/architecture/`, several backed by `*_baseline.txt` ratchets:
`sot_writer_baseline.txt`, `decision_input_bypass_baseline.txt`, `type_gate_masked_module_baseline.txt`).
Use that machinery, because a hard "scope must be in the key" rule would be **all false
positives here** — sub is single-operator and most of its ~90 keys are legitimately global.

1. **`tests/architecture/test_cache_key_scope.py` + `cache_key_scope_baseline.txt`** listing
   every currently-unscoped cache key. The test fails on any **new** unscoped key. This
   captures the real risk (a future reseller-scoped cache added without a scope segment)
   without churning the correct existing ones.
2. **Annotate the two export hazards at the source.** Add to
   `app/services/settings_cache.py` and `app/services/module_manager.py` a docstring line
   stating that the key is single-scope by design because `domain_settings` is unique on
   `(domain, key)`, and that the shape **must not be ported to a tenant-scoped assembly
   without adding a scope segment**. This is the cheapest possible fix for §4.3(a) on sub's side.
3. **HTTP scope rule.** Extend the existing route-guard architecture test with: any response
   carrying `Cache-Control: public` must either come from a static/asset path or declare a
   `Vary`. That is the check that would catch §2.3(1) on the day someone makes
   `/branding/theme.css` reseller-aware.

## 5.3 dotmac_crm — one targeted structural test, not a repo-wide rule

CRM has no `tests/architecture/` package and is single-tenant; a repo-wide org-in-key rule
would produce nothing but noise. Do the two things that actually matter:

1. **Collapse the duplicated actor-sensitive set into one constant** — export
   `ACTOR_SENSITIVE_ASSIGNMENTS` from `app/services/crm/inbox/queries.py` and import it at
   `app/services/crm/inbox/listing.py:127` and `app/web/admin/crm_inbox_conversations.py:129`.
   Then add a unit test that AST-walks `list_inbox_conversations` and asserts that **every**
   `assignment_filter` branch whose body references `assigned_person_id` has its literal in
   `ACTOR_SENSITIVE_ASSIGNMENTS`. That is a real, checkable invariant, and it is precisely the
   thing that will silently break (§3.1).
2. **Put the account in the widget rate-limit key** — `app/middleware/widget_rate_limit.py:75`
   should be `f"session_create:{config_id}:{ip_address}"`, since the *limit* is already read
   per-config. Separately: `request.state.user_id` is never set (§3.3(2)) — either set it in the
   auth dependency or delete the dead branch, because right now the API limiter silently
   degrades to per-IP.

## 5.4 dotmac_starter_mt — the key-building helper that cannot omit scope

This is where the *mechanism* belongs, because the kernel is the artefact every future product
copies. Land it **before** WS1/WS2 caches, in this order:

1. **Delete the two bad pointers first** (one-line docs change, zero risk):
   `settings_resolver.py:13-15` and `docs/superpowers/phase2-backlog.md:229-235` must stop
   naming `dotmac_sub:app/services/settings_cache.py` as "the shape to port". Point them at
   `dotmac_erp` commit `a88c05d5` instead, which is the scoped version.

2. **Add `dotmac_kernel/cache.py` with a scope type that cannot be omitted.** Not a `str`
   parameter — a *type*, so "forgot the scope" is a `TypeError`, not a plausible-looking key:

   ```python
   @dataclass(frozen=True, slots=True)
   class TenantScope:  tenant_id: UUID
   @dataclass(frozen=True, slots=True)
   class PlatformScope: pass          # the separately NAMED global path (property 3)

   Scope = TenantScope | PlatformScope

   def cache_key(*parts: str, scope: Scope) -> str:   # keyword-only, NO default
       segment = f"t={scope.tenant_id}" if isinstance(scope, TenantScope) else "platform"
       return ":".join((*parts, segment))
   ```

   The two segment forms are structurally different (`t=<uuid>` vs the bare literal
   `platform`), so no tenant identifier can ever collide with the platform scope — the same
   reasoning as the ERP fix. Give the store a `RateLimitStore`-style **Protocol** so the
   Redis swap in phase 3 inherits the key model rather than inventing one, exactly as
   `middleware/rate_limit.py` already does for rate limiting.

3. **Split the platform read path in `settings_resolver`** (property 3), mirroring
   `a88c05d5`: keep `resolve_value(..., tenant_id: UUID)` raising on `None`, and add a
   separately named `resolve_platform_value(...)`. Do this while the call sites are few.

4. **Add `tests/architecture/test_cache_scope.py`**, asserting:
   - no module under `packages/dotmac-kernel/src/dotmac_kernel/` or `app/` builds a cache key
     by string concatenation/f-string outside `dotmac_kernel.cache` (allowlist the rate-limit
     key until it is migrated onto the helper);
   - every public function that reads a `tenant_id`-bearing model **and** writes to a cache
     takes `tenant_id` as a required keyword;
   - `@lru_cache` / `functools.cache` may only decorate functions whose parameters are all
     non-tenant scalars — this is the check that catches the easily-missed instance (a
     process-wide memo on a tenant-scoped getter), and it is the one rule that would have
     needed to exist before someone copied `get_brand()`'s zero-arg `@lru_cache(maxsize=1)`
     shape onto a tenant-scoped loader.

5. **Add it to the hard-rules list.** It is rule-shaped and belongs in `AGENTS.md` alongside
   the existing thirteen: *"Every cache key carries its scope, built via `dotmac_kernel.cache
   .cache_key`; the platform scope is a separate named path, never a `None` tenant"* —
   with `tests/architecture/test_cache_scope.py` named as its enforcing test, and indexed from
   `CLAUDE.md`.

6. **Add the behavioural canary**, since §5.0 says the static check cannot cover invalidation:
   a two-tenant test that writes as tenant A, reads as tenant B, asserts isolation, then
   invalidates as A and asserts B's entry survived. `tests/` already has the Postgres RLS-canary
   convention for exactly this kind of tenancy proof.

---

# 6. Ranked confirmed leaks (fleet-wide)

| # | Repo | Location | What crosses | To whom, under what sequence | Status |
|---|---|---|---|---|---|
| 1 | `dotmac_erp` | `app/services/settings_cache.py:262` | GL account ids, warehouse ids, procurement approval thresholds, payroll automation policy, email/report branding | Any org, from the first org to read that setting, for the domain TTL (60–600s), deployment-wide via shared Redis. No race required. | **Live.** Fix on unmerged branch `security/settings-cache-org-scope` (`a88c05d5`). |
| 2 | `dotmac_erp` | `app/main.py:596` | audit-logging enable/skip-path policy | Every org on the worker, for 30s, governed by an arbitrary org's row (`allow_cross_org` + `{row.key: row}` last-wins). Compliance/forensics control. | **Live, unfixed.** |
| 3 | `dotmac_erp` | `app/middleware/rate_limit.py:290` | request-rate allowance (availability, not data) | Tenants sharing a NAT/proxy egress IP exhaust each other's budget on the same path. | **Live.** Kernel already does this correctly. |
| 4 | `dotmac_crm` | `app/middleware/widget_rate_limit.py:75` | widget session-creation allowance (availability, not data) | Different accounts' widgets sharing a visitor IP; the lowest configured limit throttles all. Limit is per-config, key is not. | **Live.** |

No confirmed cross-scope **data** leak was found in `dotmac_sub`, `dotmac_crm`, or
`dotmac_starter_mt`. Four candidates that pattern-matched the defect were read to the query
level and cleared: sub's `web_billing_overview` partner key, sub's reseller sidebar-stats reuse,
CRM's `_SIDEBAR_STATS_CACHE` degenerate key, and CRM's conditional actor key (§3.1) — the last
being correct only by an undefended coincidence of four duplicated string literals.
