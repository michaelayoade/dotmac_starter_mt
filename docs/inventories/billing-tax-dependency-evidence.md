# Tax dependency evidence successor

Tax a7 source [PR942](https://github.com/michaelayoade/dotmac_starter_mt/pull/942)
introduces explicit dependency policy, original commercial identities and sealed
prior references. Billing a9 [PR940](https://github.com/michaelayoade/dotmac_starter_mt/pull/940)
is the unpublished source allocation for lossless original-document evidence,
carried completely by and superseded by a10. Neither a declaration
nor this successor grants publication, a product pin or operational authority.

Billing a10 copies the complete public Tax V2 graph into a versioned V3 original
invoice fact. Commercial identities retain the original line grain; multiple
lines with different original amounts remain distinct. Expected withholding
retains complete lineage in WithholdingTaxEvidenceV2. Billing never selects
Tax rules, pre-adds dependency amounts for a caller or imports Tax internals.

Tax public getters validate owner seals whose hidden storage preimage is not a
public DTO. Billing binds those opaque owner fingerprints plus every copied
public field in its own immutable fact/replay digest. Observable graph and
commercial-source consistency is verified; independent reproduction of a hidden
Tax seal is not claimed. V1/V2 boundaries refuse successor shapes rather than
persisting evidence that their readers cannot decode.

Independent static review is clear after legacy-admission and combined multiline
binding fixes. Authored real PostgreSQL canaries cover both planes where supported,
tenant-only Tax evidence refusal on platform, lineage/replay/tamper, unequal-base
multiline issuance and atomic collapsed-identity refusal. No tests were run locally;
full hosted qualification remains mandatory. bi1 through bi12 remain byte-identical.

Do not publish a9 merely as an integration prerequisite. Verify the final Tax a7
and Billing a10 artifacts before product adoption, then refresh exact pins,
generated locks, deployment heads and full consumer CI.


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
