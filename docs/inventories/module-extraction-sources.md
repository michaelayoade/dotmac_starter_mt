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
