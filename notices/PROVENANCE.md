# Third-party static assets

This document identifies third-party assets bundled with `dotmac-kernel`.
The accompanying texts apply to those assets, not to the repository as a whole.
Keep this index and the applicable license texts with redistributed assets.

| Bundled files | Identified project/version | License text |
|---|---|---|
| `js/alpine.min.js` | Alpine CSP build; embedded version 3.15.8 | [MIT](alpine.LICENSE.txt) |
| `js/htmx.min.js` | HTMX; embedded version 2.0.0 | [Zero-Clause BSD](htmx.LICENSE.txt) |
| `fonts/Outfit-{400,500,600,700,800}.woff2` | Outfit; exact binary source revision unverified | [SIL OFL 1.1](Outfit.OFL.txt) |
| `fonts/PlusJakartaSans-{400,500,600,700}.woff2` | Plus Jakarta Sans; exact binary source revision unverified | [SIL OFL 1.1](PlusJakartaSans.OFL.txt) |

## Notice provenance

The texts below were retrieved unchanged on 2026-11-03. Commit coordinates
identify the notice source; they do not prove that the bundled asset bytes
match that upstream revision. Embedded JavaScript versions identify releases;
the fonts still need exact download/subsetting provenance recovered.

- Alpine: tag `v3.15.8`, revision
  `4674e391f8a7ffa3f0a061d7236f4b550ca98eee`;
  [upstream license](https://github.com/alpinejs/alpine/blob/4674e391f8a7ffa3f0a061d7236f4b550ca98eee/LICENSE.md).
  Copyright 2019-2025 Caleb Porzio and contributors, as stated upstream.
- HTMX: tag `v2.0.0`, revision
  `f3c6b20728cbdbd222d425052c48c97ca74c5511`;
  [upstream license](https://github.com/bigskysoftware/htmx/blob/f3c6b20728cbdbd222d425052c48c97ca74c5511/LICENSE).
  The upstream text contains no named copyright holder; none is invented here.
- Outfit: Google Fonts notice snapshot at revision
  `2fe1e8c6b137fb13041a7cfca6fdcd8a910ea4dc`;
  [upstream license](https://github.com/google/fonts/blob/2fe1e8c6b137fb13041a7cfca6fdcd8a910ea4dc/ofl/outfit/OFL.txt).
  Copyright 2021 The Outfit Project Authors.
- Plus Jakarta Sans: Google Fonts notice snapshot at the same revision;
  [upstream license](https://github.com/google/fonts/blob/2fe1e8c6b137fb13041a7cfca6fdcd8a910ea4dc/ofl/plusjakartasans/OFL.txt).
  Copyright 2020 The Plus Jakarta Sans Project Authors.

## Downloaded notice checksums

SHA-256 values cover the unchanged downloaded license files, not the assets:

| License file | SHA-256 |
|---|---|
| `js/alpine.LICENSE.txt` | `08b7502da6e7aa1d0bbdc97d220fbf669b9366c61bd0f072238283c89bc4773a` |
| `js/htmx.LICENSE.txt` | `d3d2456f76414f2456104660ebd65aff1c04cd7966b942bdabd63f3cdb316a38` |
| `fonts/Outfit.OFL.txt` | `c676351bf8576b9aba743cd5eaa8c0e7ee0d51f805d720447b4df4ddb6a2e416` |
| `fonts/PlusJakartaSans.OFL.txt` | `995c7199cab65954f545996326755daee7b63cc6b42b06c13da1f9502ab08a99` |

This index is limited to these static assets. It is not a complete dependency
bill of materials or legal clearance for a release.
