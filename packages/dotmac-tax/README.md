# dotmac-tax

`dotmac-tax` owns tenant tax policy data, effective-dated determinations,
statutory-report snapshots, filing obligations, and the tax-return lifecycle.

Authorities, jurisdictions, tax codes, rates, recognition bases, progressive
bands, report boxes, calendars, and due dates are operator data—not enums or
country constants. A fact can produce an ordered set of VAT and any number of
tenant-defined tax components, including compound taxes. Effective-dated
party, supply and place classifications are tax-specific, evidenced policy
inputs. Their versions form an append-only override chain: the highest version
whose interval contains the fact date is selected and its identity is frozen on
each component; an expired temporary override reveals the still-effective
earlier classification. Standard-rated, zero-rated, exempt and out-of-scope are
separate treatments even when the resulting amount is zero.

For a tax code that declares candidate rules for a fact signature, category
selection is fail-closed: if none matches the effective party, supply and place
classifications, determination stops. Publish an explicit `exempt` or
`out_of_scope` zero rule when that legal treatment is intended; omission never
silently removes a configured custom tax.

Products submit exact source facts; assemblies project approved tax
consequences through their accounting owner and perform any authority
transport outside this package. Product catalogues contain neither a tax flag
nor a statutory rate.

`determine_tax_set` returns the published, immutable
`TaxDeterminationSetV1` read contract rather than a persistence model. Its
ordered component and progressive-line contracts carry exact kernel `Money`,
the source fingerprint and the selected rule/classification evidence. Explicit
zero-rated, exempt and out-of-scope components remain available through
`reportable_zero_components` even when there is no accounting consequence.
Consumers import these values from `dotmac_tax`; `dotmac_tax.models` is not a
public integration surface.

Each a3 result also carries a persisted `rv1:` content fingerprint over the
full normalized set/component/line evidence. Projection recomputes it and
refuses membership, duplicated-evidence or content drift. The database admits
children only during the result's transaction-local `building` phase, permits
one content-preserving transition to `sealed`, and refuses commit until that
transition completes. Statutory reports aggregate those verified public
contracts, never raw child rows. Immutable a2 sets and standalone legacy
determinations have no truthful seal and therefore fail closed in a3 readers;
a caller must submit a new source version rather than rewriting statutory
history.

The optional package owns the `tx` lineage and `mod_tax` schema. It imports no
sibling domain and never reads another application's database. See
`EXTRACTION.toml` and `docs/inventories/tax-sources.md`.
