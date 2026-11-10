# UI build-once source observations — 2026-11-10

This bounded retrospective inventory accompanies the four UI slices introduced
at Starter `33057e1e1fb6861137cb07f855a2f2e9b95d48dd`, reviewed at
`a51f156a128482ff5ca68dc432261da7185a4048`. It repairs missing extraction
records; it is not proof that an inventory preceded that implementation.
Read-only `git ls-tree`, `git grep`, and `git show <commit>:<path>` supplied the
observations below. Working trees, production runtimes and installed artifacts
were not evaluated. No publication, adoption or local-copy retirement is claimed.

## Immutable product observations

| Repository / revision | Inspected source | Observation and disposition |
|---|---|---|
| Academy `be954744f139094e93c05ff6e23210eef1e01b31` | `app/web/pagination.py`; `templates/components/_pagination.html`; `legacy_probe/web/test_pagination.py`; `static/academy.js:105` | Initial code source for the limit/offset helper, compact page window, summary, filter-preserving GET form, and submit-on-change behavior. Existing tests cover middle-page compact range and empty collection. Calls occur in `app/web/{applications,audit,instructor,reminders_admin,teaching}.py` and matching templates. Production use and execution of the legacy-probe tests were not independently verified in this review. |
| ERP `74fa788ae562905e4c8a1fcd46616585e4bf7694` | `app/services/common/__init__.py` (`paginate`, `pagination_context`); `templates/components/macros.html:1466` | Existing typed result-to-page context, count/query execution and page-based macro with search/filter preservation and size selector. Domain query execution stays with ERP. Its page API and styling differ from Academy's offset dict contract; no second shared query executor is introduced. |
| Sub `c7707880232c9298ef89c24a1aea66d35bd3a1e6` | `app/services/common.py:65`; `templates/components/ui/list_macros.html:41`; `templates/components/ui/macros.html:859` | Existing limit/offset statement helper, ListDefinition/list_query-aware page navigation, filter/sort-preserving size form, and older page macro. The product list projection and URL owner remain outside this bounded offset presentation port. |
| ERP, same revision | `templates/base.html:721`; `app/services/htmx.py` | Existing failed-response feedback maps statuses and redirects 401 to login; response helpers emit HX-Trigger toasts. Kernel's fixed opaque failure message reuses the feedback pattern but deliberately owns no product login redirect or response-body interpretation. |
| Sub, same revision | `templates/base.html:190-215` | Existing HX-Trigger toast forwarding includes responseError. Kernel already forwards afterRequest triggers; its new fallback covers failed responses and sendError with a fixed message. Deduplication/browser acceptance remains a consumer check. |
| ERP, same revision | `templates/errors/{400,403,404,409,429,500}.html`; `templates/{forgot_password,reset_password,reset_password_required}.html` | Error and password presentation already exist in the product. These are observed related owners, not evidence of a generic assembly-independent error shell or the Kernel accept-invitation context contract. |
| Sub, same revision | `templates/errors/{400,403,404,409,500,csrf}.html`; `templates/auth/{forgot-password,reset-password}.html`; `templates/customer/auth/forgot-password.html` | Existing product error and recovery presentation. Product routes, authorization, recovery decisions and transport context remain product-owned. |
| Academy, same revision | `templates/login.html`; `static/academy.js` | Existing login and delegated change-submit listener. No forgot/reset/accept templates occur in this revision's template tree; no generic responseError listener occurs in academy.js. This is a bounded observation, not a fleet-wide absence claim. |

## Shared owners and deliberate changes

Kernel `query.pagination_context` and `templates/components/table_macros.html`
are the joint presentation owner. The original `pagination(page, total_pages,
base_url)` macro remains a separate legacy API. Academy supplies the initial
helper/macro code; invalid-input validation and an HTML ellipsis are adaptations.
The stale-offset behavior is a deliberate correction to that source: preserve
`offset` for the actual query, show range 0–0 and no active page when it yields
no page, anchor navigation at the last valid window, and point Previous directly
to the last valid offset for a nonempty result. Empty offset zero retains page 1;
empty positive offsets have no active page and Previous returns directly to zero.

Kernel `templates/errors/_layout.html` owns the standalone error document so a
foreign `base.html` block contract cannot hide its body. This is an existing
Kernel rendering-owner correction, not a port of an independently qualified
product shell. Kernel `templates/auth/{forgot,reset,accept}.html` extend the
existing login presentation contract using role tokens and caller-supplied form
context; route/recovery/authorization decisions remain with the product. These
are existing-owner presentation extensions after the observed related product
surfaces, not claims of byte-for-byte production ports. `static/js/components.js`
owns delegated form submission and opaque request-failure toast behavior.

## Parity and qualification gates

- `tests/unit/test_query.py` preserves the Academy compact-window and empty
  cases and covers last page, stale offsets equal to/greater than total, far
  offsets, empty positive offsets, preserved SQL query offsets, and invalid
  inputs. `tests/unit/test_pagination_macro.py` covers the rendered summary,
  valid page links, no false active page, Previous recovery and filter retention.
- `tests/unit/test_errors.py::test_html_error_page_renders_body_when_assembly_base_lacks_body_block`
  covers the independent error shell; `tests/unit/test_auth_templates.py`
  covers each caller-context form. These are authored tests, not execution receipts.
- Hosted CI must qualify these against the final head/base. Static parsing is
  not runtime parity or browser evidence. JS change submission/failure feedback,
  product composition and installed-artifact acceptance remain qualification gates.

## First cutover and retirement

Academy is the intended first consumer, not a completed adopter recorded here.
Before removing its pagination helper/macro or delegated listener, pin and verify
the released Kernel artifact, compose the public helper/macro/assets, run the
preserved parity cases and real browser filter/page-size/empty/stale navigation,
and prove the local definitions are gone at an immutable consumer revision.
ERP/Sub keep their existing owners until their own explicit adapter/parity and
installed-artifact cutover; a source observation does not retire their code.
Auth and error local templates retire only after product context/route contracts
and the foreign-shell/error-body canary pass against the installed artifact.
No version, release authorization or adoption disposition is allocated here.


## Per-render product filters — source amendment before implementation

Added 2026-11-10 before the render-filter extension. Academy
`c9cc6cb12c28992106e9a6b7324df6d6478a71b9:app/web/templating.py` constructs a
product `Jinja2Templates` with `nav_context`, registers `localtime` from
`app/services/localtime.py`, and defines/registers `datefmt` and `elapsed`.
Actual caller examples are `templates/learn/home.html` (localtime/datefmt),
`templates/labs/_operation.html` and `templates/labs/_status.html`
(localtime/elapsed), and `templates/admin/reminders.html` (localtime).
These observations were retrieved with read-only `git show` and `git grep` from
the sender-hold object repository; C9 is source evidence, not a claim that this
revision or the extension is in production.

At the existing ERP `74fa788ae562905e4c8a1fcd46616585e4bf7694` baseline,
`app/templates.py` constructs one product template environment and registers
`format_currency`, `format_number`, `format_date`, `format_datetime`,
`urldecode`, `sanitize_html` and `nl2br`. At the existing Sub
`c7707880232c9298ef89c24a1aea66d35bd3a1e6` baseline, `app/timezone.py`
installs a class-level TemplateResponse patch to localize context, while
`app/services/web_auth.py` and other product web services construct environments.
The inventory does not port these lifecycle/global mutation mechanisms.

`render(..., filters=...)` is NEW design in the existing Kernel templating
owner, not a production port or a new rendering/runtime owner. It snapshots and
validates product filter names/callables, refuses collisions with canonical
filters (including Jinja builtins), and for a nonempty mapping makes an ephemeral
environment overlay with private filter/global dictionaries and no bytecode
cache. It retains the canonical loader and autoescape policy, and uses the
public `Jinja2Templates(env=..., context_processors=...)` adapter to preserve
TemplateResponse metadata and context processing. No environment getter,
persistent overlay cache or public singleton is added. Product filter logic,
branding/nav/context facades and product resource policy remain product-owned.

Source compatibility was inspected without importing applications: the lock
pins Starlette 0.46.2, and both its installed `templating.py` and cached 0.37.2
source/metadata expose the public `env=` constructor. Kernel's FastAPI floor is
`>=0.111`; no dependency floor or version is allocated by this extension.

`compose_templates` still changes ONE process-global composition; the last
composition wins. Render-local filter isolation is not concurrent isolation for
different applications' template loaders. None/empty filter calls preserve the
existing canonical rendering path. Hosted tests in `tests/unit/test_templating.py`
cover real shared-macro/product include/import composition, alternating filter
implementations, snapshotting, canonical and shared-bytecode-cache isolation,
invalid/reserved mappings, autoescape, context processors and response metadata,
CSRF/branding/surface propagation and sequential namespace/reset behavior.
Tests are authored, not executed locally. Hosted final-head/base acceptance,
verified installed release and Academy public-render cutover remain pending;
no adoption or product environment retirement is claimed.


The consumer also exposed an existing bare-request rendering defect:
Starlette `Request.app` indexes `scope["app"]` and raises `KeyError` for direct
handler test requests without an application. The same owner now reads the
optional application through public request scope when selecting the default
surface. Normal FastAPI scopes preserve their default surface; a bare request
uses the existing empty-surface fallback. A dedicated hosted canary covers this
bounded exception to preserving the old no-filter rendering path.
