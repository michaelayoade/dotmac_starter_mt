# ADR-0068: The kernel owns the capability artifact grammar

- **Status:** Accepted
- **Date:** 2026-08-26
- **Decision owner:** Dotmac platform architecture
- **Supersedes the numbering of:** the archived ADR-0034 on
  `archive/worktrees/2026-08-25/starter-managed-services-working`, whose number
  is held on `main` by ADR-0034 (media observations). The archive branch minted
  its own numbers; they are not reservations.
- **Related:** [ADR-0006](0006-white-label-product-foundation.md) (the
  contract-catalogue classification and its release lane),
  [ADR-0008](0008-manifest-declaration-registries.md),
  [ADR-0009](0009-secrets-are-held-not-dereferenced.md)

## Context

Mailcow, Nextcloud and Keycloak are upstream products. Their admitted container
images correctly carry `upstream_third_party` evidence: provenance, signature,
SBOM, vulnerability-policy result and compatibility result. Release Catalog
correctly refuses to attach a Dotmac Product Manifest or a Dotmac-owned
capability contract to those bytes.

The managed-service capabilities are nevertheless Dotmac contracts. A Vendor
profile must bind their exact owner, operations, schemas, configuration fields,
endpoints and activation/evidence checks before it can approve a deployment.
Putting those meanings in Vendor would make the commercial control plane the
owner of every product protocol. Putting them in a connector would make a
provider plugin able to redefine the contract it claims to implement.

The two artifacts therefore answer different questions:

- the upstream component artifact says exactly which third-party software will
  run; and
- a Dotmac contract artifact says exactly which provider-neutral lifecycle that
  component is required to satisfy.

They must not be relabelled into one artifact merely to simplify a join.

## Decision

### 1. One independently released contract catalogue per owner

The first managed-service contract artifacts are Python-wheel distributions:

| Distribution | Product/owner code | Initial capability families |
|---|---|---|
| `dotmac-managed-identity-contracts` | `dotmac-managed-identity` | realm, OIDC-client and stable-reference user lifecycle |
| `dotmac-managed-email-contracts` | `dotmac-managed-email` | `email.lifecycle.v1` (application/domain/mailbox/alias/quota/delivery/app-password/DKIM) |
| `dotmac-managed-collaboration-contracts` | `dotmac-managed-collaboration` | `collaboration.application.lifecycle.v1`, `collaboration.user-oidc.configuration.lifecycle.v1`, `collaboration.user-group-quota.lifecycle.v1` and `collaboration.file-roundtrip.lifecycle.v1` |
| `dotmac-domains-contracts` | `dotmac-domains` | `dns.authoritative.v1` |
| `dotmac-managed-infrastructure-contracts` | `dotmac-managed-infrastructure` | `infrastructure.instance.lifecycle.v1`, `infrastructure.network.lifecycle.v1`, `infrastructure.volume.lifecycle.v1` and `infrastructure.firewall.lifecycle.v1` |
| `dotmac-managed-host-contracts` | `dotmac-managed-host` | `host.deployment-bundle.lifecycle.v1` (including upgrade/update), `host.backup-restore.lifecycle.v1` and `host.health-probe.lifecycle.v1` |
| `dotmac-managed-suite-contracts` | `dotmac-managed-suite` | value-free cross-capability evidence compositions over exact-pinned owner catalogues |

ERP, Academy and Workspace publish their application-lifecycle contracts with
their own product artifacts. They are not copied into one of these catalogues.

The managed-collaboration application contract owns the provider-neutral
backup, restore, upgrade, suspend, resume and decommission actions plus their
health/rollback evidence. It does not own or expose a host command surface. Its
OIDC configuration contract fixes immutable issuer/subject mapping,
preprovisioned-only accounts, no email linking, S256, audience/azp checks,
backchannel logout and provenance-bound revocation. Installation endpoints are
declared once in `endpoint_requirements`; ordinary and secret references use
the disjoint `config_fields` namespace. Integration supplies all of those
installation values separately from operation input, and no operation request
repeats them. A code appearing in both namespaces is an invalid owner contract,
not two compatible declarations of the same value.

Managed Identity's user lifecycle is the joiner/leaver owner for the identity
provider only. It locates accounts by an immutable Dotmac reference, treats
email, login name and display names as attributes rather than binding keys,
and returns public exact issuer/subject evidence for product-owned account
bindings. It carries no password, group, role or product authorization. A
disable is successful only after provider sessions have been revoked; product
sessions and application access remain each product owner's separate action.

Managed Email's application resource also owns its relying-party OIDC
configuration. It requires a pre-created held client-secret reference at the
installation boundary and accepts only public desired values in the operation:
exact issuer and client id, HTTPS redirect/logout URIs, RS256, S256,
audience/azp validation, immutable issuer/subject binding, no JIT/email linking,
and Mailpassword Flow disabled. The Keycloak client remains an Identity-owned
resource; the suite composes only its public issuer/client evidence into the
Email application input.

All four schema pairs are executable runtime gates, never decorative
attestation pins. Plan and apply validate the approved provider-neutral step
target. Observe and cancel receive only the fields their held input schemas
declare, derived from the immutable original step rather than caller-carried
replacement values. Command ids, plan hashes, operation/provider references,
configuration and secrets remain in the Integration envelope and are not
repeated in owner inputs. Each successful operation result is validated against
its held output schema before only schema-classified public/non-secret evidence
is projected into the immutable module receipt.

Each catalogue is stateless: no module manifest, database, migration, provider
client, endpoint, secret value, retry engine or business decision. It ships:

1. one canonical `ProductManifestSnapshot` for its owner code;
2. one or more canonical `CapabilityContractSnapshot` documents declared by
   that manifest;
3. the exact canonical input/output schema bytes whose digests the snapshots
   carry; and
4. provider-free types, fixtures and port conformance tests.

A contract snapshot's `capability_code` is the unversioned domain identity and
`schema_version` is a separate integer. The Product Manifest publishes the
external id as `<capability_code>.v<schema_version>`. Embedding `.v1` in the
contract code as well would encode the same version twice and make a v2
contract look like a different domain capability rather than a new schema
version.

The contract's `capability_code` never carries a `.vN` suffix. Its separate
positive `schema_version` derives the public manifest and Integrator wire id as
`{capability_code}.v{schema_version}`. This prevents a catalogue from encoding
the version twice or letting the two representations drift.

A composition-only suite catalogue may own zero capability contracts. It
exports exact dependency contracts/schemas separately from its empty owned
sets, pins those dependency distributions exactly, and publishes canonical
`CapabilityCompositionSnapshot` documents. Its Product Manifest never claims
the dependency capabilities as its own.

A capability may have multiple deployment instances, including multiple
instances of one resource-discriminated contract. A composition binding may
therefore carry optional source and target instance selectors. Each selector
is an RFC 6901 pointer plus a stable string value, and the kernel accepts it
only when the exact held APPLY input schema closes that pointer with a matching
`const` or `enum`. Vendor still records the explicit source-instance to
target-instance mapping; it may not infer an edge from names or product
semantics. The selector only proves that the owner-signed edge applies to those
chosen instances, preventing email OIDC evidence from being injected into a
domain or mailbox document that shares `email.lifecycle.v1`.

Every v1 binding is required and declares one of two coverage rules:
`each_source_exactly_one` or `each_target_exactly_one`. Vendor validates the
explicit deployment-instance edge set against that rule before planning. This
distinguishes, for example, “every OIDC client target receives one realm
issuer” from “every email domain or DKIM source supplies one DNS recordset
target”; accepting an arbitrary subset would make a required composition
silently optional, while requiring every source-to-every-target pair would
inject unrelated resource evidence.

The distribution is the immutable Dotmac product artifact to which Release
Catalog attaches `product_manifest`, `capability_contract` and
`capability_schema` attestations. A composition catalogue instead carries a
`capability_composition` attestation plus exact dependency artifact pins. It
does not become the deployed upstream
application image.

### 2. Artifact kind is part of the Vendor pin

Vendor's product-release pin records the exact artifact digest, artifact kind
and Product Manifest digest. Its catalogue lookup matches all three and then
verifies the held canonical documents. Contract catalogues therefore use
`python_wheel`; normal Dotmac application releases may use `container_image`.
The old hard-coded `container_image` filter is rejected because it would force
an upstream image to carry Dotmac product ownership evidence it is forbidden to
carry.

### 3. A separate release profile verifies the stronger shape

Contract catalogues use the already governed
`stateless-protocol-adapter` extraction classification because that value
governs their shared stateless shape: called rather than installed, no lineage,
no persistence. `contract-catalogue` is a stricter release profile, not a fifth
classification, following the connector-release precedent.

Its closed allowlist and installed-wheel gate additionally verify the Product
Manifest identity, every capability contract's ownership/canonical digest,
every referenced schema byte and digest, the provider-free conformance surface,
every composition's exact dependency coverage, and the absence of
network/process/secret material. Passing the ordinary
adapter public-surface gate is not enough.

### 4. Resource kinds never replace engine operations

Every provisioned capability declares Integration SPI 1.2's `plan`, `apply`,
`observe`, and `cancel` operations with exact request/result schemas. Domain
objects such as DNS `zone`/`recordset`, Mail `mailbox`/`alias`, or IaaS
`instance`/`network` are closed resource kinds inside those schemas. A catalogue
that substitutes resource kinds for the four engine operations is refused by
the Integration conformance gate.

### 5. Version one remains secret-output-free

Schemas may name `secret_reference` configuration fields. No request/result,
fake, fixture, exception or evidence schema may contain a secret value or a
generated-secret output. A provider operation that cannot accept pre-created
held material stays unsupported until a separately approved secret-write
boundary exists.

### 6. The kernel owns the DOCUMENT; Integration keeps the LIFECYCLE

This release adds `dotmac_kernel.capability_contract` and
`dotmac_kernel.capability_composition`. It adds no registry, installs nothing,
and runs nothing.

`dotmac_integration.capability_registry` already exists on `main` and answers a
different question. The split is exact, and neither side may define what the
other owns:

| Concern | Owner |
|---|---|
| Canonical bytes, digest, identity, ordering | **kernel** — the document |
| Operations, config fields, endpoints, checks | **kernel** — the document |
| Cross-capability evidence bindings and coverage | **kernel** — the document |
| Installed runtime registry and its refusals | **Integration** — the lifecycle |
| `SchemaGrace`, an ungated adoption window | **Integration** — the lifecycle |
| `ContractDeprecation`, succession | **Integration** — the lifecycle |

A second registry in the kernel would create a second authority, so there is
deliberately none. Equally, a second document definition in Integration would
let a runtime redefine what an owner attested, so Integration's inline
`command_schema`/`result_schema`/`observation_schema` triple is a **retirement
candidate**, not a parallel spelling to be maintained.

### 7. Two questions Integration must answer when it adopts

Landing the grammar without naming these would leave two defensible answers in
the tree, which is how parallel authorities start. They are recorded here as
decisions, to be executed by the Integration change, not by this one.

**Which digest does a connector claim?** `CapabilityDeclaration.claims_contract_digest`
must come to mean `CapabilityContractSnapshot.digest` — the digest over the
whole canonical owner-attested document. It must NOT continue to mean a
`canonical_digest` computed over three inline dicts. Two digests for "the
contract" is one digest too many, and the owner's is the one that binds.

**Does a snapshot-backed contract exit grace permanently?** Yes. `SchemaGrace`
exists because a contract could be registered with no schemas at all; a
snapshot-backed contract has schemas by construction, so it is never eligible
for grace, and adopting a snapshot is a one-way exit. A contract that could
re-enter grace after presenting exact schemas would make the ungated window
reachable from the gated state, which is the wrong direction.

### 8. One repair made to the ported grammar

The port is otherwise verbatim. One behaviour changed, and it is recorded here
because "restacked from the archive" must not become cover for a silent
redesign.

`CapabilityCompositionSnapshot.require_compatible_with` resolved the SOURCE
capability's APPLY-**input** schema unconditionally, then handed it to a
selector check that returns immediately when the binding declares no selector.
Every caller therefore had to hold a document the proof never reads.

The asymmetry is the argument. An evidence binding joins a source APPLY-OUTPUT
to a target APPLY-INPUT; the source's own input says nothing about that edge.
And the target's OUTPUT schema — referenced by the very same pinned operation —
was never required. So "hold every schema the pinned operations reference" was
not the rule the function enforced; it demanded exactly one document beyond
what it used.

The schema is now resolved only when a selector actually consults it, to prove
the chosen instance value is closed by a `const` or `enum`. Two properties are
pinned, because simply deleting the fetch would also have made the suite pass:

- a selector binding whose source input schema is withheld must still be
  REFUSED — laziness must not turn a missing document into a skipped check; and
- a half-declared selector (pointer without value, or the reverse) is refused
  when the BINDING IS BUILT, in `CapabilityEvidenceBinding.__post_init__`, so
  it never reaches compatibility checking at all.

The second property is why the lookup was free to move. The concern when the
fetch became lazy was refusal ORDER — that a malformed selector might now be
reported as "source input capability schema is not held", blaming an absent
document for a defect in the binding. It cannot: the pair is validated at
construction, so `require_compatible_with` only ever sees bindings whose
selectors are already whole. That is pinned at the boundary which actually
holds it, rather than at the one that merely happens not to be reached.

This was reachable only because the archive's own test for the ordinary
no-selector path had never been run green. It is the reason a restack ports the
tests as well as the module, and runs them.

### 9. What this decision does NOT authorize

No catalogue becomes publishable because this grammar exists. The seven
candidate catalogues remain archive-only, and
`.github/release-contracts.json` remains closed and empty until each is
restacked onto a PUBLISHED kernel carrying these types and passes its own
conformance. A declared floor naming an unpublished kernel is not a floor
(ADR-0006's 2026-08-26 amendment and the executed-floor rule).

## Consequences

- Upstream admission and Dotmac lifecycle ownership remain separately
  auditable and independently replaceable.
- Vendor composes exact evidence but mints no product/provider vocabulary.
- A connector implements an immutable owner contract and cannot alter its
  schema to fit one provider.
- A contract-only release can advance without rebuilding a third-party image;
  a profile compatibility decision still controls whether a deployment may use
  the new contract version.
- Contract catalogue publication waits for the released kernel version that
  owns `dotmac.capability-contract/v1`; a checkout-only version is not a floor.

## Alternatives rejected

**Attach Dotmac manifests to upstream images.** Release Catalog correctly
forbids this; it would convert provenance class into a caller-selected label.

**Let Vendor own the schema catalogue.** That makes one commercial plane the
business/protocol owner for every product and requires a Vendor release for a
product contract change.

**Let each connector declare its own meaning.** Two providers could then claim
the same capability id while accepting different requests or evidence, so a
binding change would silently change the approved contract.

**Call contract catalogues ordinary adapters.** Their stateless classification
is shared, but the adapter release gate does not prove manifests, schema bytes
or owner conformance. A separate release profile keeps those checks mandatory
for every catalogue rather than optional for every adapter.
