# Third-party notices

The root [LICENSE](LICENSE) applies to covered Dotmac-owned material. The
redistribution restriction does not override third-party licenses or prior
valid grants. A reference to a product in an extraction dossier is not by
itself evidence that its code or dependencies are bundled.

## Bundled source and browser assets

| Component | Included material | Applicable notice |
| --- | --- | --- |
| mpdecimal 4.0.1 | Vendored decimal implementation under `native/decimal-core/vendor/` and package-local Finance, Inventory and Surveys copies | Each tree retains `LICENSE.mpdecimal.txt`; preserve its copyright, conditions and disclaimer. |
| Alpine CSP build 3.15.8 | Kernel `static/js/alpine.min.js` | [MIT](notices/alpine.LICENSE.txt) |
| HTMX 2.0.0 | Kernel `static/js/htmx.min.js` | [0BSD](notices/htmx.LICENSE.txt) |
| Outfit | Kernel `static/fonts/Outfit-*.woff2` | [SIL OFL 1.1](notices/Outfit.OFL.txt) |
| Plus Jakarta Sans | Kernel `static/fonts/PlusJakartaSans-*.woff2` | [SIL OFL 1.1](notices/PlusJakartaSans.OFL.txt) |

The four notices were recovered in the prior RC1 review from immutable
upstream coordinates. [Notice provenance](notices/PROVENANCE.md) retains those
coordinates and checksums. The [current asset comparison](docs/inventories/starter-public-asset-review.json)
establishes an exact HTMX match and an Alpine match after CRLF normalization.
The [embedded font records](docs/inventories/starter-public-font-metadata.json)
identify the stated copyright holders and SIL OFL links in all nine font
files. The fonts differ from current Google Fonts downloads; their exact
download/subsetting history remains unverified. They retain the identified
OFL terms. The historical notice index's pending findings are superseded only
to the extent of these documented new checks.

The package inventory records included notice paths and distribution scopes.
These root notices accompany this GitHub source tree; they do not establish
that previously published wheels, images or third-party installers included
them. Inspect each separately distributed artifact before release.

## Resolved dependencies

Python, npm, Cargo, container and operating-system dependencies retain their
own licenses. `poetry.lock`, `package-lock.json` and the Cargo locks identify
resolution inputs, not a completed legal review of every installed artifact.
This change publishes no new wheels or images and vendors no additional
dependency source. A complete binary redistribution review remains separate.
