# Lossless invoice Tax evidence extraction

The existing Sub native issuance adapter at `610a88c2e1eba01be3fd3e82e2f9373f08377809`, `app/services/billing/starter_issuance.py`, copies legacy line rates into `AppliedTaxSnapshotV1`. The published Tax determination boundary at Starter `aca35172aeafdcce816e1ecae939fb4fdfb1805c` already retains source/result fingerprints, classification provenance, ordered rule components and progressive lines. The old Billing rate-only snapshot cannot represent that complete evidence.

Sub PR [#3820](https://github.com/michaelayoade/dotmac_sub/pull/3820) supplies the customer VAT/WHT presentation requirement. Its new Sub arithmetic is not the shared source algorithm: Tax owns determination and configurable effective-dated rates/bases. Billing owns document evidence; the assembly maps sibling public contracts without introducing sibling imports. This source slice adds the lossless envelope, persisted replay identity and an additive document-fact version. It leaves the old contract and released migration bytes intact.

Expected withholding has its own explicit purpose and affects presentation only. Accepted proof, withholding receivable discharge and cash-basis VAT recognition retain their existing owners. Current Tax evidence cannot be assigned to PlatformScope by inventing a tenant. No history is reconstructed from current policy.

Acceptance requires full exact-Money component/line roundtrip, fixed/progressive/multiple/zero results, tenant isolation, changed-input conflict, SQL mutation refusal, and populated bi11 predecessor upgrade/downgrade/reupgrade. Historical schema fixtures deliberately use predecessor-shaped mappings instead of current columns; current-schema acceptance must restore the new mapper. Tests are authored for hosted CI only.

The a9 allocation is unpublished source history carried completely by and superseded by a10. The dependent Sub native-issuance consumer must verify the final Billing a10 artifact before its exact pin and lock change; a9 publication is not a prerequisite. Native activation requires real matching Tax policy; package installation and source PRs do not claim that activation or a complete legacy billing authority switch.

Native proof replay also needs the original allocation identity after reversal.
The existing AccountingFactV1 DTO already carries that evidence, but its public
historical reader was missing. The additive scoped get_accounting_fact_v1
exposes verified immutable original evidence without owner-table reads in Sub
or a mutable current-allocation dependency. Hosted both-plane canaries cover
historical deallocation, isolation, discriminator and content integrity.


## Final release and adoption gates

The final serial publication train is Payroll a2 → Tax a7 → Billing a10.
Tax a6 and Billing a9 are unpublished source allocations superseded by a7 and
a10 respectively; their complete source changes remain included in those final
successors. The historical allocations are retained, not published as prerequisites.
This source documentation establishes no artifact publication or consumer adoption.

Full hosted CI must qualify the final head and current base. Migration acceptance
must exercise the direct published Tax a4 / tx_0003 → head path through tx_0004
and tx_0005, and published Billing a8 / bi11 → bi12 on both declared planes.
Published Tax a4 and Billing bi1–bi11 migration bytes remain immutable; the
existing bi12 source is preserved without a new DDL edit. V1/V2/V3 invoice and
withholding evidence must retain exact historical decoding and replay compatibility.
Old Tax policy writers require a maintenance fence before tx_0004; schema/API
compatibility does not establish old-binary write compatibility. Restore or
forward-recovery procedures must be rehearsed before any production authority
change; populated-state downgrade refusal is not a rollback plan.

Each final distribution still requires its separate verified published artifact,
install-back verification, peeled tag and truthful release record under the
release captain's freeze. Only then may consumers exact-pin those artifacts,
regenerate locks and migration heads, and pass full hosted CI. Immutable-digest
staging acceptance on Seabone remains required before any separately authorized
production authority change. These gates activate no policy or writer. The native
accepted-proof capability remains bounded to one pristine invoice proof lane;
this release train does not complete the broader legacy billing cutover.
