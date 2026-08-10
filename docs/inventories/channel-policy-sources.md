# Channel policy sources — Sub, and why this is a setting rather than a subsystem

**As of:** 2026-08-10
**Starter:** `integration/communication-capability-map` (main `d93164b` + 3)
**ERP:** `3c86b5a9` (`chore/governance-b1dfd82-schema-v3`)
**Sub:** `5d6f115b7` (`feat/hold-boot-secrets`)

The inventory for the **channel policy** owner named by ADR-0006 § 5c — the one
that amendment flagged as *"the weakest of the three and its extraction is the
least proven; it may not be taken before its own dossier shows otherwise."*

This is that dossier. Step 2 of the extraction procedure in
[`module-extraction-sources.md`](module-extraction-sources.md).

## Headline finding

**It is not a subsystem. It is a settings document, and the kernel already owns
settings.**

Sub's `notification_channel_policy` is a single `DomainSetting` row in the
`notification` domain holding a JSON document:

```
{ "default":    ["email"],
  "categories": {"billing": ["email", "sms"], ...},
  "events":     {"invoice_due": ["sms"], ...} }
```

`resolve_notification_channels(...)` walks a five-step precedence over it:

1. a legacy per-event setting (`notification_event_<code>_channels`),
2. the policy document's `events` override, keyed by template code or event type,
3. its `categories` override,
4. its `default`,
5. the caller's own defaults.

Steps 2–5 are ordinary setting resolution with a typed shape on top. The kernel
already has the resolver, the scope chain, ADR-0012 inheritance, change history
and an admin surface for exactly this. A second owner would restate all of it.

**So the § 5c "channel policy" owner resolves to: a `SettingSpec` with a typed
accessor, not a new module, table, or service.** That is why it looked weakest —
not because the capability is doubtful, but because the unit was drawn one size
too large.

## What that means concretely

- **No new table, no migration, no distribution.** `domain_settings` already
  holds it.
- **What is missing in the kernel is a typed READER.** Sub's caller passes
  `template_code`, `event_type`, `category` and its own defaults and gets back a
  tuple of channels. That resolution function is ~60 lines over the settings
  resolver and is the only thing worth porting.
- **The vocabulary stays the product's.** `SELECTABLE_CHANNELS` is Sub's
  four customer-reachable channels out of its ten-member enum. Channel is already
  an open registered string in `dotmac_kernel.consent`; the same applies here.

## Defects and coupling not to carry forward

- **The legacy per-event setting is a second writer.** Step 1
  (`notification_event_<code>_channels`) shadows the whole document, and Sub's
  own docstring calls it *legacy*. Porting it would import a parallel authority
  on day one. The extracted reader has one source: the document.
- **`SettingDomain.notification` is a native enum member in Sub.** The starter
  has already opened setting domains (kernel `0014`); the port declares its
  domain rather than adding an enum member.
- **The document is untyped at rest.** `ChannelPolicyDocument` is a `TypedDict`
  — a static hint with no runtime validation, so a malformed document fails at
  the point of use rather than at write. The kernel's settings facility validates
  on write (`SettingSpec` value types), which is where this belongs.

## ERP

No equivalent, and no notion of per-message channel selection: ERP sends email
and generates PDFs, with the channel implied by the call site. Same posture as
consent — nothing to reconcile, and a future consumer rather than a source.

## Behaviour proof available to port

`dotmac_sub:tests/` has coverage of `resolve_notification_channels` precedence
embedded in the notification handler tests rather than a dedicated file. The
precedence table above is the contract to re-prove; it is small enough that
writing fresh tests against the ported reader is more honest than extracting
assertions from tests whose subject is the event handler.

## Recommendation

Fold this owner into the **settings** facility rather than creating a fifth one:
declare the spec, port the ~60-line resolver as a typed reader, and drop the
legacy shadow setting. Sequence it **after** delivery, since the reader has no
consumer until something dispatches on a channel.

If that recommendation is accepted, ADR-0006 § 5c's six-owner map becomes five,
and the amendment should record that channel policy resolved into settings
rather than standing alone — the map named the decisions correctly, and one of
them turned out to already have a home.
