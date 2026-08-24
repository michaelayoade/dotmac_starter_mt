# dotmac-inbox

`dotmac-inbox` is the reusable tenant conversation owner. It stores a durable
thread, every message in that thread, its lifecycle, and each operator's read
cursor. Channel names remain product vocabulary; the module branches only on
declared threading and message-identity traits. Thread creation and message
recording are conflict-safe and replay the durable winner for exact redelivery;
reusing a message identity with different content fails closed.

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
