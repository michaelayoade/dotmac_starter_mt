# ADR-0077: Device CLI drivers are stateless knowledge over a host-supplied channel

- Status: Accepted (owner decisions of 2026-10-25; effective when this record merges)
- Date: 2026-10-25
- Decider: Michael
- Related: ADR-0006 (product-first extraction, dossier states), ADR-0017
  (adoption is the scarce resource; amended here), ADR-0024 (applications are
  independent), ADR-0038 section 6 (OLT vendor protocol I/O belongs to
  Integrator connector plugins; OLT/ONT business logic stays Sub authority),
  ADR-0074 section 4 (the boundary clarification this record relies on; see
  "Boundary" below, which restates it so this record stands alone); AGENTS.md
  rules 24 and 32; inventory
  [`device-cli-sources.md`](../inventories/device-cli-sources.md)

## Context

Dotmac Sub drives OLTs over SSH with roughly 12.9k lines of hand-written
dialogue, parsing and limiting code. About 7-8k of it is device *knowledge and
dialogue logic* that does not depend on Sub: capability facts per model,
prompt/pager/continuation/confirmation/logout handling, the response
classifier, the table parsers and the limiter policy. Three overlapping session
mechanisms and several parsers that return `success=True, data=[]` for empty
output (the "observed 0" defect class) live in it. More vendors and CPE will
follow (ZTE, more Huawei models, FiberHome, Nokia), and each should arrive
mostly as data plus recorded transcripts, built once.

Four standing texts keep device transport, sessions and credentials out of
Starter:

1. The 2026-09-02 boundary ruling: provider-specific connector plugins leave
   Starter.
2. ADR-0074 section 4 (a decision whose record is not yet on `main` when this
   one is written): no I/O, no credentials, no transport in Starter; a
   connector, session, SSH/REST/SNMP client or credential holder stays outside;
   the clarification is specific to a named package and is not a general
   admission route for provider code.
3. ADR-0038 section 6: OLT vendor protocol I/O belongs to Integrator connector
   plugins; OLT/ONT business logic stays Sub authority.
4. Starter is a public repository: tests use synthetic data only.

ADR-0017's supply-push moratorium also applies and needs a named exception.

## Decision

All four answers are Michael's, given on 2026-10-25.

### 1. Placement: knowledge in Starter, transport in the host (D1 option A)

A new distribution, `dotmac-device-cli`, holds device **knowledge and dialogue
logic** and nothing else:

- capability profiles as layered, validated data (vendor, family, model,
  firmware);
- a dialect interface per vendor for addressing, command composition and
  parsing;
- the dialogue state machine, run over an injected channel;
- the response classifier, vendor-neutral typed operations and results;
- limiter *policy* over an injected lock store;
- a reusable conformance suite and a synthetic transcript corpus.

The host (Sub, later Integrator) keeps everything that touches the outside
world: the SSH connect/authenticate runner that yields an already-authenticated
interactive channel, the Postgres advisory-lock store adapter, the pool, the
metrics sink, settings and credential decryption, and every database read or
write. If a second host ever needs a shared runner, its home is Integrator
(ADR-0038 section 6), not Starter.

### 2. Boundary (D2)

This record names `dotmac-device-cli` under the ADR-0074 section 4
clarification of the 2026-09-02 ruling: **no I/O, no credentials, no transport
in Starter.** The package:

- imports no socket, TLS, SSH, subprocess, database, HTTP, environment or
  filesystem-writing module, and never opens a connection;
- never receives, stores or names a login credential (a secret that must appear
  inside a command is a parameter the host supplies, redacted by name, and its
  line is never captured);
- holds no host key, address, site name or fleet count in a profile.

It is enforced by an import-linter contract and by an AST test with a
sensitivity proof. The clarification is specific to named packages and is not a
general admission route for provider code.

**This is a widening, and it is the owner's.** ADR-0074 section 4 admits exactly
one provider-specific package (`dotmac-routeros-config`). D2 adds
`dotmac-device-cli` as a **second named package** under the same "no I/O, no
credentials, no transport" clarification. Michael approved that widening on
2026-10-25. It widens the list of named packages and nothing else: the runner,
credentials and transport remain outside Starter, and the 2026-09-02 ruling and
ADR-0038 section 6 are otherwise unchanged.

### 3. ADR-0017 named exception (D2)

Decision 2 of ADR-0017 (the supply-push moratorium) is lifted for exactly one
named unit, `dotmac-device-cli`. See the dated amendment in ADR-0017. It is an
owner-directed exception, not evidence that a product is independently blocked.
Product-first treatment, dossier states, the adoption test and release ordering
are unchanged. Status starts `audit-complete`; it moves to `adopted` only on an
`adoption_evidence` row naming an immutable Sub commit (a pin is installation).

### 4. Classification and shape

`stateless-protocol-adapter`: called, not installed. No `ModuleManifest`, no
`mod_*` namespace, no lineage, no kernel allocation, no kernel floor, standard
library only. Sub therefore never raises its kernel pin to read this library.
(The package ships no database schema; `db_schema` does not apply.)

### 5. Composability: new models and vendors are data (owner scope, 2026-10-25)

- Profiles are layered files (vendor, family, model, firmware); an ordered deep
  merge resolves them; a layer sets only what differs; unknown keys fail.
- A new vendor adds one dialect, its layers and transcripts. The engine and the
  typed operations depend only on the dialect interface and never name a vendor.
- Operations and results are vendor-neutral; `OntRef` keeps the vendor-native
  string. A ZTE dialect stub, declaring no capabilities and inventing no
  command, ships to prove the interface fits a second addressing scheme.
- **Capabilities are declared per profile; undeclared means unsupported.** The
  result is an explicit `Unsupported`. There is no fallback to another model's,
  family's or vendor's syntax.
- Profiles resolve from the configured vendor, model and firmware. `detect()`
  reports a mismatch against the device's own version text; it never selects.
- A conformance suite every profile must pass, including a synthetic transcript
  and hand-written golden for **every capability the profile claims**, runs in CI.

### 6. Corpus and publication (D9)

Real captures stay in Sub's private repository. Starter's corpus is synthetic
only: serials `48575443` + `0000NNNN`, descriptions
`CUSTOMER_NN_zone_Zone_1_authd_20240101`, RFC 5737 addresses, MAC
`00:00:5E:00:53:NN`, hostname `lab-olt`, user `operator`. A publication guard
fails (it never cleans). Goldens are written by hand from the device's own
words and are never regenerated from the parser under test. Real-capture parity
is a Sub-side backtest.

### 7. Sequencing (D10)

The first Sub swap (reads) may ship before the IPAM W1 switch and never in the
same release or freeze window. `dotmac-device-cli` is released only in gaps
between billing and IPAM releases (AGENTS rule 32), never bundled and never
concurrent. Agents do not dispatch a release; Michael approves the exact head
and every merge.

### 8. What this decision does not do

It does not publish, release, pin or adopt anything, and it does not add a
release-allowlist row (the package is source only; the release lane is a later,
separately authorized step). It does not move command execution, SSH, locks or
credentials into Starter, and it relaxes no security control. It makes no
statement about writes: write templates, the `WriteGrant` mechanism and lab
write/undo receipts are a later decision. It does not decide host-key pinning,
free-text allow-list tightening, paging-off, firmware upgrade or lab hardware
(open decisions D3-D8 of the design note).

## Consequences

- One new distribution under `packages/`, carrying `EXTRACTION.toml`, a source
  inventory, an import-linter contract and architecture tests.
- A later release row will change the release-authority digest and is made in
  its own change.
- Sub gains a code-only swap path with rollback by redeploying the previous
  release; the lock keys use Sub's derivation byte for byte so old and new code
  share slots during the swap. Sub does **not** currently pin key vectors (its
  tests only check that keys are distinct), so the vectors are pinned **in this
  package** and must be mirrored into Sub's tests when Sub adopts it; until then
  drift on Sub's side is not detected by Sub. The `device_key` passed to the
  limiter **MUST** be `str(olt.id)`, as Sub passes today; any other string
  addresses different slots and silently doubles the sessions the device sees.
- ZTE, more Huawei models, FiberHome and Nokia cost data, transcripts and, where
  addressing differs, one dialect.
- Governance overhead is accepted in exchange for one shared, public, reviewable
  owner of device dialogue.
