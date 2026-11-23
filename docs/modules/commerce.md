# Sales, customers and subscriptions

- Short explanations of package responsibilities, not a claim of production readiness.
- Check the [catalog](../MODULE_CATALOG.md) for installation, release and adoption evidence.
- A **tenant** is a customer workspace; the **platform** is the operator control plane.
- [All module groups](index.md).

## Customers (`dotmac-customers`)

- **What it does:** Manages customer accounts, account numbers, profiles and lifecycle.
- **Use it when:** Representing a commercial customer relationship.
- **Keep in mind:** A customer account references Party identity rather than creating a second person record.
- **Details:** [Package guide](../../packages/dotmac-customers/README.md) · [Source and contract](../../packages/dotmac-customers/EXTRACTION.toml).

## Party relationships and contact evidence (`dotmac-party`)

- **What it does:** Adds relationships, memberships, roles and contact-reachability evidence to Party identity.
- **Use it when:** Representing how people and organizations relate to each other.
- **Keep in mind:** Authentication and the kernel's base Party identity remain separate.
- **Details:** [Package guide](../../packages/dotmac-party/README.md) · [Source and contract](../../packages/dotmac-party/EXTRACTION.toml).

## Sales (`dotmac-sales`)

- **What it does:** Manages leads, sales pipelines, quotes and an accepted-quote handoff.
- **Use it when:** Taking an opportunity from initial interest to an agreed offer.
- **Keep in mind:** Accepting a quote does not itself deliver the service or collect payment.
- **Details:** [Package guide](../../packages/dotmac-sales/README.md) · [Source and contract](../../packages/dotmac-sales/EXTRACTION.toml).

## Subscriptions (`dotmac-subscriptions`)

- **What it does:** Owns recurring offers, contract versions, periods and charge occurrences.
- **Use it when:** Selling services with recurring commercial terms.
- **Keep in mind:** It is separate from invoice accounting, payment collection and network enforcement.
- **Details:** [Package guide](../../packages/dotmac-subscriptions/README.md) · [Source and contract](../../packages/dotmac-subscriptions/EXTRACTION.toml).

## Referrals (`dotmac-referrals`)

- **What it does:** Tracks referral programmes, codes, attribution and reward requests.
- **Use it when:** Recording who referred a prospect and which conversion earned a reward request.
- **Keep in mind:** A reward request is not an executed payout.
- **Details:** [Package guide](../../packages/dotmac-referrals/README.md) · [Source and contract](../../packages/dotmac-referrals/EXTRACTION.toml).

## Reseller management (`dotmac-reseller-management`)

- **What it does:** Manages reseller accounts, hierarchies and delegated commercial authority.
- **Use it when:** Letting resellers act within a defined scope.
- **Keep in mind:** It does not replace customer identity, billing or the product's authorization checks.
- **Details:** [Package guide](../../packages/dotmac-reseller-management/README.md) · [Source and contract](../../packages/dotmac-reseller-management/EXTRACTION.toml).

## Fulfillment (`dotmac-fulfillment`)

- **What it does:** Tracks ordered steps, attempts and recovery for an accepted commercial intent.
- **Use it when:** Coordinating several owners to fulfill an order reliably.
- **Keep in mind:** Participants own the actual work; the coordinator does not implement their business logic.
- **Details:** [Package guide](../../packages/dotmac-fulfillment/README.md) · [Source and contract](../../packages/dotmac-fulfillment/EXTRACTION.toml).
