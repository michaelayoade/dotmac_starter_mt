# dotmac-inbox-operations

Owns staffed inbox queues, executable provider-neutral routing, inbox-specific
agent presence, capacity-safe conversation assignment, and workflow evidence.
Version `0.1.0a3` adds durable routing decisions, active-only uniqueness for
repeatable queue and assignment lifecycles, and queue/presence row locking for
concurrent dispatch.

`route_conversation` selects the lowest-priority-number active rule, admits the
conversation and records the exact rule, queue and queue-entry evidence in the
same caller-owned transaction. `promote_from_queue` chooses the agent itself
from caller-supplied opaque eligibility references, current presence, capacity
and the durable round-robin cursor; callers cannot select an arbitrary agent
through the queue path. `dispatch_queues_fairly` attempts one FIFO item from
every supplied queue, so a saturated queue cannot hide a queue with capacity.

Eligibility is an input projection, not copied Workforce state. The assembly
asks Workforce/product policy for eligible Party references and supplies them
with an explicit presence-freshness cutoff. Conversation/message content
remains with Inbox; channels/connectors remain transports; teams, skills,
shifts and field availability remain with Workforce. The tenant-only `io`
lineage owns `mod_inbox_ops`; services mutate and flush but never commit or
rollback.
