# dotmac-operational-escalations

Owns **whether an operational escalation should exist, under which terms, and
who answered it** — across tickets, outages and staffed inboxes alike.

A policy is a stable identity plus **immutable versions**; an escalation binds
the exact version it was raised under, so it stays auditable after the policy
moves on. Cooldown is enforced as a refusal to raise, because whether an
escalation should exist is this module's decision.

It does not deliver anything. `dotmac-durable-timers` provides the scheduling
the intervals imply, and Messaging/Integrator performs delivery and owns its
outcomes — the source kept deliveries beside the policy, which made the
escalation owner the delivery owner too.

The tenant-only `oe` lineage owns `mod_escalations`.
