# Tax dependency evidence successor

Tax a7 source [PR942](https://github.com/michaelayoade/dotmac_starter_mt/pull/942)
introduces explicit dependency policy, original commercial identities and sealed
prior references. Billing a9 [PR940](https://github.com/michaelayoade/dotmac_starter_mt/pull/940)
is the prerequisite lossless original-document evidence owner. Neither a declaration
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

Release a9 before integrating this a10 successor, and verify Tax a7 before product
adoption. Then refresh exact pins, generated locks, deployment heads and full
consumer CI. Policy activation and staged acceptance remain separate gates.
