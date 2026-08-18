# Product-first module extraction inventory

**As of:** 2026-08-08  
**Starter:** `8472b9ee` (`origin/main`)  
**ERP:** `4c9f492c` (`origin/main`)  
**Sub:** `4790737c` (`origin/dev`)

This inventory is the evidence input to ADR-0006's product-first extraction
amendment. It answers a narrower question than the feature-surface inventories:
before Dotmac creates or extends a shared distribution, which product code and
tests must be reviewed as the possible implementation source?

`packages/*/EXTRACTION.toml` is the machine-checked summary. This document keeps
the evidence readable and records why a package is approved, historical debt,
or blocked on an audit. A candidate row is not permission to extract it; the
original two-consumer, same-contract, named-owner, and cutover gate still
applies.

## Qualification and extraction procedure

For each candidate:

1. Name the shared contract before comparing implementations. Similar tables or
   screens are not enough.
2. Inventory ERP, Sub, and every other product in scope, including callers,
   migrations, tests, operational repair paths, and ownership.
3. Prefer the production-used implementation with the strongest matching
   behavioural proof. Port its code and tests first, then isolate product-only
   dependencies behind typed adapters.
4. Cut the source product over first. Shadow or reconcile old and new decisions,
   then remove or gate the local implementation so the extracted package becomes
   the one owner.
5. Move a second independent consumer on an exact released pin. Do not call the
   extraction complete before both consumers exercise the same contract.

"Copy" in this procedure means a traceable one-time move/port. It does not mean
vendoring a product directory unchanged, preserving a product import in the
shared package, or maintaining two implementations.

## Existing shared distributions

| Distribution | Current evidence status | Product-first reading | Next gate |
|---|---|---|---|
| `dotmac-kernel` | Historical pre-rule distribution | The `0.1.0a14` settings work demonstrates the desired direction: ERP/Sub supplied the mature design and defect evidence; the kernel owns the product-neutral implementation. The older kernel surface still predates a distribution-wide dossier. | Use the dossier for every new kernel facility; complete the settings cutover through ERP/Sub task #22 and retire their native domain enums. |
| `dotmac-ui` | Historical pre-rule distribution | Its semantic-token vocabulary started from Sub's live `static/css/design-system.css`, rather than the starter's smaller token set. Product adoption and local-style retirement are not complete. | Prove two released consumers and retire the overlapping local token/component owners during product cutover. |
| `dotmac-template-studio` | **Audit run 2026-08-10; still not an approved reusable owner** | The package's two-kind merge does not hold — ERP documents and Sub notifications disagree on syntax, engine class, identity and missing-variable policy, and the package is a third design adopted by neither. But the *capability* is real and mostly already built: the audit maps it to six owners, of which this package is one. Sub is the qualifying source for the notification renderer. Full evidence in [`template-studio-source-audit.md`](template-studio-source-audit.md). Accepted as ADR-0006 amendment 2026-08-10 (§ 5a–5c). Next slice: narrow this package to "what the message says" and re-base its renderer on Sub's single-brace contract. Separately: open dossiers for consent/suppression, channel policy, delivery/outbox and document generation — consent before delivery. |
| `dotmac-files` | **Audit-complete; zero contract consumers** | Sub supplies the strongest provider/staged-lifecycle source; ERP supplies the production S3 owner and broad document/image checks; CRM supplies content-spoofing canaries. ADR-0023 adds a separate declared platform table over the same persistence-free physical engine for Vendor CP artifacts. Domain meaning and imports remain excluded. Full evidence in [`files-sources.md`](files-sources.md); ownership is ADR-0022. | ERP then Academy prove tenant adoption after ERP E8 and local-owner retirement. Vendor CP is candidate cutover 3 through `PlatformScope()` and a licensing-owned exact-bundle relation. The platform declaration needs `platform_tables`, introduced in kernel source at a53; because a53–a55 were not published, a56 is the earliest installable compatible kernel, and a56 is this module's declared floor. No candidate is counted yet. |
| `dotmac-imports` | **Audit-complete; zero contract consumers** | Neither source has the whole capability: ERP's `finance/import_export/base.py` is the tabular front end (CSV/XLS/XLSX decoding, alias resolution, auto-map, preview, 23 concrete importers, 3,311 lines of tests) with no durable record of any run; Sub's `import_runs`/`import_run_rows` is the durable record with the weaker parser. CRM carries a byte-equivalent fork of Sub's row loader. Academy's `content_import` is markdown authoring, not tabular import, and Vendor CP has none. Full evidence in [`imports-sources.md`](imports-sources.md); ownership is accepted in ADR-0025 through a named owner-directed ADR-0017 exception. | Keep unreleased until the first real cutover is ready. After the Sub lineage gate and ERP E8/files prerequisites, ERP's customer-master CSV importer goes first with row-for-row dry-run parity; publish kernel a50/imports just in time. Then Sub retires `import_runs.py`; CRM deletes its forked loader. Spreadsheet extras arrive with ERP parity tests. Export stays unowned and undossiered. |

## Authorized replacement candidates

| Candidate | Evidence status | Product-first reading | Next gate |
|---|---|---|---|
| `dotmac-people` | **Audit complete; a1 implemented, unreleased and uncomposed** | ERP is the only qualifying source for the narrow employment-directory contract. Kernel Party remains the identity owner; ERP Person, credentials, payroll, attendance, finance and integration fields do not port. The six-table `mod_people` plane, Party prerequisite, forced-RLS canaries, temporal primary-overlap enforcement and ERP parity behavior now live in `packages/dotmac-people`; no authority has moved. Full evidence in [`people-directory-sources.md`](people-directory-sources.md). | Merge and publish kernel a71, then merge and publish dotmac-people a1. Compose the exact releases in clean Backoffice and implement the narrow ERP projection/backfill/shadow contract. ERP's 131 FK declarations require a rebuildable compatibility projection after the sealed writer cutover; its projection ratchet remains separate from the lifecycle-writer ratchet. Backoffice composition alone moves no authority. |

## Template Studio source audit

**Run 2026-08-10. Outcome and full comparison:
[`template-studio-source-audit.md`](template-studio-source-audit.md).** The
audit's finding is that these inputs do *not* represent one contract — the
caution below turned out to be the operative point.

The following are mandatory inputs to the audit, not a conclusion that all of
them represent one contract.

### ERP document templates

- Model and schema: `dotmac_erp:app/models/finance/automation/document_template.py`
- Generation and safe rendering:
  `dotmac_erp:app/services/automation/document_generator.py` and
  `dotmac_erp:app/services/automation/safe_template.py`
- Admin/service surface:
  `dotmac_erp:app/services/finance/automation/web.py`
- Behaviour proof:
  `dotmac_erp:tests/integration/services/test_document_generator.py`,
  `dotmac_erp:tests/services/test_workflow_engine.py`, and
  `dotmac_erp:tests/e2e/test_automation.py`

### Sub notification templates

- Model and schema: `dotmac_sub:app/models/notification.py`
- Authoring and delivery:
  `dotmac_sub:app/services/web_notifications.py`,
  `dotmac_sub:app/services/notification.py`, and
  `dotmac_sub:app/services/events/handlers/notification.py`
- Safe placeholder contract:
  `dotmac_sub:app/services/notification_template_renderer.py`
- Behaviour proof:
  `dotmac_sub:tests/test_notification_template_renderer.py`,
  `dotmac_sub:tests/test_email_services.py`, and
  `dotmac_sub:tests/test_settings_seed_services.py`

The audit must compare at least stable identity, versioning/immutability,
publish/activation semantics, supported channels/document kinds, placeholder
syntax and missing-variable behaviour, tenant/organization scoping, seeding,
delivery integration, generated-document traceability, permissions, and
migration/cutover shape. Product-specific workflows remain behind adapters; a
shared module must not absorb ERP finance or Sub subscriber/network policy.

## Settings cutover already in flight

Kernel `0.1.0a14` is released with the product-derived settings subsystem. ERP
and Sub still own native PostgreSQL `SettingDomain` enums in
`app/models/domain_settings.py`; those 21- and 28-member enums are the current
non-conformance. Task #22 therefore remains the immediate cutover slice:

1. repair each product's `domain_settings.py` with an `ALTER TYPE`-avoiding
   migration of the kernel `0014` shape;
2. repin each product to Governance `b1dfd82` with a schema-v3 required profile;
3. adopt kernel `0.1.0a14` on a separate later change.

This order is consistent with product-first extraction: the shared
implementation is released, but the work is not complete until each source
product consumes it and its conflicting local contract is retired.
