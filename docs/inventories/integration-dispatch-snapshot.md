# Receipt first-dispatch snapshot — source draft

This draft freezes dispatch identity in the receipt claim transaction before a
request can reach a product. It changes no published version, capability
message, frozen command, ProductObservation v1/v2 document or fingerprint.
Hosted CI acceptance and consumer adoption are outstanding.

`receive_verified` explicitly writes the version-one `unstarted` marker into
nullable `delivery_snapshot_json`. There is no database default or historical
backfill. The first dispatchable claim freezes source provenance and the full
DestinationBinding, including the complete negotiated descriptor when present.
It also freezes the full declared CapabilityContract for every route, including
legacy routes without a descriptor, and corroborates that declaration on retry.
It records the fingerprint, idempotency key, correlation and redundant route
columns in the same transaction as the lease. The snapshot contains no second
payload copy, headers, config, secret references, credential or client endpoint.
Relative product-port paths remain part of the routing contract.

A retry reconstructs the request from the frozen contract, the sole retained
receipt payload and original verification context. It checks the fingerprint,
key, correlation and redundant destination fields before network I/O. A v2
request still requires original receipt origin; unknown origin stays unknown.
The live route is corroboration only. Exact equality includes historical
revision UUID *and contents*, descriptor digest, product binding UUID, scope,
paths and wire selection. A changed or revoked authority refuses, never
reroutes. Both the binding and installation must currently be enabled, and an
explicit product descriptor must be enabled. These are newly explicit dispatch
requirements; legacy descriptors without activation facts are still subject to
the binding and installation checks.

Null historical snapshots refuse with the fixed privacy-safe
`dispatch_snapshot_unavailable` code and `indeterminate` acceptance, advancing
a due nonterminal receipt to `reconciliation_required` without a gateway call.
Zero attempts is not proof of freshness because authorized replay resets the
retry budget. Replay preserves snapshots and all frozen identity. Old terminal
rows remain untouched. Preparation refusals return a normal DeliveryReport
whose `preparation_refused` flag distinguishes them from held/not-due work.

The successor `ig_0018_dispatch_snapshot` (after `ig_0017_provisioning`) leaves legacy nulls unchanged, checks
versioned snapshot shapes and installs a final AFTER INSERT/UPDATE guard.
Frozen receipt identity cannot be overwritten by direct SQL or earlier BEFORE
triggers. The actual claim's full destination and source must match its typed snapshot
before dispatch and settlement, including metadata absent from published
fingerprints. Settlement compares the frozen identity and exact current lease;
it changes only lifecycle, outcome, retry and lease fields. Existing retention
may replace the sole payload with its digest-bearing tombstone or null, or
delete the receipt. Redacted payloads cannot be restored or dispatched through
the snapshot. Downgrade refuses to erase retained frozen dispatch identity.
The module stays platform-only with existing ACLs and no app_user privileges.

Adoption must quiesce old receipt workers before the migration and switch to
the new claim path. Old workers do not understand this contract. Continuity of
assembly-authenticated client credentials and endpoint/origin routing is not
proved by this slice; no end-to-end, cash, Gate0 or deployment claim follows.

Hosted canaries are authored for receiver commit followed by worker death,
real ReceiptClaims/Postgres retries, changed route and authority refusals,
concurrency, rollback, stale settlement, direct SQL tampering, actual predecessor
migration, null legacy attempt resets, replay and payload retention. Local
validation uses only stdlib syntax inspection and pinned Ruff; it is not runtime
or publication evidence.

The existing structural custom-store seam remains compatible when it supplies
no snapshot. Such stores do not satisfy or inherit ReceiptClaims' hardened
durable persistence guarantee. Every snapshot-bearing custom-store claim must
match its snapshot before dispatch; actual ReceiptClaims always requires and
checks that snapshot during preparation and settlement.

CapabilityContract's declared alternatives remain exact: one or more published
schemas require a payload contract digest and no schema grace. An explicit
schema grace requires all three schemas and the digest to remain null; the
snapshot never invents agreement with an unpublished payload contract.
