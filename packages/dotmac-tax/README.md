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

Policy authoring in a5 uses `supersede_tax_rule` with a typed
`TaxRuleSupersessionInput`. The command appends an evidenced replacement edge;
it never edits a published rule or a sealed determination. The replacement
preserves applicability and priority, starts at or after the predecessor's
start, and inherits its end. Rate, method, progressive bands, recovery,
treatment, inclusive flag, sequence and supported calculation base may change.
Only a leaf may be replaced. Source reference/version provides exact retry
identity; different content and stale predecessors raise `TaxConflict`.

A replacement suppresses its explicit predecessor from the boundary onward.
Chains retain earlier date behavior; unrelated equal-ranked overlaps still
refuse determination. Existing sealed source facts replay before selection and
retain their exact original results. Downgrading a database containing any
rule or supersession refuses, because a4 would reopen band membership and
reactivate superseded policy.

`preview_tax_policy` reads current persisted policy through the same selector
and calculation builder as determination. Its distinct `TaxPolicyPreviewV1`
contains selected rules, bases, amounts and lines without determination IDs,
seals or authoritative evidence. It writes and flushes nothing, and raises the
same typed refusals. It does not simulate an unpublished replacement.

The replacement command is flush-only and opens no savepoint. The product
transaction owner must roll back and retry the whole command after a database
conflict. Legacy `ensure_*` commands retain their a4 savepoint behavior and
must not be called inside a host that forbids nested transaction boundaries.
This source candidate establishes no UI delivery, publication, product pin,
authority switch or deployment acceptance.

A5 seals progressive-band membership with the rule. Existing a4 rule rows
acquire `is_published=true` through the additive column default; no financial
values are repaired. New rules build with `false`, insert bands, then make the
sole content-preserving `false→true` transition before the owner returns.
A deferred constraint forbids committing an unpublished rule, and band inserts
lock their parent and refuse published membership. Reads and selection exclude
transaction-local unpublished rows. Both migration directions acquire bounded
five-second admission locks; downgrade requires a globally empty rule/edge
store and restores the prior trigger only in that empty case.

This schema change requires a write fence: old a4 binaries cannot publish new
rules after tx_0004, because they do not perform the required publication
transition. Drain old tax-policy writers before migration and resume them only
with a5. Public a4 service signatures and immutable `TaxRuleV1` remain available
in a5; this is source API compatibility, not old-binary write compatibility.


The unpublished a7 source adds `TaxFactV2(fact, commercial_source, prior_results)`
and `determine_tax_set_v2`. `commercial_source` identifies the original commercial
source independently of each purpose's determination source identity. Prior
references carry exact result identities and expected fingerprints, never amounts.
Only complete, verified V2 results from the same tenant and authority, commercial
source/version, date, original source amount/currency, recognition basis and
counterparty/supply/place references can supply a dependency.

`TaxRuleInputV2.dependency_policy` authors a required prior fact signature and
selects `all_prior` or an explicit tuple of tax code identities. Its calculation
base must be `source_plus_dependency_tax`. Exactly one referenced complete result
must match the signature, and every selected code must exist, including explicit
zero components. Tax adds only the selected prior tax to the original source for
this component's base. The new purpose's tax total contains its own components;
prior tax is never added again to that total or to statutory report aggregation.
External dependency bases cannot combine with `source_plus_prior_tax` or inclusive
components. Existing basis meanings remain unchanged.

`TaxDeterminationSetV2` retains commercial identity, every supplied prior reference,
and ordered V2 components carrying selected policy and exact prior component
snapshots. Its separate `rv2:` seal covers all common and V2 evidence. Exact replay
precedes current policy selection; neither prior nor current historical evidence
is rewritten. V1 readers refuse V2 results and dependency rules. Legacy V1 results
cannot be assigned an invented commercial binding.

`ensure_tax_rule_v2_in_transaction` and `supersede_tax_rule_v2` flush through the
existing publication owner. `ListTaxRuleHistoryV2` / `list_tax_rule_history_v2`
retain bounded cursor pagination, and `preview_tax_policy_v2` uses the same current
persisted policy and dependency validation without storing financial evidence.
The additive tx_0005 migration is a maintenance boundary for dependency writers:
a6 writers cannot determine a dependency rule as a V1 source-only result. Downgrade
refuses any populated V2 policy or evidence. This source is not publication,
installation, policy activation or product adoption evidence.

`get_tax_determination_set_v2` resolves one exact tenant-scoped sealed result
for guided dependency selection. It verifies the original result seal and its
bound source fingerprint without consulting current policy or creating evidence.
Missing, V1, unsealed or corrupt results refuse; no result scan is exposed.
