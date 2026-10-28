# Lossless invoice Tax evidence extraction

The existing Sub native issuance adapter at `610a88c2e1eba01be3fd3e82e2f9373f08377809`, `app/services/billing/starter_issuance.py`, copies legacy line rates into `AppliedTaxSnapshotV1`. The published Tax determination boundary at Starter `aca35172aeafdcce816e1ecae939fb4fdfb1805c` already retains source/result fingerprints, classification provenance, ordered rule components and progressive lines. The old Billing rate-only snapshot cannot represent that complete evidence.

Sub PR [#3820](https://github.com/michaelayoade/dotmac_sub/pull/3820) supplies the customer VAT/WHT presentation requirement. Its new Sub arithmetic is not the shared source algorithm: Tax owns determination and configurable effective-dated rates/bases. Billing owns document evidence; the assembly maps sibling public contracts without introducing sibling imports. This source slice adds the lossless envelope, persisted replay identity and an additive document-fact version. It leaves the old contract and released migration bytes intact.

Expected withholding has its own explicit purpose and affects presentation only. Accepted proof, withholding receivable discharge and cash-basis VAT recognition retain their existing owners. Current Tax evidence cannot be assigned to PlatformScope by inventing a tenant. No history is reconstructed from current policy.

Acceptance requires full exact-Money component/line roundtrip, fixed/progressive/multiple/zero results, tenant isolation, changed-input conflict, SQL mutation refusal, and populated bi11 predecessor upgrade/downgrade/reupgrade. Historical schema fixtures deliberately use predecessor-shaped mappings instead of current columns; current-schema acceptance must restore the new mapper. Tests are authored for hosted CI only.

The dependent Sub native-issuance consumer must publish/verify Billing a9 before its exact pin and lock change. Native activation requires real matching Tax policy; package installation and source PRs do not claim that activation or a complete legacy billing authority switch.

Native proof replay also needs the original allocation identity after reversal.
The existing AccountingFactV1 DTO already carries that evidence, but its public
historical reader was missing. The additive scoped get_accounting_fact_v1
exposes verified immutable original evidence without owner-table reads in Sub
or a mutable current-allocation dependency. Hosted both-plane canaries cover
historical deallocation, isolation, discriminator and content integrity.
