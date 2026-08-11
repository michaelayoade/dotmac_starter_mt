# UI surface inventory (F0)

**Status:** Evidence, not a decision. **Date:** 2026-08-11.
**For:** the white-label foundation programme, U1–U3.
**Method:** hand-authored CSS under each repo's `src/`, class usage counted from
`class="…"` in templates. Compiled Tailwind output is excluded — an earlier pass
that included it ranked `flex` and `text-sm` as top "components", which would
have argued for building `flex` once.

> **Correction — 2026-08-11 (afternoon).** Executing this plan against Sub
> measured two things it got wrong. Both are recorded in
> [§ "Correction: only ERP runs its copy"](#correction-only-erp-runs-its-copy)
> below and in `dotmac_sub`'s ADR-0010. **Read that section before acting on
> "Recommended order" step 3 or 4** — as written, step 3 promotes files that
> only one product runs, which is the extraction the ADR-0006 § 5 gate exists to
> refuse. Everything above and below it still stands as measurement.

## The finding that changes the plan

`dotmac_erp` and `dotmac_sub` do not have independent design systems. They have
**one system, forked once**: an identical 23-file tree under `src/css/`, same
paths, same structure.

- **3,263 lines** (erp), **814 differing** — **24% drift**
- **8 of 23 files are byte-identical**, including `layout/_app-shell.css` at 137 lines
- `base/_tokens.css` has drifted **62 of 130 lines** — the design tokens themselves are ~48% divergent

So U1 is not "design a component library". Most of it already exists, in
duplicate, and is drifting. The work is **promotion and reconciliation**, which
is cheaper and lower-risk than design from scratch — and it is build-once in the
strongest sense: the second copy stops existing.

`dotmac_academy_app` is the exception: 48 hand-authored classes, its own
vocabulary, no shared ancestry. `dotmac_vendor_control_plane` has **no templates
at all** and is not a UI consumer.

## Drift, file by file

Ordered by divergence. The top five are where design decisions have to be made;
the eight zeros can be promoted as-is.

| file | erp | sub | differing lines |
| --- | ---: | ---: | ---: |
| `components/_dashboard.css` | 537 | 552 | 439 |
| `components/_badges.css` | 110 | 119 | 83 |
| `base/_tokens.css` | 130 | 130 | 62 |
| `components/_tables.css` | 125 | 178 | 57 |
| `components/_buttons.css` | 141 | 108 | 53 |
| `utilities/_animations.css` | 123 | 130 | 31 |
| `layout/_responsive.css` | 354 | 325 | 29 |
| `components/_workflow.css` | 142 | 124 | 24 |
| `components/_navigation.css` | 109 | 97 | 12 |
| `components/_forms.css` | 189 | 182 | 11 |
| `components/_cards.css` | 47 | 47 | 4 |
| `utilities/_helpers.css` | 85 | 85 | 4 |
| `base/_base.css` | 66 | 66 | 2 |
| `main.css` | 34 | 34 | 2 |
| `components/_documents.css` | 272 | 271 | 1 |
| `base/_backgrounds.css` | 93 | 93 | 0 |
| `components/_bulk-selection.css` | 87 | 87 | 0 |
| `components/_command-palette.css` | 53 | 53 | 0 |
| `components/_empty-states.css` | 82 | 82 | 0 |
| `components/_loading.css` | 72 | 72 | 0 |
| `layout/_app-shell.css` | 137 | 137 | 0 |
| `utilities/_print.css` | 79 | 79 | 0 |
| `utilities/_touch.css` | 196 | 196 | 0 |

## Consumers, weighted by use

| repo | templates | classes defined | used in templates | total uses |
| --- | ---: | ---: | ---: | ---: |
| `dotmac_erp` | 865 | 1,164 | 875 | 146,152 |
| `dotmac_sub` | 753 | 452 | 72 | 1,602 |
| `dotmac_academy_app` | ~60 | 48 | 36 | 312 |
| `dotmac_vendor_control_plane` | 0 | 0 | — | — |

Two cautions on reading this. erp's totals include compiled utilities, so its
875 is an upper bound on *components*. And sub defines 452 but uses only 72 in
templates — the remainder is either dead or driven from JavaScript, and should
be established before any of it is promoted.

## Buttons: 33 names, zero shared by all three

| repo | `btn-*` names | unique to it |
| --- | ---: | ---: |
| `dotmac_erp` | 19 | 15 |
| `dotmac_sub` | 13 | 12 |
| `dotmac_academy_app` | 5 | 2 |
| **in all three** | **0** | |

`components/_buttons.css` is also the most drifted component file (53 lines,
erp 141 vs sub 108), so the divergence is real rather than cosmetic.

Several of these names are not variants at all: `btn-hide-on-load`, `btn-fit`,
`btn-measure`, `btn-load`. They encode **loading state and sizing**, which the
system does not express, so each product invented a class instead. Those should
disappear into properties rather than being ported — the 33 collapses to roughly
five semantic variants plus state and size modifiers.

## The boundary the system must not cross

Academy's 48 classes split cleanly, and the same line applies to the others:

- **Platform furniture (~8)** — `btn`, `btn-primary` (54 uses), `btn-ghost` (53),
  `card` (135), `shell-navlink`, `shell-tab`, `shell-usermenu`.
- **Product domain (~40)** — the `coursework-*` family (20 classes),
  `subtopic-*`, `prose`, `lightbox`, `disclosure`, `code-copy`,
  `heading-anchor`.

Coursework chapter navigation is not platform furniture. A UI system that
absorbs it has not become more reusable; it has acquired a product's domain.

## What `dotmac-ui` 0.1.0a1 already provides

Measured, not assumed: **190 tokens** across 13 categories, a real dark theme
(`[data-dmui-theme="dark"]`, **189 tokens with distinct light/dark values**),
`stylesheet_url()`/`asset_digest()` for content-hashed assets, and
`contrast_ratio()`/`check_contrast()` against declared WCAG requirements.

That is already a superset of what the two drifted `base/_tokens.css` copies do,
and it makes the token half of U3 available immediately.

**What it lacks:** it is unpublished (local path dependency, zero consumers), it
ships no Tailwind preset, and no theme bootstrap. Its own docstring places the
component library in "later slices".

## Recommended order

1. **Publish `dotmac-ui`**, add a Tailwind preset and a packaged theme bootstrap.
   Those three are what let any assembly adopt tokens without rewriting a config
   or hand-rolling the pre-paint script. `dotmac_academy_app` PR #57 currently
   hand-rolls both, in four places, under a different attribute name.
2. **Migrate the token layer first**, in all three consumers. It retires two
   drifted `_tokens.css` copies and academy's palette, and it is the piece
   `dotmac-ui` can already satisfy today.
3. **Promote the 8 byte-identical files as-is.** ⚠️ **Superseded — see the
   correction below.** Sub does not run its copy, so this promotes a
   single-consumer file set. Re-base it on the live trees first.
4. **Reconcile the drifted five** — `_dashboard`, `_badges`, `_tokens`,
   `_tables`, `_buttons` — each as a deliberate decision recorded with the
   promotion, not a merge. ⚠️ Same correction: in Sub these five are dead code,
   so four of the five reconcile a live file against an abandoned one.
5. **Then** design what nothing has: the missing state and size primitives that
   `btn-load` and `btn-fit` are standing in for.

Step 5 is the only part that is design from scratch. Steps 1–4 are removing
duplicates that already exist, which is where the build-once return is.

## Correction: only ERP runs its copy

**Measured 2026-08-11 at sub `1f41538e2`, erp `1e6b3270`, while executing step 2
in `dotmac_sub` (PR #2296 / that repo's ADR-0010).**

### Sub's `src/css/` tree is dead code

Nothing builds it:

- `package.json`'s `css:build` compiles `static/css/src/main.css` — **not**
  `src/css/main.css`.
- No Dockerfile, workflow, Makefile or module references `src/css`.
- The compiled `static/css/main.css` contains **zero** `--ink` and **zero**
  `--parchment`, the tokens that tree defines.
- Last commit: **2026-02-16**, the commit that added it.

ERP's identical tree is live: `build:css` compiles it, both Dockerfiles `COPY
src/css`, the compiled `static/css/app.css` carries `--ink` and 35 `parchment`
references, last touched **2026-07-24**.

So "one system, forked once" is accurate as file archaeology and **misleading
about running code**. A large share of the measured 24% drift is Sub's copy
fossilising while ERP's kept moving. This is the "promoting dead CSS would be
the same mistake in a new package" risk in § "What this inventory does not
settle" — now answered, in the direction the question feared.

**Sub's live token surface** is `static/css/design-system.css` (353 lines) plus
the `@theme` block in `static/css/src/main.css` (Tailwind v4, CSS-first).

This repo already recorded the corroborating fact and nobody read it as one:
this README's scale table lists Sub's CSS toolchain as **Tailwind v4 CSS-first**
and ERP's as **Tailwind v3.4.19 + JS config**. The shared `src/css/` tree is
v3-era. Sub did not merely stop editing its copy — it moved to a different
Tailwind major and left the fork behind, which is why the drift is one-sided.
A shared file tree spanning two Tailwind majors was never going to be promoted
as-is regardless of how many lines happened to match.

### Sub's live tokens share the package's role names, not its values

`design-system.css` already uses `dotmac-ui`'s role vocabulary verbatim and
unprefixed — which is not coincidence: `COMPATIBILITY.md` § "Where the
vocabulary came from" records that the package took it from this file, and
`contract.py` explains the `--dmui-` prefix exists so the two cannot collide.

Comparing all 85 by resolved value: **6 identical, 79 different.** The package
shipped Tailwind's default ramps under Sub's role names. Sub's brand is green
(`#367920`); the package's is blue (`#3b82f6`). Sub's neutrals are warm
(`#596678`); the package's are slate (`#64748b`).

**The vocabulary is already shared. Only the palette is not** — so "migrate the
token layer" is a palette decision, not a mechanical alias, and adopting the
package's values would repaint every Sub page. The choice is open in
`dotmac_sub` ADR-0010 § 3: re-declare the `--dmui-*` ramps with Sub's values
(the override path `COMPATIBILITY.md` sanctions — one vocabulary, zero visual
change), or adopt the package's ramps (one fleet palette, repaints Sub).

### Method note for anyone re-running this

Compare tokens by **resolved value inside a category**, never by name alone. A
first pass here matched on value across all categories and "found"
`--radius-2xl → --dmui-font-size-base`, because both are `1rem` — nonsense that
renders correctly until someone rescales type. Constrain candidates to the same
family, resolve `var()` chains on both sides, and normalise units (`4px` ≡
`0.25rem`) and hex casing before comparing. Check **both** themes.

## What this inventory does not settle

- Whether sub's 380 defined-but-unused classes are dead or JavaScript-driven.
  Promoting dead CSS would be the same mistake in a new package.
- Whether erp's component count survives separating hand-authored classes from
  its compiled Tailwind layer. The 875 figure is an upper bound.
- Which product owns a shared-looking class when the two copies disagree
  semantically rather than cosmetically. `_dashboard.css` at 439 differing lines
  is unlikely to be one component wearing two skins.
