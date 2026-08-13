# dotmac-ui — compatibility & public API

This document defines the **supported public surface** of `dotmac-ui` and the
stability guarantees around it. The authoritative machine-readable manifest is
`dotmac_ui.__init__` (`SUPPORTED_MODULES`, `INTERNAL_MODULES`, the curated
top-level `__all__`, and each supported module's own `__all__`); this document is
its prose companion. The governance test
`tests/architecture/test_ui_public_surface.py` enforces that the reference
assembly imports only what is documented here, and that the package itself never
imports outside its boundary.

The current package version is the `[tool.poetry] version` in this package's
`pyproject.toml` (pre-release `0.x` alphas; per-release notes in `CHANGELOG.md`).
The **UI contract version** is a different thing entirely — see below.

## Two version axes

ADR-0006 § 1 gives a module two independent version declarations:

| Axis | Owner | Constant | What it gates |
|---|---|---|---|
| Module contract | `dotmac-kernel` | `KERNEL_MODULE_CONTRACT_VERSION` | whether a module's manifest can be loaded |
| **UI contract** | **`dotmac-ui`** | **`UI_CONTRACT_VERSION`** | **what a module may assume about tokens, classes, and asset paths** |

They evolve **independently**, and neither is derived from the other. A UI
component-library revision must not force every module in the fleet to
re-declare its capability contract; a kernel manifest revision must not
invalidate a module's rendering assumptions. `dotmac_ui` imports nothing from
`dotmac_kernel` at all, so the two cannot be accidentally coupled — the
architecture guard proves the mechanism, and
`test_the_ui_contract_version_is_owned_by_this_package_alone` proves the intent.

`UI_CONTRACT_VERSION` is bumped **only** for a change a consumer can observe in
the *stable* surface below: removing or renaming a published token, changing what
a published token role MEANS, or removing a published component class or data
attribute. Adding a token, changing a token's VALUE, or publishing a new class is
additive and does not bump it. The contract version is also the artifact's
identity — it is in the stylesheet's filename — so contract 2 can ship beside
contract 1 rather than replacing it under a pinned consumer's feet.

## What is public

A name is public **only** if it is either:

- in the curated top-level `dotmac_ui.__all__` (`from dotmac_ui import X`), or
- in the `__all__` of a module listed in `SUPPORTED_MODULES`
  (`from dotmac_ui.<module> import X`).

Everything else — any module not in `SUPPORTED_MODULES` (today: `dotmac_ui.build`,
the asset generator), any name not in a supported module's `__all__`, and every
underscore-prefixed name — is **private and may change or disappear without a
deprecation cycle**.

### Supported modules and their public names

| Module | Public names |
|---|---|
| `dotmac_ui.contract` | `UI_CONTRACT_VERSION`, `SUPPORTED_UI_CONTRACT_VERSIONS`, `TOKEN_PREFIX`, `CLASS_PREFIX`, `DATA_ATTRIBUTE_PREFIX`, `PUBLISHED_COMPONENT_CLASSES`, `THEME_ATTRIBUTE`, `DARK_THEME_SELECTORS`, `ACCESSIBILITY_TARGET` |
| `dotmac_ui.tokens` | `DesignToken`, `TOKENS`, `TOKENS_BY_NAME`, `CATEGORIES`, `MODES`, `RAMP_STEPS`, `SEMANTIC_INTENTS`, `ACTION_INTENTS`, `ACTION_STATES`, `REDUCED_MOTION_DURATION`, `token`, `tokens_in`, `token_names`, `variable_names`, `css_variable`, `resolve_color`, `reference_target`, `declarations`, `iter_categories`, `COLOUR_CATEGORIES`, `CHANNEL_SUFFIX`, `colour_tokens`, `channel_variable` |
| `dotmac_ui.theme` | `bootstrap_script`, `set_theme_script`, `THEME_STORAGE_KEY`, `THEME_VALUES`, `DEFAULT_THEME` (pre-paint theme selection; returns source, not a `<script>` tag, so the host owns the CSP nonce) |
| `dotmac_ui.assets` | `static_dir`, `stylesheet_path`, `stylesheet_url`, `manifest_path`, `asset_manifest`, `asset_digest`, `STYLESHEET_RELPATH`, `MANIFEST_RELPATH`, `ASSET_NAMESPACE`, `DIGEST_LENGTH` |
| `dotmac_ui.a11y` | `ACCESSIBILITY_TARGET`, `TEXT_CONTRAST_MINIMUM`, `NON_TEXT_CONTRAST_MINIMUM`, `ContrastRequirement`, `ContrastFailure`, `CONTRAST_REQUIREMENTS`, `check_contrast`, `token_contrast`, `contrast_ratio`, `relative_luminance` |
| `dotmac_ui.components` | `template_dir`, `TEMPLATE_NAMESPACE`, `ComponentContract`, `COMPONENTS`, `EMPTY_STATE`, `component_classes` |

The whole of that surface is also re-exported at the top level, and there is no
DB-touching subset to keep out of it: `import dotmac_ui` has no side effect
beyond reading its own package data.

## The compiled-asset boundary (ADR-0006 D3)

**A consumer never runs this package through a compiler.** `dotmac-ui` may build
its own assets however it likes — today with a deterministic pure-Python
generator, tomorrow possibly with Tailwind v4 for the component layer — but what
it *publishes* is compiled CSS, **inert Jinja templates**, and the Python
contracts above. The templates change nothing about this boundary: they are DATA,
not source to preprocess, and the host's own Jinja renders them (see "The
component boundary" below). Consequences that consumers can rely on:

- **No Tailwind requirement, and no major-version agreement.** `dotmac_erp` is on
  Tailwind v3.4 with a JS config; `dotmac_sub` and `dotmac_starter_mt` are on v4
  CSS-first. All three consume the identical artifact. `test_stylesheet_needs_no_preprocessor`
  asserts the file contains no `@tailwind`, `@apply`, `@theme`, `@source`,
  `@config`, or `@layer` — nothing a preprocessor must expand. ERP migrating off
  v3 is a separate piece of work and is **not** an adoption prerequisite.
- **No npm, no bundler, no PostCSS** anywhere in a consumer's build.
- **Self-hosted and CSP-clean.** No `@import`, no CDN, no remote origin, no
  `@font-face`, no remote `url()`. Asserted by
  `test_stylesheet_references_no_external_origin`. Font *files* are deliberately
  not shipped: the family tokens default to system stacks, and a product that
  wants a brand face self-hosts it and re-declares `--dmui-font-display` /
  `--dmui-font-body`. This is what keeps the design system compatible with
  ADR-0006 D7's deny-by-default CSP and with an air-gapped profile.
- **Committed output.** The stylesheet and manifest are tracked in git, not
  gitignored build artifacts. A checkout — or a vendored copy in an offline
  bundle — has working assets with no build step. `make ui-check` (wired into
  `make check`) and `test_committed_stylesheet_matches_a_fresh_build` fail if the
  committed copy drifts from the token source, so it cannot become a fork of
  itself.

### Published assets

| Path | What |
|---|---|
| `static_dir()/dotmac-ui/dotmac-ui-1.css` | the compiled stylesheet for UI contract 1 |
| `static_dir()/dotmac-ui/manifest.json` | contract version, package version, token count, and each asset's full sha256 + byte size |
| `static_dir()/dotmac-ui/tailwind-preset.js` | the generated preset, for consumers that compile their own utilities |
| `template_dir()/dotmac_ui/components/*.html` | the published component templates — inert package data, rendered by the HOST's Jinja |

`manifest.json` exists for consumers that integrate **outside Python** — a JS
build step, an nginx asset pipeline, an air-gapped bundle verifier. Nothing in
Python needs to parse it; `asset_digest()` reads the file directly.

### How a consumer adopts it

Python consumer (the reference assembly is the worked example — see
`app/assembly.py`, which is the entire integration):

```python
import dotmac_ui

spec = ProductAssemblySpec(
    ...,
    packaged_static_dirs=(dotmac_ui.static_dir(),),   # serve it
    stylesheets=(dotmac_ui.stylesheet_url(),),        # link it
)
```

Non-kernel consumer, e.g. **`dotmac_erp` on Tailwind v3** — the case D3 exists
for. ERP has adopted no kernel, mounts its own static, and builds its CSS with a
v3 JS config. It does not change any of that:

1. `pip install dotmac-ui` (it has **no dependencies**, so this pulls in nothing
   and forces no version resolution against ERP's existing pins);
2. either mount `dotmac_ui.static_dir()` under an existing static route, or copy
   `dotmac_ui.stylesheet_path()` into the asset output directory its v3 build
   already produces — the file is inert plain CSS, so passing it *through* a v3
   pipeline is also safe but pointless;
3. add one `<link rel="stylesheet" href="…/dotmac-ui/dotmac-ui-1.css?v=…">` after
   its own stylesheet, using `dotmac_ui.stylesheet_url(mount=...)` for the URL
   and cache-busting token;
4. start authoring against `var(--dmui-*)`. ERP's existing value-named tokens
   (`--teal`, `--gold`) keep working untouched — the `--dmui-` prefix exists
   precisely so nothing collides and migration can be incremental, file by file,
   with no flag day.

Nothing in that list requires ERP to change its Tailwind major, its build, or its
kernel posture.

## The token vocabulary — what is stable

**Stable:** every token NAME in `dotmac_ui.token_names()`, its category, its
ROLE (what it is for), the `--dmui-` prefix, the ramp step scale, the dark-mode
selectors, and the asset paths above.

**Not stable:** every token VALUE. Values are placeholders that a resolved
`BrandProfile` overrides at runtime (U2), and a value change is a PATCH. Do not
pin a colour; pin the role.

Component classes are stable **only where a contract declares them**. Since
0.1.0a5 `PUBLISHED_COMPONENT_CLASSES` is derived from
`dotmac_ui.components.COMPONENTS` and covers exactly the components listed under
"The component boundary" below; every other `.dmui-*` name remains reserved and
unpublished, and `test_no_component_class_is_published_without_its_contract`
fails the build if a `.dmui-*` selector appears in the stylesheet without being
declared there. This is ADR-0006 § 5 still in force, and the amended ADR is precise about what
two consumers do: **two independent consumers of the same CONTRACT establish
`reuse-proven`; they do not grant placement permission.** Placement is decided
by the ownership map (§ 2) and the named owner — a component belongs here
because presentation is this package's job, not because two products happened
to want it. Evidence and permission are separate questions, and conflating them
is what produces both a component nobody can adopt (waiting for consumers it
cannot get) and a component extracted because two templates look alike.

### Where the vocabulary came from

`dotmac_sub`'s `static/css/design-system.css` (90 role-named properties) is the
most advanced token layer in the fleet, and `docs/inventories/README.md` reading
1 recommends starting from it rather than inventing a scheme. It is the source
of the role words used here.

| Sub | `dotmac-ui` |
|---|---|
| `--surface-{primary,secondary,tertiary}` | `--dmui-surface-{primary,secondary,tertiary}` |
| `--text-{primary,secondary,tertiary}` | `--dmui-text-{primary,secondary,tertiary}` |
| `--border-{default,subtle}` | `--dmui-border-{default,subtle}` |
| `--color-semantic-<intent>-<step>` | `--dmui-color-semantic-<intent>-<step>` (same five intents, same 50–950 steps) |
| `--color-brand-<step>` / `--color-accent-<step>` | `--dmui-color-brand-<step>` / `--dmui-color-accent-<step>` (extended to 950) |
| `--status-{surface,border,foreground,indicator}` set inside `.status-tone-*` | `--dmui-status-<intent>-{surface,border,foreground,indicator}` at `:root` |

`dotmac_erp`'s 77 value-named tokens (`--teal`, `--gold`, `--ink`) were
deliberately **not** adopted; `test_no_token_is_named_by_value` makes that a
build failure rather than a review habit.

**Four additions**, each answering a gap the inventories named — not taste:

1. **`action-<intent>-{default,hover,pressed,disabled,on}`.** Sub has *no*
   interaction/intent tokens at all, which is why every hover state in the fleet
   is a hardcoded utility class.
2. **Non-colour scales** (typography, spacing, radius, shadow, focus ring,
   breakpoints, motion). Sub has these only in `src/css/base/_tokens.css`, which
   the inventory records as git-tracked and *entirely unreferenced*.
3. **`surface-background` / `surface-elevated`.** Sub has no page-canvas or
   raised-surface role, which is why its templates reach for `bg-slate-50`.
4. **`status-<intent>-*` at `:root`.** Sub scopes the quartet inside component
   classes; a package that publishes no component classes must expose them as
   tokens so the later component layer consumes them rather than reinventing
   them.

**The `--dmui-` prefix is load-bearing, not decoration.** Tailwind v4's `@theme`
emits `--color-*`, `--font-*`, and `--spacing-*` into the consumer's own `:root`,
and every product already defines unprefixed tokens of its own. An unprefixed
`--surface-primary` from this package would collide with Sub's, and whichever
stylesheet loaded last would win.

### Categories

190 tokens in 13 categories: `color` (77 — brand, accent, and five semantic ramps
at 11 steps each), `typography` (22), `action` (20), `status` (20), `space` (9),
`radius` (7), `motion` (7), `breakpoint` (6), `surface` (5), `text` (5), `shadow`
(5), `border` (4), `focus` (3).

### The channel form

Every colour token also publishes a **channel** variable holding space-separated
components rather than a complete colour:

    --dmui-color-brand-500:      #3b82f6
    --dmui-color-brand-500-rgb:  59 130 246

Both are public and both are restated for dark mode. The channel form exists
because Tailwind can only synthesise alpha from separate components, so
`bg-brand-500/50` compiles to `rgb(var(--dmui-color-brand-500-rgb) / 0.5)`. A
variable holding a complete colour cannot take an opacity modifier at all — the
utility renders **opaque with no warning**, which is why 0.1.0a2 could not be
adopted by any consumer (`dotmac_sub` uses 5,872 opacity modifiers, `dotmac_erp`
4,372). Plain CSS can use the channel form the same way:
`rgba(var(--dmui-surface-primary-rgb) / 0.6)`.

`variable_names()` returns **321** names: the 190 declared tokens plus the 131
derived channel forms. Both are covered by this package's compatibility promise.

**Two mechanics worth knowing.** Colour *ramps* are mode-independent — a step is
the same colour in light and dark; what changes is which step a ROLE points at.
`render_brand_css()` therefore re-declares each generated ramp once and, since
0.1.0a6, re-points the channel form of every dependent semantic role in both
modes. Whole role variables already follow the ramp through `var()`; their
channel companions otherwise retain the compiled package value and make an
opacity utility disagree with its solid counterpart. Unrelated role channels
are not restated, so brand projection cannot overwrite a trusted theme outside
the allowed brand and accent ramps.

*Breakpoint* tokens are declared values only: CSS custom properties cannot be
read inside a media query's condition, so a consumer mirrors them in its own
`@media`/container queries rather than referencing them there.

## The component boundary — inert templates, host-supplied Jinja

ADR-0006 § 2 assigns the Jinja/HTMX component library to this package, and § 5
still governs what may enter it. This section is the contract that governs
*shipping* one; it does not relax the gate for *choosing* one.

**The package stays dependency-free.** Templates ship as **inert package data**.
`dotmac_ui` does not import Jinja and does not declare it as a dependency — it
resolves a directory, and the HOST supplies the environment. That is what keeps
ERP (no kernel, Tailwind v3) and an air-gapped consumer able to adopt components
on the same terms they adopt tokens.

**Namespaced paths.** `template_dir()` returns a directory whose only child is
`dotmac_ui/`, so every published template is addressed
`dotmac_ui/components/<name>.html`. A flat `components/<name>.html` would
collide with a consumer's own `components/` tree inside one `ChoiceLoader` and
the winner would depend on layer order — a silent, order-dependent override.
`TEMPLATE_NAMESPACE` is part of the contract; a template outside it is not
published.

**Declared signatures.** Each component is a `ComponentContract` in
`dotmac_ui.components.COMPONENTS`, naming its template, its macro, its accepted
`parameters` in positional order, and every `.dmui-*` class its markup emits.
The parameter tuple is the signature: **removing a parameter, reordering the
positional ones, or changing what one means is a breaking change** and bumps
`UI_CONTRACT_VERSION`, exactly like removing a token. Adding a keyword parameter
with a default is additive and does not.

**What a component may assume: stock Jinja and nothing else.** No custom filter,
no global, no context processor, no `url_for`, no request. Every value a
component renders arrives as a macro argument. HTMX and Alpine are **not**
assumed either — published markup is static, and a consumer adds `hx-*` from the
outside. A component that needed a filter would silently render differently, or
fail, on a host that spells that filter another way.

**Styling is `.dmui-*` classes only, never utilities.** A consumer does not run
this package through Tailwind, so a template carrying `bg-slate-700` renders
unstyled anywhere the consumer's content globs do not reach into site-packages —
which is every correctly configured consumer. Component classes are declared in
`PUBLISHED_COMPONENT_CLASSES`, which is **derived** from the contracts rather
than listed separately, and every class the compiled stylesheet defines must
appear there (`test_no_component_class_is_published_without_its_contract`).

**Proofs, not promises.** Two tests hold the boundary: one asserts the templates
are present in the built **wheel** (package data that is not packaged is a
source-checkout-only feature that breaks on install), and one renders every
component on a **clean host** — a bare `jinja2.Environment` over `template_dir()`
with none of the kernel's globals or filters installed.

### Published components (UI contract 1)

| Template | Macro | Parameters | Classes |
|---|---|---|---|
| `dotmac_ui/components/empty_state.html` | `empty_state` | `message`, `action_url`, `action_label` | `dmui-empty-state`, `dmui-empty-state__icon`, `dmui-empty-state__message`, `dmui-empty-state__action` |

`empty_state` renders the "nothing to show here" panel for a list, table body or
card. In a table the **caller** owns the row and the `colspan`; the component
owns only the panel. `action_url` is optional — omit it and no action renders.

## Accessibility contract

**Target: WCAG 2.2 Level AA** for the critical journeys of any product assembled
from this system — sign-in, navigation, reading and filtering a record list,
creating/editing a record, confirming a destructive action, recovering from an
error.

That is a commitment about the design system's own output. A consumer can still
build an inaccessible page out of accessible tokens, and this package makes no
claim about that.

**What is machine-checked today** (`tests/unit/test_dotmac_ui_a11y.py`):

- **Colour contrast**, on 70 requirements — every colour pair the vocabulary
  claims will be used together, in **both** modes, against the threshold that
  applies to it: **4.5:1** for text (SC 1.4.3) and **3:1** for non-text UI
  components, graphical objects, and the focus indicator (SC 1.4.11, SC 2.4.11).
  Covered: every text role on `surface-primary`; `text-primary` on every surface;
  each action intent's label against its resting, hover, and pressed fill; each
  semantic intent's status text and status dot against its own tinted surface;
  `border-strong` and `focus-ring-color` against the surfaces they land on.
- **The requirement list itself**, because a contrast suite's real failure mode
  is that deleting a failing requirement is easier than fixing the colour. Guards
  assert every text role has a requirement, every action intent is checked in all
  three fill states, every semantic intent has both a text and an indicator
  requirement, the focus ring is checked against every surface, and every pair is
  checked in both modes.
- **The checker can fail.** A sensitivity self-test feeds it an unsatisfiable
  requirement and asserts it reports one.
- **Reduced motion** (SC 2.3.3): every duration token collapses to `1ms` under
  `prefers-reduced-motion: reduce` — 1ms rather than 0s so `transitionend`
  handlers still fire and nothing waiting on one can hang.
- **Focus appearance** (SC 2.4.11): the package ships exactly one base rule, a
  `:focus-visible` outline built from the focus-ring tokens. It is deliberately
  unlayered and at (0,1,0) specificity — inside an `@layer` it would lose to
  every unlayered rule in the host stylesheet, silently giving the promise away.
- **Relative type units**: the whole type scale is `rem`, so a browser
  text-size preference is honoured (SC 1.4.4).

**What is NOT claimed.** AA's relaxed 3:1 allowance for *large* text is used
nowhere: the type scale is a token, so the package cannot know what size a role
renders at, and assuming "large" would assume away the failure. Other AA
criteria — focus order, name/role/value, error identification, target size,
dragging alternatives — depend on **markup and composition**. This release
publishes `empty_state`; its narrow markup guarantees live with that component's
contract and tests, not in the token contrast checker. Each future component
must likewise state and prove the criteria it carries. Contrast of a consumer's
own colours and of text over imagery is outside the contract. Tenant-supplied
`custom_css` is outside the contract entirely — ADR-0006 D8 and the reference
kernel's 0.1.0a47 release retired that input rather than pretending it could be
measured or sanitized safely.

`check_contrast()` is public API for exactly this reason: a product that
re-declares brand ramps at runtime (U2) runs the same requirement set against its
own resolved palette, so "this brand is legible" is one call rather than a review
habit.

## Dependencies

**None.** `python = ">=3.11,<3.14"` and nothing else — asserted by
`test_ui_package_declares_no_runtime_dependencies`.

ADR-0006 § 2 permits `dotmac-ui → dotmac-kernel`. This release takes no such
dependency, and the import-linter contract pins the stronger fact. That is
load-bearing rather than incidental: it is what lets `dotmac_erp`, which has
adopted no kernel at all, consume the design system without adopting anything
else first. Relaxing it is a decision about ERP's adoption path.

The package also imports no web framework, no ORM, and no templating engine —
enforced by `test_ui_package_imports_nothing_it_must_not`. ADR-0006 § 2:
`dotmac-ui` "never imports a module or reads a database. It renders what it is
given."

## Versioning & deprecation policy

`dotmac-ui` follows **Semantic Versioning** for its public surface:

- **MAJOR** — a breaking change to any public name (removal, signature change,
  observable behaviour change), removing a module from `SUPPORTED_MODULES`, or
  removing/renaming a published token or component class.
- **MINOR** — additive: new tokens, new public names/modules, new optional
  parameters, a new published component class.
- **PATCH** — bug fixes and **token value changes** with no name/role change.

**Pre-1.0 (`0.x`, incl. the current alphas):** the surface is still settling; a
`0.MINOR` bump may carry breaking changes, each called out in `CHANGELOG.md`.

**Deprecation:** once past `1.0`, a public name or token is removed only after at
least one MINOR release in which it is documented as deprecated (in `CHANGELOG`
and, for tokens, kept emitting as an alias) with a stated replacement.

**Private surface:** carries no guarantee at any version. Reaching into a private
name or module is unsupported and the governance test blocks the reference
assembly from doing so.
