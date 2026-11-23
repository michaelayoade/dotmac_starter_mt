# Conversations and external integrations

- Short explanations of package responsibilities, not a claim of production readiness.
- Check the [catalog](../MODULE_CATALOG.md) for installation, release and adoption evidence.
- A **tenant** is a customer workspace; the **platform** is the operator control plane.
- [All module groups](index.md).

## Inbox (`dotmac-inbox`)

- **What it does:** Owns conversations, ordered messages, threading and read state.
- **Use it when:** Building a durable shared conversation history.
- **Keep in mind:** Provider delivery and staffed queue routing are separate.
- **Details:** [Package guide](../../packages/dotmac-inbox/README.md) · [Source and contract](../../packages/dotmac-inbox/EXTRACTION.toml).

## Inbox queues and staffing (`dotmac-inbox-operations`)

- **What it does:** Manages staffed queues, routing, agent availability and assignments.
- **Use it when:** Directing incoming conversations to the right available staff.
- **Keep in mind:** The conversation and message records remain with Inbox.
- **Details:** [Package guide](../../packages/dotmac-inbox-operations/README.md) · [Source and contract](../../packages/dotmac-inbox-operations/EXTRACTION.toml).

## Integration (`dotmac-integration`)

- **What it does:** Runs the external connector control plane: bindings, inbox/outbox, retries and repair.
- **Use it when:** Operating provider connections through one shared integration engine.
- **Keep in mind:** Products retain business decisions; connector plugins retain provider-specific protocol details.
- **Details:** [Package guide](../../packages/dotmac-integration/README.md) · [Source and contract](../../packages/dotmac-integration/EXTRACTION.toml).

## Flutterwave connector (`dotmac-connector-flutterwave`)

- **What it does:** Translates Flutterwave v4 payment commands and settlement observations.
- **Use it when:** Connecting supported payment capabilities through Integrator.
- **Keep in mind:** It does not own invoice balances, entitlement or local payment policy.
- **Details:** [Package guide](../../packages/dotmac-connector-flutterwave/README.md) · [Source and contract](../../packages/dotmac-connector-flutterwave/EXTRACTION.toml).

## Paystack connector (`dotmac-connector-paystack`)

- **What it does:** Translates Paystack payment commands, webhook facts and reconciliation results.
- **Use it when:** Connecting supported Paystack capabilities through Integrator.
- **Keep in mind:** Supported capabilities and known boundary debt must be checked in its contract.
- **Details:** [Package guide](../../packages/dotmac-connector-paystack/README.md) · [Source and contract](../../packages/dotmac-connector-paystack/EXTRACTION.toml).

## Remita connector (`dotmac-connector-remita`)

- **What it does:** Issues Remita payment references and observes their status.
- **Use it when:** Connecting Remita reference-based payment flows.
- **Keep in mind:** A provider status does not itself decide local settlement or invoice state.
- **Details:** [Package guide](../../packages/dotmac-connector-remita/README.md) · [Source and contract](../../packages/dotmac-connector-remita/EXTRACTION.toml).

## Mono connector (`dotmac-connector-mono`)

- **What it does:** Fetches Mono bank-transaction observations.
- **Use it when:** Supplying bank data to a local financial owner through Integrator.
- **Keep in mind:** It does not decide reconciliation matches or move money.
- **Details:** [Package guide](../../packages/dotmac-connector-mono/README.md) · [Source and contract](../../packages/dotmac-connector-mono/EXTRACTION.toml).

## Whatsapp connector (`dotmac-connector-whatsapp`)

- **What it does:** Verifies and translates WhatsApp events, sends and message-template observations.
- **Use it when:** Connecting supported Meta WhatsApp Cloud API capabilities.
- **Keep in mind:** Consent, customer-window policy and local conversation lifecycle are separate.
- **Details:** [Package guide](../../packages/dotmac-connector-whatsapp/README.md) · [Source and contract](../../packages/dotmac-connector-whatsapp/EXTRACTION.toml).

## Meta Social connector (`dotmac-connector-meta-social`)

- **What it does:** Translates Facebook and Instagram messages, comments and supported sends.
- **Use it when:** Connecting supported Meta social messaging capabilities.
- **Keep in mind:** It does not own local contacts, conversations or content decisions.
- **Details:** [Package guide](../../packages/dotmac-connector-meta-social/README.md) · [Source and contract](../../packages/dotmac-connector-meta-social/EXTRACTION.toml).

## Linkedin connector (`dotmac-connector-linkedin`)

- **What it does:** Translates supported LinkedIn webhook observations.
- **Use it when:** Receiving social activity and lead observations through Integrator.
- **Keep in mind:** It is ingress-only; it does not assign leads or manage campaigns.
- **Details:** [Package guide](../../packages/dotmac-connector-linkedin/README.md) · [Source and contract](../../packages/dotmac-connector-linkedin/EXTRACTION.toml).

## Freeradius connector (`dotmac-connector-freeradius`)

- **What it does:** Projects access data to FreeRADIUS and observes sessions and disconnect outcomes.
- **Use it when:** Connecting network access decisions to FreeRADIUS.
- **Keep in mind:** It does not make subscriber commercial decisions.
- **Details:** [Package guide](../../packages/dotmac-connector-freeradius/README.md) · [Source and contract](../../packages/dotmac-connector-freeradius/EXTRACTION.toml).

## Traccar connector (`dotmac-connector-traccar`)

- **What it does:** Reads supported Traccar device, position and history information.
- **Use it when:** Obtaining fleet-location observations through Integrator.
- **Keep in mind:** Its declared capabilities are read-only, not a fleet-command interface.
- **Details:** [Package guide](../../packages/dotmac-connector-traccar/README.md) · [Source and contract](../../packages/dotmac-connector-traccar/EXTRACTION.toml).

## Nira connector (`dotmac-connector-nira`)

- **What it does:** Translates .ng registry commands and queued messages through CoCCA EPP.
- **Use it when:** Connecting supported domain-registry operations to NiRA.
- **Keep in mind:** It does not decide domain prices, registration policy or retry schedules.
- **Details:** [Package guide](../../packages/dotmac-connector-nira/README.md) · [Source and contract](../../packages/dotmac-connector-nira/EXTRACTION.toml).

## Sub Accounting connector (`dotmac-connector-sub-accounting`)

- **What it does:** Reads Sub's invoice accounting-sync feed.
- **Use it when:** Supplying invoice observations to an independent accounting integration.
- **Keep in mind:** It does not write directly to another application's database.
- **Details:** [Package guide](../../packages/dotmac-connector-sub-accounting/README.md) · [Source and contract](../../packages/dotmac-connector-sub-accounting/EXTRACTION.toml).

## SMTP email delivery (`dotmac-delivery-smtp`)

- **What it does:** Sends email through the kernel delivery-provider contract with configured SMTP and production STARTTLS.
- **Use it when:** An application needs an SMTP transport for queued messages.
- **Keep in mind:** Passwords come from the held-secret seam; message policy and durable queue ownership stay outside the adapter.
- **Details:** [Package guide](../../packages/dotmac-delivery-smtp/README.md) · [Source and contract](../../packages/dotmac-delivery-smtp/EXTRACTION.toml).

## Safe delivery resend (`dotmac-delivery-resend`)

- **What it does:** Protects a human-triggered resend within the caller transaction and pins its delivery and audit evidence.
- **Use it when:** A product needs to retry a queued delivery without borrowing stale authorization.
- **Keep in mind:** It is a transaction safety facility, not a provider connector; its exact database and SQLAlchemy constraints apply.
- **Details:** [Package guide](../../packages/dotmac-delivery-resend/README.md) · [Source and contract](../../packages/dotmac-delivery-resend/EXTRACTION.toml).
