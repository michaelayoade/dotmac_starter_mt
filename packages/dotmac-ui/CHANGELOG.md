# Changelog — dotmac-ui

All notable changes to the `dotmac-ui` distribution. This package follows
[Semantic Versioning](https://semver.org); see `COMPATIBILITY.md` for the
public-surface stability policy and for the **UI contract version**, which is a
separate axis from the package version. Pre-1.0 (`0.x`, incl. this alpha) the
surface is still settling — a `0.MINOR` bump may carry breaking changes, each
called out here.

## Unreleased

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
