# Device configuration composition: extraction sources

As of 2026-10-14. Starter `7a133895d611d9043eff10dc1b8271bdda493ff2`; Dotmac Sub
`main` `c837d2277af7b95fe15848b4071333582d320596`; Sub draft PR 3625 head
`ab2d08e0e8f4cadca0fd4b3c2e65d334ea03bcdc`.

Written before any router-configuration package source, per AGENTS.md rule 24
(product-first extraction). Facts, not mandates: the decision is
[ADR-0074](../adr/0074-device-desired-configuration-is-composed-from-layered-documents.md).
Starter is a public repository, so this file names no router, site, address or
credential from the estate.

## 1. Question

Is there a qualifying, production-used and tested implementation, in any Dotmac
product, of (a) composing a device's desired configuration from weighted layers
with provenance, typed conflicts and dated exceptions, and (b) parsing RouterOS
`/export` text? The answer decides which package starts from a product source
and which is `greenfield-after-inventory`.

## 2. Candidates read

| # | Candidate | Where | Verdict |
|---|---|---|---|
| 1 | Kernel settings resolver | `packages/dotmac-kernel/src/dotmac_kernel/settings_resolver.py` (Starter) | Not qualifying. Walks a scope chain (specific row, assembly default, spec fallback) and reports the source, but is scalar per key: no weights, tags, deep merge, list strategies, conflicts or dated exceptions. It is the kernel's tenancy-aware settings read, not a document composer. |
| 2 | `dotmac-brand-profiles` | `packages/dotmac-brand-profiles/` (Starter) | Not qualifying. Closest prior art for per-field provenance and locks, but brand-specific and first-non-null-wins on scalars. |
| 3 | Captive access policy | Sub `app/services/captive_access_policy.py` | Not qualifying as a generic engine. Domain-specific scope ladder (account > customer set > plan family > global, deny beats allow) over its own tables. It is a possible later plug-in consumer; it keeps its tables and decisions. |
| 4 | Router-health policy | Sub `app/services/router_health/policy.py` | Not qualifying. Role precedence, per-router overrides and dated exceptions with a seven-day lapse note, tied to Sub's check ids and vocabulary. Stays in Sub as the consumer of an assurance projection. |
| 5 | NetBox config contexts | external prior art | Semantic reference only (weighted contexts, deep merge). Not a Dotmac implementation. |
| 6 | Sub draft PR 3625 `app/services/composition/` | Sub `ab2d08e0e` | The reviewed initial code (about 2,930 lines, 12 modules) and tests (about 1,720 lines). Not a qualifying production source: it was never merged and never used in production. |
| 7 | `dotmac-network-control` and the other eight ADR-0038 modules | Starter `packages/` | None owns composed desired configuration. `dotmac-pon-access` owns OLT/ONT desired configuration only. |
| 8 | ERP, CRM, Workspace, Vendor Control Plane, Academy | their repositories | No layered device-configuration composer was found in the inventories already audited for the network suite (`network-module-sources.md`); no new read of these products was made for this file. |
| 9 | Sub RouterOS `/export` parser | Sub `app/services/router_health/routeros_export.py` (266 lines, blob `34cb8d56f28ee493c169657cc1b65c75ff70aeda`) and `tests/services/router_health/test_routeros_export.py` (blob `94b4c64df419f3dff98f2ff5f9e5a616c6b8c1a4`, 11 test functions) | **Qualifying.** Pure, deployed with Sub PR 3570 (merged at `8a024981c5d00fd2ca82c96e3ed7b3137c3a63cc`, migration 672 reconciler) and exercised by the router-health tests. It is the mandatory reference and initial code for the parser. |

## 3. Conclusion

- **Engine (`dotmac-config-layers`)**: no qualifying generic implementation
  exists. `source_mode = "greenfield-after-inventory"`, citing this file. The
  initial code is Sub draft PR 3625, ported with its review fixes (H1 and
  L1 to L4) and its spec tests; the PR is a reviewed draft, not production
  evidence.
- **Router plug-in (`dotmac-routeros-config`)**: one dossier carries one
  `source_mode`. The `/export` parser is product-first (candidate 9: Sub
  production code and tests become `source_paths` and `preserved_tests`); the
  plug-in modules, report and input parsing are greenfield after this
  inventory. The dossier's `source_mode_note` states that split.
- **Desired-state owner (`dotmac-network-config`)**: greenfield after this
  inventory; its tables, commands and projection have no product source.
- The parser in Sub is a one-time extraction. After Sub pins the published
  package, Sub deletes its copy and its parser tests keep running against the
  package parser (Sub is `no_writer` for the engine and `qualifying_source` for
  the parser, with local-copy retirement required for the latter).

## 4. What was built new, and what was product-first

| Unit | Mode | Source | Evidence |
|---|---|---|---|
| Composition engine (tiers, merge M1 to M11, conflicts, provenance, exceptions, derived DAG, widening, drift vocabulary, plug-in protocol) | built new (greenfield after inventory) | Sub draft PR 3625 `ab2d08e0e` | spec vectors T1 to T25, equal-rank and widening tests |
| RouterOS plug-in modules, report and input parsing | built new (greenfield after inventory) | Sub draft PR 3625 `ab2d08e0e` | module, report and worked-example tests |
| RouterOS `/export` parser | product-first | Sub `main` blob `34cb8d56f28ee493c169657cc1b65c75ff70aeda`, PR 3570 | Sub `test_routeros_export.py` as `preserved_tests` |
| Desired-state owner, commands, blast radius, assurance projection | built new (greenfield after inventory) | none | none yet |

## 5. Boundaries recorded for the dossiers

- Sub keeps the snapshot source (SSH `/export`), router inventory, real layers
  and seed values, the approver allowlist and step-up, the CLI, the `router_health`
  checks and vocabulary, and role precedence. None of that enters Starter.
- Starter packages hold no I/O, credential or transport (ADR-0074 section 4) and
  use synthetic router names and documentation address ranges only.
- Writer claims (rule 33): Sub is `no_writer` for the engine (no current writer
  exists) and `qualifying_source` for the parser. No other product is named by
  the dossiers' `source_repositories`.
