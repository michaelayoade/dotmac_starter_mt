# dotmac-ai-operations

Product-first from Sub, this tenant module owns versioned intake policy,
provider-neutral operation intent, immutable execution observations and
advisory insight acknowledgement. Provider/model/request names are observation
strings only. Integrator plugins own credentials, endpoints, payloads and I/O;
products own prompts, domain context and every consequence.

`advisory.py` additionally carries a stateless advisory protocol: one
`AIAdvisoryInvocation` and its `AIAdvisoryResult` crossing a boundary, plus
`AIActionProposal`/`AIActionDecision`/`AIExecutionObservation`. Business
capability admission — whether a domain-owned capability such as
`support.customer.identify.v1` exists or is permitted at all — reaches this
package only as an opaque `AICapabilityExposureRef` (schema, permission,
approval and idempotency stay domain-owned). This module carries no
workflow node, routing decision, wait deadline or tool loop; session,
routing/orchestration policy and workflow next-step remain owned by the
consuming product's own orchestration owner (for example, Sub's
`ai.intake`).

**`AIActionDecision` is not authorization and not proof of human review.**
It records that a caller asserted a human reached an outcome on a
proposal — including an `edited` outcome, which asserts an edit happened,
not what the resulting owner command actually contained. There is no
signer, no authenticated reviewer identity, and none is invented here. A
product that needs real authorization or real proof of review must obtain
it from its own authenticated review system; see `advisory.py`'s module
docstring for the exact fields an authoritative reviewer receipt would
need to bind if one is ever composed with this protocol.

Evidence that affects a decision or an owner command is never a bare
string. `AIActionDecision.resulting_owner_command_evidence`,
`AIActionProposal.supporting_evidence_refs` items, and
`acknowledge_insight`'s `action_evidence` are each an `AIEvidenceBinding`
— a locator namespace, an immutable content digest and a media type/shape,
checked for shape and non-blankness at construction. This is a
structurally-valid CLAIM the caller makes, not a verified fact: nothing
resolves the locator, hashes the referenced content, or confirms the
media type matches it — see `AIEvidenceBinding`'s own docstring. A plain
locator string is refused outright where a binding is required; this
package never silently wraps or promotes a caller's string into one.
`AIInsight`'s legacy `action_evidence_ref` DATABASE COLUMN (published in
`0.1.0a1`) is preserved unrenamed and coexists with the new binding
columns — it is descriptive-only now, carries no digest, and never will;
`acknowledge_insight` only ever writes the new binding columns. The
CURRENT ORM model maps it to the private-by-convention
`_legacy_action_evidence_ref` rather than an ordinary attribute name.
**This is a convention, not an enforced guard**: Python has no real
private attributes, and nothing in this codebase stops a caller from
reading `_legacy_action_evidence_ref` directly, going through
`AIInsight.__table__.c["action_evidence_ref"]`, enumerating mapper
attributes, serializing `__dict__`, or issuing raw SQL against the
preserved column. Because the underlying column also stays writable by
an older, rolled-back application version (required for expand-only
rollback compatibility), a single row can end up carrying both a legacy
locator and a typed binding from different writers;
`authoritative_acknowledgement(insight)` is the function that applies the
read-side precedence this package defines for that case — the typed
binding wins whenever fully populated, the legacy locator is never
treated as evidence, and nothing falls back to "whichever is non-null" —
but using it is a convention this package cannot enforce against a
determined reader.

Acknowledgement ATTRIBUTION (who acknowledged an insight, and when) gets
the identical typed-shadowing treatment as evidence, for the same reason:
`acknowledged_by_ref`/`acknowledged_at` are LEGACY-DESCRIPTIVE, still
written every call for N-1 round-trip correctness, but no longer
authoritative. `AIAcknowledgementAttribution` (actor namespace, actor
type, opaque actor ref, aware acknowledgement timestamp) is the typed
replacement; `authoritative_acknowledgement(insight)` returns BOTH the
evidence AND the attribution together, as one paired result — the ONE
SANCTIONED way to read them without accidentally pairing a fresh
snapshot of one against a stale snapshot of the other.

**`authoritative_acknowledgement` REQUIRES an aware `acknowledged_at` and
REFUSES a naive one — decided permanently, not a gap to fix.** SQLite is
a fast LOGIC harness for this package's own test suite; it is NOT a
valid PERSISTENCE round-trip for authoritative attribution, and never
will be. SQLite's `DATETIME` type discards timezone offset on every
round trip, so calling this accessor after `db.refresh()` or a
fresh-session reload against SQLite raises `ValueError` — this is
correct, not a bug: an acknowledgement integrity envelope binding actor,
evidence and time is not the place for a timestamp this package inferred
rather than proved. Real PostgreSQL's `timestamptz` always returns an
aware value, so the boundary holds there without any special handling.

It is not an ENFORCED way: all eight underlying `AIInsight` columns are
private-by-convention (round 15 correction — this was previously claimed
as effectively impossible to read apart, which was false while the eight
columns were ordinary public attributes; they are now privatized, the
identical treatment the legacy locator already had, narrowing the gap
between the claim and the code rather than only the claim). Reading
`insight._action_evidence_locator_ref` (or any of the other seven)
directly, bypassing the accessor, is still possible and unsupported, not
prevented — a determined reader can also still go through
`AIInsight.__table__.c[...]`, mapper introspection, or raw SQL. Unlike
evidence, an N-1
writer's unconditional update genuinely overwrites the SAME
`acknowledged_by_ref`/`acknowledged_at` columns the current writer also
writes (there is no separate legacy pair being shadowed there); the
typed attribution columns survive intact only because that older writer
has no reference to them at all — not because of a lock or a database
constraint. There is deliberately no CHECK constraint enforcing
all-four-attribution-columns-together at the database level: the
writer-side guarantee is a claim about this repository's code, not proof
of what a real N-1 rehearsal against the actual published artifact would
show, and that rehearsal was found unreachable from this repository's
ordinary CI today (see `ao_0003_ack_attribution`'s module
docstring). The constraint is deferred to a focused successor migration
once that evidence exists, rather than landed early with a comment
admitting its premise is unverified.

`AIActionProposal` requires its own `capability`,
`execution_policy_version_ref` and `execution_policy_digest` to match the
`invocation` it carries — checked at proposal construction, not only
when the proposal is later wrapped in an `AIAdvisoryResult`. This is what
stops a decision from carrying a proposal that names a different
capability or policy than the invocation it claims to have been generated
for; `AIAdvisoryResult`'s equivalent checks remain as defence in depth.

`record_attempt` constructs the canonical `AIExecutionObservation` itself
from raw attempt inputs, so a caller cannot hand it an unvalidated
`AttemptInput` and reach durable storage or the idempotency fingerprint
without passing every invariant `AIExecutionObservation` enforces; a
malformed input is still reported as `AIOperationRefused`, matching every
other refusal in this module.
