# ADR-0073: Control owns authenticated host admission and consumption

- Status: Accepted
- Date: 2026-09-21
- Decider: Michael
- Related: ADR-0070; `docs/DISPATCH_CONSUMPTION.md`; `docs/HOST_ATTESTER_ENROLMENT.md`

## Context

ADR-0070 separates Control's durable deployment decisions from Foundation's stateless execution verification. Control already owns a durable attestation registry and private dispatch-consumption seam, but neither is an authenticated host-admission protocol. Existing rows have no authenticated presenter-to-host association or complete root descriptor, and their canonical base64url public material is not Foundation's padded standard-base64 wire form. The cited documents record these as open boundaries, not implemented behaviour.

Michael accepted this contract and authorized its source implementation on
2026-09-21. Acceptance does not itself authorize a deployment, release, launch,
or change to existing production admission authority; those retain their own
gates and evidence requirements.

## Decision

### Ownership and composition

- Control owns authenticated presenter -> target -> canonical Fleet `host_id` derivation, current root/standing resolution, and atomic dispatch consumption.
- Fleet owns the canonical `host_id` and its grammar.
- Foundation owns stateless artifact and attestation verification.
- The Platform control plane (CP) is trusted composition and transport: it owns startup-fixed authenticator, verifier, clock, transaction and commit-then-launch ordering. It cannot decide trust.

Foundation and Control do not import each other. A thin CP adapter maps the two contracts. No request-shaped verifier, authenticator or clock input, and no mutable runtime registry, exists: CP installs purpose-specific implementations at startup.

### Authenticated presentation

An active, distinct, purpose-bound `TargetCredential` with purpose `dotmac.control.host-admission-presentation.v1` signs a canonical, versioned presentation. The presentation is `{statement, signature}` and its signature is canonical unpadded base64url. Its statement fields are schema, version, purpose, fixed audience `dotmac.deployment-control`, `presentation_id`, `key_id`, signed dispatch-envelope `dispatch_id`, candidate-attestation envelope digest, installed-attestation envelope digest, `issued_at`, and `expires_at`; its maximum lifetime is five minutes. Statement bytes use Foundation's deterministic JSON contract: `json.dumps(..., sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")`, with canonical UTC RFC3339 instants.

The request supplies none of host, target, root, custody role, standing, verifier, or trusted boolean. Control initially verifies the signature before treating `key_id` as identity, using algorithm, public key and purpose only from the looked-up credential. Its startup-fixed clock requires `issued_at <= now < expires_at` and `0 < expires_at - issued_at <= 5 minutes`, with no implicit skew. It then locks target -> credential, refreshes and rechecks current credential standing/eligibility/purpose at `now`, and either repeats verification or checks locked key equality to prevent a pre-lock authentication race. Control derives `target_id` from that credential and reads `target_ref` only from the locked target row.

### Host association and target policy

Control's append-only target-to-Fleet-host associations and append-only target-admission policy revisions are truth; their one-current projections are derived accelerators and serialization pointers, reconciled from history. Each replacement is linked by an append-only closure/successor record, so the current association or policy is derivable without trusting its projection. Admission never repairs them: drift or ambiguity refuses. One target has at most one current host; many targets may share a host. Bind, rotate and revoke are explicit transitions and association/policy mutations lock the target. The stored host is validated against Fleet grammar. Credential -> target -> current association is the sole host derivation: do not infer it from `target_ref`, `subject_ref`, hostname, machine-id, or presentation text. Missing, ambiguous, mismatched or drifted associations refuse.

Control's current target-admission policy binds that association, a candidate-root subject, candidate and installed audiences, and the expected Foundation package. The installed-root subject is the current Fleet `host_id`. The request cannot select policy terms.

### Root descriptors and canonical key conversion

Existing `AttestationEnrolment` remains the sole owner of `custody_domain`, subject, `public_key_b64`, `public_key_fingerprint`, algorithm, `enrolled_at` and enrolment authority. Each NEW enrolment/successor inserts, in the same transaction, separately immutable one-to-one FK metadata owning only issuer, attestation `key_id`, exact evidence purpose and bounded `not_after`. Root version is derived solely from the enrolment UUID in canonical string form; `not_before` solely from `enrolled_at`. The admission descriptor composes these owners and refuses any missing or inconsistent field. Existing immutable enrolments are never patched or given metadata later: legacy rows without it permanently refuse admission and must rotate or re-enrol to a new complete successor. The exact evidence-purpose wire values are Foundation's already-published constants: candidate custody uses `dotmac.foundation.candidate-artifact.v2` (`CANDIDATE_ATTESTATION_PURPOSE`) and installed-host custody uses `dotmac.foundation.installed-host.v2` (`INSTALLED_OBSERVATION_PURPOSE`). Control stores and compares those exact strings without importing Foundation; the fixed cross-repository contract test below detects drift.

Control stores canonical unpadded base64url. Its admission-context adapter decodes once, recomputes the fingerprint, and emits strict padded, standard-alphabet base64 to Foundation. Malformed/non-canonical storage or a fingerprint mismatch refuses. Foundation never performs reverse conversion; presented evidence never supplies root material.

The immutable, versioned prepare context contains target, the locked target-policy revision and its `candidate_audience`, `installed_audience` and `expected_foundation_package`, and the separate structured identity fields `host_id`, `public_key_fingerprint` (the incarnation), and `trust_root_version`; it rejects a composite URN and any duplicate incarnation counter. It also contains complete candidate and host root descriptors: issuer, key id, purpose, root version, validity bounds, algorithm, custody domain, exact public material, fingerprint and Control-resolved standing. Only a current admissible root is emitted with `revoked=False`; Foundation and CP never invent that flag. This matches Foundation's `expected_host_identity`. Prepare returns facts/context only, never a launch grant or staged consumption result.

### Evidence coordinate and atomic consumption

Foundation adds one versioned public `attestation_envelope_digest` helper over the parsed exact full mapping, including signature, using its deterministic JSON bytes. CP calls that helper; Control validates and binds its canonical `sha256:<lowercase hex>` result without parsing Foundation envelopes. A cross-repository fixed-vector conformance test prevents the Control coordinate and Foundation helper drifting. The installed attestation's `observation_id` must equal the exact signed stored dispatch-envelope `dispatch_id`; Control may locate by internal attempt UUID but compares against that envelope value and uses the same value as the Kernel key. Foundation's verifier accepts expected observation/challenge and expected package parameters, checks both, and rejects when the authenticated candidate subject `package` differs from the locked `expected_foundation_package`; candidate/installed agreement alone cannot admit a wrong package. A host observation is fresh per attempt; candidate evidence may be reused for the same immutable artifact. The presentation signs the dispatch ID and both digests.

No second replay ledger is added. Control has a persistent subject-lock row keyed by `(custody_domain, subject)` that is never deleted. Its migration backfills distinct subject pairs before enabling a writer; first enrolment creates then locks it. Enrolment, rotation and revocation acquire this lock before mutation. Subject locks serialize only; they hold no trust state. Preparation authenticates with the looked-up immutable key, then locks target -> credential -> current association/policy -> candidate and host subject rows in lexicographic `(custody_domain, subject)` order, refreshes the credential at `now`, and rereads append-only truth and the current projection at PostgreSQL READ COMMITTED. Implementation measurement on 2026-09-21 found the pre-ADR activation/revocation writers locked only the credential; this implementation must reconcile every credential mutation to target -> credential before admission is enabled, rather than documenting that order as already true. `AttestationCurrentRoot` is not the serialization arbiter: revoke deletes it, so it cannot serialize absence or recovery.

Prepare and finalize use the same caller-owned database session and transaction. The private non-serializable prepare context is distinct from the private staged result returned only by finalize. Foundation verifies using its immutable context while that transaction and its locks remain open; a Foundation refusal requires the CP transaction owner to roll back. Control services add/flush only and never roll back. Finalization rechecks the prepared coordinate, root versions, exact policy revision and its `candidate_audience`, `installed_audience` and `expected_foundation_package`, requires the exact two digests, then calls `_stage_dispatch_consumption`.

That seam keeps Kernel scope `deployment.consume_dispatch_challenge.v1`. Its idempotency fingerprint is SHA-256 over canonical JSON mapping `{schema: "dotmac.control.host-admission-consumption", version: 1, dispatch_envelope_digest, candidate_attestation_envelope_digest, installed_attestation_envelope_digest}`; Kernel receives the resulting bare lowercase hex. The same dispatch with changed evidence is an integrity conflict; the same coordinate is already consumed. A committed mutation first makes consumption reread and refuse; a committed consumption first remains spent. Rollback consumes nothing. CP returns a response or launches only after commit; a committed but lost response remains spent.

### Refusals

The implementation has distinct typed families for authentication failure; absent, ambiguous, mismatched or drifted host binding; target mismatch; purpose/custody mismatch; missing root metadata; registry ABSENT, DISAGREEMENT and DRIFT; revoked or superseded credential/root; expired or not-yet-valid evidence; changed evidence coordinate; already-consumed authority; and integrity conflict. It does not collapse a safety refusal into absence or replay.

## Implementation sequence and evidence

ADR acceptance is a prerequisite, not Gate 1. This ADR does not close Gate 1 and does not resolve the separate RecoveryExecutor roles-fail/objects-succeed defect. After the Control, Foundation and CP code/adapter fixes, ADR-0070's sequence is source freeze -> candidate version allocation -> build once -> sign exact bytes -> commit receipt -> protected exact-wheel rehearsal -> publish. Tests must cover real signatures with two independently generated keys; future, expiry and exact-boundary presentation liveness; one-coordinate negatives; caller substitutions without consumption, including one-field substitutions for candidate audience, installed audience and expected package; exact base64 bytes; fixed cross-repository envelope-digest vectors; deterministic two-connection both-order credential revoke/retire, host association rotate/revoke, policy change, and both-root revoke/rotation; concurrent replay admitting exactly one; rollback admitting none; and sensitivity-tested sole-caller guards.

## Rejected alternatives

- Infer a host from existing strings, hostname or machine identity.
- Treat installed attestation alone as target authentication.
- Cache a proof or provider result instead of serializing standing under lock.
- Create a separate replay ledger.
- Accept caller-supplied roots, verifier, standing or trusted boolean.
- Keep the v2 observation unbound to its dispatch challenge.
- Give old incomplete rows blanket defaults.
