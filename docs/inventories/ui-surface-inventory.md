# UI surface inventory (F0)

**Status:** Evidence, not a decision. **Date:** 2026-08-11.
**For:** the white-label foundation programme, U1–U3.
**Method:** hand-authored CSS under each repo's `src/`, class usage counted from
`class="…"` in templates. Compiled Tailwind output is excluded — an earlier pass
that included it ranked `flex` and `text-sm` as top "components", which would
have argued for building `flex` once.

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
3. **Promote the 8 byte-identical files as-is.** No design decisions required;
   the second copy simply stops existing.
4. **Reconcile the drifted five** — `_dashboard`, `_badges`, `_tokens`,
   `_tables`, `_buttons` — each as a deliberate decision recorded with the
   promotion, not a merge.
5. **Then** design what nothing has: the missing state and size primitives that
   `btn-load` and `btn-fit` are standing in for.

Step 5 is the only part that is design from scratch. Steps 1–4 are removing
duplicates that already exist, which is where the build-once return is.

## What this inventory does not settle

- Whether sub's 380 defined-but-unused classes are dead or JavaScript-driven.
  Promoting dead CSS would be the same mistake in a new package.
- Whether erp's component count survives separating hand-authored classes from
  its compiled Tailwind layer. The 875 figure is an upper bound.
- Which product owns a shared-looking class when the two copies disagree
  semantically rather than cosmetically. `_dashboard.css` at 439 differing lines
  is unlikely to be one component wearing two skins.
