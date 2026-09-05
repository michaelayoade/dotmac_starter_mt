# dotmac-inbox

`dotmac-inbox` is the reusable tenant conversation owner. It stores a durable
thread, every message in that thread, its lifecycle, and each operator's read
cursor. Channel names remain product vocabulary; the module branches only on
declared threading and message-identity traits. Thread creation and message
recording are conflict-safe and replay the durable winner for exact redelivery;
reusing a message identity with different content fails closed.
The public `bind_message_observation_ref` command may late-bind only the opaque
transport observation reference by local message UUID; it locks the tenant
message, is idempotent for the exact value, and never changes message identity
or conversation activity.

The read surface is exposed by `get_conversation`, `list_conversations`, and
`list_messages`. It returns frozen value DTOs, always requires a tenant id,
and uses bounded opaque keyset cursors. Conversation lists support only the
declared status, channel, and account-scope filters; message timelines are
ordered by occurrence time and UUID, so equal timestamps cannot create gaps or
duplicates between pages. Reads do not expose ORM rows or accept SQL
predicates from products.

Snoozing has two explicit forms: a timezone-aware finite deadline, or the
typed `UNTIL_REPLY` value (`SnoozeUntilReply`) for an indefinite snooze. An
omitted deadline is rejected; `UNTIL_REPLY` persists as `status=snoozed` with
`snoozed_until=NULL`, and only inbound module-owned message activity wakes it.
Timed wake scheduling remains product-owned and outside this package.

Products may declare `SUPPLIED` thread or message identities for stable local
references. A supplied thread reference is separate from external transport
thread evidence and permits a nullable contact; a supplied message reference is
separately scoped to the declared channel and account. The product retains its
subscriber, person, ticket, and other domain relationships, and must reuse the
same logical supplied message reference on retries. These declarations transfer
neither provider nor delivery authority, and do not by themselves establish an
adoption or cutover.

Adopters preserve established UUIDs through the separate typed
`import_conversation`, `import_message` and `import_read_state` history seam.
Those commands validate the same owner contracts, preserve source timestamps,
and replay only exact facts; they never trigger live reopen or activity-clock
consequences. Runtime `create_*` commands remain responsible for minting new
identity.

It is intentionally not an omni-channel transport or contact-centre suite. It
contains no provider client, connector configuration, webhook authentication,
delivery retries, contact resolution, queue, assignment, presence, attachment
storage, templates, or product-domain subject columns. `dotmac-integration`
owns transport receipts and delivery machinery; adopting products link their
own subjects and consequences to local `mod_inbox` rows.

The first release is an audit-complete candidate. Starter builds and proves the
package but does not compose or publish it until a first product cutover is
authorized.
