# ADR-0064: Semantic content identity is not a row counter

**Status:** Accepted  
**Date:** 2026-08-25  
**Scope:** Fleet-wide; source facts, observations, outbox/event envelopes,
connector admission, webhook deduplication, cache validators and ETags

## Context

ERP's first `dotmac-tax` adapter used `VersionedMixin.version` as
`TaxFact.source_version`. Independent review found that the column had no
`server_onupdate`, trigger or constraint. It was an optimistic-locking writer
convention, not a revision of the tax evidence.

That failed in both directions:

- a tax-relevant child edit could leave the counter unchanged, reusing a source
  version for different evidence and producing a loud fingerprint conflict;
- an unrelated parent update could advance the counter while the tax evidence
  stayed identical. `dotmac-tax` identifies determination sets by source
  reference/version and does not make the content fingerprint unique, so the
  second submission could silently create duplicate statutory evidence.

The over-count direction is worse because every component reports success. The
source says it sent a new revision; the receiver truthfully records it; no
constraint collides; and two immutable records now describe one unchanged fact.

ERP PR #355 corrected the worked case at
`d4da838d80ebee878c74e0f5b40ebe93a5a0b809`: all three source-fact families use
`cv1:<sha256>` over their normalized tax evidence. The digest is length-prefixed,
canonicalizes exact finite money to currency minor units, distinguishes absent
values with a typed sentinel, sorts unordered references and namespaces its
algorithm. The payroll-only caller-supplied workaround disappeared because one
content contract now serves every family.

This is not tax-specific. The same defect exists whenever a row's `version` or
`updated_at` is spent as a semantic `source_version`, deduplication identity,
outbox/event content identity, webhook key, cache validator or strong ETag.

## Decision

### 1. A mutation counter proves mutation, not meaning

A generic row `version` or `updated_at` value may be an optimistic-concurrency
or ordering token. It does not prove which consumer-relevant evidence changed
and cannot, by itself, be a semantic content identity.

When a consumer needs both ordering and semantic identity, the contract carries
both explicitly:

- a sequence/revision token answers "which accepted mutation came later?";
- an algorithm-versioned content fingerprint answers "is this the same complete
  normalized evidence?"

Neither value is overloaded onto the other.

### 2. The fingerprint binds complete decision evidence

The owner defines the normalized evidence set. Its digest includes every fact
whose addition, removal or change should cause the consumer to reconsider,
including provenance even when the derived decision or amount remains equal.

The encoding must:

1. declare and freeze the field set and ordering;
2. length-prefix keys and values, so delimiters inside a value cannot collide;
3. sort only collections whose order is semantically irrelevant;
4. encode money as finite exact decimal text quantized to the currency's minor
   units, never as float or incidental `repr`;
5. distinguish absence from every literal value with a typed sentinel; and
6. namespace the algorithm (`cv1:`, `cv2:`), so changing fields or encoding is
   an explicit new contract.

The digest is not a substitute for a typed command. The command remains the
reviewable contract; the digest binds the exact normalized instance.

### 3. Algorithm transitions are cutovers

Changing the digest field set while retaining its namespace silently
re-identifies old evidence and is forbidden.

Persisted submissions replay their stored bytes and stored content version.
New submissions after the cutover use the new algorithm. Historical facts are
not recomputed wholesale under the new namespace; a required re-determination
is an explicit, evidenced supersession cohort.

For ERP tax adoption specifically:

- C3 keeps `observed_tax_code_refs` in `cv1`. Shadowing compares the complete
  legacy submission, so a legacy-calculator change must move the comparison
  unit even when `dotmac-tax` derives the same answer.
- C4 retires the legacy calculator and that field under `cv2`. Stored C3
  submissions keep replaying their `cv1` identity; new facts use `cv2`.

### 4. Concurrency and HTTP exceptions are narrow

A row counter remains valid for optimistic concurrency and may back an HTTP
conditional-write or weak-ETag contract only when the contract deliberately
means "any accepted row mutation", every relevant writer advances the token,
and that premise is enforced and sensitivity-tested.

A strong ETag identifies representation bytes. It therefore comes from those
bytes or from a revision contract proven to change exactly when those bytes do.
Naming a generic row counter `etag` does not establish that premise.

## Consequences

- Source contracts distinguish revision/order from content identity.
- Identical normalized evidence replays instead of minting a duplicate record.
- Same key/version with different normalized evidence remains a conflict.
- A provenance-only change intentionally moves the fingerprint even when the
  derived answer is unchanged.
- Digest evolution costs a named algorithm transition and cutover rule rather
  than silently changing historical identity.
- Existing generic row counters need not be removed; they keep their legitimate
  concurrency/ordering role.

## Enforcement

`tests/architecture/test_semantic_identity_and_replay.py` rejects direct AST
dataflow from `.version`/`.updated_at` into semantic identity sinks and proves
the detector against planted violations. Its one apparent hit is
`dotmac-accounting`'s typed `SourceIdentity`: the same test proves that this
frozen source contract carries `version` and an independently validated
`fingerprint`, and that the journal stores both. This is a machine-checked
premise, not a path exemption.

The detector is deliberately direct-dataflow coverage, not a claim of
whole-program taint analysis. Typed source contracts, focused behavior tests and
review remain responsible for indirect construction. Expanding the detector
requires another sensitivity proof and an explicit disposition of every new
finding; a prose scanner is not a substitute for semantic analysis (ADR-0018).

Related: ADR-0014 (at-most-once execution), ADR-0024 (typed inter-application
facts), and the external-observation admission standard in Sub's checked-in
source-of-truth map.
