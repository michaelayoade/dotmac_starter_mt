# Conversation/inbox source inventory

- **Revalidated:** 2026-08-18
- **Starter baseline:** `92ae7a6f9c83`
- **Sub:** `3f8d74825bee` · **CRM:** `c64b5aa0f790` ·
  **ERP:** `0f4b1698ddbf` · **Vendor CP:** `e6b2bbee815c`

Evidence for the audit-complete `dotmac-inbox` candidate and ADR-0052. This
inventory names the qualifying production source, the product-neutral owner,
the behavior tests that must move with it, the current Integrator boundary and
the retirement/cutover gates.

## Verdict

There is one active implementation, not two. Sub's Team Inbox is the qualifying
source. CRM's inbox is a retirement source under Sub's accepted CRM web
retirement ledger; treating that fork as a second consumer would count a writer
the fleet is already deleting. ERP and Vendor CP have no conversation owner.

ERP nevertheless supplies credible candidate demand. Its support ticket stores
`raised_by_email`, `contact_email`, `contact_phone` and a `REPLIED` state, but
has no durable inbound message record. It needs a conversation record without
Sub's transport, ISP identity or workforce policy. That is why the candidate is
drawn around the thread rather than the current product screen.

`dotmac-inbox` therefore owns this sentence:

> A durable, tenant-scoped exchange with one external party through a declared
> channel, containing an ordered record of its messages, its lifecycle and each
> operator's read cursor.

It is not an omni-channel provider runtime or a contact-centre suite.

## Fleet inventory

| Product | Current implementation | Classification | Target |
|---|---|---|---|
| Sub | `app/models/team_inbox.py`, 35+ inbox services, source-of-truth registry, operator read state, Integrator shadow port | qualifying production source and cutover 1 | local `mod_inbox` rows plus product-owned policy/link tables |
| CRM | `crm_conversations`, `crm_messages`, CRM inbox services | retirement source in the CRM-to-Sub consolidation | no independent writer |
| ERP | ticket correspondence fields, no conversation/message ingress | candidate consumer and requirement input | cutover 2 only after Sub proves the release |
| Vendor CP | no staffed inbox | genuine non-consumer | none |

Sub remains the source because it has named owners, behavior tests, an
observation-before-consequence path and the active production surface. CRM has
more routes in places, but its lack of a durable observation owner and its
accepted retirement status disqualify it as the extraction base.

## Source behavior to preserve

### Conversation and message record

Source paths:

- `dotmac_sub:app/models/team_inbox.py`
- `dotmac_sub:app/services/team_inbox_channel_receive.py`
- `dotmac_sub:app/services/team_inbox_commands.py`
- `dotmac_sub:app/services/team_inbox_read_state.py`

Required behavior:

- one stable thread per channel/account/contact or provider thread;
- inbound, outbound and internal directions;
- open, pending, snoozed and resolved lifecycle;
- reopening a resolved conversation on new inbound activity;
- ordered messages with provider and application time kept distinguishable at
  the product ingress boundary;
- per-operator read cursors that never move backwards; and
- a unique message identity that suppresses exact redelivery without merging
  distinct connected accounts, while the same identity with different content
  fails closed as a conflict.

Parity inputs:

- `dotmac_sub:tests/test_team_inbox_channel_receive.py`
- `dotmac_sub:tests/test_team_inbox_receive.py`
- `dotmac_sub:tests/test_team_inbox_smtp_inbound.py`
- `dotmac_sub:tests/test_team_inbox_whatsapp_webhook.py`
- `dotmac_sub:tests/test_team_inbox_meta_social_webhook.py`
- `dotmac_sub:tests/test_team_inbox_read.py`
- `dotmac_sub:tests/test_team_inbox_lifecycle_audit.py`

### Integrator boundary revalidation

Since the first 2026-08-11 audit, Sub added the
`messaging.receive.v1` Integrator shadow path:

- `app/services/team_inbox_integrator_envelope.py` normalizes the authenticated
  product-port envelope into Sub's owner command;
- `app/services/team_inbox_integrator_mirror.py` compares it against the local
  webhook observation without writing;
- `tests/test_integrator_observation_port.py` proves authentication, collision
  and port behavior; and
- `tests/architecture/test_integrator_port_boundary.py` prevents provider and
  destination policy leaking into the adapter.

This settles a boundary the older prototype assigned incorrectly to the
kernel. `dotmac-integration` owns connector installation, verification, raw
transport evidence, inbox/outbox receipt lifecycle, retry, checkpoints, health
and repair. The product owns the authenticated domain port and normalization.
`dotmac-inbox` owns only the resulting conversation decision. A message may
carry opaque transport correlation references; it does not copy raw payloads
or transport processing state.

The transport cutover and the conversation-owner cutover are different proofs.
Neither may be treated as evidence for the other.

## Shared versus product-owned boundary

| Concern | Owner |
|---|---|
| conversation id, canonical thread key, lifecycle and activity clock | `dotmac-inbox` |
| ordered message row and direction | `dotmac-inbox` |
| declared conversation-channel traits | `dotmac-inbox`, declared by the adopting product |
| per-operator read cursor with opaque actor id | `dotmac-inbox` |
| provider credentials, webhook verification, raw payload and transport receipt | Integrator module + connector plugin |
| contact → subscriber/person/customer resolution | adopting product |
| teams, routing, assignment, capacity, presence and queue | adopting product workforce owner |
| outbound provider send, receipt and retry | existing delivery/Integrator owners |
| attachments and bytes | product link + `dotmac-files` where adopted |
| realtime/websocket projection | adopting product |
| templates, macros, AI intake and saved filters | their product/module owners |
| ticket, lead, field-job and subscriber consequences | owning product services |

The shared schema has no subject foreign key. Products link from their own
schema to the local conversation id, because the module cannot require Sub's
subscriber identity, CRM's person identity or ERP's ticket identity.

## Mechanism: names are open, traits are fixed

Both implementations branch on hardcoded sets of channel names. Sub's
`_OPAQUE_CONTACT_CHANNELS` decides normalization. CRM places a channel list in
a partial unique index. Those sets are asking about properties, so the module
declares them:

| Trait | Values | Decision |
|---|---|---|
| `address_form` | email, phone, opaque | whether a contact is addressable or provider-scoped |
| `transport` | external, internal | whether a provider exists for this channel |
| `thread_identity` | provider, derived | provider thread ref versus `(channel, account, contact)` |
| `message_id_scope` | global, account, none | how the canonical message key is formed |

No channel name may occur in a package conditional. The architecture guard has
a sensitivity proof that plants such a branch and demonstrates detection.

Statuses are closed: `open`, `pending`, `snoozed`, `resolved`. Product terms
that explain a state use the declared reason layer. CRM's
`resolved_to_ticket` is the worked example: it explains `resolved`; it is not a
fifth answer to whether the conversation is live.

## Defects not to port

1. **Global message-id assumption.** Sub deduplicates every inbound provider id
   across every account. Account-scoped ids can legitimately repeat at a second
   connected account.
2. **Contradictory CRM indexes.** CRM has both account-scoped and narrower
   channel-specific global uniqueness, so the narrower rule silently drops the
   second message.
3. **Channel names inside decisions.** Adding a channel currently requires code
   and sometimes DDL changes rather than one declaration.
4. **CRM post-processing after commit on a second session.** Consequences can
   fail outside the owner transaction and cannot be replayed from a durable
   accepted fact.
5. **No tenancy in either source.** Revision 1 must add `tenant_id NOT NULL`,
   tenant-composite parent keys, and forced RLS.
6. **Product identity in conversation rows.** Subscriber/person/ticket columns
   would make the candidate unadoptable outside its source.
7. **Delivery state beside the message.** Attempts, backoff and next-attempt
   fields would rebuild an existing transport owner.
8. **Read cursors tied to a product identity FK.** The shared row uses an opaque
   actor UUID; the product authenticates and resolves it.

## Cutover and retirement

Sub is cutover 1. Its expand slice installs `ib`, creates product-owned subject
and workforce links, and shadows the module commands inside the same
transaction. Comparison covers thread keys, lifecycle/reasons, message keys and
order, activity clocks and operator read cursors. Account-scoped message-id
differences are expected only when explicitly classified and counted.

The authority switch requires:

1. a non-empty shadow population with zero unexplained differences;
2. PostgreSQL forced-RLS and cross-tenant parent-reference canaries;
3. verified product adapters for contact links, workforce state, media,
   realtime, delivery and consequences;
4. an ADR-0031 sealed one-writer switch; and
5. two-directional caller/model/table ratchets at zero before local retirement.

CRM retires through its existing consolidation path. ERP follows only on an
exact released pin and keeps ticket lifecycle, routing and every back-office
consequence outside the module.

Until the Sub adoption proof exists, `dotmac-inbox` remains `audit-complete`:
built and tested in Starter, absent from the reference assembly's Alembic
composition, and absent from the module publication allowlist.
