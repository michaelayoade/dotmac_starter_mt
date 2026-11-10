# Subscriptions: catalog-price and period-exclusion exceptions record

Dated 2026-11-09. Covers `dotmac-subscriptions` migrations `su_0004` and
`su_0005` (0.1.0a4). This is a RECORD for manual review, not a repair plan:
nothing here is rewritten, backfilled or inferred by code.

## What the new rules are

1. A `catalog_price` contract line names `(offer_version, price_key)` and equals
   the published `OfferVersionPrice`. Enforced by the service, a composite
   foreign key and an INSERT trigger (`su_0004`).
2. One contract line (tenant, contract, line key) has no two current,
   non-cancelled occurrences with overlapping rating coverage, including under a
   new contract version; a correction may overlap its own chain. Enforced by a
   trigger on the su_0001 pattern (`su_0005`).

## Existing rows

| Set | Why it is outside the rule | Handling |
|---|---|---|
| Contract lines of `catalog_price` offer versions created before `su_0004` | They have no `price_key`; the price they carry was never tied to a published price row | Grandfathered. The foreign key is valid (NULL keys are skipped) and the trigger fires on INSERT only. The upgrade logs the count per plane. Lines are immutable, so they are never edited. A reviewer decides, line by line, whether to supersede them with a new contract version that names a price. |
| Overlapping current occurrence pairs, or an occurrence with more than one correction, before `su_0005` | A trigger and a unique constraint cannot be added `NOT VALID` | The upgrade REFUSES with the counts and changes nothing. Pairs go to the manual exceptions review; the upgrade is re-run once they are resolved by the owner's process. |

The module is unpublished beyond a3 and uncomposed in any adopter, so both sets
are expected to be empty in practice. Both are recorded so that an adopter that
did write such rows has a named place to find them.

## Review query (read-only)

```sql
-- catalog-priced lines that name no price (tenant plane; drop the tenant
-- predicates for the platform plane)
SELECT l.id, l.tenant_id, l.contract_version_id, l.contract_line_key
  FROM mod_subscriptions.subscription_contract_lines l
  JOIN mod_subscriptions.offer_versions v
    ON v.tenant_id = l.tenant_id AND v.id = l.offer_version_id
 WHERE v.pricing_mode = 'catalog_price' AND l.price_key IS NULL;

-- overlapping current occurrence pairs (coverage; superseded rows excluded)
SELECT a.id, b.id
  FROM mod_subscriptions.recurring_charge_occurrences a
  JOIN mod_subscriptions.recurring_charge_occurrences b
    ON a.id < b.id AND a.tenant_id = b.tenant_id
   AND a.contract_id = b.contract_id AND a.contract_line_key = b.contract_line_key
   AND a.state <> 'cancelled' AND b.state <> 'cancelled'
   AND NOT EXISTS (SELECT 1 FROM mod_subscriptions.recurring_charge_occurrences s
                    WHERE s.tenant_id = a.tenant_id AND s.corrects_occurrence_id = a.id
                      AND s.state <> 'cancelled')
   AND NOT EXISTS (SELECT 1 FROM mod_subscriptions.recurring_charge_occurrences s
                    WHERE s.tenant_id = b.tenant_id AND s.corrects_occurrence_id = b.id
                      AND s.state <> 'cancelled')
   AND a.rating_coverage_start < b.rating_coverage_end
   AND b.rating_coverage_start < a.rating_coverage_end;
```
