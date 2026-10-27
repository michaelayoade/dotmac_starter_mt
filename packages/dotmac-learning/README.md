# dotmac-learning

Starter owns this reusable learning domain. The initial slice covers activity
results, best-result projections, course progress, completion, certificate
issuance and learning events, including course/activity reference persistence.
A further source slice adds curriculum lifecycle, question-bank validation,
chapter/subtopic reading and learner annotations (bank writing itself is now
the versioned lifecycle described below). The participation
slice adds enrolment/audience, tracks and course access, alongside the existing
pure exam-engine/grader port. The prepared continuation adds attempt policy and
persistence, scheduling, pacing, announcements, support queues and learning
read projections. The current unreleased successor candidate (intended
0.1.0a3, not allocated; version strings still read 0.1.0a2) adds composable
prerequisite/policy settings and the question-bank system of record, covering
28 tables and 298 persisted fields with 598 root exports. Academy is the
intended first adopter.

This is prepared source, pending hosted qualification and publication. Academy
has not adopted this module. See `EXTRACTION.toml` for source provenance,
ownership, migration and retirement gates.

The package uses the caller's SQLAlchemy Session and transaction. Commands retain
source staging/flush behavior; the application boundary commits or rolls back. Compose its `module` manifest
and `versions_dir()` lineage, bind the three declared Kernel prerequisites, and
use genuine Kernel tenant, Party and PartyPerson records. The prepared manifest owns 28 tables in `mod_learning` and the independent
`learning` Alembic branch, from `lr_0001_learning` through
`lr_0010_question_bank_versions`. `lr_0005_tenant_audit` verifies the existing Kernel
audit catalog without copying its provider or writer. Promotion requires the new
Kernel audit API to be published, a proven dependency floor and the additional
audit prerequisite declaration. These release gates remain outstanding. The
package creates no runtime engine or session.

The main command paths are `record_mcq_result`, `record_lab_result`,
`override_score`, `recompute_completion` and `issue_certificate`. Typed reference
commands preserve course/activity records. Best-result queries retain the
source fraction, pass, override and timestamp ordering. Completion counts every
activity, including labs and chapterless activities; a course with no activities
is incomplete. Regression clears completion time and retains an issued
certificate. Certificate lookup returns an existing issuance before checking
current eligibility.

Curriculum commands preserve authoring saves that always advance course version,
manual-import change detection, retained missing chapters and requested bank
versions. `create_draft_course` owns curriculum creation; the original
`create_course` reference command remains available. File and figure discovery,
YAML decoding, HTML rendering/sanitization and trusted visibility selection are
product adapters. Reading commands preserve tenant/person-scoped bookmarks and
notes, append-only subtopic progress and source heading behavior. All mutators
require an existing caller transaction.

The pure exam engine and grader reuse Academy's source algorithms.
The prepared `record_mcq_result_with_attempt_policy` binds the original attempt
policy to the same caller Session and required result/completion path. Academy
retains authorization, lab execution/operation authority and external-provider
adapters. The MCQ path
invokes a trusted typed admission policy with immutable observations under the
learner lock, then allocates the attempt in the same transaction. The policy
interface is an application boundary, not a sandbox for arbitrary Python code.
The application must use READ COMMITTED and roll back after typed admission
contention; the module does not retry a failed root transaction.

Required result, completion and certificate failures propagate to the caller.
Optional event emission reports recorded, suppressed or failed. It isolates its
own database failure in a savepoint; pending required caller writes must flush
successfully before that optional boundary. The application supplies the local
day timezone for daily event deduplication. Existing nullable-subject semantics
and the daily view-event concurrency race are preserved.

Presentation, PDFs, email, branding and deployment configuration remain product
responsibilities. ERP acknowledgement facts remain product integration state;
they are excluded from module completion storage. Adoption requires a qualified
lossless import, reference transfer, stopped legacy writers, one writer switch,
and reversal preserving accepted post-cutover facts. The installed Academy
binding and full product transfer/reversal remain outstanding.

Prepared storage transfer uses `LearningSnapshotV1` and seven closed row
contracts, independently of domain command validation. `export_snapshot` reads
the complete current declared tenant extent; `import_snapshot` requires an
entirely empty seven-table destination and independently verifies every key and
field. `verify_snapshot` compares the selected extent. They use an existing
caller-owned PostgreSQL Connection and transaction, with whole-table visibility,
and never complete the transaction. JSONB travels as canonical PostgreSQL text;
float8 travels as exact binary values, with a server roundtrip before insertion.
Timestamps are decoded from PostgreSQL binary values into UTC datetimes and
verified by their exact stored microseconds. Nonfinite timestamps or instants
outside Python's datetime range refuse. An unsupported representation refuses
rather than normalizing stored history. A logical transaction with driver
autocommit enabled is refused before SQL executes.

These functions report uncommitted storage observations. The application still
owns complete tenant selection, real migration lineage and catalog acceptance,
writer fencing, incoming references, worker grants, ACK handoff, and the
compatible assembly used for reverse transfer. A matching supplied snapshot
does not prove that the source snapshot included every required record.

`LearningSnapshotV2` and `export_snapshot_v2`, `import_snapshot_v2`,
`verify_snapshot_v2` cover all fourteen tables and 135 persisted fields. The
seven-table V1 API remains unchanged. V2 checks the complete destination is empty,
all representations and the actual fourteen-table FK graph before any insertion.
This includes genuine Kernel event subjects: the transfer actor needs effective
SELECT on `public.parties(id, tenant_id)` and `public.party_persons(party_id)`
in addition to the existing tenancy and learning catalog privileges. The module
grants no extra privileges. SIMPLE chapter/course edges preserve their actual
source semantics; logical references and plain person UUIDs gain no invented FK.
Question weights remain integers; only actual float columns use float8 bits.
V2 observations remain uncommitted storage evidence, with the same assembly
cutover and current-fact reversal obligations described above.

`LearningSnapshotV3` and its export/import/verify APIs cover the full twenty-one
tables and 194 persisted fields. All21 emptiness, representation and actual FK
preflight finishes before V2 inserts. Enrollment retains genuine tenant-scoped
Party plus PartyPerson references; nullable track uses actual MATCH SIMPLE
semantics, and entrance-bank UUID remains logical. Nullable cohort thresholds
use exact float8 bits; nullable windows retain exact timestamps. Independent
readback covers every field and current key, including deletions and extra rows.
These uncommitted package-store observations do not qualify Academy reversal.

Roster commands require trusted product authorization facts scoped to the tenant
and, for instructor eligibility, the person. The product must resolve these from
its Kernel authorization owner, never request payloads. Audience classification
uses an explicit roster, retaining reference bytes and unmatched classifications.
Track ordering, status-filter asymmetries, visibility versus access and inclusive
window/extension rules preserve source behavior. No retry, atomic-refusal or
concurrent-idempotency guarantee is added; callers roll back propagated failures.
Prepared cohort lifecycle and facilitator-removal commands retain their source
owner. Remaining direct Academy learning writers and domain reads must delegate
to qualified released APIs before thin-assembly retirement.


`LearningSnapshotV4` covers 23 tables and 212 fields; V5 covers 27 tables
and 260 fields. Each version preflights its complete extent before delegating
inserts to its predecessor and independently verifies current fields, keys and
deletes. V1–V3 retain their original scopes. Package-store observations remain
uncommitted and do not establish populated Academy rollback safety.

`LearningSnapshotV6` is the current full protocol: all 28 tables and 298
fields, including the composable-policy columns, prerequisite origin and
question-bank version pins, via `export_snapshot_v6`, `import_snapshot_v6` and
`verify_snapshot_v6`. Frozen V1–V5 keep their original scopes and row
contracts, and their export/verify paths refuse — rather than silently
dropping data — when a column they do not carry holds a non-default value;
default-only extents still transfer through them unchanged.

Prepared read APIs cover grades, transcripts, item analysis, catalog, agenda,
timeline, insights, learner dashboards, instructor workspaces, legacy support,
search, digests, reminder learning facts and support reports. Academy retains
rendering, account/authentication audience decisions, reminder occurrence policy,
preferences, notifications, token handling and delivery. Trusted account
availability is explicit and distinct from Kernel identity activity. Coupled
SuccessQueue and instructor reads require `ActiveAccountSelection`, a tenant-bound
nonexecuting product account relation embedded in the original SQL statement.
Instructor projects membership while retaining every roster row. Scoped facilitator
eligibility remains a separate source observation. Actual installed Academy account
storage, identity correspondence and concurrency parity remain unqualified.

`ensure_admission_enrollment` owns enrollment lookup/create/track adjustment while
preserving all existing role, status, audience, employee and access metadata.
Its required keyword `initial_state=AdmissionEnrollmentInitialState(...)` carries
product-resolved `role_in_cohort` and `status` only for a new row. Both fields are
required builtin strings of at most 20 characters, preserved verbatim. Existing
command intent and caller transaction ownership are unchanged. This intentional
API change requires a new future release; released a1 is immutable. See
[admission policy compatibility](../../docs/learning-admission-initial-state-policy.md).
Applicant, intake, identity,
admissions transitions and ERP acknowledgment remain product responsibilities.
All prepared continuations still require hosted qualification, release, installed
Academy execution, full caller retirement and qualified reversal.


The same versioned `dotmac-learning` wheel also provides the typed,
standard-library-only `dotmac_learning_bank_rules` import package. Its public API
is `BankDoc`, `lint_bank`, `balance_targets` and `propose_balance`; it owns the
single pure implementation. `dotmac_learning.bank_validation` explicitly aliases
that owner, including private helpers for existing internal callers. The eager
`dotmac_learning` root and its normalized-document `lint_bank` wrapper retain
their existing bindings. `BankDoc` identity is shared across these paths; moved
objects intentionally report `dotmac_learning_bank_rules` as `__module__`.
Academy retains YAML decoding (and its separate PyYAML dependency), paths and CLI
presentation. Standalone content linting imports the pure package directly.

Default installation still declares Kernel, SQLAlchemy, Pydantic and Alembic.
A clean environment installing the same wheel with `--no-deps` may qualify only
the pure entry point; it is not a `pip check`-clean full-module installation.
Hosted qualification must inspect both wheel and sdist for the new package and
its empty PEP 561 marker, prove same-wheel provenance, exercise the actual Academy
standalone linter from `/` without application or SQLAlchemy imports, and check
installed typing and shared class/function identity outside the checkout. Full
module dependency/registry qualification remains a separate required gate.
These checks, release and Academy installed adoption remain pending; source unit
subprocess canaries with an explicit source path are not installed-package proof.


Enrollment creation requires product-owned state snapshots: `BulkEnrollmentInitialState`
(role and status), `AdmissionEnrollmentInitialState` (role and status), and
`InvitationEnrollmentInitialState` (status; the required role policy still maps roles).
They preserve exact builtin strings up to 20 characters, including blank values.
Every public owner reconstructs its exact snapshot before database access.
Bulk also requires tenant-bound `AccountPresenceSelection`; its one-column person
relation must express actual product profile/account presence, allowing inactive
accounts. It conveys neither active-account eligibility nor authorization.
Existing enrollment activation remains an explicit command and facilitator
assignment retains its guarded instructor/active semantics.

Revision `lr_0007_enrollment_state_policy` removes only the two enrollment server
defaults. Current ORM columns likewise require explicit facts. It does not rewrite
rows or change constraints, indexes, RLS or grants. Its downgrade restores historical
schema defaults only and does not recover or erase post-upgrade accepted facts.
Hosted PostgreSQL qualification is required; this source capsule has no runtime claim.


## Composable prerequisites, policies and terms

Course prerequisites are tenant data rows evaluated by one Learning evaluator:
a closed condition-kind catalog (`course_completed`, `certificate_held`,
`course_grade_at_least`, `activity_passed`, `attempt_number_at_least`) with
typed-validated parameters and group keys, composed `all|any` across groups
(ALL within a group). The defaults (`course_completed`, group `g0`,
composition `all`) reproduce the prior behaviour exactly, and legacy
`requires: []` declarations still parse. Track gating
(`none|previous|all_previous`, default `previous`) is evaluated in the same
pass. Reconciliation records each row's origin and rewrites only
`origin=yaml_import` rows; app-authored rows are never rewritten by a file
reconcile.

Learner-visible policies are data or explicit terms inputs, each defaulting to
the prior hardcoded behaviour: completion fraction `1.0`, certificate
auto-issue on with no expiry, scoring policy `best`, attempt cooldown `0`,
pacing default open, feedback reveal practice=immediate /
graded=on_pass_or_exhausted / exam=never (`exam -> immediate` is refused),
progress-trend 8/3/0.03, cohort half-fraction `0.5`, and all five success rules
enabled at their current severities. Learning supplies no fallback for the new
required terms/command fields (see COMPATIBILITY.md for the exhaustive
breaking list); the product composes them from its Settings at each call site.
Policy changes are prospective only: completion-fraction and certificate
validity never rewrite stored completions or issued certificates, a scoring
policy change is refused once submissions exist (the raw staging path is a
documented trusted-import bypass), and cooldowns constrain only later
admissions.

## Question-bank system of record

Question banks are versioned in the database. Authors work on drafts
(create/copy/upsert/delete/discard), lint them with `lint_bank_version`, and
publish a lint-clean draft as an immutable version; publishing never
auto-repoints activities, so moving an activity to a new version and retiring
an old version are explicit commands. Attempts pin the bank version they
opened against and are graded and read through that pin. YAML is a transport
format only: import lands as a draft (never overwriting a UI draft, replacing
an untouched YAML draft only via the explicit flag, writing no Activity rows)
and export emits the audited published document. The former destructive
writers `load_bank` and `import_bank_with_activity` now raise
`LegacyBankWriterRetired` without executing SQL. Learner sittings use the
key-free `sitting_questions` projection. Learning checks no roles internally:
commands take a trusted `BankAuditContext`, and restricting publish/export to
administrators is the adopting product's composition responsibility.

Answer keys historically lived in Git YAML; that exposure cannot be undone by
deletion and no renewed secrecy is claimed for it. Treat historically
committed bank content as disclosed, and retire/replace exposed banks with
newly authored items rather than rewriting history. Hosted PostgreSQL
qualification, the exact installed pair, and Academy adoption/caller
retirement for all of the above remain pending gates.

Bulk creation of a non-student enrollment requires admin authority, including
when product policy configures the instructor role. Refused new members appear
in `admin_required`; other emails continue. The policy vocabulary stays open for
admins. For this refusal mode, track validation remains first and read-only;
only permitted existing members cause track offerings to be ensured. Existing
activation, normal student/admin bulk behavior, and caller transactions retain
their established semantics. Genuine product Request authorization remains the
consumer's responsibility; the Learning authority DTO is not independent proof.
