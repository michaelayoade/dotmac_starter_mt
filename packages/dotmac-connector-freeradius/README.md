# dotmac-connector-freeradius

FreeRADIUS SQL projection, observation polling and CoA/Disconnect connector
for the independently deployed Dotmac Integrator. It implements, over
SPI >=1.7,<2.0:

| capability | mode | what it does |
|---|---|---|
| `network.access_projection.v1` | PROVISION | renders one login's `radcheck`/`radreply`/`radusergroup` rows and applies them delete-and-insert in one target transaction, then verifies by read-back |
| `network.access_observation.v1` | POLL | cursors `radpostauth` + `radacct` into provider-neutral authentication/accounting observations |
| `network.session_disconnect.v1` | PROVISION | one RFC 5176 Disconnect-Request to one NAS endpoint inside the installation's bound fleet targets |

It owns no database, no access decision and no credential. For an enabled
login it fetches the secret exactly once per apply through the credential
owner's fetch port (bound by the assembly; the shipped default refuses
loudly), uses it in memory to render the password row, and never persists,
logs or echoes it. Desired state, policy and attribute values arrive in the
step input Sub authored; rendering those into Mikrotik/FreeRADIUS rows —
byte-identical to Sub's incumbent writer — is this connector's one job.

`subject_ref` in emitted observations is the RADIUS login, the only subject
identity the wire carries. Translating login to service-instance subject is
the receiving composition's job; this connector never invents one.

Not released: see `.github/release-connectors.json` (held back until the
SPI 1.7 integration release is tagged and shadow parity against Sub's
incumbent writer is green).
