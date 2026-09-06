# Public surface and stability — dotmac-deployment-foundation

## What is public

- `ProductDeploymentSpec` and every type it exposes, plus the `SCHEMA` string.
- `ProductDeploymentSpec.to_canonical_document()` and
  `DeploymentDescriptorDocumentV1` — its `canonical_bytes()`,
  `sha256_digest()` and the `DESCRIPTOR_DOCUMENT_SCHEMA` string. The BYTES
  are public contract exactly as the rendered assets are: a change to the
  document's shape changes every consumer's digest at once, which is the
  intended behaviour and makes it a MINOR bump at least.
- `FoundationExecutionPlanV1` and `ExecutionPlanDigestV1` — the document, its
  ten canonicalization rules, and the digest over it. The BYTES are the
  contract and two other systems bind to them: Platform CP submits the digest
  and Control freezes it, so a change to the document's shape, key set, key
  names, or any of the ten rules invalidates every frozen digest at once and is
  a MINOR bump at least. `dotmac-deploy execution-plan --format digest` is the
  only supported way to produce the value; re-implementing the canonicalization
  is what this contract exists to stop.
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
- `ExposureEffects`, plus `OWNERSHIP_PREFIX`, `ownership_comment()`,
  `foreign_rules()`, `foreign_rule_arguments()`, `managed_ports()` and
  `require_preserved_foreign_rules()`. Ownership is part of the CONTRACT rather
  than of one provider, because the preservation property is measured against
  it: a shared filter chain is never restored wholesale, and an implementation
  that replays one is refused rather than trusted not to. The measurement is a
  FUNCTION taking a before and an after observation, so a caller that never
  mutates can still make it.
- `ExposureReconciliationV1` and `FoundationExecutionPlanV2`'s
  `exposure_reconciliations` field — the typed authority to reconcile one
  address family's exposure. The member carries no rule, port, chain position
  or command: the rules are derived from the descriptor, whose digest is
  already in the plan and in the grant. Adding the field CHANGES the V2
  document's key set and therefore every V2 digest, which would be a MINOR bump
  at least — it is free here only because V2 has existed solely under the spent
  `0.4.0a1` and has never been published.
- `Digest`, `require_same_digest()`, `ALGORITHMS` and `CANONICAL_ALGORITHM`.
  The canonical serialization is `sha256:<64 lowercase hex>`; the bare form is
  accepted on INPUT as a compatibility affordance for
  `dotmac-deployment-control`'s `String(64)` column and is not a second
  canonical form.
- `RehearsalReceipt.v1`: `RehearsalReceiptV1`, `RequirementResult`,
  `RequirementStatus`, `REQUIRED_ITEMS`, `build_receipt()`,
  `verify_publication()`, `render_status_document()` and
  `render_pending_document()`. The sixteen item CODES are contract — a release
  gate reads them — and only `executed_passed` satisfies publication.
- `HostLease.v2`: `HostLease`, `load_lease()`, `write_lease()`. A lease is
  never self-granted; `authorization_run_id` is mandatory, and so is the
  `workload_principal` that holds and releases it. `HostLease.v1` is READABLE AS
  HISTORY through `HistoricalLeaseV1`, which has no `covers()` and no principal:
  legibility is not authority. This bullet said `.v1` until 0.4.0a1 and was
  stale by one schema.
- `HostLeaseRelease.v1`: `HostLeaseReleaseV1`, `TerminalOutcome`,
  `TerminalRefusal`, `HostClosure`, `CleanupDisposition`, `ReleasingPrincipal`,
  `write_release()`, `load_release()`, `require_release_for_destruction()`,
  `require_release_for_reuse()`. The refusal, closure and cleanup vocabularies
  are CLOSED and are contract — a destroy gate branches on them. Expiry is not
  release: a crashed run leaves `expired_held`, which does not authorize a wipe.
- `ControllerSshFingerprintV1`. The controller key's OpenSSH SHA-256
  fingerprint, on BOTH the lease and the release. Parsed by DECODING the
  canonical `SHA256:<43 base64>` that `ssh-keygen -lf` emits and requiring 32
  bytes; equality is over those bytes, so comparison answers about the KEY.
  There is exactly one accepted spelling per key.
- `VantageQualification` and `qualify_vantage()`. Typed observations in, a
  verdict out; this package performs no network I/O.
- `Effects`, `Executor`, `DeploymentPlan`, `Step`, `StepKind`, `Strategy`.
- `conformance.*` — the functions a product calls in its own CI.
- `RESOURCE_ATTRIBUTES` and the alert `code` of every entry in `COMMON_ALERTS`.
- `PostgresRecoveryBundle.v1`: `BundleComponent` (the thirteen component CODES
  are contract - a bundle is identified by them), `COMPONENTS` and each
  `ComponentSpec.covers`, `RecoveryBundleManifestV1`, `build_manifest()`,
  `load_manifest()`, `derive_role_closure()`, `RoleClosure`, and the typed
  evidence: `RoleFact`, `MembershipFact`, `OwnershipFact`, `PrivilegeFact`,
  `EffectivePrivilegeFact`, `DefaultPrivilegeFact`, `FunctionSecurityFact`,
  `PolicyFact`, `RlsFact`, `ExtensionFact`, `TablespaceDecision`,
  `CatalogEvidence`. `RoleFact` having NO password field is part of the
  contract, not an omission: adding one would be a breaking change in the
  direction that matters.
- `RESTORE_PROCEDURE` and `RestoreStep` - the ten step CODES and their ORDER
  are contract. `restore_plan()`, `adjudicate_restore()`, `Disposition`,
  `RestoreAttempt`, `verify_recovery()`, `verify_plane_isolation()`,
  `invariant_breaches()`, `classify_invariant_breaches()` and
  `InvariantBreach`. The `RESTORE DEFECT` / `SOURCE DRIFT` prefixes are
  contract - an operator and a dashboard both branch on which side is
  wrong. Manifest `counts` are observations and are gated on by nothing.
- `RecoveryReceipt.v1`: `RecoveryReceiptV1` and `build_recovery_receipt()`.
  `restore_duration_seconds` is mandatory and stays mandatory.
- `refuse_identity_stripping()` and `IDENTITY_STRIPPING_ARGS`.
- `ArtefactClass` - `data_export` vs `recovery_bundle`, and the rule that a
  `data_export` may not reach `RESTORABLE` or `PROVED`.
- `[database]` in `ProductDeploymentSpec.v1`: `DatabaseContract`,
  `DatabaseRole`, `IsolationInvariant`. There is deliberately no `superuser`
  key on a declared role; adding one would be breaking. V1 remains immutable
  and has no catalogue coordinate.
- `[database]` in `ProductDeploymentSpec.v2`: the v1 fields plus mandatory
  `[[database.catalogs]]` whenever `[database]` exists. Coordinates are typed,
  contained and digest-covered. The kernel module schema is valid only at
  MODULE scope (one complete schema), and the kernel product schema only at
  PRODUCT scope (all expected schemas); future schemas need an explicit typed
  registration. Product coordinates also bind catalogue product code/version
  to the descriptor product, with a decision reference required for aliases.
  V1 refuses this key rather than partially reading it.
- Database contract drift: `ObservedDatabaseState`,
  `EffectivePrivilegeAuditUniverse`, `PrivilegeUniverseDerivation`,
  `DatabaseDriftExclusions`, `DatabaseDriftPhase`,
  `DatabaseContractDriftReport`, `DatabaseDriftFinding`,
  `DatabaseContractGap`, `DatabaseContractGapCode`,
  `DatabaseCatalogScope`, `DatabaseCatalogCoordinateV1`,
  `DatabaseCatalogProductIdentityV1`,
  `DatabaseDescriptorCatalogBindingV1`,
  `DatabaseStructureComparisonResultV1`, `DatabaseStructureCoverageV1`,
  `DatabaseStructureFactAttribute`, `DatabaseStructureFactKeyV1`,
  `DatabaseStructureFindingV1`, `DatabaseStructureObservationEvidenceV1`,
  `DatabaseStructureWitnessV1`,
  `accept_database_structure_comparison()` and `compare_database_contract()`.
  The sidecar binds catalogue coordinates to a v1 descriptor digest without
  pretending they are inside v1; `ProductDeploymentSpec.v2` embeds the same
  coordinates. Contract-id allowlists are not an evidence path: acceptance
  requires an injected typed verifier and invokes it over the held catalogue
  and observation bytes. The verifier result's exact contract id, fact scope,
  digests, PostgreSQL major, declaration schema/scope/complete schemas and
  product code/version are rechecked before normalization. The
  structural result and `ObservedDatabaseState` must bind the same observation
  digest and PostgreSQL major. A report exposes `matched_descriptor_digest`
  only for v2 after complete accepted structural evidence covers every schema;
  a module-scoped witness is useful evidence but can never masquerade as a
  whole-product match.
- `DatabaseDescriptorTransition.v1`,
  `DatabaseDescriptorPromotionPending.v1` and
  `DatabaseDescriptorTransitionReceipt.v1`: the `from`/`to`, plan and target
  bindings; the one-transaction versus declared-checkpoint durability shape;
  `promotion_pending`; the idempotent compare-and-swap promotion port; and the
  terminal postcondition and promotion evidence. Authorization binds the
  result descriptor; the starting descriptor is the live/CAS precondition.

## Removed in the 0.4 series

Stated here rather than only in the changelog, because this file is what a
consumer reads to decide whether an upgrade can break them.

**Neither entry below has ever appeared in a tagged version.** Verified against
the tags in this repository: `dotmac-deployment-foundation-v0.2.0a2` is the
newest tag, and `exposure.py` does not exist in it at all — the module was
introduced after that tag. Two further facts belong with that one rather than
being left to inference: `0.2.0a2`'s own changelog heading AT THAT TAG reads
`## 0.2.0a2 — unreleased`, and **no version of this facility has ever been
published**. So there is no published baseline these removals are measured
against, and no installed consumer can be holding them. They are listed as
BREAKING because they were listed under "What is public" and this document's
promises do not depend on whether anyone happened to take them up.

### `ExposureTransaction` and `apply_exposure()` — REMOVED

No shim, no alias, no re-export;
`test_the_retired_surface_is_UNAVAILABLE_not_merely_unmentioned` asserts that
`from dotmac_deployment_foundation.exposure import ExposureTransaction` raises.

They were a second executor. They took the product's deployment lock
themselves, ordered apply / re-observe / verify / compensate, and no
`ExecutionGrant`, frozen execution plan or Control receipt reached any of it.
A compatibility alias was considered and refused: it is a fork with a friendly
name, keeping the removed ordering callable while reading in a diff as a
courtesy.

**Replacement: `Executor`, holding an `ExecutionGrant`.** The exposure act is
`Executor._reconcile_exposure`, performed from the authorized plan.

**The migration boundary** — what a caller must now supply, and why each is not
optional:

| was | is | why it cannot be defaulted |
|---|---|---|
| `ExposureTransaction(spec=..., effects=...)` | `Executor(spec, effects, grant, execution_plan=..., exposure_effects=...)` | the grant is positional and only `authorize()` can issue one, from a `VerifiedAuthorization` |
| `.run()` | `Executor.run(plan, lock=held)` | `lock` is a `DeploymentLockHeld`, obtainable only inside `deployment_lock`'s `with` block |
| implicit — any transaction could apply | `FoundationExecutionPlanV2.exposure_reconciliations` must name the address families | the act is inside the frozen plan digest, so Control authorizes THIS deployment's exposure and not exposure in general |
| `.rolled_back` | the `restore_exposure` record on `DeploymentOutcome` | compensation is evidence on the run, not state on a caller's object |

A caller that constructed the transaction has **no in-package direct
replacement**, and that is the change rather than a gap in it: performing this
act without an authorization is what was removed, not an API. Callers that only
MEASURED are unaffected and better served — `require_preserved_foreign_rules()`,
`foreign_rule_arguments()` and `managed_ports()` are now public functions taking
observations, so a caller that mutates nothing can still make the measurement
that used to require constructing a transaction.

**Known consequence, recorded rather than deferred:** Lane 3's `apply_under_lock`
and `provoked_rollback` items are `blocked`. Control issues no V2 plan carrying
an exposure reconciliation yet, so no grant in existence names the act, and
Foundation must not mint one. `verify_publication` refuses a receipt with a
blocked item, so this is loud at the publication gate rather than silent.

### `dotmac-deploy exposure-apply --execute` — REMOVED

The CLI's subcommands and flags are public contract (see "What is public"), so
a dropped flag is breaking on its own terms. The subcommand survives and still
observes, verifies and reports; it can no longer change a shared firewall
chain. It never accepted `--authorization`, so there was no way to hand it a
Control decision and nothing in it looked for one — the flag was permission
supplied by whoever typed it.

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

### 0.3.0a4: `observability_promotion`, and why it is ADDITIVE

A new module, `dotmac_deployment_foundation.observability_promotion`, implementing
the host half of Observability ADR-0010's promotion facility: immutable
release-directory staging, a previous-release-pointer reader, an exact-byte
transport, `rename(2)` activation, per-evaluator reload with a read-back that
proves the process took the bytes, a complete live read-back, and a rollback that
returns one.

**Nothing existing changed.** No signature, no refusal and no rendered byte of any
prior surface moved. `providers/compose_host.py`'s `Runner` in particular is
untouched: a file transport needs to feed a command bytes on stdin and that
`Runner` cannot, so this module declares its own `HostTransport` Protocol rather
than widening the shipped one. Widening it would have been a MINOR-at-best change
to a seam every existing provider implements.

**The declared version moved for a reason unrelated to compatibility.**
`0.3.0a3` had already been built, so adding source under it would have made one
version name two contracts. The bump is the version rule above being applied, not
a signal that anything broke.

**Zero runtime dependencies still.** The module is stdlib-only — `urllib` rather
than `requests`/`httpx`, `subprocess` with an argv list — so the facility remains
something a build runner can adopt without adopting a runtime.

### 0.3.0a3: the external-recovery contract, measured against the version rule

Stated rather than assumed, because the rule above is strict and this change
touches every clause of it.

**New refusals in `spec.py`, and why this is not the MAJOR case.** Declaring an
`external_executor` without a `lineage` is refused, and so is a lineage that is
host-shaped or that repeats the executor's identifier. Both fire only on keys
that did not exist in any previous version, so **no descriptor that parsed
yesterday fails today** — the premise of the major-bump rule ("a consumer's
build breaks") is absent. A refusal reachable only from new syntax is a
constraint on new syntax, not a retraction of old.

**The verification vocabulary WIDENED.** `roles`, `ownership`, `memberships`
and `effective_privileges` are now accepted where they were refused at parse.
That direction cannot break a consumer, and an unknown verification is still
refused, so the vocabulary is wider and not open.

**One breaking signature change**, named plainly: `backup.assess()`'s
`expected_interval_seconds` parameter is now `expected_backup_interval_seconds`,
matching the descriptor field that supplies it. It was a second name for one
control, and one the descriptor could not state at all — so every caller
silently took the daily default whatever the product's real cadence was. There
were **no callers**: `assess()` had none in this package or in any consuming
product, which is the same fact that made `restore_proof_max_age_days` inert.
A rename with no callers is recorded here rather than deferred to a major
release nobody is waiting for.

**`SECONDS_PER_DAY` moved** from `backup` to `spec`, which now declares a
cadence in seconds and would otherwise hold a second definition of it. `backup`
re-exports it, so no import moves.

**Rendered bytes changed.** The descriptor gained fields, so
`io.dotmac.deployment.configuration.digest` moves and `docker-compose.yml` with
it — the MINOR case. `0.2.0a2` is the newest published version, so the published
line moves `0.2` → `0.3`, which satisfies it. No consumer has committed
`0.3.0a2`'s bytes: that candidate was never published and is recorded
invalidated.

## Consuming this package

Exact-pin it. A conformance gate resolving to "whatever is newest" cannot
distinguish a product that drifted from a foundation that changed, and the
reusable workflow refuses a range for that reason.
