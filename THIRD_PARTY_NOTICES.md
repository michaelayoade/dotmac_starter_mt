# Third-party notices

These notices apply to the identified third-party components. They do not
license Dotmac-authored code or settle the repository's pending license choice.

## Bundled browser assets

The kernel's [static-asset notice index](packages/dotmac-kernel/src/dotmac_kernel/static/THIRD_PARTY_NOTICES.md)
identifies Alpine, HTMX, Outfit, and Plus Jakarta Sans, with full license texts,
source coordinates, and notice checksums alongside their assets. Keep that
index and the applicable texts with redistributed static assets.

`packages/dotmac-kernel/pyproject.toml` already includes `static/**/*` in both
source distributions and wheels. The Dockerfile copies the package tree into
the runtime image. These source rules cover the added notices without a
packaging-code change; built-artifact verification remains outstanding.

## Bundled decimal implementation

The native decimal implementation includes mpdecimal 4.0.1. Its copyright,
redistribution conditions, and disclaimer are retained in
[LICENSE.mpdecimal.txt](native/decimal-core/vendor/LICENSE.mpdecimal.txt).
Source and binary redistributions must preserve the information required by
that text.

The following package-local copies are also retained, and each owning
`pyproject.toml` explicitly includes its notice in source and wheel outputs:

- [Finance](packages/dotmac-finance/native/decimal-core/vendor/LICENSE.mpdecimal.txt)
- [Inventory](packages/dotmac-inventory/native/decimal-core/vendor/LICENSE.mpdecimal.txt)
- [Surveys](packages/dotmac-surveys/native/decimal-core/vendor/LICENSE.mpdecimal.txt)

## Other dependencies and unresolved questions

This is not a complete set of notices for Python, npm, Rust, operating-system,
or container dependencies. Those components retain their respective terms.
Dependency metadata alone does not establish that distributed artifacts carry
all required notices or source materials.

See [the licensing review](docs/LICENSING_REVIEW.md) for the remaining ownership,
provenance, dependency, and distribution checks. No repository-wide license is
added by this notice inventory.
