# Public surface and stability — dotmac-deployment-foundation

## What is public

- `ProductDeploymentSpec` and every type it exposes, plus the `SCHEMA` string.
- `ProductDeploymentSpec.to_canonical_document()` and
  `DeploymentDescriptorDocumentV1` — its `canonical_bytes()`,
  `sha256_digest()` and the `DESCRIPTOR_DOCUMENT_SCHEMA` string. The BYTES
  are public contract exactly as the rendered assets are: a change to the
  document's shape changes every consumer's digest at once, which is the
  intended behaviour and makes it a MINOR bump at least.
- `IngressPolicy.v1`: the exposure vocabulary, the provider capability
  matrix, the derived endpoint-token format and the firewall rule shape.
- The `dotmac-deploy` CLI: its subcommands, its flags, and its **exit codes**
  (`0` ok, `1` refused, `2` usage). CI wires the exit codes, so they are part
  of the contract rather than an implementation detail.
- The rendered output of every renderer, treated as bytes: `render --check` is
  a byte comparison, so a whitespace change is a breaking change for every
  consumer that has committed the previous output.
- `DeploymentProvenance.v1`: `AuthorizationReceipt`, `DeploymentProvenanceV1`,
  `build_provenance()`, `normalize_digest()` and the `PROVENANCE_SCHEMA`
  string. The canonical BYTES are public contract on the same terms as the
  descriptor document's. `AuthorizationReceipt` is a typed INPUT this facility
  never produces: `dotmac-deployment-control` owns authorization, and the
  receipt is bound by VALUE so a zero-dependency build runner never acquires a
  stateful module and never reaches into another owner's state.
- `ExposureEffects` and `ExposureTransaction`, plus `OWNERSHIP_PREFIX`,
  `ownership_comment()` and `foreign_rules()`. Ownership is part of the
  CONTRACT rather than of one provider, because the transaction measures the
  preservation property against it: a shared filter chain is never restored
  wholesale, and an implementation that replays one is refused by the
  transaction rather than trusted not to.
- `Effects`, `Executor`, `DeploymentPlan`, `Step`, `StepKind`, `Strategy`.
- `conformance.*` — the functions a product calls in its own CI.
- `RESOURCE_ATTRIBUTES` and the alert `code` of every entry in `COMMON_ALERTS`.

## What is not

Anything underscore-prefixed, the internal layout of the renderers, and the
`Finding`/`Comparison` message TEXT (the `rule` and `verdict` values are
stable; the prose explaining them is not).

## The version rule

**A change to rendered bytes is a MINOR bump at least**, even when the change
is cosmetic, because every consumer has committed the previous bytes and
`render --check` will fail for all of them at once. That is the intended
behaviour — the alternative is a renderer that can change what a host runs
without anyone reviewing a diff — but it means a whitespace tidy-up is a
release, not a patch.

**A new REFUSAL in `spec.py` is a MAJOR bump.** A descriptor that parsed
yesterday and does not parse today breaks a consumer's build, and calling that
a patch is how a facility loses the trust it needs to be adopted. The right
shape for a new rule is: add it warning-only in a minor release, name the
version it becomes fatal in, then make it fatal in the next major.

### Deviation, 0.3.0a1: the exposure contract is fatal without a warning release

`IngressPolicy.v1` makes four descriptor changes fatal in this release,
skipping the warning-only minor the rule above requires:

- `PortPublication.bind` is REMOVED, and declaring it raises rather than being
  ignored;
- `exposure` and `address_family` are MANDATORY on every publication;
- `[ingress]` must declare its own `exposure` and `address_family`, and a
  public edge must carry `approval_ref` and `rationale_url`;
- `trusted_proxies` entries are source-set NAMES; a CIDR is refused.

The deviation is recorded here rather than taken silently, and it rests on four
premises a reader can check rather than on judgement:

1. **The consumer census is exactly one.** `EXTRACTION.toml` records one
   contract consumer, and the product-first gate derives the dossier's status
   from that count exactly. The warning phase exists to protect consumers who
   would otherwise break without notice; here there is one, and it is known.
2. **That consumer migrates in the same train.** Its descriptor changes
   alongside this release, so the notice a warning phase would have provided is
   provided by the change itself.
3. **A changed consumer set fails the migration gate.** If a second consumer
   appears, the recorded count no longer supports the dossier's status and the
   gate fails before this reasoning can be reused on a fleet it no longer
   describes. The premise is enforced, not asserted.
4. **Warning-only would still render the unsafe condition.** A release that
   accepts `bind` keeps emitting a publication whose address family is
   undeclared — and an undeclared family is not a tidiness problem. A
   short-form publish spawns one `docker-proxy` per family, so a descriptor
   silent about IPv6 gets IPv6 anyway, and the `ip6tables DOCKER-USER` rules
   written to cover it cannot fire. That is not hypothetical: two production
   DROP rules were measured with zero packet counters while the ports they
   named were open from the internet. A deprecation window is affordable when
   the old behaviour is merely untidy; it is not when the old behaviour is the
   defect.

This deviation authorises exactly these four changes. It is not a precedent for
skipping the warning phase generally, and a future removal without these four
premises holding is an ordinary rule violation.

**A new schema version is a new string.** `ProductDeploymentSpec.v2` is a
different `schema` value, and a v1 reader REFUSES a v2 document rather than
reading the subset it understands — a field an older reader cannot see may be
the one that disables a control.

## Consuming this package

Exact-pin it. A conformance gate resolving to "whatever is newest" cannot
distinguish a product that drifted from a foundation that changed, and the
reusable workflow refuses a range for that reason.
