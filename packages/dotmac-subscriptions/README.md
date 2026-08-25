# dotmac-subscriptions

Reusable recurring-commercial ownership for explicit tenant and platform
planes.

The module owns stable offers, immutable published offer/price versions,
stable subscription contracts with immutable effective-dated versions and line
lineage, calendar cadence and declared proration, and one replayable recurring
charge occurrence per natural identity. It emits exact pre-tax rated facts and
commercial-intent projections. The consuming assembly maps those outputs to
the independently owned Billing or entitlement/access services.

The checked-in Cloud composition canary proves that the Billing mapping
preserves source identity, the rated service period, collection timing and
declared money scale without rounding. Timer generation is deliberately absent
from Billing identity. Corrections require the assembly to resolve the original
Subscriptions occurrence to the independently minted Billing obligation ID.
This is conformance evidence only; neither candidate product is claimed as an
adopter until it pins a released package and retires its local writer.

It also owns complimentary and sponsored treatment of a contract line: an
effective-dated arrangement recording approval NOT to collect a positive
contracted amount, and append-only non-cash grants recording exactly how much
was foregone for one exact service period. A complimentary service is never a
zero price — the line keeps its real price and the grant carries the evidence.
The seven ported reasons are an open declared registry, not an enum, so a
product names its own eighth reason without a module release.

It does not own invoices, tax, FX, receivables, payments, collections policy,
service state, entitlements, billing anchors, sponsor receivables, expense
postings, licences, provider integrations, documents, or the general ledger. It
imports no sibling module.

Both persistence planes share one behavior path. Tenant tables carry
`tenant_id NOT NULL`, composite tenant keys, and forced RLS. Platform tables
carry no tenant column and are revoked from the tenant application role. Plane
selection belongs to each consuming assembly.

The extraction dossier is [EXTRACTION.toml](EXTRACTION.toml); source and parity
evidence remain in `docs/inventories/subscriptions-sources.md` and
`docs/inventories/subscriptions-extraction-dossier.md`.

The current release candidate is `0.1.0a3` and requires
`dotmac-kernel>=0.1.0a94` plus `alembic>=1.13`. Kernel `a89` supplies the
subscriptions lineage allocation; `a94` is the actual minimum because it adds
the manifest-owned `charge_models` and `obligation_sources` declarations that
the package validates before offer, contract, and occurrence writes.
