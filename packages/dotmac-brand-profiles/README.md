# dotmac-brand-profiles

The reusable owner of **brand profile data** — what lets the same released
artifact appear as Dotmac Academy and as NDIC Academy through approved profiles.

Extracted product-first from `dotmac_sub`'s `BrandProfile` (897 LOC, production)
under [ADR-0033](../../docs/adr/0033-the-vendor-control-plane-composes-existing-owners.md) § 2.
Ownership record: [`EXTRACTION.toml`](EXTRACTION.toml).

## What it is not

| Not | Owner | Consequence here |
|---|---|---|
| a design system | `dotmac-ui` (ADR-0006 U1) | no CSS column, no token map, **no colour parser** |
| a file store | `dotmac-files` (ADR-0022) | logo/icon columns are opaque refs, never dereferenced |
| a host→tenant resolver | kernel `TenantDomain` | host bindings answer host→**brand**, platform plane only |
| a mobile build pipeline | the build pipeline | `mobile_build_profile_ref` names a profile; no key, no certificate |

## The three-way boundary (Michael, 2026-08-19)

| Owner | Owns |
|---|---|
| `dotmac-ui` | token vocabulary, projection logic, contrast validation |
| `dotmac-brand-profiles` | scoped values, provenance, precedence, locks |
| the assembly | maps profile values into `dotmac_ui.BrandOverride` |

Constrained runtime brand/accent values are **permitted and intended** here. What
is not permitted is arbitrary CSS or an open-ended token map, and neither exists:
there is no CSS column, no token map and no colour parser, so **ADR-0006 D8 is
structural rather than remembered**.

The module publishes `BRAND_OVERRIDE_INPUTS` — the allowlist, asserted equal to
`BrandOverride`'s own fields — so the assembly's one-line mapping cannot drift.
It deliberately exposes no `brand_override()` constructor: returning a ready-made
override would take the assembly's job back.

## Every resolved field reports its source

```python
resolved = resolve_for_tenant(db, tenant_id=t, chain=[
    ("organization", org_id),
    ("reseller", reseller_id),
    ("tenant", None),
])
resolved.get("display_name")        # "Acme Broadband"
resolved.source_of("display_name")  # "reseller"
resolved.source_of("legal_name")    # "tenant"  ← pinned, see below
resolved.locked                     # frozenset({"legal_name", "support_email", ...})
```

ADR-0006 § 3's first safety rule. Sub carries one source for the whole record;
this generalises it **per field**, because a whole-record source cannot express
"the name came from the reseller and the legal identity from the platform".

## A lock beats precedence

```python
upsert_tenant_profile(db, UpsertTenantProfileCommand(
    ..., scope_type="tenant", lock=sorted(IDENTITY_FIELDS),
))
```

§ 3's second and third rules together: a higher layer pins a field, and
`IDENTITY_FIELDS` is exactly the legal / support / sender / locale set that must
stay separate from display brand. "Let them change the look, not who they are" is
one call rather than a convention. A lock naming a field that does not participate
in precedence is **refused**, because a lock nothing honours is worse than no lock.

## Dual-plane, with a real consumer on each side

| Plane | Consumer today | Isolation | Host bindings |
|---|---|---|---|
| tenant | **Sub** (the extraction source) | `tenant_id NOT NULL`, RLS ENABLEd **and** FORCEd, composite uniques | no — the tenant is already resolved |
| platform | **vendor control plane** (OEM) | no RLS, `app_user` REVOKEd | yes — a profile must be selectable *before* any tenant |

The asymmetry is the design. `supported_plane_sets` offers all three combinations
and the assembly makes an explicit `ModulePlaneSelection` (ADR-0028) — there is no
default. **No foreign key crosses the planes** (hard rule 27).

## Legacy values are translated, and the rest is reported

`translate_legacy_brand_values()` maps a legacy brand record onto the allowlist
and returns **both halves**:

```python
result = translate_legacy_brand_values({
    "primary_color": "#206a07",      # -> primary_hex
    "secondary_color": "#06b6d4",    # -> accent_hex
    "positive": "#15803d",           # -> unsupported
})
result.accepted        # {"primary_hex": "#206a07", "accent_hex": "#06b6d4"}
result.unsupported[0].disposition   # Disposition.OWNED_BY_PUBLISHED_TOKEN
result.is_lossless     # False
```

Sub's five-tone `semantic_colors` quintet is **not carried** — ruled 2026-08-19.
It was never an open token map (Sub constrains it to known tones, 6-digit hex and
WCAG AA in both themes); the reason is ownership: `dotmac_ui.SEMANTIC_INTENTS`
publishes exactly those five names as tokens with built-in ramps that
`render_brand_css` does not seed, so a per-profile override would be a second
authority over a published token. A product that needs one changes the published
token.

The disposition is **executable, not prose** — a cutover runs it over every row
and reviews the aggregate before writing anything. Reporting rather than dropping
is `dotmac_ui.BrandWarning`'s own D8 rule applied to migration.

## Status

**Built and validated, not adopted.** See `EXTRACTION.toml` for what Sub's
cutover owes, including that the migrated colours are the same *values* rendered
by a *different generator*, so the cutover renders both and diffs the output.
