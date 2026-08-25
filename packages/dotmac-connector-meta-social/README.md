# dotmac-connector-meta-social

Meta Social plugin for the independently deployed Dotmac Integrator. It
verifies the Meta subscription challenge and exact request bytes, normalizes
Facebook Messenger, Instagram DM, Facebook comment and Instagram comment events
into `messaging.receive.v1` observations, and carries product-decided outbound
replies through `messaging.send.v1`.

Four outbound operations, all reached through one `messaging.send.v1` binding:

| action | `channel` | Graph call |
| --- | --- | --- |
| `send_direct_message` | `facebook_messenger` | `POST /{v}/{page_id}/messages` |
| `send_direct_message` | `instagram_dm` | `POST /{v}/me/messages` (Instagram Login) or `POST /{v}/{ig_account_id}/messages` (shared OAuth) |
| `reply_to_comment` | `facebook_comment` | `POST /{v}/{parent_comment_id}/comments` |
| `reply_to_comment` | `instagram_comment` | `POST /{v}/{parent_comment_id}/replies` |

**Whether a reply may be sent at all is not decided here.** The messaging
window, the permission to respond, which draft wins and what it says are the
owning product's decisions, taken before a command reaches this package. The
connector translates the command, makes one call, and classifies what came
back. It computes no clock and holds no duration; a provider refusal is
reported, never anticipated.

Outcomes are typed: `SUCCEEDED` with the provider's message reference,
`RETRYABLE` for throttles and transient Graph failures, `TERMINAL` for policy
refusals and untranslatable commands, and `RECONCILIATION_REQUIRED` when the
send may have landed but cannot be named — a read timeout after the request
started is never retried as though it failed.

The package owns no database, product decision, retry policy, destination,
provider schedule, or outbound ledger. Runtime configuration binds the logical
secret names declared by the manifest. Values are materialized by the
Integrator and are never persisted by this package; provider response prose is
never retained.
