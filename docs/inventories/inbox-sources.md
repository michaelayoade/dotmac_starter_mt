# Omni-channel inbox source inventory — one implementation, and the trait seam

**As of:** 2026-08-11
**Starter:** `c8237bd` (`origin/main`)
**Sub:** `9f6f9f36b` · **CRM:** `c64b5aa0` · **ERP:** `766d4c0e` · **Vendor CP:** `eb667fa`

Evidence input to a reusable inbox module. It executes steps 1–3 of
[`module-extraction-sources.md`](module-extraction-sources.md)'s procedure: name
the contract, inventory every product, select the source implementation. It
follows the method [`ticket-sources.md`](ticket-sources.md) established, and
reaches a structurally similar answer for a different reason.

## Headline

**There is one implementation, not two.** CRM's inbox looks like a second
consumer and is not: its eleven `crm_inbox_*` web modules and `crm_presence`
are all classified `partial_capability` in Sub's CRM→Sub retirement ledger
(`dotmac_sub:docs/audits/crm_web_retirement_ledger.json`), under an accepted
decision that Sub is the owner. Counting it as a second consumer would repeat
exactly the error `ticket-sources.md` caught — "CRM's copy is already being
retired, so it is not a second consumer and must not be counted as one."

So the extraction question is not "can these two merge" but the harder one:
**does a single active implementation, with no other product asking for it,
justify a module at all?** § "The verdict" answers that, and the answer is a
narrowed unit rather than a yes or a no.

**The mechanism that makes the narrowed unit work is channel traits.** Both
products branch on hardcoded *sets of channel names* — in Python and, in CRM's
case, inside a partial unique index in SQL. Every one of those sets is asking a
question about the channel's transport, not its identity. Replacing the name
sets with four declared traits is what lets one module serve products whose
channel vocabularies cannot merge, and it is the direct analogue of the
lifecycle class in `ticket-sources.md`.

## The four products

| | Sub | CRM | ERP | Vendor CP |
|---|---|---|---|---|
| models | `app/models/team_inbox.py` | `app/models/crm/{conversation,comments,presence,team,…}.py` | — none — | — none — |
| tables | **28** | 22 | 0 | 0 |
| conversation table | `inbox_conversations` | `crm_conversations` | — | — |
| message table | `inbox_messages` | `crm_messages` | — | — |
| service LOC | **20,521** (35 modules) | 20,195 (60 modules) | 0 | 0 |
| routes | 58 | 105 | 0 | 0 |
| templates | 21 | 2 | 0 | 0 |
| channels | **10** | 6 | — | — |
| statuses | 4 | 5 | — | — |
| named SOT owners | **25** | 0 | — | — |
| observation ledger | **yes** | no | — | — |
| test files | **106** | 122 | 0 | 0 |
| tenancy | none (single operator, by design) | **none** | — | — |
| commits, last 90d | **72** | 38 | 0 | 0 |
| last touched | **2026-08-10** | 2026-07-09 | — | — |

Two of the four products have no inbox at all. That is a materially weaker
cross-product demand than `ticket-sources.md` found for tickets, and it is the
whole reason the unit gets re-drawn below rather than lifted whole.

**But "no inbox" turned out not to mean "no need", and that is this audit's most
useful surprise.** ERP's ticket already models email correspondence: a `REPLIED`
status no other product has, plus `raised_by_email`, `contact_email` and
`contact_phone` (`dotmac_erp:app/models/support/ticket.py`). It has no inbound
handling whatsoever to go with them — no IMAP, no parser, no message record
anywhere in the repository. The correspondence is modelled as *fields on the
request*, with the correspondence itself nowhere.

That is the shape of a product that needs a conversation record and has been
working around not having one. It is also the cleanest available evidence that
the unit below is drawn in the right place, because ERP would adopt the
conversation record while wanting **none** of the transport, ISP identity policy
or workforce policy the narrowing excludes. The vendor control plane, a
licensing surface, remains a genuine no.

### Why Sub is the qualifying source

Not size — CRM is within 2% on service LOC and has more route surface and more
test files. Three things decide it:

1. **CRM is a retirement source.** Its eleven `crm_inbox_*` web modules and
   `crm_presence` are ledger rows headed for deletion; PRs #1602–#1611 and #1615 already moved workspace,
   routing, collaboration, attachment, initiation, snooze, scheduled-send,
   transcript, ticket-handoff and field-job conversation behaviour into Sub.
   Porting *from* CRM would import a codebase the fleet has decided to delete.
2. **Sub has already done the decomposition.** Its SOT registry names
   **25 separate inbox owners** (`communications.team_inbox_threads`,
   `…_observations`, `…_contact_resolution`, `…_routing`, `…_delivery_receipts`,
   `…_operator_state`, `…_outbound_intents`, and 18 more) with declared `owns`,
   `depends_on`, `inputs` and event types. The hard part of "what is a separable
   unit" is answered in the source, not left to this audit's judgement.
3. **Sub separates observation from decision; CRM does not.** See below — this
   is the single largest architectural gap between them.

**One live exception.** ADR-0006 in Sub pauses the portal-chat portion of CRM
retirement: CRM still owns customer and reseller portal live chat through the
typed `crm.chat_session.v1` transport capability until the staffed Inbox cutover
gate is ready (`dotmac_sub:docs/runbooks/TEMPORARY_CRM_CHAT_AUTHORITY.md`). The
chat-widget channel is therefore genuinely CRM-owned *today*. It does not make
CRM a second consumer of an inbox contract — it makes it a transport under a
dated exception.

## The largest architectural difference

> Sub admits a provider fact before anything decides on it. CRM decides first
> and stores the decision.

**Sub.** `InboxProviderObservation` (`inbox_provider_observations`) is a durable
normalized fact, unique on `(provider, provider_account_scope,
provider_event_id)`, carrying `payload_fingerprint`, `normalized_payload`,
`observed_at`, `processing_status` and nullable `conversation_id`/`message_id`
back-references. `app/services/team_inbox_observations.py` fronts it with typed
frozen dataclasses — `InboundMessageObservation`, `DeliveryReceiptObservation`,
`FiberWebsiteInquiryObservation` — and the SOT registry declares
`communications.team_inbox_threads` as taking a "normalized inbound message
fact" of `AuthorityKind.OBSERVATION` from `communications.team_inbox_observations`.
That is the source-of-truth standard's observation → decision → consequence
split, implemented.

**CRM.** `InboundHandler.receive` (`app/services/crm/inbox/handlers/base.py`)
takes the provider payload straight to `create_message_and_touch_conversation`,
registers an `after_commit` hook that opens a *second session* to run
post-processing, emits events, and pokes the workqueue — all inside one method,
with no durable record of what the provider actually said. There is nothing to
replay. A parsing bug is unrecoverable for messages already ingested, because
the only artefact is the derived one.

This gap is the reason a naive "merge the two" would have been wrong in the
direction that matters, and it is why the module below makes the observation
seam mandatory rather than optional.

**Consequence for the kernel, and a boundary to respect.** An observation ledger
is *adjacent to* — and must not become a second writer of — the kernel's
at-most-once owner. `dotmac_kernel.idempotency.execute_once` (ADR-0014, hard rule
21) already answers "has this been done" for a `(tenant_id, scope, key)` with its
own fingerprint column, and `messaging.process_once` is explicitly an adapter
over it rather than a second mechanism. Sub's observation table re-implements
that decision alongside a genuinely different one:

| Question | Owner |
|---|---|
| has this provider event already been processed? | `dotmac_kernel.idempotency` — already exists |
| what exactly did the provider say, so consequences can be re-derived after a fix? | the observation ledger — genuinely new |

The module must delegate the first and own only the second. Building both is how
the fleet would acquire a fourth idempotency implementation while removing none,
which is precisely the failure `idempotency-sources.md` documented.

## The channel-name sets — the finding the module is shaped around

Neither product branches on "which channel is this" in one place. Both scatter
hardcoded membership sets, and every set is a proxy for a transport property
nobody named:

| Evidence | What it is really asking |
|---|---|
| `dotmac_sub:app/services/team_inbox_channel_receive.py` `_OPAQUE_CONTACT_CHANNELS` — a 5-name frozenset | is the contact string an address I can normalize and match, or a provider-scoped opaque id? |
| `dotmac_sub:app/models/team_inbox.py` — `field_job`'s comment: *"It has no external transport: delivery is the shared conversation websocket"*; `note` likewise | does an outbound message need a provider send at all? |
| `dotmac_sub:app/services/team_inbox_channel_receive.py` `_thread_id(channel_type, normalized_contact, fallback)` — synthesises a thread id when the provider gives none | does the provider carry thread identity, or must it be derived from the contact? |
| `dotmac_crm:app/models/crm/conversation.py` — `uq_crm_messages_inbound_external`, a partial unique index whose predicate contains **`channel_type IN ('email','facebook_messenger','instagram_dm')`** | is a provider message id unique globally, or only within one connected account? |

The last one is worth stating plainly, because the two products **disagree**,
and CRM disagrees with itself. CRM carries two overlapping partial unique
indexes on `crm_messages`: `uq_crm_messages_external` keys dedup on
`(channel_type, coalesce(channel_target_id, …), external_id)` — per-account —
while `uq_crm_messages_inbound_external` keys it on `(channel_type, external_id)`
for inbound email/Messenger/Instagram only — global. For those three channels
the narrower index wins, so **the same provider message id arriving at two
different connected mailboxes is rejected as a duplicate**. Sub has only the
global form (`uq_inbox_messages_inbound_external` on
`(channel_type, external_message_id)`), so it takes the same position without
the contradiction, and without recording that it is a position at all.

Whether an RFC 5322 `Message-ID` is globally unique is a real question with a
real answer per channel. Encoding the answer in an index predicate as a literal
list of channel names means it cannot be extended without a migration, cannot be
tested as a rule, and — demonstrably — can be silently contradicted by a second
index on the same table.

## The contract

> **An inbox conversation is a durable, threaded exchange between one tenant and
> one external party across a declared channel, with an auditable record of
> every message and the provider fact each one came from.**

The module owns that sentence and nothing else.

### Core — the module owns

| Element | Why it is generic |
|---|---|
| conversation identity, threading, lifecycle | every channel needs it; the threading *rule* differs by trait, not by product |
| the message record and its ordering | universal |
| **the channel trait registry** | the mechanism below; the one layer a product extends |
| **status — a closed 4-value vocabulary + open reasons** | CRM's fifth status, `resolved_to_ticket`, is a *reason* for `resolved`, exactly as `lastmile_rerun` was a reason not a status |
| direction (inbound / outbound / internal) | identical in both products, and there is no third opinion available |
| the dedup rule, keyed off the `message_id_scope` trait | replaces four hardcoded channel-name predicates with one rule |
| the observation ingress seam | the fact is admitted before anything decides — Sub's shape, minus the duplicated idempotency decision |
| per-operator read state | both products have it (`inbox_conversation_read_states`, `crm_messages.read_at`) with the same semantics |
| tenant scoping + RLS from revision 1 | the starter's contribution — **neither source has any tenancy at all** |
| audit of every status and assignment transition | both products have it; Sub's is typed and graded |

### Variant — each product declares or owns

| Element | Owned by | Example |
|---|---|---|
| **the channel vocabulary** | product manifest | Sub's `website_fiber`, `field_job`; CRM's chat widget |
| **the transport** | product | every `_send_*` function, OAuth refresh, Meta attachment URLs, SMTP polling |
| **contact resolution to a domain entity** | product | Sub's subscriber/reseller/party-contact-point matching — 340 lines of ISP identity policy |
| **routing and assignment policy** | product | Sub's team routes, capacity snapshots, FIFO queue; CRM's round-robin and least-loaded |
| agent presence and queueing | product | both have it; it is workforce policy, not conversation state |
| subject linkage | product | Sub → subscriber / reseller / lead / field job; CRM → person / ticket |
| outbound delivery and retry | product, through the kernel outbox | Sub queues into its notification outbox; CRM has `crm_outbox` |
| labels, macros, templates, saved filters | product | operator conveniences with no shared contract |
| AI intake, campaigns, CSAT, widget | product | none of it is "a conversation" |

The split is deliberately harsher than the table of features suggests. Of Sub's
20,521 service lines, the product-neutral core is a low-thousands fraction;
almost everything else is transport, ISP identity policy, or workforce policy.
**A module that absorbed Sub's inbox wholesale would be Sub, renamed.**

## The mechanism: a channel declares traits, and the core branches on traits

A channel is a declaration (ADR-0008), never an enum — the same rule that
governs `SettingDomain` and ticketing's reasons, and for the same reason: Sub's
ten values and CRM's six cannot merge, and the next product's cannot be
predicted. What the core needs is not the name but four properties, each one
already being asked in product code today:

| Trait | Values | The core branches on it to decide |
|---|---|---|
| `contact_identity` | `addressable` \| `opaque` | whether the contact string may be normalized and matched, or is meaningful only within one provider account |
| `transport` | `external` \| `internal` | whether an outbound message needs a provider send, or is delivered in-band |
| `thread_identity` | `provider` \| `derived` | whether to thread on the provider's thread id, or synthesise one from `(channel, contact)` |
| `message_id_scope` | `global` \| `account` \| `none` | the dedup key — and `none` says the provider gives no usable id, which neither product can currently express |

Every hardcoded set in the evidence table above becomes a trait predicate. The
test that keeps it honest is the one both products lack: **no channel name may
appear in a conditional in the module's core.** A product adds a channel by
declaring it, not by editing a frozenset in three files and an index predicate.

`message_id_scope = none` is the value that shows the mechanism is doing work
rather than re-describing what exists. Both products model dedup as "there is an
external id, or there is a NULL and we fall back to content hashing"
(`dotmac_crm:app/services/crm/inbox/dedup.py` builds a SHA-256 over channel,
address, subject, body and truncated timestamp). Making "this channel has no
stable id" a declared property makes the content-hash fallback a rule with a
reason, rather than a NULL check that fires by accident.

## Defects not to carry forward

| Defect | Where | Why it must not port |
|---|---|---|
| dedup rules contradicting each other across two indexes on one table | CRM `crm_messages` | the narrower index silently rejects legitimate mail to a second mailbox |
| post-processing in an `after_commit` hook on a **second session** | CRM `handlers/base.py` `_after_commit` | outside the transaction, unretriable, and invisible to the request's error handling |
| broad `except Exception` around event emission, workqueue poke and outreach reconciliation, downgraded to `logger.warning`/`debug` | CRM `handlers/base.py` (three sites) | a consequence that silently does not happen is worse than one that fails loudly |
| no durable record of the provider payload | CRM, all channels | nothing to replay after a parsing fix |
| the observation ledger re-deciding at-most-once | Sub `inbox_provider_observations` | ADR-0014 and hard rule 21 give that decision one owner; delegate it |
| **no tenancy of any kind** | both | the starter is multi-tenant always; every table gets `tenant_id NOT NULL` + composite uniques + RLS in revision 1 |
| conversation status carrying a *destination* (`resolved_to_ticket`) | CRM | it is a reason for `resolved`; as a status it forces every membership set to know it |

## Tests available to port

Sub's 106 inbox test files are the behavioural proof, and they port **with the
code they prove**, per the product-first amendment. The ones that map onto the
narrowed core:

| Test | Proves |
|---|---|
| `tests/test_team_inbox_channel_receive.py` | inbound admission, threading, duplicate handling |
| `tests/test_team_inbox_receive.py` | conversation identity and message recording |
| `tests/test_team_inbox_whatsapp_webhook.py` | a `derived`-thread, `opaque`-contact channel end to end |
| `tests/test_team_inbox_smtp_inbound.py` | a `provider`-thread, `addressable`-contact channel end to end |
| `tests/test_team_inbox_meta_social_webhook.py` | the third trait combination |
| `tests/test_team_inbox_read.py`, `test_team_inbox_read_state` coverage | the read cursor |
| `tests/test_team_inbox_lifecycle_audit.py` | transition auditing |
| `tests/test_team_inbox_rfc822.py` | email thread-header parsing (ports as a product transport helper, not core) |

CRM's 122 test files are read for the dedup and duplicate-detection scoring
only. They are not parity proofs for a module the fleet is retiring CRM into.

## Prerequisites — what a working inbox needs, and what already exists

The conversation record is the middle of a stack, not the whole of it. Measured
against the starter at `c8237bd`, the **outbound** half is largely built and the
**inbound** half does not exist at all.

### Already owned by the kernel — do not rebuild any of it

| Need | Owner | Note |
|---|---|---|
| queue, retry, backoff, worker lease, dead-letter | `dotmac_kernel.messaging` (`OutboxEvent` + relay) | `delivery-outbox-sources.md` already found Sub's notification queue to be this engine built twice |
| at-most-once execution | `dotmac_kernel.idempotency` | ADR-0014, hard rule 21 |
| the one send path | `dotmac_kernel.delivery_providers.send` | replay → **consent** → provider → receipt → outcome. A `Protocol` and no client |
| delivery receipts + bounce→consent loop | `dotmac_kernel.delivery.record_receipt` | the loop neither product has |
| may we contact this address | `dotmac_kernel.consent` | marketing vs transactional, transactional-by-default |
| which channels an event goes out on | `dotmac_kernel.channel_policy` | a settings document with a typed reader, not a subsystem |
| what the message says | `dotmac-template-studio` | render contexts declared by the product |
| provider credentials | `dotmac_kernel.secret_sources` | held, never dereferenced (ADR-0009) |
| per-tenant configuration | `dotmac_kernel.settings_resolver` | the inbox declares specs like anything else |
| audit trail, tenant timezone/formats | `dotmac_kernel.audit`, `.display` | — |

So "notification/communications hub" is **already answered**, and the answer is
that it is not a hub: it is five separate owners the ADR-0006 § 5c work already
landed. An inbox reply is an `OutboundMessage` on the existing send path, not a
new delivery mechanism — and routing it there is what makes the consent check
structural rather than a convention.

### Closed 2026-08-12: the inbound seam

**Inbound transport and connector configuration** was the largest gap — no
webhook-receiver contract, no model for "this tenant has connected *this*
mailbox / Meta page / WhatsApp number", nowhere for per-connector credentials,
and therefore no source for the `account_scope` every thread key and dedup key
depends on.

It is now `dotmac_kernel.inbound` + `.inbound_models` (kernel 0.1.0a41,
migration `0022_inbound_seam`), on the same terms as `delivery_providers`: an
`InboundReceiver` **`Protocol` and no clients**, so SMTP, IMAP and Meta stay
product dependencies. `connected_accounts` is the registry; `inbound_observations`
is the durable fact; `admit()` delegates at-most-once to
`dotmac_kernel.idempotency` rather than re-deciding it. Credentials are a NAME
resolved through `secret_sources`, never a value (ADR-0009).

Two boundaries worth recording. `verify` is separate from `parse` because a
signature is over raw bytes and re-serialising defeats it. And an **undeclared
channel is admitted** while an undeclared channel on a connected account is
**refused** — opposite calls on purpose: an observation that cannot be recorded
is a message silently lost, whereas a misspelled channel on an account is a
misconfiguration caught while an operator can still fix it.

### Still missing, in rough order of how much they block

1. **Attachments and media.** Sub has `inbox_media_assets` plus a 538-line
   `team_inbox_media.py`; CRM has `crm_message_attachments` and three attachment
   modules. The kernel has no blob-storage seam of any kind. A conversation
   record that cannot hold the photo a customer sent of their broken ONT is half
   a product.
2. **Realtime.** Both products push new messages over websockets
   (`dotmac_sub:app/services/team_inbox_realtime.py`, `app/websocket/`). The
   starter has **no websocket support whatsoever** — zero hits across the kernel
   and the assembly. An inbox that needs a page refresh is not an inbox.
3. **Per-operator read state.** Named as core in § "The contract" above and
   deliberately **not shipped** in 0.1.0a1, which carries only conversations,
   messages and observations. Sub's `inbox_conversation_read_states` and
   `team_inbox_read_state.py` are the source when it lands.

Each is its own extraction question with its own dossier. None should be
absorbed into the conversation record to make it look complete.

## Two collisions this audit found, both needing a decision

### Four places knew what a channel was — RESOLVED 2026-08-12

| Where | What it knows | Validated? |
|---|---|---|
| `dotmac_kernel.consent.register_numeric_channels` | **which channels are phone-numbered**, driving `normalize_address` | a registry, but only this one facet |
| `dotmac_kernel.channel_policy` | channel names inside the policy document | no — "an open registered string" |
| `dotmac_kernel.delivery_providers.OutboundMessage.channel` | a bare `str`, checked only for non-emptiness | no |
| `dotmac_inbox.channels` (new) | the four traits | yes, with an owner |

`register_numeric_channels` is the one that matters: it is already a per-channel
**behaviour** registry, and "is this address a phone number" is a facet of the
`contact_identity` trait. So the new module is not introducing the idea of a
channel registry — it is introducing a *second* one, beside a kernel that
otherwise treats channels as unvalidated strings in three places.

**Resolved by moving the vocabulary to the kernel** (`dotmac_kernel.channels`,
0.1.0a41). Modules may not import each other, so no module could ever have been
the source for consent, channel policy and delivery alike; the kernel is the only
place one registry can live. The module is now a pure consumer — it reads two of
the four traits and owns none.

The move exposed a defect worth recording, because it is what makes merging the
registries worth doing rather than merely tidy: the module's original
`contact_identity: addressable | opaque` **collapses email and phone into one
value**. Adopting it wholesale would have lost the numeric distinction and
reintroduced exactly the punctuation-dodge bug `register_numeric_channels` exists
to prevent — a suppression on `+234 801 234 5678` evaded by sending to
`2348012345678`. The trait is now `address_form: EMAIL | PHONE | OPAQUE`, and
"addressable" is simply `form is not OPAQUE`.

`register_numeric_channels` survives as an adapter over the registry rather than
being removed: it is in the published surface, and an adapter over one owner is
not a second writer — the same relationship `messaging.process_once` has to
`idempotency`. `sms`/`whatsapp` ship pre-declared, so normalisation is
byte-identical to a40 for every existing deployment.

### The word "inbox" is already taken, and means something else

`dotmac_kernel.messaging.inbox` is the **idempotent command inbox** — the
transport-delivery side of `process_once`, which uses `scope="inbox"` — and the
kernel ships a migration named `20260730_0009_platform_audit_inbox.py`. A
distribution called `dotmac-inbox` beside it makes "inbox" denote two unrelated
things one import apart. `dotmac-conversations` matches what the module actually
contains — § "The contract" never uses the word *inbox* — at the cost of
diverging from what both products call the screen. Worth deciding before it is a
published distribution rather than after.

## The verdict

ADR-0006 § "The extraction rule" requires same contract, named owner, migration
path — and ADR-0017 adds that adoption, not implementation, is the scarce
resource. Measured honestly:

- **same contract** — not demonstrated across products, because only one product
  has an inbox. It *is* demonstrated across channels: Sub's ten and CRM's six
  reduce to four traits with no residue, which is the same kind of evidence one
  step down. ERP's orphaned email-helpdesk fields are corroborating evidence of
  demand, but a product that has not built the thing cannot prove a contract.
- **named owner** — yes, and unusually well: 25 of them, already declared in
  Sub's executable SOT registry.
- **migration path** — Sub is the only holder of rows, and its CRM cutover is
  already in flight. A module cutover would be a *third* destination unless it
  lands as the target of the existing one.

So: **extract, with the unit re-drawn, and with the first cutover chosen to
prove the lineage rather than to move Sub.** The re-drawing is the load-bearing
part — "an omni-channel inbox" is not a module the fleet needs, and shipping one
would put transport, ISP identity policy and workforce policy behind a shared
version. "A tenant-scoped, trait-driven conversation record with a mandatory
observation ingress" is.

Three things follow, and the dossier
(`packages/dotmac-inbox/EXTRACTION.toml`) is blocked on all three before it can
leave `audit-complete`:

1. **A second consumer must exist before the contract is called proven.** Today
   there is one. Until a second product adopts it, the module is a bet, and
   `EXTRACTION.toml` says so rather than implying otherwise.
2. **CRM's inbox cutover should land into this module**, not into Sub's local
   owner — otherwise the fleet gains a third implementation while removing a
   second. Same instruction `ticket-sources.md` gave, same reason.
3. **The observation ingress delegates at-most-once to the kernel.** Non-
   negotiable: it is hard rule 21.

## Cross-cutting candidate, unresolved

`dotmac_ticketing.vocabulary` and this module both need "a declared, open,
product-extensible code registry with an owning module and a required
consumer" — the ADR-0008 shape. `dotmac_kernel.settings_resolver` has a third
copy for setting specs. Three implementations of one mechanism is the condition
under which ADR-0006 § 5 says stop and name an owner, and modules may not import
each other, so the owner would have to be the kernel. Recorded here as a
decision to make, not made: this audit is not a mandate.
