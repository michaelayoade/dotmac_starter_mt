# Dotmac host-agent protocol v1

This document is the connector's required target API. No target implementation
is included in this distribution. A separately deployed agent must satisfy this
contract before a binding may activate or Seabone acceptance may begin.

## Transport and authority

- HTTPS only, TLS 1.2 or newer, mutually authenticated against a dedicated CA.
- The connector presents a client certificate and a separately held bearer
  token. The agent verifies both and binds them to one `agent_identity_ref`.
- HTTP redirects are never followed. All bodies are UTF-8 canonicalizable JSON
  and no body may exceed 1 MiB.
- `x-request-id` is a UUID used for correlation, never authorization.
- The API has no generic route, path suffix, script name, shell text, argv,
  environment map, executable bytes, upload-and-run, file path or SSH surface.
- PLAN and OBSERVE are read-only. APPLY and CANCEL mutations are durably
  idempotent by `idempotency_key` plus the exact request fingerprint, across
  agent restarts. Reuse with another fingerprint is refused, never replayed.

## Capability identity

For each exact capability the agent exposes:

`GET /v1/capabilities/{capability_id}`

The response has exactly:

```json
{
  "agent_identity_ref": "host-agent/seabone-1",
  "capability_id": "host.health-probe.lifecycle.v1",
  "contract_digest": "sha256:<64 lowercase hex>",
  "evidence": {},
  "protocol_version": 1,
  "schema_digests": ["sha256:<exact operation schema digest>"]
}
```

`contract_digest` and the sorted eight-element `schema_digests` array must
equal the exact `dotmac-managed-host-contracts 0.1.0a1` declaration embedded in
the connector. `agent_identity_ref` must equal installation config.

Capability evidence has a closed shape:

### Deployment bundle

```json
{
  "bundle_catalogue_digest": "sha256:<64 lowercase hex>",
  "bundle_catalogue_ref": "sha256:<same digest>",
  "bundle_catalogue_signature_valid": true,
  "bundle_operation_version": 1
}
```

The target implementation must verify the catalogue signature against a
locally installed publisher trust anchor before returning `true`. A response
field is evidence of that completed check, not permission to trust arbitrary
catalogue bytes. The catalogue is the only mapping from bundle/version/artifact
and configuration digests to closed host actions. It may not map to arbitrary
caller-provided commands.

### Backup/restore

```json
{
  "backup_storage_ref": "storage/policy-ref",
  "content_digest_algorithm": "sha256",
  "immutable_version_refs": true,
  "object_lock_enabled": true,
  "restore_by_exact_version": true
}
```

The target must create a new immutable object version for each backup, compute
the digest from stored bytes, and restore only the exact requested object and
version. “Latest”, mutable path aliases and source-side success markers are not
version evidence. Restore validation includes starting the restored workload's
closed health check; it is not merely a successful archive extraction.

### Health probe

```json
{
  "max_response_bytes": 1048576,
  "max_timeout_seconds": 300,
  "probe_kinds": ["http_roundtrip", "liveness", "readiness", "service"],
  "response_digest_algorithm": "sha256"
}
```

Each `probe_ref` is resolved from a local, signed closed probe catalogue. It may
not contain a URL, command or file supplied by the caller. HTTP targets are
predeclared and SSRF-bounded by the target agent. `latency_milliseconds` uses a
monotonic clock and `response_digest` covers the bounded observed response.

## Operation routes

For each capability and exact operation:

`POST /v1/provision/{capability_id}/{plan|apply|observe|cancel}`

Every request includes `protocol_version`, `capability_id`, `operation` and an
exact owner-schema `target`. APPLY adds `operation_ref` and `idempotency_key`;
OBSERVE adds `operation_ref` and `provider_operation_ref`; CANCEL adds those
plus `reason` and `idempotency_key`. These are protocol-envelope fields, not
owner operation-schema fields. Command/approval IDs, plan hashes and capability
instance identity remain in Integration and are never accepted as host actions.

PLAN returns exactly:

```json
{
  "capability_id": "<exact id>",
  "evidence": {"<exact owner PLAN output>": "..."},
  "operation": "plan",
  "protocol_version": 1
}
```

The target may describe only changes produced by its trusted local catalogue.
It never changes the connector's signed steps.

APPLY, OBSERVE and CANCEL return exactly:

```json
{
  "capability_id": "<exact id>",
  "error_code": null,
  "evidence": {"<exact public owner output>": "..."},
  "operation": "apply",
  "outcome": "succeeded",
  "protocol_version": 1,
  "provider_operation_ref": "opaque-agent-operation-ref"
}
```

All operation routes return HTTP 200 for a syntactically valid protocol
response, including accepted, pending and failure outcomes; semantic state is
carried only by `outcome`. Transport/authentication failures use their ordinary
non-2xx status. This prevents an HTTP client from guessing whether `202` means
an accepted agent operation or an incomplete response document.

`outcome` uses Integration SPI 1.2's closed status vocabulary. Successful or
cancelled outcomes carry exact public output-schema evidence and no error.
Accepted/pending outcomes carry no evidence or error and require an opaque
operation ref. Failure outcomes carry an `[a-z][a-z0-9_]*` code and empty
evidence. Responses have no detail/string field where host output could leak.

## State and evidence ownership

- Integration owns command approval, capability-instance selection, dispatch,
  retry policy, receipts and reconciliation.
- The target owns only the local bundle/backup/probe execution state and its
  durable effect-idempotency record.
- The connector is stateless and owns neither.
- Successful evidence is public non-secret by the exact owner output schema.
  stdout/stderr, environment, file contents, authorization material, private
  keys and backup payload bytes can never enter a response.

## Seabone acceptance gate

Acceptance uses a disposable agent identity and isolated host/workload. It must
prove:

1. TLS server trust, required client certificate, token binding, wrong-identity
   refusal, redirect refusal and response-size bounds.
2. Exact three capability declarations and all activation sensitivities.
3. One signed catalogue bundle install, observe, upgrade, rollback and
   decommission, with an unknown bundle/version refused before execution.
4. One immutable backup, exact-version restore and post-restore health proof;
   overwrite/latest/version-substitution probes must fail.
5. All four health kinds, timeout enforcement, response digest and an SSRF
   target outside the local signed probe catalogue refused.
6. Replayed identical APPLY returns the prior result; reused idempotency key
   with changed target refuses; a response lost after mutation reconciles by
   OBSERVE without a second effect.
7. Static and runtime proof that no route or document can express shell, argv,
   SSH, executable upload, arbitrary file execution or secret output.
