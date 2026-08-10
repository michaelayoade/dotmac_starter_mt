# Consent and suppression sources — Sub, and ERP's absence

**As of:** 2026-08-10
**Starter:** working tree on `feat/template-studio-notification-rebase` (main `d93164b`)
**ERP:** `3c86b5a9` (`chore/governance-b1dfd82-schema-v3`)
**Sub:** `5d6f115b7` (`feat/hold-boot-secrets`)

The inventory for the **consent/suppression** owner named by ADR-0006 § 5c — the
first of the four owners that amendment split out of Template Studio, and the one
it sequences first: *consent must be answerable before delivery exists.*

Step 2 of the extraction procedure in
[`module-extraction-sources.md`](module-extraction-sources.md). Facts, not a
mandate.

## Headline finding

**Sub has a complete, production implementation. ERP has nothing at all.**

`grep` for suppression, unsubscribe, opt-out, do-not-contact and bounce across
`dotmac_erp/app` returns two files, and both are false positives —
`finance/ar/customer_payment.py` and `dotmac_sub/sync/_payments.py` use "bounce"
for a returned cheque. ERP has no consent model, no suppression table, no
unsubscribe path, and no eligibility check, while sending invoices, statements,
offer letters and termination letters by email.

That asymmetry is the § 5a case in miniature. ERP having built nothing is not
evidence that consent is product-specific to Sub — it is evidence that ERP has an
unmet legal obligation. A hard bounce, a spam complaint and an erasure request
apply to any system that sends mail.

So this is a **product-first** extraction with a single qualifying source, not a
greenfield one: Sub's implementation is ported, and ERP becomes a consumer that
gains a capability it never had.

## Sub — the qualifying implementation

| Piece | Where | Lines |
|---|---|---|
| Model | `app/models/notification.py::CommunicationSuppression` | 56 |
| Decision owner | `app/services/communication_eligibility.py` | 374 |
| Behaviour proof | `tests/test_communication_eligibility.py` (14), `tests/test_notification_queue_suppression.py` (4) | 18 tests |
| Askers | `app/tasks/notifications.py` (2 sites), `app/services/comms_campaigns.py`, `app/api/campaigns.py`, `app/api/notifications.py` | — |

### The decisions worth porting

1. **One question, one table, every sender.** `may_send(db, channel=, address=,
   category=) -> bool`, with `suppression_reason(...)` returning the canonical
   reason. Sub's own docstring records why this exists: marketing eligibility
   used to be decided inside the campaign segment filter, where opting in was an
   *optional checkbox*, so the answer depended on who was asking and an
   unsubscribed customer stayed reachable by every other path.

2. **Scope is the load-bearing distinction.** `marketing` blocks marketing only;
   `all` blocks everything including transactional, and is reserved for hard
   bounces, complaints and legal erasure. An unsubscribe sets `marketing` and
   never `all`. Collapsing the two turns a consent ledger into a billing
   incident: a customer who unsubscribed from a promo has not waived their
   invoice.

3. **Unknown category defaults to TRANSACTIONAL.** `is_marketing()` returns True
   only for an explicit allowlist. Defaulting the other way would make any new or
   misspelled category silently suppressible — a typo could stop someone's
   invoices.

4. **Keyed on the ADDRESS, not the person.** The ledger keys `(channel, address)`
   because the address is what the transport actually sends to; `subscriber_id`
   is a best-effort link, since an address is not always resolvable to a person
   (imports, forwarded mail).

5. **Addresses are normalised so a suppression cannot be dodged.**
   `Foo@Bar.com` and `foo@bar.com` are one address; `+234 801 234 5678` and
   `2348012345678` are one number. Channel-aware: digits-only for `sms`/
   `whatsapp`, lowercase otherwise. `raw_address` keeps what the customer
   actually clicked, so the row stays auditable.

6. **Suppression escalates, never de-escalates.** Re-suppressing an address
   raises `marketing → all` but never the reverse, so a hard bounce cannot be
   downgraded by a later unsubscribe click. Sub's code carries a scar comment on
   the `db.flush()` that persists the escalation: without it the mutation lived
   only in the Session, the row stayed `marketing`, and invoices resumed to an
   address that had hard-bounced.

7. **Campaign admin is not authority to clear a hard bounce.**
   `unsuppress_marketing` removes a row only when its scope is `marketing`;
   `all`-scoped rows are managed by the canonical surface.

8. **Bulk is the same rule in one query.** `filter_eligible` /
   `suppression_reasons_for_addresses` exist so campaigns cannot hand-roll a
   per-recipient loop that drifts from the single-address path.

9. **An empty address is not a consent decision.** It returns "sendable" so the
   sender fails loudly on its own terms rather than being silently classed as
   suppressed.

### Defects and product coupling not to carry forward

- **`*_committed` variants call `db.commit()`.** `suppress_committed`,
  `unsuppress_committed`, `unsuppress_marketing_committed`,
  `unsuppress_by_id_committed`. The starter's hard rule 8 makes
  `dotmac_kernel.db` the one transaction authority and rule 9 forbids a service
  managing its own transaction. Port the flush-only forms and drop the four
  committing wrappers.
- **`MARKETING_CATEGORIES` is a hardcoded frozenset.** `{"marketing",
  "campaign", "promotion"}` is a product's vocabulary. Same mechanism/vocabulary
  split as Template Studio's render contexts: the kernel owns *transactional by
  default, allowlist decides*, the product declares which of its categories are
  marketing (ADR-0008).
- **`NotificationChannel` is a Python enum** with ten ISP-shaped members
  (including `facebook_comment`, `instagram_dm`, `nextcloud_talk`). Channel must
  be an open registered string, as `SettingDomain` and idempotency `scope`
  already are.
- **No tenancy.** Sub is single-tenant, so the table has no `tenant_id` and no
  RLS. The extracted table is tenant-scoped with RLS from its first migration —
  a consent ledger leaking across tenants would be the worst possible table to
  get wrong.
- **`unsuppress` deletes the row.** Defensible for a marketing re-subscribe;
  it also means an `all`-scoped bounce record can be erased with no trace of it
  having existed. Worth an explicit decision at port time rather than an
  inherited default.

## Where the extracted owner belongs

**The kernel, not a module** — `dotmac_kernel.consent`. Three reasons, in
descending strength:

1. **A module may not import another module.** Delivery/outbox is a separate
   owner under § 5c and will be a module; it MUST ask consent in-process on every
   send. Cross-module composition is limited to htmx fragments, which is useless
   for a service-level eligibility check. Consent as a module would be
   unreachable by the one caller that most needs it.
2. **ADR-0006 § 2 gives the kernel "invariants that must be corrected exactly
   once."** A legal do-not-contact rule is exactly that, and the 2026-08-08
   amendment's placement rule sends behaviour required by every assembly and free
   of business-domain policy to the kernel.
3. **It is asked from everywhere** — template rendering's callers, delivery,
   campaigns, and a product's own code. A facility with that many askers behind a
   module boundary would accumulate adapters.

Precedent for the shape: `dotmac_kernel.idempotency` / `idempotency_models`
(ADR-0014, kernel `0.1.0a33`) is a stateful kernel facility with a tenant-scoped
RLS table, an open-string `scope` vocabulary, and a service module beside its
models. Consent follows it exactly.

This placement means no new distribution, so no new `EXTRACTION.toml` — the
kernel's existing dossier covers it, and its `historical-mixed` status is
unchanged by adding a facility that IS product-first sourced.

## Behaviour proofs available to port

| Test | Tests | Proves |
|---|---|---|
| `dotmac_sub:tests/test_communication_eligibility.py` | 14 | scope semantics, transactional default, normalisation, escalation, bulk parity |
| `dotmac_sub:tests/test_notification_queue_suppression.py` | 4 | the queue path actually asks, and an `all` suppression stops a transactional send |

## Not covered

Sub's `CommunicationIntentRecord` (the durable reason/policy record behind a
send) and its channel-policy matrix are separate § 5c owners with their own
dossiers. Preference centres, per-category opt-in, and double opt-in are not
implemented in either product and are not in scope here.
