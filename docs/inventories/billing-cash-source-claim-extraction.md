# Immutable cash-source receipt claim prerequisite

This bounded source slice starts from accepted Starter
`05c6d2560de2cbe1223ecf55b26ecabead7c5166` with accepted Billing predecessor
`bi_0005_non_cash_credit`. It adds no opening money, importer, future owner kind,
activation, confirmation policy, or new public command. Hosted acceptance is a
separate gate; no local runtime tests or database operations are permitted.

## Source inventory and disposition

| Qualified source | Disposition |
|---|---|
| Starter `dotmac_billing/service.py:accept_settlement` at the accepted revision | Preserve the existing trust/scope/account admission, exact key and request fingerprint, scalar replay affinity and ordinary financial effects. The real consumer inserts a provenance-only claim inside the existing Kernel idempotent operation. |
| Starter `dotmac_billing/models.py:ConfirmedSettlement`, `PlatformConfirmedSettlement` | Extract exact independently stored `source_system` and `source_settlement_key` natural identities on both planes; never derive a tuple by splitting the historical concatenated retry key. |
| Kernel `idempotency.py:execute_once`, `execute_once_platform` | Reuse the existing conflict savepoint and winner replay. No Billing-owned transaction or savepoint and no blanket IntegrityError conversion. |
| Sub `8ad84a677eb23b62c5d7b832df5c3954d8b89ac5`, `app/models/billing.py:PaymentSettlement`, `app/services/billing/payments.py`, `app/services/payment_provider_events.py` | Source evidence for actual settlement/payment/provider-event affinity and exact consumption. Not a reusable shared immutable receipt-claim implementation. Confirmation authority and source mapping remain unchanged. |
| Sub opening census and immutable opening preflight | Evidence-only inputs. Neither a census balance, digest, actor permission nor caller boolean proves receipt funding. No opening claimant is added in this prerequisite. |

The maintained Billing extraction audit is complete. This is an explicit
PORT-DELTA closing a source-identity persistence gap in the existing qualified
settlement consumer, rather than claiming a product implementation that does
not exist. Metadata identity, repair, proportional attribution, and synthetic
settlement creation are excluded. Actual source-cash proof gaps remain in the
canonical `billing-opening-cash-source-evidence-20261013` record.

## Stored invariant and accepted behavior

A claim records one real settlement's immutable scope, settlement ID, account,
source-system/key tuple, currency and precision. Its recorded time is claim
recording time, including migration time for historical rows; it never
fabricates historical confirmation time. Scoped natural uniqueness and a
composite foreign key bind every scalar to its actual settlement. Both planes
are append-only; tenant claims have forced RLS and platform claims are revoked
from the tenant role.

The additive successor migration copies every predecessor settlement identity
verbatim into a claim, verifies complete one-to-one affinity, then installs a
deferred completeness trigger requiring every subsequent inserted settlement
to have its exact claim at outer transaction completion. It changes no stored
settlement, idempotency response, financial fact, effect, outbox event, or money.
Inconsistent predecessors fail rather than being repaired. Old writers must be
fenced before upgrade because they cannot satisfy the new claim invariant.

Ordinary settlement acceptance creates the settlement and claim in the same
existing Kernel idempotent operation before posting. Exact replay first retains
all existing source/trust/scope/request checks, then verifies the persisted
claim using scalar SQL. Missing or mismatched claims are typed evidence
integrity failures and never read-repaired. A new operation against an already
claimed exact natural tuple refuses without adding financial effects. Historical
concatenated-key alias checks remain intact. Future opening admission must
share this namespace but is deliberately absent until real proof and complete
cash lifecycle semantics close.

## Hosted canary scope

Both-plane canaries cover exact replay and old key aliases, source/account/
currency conflicts, one winner under concurrent replay, forced tenant isolation
and platform grants, direct SQL natural uniqueness and composite affinity,
append-only behavior, deferred missing-claim refusal, caller rollback and an
injected fault after claim creation, and genuine predecessor backfill preserving
all historical rows and retry responses. These are authored checks until hosted
CI reports results; static inspection is not runtime evidence.

Claim affinity is to the immutable stored settlement, including its account ID,
currency and precision. This provenance-only backfill does not independently
qualify historical cash, or reinterpret a legacy settlement against current
account configuration. Import admission must perform those separate checks.
