# Licensing review and remaining gaps

Review date: 2026-11-03. Source baseline: `fc41c1fc884013aaac386afb115fafca77ccd9f0`.
This is a repository evidence review, not confirmation of legal ownership or
permission to relicense. Application code and bundled asset bytes are unchanged.
The root npm license declaration is corrected as described below; third-party
license declarations and release versions are unchanged.

## Root npm metadata correction

On 2026-11-03, the requester confirmed that the root `ISC` declaration was
unintentional and authorized replacing it with `UNLICENSED` in `package.json`
and the root package entry of `package-lock.json`. The existing `private: true`
flag is retained. Third-party dependency license entries remain unchanged.

`UNLICENSED` records that this package is not offered under an npm license
grant; it is not a repository-wide proprietary license agreement. The legal
owner and future distribution terms still need confirmation. This correction
does not determine the effect of earlier declarations or distributions and
does not purport to withdraw any earlier valid permissions.

## Notice remediation completed in source

Full upstream license texts for Alpine 3.15.8 (MIT), HTMX 2.0.0 (0BSD), Outfit
(OFL-1.1), and Plus Jakarta Sans (OFL-1.1) now accompany the kernel's static
assets. [The packaged notice index](../packages/dotmac-kernel/src/dotmac_kernel/static/THIRD_PARTY_NOTICES.md)
records immutable notice-source coordinates and SHA-256 values. The existing
mpdecimal notices are indexed in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

The notice additions repair missing standalone notices; they do not prove
historical compliance, exact asset provenance, or release-artifact coverage.

## Open questions and closure evidence

The owner roles below identify who should resolve each question; they do not
assert that any person or company already holds the relevant rights.

| ID | Gap and repository evidence | Responsible role | Evidence needed to close |
|---|---|---|---|
| L1 | Legal copyright owner unconfirmed. Root and package Poetry metadata name `DotMac`/`Dotmac`, without establishing a registered legal entity or chain of title. | Business owner and legal reviewer | Exact legal owner name; relevant employee, contractor, contributor, and assignment agreements; authorization for the selected distribution terms. |
| L2 | Root metadata corrected to `UNLICENSED` following the requester's confirmation that ISC was unintentional. README still correctly leaves the repository-wide decision pending. Historical scope and distribution remain unreviewed. | Business owner and repository maintainer, with legal review | Confirm the legal owner's authorization and review what earlier declarations covered and what historical distributions represented. Select future terms without assuming a metadata edit withdraws earlier grants. |
| L3 | Extraction provenance is technical, not legal. All 101 package distributions have `EXTRACTION.toml`; source repository/revision/path fields and functional `owner` fields do not establish permission to copy or relicense. Examples: kernel, UI, and Document Rendering dossiers. | Source-product owners and legal reviewer | For each actual extracted source, inspect its recorded revision's license, notices, contributors and ownership; record permission or common ownership. Distinguish inventoried candidates from code actually copied. |
| L4 | Font filenames and CSS identify Outfit and Plus Jakarta Sans, but their exact download revisions, binary metadata, and subsetting/conversion history were not verified. Added OFL texts are traceable upstream snapshots, not proof of the font binaries' origin. | Asset maintainer | Recover download/build records; inspect font metadata and any reserved-name conditions; bind each bundled binary hash to its applicable copyright/license and modification history. |
| L5 | JavaScript embedded versions identify HTMX 2.0.0 and Alpine 3.15.8, and version-specific notices were recovered. Byte equivalence, original download source, and local modifications remain unverified. | Asset maintainer | Compare the bundled bytes with the corresponding upstream distribution/build, record asset hashes and source coordinates, and explain any differences. Preserve all applicable notices. |
| L6 | Dependency review is incomplete. Root Poetry lock has 179 package records: 101 directory dependencies and 78 external packages. npm lock has 78 non-root entries, including platform variants; its declared licenses include MIT, ISC, Apache-2.0, BSD-3-Clause, MPL-2.0 and CC-BY-4.0. | Dependency/release maintainer and legal reviewer | Review exact resolved Python/npm/Rust artifacts, transitive native libraries, build/runtime scope, modifications and distribution obligations. Inspect license files, not only metadata labels. Include separately built wheels and all native Cargo lineages. |
| L7 | Source packaging rules include the static notices, and Finance/Inventory/Surveys explicitly include mpdecimal notices, but no built wheel, sdist, published image, or offline bundle was inspected. | Release maintainer | Hosted artifact inspection showing the applicable notices reach each actual distribution; inspect native and OS-library notices and any required source/access mechanisms. Record immutable artifact digests and results. |

Counts describe the named baseline, including development and optional/platform
dependency records; they are not counts of packages installed in every runtime.
The npm lock declarations are not a conclusion that every license obligation
applies to the generated stylesheet or to the entire application.

## Next decisions

1. Obtain the legal owner's confirmation and review historical ISC declarations
   before selecting a repository-wide license. The root npm metadata correction
   is complete in source; preserve third-party terms separately.
2. Recover asset and extraction rights evidence, keeping unresolved records open.
3. Complete exact-artifact dependency review and hosted distribution checks.

Tests and test/development dependency installation remain Git-hosted-only under
`AGENTS.md`. These source changes do not authorize a release, change third-party
terms, or claim that any previously released artifact is repaired.
