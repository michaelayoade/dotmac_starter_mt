# Existing assessment policy and sitting source audit

This is the next source preparation within the full 27-table learning extraction.
It is not hosted qualification, a release or Academy adoption. The preceding
21-table source is PR888 at `055a3a0a5d54861e5021116fa38e62a97e9c62d6`,
with hosted qualification still pending.

The source is accepted Academy `0968881fc40ad86b3abfea2fc40044dd2dbf47c9`.
The complete immutable 119-file source/test/migration closure has manifest
SHA256 `0c6f477bcdb8477a6e34c6b15809f71f7382c2cc8ce3b43e72c387daa6879a37`.
Every full file was checked against its Git blob and byte count. The closure
includes the earlier 97-file participation audit, eight existing-engine files,
and assessment/audit/model/concurrency/policy/feedback/random-pool source tests.
Initial dossier source pins remain unchanged; this is accepted-source revalidation.

Two tables add eighteen persisted fields, giving 23 tables and 212 fields:

| Source model | Persisted fields, including timestamps |
|---|---|
| `ActivityAttempt` | `id`, `tenant_id`, `activity_id`, `person_id`, `question_ext_ids`, `started_at`, nullable `submitted_at`, `created_at`, `updated_at` |
| `AttemptGrant` | `id`, `tenant_id`, `activity_id`, `person_id`, `extra_attempts`, `reason`, nullable `granted_by`, `created_at`, `updated_at` |

Actual DDL is `0020_activity_attempts`, `0041_review_remediation` and
`0051_attempt_grants`. Preserve tenant-root and composite tenant/activity CASCADE
references, composite tenant/id uniques, lookup indexes, FORCE RLS and CRUD grants.
The open-sitting partial unique is `(tenant_id, activity_id, person_id)` where
`submitted_at IS NULL`. Fresh module DDL creates that constraint directly; the
legacy duplicate-closing backfill belongs to Academy migration, not fresh storage.
`person_id` and `granted_by` are logical UUIDs without source foreign keys.
Do not add identity, role, positivity or status constraints to historical storage.
Question IDs are JSONB with database default `[]`; grant extra attempts default
to one. Source timestamps and UUID defaults retain their existing ownership.

The prepared storage continuation is `lr_0004_assessment`, down `lr_0003_participation`,
in the same `mod_learning` namespace and learning lineage. Existing migrations
remain untouched. Kernel prerequisites must be verified before any new DDL.

Reuse `attempt_policy`'s existing `Entitlement`, `ActivityTerms`,
`BoundGrantReader`, grouped/per-learner grant reads and arithmetic. Granting adds
at least one attempt with a nonblank stripped reason, then writes the required
audit trail in the same caller transaction. The audit owner and exact field
mapping use Kernel's public `write_audit_event`, with the module declaring the
existing `attempt_granted` action. A separate trusted, tenant-bound audit context
supplies the actual actor type/id/label and source application; none is inferred
from nullable `granted_by`. The legacy `granted_by` value remains optional actor
Party enrichment. Preserve activity entity/id and person/extra/reason details.
Audit failure must propagate after the grant flush for caller rollback.
Do not create a competing audit store or transport/provider engine.

Reuse `attempts`' persisted open-sitting reuse, learner row lock, exact engine
selection/attempt variant, timestamp behavior, close operation and optional event.
Reuse feedback/read projections without introducing a second Submission/Score
writer. The existing module result path remains the canonical writer and requires
progress/completion/certificate failures to propagate; do not reintroduce the
legacy catch-and-log completion behavior. Products retain authorization,
presentation, admissions and external adapters.

The default attempt-policy result wrapper constructs its bound grant reader with
the exact caller Session and observed tenant/person/activity, then delegates to
the existing result writer. Its admission callback receives only immutable
observations; pure arithmetic retains the accepted source implementation. Preserve
the sitting lock's exact SQLAlchemy `key_share=True, read=False` flags, which emit
PostgreSQL `FOR NO KEY UPDATE`. That serializes sitting operations while remaining
compatible with foreign-key `KEY SHARE` checks. Do not call it a shared lock or
invent a new profile prerequisite for source sitting operations.

Before this slice is accepted, qualify its real migration-chain PostgreSQL
isolation, source algorithms, concurrent open constraint/locks, grant audit
failure, same-Session attempt-admission reader and full current 23-table transfer.
Installed release, stable Academy identity, ACK/reference/grant closure, writer
fences, compatible reversal preserving accepted new facts and deletes, and local
implementation retirement remain gates for the full goal.

The storage, remaining cohort commands, reused attempt services/read projections,
V4 transfer, catalog integration and 273-name public surface have independent
static review. This is prepared source, not executed acceptance evidence. Sitting
JSON facts retain historical JSON null, scalar, object and array values, including
array type and order. Native PostgreSQL JSONB types retain their comparators;
SQLite variants permit the shared metadata harness to create its schema. The
assessment migration keeps its explicit JSONB default; the ORM default relies on
the PostgreSQL column's equivalent implicit cast.

Required tenant-audit installation closure remains unresolved in this prepared
slice. Its current three declared prerequisites do not certify Kernel's tenant
audit table or append privileges. Kernel must own `tenant_audit_log.v1`, verify
the complete current 18-column audit contract, forced tenant RLS and effective
append-only runtime privileges, and bind its provider to the existing
`0029_audit_history_shape` revision. A separate verification-only Learning
successor after `lr_0004_assessment` must require this effect. Existing Fulfillment
and Template Studio tenant-audit consumers must convert together; the architecture
guard must resolve the public root writer export instead of missing that call.
No migration rewrite, competing audit writer or frozen-debt increase is proposed.
This assessment source is not ready for publication without that dependency.

The new Kernel effect also changes the release-floor proof. Published Kernel 105
validates prerequisite names during manifest construction, so neither importing
the new public constant nor spelling its literal name reaches namespace
registration on that predecessor. The unchanged Learning wheel must prove the
precise predecessor API refusal separately from installed shipped-ledger
allocation absence/presence, then pass actual default `ModuleRegistry` registration
with the complete current table set on a sufficient published Kernel. An import
failure is not namespace evidence. No numeric floor or release identity is
allocated by this source preparation.


Prepared bank-rule ownership uses the typed `dotmac_learning_bank_rules` import
package in the same versioned `dotmac-learning` wheel. It owns the single pure
`BankDoc`, `lint_bank`, `balance_targets` and `propose_balance` implementation;
`dotmac_learning.bank_validation` aliases it for existing internal callers.
This adds no distribution, storage namespace or migration. Default dependencies
remain declared. Limited same-wheel `--no-deps` standalone qualification, installed
typing/provenance, the actual Academy content-linter boundary and full-module
qualification remain pending; this inventory records source ownership only.
