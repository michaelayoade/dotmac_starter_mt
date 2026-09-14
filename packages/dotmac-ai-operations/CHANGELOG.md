# Changelog — dotmac-ai-operations

## 0.1.0a2 — unreleased

- `AIActionProposal` now requires its own `capability`,
  `execution_policy_version_ref` and `execution_policy_digest` to match
  the `invocation` it carries, checked at PROPOSAL CONSTRUCTION — this is
  the primary gate now, not only the check `AIAdvisoryResult` already
  applied when a proposal was wrapped in a result. Before this fix, a
  proposal (or an `AIActionDecision` carrying one, since decision only
  ever checked `proposal_ref` self-consistency) could be built directly
  with a capability or policy pin that contradicted its own embedded
  invocation, handing a consumer two contradictory identities with
  nothing refusing it. `AIAdvisoryResult`'s equivalent checks remain, now
  as defence in depth rather than the only gate.
- `AIInsight`'s legacy action-evidence column is no longer published
  under an ordinary ORM attribute name: preserving the database column
  for N-1 compatibility does not require the CURRENT model to keep
  exposing it as a normal attribute a reader could mistake for a governed
  read path. The Python attribute is renamed to the private
  `_legacy_action_evidence_ref` (the underlying column name, and its
  presence for N-1 rollback compatibility, are both unchanged — an older
  deployed application version has its own separate model code and never
  sees this rename). `authoritative_acknowledgement` is the one function
  every read should go through.
- `record_attempt`'s `observed_at` canonicalization now runs INSIDE the
  same `try` as `_aware`/`AIExecutionObservation` construction (it
  previously ran after that `try` ended), and that `try` now also catches
  `OverflowError` — an aware `observed_at` far enough from UTC midnight
  (e.g. `datetime.max` with a negative fixed offset) previously passed
  the awareness check, then overflowed converting to UTC and raised a raw
  `OverflowError` instead of `AIOperationRefused`.
- The migration test's docstring corrected: it claimed a "later raw
  SELECT", but the reload goes through the CURRENT `AIInsight` ORM model
  (which maps the same physical, unchanged `action_evidence_ref` column)
  — not a raw SQL select. The test now also writes a THIRD, MIXED row
  (both a raw legacy write and an `acknowledge_insight` typed write
  landing on the SAME row) and asserts `authoritative_acknowledgement`
  resolves it correctly; the previous version only proved two SEPARATE
  rows could coexist, which says nothing about precedence on one row —
  the case precedence exists for.
- The blank-output-pair service-boundary test now expires the session and
  reloads before asserting, so it can only pass if the row was genuinely
  added and flushed — the previous version only inspected the in-session
  object `record_attempt` returned, which would have passed even if the
  row were never persisted at all.
- `ao_0002_insight_evidence_binding` is now declared in
  `tests/architecture/test_released_migrations.py`'s `UNRELEASED` map —
  a migration existing on disk but unclassified there fails that
  two-directional guard on hosted CI unconditionally.
- New precedence rule for `AIInsight` action evidence, since the
  expand-only migration above deliberately leaves an old, rolled-back
  application version free to keep writing the legacy
  `action_evidence_ref` column: a race between an old writer and a new
  one can leave a single row carrying BOTH a legacy locator and a typed
  binding. `models.py`'s comment no longer claims an all-four-or-none
  database-level invariant the schema cannot actually hold; new function
  `service.authoritative_acknowledgement(insight)` defines and applies
  the read rule instead — the typed binding is authoritative whenever
  fully populated, the legacy locator is descriptive residue only, never
  the reverse and never "whichever is non-null".
- The mixed-legacy/typed-row test now runs the REAL
  `ao_0002_insight_evidence_binding.upgrade()` against a table built to
  `ao_0001`'s column NAMES AND TYPES only (not current ORM metadata,
  which already declares the expanded columns and would have proven
  nothing about the migration) — narrower than the real published table:
  it omits the schema qualifier, foreign keys, unique constraints and
  row-level security the real `ao_0001` migration also creates, so it
  proves the additive-column and mixed-row-precedence behaviour, not
  full schema fidelity to `ao_0001`. It writes the legacy row via a raw
  INSERT naming
  `action_evidence_ref` directly, and reloads from a genuinely fresh
  session with no shared identity map. The prior version of this test
  built the expanded schema directly and reloaded via `db.get()` inside
  the same session's identity map, so it would have stayed green through
  a rename or drop of the legacy column and proved nothing about
  persistence.
- `record_attempt`'s idempotency fingerprint now canonicalizes
  `observed_at` to UTC before hashing — two values naming the same
  instant under different offsets (which a `timestamptz` column stores
  identically) previously fingerprinted differently and would have
  wrongly conflicted under one attempt key.
- `record_attempt` now wraps `_aware(observed_at, ...)` inside the same
  `try` as `AIExecutionObservation`'s construction, so a naive
  `observed_at` is also reported as `AIOperationRefused` — previously it
  raised a raw `ValueError` outside that wrapping, narrower than the
  "every refusal" exception-contract claim already documented.
- `acknowledge_insight`'s `action_evidence_ref: str | None` (a naked
  locator, despite `EXTRACTION.toml` already calling this field
  "acknowledgement/action evidence") is replaced by
  `action_evidence: AIEvidenceBinding | None`. This is deliberately an
  EXPAND-ONLY schema change, by ruling: `AIInsight.action_evidence_ref`
  is **not renamed, not dropped, and not backfilled** — it is preserved
  exactly as published in `ao_0001_ai_operations` (0.1.0a1), so an
  application version rolled back to before this change still reads and
  writes it correctly against the expanded schema. It is reclassified as
  DESCRIPTIVE-ONLY: it stops receiving authoritative writes from
  `acknowledge_insight`, and carries no evidentiary weight going forward.
  Migration `ao_0002_insight_evidence_binding` ADDS four new nullable
  columns instead (`action_evidence_locator_namespace`,
  `action_evidence_locator_ref`, `action_evidence_content_digest`,
  `action_evidence_media_type` — `AIEvidenceBinding`'s fields, WRITTEN
  together as a full binding or not at all; see `COMPATIBILITY.md`'s
  canonical read-side statement for what reading them actually
  guarantees, not restated here); existing rows get all four as
  `NULL`, which is not backfilled and never will be — synthesising a
  digest for a locator captured without one would fabricate an integrity
  claim nobody made. Retiring `action_evidence_ref` (once the legacy
  inventory of rows still using it reaches zero) is deferred to a later
  migration. A bare string offered as `action_evidence` is refused, not
  silently wrapped.
- `record_attempt`'s idempotency fingerprint now includes `observed_at` —
  it previously covered every validated observation field except the one
  that is independently persisted, so reusing an attempt key with
  identical fields but a different observation time returned the earlier
  row instead of reporting a content mismatch.
- `record_attempt` now reports a malformed `AIExecutionObservation`
  construction (bad outcome, unpaired output evidence, malformed digest)
  as `AIOperationRefused`, not the underlying `ValueError`/
  `InvalidContractDigestError` — preserving the exception contract this
  function's callers already have with every other refusal in this
  module, which the move to internal construction silently changed.
  `COMPATIBILITY.md` documents this explicitly.
- `AIEvidenceBinding`'s docstring corrected: it had claimed the namespace
  is "never ambiguous" and the media type says what the content "actually
  IS" — false. The type checks exact string types, non-blank values and a
  digest SHAPE; it does not resolve the locator, hash the target, or
  validate the media type against real content. The docstring now says
  precisely what it proves: an immutable, structurally-valid CLAIM by the
  caller, whose resolution and verification remain the consumer's
  responsibility.
- New tests drive malformed and blank input THROUGH `record_attempt`
  (not only directly through `AIExecutionObservation`), and through
  `acknowledge_insight`, so the service-boundary guarantees above have
  tests that would fail if `record_attempt`/`acknowledge_insight` ever
  reverted to reading raw parameters instead of the constructed,
  validated/normalized types.
- `record_attempt` (`service.py`) now takes raw attempt inputs
  (`attempt_key`, `outcome`, `output_ref`, `output_digest`,
  `provider_observation`, `model_observation`, `request_observation`,
  `error_code`) directly instead of a single `command: AttemptInput`, and
  constructs the canonical `AIExecutionObservation` itself. A caller
  passing `command=AttemptInput(...)` previously could bypass every
  invariant `AIExecutionObservation` enforces (paired output evidence,
  failure-evidence requirement, digest shape, blank normalization) simply
  by not routing through that validated type; there is no longer a
  parameter a pre-built object could be passed through. Existing callers
  updated (`tests/unit/test_ai_operations.py`).
- Evidence that affects a decision or an owner command is no longer a
  bare `str`, per Michael's ruling: *"Naked evidence-reference strings are
  inadmissible for integrity claims. Every evidence input that affects a
  decision or owner command needs a typed binding containing at least a
  locator namespace, immutable content digest, and media type/shape,
  verified before use. A locator alone may remain descriptive metadata but
  cannot authorize or prove review."* New type `AIEvidenceBinding`
  (`locator_namespace`, `locator_ref`, `content_digest`, `media_type`),
  validated together at construction like every other type here.
  `AIActionDecision.resulting_owner_command_evidence_ref: str | None` is
  replaced by `resulting_owner_command_evidence: AIEvidenceBinding | None`,
  and `AIActionProposal.supporting_evidence_refs` changes from
  `tuple[str, ...]` to `tuple[AIEvidenceBinding, ...]`. A bare string
  offered where a binding is required is REFUSED, never wrapped, defaulted
  or promoted into one — the same "refuse, don't coerce" rule already
  applied to every scalar in this module. See `COMPATIBILITY.md` for the
  call-site migration.
- `AIActionDecision` is now explicit, in the module docstring, in its own
  class docstring, and here: it is **NOT AUTHORIZATION and NOT PROOF OF
  REVIEW**. It records that a caller asserted a human reached an outcome;
  there is no signer, no authenticated reviewer identity, and none is
  invented. This includes `outcome == "edited"`, which records an
  assertion that an edit happened, not proof of what the resulting owner
  command actually contained. The module docstring specifies what an
  authoritative reviewer receipt would need to bind (reviewed proposal,
  policy version/digest, command ref/digest, outcome, authenticated
  reviewer identity, review time, evidence-set digest) if a consuming
  product ever composes one — no such service exists today, and building
  one is that product's decision, not this module's.
- Add the stateless advisory-protocol contracts: `AIAdvisoryInvocation`,
  `AIAdvisoryResult`, `AIActionProposal`, `AIActionDecision`,
  `AIExecutionObservation` and `AICapabilityExposureRef`. One advisory
  invocation and its result cross the boundary; capability admission is an
  opaque exposure reference (schema/permission/approval/idempotency stay
  domain-owned). This package carries no workflow node, routing decision or
  session authority — that stays with the consuming product's own
  orchestration owner (for example, Sub's `ai.intake`). See ADR-0040's
  2026-09-13 amendment for the corrected ownership boundary.
- Harden digest binding: `capability_id` now requires a non-zero,
  non-leading-zero version segment (`v1`, `v2`, ... — rejects `v0`/`v01`);
  `contract_digest`, the new `context_projection_digest` and the new
  `proposed_command_digest` fields now validate the exact 64-lowercase-hex
  shape produced by `dotmac_integration.spi.canonical_digest()`, replicated
  locally rather than imported. `policy_version_ref` is renamed
  `execution_policy_version_ref` on both `AIAdvisoryInvocation` and
  `AIActionProposal` to distinguish this package's own execution-admission
  policy from the consuming product's orchestration policy.
  `AIAdvisoryResult.invocation_ref` is replaced by `invocation` (the full
  `AIAdvisoryInvocation`), and a present `action_proposal` must structurally
  agree with `invocation` on both `capability` and
  `execution_policy_version_ref`.
- Close a further adversarial-review pass on the content-identity-pinning
  decision above: `AIAdvisoryInvocation` and `AIActionProposal` add
  `execution_policy_digest` (validated with the same 64-lowercase-hex rule),
  and `AIAdvisoryResult`'s cross-check now also requires the proposal's
  digest to match the invocation's — a version ref that resolves differently
  at two moments no longer passes. `AIActionDecision` initially grew loose
  `capability`/`proposed_command_digest`/`execution_policy_version_ref`/
  `execution_policy_digest` fields, but a follow-up review correctly found
  that copying pins onto the decision does not bind it to anything — a
  caller could still supply consistent-looking copies for a proposal that
  was never actually reviewed (different evidence or confidence, identical
  pins). `AIActionDecision` instead now carries `proposal: AIActionProposal`
  (the full reviewed object) alongside `proposal_ref` as the lookup handle,
  with `proposal_ref` validated to match `proposal.proposal_ref`. **This is
  still insufficient on its own**: it proves internal self-consistency of
  whatever `AIActionProposal` the caller supplies, not that the supplied
  proposal is the one that actually appeared in a reviewed
  `AIAdvisoryResult` — a fresh, never-reviewed, internally-consistent
  proposal still passes. Closing that gap needs either binding the decision
  to an `AIAdvisoryResult` or giving the proposal a canonical identity the
  result records and the decision must match; that mechanism is undecided
  and not yet implemented.
  `AIExecutionObservation.output_digest`
  is now validated with the same digest rule (a loose non-blank string like
  `"digest-1"` is rejected), the `output_ref`/`output_digest` pairing
  requirement now applies whenever either field is present regardless of
  `outcome` (a `failed` observation can no longer carry a partial pair), and
  a `failed` observation must carry at least one non-blank failure-evidence
  field (`provider_observation`, `model_observation`, `request_observation`,
  `error_code`). `AICapabilityExposureRef` type checks on
  `AIAdvisoryInvocation` and `AIActionProposal`, and the `AIActionProposal`
  type check on `AIActionDecision.proposal`, now require the exact type
  rather than `isinstance`, since a subclass adding fields would inherit
  the base `__eq__` and defeat the structural cross-checks above by
  comparing equal despite differing meaning.
- Close the one remaining `isinstance`-based extension channel:
  `AIAdvisoryResult.invocation` and `AIAdvisoryResult.action_proposal` now
  also require the exact type (`AIAdvisoryInvocation` /
  `AIActionProposal`), not a subclass — a subclass adding a workflow field
  such as `next_step` or `wait_deadline` would otherwise pass through this
  stateless protocol unexamined, which is the one thing it must not permit.
- Bind a proposal to the invocation it was generated for. A first pass added
  `invocation_ref` and `context_projection_digest` to `AIActionProposal` and
  cross-checked both — but a further review correctly found this covered
  only a subset: `AIAdvisoryInvocation` also carries `interaction_ref`,
  `context_projection_ref`, `context_projection_as_of` and
  `context_sensitivity`, none of which were compared, so two invocations
  sharing an `invocation_ref` and projection digest but differing in any of
  those would both wrongly accept the same proposal. `AIActionProposal` now
  carries the FULL `invocation: AIAdvisoryInvocation` object instead of a
  ref/digest subset, and `AIAdvisoryResult`'s cross-check compares it via
  ordinary dataclass equality — every field, not a hand-picked list. This
  was the most serious defect found in this package to date.
- Every public type's own `__post_init__` now rejects being subclassed at
  all (`AICapabilityExposureRef`, `AIAdvisoryInvocation`, `AIActionProposal`,
  `AIAdvisoryResult`, `AIExecutionObservation`, `AIActionDecision`) — the
  earlier exact-type checks only examined NESTED values (e.g. `capability`
  inside `AIAdvisoryInvocation`), so a subclass adding a workflow `next_step`
  could still construct successfully and cross the boundary as the
  top-level value itself, with only embedding it inside another type
  refused.
- Validate every scalar field (`str`, `float`, `datetime`) against its exact
  built-in type at construction, closing a gap the exact-CLASS checks above
  left open: an ordinarily-constructed `str`/`float`/`datetime` subclass
  instance could still carry its own extension attribute in any digest,
  observation, actor or evidence field, or override `__eq__`/`__ne__`/
  `strip`/`__hash__` so that a value which should fail one of this module's
  cross-object identity checks instead reports itself as equal or a member.
  **This landed broken and was corrected in the same unreleased version**:
  the first attempt "coerced" a non-exact value via `str.__new__(str, x)`/
  `float.__new__(float, x)`, on the claim that this reads the underlying
  data directly without calling caller code. That claim was false —
  `str.__new__(str, x)` for a non-`str` argument dispatches `x.__str__()`,
  and `float.__new__(float, x)` dispatches `x.__float__()`, which is
  exactly the caller-controlled path the check existed to avoid: a subclass
  whose `__str__` fabricates a valid digest, or whose `__float__` always
  returns `0.5`, would have passed validation with a value the caller never
  actually supplied (and a plain `bool`/`int` would have silently become a
  "valid" `1.0`/`0.0` confidence). The corrected behavior REFUSES rather
  than coerces: only a value that is already `type(x) is str` (or `float`)
  is accepted; anything else raises `ValueError` naming the field. There is
  no such thing as a caller-code-free way to normalize an arbitrary
  object's `str`/`float` value, so refusal is what "closing this gap"
  actually means. `AIActionProposal.supporting_evidence_refs` items get the
  same exact-type-or-refuse treatment, checked BEFORE `.strip()` is called
  (a subclass's overridden `strip()` must never run). Every accepted aware
  `datetime` is additionally canonicalized to a UTC instant carrying the
  plain `datetime.UTC` singleton, never the caller's original `tzinfo`
  object, which could otherwise hold hidden state or a mutable offset. None
  of this is a tamper-proof boundary — see the module docstring's THREAT
  MODEL section, added in this same pass, for the honest scope: validation
  targets accidental misuse, not a determined in-process caller who can
  bypass `__post_init__` entirely via `pickle` or `object.__new__` +
  `object.__setattr__`. Earlier CHANGELOG entries describing these checks
  as "closing a channel" a hostile subclass could exploit overclaimed what
  was achieved; this entry corrects that framing.
- Extend the full-invocation mismatch test coverage from six of
  `AIAdvisoryInvocation`'s nine fields to all nine (adding `capability`,
  `execution_policy_version_ref` and `execution_policy_digest` on the
  EMBEDDED invocation, distinct from `AIActionProposal`'s own separate
  scalar fields of the same names), and add exact-permitted-field-set
  guards (previously only on `AIActionProposal` and `AIAdvisoryResult`) to
  `AIAdvisoryInvocation`, `AIActionDecision`, `AIExecutionObservation` and
  `AICapabilityExposureRef`.
- Normalize a whitespace-only `output_ref`/`output_digest` pair on
  `AIExecutionObservation` to `None` rather than retaining the blank
  strings — previously such a pair was correctly treated as ABSENT for the
  pairing/succeeded checks, but the original blank values were still
  stored, contradicting the "entirely present or entirely absent"
  invariant those checks exist to enforce. **Corrected in the same
  unreleased version**: the first pass stripped `output_digest` before
  validating it, so a whitespace-PADDED digest (`" " + 64 hex + " "`)
  passed — the owning digest oracle applies `fullmatch` to exactly what
  was supplied, not a trimmed copy, so padding must fail. `output_digest`
  is now validated against its original, unstripped value; only
  `output_ref` (which has no regex) is trimmed for storage.
- Fix a `str | None` narrowing gap in `AIExecutionObservation.__post_init__`
  that failed this package's strict `make type-check` gate: the presence
  check is now performed directly on locals bound from `self.output_ref`/
  `self.output_digest`, which mypy can actually narrow, rather than through
  an intermediate boolean.
- Correct two remaining copies of a false claim: `AIActionDecision`'s
  internal comment and its `proposal_ref` mismatch error message no longer
  say carrying `proposal` makes a decision "unable to name a proposal it
  did not actually review" or that it names "the exact proposal it
  reviewed" — both now say plainly that this is only a self-consistency
  check between the decision's own two fields, matching the CHANGELOG
  correction made earlier for the same false claim.
- **Superseded by the `record_attempt` entry above, kept only as history**:
  this version's CHANGELOG briefly stated that `AIExecutionObservation`'s
  invariants were enforced at construction time only, and that
  `dotmac_ai_operations.service.record_attempt` still accepted the
  unvalidated superclass `AttemptInput`. That was true when written and is
  false now, in this same unreleased version — `record_attempt` was
  subsequently changed (see above) to construct the canonical observation
  itself. Leaving contradictory claims standing in the same changelog is
  itself a defect; this entry says so explicitly rather than deleting the
  history silently.

- `record_attempt`'s `_aware`/canonicalization boundary now catches
  `Exception` broadly rather than `(ValueError, OverflowError)` — found
  incomplete a third time when a caller-supplied `tzinfo` whose
  `utcoffset()` raises an arbitrary exception (not just those two types)
  leaked past this function raw. Also: `observed_at` is now canonicalized
  to UTC EXACTLY ONCE, and that single value — not the original,
  still-caller-controlled `observed_at` — is used for the idempotency
  digest AND both persisted timestamps (`AIExecutionAttempt.observed_at`,
  `operation.completed_at`). Previously only the digest used the
  canonical value, so a stateful `tzinfo` could disagree with itself (or
  raise, outside this function's own exception handling) when the driver
  serialized the original value later, at `db.flush()`.
- New `AIAcknowledgementAttribution` (`advisory.py`): actor namespace,
  actor type, opaque actor ref, aware acknowledgement timestamp — gives
  acknowledgement ATTRIBUTION the identical typed-shadowing treatment
  `AIEvidenceBinding` already gave evidence (Michael's ruling, round
  13/14: a row pairing authoritative typed evidence with an actor/time an
  N-1 writer can silently overwrite makes a false HISTORICAL claim, not
  merely a stale display one).
- New migration `ao_0003_ack_attribution`: four new,
  additive, nullable columns (`attribution_actor_namespace`,
  `attribution_actor_type`, `attribution_actor_ref`,
  `attribution_acknowledged_at`). `acknowledged_by_ref`/`acknowledged_at`
  are NOT renamed, dropped or backfilled — same N-1 round-trip reasoning
  as `action_evidence_ref`, except here (unlike evidence) they remain the
  SAME columns an older writer also writes; there is no separate legacy
  pair being shadowed, only the new typed pair an older writer has no
  reference to. Deliberately NO CHECK CONSTRAINT enforcing
  all-four-together: the writer-side guarantee is a claim about this
  repository's code, not proof from a real N-1 rehearsal against the
  actual published artifact, which was investigated and found
  unreachable from this repository's ordinary CI today. Landing the
  constraint anyway with a comment admitting its premise is unverified
  would still make the database enforce an unproven assumption — the
  constraint is deferred to a focused successor migration once that
  evidence exists, not landed early with a caveat.
- `acknowledge_insight` gains `actor_attribution: AIAcknowledgementAttribution
  | None`; a bare actor string is refused, never silently wrapped. It
  canonicalizes `acknowledged_at` to UTC exactly once and writes that
  single value to both the legacy `acknowledged_at` column and the typed
  `attribution_acknowledged_at` column — a caller supplying
  `actor_attribution.acknowledged_at` naming a different instant than the
  top-level `acknowledged_at` is refused outright, never silently
  resolved in favour of either value.
- `authoritative_action_evidence` is REPLACED by
  `authoritative_acknowledgement`, which returns evidence AND attribution
  TOGETHER (`AuthoritativeAcknowledgement`) — the ONE SANCTIONED way to
  read them without pairing a fresh snapshot of one against a stale
  snapshot of the other. **Round 15 correction**: this was first claimed
  as making them "impossible to read apart," which was false while the
  eight underlying columns were ordinary public `AIInsight` attributes —
  the third time on this lane an impossibility claim turned out to be
  only a convention. Fixed by narrowing the gap, not only the claim: all
  eight columns (`action_evidence_*`/`attribution_*`) are now
  private-by-convention Python attributes (`_action_evidence_*`/
  `_attribution_*`), the identical treatment the legacy locator already
  had — physical column names unchanged, only the ORM attribute names
  gain a leading underscore. Reading a column directly, bypassing this
  accessor, is still possible (via the private name, `__table__.c[...]`,
  mapper introspection, or raw SQL) and unsupported, not prevented.
  Attribution follows the identical partial-read
  refusal evidence already had: not fully populated means `None`, never
  combined with the legacy actor/time columns to manufacture a
  complete-looking answer. No backfill: a row with no trustworthy typed
  actor captured (every row acknowledged before this migration, or by an
  N-1 writer since) returns `None` attribution permanently.
- `acknowledge_insight`'s conditional-update failure contract
  (`rowcount == 0` → `AIOperationRefused`) is documented as assuming READ
  COMMITTED, Postgres's default — under REPEATABLE READ or SERIALIZABLE
  the losing caller can instead get a database serialization failure
  (still fails closed; at-most-one-commit still holds, but not as this
  package's own exception type). Mirrors `dotmac_kernel/
  external_identity.py`'s identical documented assumption for the same
  shape of conditional update.
- `.github/release-modules.json`'s `dotmac-ai-operations` wheel-content
  policy now lists `ao_0002_insight_evidence_binding.py` and
  `ao_0003_ack_attribution.py` as required, closing the gap
  where a released wheel could omit a migration its own ORM model
  requires and still pass every content check. `scripts/release_module.py
  ::cmd_inspect` and a new PR-time architecture test
  (`test_every_migration_on_disk_is_enumerated_in_wheel_contents_required`)
  additionally DERIVE the required-migration extent from the actual
  `migrations/versions/` directory on disk, rather than trusting the
  hand-maintained `required` list alone — so a future migration file
  added without a matching allowlist entry fails at PR time, not silently
  in a release.

## 0.1.0a1 — 2026-08-22

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `8f52abc4` by release run `32571033237`.
The composition check registered this manifest alongside all six siblings
against published kernel `0.1.0a88` before the tag was written. Publication
composes nothing into an assembly and adopts nothing.

- Port Sub's policy/operation/attempt/insight state behind provider-neutral
  intents and immutable observation strings, excluding clients and secrets.
