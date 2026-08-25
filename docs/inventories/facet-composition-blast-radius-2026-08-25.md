# Facet composition — blast radius and source patterns

**As of:** 2026-08-25.
**Taken at:** `dotmac_starter_mt` `1029354b` (`origin/main`), `dotmac_sub`
`3ec14bbb7`, `dotmac_erp` `dbe7cd95`, `dotmac_workspace` `c72fe30`.
**Evidence for:** ADR-0006's 2026-08-25 amendment (the facet runtime contract).

**Implementation status in this worktree:** the typed `WebFacetMount` /
`WebSurfaceContribution` runtime, Template Studio canary migration, platform
surface declaration, request-scoped `SurfaceContext`, and the independent CSRF
remediation have now been implemented. Runtime canaries accompany that code;
execution evidence belongs to the repository-required exact-commit run on
Observer and CI, not to this inventory. The measurements below remain the
before-state evidence that justified the extraction and must not be read as the
current starter behaviour.

This is a characterization, not a mandate — see `README.md`'s two cautions. It
answers the questions the amendment needed settled before a kernel facility could
be introduced: does the audience split already exist in products, which running
implementation should the contract be sourced from, what would the contract have
to change in the starter, what does the browser-security posture already do, and
what exists on native mobile.

All claims below are repository-local reads of the trees named above. No release,
registry or production-adoption claim is made (`AGENTS.md` rule 30). Counts
obtained by `grep` are stated as occurrences and files, not as audited artefacts.

**One finding was a live defect rather than a design gap**: § 5's measured CSRF
trigger did not cover a pre-auth cross-site POST. The implementation in this
worktree corrects it independently of the facet contract.

---

## 1. The audience split already exists, authored per product

`dotmac_sub` serves **five audience template trees** from one application:

| Tree | `templates/` child |
|---|---|
| Staff admin | `admin` |
| Customer self-service | `customer` |
| Reseller | `reseller` |
| Vendor | `vendor` |
| Public / pre-auth | `public` |

behind **three** guards:

| Guard | File | Shape |
|---|---|---|
| `require_web_auth` | `app/web/auth/dependencies.py:161` | session validation; picks the login/refresh route by path prefix (`/vendor/auth/refresh` vs `/auth/refresh`) |
| `require_admin_web_auth` | `app/web/auth/dependencies.py:205` | default-deny on principal TYPE (`STAFF_PRINCIPAL_TYPES = {"system_user"}`) |
| `require_vendor_web_auth` | `app/web/vendor_auth_flow.py:505` | `require_web_auth` + `vendor_context(db, auth)` |

`dotmac_erp` has its own `require_web_auth` (`app/web/deps.py:1273`, accepting
either a bearer header or an `access_token` cookie and returning a
`WebAuthContext`) over a parallel per-domain tree set (`admin`, `finance`,
`inventory`, `operations`, `coach`, `careers`, `fleet`, …).

**Reading.** This is exactly the duplication ADR-0006 § 1 predicted when it named
the facet the missing concept: the audience dimension is real and is already
being expressed, but each product invents its own vocabulary, its own guard
layering and its own template partition. Nothing is shared, and nothing prevents
the next product from inventing a sixth spelling.

## 2. The three-layer split is extracted, not invented

Sub's two-guard layering already separates the questions the amendment's § 2
table names, and says so in its own docstring:

> ``require_web_auth`` already guarantees the request is authenticated (and
> redirects to login otherwise). This adds a default-deny on principal type so
> that subscriber/reseller logins cannot reach ``/admin`` … Per-route permission
> checks (``require_permission``) still apply on top of this baseline.

Three properties of that implementation carried into the ADR:

1. **Admission is not a role check.** Sub gates on principal *type*, not on a
   role slug. The amendment's ruling that a raw role must not sit on `WebFacetMount`
   matches the running code rather than contradicting it; the starter's
   improvement is to express admission as a *declared permission* evaluated
   through `authorize_party`, which Sub could not do because its check predates
   the authentication-neutral seam.
2. **Layer 3 is explicitly retained.** Per-route `require_permission` survives
   admission. Facet admission is a baseline, never a substitute for route
   authorization.
3. **Context supply is separate from decision.** `require_vendor_web_auth` adds
   `vendor_context(db, auth)` and decides nothing. This is the shape of "facets
   provide context but never decide domain authorization".

## 3. The authentication profile already runs, hand-rolled

`dotmac_workspace/src/dotmac_workspace/web_auth.py:57`:

```python
def require_workspace_auth(request: Request, db: Session = Depends(get_db)) -> Party:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise WebAuthRedirect(request.url.path, login_path=LOGIN_PATH)
    party = authenticate_request(request, db, token=token)
    if party is None:
        raise WebAuthRedirect(request.url.path, login_path=LOGIN_PATH)
    return party
```

`SESSION_COOKIE` and `LOGIN_PATH` come from the assembly's own
`session_contract` module. This is the amendment's **authentication profile** —
cookie mechanism, login route, accepted principal context — already implemented
as a hand-rolled per-assembly constant pair, delegating identity to the shared
`authenticate_request` and deciding no authorization whatsoever.

**This is the product-first source for the contract.** Porting it means naming
what Workspace already declares, not designing something new.
`docs/ARCHITECTURE.md` records why Workspace had to hand-roll it: the kernel's
`require_permission` was welded to the bearer header while `require_web_auth`
read the wrong cookie and hardcoded `"admin"`, "leaving the assembly to hand-roll
the role query, which is how a plane falls behind a kernel security fix".

## 4. Starter blast radius

### 4a. Surface state — the largest item

`packages/dotmac-kernel/src/dotmac_kernel/templating.py` holds **one**
module-scope environment:

```python
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
```

and four process-static globals written into it:

| Global | Writer | Facet-varying? |
|---|---|---|
| `nav_items` | `install_surface_globals` | yes — per facet |
| `enabled_features` | `install_surface_globals` | no (deployment-wide) |
| `extra_stylesheets` | `install_stylesheets` | yes — per facet cascade |
| `brand` | module import | yes — per facet/tenant |

`install_surface_globals` builds one flat `nav_items` tuple across every
manifest, and `app_factory.create_app` calls it once with the comment
"Process-static Jinja globals … must be set before any template renders". Two
facets cannot differ under this mechanism. **Retiring process-static surface
state in favour of per-facet resolution is the load-bearing change**, and it is
larger than adding the contract types.

Note the existing per-request precedent to follow rather than invent:
`request.state.branding` and `request.state.display` are already resolved once
per request and memoized, warmed in `require_web_auth`.

### 4b. Guards

| Item | Location | Change |
|---|---|---|
| Role hardcode | `web_deps.py:151` — `if "admin" not in roles`, inside `require_web_auth` (declared `:93`) | moves out of authentication into a declared permission at layer 2 |
| Phase-3 TODO | `web_deps.py` docstring `:105` | discharged by the refactor |
| Branding/display warming | `web_deps.py` (call site 1 of 3) | must survive the split — it covers the whole authenticated portal in one seam |
| `authorize_party` | `deps.py:159` | reused unchanged — takes an established party, reads no cookie/header |
| `permission_guard` | `deps.py:216` | reused unchanged — the route-level factory both flows go through |
| `require_permission` | `deps.py:273` | unchanged; already `permission_guard` bound to the bearer flow |

`docs/ARCHITECTURE.md` § "The permission seam is authentication-neutral"
(kernel `0.1.0a62`) states the seam exists and that `require_web_auth`'s
hardcoded `"admin"` "is UNCHANGED and remains the phase-3 item its docstring
records — this seam is what a fix will be built on, not the fix itself."
**The prerequisite is already in place**; separate portal-role work is not needed
before facets.

Call-site count: `require_web_auth` appears in **6 assembly `web.py` files**
(`auth`, `parties`, `rbac`, `settings`, `custom_fields`, `web`), **1 module**
(`dotmac_template_studio/web.py`), and 6 kernel modules. 8 `web.py` files exist
in total across the assembly and packages.

### 4c. Compatibility

`ModuleManifest` declares `contract_version` (kernel module contract) but nothing
about the UI contract, despite ADR-0006 § 1's two-axis ruling. `dotmac_ui`
already owns `UI_CONTRACT_VERSION`, `SUPPORTED_UI_CONTRACT_VERSIONS` and encodes
the contract in the stylesheet filename (`dotmac-ui-<N>.css`), so contract 2 can
ship beside contract 1. The missing half is a module-side declaration and a
startup check.

### 4d. Governance scope — and a live sensitivity lesson

`tests/architecture/test_web_conventions.py` scans a hand-maintained
`TEMPLATE_ROOTS` list (kernel templates + Template Studio templates) with globs
`admin/**/*.html`, `auth/*.html`, `platform/**/*.html`. The non-admin route sweep
is scoped to the `/admin` prefix. The file documents its own limitation: "A
future non-admin portal surface must extend `_admin_and_auth_templates()` and the
sweep prefix accordingly."

The same file records a past failure that is directly predictive here:

> the constant was `PROJECT_ROOT / "templates"`, which stopped existing when the
> templates moved into the kernel package, and four checks went on passing

A facet contract multiplies both template roots and path prefixes. Under
ADR-0018 the extended guard ships with a sensitivity proof, or it is not a guard.

Current surface: 42 kernel templates across `admin`, `auth`, `platform`,
`components`, `layouts`, `errors`.

### 4e. Navigation

`NavItem` is `label` / `path` / registry-stamped `feature`.
`test_nav_items_paths_exist_in_web_routers` proves the path resolves within the
declaring manifest's own `web_routers`. Once an assembly owns the mount prefix, a
module-authored absolute path is wrong by construction — the module cannot know
where it was mounted. Hence route-name navigation.

## 5. Browser security as built

Measured directly from `packages/dotmac-kernel/src/dotmac_kernel/middleware/csrf.py`
and `packages/dotmac-kernel/src/dotmac_kernel/static/js/csrf.js`.

**Prefix-independent, which is the part that is already right.** `CSRFMiddleware`
is installed application-wide at `app_factory.py:575`
(`app.add_middleware(CSRFMiddleware, enabled=settings.csrf_enabled)`) and never
inspects the path. A new facet prefix does **not** silently escape it.

**The trigger is the defect.** The unsafe-method branch reads:

```python
if request.method not in SAFE_METHODS:
    cookie_token = request.cookies.get(CSRF_COOKIE)
    header_token = request.headers.get(CSRF_HEADER)
    if request.cookies and not _valid_csrf_token(cookie_token, header_token):
        ... 403
```

`request.cookies and ...` is the bearer-API accommodation, and it makes the check
conditional on the request having carried *some* cookie. An unsafe request with
**no cookies at all is not checked**. Under `SameSite=lax` a cross-site POST sends
no cookies, so a cross-site `POST /admin/login` — a pre-auth mutation that
establishes a session — reaches the handler with no CSRF proof demanded. Login
CSRF is a recognised class in its own right; `SameSite` is defence in depth, not
a substitute for a token.

Other as-built properties:

| Property | Value |
|---|---|
| Pattern | naive double-submit — `csrf_token` cookie vs `x-csrf-token` header |
| Comparison | `hmac.compare_digest` — constant-time ✓ |
| Token | `secrets.token_urlsafe(32)`, **not signed, not session-bound** |
| Cookie | `Path=/; SameSite=lax`, `Secure` only when `request.url.scheme == "https"` |
| Cookie prefix | host-only (no `Domain`), but **no `__Host-` prefix** |
| Issued | on a safe method when the cookie is absent |
| Disable switch | `settings.csrf_enabled` (default `True`) |

`static/js/csrf.js` bridges the cookie onto the header for htmx
(`htmx:configRequest`) and monkey-patches `fetch`. Its own docstring states what
it cannot do: "Plain `<form method="post">` submits … A bare method="post" form
will 403". Note the file's docstring still names `app.core.middleware.csrf`, a
path that no longer exists — minor drift, recorded rather than fixed here.

### 5a. Mutation transport is already divergent across the fleet

| Product | Transport |
|---|---|
| Starter | htmx/`fetch` header bridge; native `method="post"` banned by `test_web_conventions.py` under the admin/auth/platform globs |
| `dotmac_sub` | **488** `method="post"` occurrences across **228** template files; **194** `csrf_token` references in templates (hidden-input pattern) |

Measured by `grep -rno` over each repository's `templates/` tree at the commits
above; the counts are occurrences and files, not audited forms.

**Consequence for the contract.** A reusable module screen cannot silently assume
one product's bridge. If the framework's CSRF contract supports only a header,
every Sub surface must be rewritten to adopt a module screen; if it supports only
a hidden input, the starter's htmx surfaces must change. The server-side
validator has to accept both, or htmx becomes an accidental invariant of the
whole framework.

## 6. Native mobile — what exists, and why nothing is extractable yet

From `docs/inventories/sub-surfaces.md` (a dated inventory, not re-audited here)
and confirmed present on disk:

- `dotmac_sub/mobile/` — Flutter **customer self-care**, `pubspec.yaml`
  `name: dotmac_portal`.
- `dotmac_sub/field_mobile/` — Flutter **field technician / vendor**,
  `name: dotmac_field`.
- Both take branding from the repo-root `brand.json` via
  `flutter build --dart-define-from-file=../brand.json` — a **build-time** brand
  flow, categorically different from the runtime tenant branding the web facets
  resolve per request.
- A brand-driven PWA manifest is served at `GET /branding/manifest.webmanifest`;
  there is **no service worker** — no `sw.js`, no `serviceWorker` registration
  anywhere in the tree.

**Why this is not yet an extraction candidate.** Both applications live in *one
product*. ADR-0006's 2026-08-12 amendment requires two *independent* products
before a presentation contract is `reuse-proven`, and the 2026-08-13 amendment
adds that consumption by the owning assembly is reference proof that never closes
the gate. Two apps in one repository can establish candidate semantics and
nothing more.

No source-and-test audit of either application has been performed. This section
records what is *present*; it is explicitly not the product-first inventory that
a shared mobile package would require.

## 7. Migration surface

Template Studio is the **only** installable module contributing
`web_routers`/`nav` (`manifest.py:45-46`, `NavItem("Templates", "/admin/templates")`).
Of 91 `packages/dotmac-*` distributions with a `src/` tree, three ship a
`templates/` directory: `dotmac-kernel`, `dotmac-template-studio`, `dotmac-ui`.

That makes Template Studio the whole canary and the whole risk. The six assembly
`web.py` files migrate behind the `staff_admin` compatibility adapter.

`dotmac-billing` is fully headless today (`authority`, `commands`, `contracts`,
`engine`, `service`, `linking`, `models` — no `router.py`, no `web.py`), so any
billing surface is a port from the product that already runs one, never a
greenfield invention.

## 8. What this dossier does not establish

- Which products adopt facets, in what order, or on what schedule.
- Whether any module beyond Template Studio should grow a surface.
- The portal role taxonomy — deliberately out of scope; the amendment rules that
  designing roles before real facets establish the required permissions inverts
  the order.
- Any claim that Sub's or ERP's patterns are *correct*; they are recorded as what
  exists, and their divergence is the finding.
- Any audit of the two Flutter applications. § 6 records that they are present
  and how they are branded; no source, test, dependency or platform review has
  been done, and none of it is the product-first inventory a shared mobile
  package would need.
- Any real-browser exploitability claim about § 5. This inventory did not craft
  a request. `tests/unit/test_csrf_contract.py` now carries request-level
  canaries for the no-cookie pre-auth case, both proof transports, tampering,
  cross-site provenance and session binding; a full browser login journey is
  still separate evidence.
- Whether `settings.csrf_enabled` is ever false in a deployed configuration.
