# Changelog — dotmac-ui

All notable changes to the `dotmac-ui` distribution. This package follows
[Semantic Versioning](https://semver.org); see `COMPATIBILITY.md` for the
public-surface stability policy and for the **UI contract version**, which is a
separate axis from the package version. Pre-1.0 (`0.x`, incl. this alpha) the
surface is still settling — a `0.MINOR` bump may carry breaking changes, each
called out here.

## 0.1.0a8 — UNRELEASED

Five additive, unadopted presentation candidates: provider-neutral map frame,
display-only catalog grid, list surface, recent activity and generic form
behaviours; `UI_CONTRACT_VERSION` stays **1**.

### Added

- `catalog_grid` and its display-only `CatalogItem` contract, extracted from
  Workspace application discovery and Academy course discovery. It renders a
  responsive token-native grid, optional media and notices, a paired action,
  and the published empty state without depending on host filters or globals.
- Accessibility requirements for secondary and tertiary text on elevated card
  surfaces.
- `list_surface`, a token-native inert renderer over the public kernel
  `ListQuery`/`PageMeta` behavior with display-only columns, cells, filters and
  pre-eligible row actions. Query execution and action eligibility stay in the
  product owner.
- `recent_activity`, a token-native display panel whose caller supplies the
  official ordered timeline, wording, authorized URLs and formatted times.
- `dotmac_ui.behaviors` plus the generated, manifested
  `dotmac-ui-behaviors-1.js`: prefixed validated-input, submit, generic
  repeatable-field and unsaved-change factories. Invoice/tax/money,
  contact-role and CSV/import logic are explicitly excluded.
- `control-min-block-size`, the shared 44 CSS px minimum for packaged controls.

### Evidence

- This slice is `audit-complete`, not adopted. Workspace and Academy remain the
  local markup/CSS owners until a later coordinated cutover; this package
  change does not alter either product or any assembly composition.

- `map_frame`, a provider-neutral inert Jinja component for an accessible map
  canvas and generic ready/loading/empty/error presentation. It accepts only a
  canvas id, accessible label and caller-owned state copy; provider runtime,
  tiles, coordinates, endpoints, polling, layers and domain vocabularies remain
  in the host.
- `map-frame-min-block-size`, the role-named, scoped override seam replacing
  product-specific fixed map heights in a future adoption. The compiled default
  is `24rem`; no viewport, device or geographic assumption enters the package.

- The map-frame slice is `audit-complete`, not adopted. Sub live-map/playback,
  CRM degraded/list behavior and ERP geofence editing are inventoried in
  `docs/inventories/map-ui-sources.md`; no product is modified or counted as a
  consumer in this change.

## 0.1.0a7 — 2026-08-13

One additive token and a pre-release component-contract repair;
`UI_CONTRACT_VERSION` stays **1**.

### Added

- `surface-overlay`, the opaque semantic colour behind a translucent scrim.
  The first real consumer is the reference assembly's mobile admin-navigation
  overlay. A button, status or raw neutral-ramp token would assign the wrong
  role; preserving `bg-black/50` would leave the shared shell outside runtime
  theme/brand governance.

### Reference adoption

- The starter's document canvas, tenant login, authenticated shell, sidebar,
  topbar and toast/status surfaces now consume semantic token roles without
  per-element light/dark colour branches.

### Changed

- Before the first component-bearing package release, `empty_state` was aligned
  with the independently used ERP/Sub/CRM core: `(title, message,
  action_label, action_url)`. It now renders a distinct title, optional message,
  and an action only when label and URL are both present.
- The richer markup remains portable: its visual and CTA icon are fixed package
  markup styled only through published `.dmui-*` classes. ERP-only aliases,
  host icon helpers, title-keyword illustration inference, raw SVG input and
  product `/static/` paths are deliberately excluded.
- The release workflow now requires the component template in the wheel and
  renders it through a clean host-supplied Jinja environment both before
  publication and after installation from the private registry.

## 0.1.0a6 — 2026-08-13

Runtime brand projection is now complete for colour-channel consumers. Patch
behavior fix; `UI_CONTRACT_VERSION` stays **1**.

### Fixed

- `render_brand_css()` now re-projects the `-rgb` variables of every semantic
  role that resolves through the generated brand/accent ramps, in both light
  and dark mode. Previously solid roles followed the override through their
  whole-colour `var()` reference while opacity utilities used the compiled
  package channels, splitting one control across the tenant and placeholder
  palettes.
- Unrelated semantic/status channel variables are not restated, so a runtime
  brand cannot overwrite trusted theme decisions outside the two allowed
  ramps.

## 0.1.0a5 — 2026-08-13

The component library opens, with one component. Additive — `UI_CONTRACT_VERSION`
stays **1**, because publishing a new component class is additive under
`COMPATIBILITY.md` § "Two version axes".

### Added

- **`dotmac_ui.components`** — the component contract surface: `template_dir()`,
  `TEMPLATE_NAMESPACE`, `ComponentContract`, `COMPONENTS`, `component_classes()`.
- **Jinja templates ship as package data**, and the package **still declares no
  dependencies**. The templates are inert: `dotmac_ui` imports no Jinja, and the
  HOST adds `template_dir()` to its own environment's search path. A consumer's
  entire wiring is one more entry in `packaged_template_dirs`.
- **`empty_state`** (`dotmac_ui/components/empty_state.html`) — the first
  component candidate. Its initial development signature was superseded before
  publication by the product-evidenced contract recorded under 0.1.0a7.
- `PUBLISHED_COMPONENT_CLASSES` is now **derived** from `COMPONENTS` rather than
  maintained as a second list, so the registry and the markup cannot disagree.

### Notes for consumers

- Templates are namespaced `dotmac_ui/…`, so adding `template_dir()` to a
  `ChoiceLoader` **cannot** shadow your own `components/` tree whatever the layer
  order.
- A published component assumes **stock Jinja only** — no custom filter, global,
  context processor, `url_for`, HTMX or Alpine. Every value arrives as a macro
  argument.
- Components are styled with `.dmui-*` classes, never utility classes, because
  you do not compile this package's templates.
- Two proofs back the above: the templates are asserted present in the built
  **wheel**, and every component is rendered on a **bare `Environment`** with
  none of a host's globals installed.

## 0.1.0a4 — 2026-08-11

Brand overrides become a generated, validated surface. Additive.

### Added
- **`dotmac_ui.brand`** — `BrandOverride(primary, accent)` in, CSS out, via
  `render_brand_css()`. The two input fields are exactly what
  `dotmac_kernel.branding` already stores, so an existing brand record maps on
  without a migration.
- **`dotmac_ui.color`** — sRGB ↔ OKLCH with a gamut clamp that holds lightness
  and hue and reports the chroma it could not show.
- `resolve_color`, `token_contrast` and `check_contrast` accept an `overrides`
  palette. `check_contrast`'s docstring already promised this use — *"a product
  that re-declares brand ramps at runtime runs this against its own resolved
  palette"* — but there was no way to pass one in, so the promise was not
  reachable.

### Why generated rather than a `:root` block per product

**0.1.0a3 made hand-written overrides unsafe.** Every colour now has two
published variables. An override setting `--dmui-color-brand-500` but not
`--dmui-color-brand-500-rgb` renders the brand on solid fills and the
*placeholder blue* on every translucent one, silently — against roughly 10,300
opacity modifiers across the fleet. Only something that knows both forms exist
can emit them consistently.

It is also the replacement ADR-0006 **D8** names for `custom_css`, which
`dotmac_kernel.branding` still accepts, regex-sanitizes, and renders `| safe`
into a `<style>` block. Output here is assembled from validated colours: there
is no path by which a URL, an `@import` or a selector could appear.

### One seed, not eleven steps

Brand data in the wild is one or two colours; the token layer's roles derive
from eleven-step ramps. The lightness, chroma and hue-drift curves are measured
from this package's own brand ramp, so **regenerating from `#3b82f6` reproduces
the built-in ramp exactly at all eleven steps** — the calibration test that
caught two errors: averaging the brand and accent lightness profiles (which
anchored the seed one step off) and holding hue constant (which oversaturated
600 and 700, the steps primary buttons use).

### Legibility is structural, not checked-and-hoped

Because lightness is pinned by the curve, a generated brand cannot fail the
contrast contract. Swept 480 seeds across the hue circle at four
lightness/chroma combinations: **zero failures**. `check_contrast` is retained
as a backstop against the curve changing, and a separate test proves it can
still fail so it is not theatre.

### Deliberately not warned about

Chroma the gamut could not show. The clamp lands on the most saturated
renderable colour at that step, so the result is the best available answer.
Seeding with this package's own accent reported six clamped steps while
producing a ramp within 0.012 chroma of the built-in one — six warnings an
operator cannot act on, which would train them past the contrast ones. The
colour they actually supplied is pinned verbatim and never clamped.

## 0.1.0a3 — 2026-08-11

Colour tokens gain a channel form, so alpha modifiers work. No API change.

### Fixed
- **`bg-brand-500/50` and every other opacity modifier now work.** 0.1.0a2
  documented this as a known limitation; it is a blocker. `dotmac_sub` uses
  5,872 opacity modifiers, `dotmac_erp` 4,372 and `dotmac_academy_app` 48 —
  roughly 10,300 across the fleet. **No consumer could have adopted the token
  layer**, and the failure was silent: Tailwind cannot synthesise alpha from a
  variable holding a complete colour, so the utility renders opaque with no
  warning.

  Every colour token now also publishes `--dmui-<name>-rgb` holding
  space-separated channels, and the preset emits
  `rgb(var(--dmui-<name>-rgb) / <alpha-value>)`. The whole-colour variables are
  unchanged, so CSS using them directly is unaffected.

  The channel forms are **restated in the dark block**. Without that,
  `bg-surface-primary/50` would keep rendering the light surface at 50% in dark
  mode — a bug that survives a screenshot review because most of the page still
  looks right.

### Why it was missed

0.1.0a2 recorded the limitation honestly and deferred it as "a token-layer
change, not a preset change". That was true and beside the point: the first
consumer examined needed it, and so does every other one. Measuring the fleet
before deferring would have caught it — see
`docs/inventories/ui-surface-inventory.md`.

## 0.1.0a2 — 2026-08-11

The two things that stood between this package and its first consumer.

### Added
- **A generated Tailwind preset**, shipped as package data
  (`dotmac_ui.assets.tailwind_preset_path()`). It maps every utility at
  `var(--dmui-*)`, so a consumer's compiled CSS contains variable references
  rather than values: one stylesheet swap re-themes it, and dark mode needs no
  `dark:` variant on any element. Generated from the same tokens as the
  stylesheet, because a hand-maintained preset is a second copy of the token
  names and a second copy drifts.
- **`dotmac_ui.theme`** — `bootstrap_script()` for pre-paint theme selection and
  `set_theme_script()` for a switcher. Returns source, not a `<script>` tag: the
  host owns the CSP nonce.

### Why

`dotmac-ui` shipped 190 tokens and a dark theme, and no assembly used it.
Adopting it still meant hand-writing a Tailwind mapping and a pre-paint script
per consumer. `dotmac_academy_app` wrote both by hand — the script four times,
under a non-matching attribute name — and `dotmac_erp` and `dotmac_sub` have two
copies of `base/_tokens.css` that are now 48% divergent. Anything a consumer must
copy will drift, so both are generated and shipped. Evidence:
`docs/inventories/ui-surface-inventory.md`.

### Known limitation

Opacity modifiers (`bg-brand-500/50`) do not work on token colours: Tailwind
needs channel components to synthesise alpha, and the tokens hold complete
colours. Fixing that means publishing channel-form tokens — a token-layer
change, not a preset change.

### Note for reviewers

`screens` deliberately emits **literal** values while every other scale emits
`var()`. `@media (min-width: var(--x))` is not valid CSS, and a preset emitting
`var()` there would compile silently and break every responsive utility.
Breakpoints are therefore fixed at build time, which is correct: a breakpoint is
a layout contract, not a brand decision.

## 0.1.0a1 — 2026-08-02

First alpha. The design-system foundation (ADR-0006 U1): the semantic token
vocabulary, the compiled-asset boundary, and the accessibility contract. **UI
contract version 1.**

Deliberately NOT in this release: the Jinja/HTMX component library, layouts, and
navigation primitives that ADR-0006 § 2 assigns to this package. U1 lays the
foundation later slices extend, and ADR-0006 § 5 forbids harvesting components
from the fleet on the grounds that they look similar.

### Added

- **`dotmac_ui.tokens` — 190 semantic design tokens in 13 categories**, named by
  ROLE, never by value: `color` (77 — brand, accent, and five semantic ramps at
  11 steps each), `typography` (22), `action` (20), `status` (20), `space` (9),
  `radius` (7), `motion` (7), `breakpoint` (6), `surface` (5), `text` (5),
  `shadow` (5), `border` (4), `focus` (3).

  The role vocabulary is `dotmac_sub`'s `design-system.css`, per the inventories'
  own recommendation (`docs/inventories/README.md` reading 1) — `surface-*`,
  `text-*`, `border-*`, `semantic-{positive,info,warning,negative,neutral}` at
  50–950, and the `status-{surface,border,foreground,indicator}` quartet.
  `dotmac_erp`'s value-named tokens were not adopted, and
  `test_no_token_is_named_by_value` makes that permanent.

  Four additions, each answering a gap the inventories named: interaction/intent
  tokens (`action-<intent>-{default,hover,pressed,disabled,on}`, of which Sub has
  none); the non-colour scales that live only in Sub's dead, unreferenced
  `src/css/base/_tokens.css`; `surface-background`/`surface-elevated`; and the
  status quartet promoted from class scope to `:root`.

  Every published property carries the `--dmui-` prefix. That is not cosmetic:
  Tailwind v4's `@theme` emits unprefixed `--color-*`/`--font-*` into the
  consumer's own `:root`, and every product already defines unprefixed tokens, so
  an unprefixed `--surface-primary` here would collide with Sub's and lose or win
  by load order.

  Token VALUES are generic placeholders carrying no product identity. ADR-0006
  § 3 requires the kernel-default brand layer to be generic; a resolved
  `BrandProfile` overrides the ramps at runtime (U2), and every role that points
  at them follows automatically.

- **`dotmac_ui.assets` — the compiled-asset boundary (ADR-0006 D3).** The
  published contract is compiled CSS (`dotmac-ui/dotmac-ui-1.css`) plus a
  machine-readable `manifest.json`, resolved by package path and served by the
  consumer from its own static mount. A consumer runs **no** Tailwind, PostCSS,
  bundler, or npm step and needs **no** particular Tailwind major:
  `dotmac_erp` (v3.4 + JS config), `dotmac_sub`, and `dotmac_starter_mt` (both v4
  CSS-first) consume the identical file. Enforced, not asserted:
  `test_stylesheet_needs_no_preprocessor` fails on any `@tailwind`, `@apply`,
  `@theme`, `@source`, `@config`, or `@layer`.

  Assets are self-hosted and CSP-clean — no `@import`, no CDN, no remote origin,
  no `@font-face`, no remote `url()` — which is what makes them compatible with
  the fleet's no-CDN standard, ADR-0006 D7's deny-by-default CSP, and an
  air-gapped profile. Font files are deliberately not shipped; the family tokens
  default to system stacks and a product self-hosts its own face.

  `stylesheet_url()` carries a content-derived `?v=` token, so cache-busting is
  the package's job rather than something each consumer must wire in.

- **`dotmac_ui.contract` — `UI_CONTRACT_VERSION`, independent of the kernel's
  module contract version** (ADR-0006 § 1's two version axes). It is in the
  artifact's filename, so a consumer pinned to contract 1 keeps resolving
  contract 1 even once contract 2 ships beside it. Also the reserved
  `dmui-`/`data-dmui-` namespaces (`PUBLISHED_COMPONENT_CLASSES` is empty at
  0.1.0a1, and a guard fails the build if a `.dmui-*` selector appears without
  being declared) and the dark-mode selectors — `.dark` alongside
  `[data-dmui-theme="dark"]`, so a host already using Tailwind's class strategy
  needs no template change.

- **`dotmac_ui.a11y` — the accessibility contract, machine-checked.** Target
  **WCAG 2.2 Level AA** for critical journeys. 70 contrast requirements cover
  every colour pair the vocabulary claims will be used together, in both modes,
  at 4.5:1 for text (SC 1.4.3) and 3:1 for non-text UI, graphical objects, and
  the focus indicator (SC 1.4.11, SC 2.4.11). A second layer of guards checks the
  requirement LIST, because a contrast suite's real failure mode is deleting the
  failing requirement, and a sensitivity self-test proves the checker can fail at
  all. `check_contrast()` is public so a product that re-declares brand ramps at
  runtime re-runs the same requirement set against its own palette.

  Reduced motion (SC 2.3.3) and focus appearance (SC 2.4.11) are shipped, not
  just documented: every duration collapses to 1ms under
  `prefers-reduced-motion`, and the package's one base rule is a
  `:focus-visible` outline built from the focus-ring tokens.

- **A deterministic, committed build.** `make ui-build` regenerates the assets
  from the token source with pure Python — no npm, hermetic, byte-identical run
  to run. The output is tracked in git rather than gitignored, because it IS the
  published contract: a reviewer sees a token change as a CSS diff, and an
  air-gapped consumer gets working assets from a checkout. `make ui-check`
  (wired into `make check`) and
  `test_committed_stylesheet_matches_a_fresh_build` fail if the committed copy
  drifts from its source.

### Dependencies

None beyond `python >=3.11,<3.14`. ADR-0006 § 2 permits `dotmac-ui →
dotmac-kernel`; this release takes no such dependency, and the import-linter
contract pins the stronger fact — it is what lets `dotmac_erp`, which has adopted
no kernel at all, consume the design system without adopting anything else first.
