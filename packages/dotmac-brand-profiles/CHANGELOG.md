# Changelog — dotmac-brand-profiles

All notable changes to the `dotmac-brand-profiles` distribution. Pre-1.0 the
surface is still settling — a `0.MINOR` bump may carry breaking changes.

## 0.1.0a1 — 2026-08-19

First release. Product-first extraction of Sub's `BrandProfile` (ADR-0033 § 2).

### Added

- `mod_brand`, dual-plane: `brand_profiles` (tenant, FORCEd RLS) and
  `platform_brand_profiles` + `platform_brand_host_bindings` (platform, `app_user`
  REVOKEd). Lineage root `bp_0001_brand_profiles`, plane-conditional on the
  assembly's explicit `ModulePlaneSelection`.
- Per-field precedence over a caller-supplied scope chain, with `sources`
  reporting which layer supplied each resolved field.
- Field LOCKING, with `IDENTITY_FIELDS` naming the legal/support/sender/locale set
  ADR-0006 § 3 keeps separate from display brand.
- Host → brand bindings on the platform plane, resolvable before any tenant.
- `BRAND_OVERRIDE_INPUTS` — the allowlist, asserted equal to `BrandOverride`'s
  own fields so it cannot drift when `dotmac-ui` grows.
- `translate_legacy_brand_values()` — maps a legacy record onto the allowlist and
  REPORTS every unsupported value with a typed `Disposition`, rather than
  dropping it.
- `validate_brand_values()` — validation on write, through `dotmac-ui`'s own
  parser.

### Changed from the source implementation

- `primary_color`/`secondary_color` → `primary_hex`/`accent_hex`, validated by
  `dotmac_ui.BrandOverride` rather than by a local regex. Sub's own
  `brand_theme.py` generator is superseded by `dotmac-ui`'s (ADR-0006 U1, D8).
- One record-level `source_scope` → per-field `sources`, which is what ADR-0006
  § 3 specifies.
- Field locking is new. Sub has precedence but no way to pin a field, so a
  lower-precedence layer can currently rebrand the operator's legal identity.
- Host bindings are new, and are what makes a profile selectable before a tenant
  exists — the property that stops a brand profile being a tenant setting.

### Deliberately NOT included

- **`semantic_colors`.** RULED 2026-08-19: not carried. Sub's quintet is already
  constrained (known tones, 6-digit hex, WCAG AA in both themes), so the
  objection is ownership rather than safety — `dotmac_ui.SEMANTIC_INTENTS`
  publishes those five names as tokens with built-in ramps that
  `render_brand_css` does not seed. Every affected value is REPORTED by
  `translate_legacy_brand_values()` with
  `Disposition.OWNED_BY_PUBLISHED_TOKEN`.
- **A `brand_override()` constructor.** The assembly maps; a module function
  returning one would take that job back.
- **Any CSS, open-ended token map or colour parser.** `dotmac-ui` owns all three.
- **Any file byte, certificate or signing key.**
