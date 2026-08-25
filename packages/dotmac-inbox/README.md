# dotmac-inbox

A tenant-scoped, trait-driven conversation record. Not an omni-channel inbox —
see "What this is not" below, which is the load-bearing half of the design.

## The layering

| layer | owner | extensible | decides |
|---|---|---|---|
| **channel trait** — `address_form` / `transport` / `thread_identity` / `message_id_scope` | **`dotmac_kernel.channels`** | **no**, fixed at 4 | threading, deduplication, whether an outbound send is even needed |
| **channel** — `email`, `whatsapp`, `field_job`, … | **product declares** (in the kernel registry) | yes | nothing on its own; it is a bundle of traits with a name |
| **status** — `open` / `pending` / `snoozed` / `resolved` | module | **no** | which transitions are legal; whether the exchange is live |
| **status reason** | **product declares** | yes | *why* it is in that status; filterable, drives product policy |
| **tag** | operator | yes | searchable only, no behaviour |

A product extends the **channel** and **reason** layers, never the trait or
status layers.

## The mechanism

**No channel name appears in a conditional anywhere in this package**, and
`tests/architecture/test_inbox_module.py` fails the build if one does.

That is not a style rule. It is the property that makes the module composable,
and the evidence for it is that both source products violate it constantly:

- `dotmac_sub` keeps `_OPAQUE_CONTACT_CHANNELS`, a five-name frozenset, to
  decide whether a contact string can be normalized and matched.
- `dotmac_crm` encodes the same class of decision **inside a partial unique
  index predicate** — `channel_type IN ('email','facebook_messenger',
  'instagram_dm')` — and a second index on the same table silently contradicts
  it, so the same provider message id arriving at two connected mailboxes is
  dropped as a duplicate.

Both sets are asking about transport properties, not identities. Declaring the
properties is all it takes:

```python
# The channel vocabulary is the KERNEL's — consent, channel policy and
# delivery all read it, and no module may be their source.
from dotmac_kernel.channels import (
    AddressForm, ChannelSpec, MessageIdScope,
    ThreadIdentity, Transport, register_channels,
)

register_channels([
    ChannelSpec(
        code="email", owner="my_product", label="Email",
        address_form=AddressForm.EMAIL,
        transport=Transport.EXTERNAL,
        thread_identity=ThreadIdentity.PROVIDER,   # References / In-Reply-To
        message_id_scope=MessageIdScope.GLOBAL,    # RFC 5322 Message-ID
    ),
    ChannelSpec(
        code="whatsapp", owner="my_product", label="WhatsApp",
        address_form=AddressForm.PHONE,                 # E.164, digits-only
        transport=Transport.EXTERNAL,
        thread_identity=ThreadIdentity.DERIVED,         # no provider thread
        message_id_scope=MessageIdScope.ACCOUNT,        # per business number
    ),
    ChannelSpec(
        code="field_job", owner="my_product", label="Technician chat",
        address_form=AddressForm.OPAQUE,
        transport=Transport.INTERNAL,          # delivered over our own socket
        thread_identity=ThreadIdentity.DERIVED,
        message_id_scope=MessageIdScope.NONE,  # content fingerprint
    ),
])
```

Threading and deduplication then follow without further product code:

```python
from dotmac_inbox import InboundIdentity, dedup_key, thread_key

identity = InboundIdentity(
    channel="email",
    account_scope="support@example.net",
    contact="customer@example.com",
    external_thread_id="<thread-abc@mail>",
    external_message_id="<msg-123@mail>",
)
thread_key(identity)   # 'email:support@example.net:t:<thread-abc@mail>'
dedup_key(identity)    # DedupKey('email:m:<msg-123@mail>', derived=False)
```

`DedupKey.derived` distinguishes "the provider told us this id" from "we hashed
the content because the provider gives none". Only the derived form can produce
a false positive, so callers can treat it as weaker evidence — a distinction
neither source product can currently express.

## Observations before consequences

`dotmac_kernel.inbound.admit` records what the provider actually said, before
anything decides on it. It is what lets consequences be **re-derived** after a
parsing bug is fixed — CRM has no equivalent, so a parsing defect there is
unrecoverable for everything already ingested.

The ledger is the KERNEL's, not this module's: admission needs
`dotmac_kernel.idempotency` and a connected-account registry that consent,
delivery and any conversation module all sit beside.

| question | owner |
|---|---|
| has this provider event already been processed? | `dotmac_kernel.idempotency` |
| what exactly did the provider say? | `dotmac_kernel.inbound_models` |
| what conversation did it become? | this module (`InboxMessage.observation_id`) |

The pointer runs consequence → fact. The kernel cannot reference a module's
schema, so a module row carries the observation id rather than the reverse.

## Tenancy

Both tables carry `tenant_id NOT NULL`, composite `(tenant_id, id)`
references throughout, and FORCEd RLS with an isolation policy from revision 1.

**Neither source product has any tenancy at all** — Sub is single-operator by
design, and CRM's inbox tables carry no scoping column. This is the starter's
contribution rather than something ported.

## What this is not

The audit (`docs/inventories/inbox-sources.md`) found that most of what the
fleet's one implementation calls an inbox is transport, ISP identity policy or
workforce policy. Of Sub's 20,521 service lines, the product-neutral core is a
low-thousands fraction. A module that absorbed the rest would be Sub, renamed.

So this module has **no**:

- **transport** — no provider SDK, no OAuth refresh, no SMTP polling, no send
- **contact resolution** — no subscriber/person matching, no contact table; it
  stores what the provider said and stops
- **routing, assignment, presence or queueing** — workforce policy
- **subject columns** — no `subscriber_id`, no `person_id`, no `ticket_id`;
  products link from their own schema
- **outbound delivery state** — the kernel outbox owns delivery
- **labels, macros, templates, saved filters, AI intake, campaigns, widget**

## What it needs, and what it must not rebuild

The conversation record is the middle of a stack. The **outbound** half is
already owned — an inbox reply is an `OutboundMessage` on the existing send path,
not a new delivery mechanism:

| Need | Owner |
|---|---|
| queue, retry, backoff, worker lease | `dotmac_kernel.messaging` |
| at-most-once | `dotmac_kernel.idempotency` |
| send: replay → consent → provider → receipt | `dotmac_kernel.delivery_providers.send` |
| receipts + the bounce→consent loop | `dotmac_kernel.delivery` |
| may we contact this address | `dotmac_kernel.consent` |
| which channels an event goes out on | `dotmac_kernel.channel_policy` |
| what the message says | `dotmac-template-studio` |
| credentials, configuration | `dotmac_kernel.secret_sources`, `.settings_resolver` |

Routing replies through `delivery_providers.send` rather than calling a provider
directly is what makes the consent check structural instead of a convention —
which is the whole reason that function exists.

The **inbound** half now exists in the kernel: `dotmac_kernel.inbound` (the
receiver `Protocol` and `admit`) and `connected_accounts` (where `account_scope`
comes from). Three gaps remain, each its own extraction question and none to be
absorbed here: attachments/media, realtime, and per-operator read state. See
`EXTRACTION.toml`'s `prerequisites`.

## Status

`0.1.0a1`, and the dossier (`EXTRACTION.toml`) is at `audit-complete`, not
`approved`. There is currently **one** implementation in the fleet and **zero**
other consumers: ERP and the vendor control plane have no inbox and no evident
need for one. Until a second product adopts this contract, the module is a bet,
and the dossier says so rather than implying otherwise.
