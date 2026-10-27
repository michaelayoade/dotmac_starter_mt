# ADR-0074: FreeRADIUS transport carries fetched secrets and declared network targets

- Status: Accepted (corrected 2026-10-27 before first commit: decision 1 as
  first drafted — ciphertext payloads through Integrator persistence with the
  connector holding the product's Fernet key — contradicted ADR-0076 §§7–8,
  accepted 2026-10-20, which had already settled secret crossing for this
  exact boundary. Decision 1 below conforms to ADR-0076; ADR-0076 is not
  superseded or amended by this record.)
- Date: 2026-10-27
- Decider: Michael
- Related: ADR-0024 (applications are independent; the Integrator is the sole
  external connector control plane), ADR-0038 (network capabilities decompose
  Sub-first; §6 Integrator owns external provider I/O), ADR-0076 (service
  access credentials have one owner; §§7–8 own secret storage and the
  command-to-effect contract with network-access and Integrator),
  `packages/dotmac-integration/COMPATIBILITY.md`,
  `packages/dotmac-network-access/EXTRACTION.toml`

## Context

ADR-0038 assigns NAS/RADIUS access projection, authentication/accounting
observations, sessions, reconciliation and drift evidence to
`dotmac-network-access`, and RADIUS delivery itself to an Integrator connector
plugin. The module's retirement gate is that Sub owns customer/service
eligibility and requests projection while Integrator plugins own provider
transport. No FreeRADIUS connector exists yet. Building one against Sub's
current implementation (`app/services/radius_population.py`,
`app/services/enforcement.py`) exposes two facts that the existing connector
SPI was not designed around:

1. **The projection payload contains subscriber passwords.** Sub stores each
   access credential as a Fernet ciphertext (`AccessCredential.secret_hash`,
   `app/services/credential_crypto.py`, with current + previous key support)
   and the incumbent writer decrypts it to write `Cleartext-Password` rows
   into the FreeRADIUS `radcheck` table. The FreeRADIUS site enables
   PAP, CHAP and MS-CHAP; CHAP requires the cleartext at the server. The
   Integrator, by contrast, persists outbound payloads and retains
   delivered-payload evidence, and its evidence model deliberately has no
   place for secret material.
2. **CoA targets are a fleet, not a host.** Disconnect applies to NAS devices
   addressed by IP (`NasDevice.ip_address`/`management_ip`/`nas_ip`,
   `coa_port` defaulting to 3799/UDP). The SPI 1.3 egress declaration admits
   only exact DNS hostnames baked into a connector manifest, and
   installation-provided hosts are deliberately absent from it. A changing
   fleet of IP-addressed UDP endpoints is not expressible. The same shape of
   problem applies to every other fleet protocol ADR-0038 §6 names for
   Integrator plugins (SNMP, RouterOS/router APIs, OLT vendor protocols).

Michael accepted the two decisions below on 2026-10-27. Acceptance settles the
transport contract shape; it does not itself authorize a release, a deployment
or the network-suite cutover, which keep their own gates.

## Decision

### 1. Subscriber secrets cross only by fetch, per ADR-0076 §§7–8

This record adopts ADR-0076's already-accepted crossing unchanged and binds
the connector work to it:

- Projection requests carry identifiers, credential id and secret version
  only — no cleartext and no ciphertext. Integrator persistence (outbox,
  delivery, retry and dead-letter rows, events, drift records) contains no
  secret in any form, and the connector holds no product credential key.
- The secret reaches the connector through the synchronous, mutually
  authenticated fetch ADR-0076 §7 defines: keyed by
  `(credential_id, secret_version)`, honoured only against an open projection
  request issued by network-access, at most once per request, rate-limited,
  every other fetch refused and audited. (ADR-0076's envelope-encryption
  variant remains available under that ADR; selecting it is an ADR-0076
  amendment, not a connector choice.) Plaintext exists only in the three
  places ADR-0076 §7 names.
- Storage and convergence follow ADR-0076: secrets are ciphertext in the
  tenant database under the credential owner's own keyring, fingerprints are
  keyed-HMAC values under a separate key, and convergence means every target
  reports the expected keyed fingerprint (§8.3). No fingerprint in this
  design is an unkeyed hash of secret material or of non-deterministic
  ciphertext.
- The durable provisioning runtime must therefore provide the machinery
  ADR-0076 §8 assumes: a durable open-projection-request record the fetch
  authorizes against (single-use per request), at-least-once apply with
  `process_once` consumers, Integrator-owned backoff, and an explicit
  failed-projection outcome network-access can surface — never a swallowed
  log line.

### 2. Fleet endpoints are declared network targets, not manifest hosts

- `dotmac-integration` gains an additive SPI minor (in the same release that
  ships the durable SPI 1.5 provisioning runtime) for installation-scoped
  **network targets**: CIDR ranges with protocol and port, declared inside the
  immutable connector configuration revision, schema-validated before
  revision write, binding and activation, and covered by the revision digest.
- The absence of a network-target declaration is deny-all for fleet traffic,
  exactly as the empty egress declaration is deny-all for provider hosts. The
  manifest egress declaration is unchanged and continues to name the
  provider-style hosts (for this connector, the FreeRADIUS database alias).
- Before sending any fleet packet, the engine-facing invocation path validates
  the step's target address/port/protocol against the bound installation's
  declared network targets. A target outside the declaration is a refusal
  before I/O, not a connector-level error after it.
- The Integrator assembly reports declared network targets per connector in
  its runtime-policy surface, beside egress hosts and secret-binding names,
  derived from the installed manifests — never restated as a second list in
  the assembly.
- This mechanism is protocol-neutral on purpose: SNMP, RouterOS and OLT
  connectors under ADR-0038 §6 declare the same shape rather than each
  widening the SPI again.

## Consequences

- CoA-Disconnect is in scope for connector v1 via the PROVISION mode, with its
  command lifecycle owned by `dotmac-network-control`; Sub's local CoA path in
  `enforcement.py` retires at the network-suite-v1 cutover as ADR-0038
  requires.
- The durable-provisioning release of `dotmac-integration` grows by one SPI
  design-and-conformance cycle. No connector work can be certified against
  the network-target rules until that release exists.
- Credential key custody does not widen: the owner's keyring stays inside
  the adopting assembly (ADR-0076 §7). What the Integrator gains instead is
  a synchronous product-facing fetch surface whose authentication, single-use
  authorization and rate limiting are security-reviewed runtime work, not
  connector code.
- FreeRADIUS and NAS authentication behavior is untouched: no site change, no
  PPPoE auth-method migration, and the `radcheck` contents at cutover are
  byte-identical to what the incumbent writer produces.

## Alternatives rejected

**Ciphertext payloads with a connector-held product key** (this record's
first draft, 2026-10-27). The projection step would carry the credential's
Fernet ciphertext through Integrator persistence and the connector would
decrypt with a secret-bound product key. ADR-0076 §§7–8, accepted a week
earlier, already forbids the secret from appearing in outbox, delivery,
retry or dead-letter rows in any form, keeps the owner keyring inside the
adopting assembly, and defines the single-use fetch instead. The draft was
written against a stale checkout that predated ADR-0076's merge; conforming
costs nothing at draft stage and keeps one crossing design fleet-wide.

**Hash-only `radcheck` (NT-Password).** Sub would store and transport the NT
hash instead of cleartext. CHAP cannot be answered from a hash, and the
current site enables CHAP, so this forces a fleet-wide PPPoE auth-method
migration with customer impact — to gain a marginal improvement, since the NT
hash is itself password-equivalent material for MS-CHAP and would still sit in
Integrator persistence.

**Split writer (Sub keeps password rows).** The connector would own
`radreply`/groups/sessions while a reduced Sub writer kept writing
`Cleartext-Password`. That leaves two writers on `radcheck`, fails the
ADR-0038 retirement gate, and defers the riskiest rows to a second cutover.

**Defer CoA from v1.** Projection and observation could cut over with egress
limited to the database host, leaving CoA in Sub. The SPI amendment would
still be owed by the first SNMP/RouterOS/OLT connector, `dotmac-network-control`'s
disconnect lifecycle would stay unexercised, and Sub would retain exactly the
provider-transport path the suite cutover exists to retire. Deferred, not
avoided — so it is paid once, now, in the release that already has to happen.

**Per-release NAS hostnames in the manifest.** Fleet membership changes would
require a connector release, and IP literals are invalid egress hosts by
design. It neither scales nor preserves the declaration's meaning.
