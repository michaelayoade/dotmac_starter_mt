# Compatibility — dotmac-ai-operations

Version `0.1.0a2` requires `dotmac-kernel>=0.1.0a88`, which allocates
`mod_aiops` and prefix `ao`. Tenant-only; not composed by Starter.

`0.1.0a2`'s advisory-protocol contracts (`advisory.py`) declare no dependency
on `dotmac-integration`; the capability-id and contract-digest shapes are
replicated to match `dotmac_integration.spi` exactly, not imported from it.

**Field-shape change, still within unreleased `0.1.0a2`** (no prior tag ever
published this shape, so this is a pre-publication correction, not a
breaking change to a shipped contract): `AIActionDecision`'s
`resulting_owner_command_evidence_ref: str | None` is replaced by
`resulting_owner_command_evidence: AIEvidenceBinding | None`, and
`AIActionProposal.supporting_evidence_refs` changes from
`tuple[str, ...]` to `tuple[AIEvidenceBinding, ...]`. `AIEvidenceBinding`
is a new exported type (`locator_namespace`, `locator_ref`,
`content_digest`, `media_type`). A caller previously constructing either
field with a bare string must now construct an `AIEvidenceBinding`
instead; passing a bare string is refused at construction, not coerced.

`record_attempt` (`service.py`) changes its accepted parameters from a
single `command: AttemptInput` to the raw attempt fields directly
(`attempt_key`, `outcome`, `output_ref`, `output_digest`,
`provider_observation`, `model_observation`, `request_observation`,
`error_code`); it constructs the canonical `AIExecutionObservation`
internally. A caller previously passing `command=AttemptInput(...)` must
pass the same fields as keyword arguments instead. **Exception contract,
now made true rather than narrowed a fourth time** (this claim has been
found incomplete THREE times already — `observed_at` running outside the
`try`; `_aware` calling a `datetime` method on a value that was never
checked to be a `datetime`; and most recently `(ValueError,
OverflowError)` still letting a caller-supplied `tzinfo` whose
`utcoffset()` raises, e.g. `RuntimeError`, leak past this function raw):
`record_attempt`'s single `try` wraps `_aware(observed_at, ...)`, UTC
canonicalization of `observed_at`, and `AIExecutionObservation`'s
construction, and now catches `Exception` broadly rather than a named
subset. `_aware` itself still checks `isinstance(value, datetime)` before
calling any method on it. This is a deliberate trade-off, not an
oversight: `observed_at` and its `tzinfo` are always caller-supplied,
untrusted input, so a broad catch at this boundary genuinely holds the
documented contract ("malformed caller input is refused as
`AIOperationRefused`") — at the cost of also reporting a genuine bug
inside this three-line block as a refusal instead of letting it surface
unhandled. Three rounds of narrowing to a named exception set each found
a new caller-controlled edge the set didn't cover; this is why the
boundary is now broad instead of narrower-but-still-incomplete. The
idempotency
fingerprint hashes `observed_at` canonicalized to UTC, not its original
`isoformat()` — two values naming the same instant under different
offsets (which a `timestamptz` column
stores identically) fingerprint identically and do not conflict.

**Schema change against an ALREADY-PUBLISHED table, EXPAND-ONLY**
(`ai_insights`, shipped in `dotmac-ai-operations 0.1.0a1`; migration
`ao_0002_insight_evidence_binding`, additive against the immutable
`ao_0001_ai_operations`, by explicit ruling):

- The `ai_insights.action_evidence_ref` DATABASE COLUMN is **preserved
  exactly as published — not renamed, not dropped, not backfilled.** An
  application version from before this change still reads and writes that
  column correctly against the expanded schema (it has its own,
  separately-deployed model code, not this one); that round-trip is the
  actual compatibility guarantee here. Going forward it is
  DESCRIPTIVE-ONLY: `acknowledge_insight` no longer writes it, and it
  carries no evidentiary weight (no namespace, no digest, no media type
  ever validated against it) — it is not "the same evidence, differently
  shaped," it is a locator nobody verifies anymore.
- The CURRENT `AIInsight` ORM model, unlike the column, no longer
  publishes this under a normal attribute name: the mapped Python
  attribute is `_legacy_action_evidence_ref` (private BY CONVENTION only —
  Python has no real private attributes, and nothing here stops a caller
  reading that attribute directly, going through `AIInsight.__table__.c
  ["action_evidence_ref"]`, enumerating mapper attributes, serializing
  `__dict__`, or issuing raw SQL against the preserved column; this is
  documented as a convention, not claimed as an enforced guard), still
  mapped to the unchanged `action_evidence_ref` column. Preserving the
  database column for N-1 compatibility does not require the current
  model to keep exposing it as an ordinary attribute — use
  `authoritative_acknowledgement(insight)` for every read instead, which
  applies the precedence rule (typed binding wins whenever fully
  populated; the legacy locator is descriptive residue only, even when it
  is the only thing present on a row).
- Four new, additive, nullable columns carry the actual binding going
  forward: `action_evidence_locator_namespace`, `action_evidence_locator_ref`,
  `action_evidence_content_digest`, `action_evidence_media_type`
  (`AIEvidenceBinding`'s fields). `acknowledge_insight` WRITES all four
  together or all four `NULL`, never independently — that is a real
  writer-side guarantee. The READ side is narrower than "never
  independently" might suggest: see the `authoritative_acknowledgement`
  bullet below for the one, precise statement of what reading these
  columns actually guarantees (paired access is sanctioned; reading one
  independently is unsupported but possible) — not restated here, so
  there is one place this claim can drift out of sync with the code,
  not several.
- `acknowledge_insight`'s parameter changes from `action_evidence_ref: str
  | None` to `action_evidence: AIEvidenceBinding | None`. A caller
  previously passing `action_evidence_ref="some-locator"` must now
  construct an `AIEvidenceBinding` and pass it as `action_evidence=`; a
  bare string is refused, never silently wrapped.
- A row acknowledged before this migration keeps its original
  `action_evidence_ref` locator UNCHANGED and has all four new binding
  columns `NULL`. That is not a gap awaiting backfill: no digest or media
  type was ever captured for that acknowledgement, synthesising one would
  fabricate an integrity claim nobody made, and historical rows carry no
  digest and never will.
- Retiring `action_evidence_ref` (dropping the column) is deferred to a
  LATER migration, once the legacy inventory of rows still using it
  reaches zero — not attempted here.
- `acknowledge_insight`'s conditional `UPDATE ... WHERE status =
  'advisory'` guarantees at most one writer ONLY AMONG CALLERS OF THIS
  FUNCTION. It does NOT hold across the N-1 rollback window: an older
  application version's own (unconditional, pre-dating this guard)
  acknowledgement write can still land after this version's write
  commits, overwriting `acknowledged_by_ref` and `acknowledged_at` with
  the older version's actor and time. Round 13's finding here — this
  attributed the current version's typed EVIDENCE to the older version's
  actor/time, a false historical claim — is CLOSED by
  `ao_0003_ack_attribution` below: attribution now has its
  own typed columns, exactly like evidence, so an older writer with no
  reference to those columns cannot touch them either.

**Schema change against an ALREADY-PUBLISHED table, EXPAND-ONLY**
(`ai_insights`; migration `ao_0003_ack_attribution`,
additive against the immutable `ao_0001_ai_operations`, mirroring
`ao_0002_insight_evidence_binding`'s shape for the identical reason —
Michael's ruling, round 13/14: a row pairing authoritative typed evidence
with an actor/time an N-1 writer can overwrite makes a false HISTORICAL
claim, not merely a stale display one):

- `acknowledged_by_ref`/`acknowledged_at` are NOT renamed, NOT dropped,
  and NOT backfilled — preserved exactly as published (0.1.0a1), for the
  same N-1 round-trip reason `action_evidence_ref` is preserved. UNLIKE
  evidence, these are the SAME two columns an older writer also writes —
  there is no separate legacy pair being shadowed by a new pair of the
  identical shape, and that asymmetry is inherent, not an oversight:
  retrofitting a "legacy" rename onto `ao_0001`-published columns would
  break the very round-trip this package exists to preserve.
- Four new, additive, nullable columns carry the actual attribution going
  forward: `attribution_actor_namespace`, `attribution_actor_type`,
  `attribution_actor_ref`, `attribution_acknowledged_at`
  (`AIAcknowledgementAttribution`'s fields). `acknowledge_insight` WRITES
  all four together or all four `NULL`, never independently — a real
  writer-side guarantee, identical in shape to evidence's above. The
  READ side is covered once, precisely, by the `authoritative_
  acknowledgement` bullet immediately below — not restated here.
  `acknowledge_insight` gains `actor_attribution:
  AIAcknowledgementAttribution | None`; a bare actor string is refused,
  never silently wrapped.
- `acknowledged_at` is canonicalized to UTC EXACTLY ONCE inside
  `acknowledge_insight`, and that single value is what is written to
  BOTH the legacy `acknowledged_at` column and the typed
  `attribution_acknowledged_at` column — the same "one evaluation, one
  stored fact" standard `record_attempt`'s `_canonicalize_utc` fix holds
  for `observed_at`. A caller supplying `actor_attribution.acknowledged_at`
  naming a DIFFERENT instant than the top-level `acknowledged_at` is
  refused outright, not silently resolved in favour of either value.
- **CANONICAL READ-SIDE STATEMENT for both evidence and attribution — the
  one place this claim is made in this file; every other mention above
  points here rather than restating it.** `authoritative_acknowledgement
  (insight)` is the ONE SANCTIONED accessor returning evidence AND
  attribution TOGETHER (replacing the former `authoritative_action_
  evidence`) — a caller using it will not accidentally read one apart
  from the other. It is not an ENFORCED path: all eight underlying columns
  are private-by-convention
  (`_action_evidence_*`/`_attribution_*` Python attributes, physical
  column names unchanged), the identical treatment the legacy locator
  already had, and reading one directly — bypassing this accessor — is
  still possible and unsupported, not prevented. Attribution is `None`
  whenever the typed attribution is not fully present — partial
  attribution is REFUSED at the read side, never combined with the
  legacy `acknowledged_by_ref`/`acknowledged_at` to manufacture a
  complete-looking answer.
- **`authoritative_acknowledgement` REQUIRES an aware `acknowledged_at`
  and REFUSES a naive one — this is a decided, permanent boundary
  (Michael's ruling), not a gap awaiting a fix.** SQLite's `DATETIME`
  type discards timezone offset on every round trip: what it returns
  after a genuine reload is naive, and `AIAcknowledgementAttribution`'s
  `_require_aware` correctly raises on it rather than inferring UTC —
  an acknowledgement integrity envelope binding actor, evidence and time
  is not the place for an inferred timestamp; "an envelope whose
  timestamp was inferred is not an envelope." **Read this plainly, so a
  green SQLite suite is never mistaken for evidence that attribution
  round-trips: SQLite is a fast LOGIC harness for this package's test
  suite — it is NOT a valid PERSISTENCE round-trip for authoritative
  attribution, and never will be treated as one.** The genuine
  round-trip proof (write the canonical UTC instant, refresh, reload in
  a new session, resolve the paired evidence and attribution, including
  surviving an N-1 legacy overwrite) runs against real PostgreSQL in
  `tests/test_ai_operations_attribution_postgres.py`, using the real
  `ao_0001` → `ao_0002` → `ao_0003` migration chain, not ORM metadata.
  No global UTC-restoring type is added to accommodate SQLite in this
  slice; if genuine SQLite runtime support is ever required, that is a
  separately reviewed dialect-aware persistence type, not something
  folded into this accessor.
- A row acknowledged before this migration (or acknowledged only by an
  N-1 writer since) has all four attribution columns `NULL` and always
  will. NO BACKFILL: no trustworthy typed actor was ever captured for
  that acknowledgement, and manufacturing one would fabricate a
  historical claim nobody made.
- **NO CHECK CONSTRAINT** enforcing all-four-attribution-columns-together
  is added, deliberately. The writer-side guarantee is a claim about this
  repository's code, not proof of what a genuine N-1 rehearsal against
  the actual published artifact would show — that rehearsal was
  investigated and found unreachable from this repository's ordinary CI
  today (the private registry read path needed to install the real
  published N-1 artifact is gated to a `main`-only, `workflow_dispatch`-only
  protected environment). A constraint landed with a comment admitting
  its premise is unverified would still make the database enforce an
  unproven assumption — documenting the risk is not the same as removing
  it. The constraint is deferred to a focused SUCCESSOR migration, once
  that evidence actually exists.
- Retiring `acknowledged_by_ref`/`acknowledged_at` (dropping the columns)
  is deferred to a LATER migration, once the legacy inventory of rows and
  N-1 writers relying on them reaches zero — not attempted here, and not
  even proposed yet: unlike evidence's legacy column, these are still the
  columns EVERY currently-supported application version writes.

**Tightened validation, still within unreleased `0.1.0a2`** (no prior tag
ever published the looser shape, so this is a pre-publication correction):
`AIActionProposal` now requires its own `capability`,
`execution_policy_version_ref` and `execution_policy_digest` to match the
`invocation` it carries — previously this was checked only when the
proposal was wrapped in an `AIAdvisoryResult`, so a proposal (or an
`AIActionDecision` carrying one) could be constructed directly with a
capability or policy pin that contradicted its own embedded invocation. A
caller constructing a proposal must now supply consistent pins.
