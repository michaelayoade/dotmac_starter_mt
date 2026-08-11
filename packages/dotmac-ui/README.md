# dotmac-ui

The DotMac shared UI design system: **semantic design tokens**, **compiled
self-hosted assets**, and an **accessibility contract**. Later slices add the
Jinja/HTMX component library, layouts, and navigation primitives that ADR-0006
§ 2 assigns to this package.

- **Public API + stability policy:** `COMPATIBILITY.md` (authoritative manifest:
  `dotmac_ui.__init__`).
- **Release notes:** `CHANGELOG.md`.
- **The decision that governs it:** `docs/adr/0006-white-label-product-foundation.md`
  — § 1 (module/theme/brand/facet, and the two version axes), § 2 (package
  ownership and the one-way dependency), § 5 (the extraction rule), and rulings
  D3 (toolchain-agnostic to consumers) and D5 (narrow consumer boundary).

## What it is, in one paragraph

A vocabulary of 190 role-named CSS custom properties (`--dmui-surface-primary`,
`--dmui-action-destructive-hover`, `--dmui-status-warning-foreground`), compiled
into one plain stylesheet that any product can link. Each colour also publishes a
channel form (`--dmui-color-brand-500-rgb: 59 130 246`) so opacity modifiers like
`bg-brand-500/50` work — see COMPATIBILITY.md. It has **no dependencies**,
reads no database, mounts no route, and imports no kernel — so a product adopts
the design system without adopting anything else.

## Using it

```python
import dotmac_ui

dotmac_ui.static_dir()      # serve this directory from your static mount
dotmac_ui.stylesheet_url()  # -> "/static/dotmac-ui/dotmac-ui-1.css?v=<digest>"
```

Then one `<link rel="stylesheet">` at that URL, after your own stylesheet, and
author against `var(--dmui-*)`.

**There is no build step for consumers.** No Tailwind, no PostCSS, no bundler,
no npm, and no requirement to match a Tailwind major — ERP's v3.4 and the
starter's v4 consume the identical file. See COMPATIBILITY.md § "The
compiled-asset boundary" for the worked ERP-on-v3 path.

The reference assembly's entire integration is two lines in `app/assembly.py`.

## Developing it

The token vocabulary in `src/dotmac_ui/tokens.py` is the source of truth; the
stylesheet is generated from it and **committed**.

```
make ui-build   # regenerate the assets (commit the result)
make ui-check   # fail if the committed assets are stale — wired into `make check`
```

Tests live with the reference assembly: `tests/unit/test_dotmac_ui_tokens.py`,
`tests/unit/test_dotmac_ui_a11y.py`, `tests/unit/test_dotmac_ui_consumer.py`,
and the boundary guard `tests/architecture/test_ui_public_surface.py`.
