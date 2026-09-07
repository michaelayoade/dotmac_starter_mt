# Changelog — dotmac-platform-health

## 0.2.0a1 — 2026-09-07

Allocate the canonical freshness-provenance and signed-health-evidence
refusal contract. Legacy observations retain unknown provenance; new
observations snapshot a positive policy value. Canonical signing now goes
through `produce_signed_health_evidence`, which builds from durable state and
accepts only `ed25519` signatures.

## 0.1.0a1 — 2026-08-22

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `8f52abc4` by release run `32570549230`.
The composition check registered this manifest alongside forms, workflow-runtime
against published kernel `0.1.0a88` before the tag was written. Publication
composes nothing into an assembly and adopts nothing.

- Add immutable bounded observations, deterministic projections and explicit
  fresh/stale/missing incident summaries on the platform plane.
