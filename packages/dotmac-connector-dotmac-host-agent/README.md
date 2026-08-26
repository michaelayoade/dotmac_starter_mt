# dotmac-connector-dotmac-host-agent

Integrator-side client for the separately deployed, constrained Dotmac host
agent. It implements the exact managed-host `PROVISION` capabilities:

- `host.deployment-bundle.lifecycle.v1`
- `host.backup-restore.lifecycle.v1`
- `host.health-probe.lifecycle.v1`

This wheel is not the target agent. It never opens SSH, accepts shell text or
argv, uploads an executable/file, chooses a process, or runs host commands. It
transports only owner-schema documents to capability- and operation-specific
HTTPS routes. The exact target API still required is frozen in
`TARGET_AGENT_API.md`.

Every call first reads the target's capability declaration and verifies the
exact owner contract digest, all eight operation-schema digests, the configured
agent identity and a capability-specific activation proof:

- deployment requires a content-addressed catalogue reference, the same target
  digest, a valid catalogue signature and bundle operation version 1;
- backup requires the configured storage reference, immutable version refs,
  object lock, SHA-256 content digests and restore-by-exact-version;
- health requires the four closed probe kinds, timeout at most 300 seconds,
  bounded response evidence and SHA-256 response digests.

Because the three owner contracts intentionally have different installation
configuration, use one connector installation/config revision per capability
family. Connection validation selects exactly one family from that declared
config shape; it never probes unrelated capabilities using configuration they
do not own.

The real transport requires an exact held `agent_secret_ref` document containing
`identity_ref`, `expected_origin`, `authorization_token`, and absolute paths to
the CA, client certificate and client private key. `identity_ref` and origin
must match installation config exactly. It constructs a TLS 1.2+ mutual-TLS
context per invocation, refuses redirects and environment proxies, accepts only
a strict HTTPS origin and closed `/v1/capabilities/**` or
`/v1/provision/**/{plan,apply,observe,cancel}` paths, and bounds request/response
bytes and connect/read/write/pool time. Held material never appears in repr,
exceptions, agent bodies, or public evidence.

Apply and cancel uncertainty is classified `ambiguous`; plan, activation and
observe transport failures are retryable. A syntactically successful mutation
whose response cannot validate is also ambiguous, because the target may have
acted before emitting invalid evidence. Integration owns retries and receipts;
the target API must own durable idempotency for its local host effects.

The first release depends on unpublished `dotmac-kernel 0.1.0a69`,
`dotmac-integration 0.1.0a6`, and `dotmac-managed-host-contracts 0.1.0a1`.
Release wiring belongs in a later change after those artifacts publish and
Observer CI executes these unrun package canaries.
