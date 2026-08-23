# dotmac-subscriptions

Reusable recurring-commercial ownership for explicit tenant and platform
planes.

The module owns stable offers, immutable published offer versions and optional
positive reference prices,
stable subscription contracts with immutable effective-dated versions and line
lineage, calendar cadence and declared proration, and one replayable recurring
charge occurrence per natural identity. It emits exact pre-tax rated facts and
commercial-intent projections. The consuming assembly maps those outputs to
the independently owned Billing or entitlement/access services.

`list_effective_offers` is the owner read for discovery: it returns one
effective immutable version per stable offer with exact price and provenance
facts. It does not decide product family, search facets, stock availability,
eligibility, locale formatting or which action a viewer may take.

Every offer version declares one product-owned charge model and a closed
pricing mode. `catalog_price` requires at least one strictly positive reference
price. `contract_price` may intentionally publish none: negotiated price then
lives only on the immutable contract line, where every active line still
requires a strictly positive unit price. A zero-price offer or contract line is
not the complimentary-service mechanism.

It does not own invoices, tax, FX, receivables, payments, collections policy,
service state, licences, provider integrations, documents, or the general
ledger. It imports no sibling module.

Both persistence planes share one behavior path. Tenant tables carry
`tenant_id NOT NULL`, composite tenant keys, and forced RLS. Platform tables
carry no tenant column and are revoked from the tenant application role. Plane
selection belongs to each consuming assembly.

The extraction dossier is [EXTRACTION.toml](EXTRACTION.toml); source and parity
evidence remain in `docs/inventories/subscriptions-sources.md` and
`docs/inventories/subscriptions-extraction-dossier.md`.
