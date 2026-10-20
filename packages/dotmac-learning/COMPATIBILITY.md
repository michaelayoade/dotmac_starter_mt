# Compatibility — dotmac-learning

## Current source correction — 2026-10-19

Published `0.1.0a1` is immutable. This prepared successor declares
`0.1.0a1+dev` for source qualification only; no numeric successor is allocated.
The actual declared published Kernel floor is `>=0.1.0a106`, including the
Learning namespace allocation. The older initial-declaration and floor statements
below are historical and are superseded by this dated correction.

The source retains the complete 27-table/260-field domain and 520 prior root
exports, adding 13 for 533 total. New reads are `catalog_course_by_slug`,
`catalog_chapter_by_number`, `account_cohort_names`, `get_certificate` and
`instructor_cohort_by_id`. Admission, bulk and invitation enrollment commands
require their frozen product-supplied initial-state snapshots; bulk additionally
requires a genuine tenant-bound `AccountPresenceSelection`. Presence includes
inactive accounts and supplies no authorization. Role/status creation values
have no fallback, and existing-row facts and authority checks are preserved.

`SessionUpdate.audit_event_id` is a required UUID-or-None field.
`SuccessTransitionEffect` and `SegmentMessageEffect`, returned by
`transition_success_entry_with_receipt` and `message_segment_with_receipt`,
carry the single operation's actual audit UUID alongside its observed result.
Legacy result APIs retain their shapes. Caller transaction ownership is unchanged.
Additive unreleased `lr_0007` removes only Enrollment role/status schema defaults;
released `lr_0001` through `lr_0006` remain byte-for-byte unchanged.

Shared Kernel/Learning composition, independent composed review, hosted migration
and full-suite qualification, and the exact installed candidate pair remain
pending. Academy caller retirement, Settings-owned policy binding, lab/identity
qualification and ERP integration remain separate adoption gates. No consumer is
newly adopted by this source preparation.

## Historical source notes

The initial source declaration is `0.1.0a1`; it is not a publication claim.
Its minimum Kernel dependency remains unset until a published Kernel containing
the accepted learning namespace allocation is verified. Kernel `0.1.0a105`
provides the identity contracts used by the source but does not contain that
allocation. Do not install or release against a guessed successor version.

The tenant-only module uses schema `mod_learning`, migration prefix `lr` and
branch `learning`. Its lineage requires `TENANT_SCOPE_CATALOG_V1`,
`MODULE_DATABASE_ROLES_V1` and `PARTY_PERSON_CATALOG_V1`, resolved through the
adopting assembly's bindings. It never embeds Academy revision IDs. Existing
published migrations retain their immutable bytes and history.

Academy adoption requires its genuine Kernel identity cutover and reference
closure, actual application and lab-worker privilege qualification, the existing
exam-engine/attempt-policy binding on the caller Session, and a resolved ERP
acknowledgement ownership policy. Installing a package alone proves none of
these. No production deployment is asserted.

The result path deliberately propagates required progress/certificate failures.
Academy's former MCQ catch-and-log path must be retired with explicit hosted
fault-injection qualification. Lab execution, leases and operation authorization
remain Academy responsibilities; the lab-result command's identifiers are
correlation facts, not proof of authorization.

Academy's MCQ adapter uses `SubmitMCQCommand` and `submit_mcq_activity`.
This command admits the learner through the existing attempt policy before
reading questions, then appends the Submission and optional submission event
before invoking the existing grader. Manual grading still reads the selected
questions before creating a pending Submission, without invoking the grader.
An exhausted or missing learner therefore retains precedence over a question
read failure or malformed stored JSON that causes the grader to raise. Grading
errors propagate after the staged Submission; the caller owns rollback.
The admitted observation supplies the bank, threshold, grading mode and course.
The product separately supplies the required `SubmitMCQCommand.missing_question_weight`;
this policy value is not inferred from the admitted observation.
Concurrent authoring and grant changes are not newly serialized.

The existing pregraded `MCQResultCommand`, `record_mcq_result` and
`record_mcq_result_with_attempt_policy` APIs keep their existing contracts;
lab result commands continue accepting externally validated grades. All these
paths share the same result writer. The ordered command is an additive source
candidate, with hosted ordering, concurrency, isolation and installed Academy
qualification still required before adoption. It adds no migration or release
identity.

Hosted CI must qualify real migration-chain PostgreSQL isolation, concurrency,
failure recovery, lossless transfer and post-new-facts reversal, then execute the
released artifact installed in Academy. Source review and unexecuted canaries
are not compatibility acceptance. Later slices of the full learning extraction
remain required after the initial seven-table release.

The prepared participation extension continues the same lineage at `lr_0003`.
V3 is a full21/194-field storage protocol; V1 and V2 remain seven/fourteen-table
compatibility APIs. Fresh Enrollment dual Party/profile references translate the
legacy People constraint explicitly; stable Academy identity mapping is unproved.
Trusted authority facts are supplied by product authorization and are not signed
credentials. Existing exam selection/grading are ported without algorithm changes;
attempt policy and remaining direct product learning writers still require their
designated extraction. Source static review is not hosted acceptance.

`get_completion(db, *, tenant_id, person_id, course_id)` reads the exact stored
aggregate as a detached `CompletionFact`, or returns `None`. It does not lock,
explicitly flush, recompute progress, issue a certificate, or apply visibility
policy. Normal caller Session query/autoflush behavior remains intact. The caller
supplies the tenant-scoped Session and owns its transaction. In particular, a retained certificate after regression does not
imply the stored completion status is still `completed`; the existing issuer's
return-existing-certificate behavior remains unchanged. Hosted PostgreSQL and
installed Academy route qualification remain required.

`bank_questions(db, *, tenant_id, bank_id)` returns a tuple of detached frozen
`QuestionFact` values with all persisted question fields. JSON options and
answer keys retain their stored shapes as independent deep copies, including
numeric tolerance and short-text regex option objects. A None or missing bank
ID returns an empty tuple. This ordinary tenant-scoped query adds no bank
existence, authoring validation, visibility, status or ordering gate, and keeps
normal Session query/autoflush behavior. Products retain answer-key disclosure
policy. The existing `questions_for` grading dictionaries and writers remain
unchanged. Hosted and installed caller qualification are still required.

`BankQuestion.options` accepts JSON values while preserving its omitted-value
empty-list default. In particular, numeric tolerance and short-text regex
configuration objects survive the existing `load_bank`, `doc_from_db` and
`ImportBankWithActivityCommand` paths. The existing linter and grader retain
their source rules, and list options retain their existing validation.
`BankQuestion.weight` is required; `load_bank` also requires the product-selected
`missing_question_weight` used only when a raw question omits its weight. This input correction adds no migration or writer change;
hosted PostgreSQL and installed caller qualification remain required.

The source-only `cohort_course_ids(db, *, tenant_id, cohort_id) -> list[UUID]`
read delegates the existing insights helper in the caller Session. It selects
active offering course IDs for the exact tenant and cohort, without an activity
join, existence check, explicit ordering or deduplication. Existing insights
callers and query behavior are unchanged. The canonical offering uniqueness
constraint prevents two rows for the same tenant, cohort and course; the reader
adds no deduplication policy. Hosted PostgreSQL membership, empty-result
and non-bypass RLS canaries are authored but unexecuted. This additive
source change claims no hosted qualification, publication or Academy adoption.

The source-only attempt selection input and exam question protocol now accept
`JsonValue` options, matching stored questions including numeric tolerance and
short-text regex dictionaries. Defaults remain `None`; no option normalization
or engine algorithm changes are introduced. Validity thresholds and duration factors come from required `ValidityTerms`
supplied by the product to `check_validity`; the engine continues to derive
paper-specific values from the actual questions. The public reason identifiers
`INVALID_NEAR_CHANCE` and `INVALID_TOO_FAST` retain their existing owner and values. Unit
JSON-shape/deep-copy and pure-engine canaries plus hosted PostgreSQL mixed-question
open/reload/close/retake canaries are authored but unexecuted. This source change
claims no hosted qualification, publication or Academy adoption.

## Chapter authoring renderer composition (source only)

`AuthorChapterCommand` and `author_chapter` add a trusted product-renderer
composition boundary: one tenant/course lookup rejects a missing course before
rendering, the renderer runs once, and the existing canonical chapter writer
then queries/inserts/updates, always increments course version, and flushes.
`upsert_chapter` retains its rendered-input signature and behavior. Both use
the caller Session and active root transaction; no commit or transaction is
created. Renderer errors propagate before chapter work; renderer transaction
changes are detected before chapter work using the existing boundary guards.
HTML rendering and safety remain product responsibilities; this callback is
not a sandbox. New sequencing and PostgreSQL canaries are authored only;
installed-package and hosted execution remain pending.


Raw decoded bank boundary (source preparation, 2026-10-17): the existing mutable,
unvalidated `BankDoc` and existing `balance_targets`/`propose_balance` owners are
public. `lint_bank` accepts that instance directly; normalized `BankDocument`
conversion remains available. `ImportBankWithActivityCommand` accepts raw instances
with `InstanceOf`, retaining nested decoder values and identity. The single loader
loop preserves required-key access, category conversion, then integer
weight conversion in original encounter order. Missing raw question weights use
the required product-supplied `missing_question_weight`; other raw defaults remain unchanged. It does not preflight or normalize
the raw document. Unknown-course skip precedes loading; new-bank flush and existing
replacement deletion/version changes precede question errors. Python conversion
errors leave the caller transaction active; non-JSON question values reach database
flush serialization instead of an earlier input-model rejection. Explicit null
policy preserves insert-default versus update-constraint behavior at caller flush.
The normalized API and compound course → bank → Activity sequence remain intact.
The seven pure rule bodies are unchanged. Added hosted canaries are authored only;
no runtime, installed-package, database, release or Academy adoption proof is claimed.
Academy still owns decoding, lint-before-lookup, path/YAML, rendering and per-file
commit behavior. Package version, dependency floor and schema remain unchanged.

`BankDocumentHeader` is a read-only structural protocol for already supplied bank
metadata; `BankFact` and legacy bank rows fit it without conversion. It is a trusted
header contract, not a validation or security boundary. `decoded_doc_from_db`
returns a mutable raw `BankDoc` with the source's exact nine question keys and JSON
values/nulls. It performs the original single ordered tenant/bank Questions query,
without a bank lookup, input validation, authoring/lint gate or transaction guard.
A stale header therefore returns an empty raw document retaining supplied metadata.
The existing typed `doc_from_db` reader remains unchanged. Raw-read sequencing,
shape, null/numeric/regex and stale-header canaries are authored, unexecuted. The
v1 hosted SQL assertions now use canonical schema-qualified model table names.


## Pure bank-rules import ownership (prepared source)


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


## Roster state authorization (prepared source)

`set_roster_state` deliberately corrects the legacy cached-role and query/write
race. It selects the target Enrollment and fresh tenant/cohort/person/role
scalars under `FOR UPDATE` before checking current non-student authorization.
The row lock lasts until the caller's transaction boundary, including after a
permission denial. An already cached ORM role is not refreshed or overwritten;
the returned fact uses the locked identity and role observations. Other cached
fact fields retain their existing semantics.

With caller autoflush disabled, a target pending deletion or unflushed scalar
change raises the existing `TransactionChanged` with `clean target enrollment required; caller rollback
required` before domain errors or the explicit final flush. This includes
Kernel's `autoflush=False` Sessions and caller `no_autoflush` scopes. The caller
must roll back; adapters must propagate this failure rather than catch it as an
inline domain error and commit pending unauthorized changes. Ordinary caller
query/autoflush behavior is retained. A successful command still performs the
existing full-session flush, including unrelated pending caller objects.

Authority scope still precedes the query; current non-student authorization
precedes invalid-state handling, which precedes missing-enrollment handling.
Shared roster helpers and other commands are unchanged. No Session, commit,
rollback, savepoint, retry, model, migration or public API is added. Real migrated
PostgreSQL stale-identity, pending-state, lock/contention, tenant-isolation and
caller-rollback canaries are authored only; hosted execution and installed
Academy qualification remain required before release or adoption.


## Ordered manual source composition (prepared source)

`ManualImportHeader` requires product-supplied `new_course_status` and
`new_course_listed` for newly created courses. It retains the five prior course
input fields in their original order and types. Existing-course imports do not
reset status or listing from those creation policy values. `ManualImport` inherits those fields and retains its
original default empty tuple of normalized `ImportedChapter` values. Existing
`import_manual(db, command)` still uses its final scoped refresh query and returns
an immutable `CourseFact`.

The additive `import_manual_chapters(db, header, *, chapters)` consumes a trusted
product `Iterable[ImportedChapter]` only after the original course lookup, new
course creation/flush, and metadata staging. Both entry points delegate one
writer with the original chapter lookup, source-hash skip, new order number,
body_md/order preservation on update, version bump, and final full-session flush.
The new path projects the owned row through the existing internal fact helper;
it adds no explicit final refresh query. Normal ORM attribute loading, caller
query/autoflush behavior, and database failure behavior remain intact.

Academy retains the strict figure preflight, sorted file discovery inside a lazy
generator, per-file decoding/rendering, raw UTF-8 and distinct figure byte
observations supplied to the public source-hash function, and filesystem copying.
A later source failure therefore leaves earlier chapter work and course metadata
staged, without bumping the course version or choosing rollback/commit. The caller
owns that outcome. The iterable must preserve the active caller root and nested
transaction; existing guards check after each yielded value before chapter SQL,
after exhaustion before version/flush, and after result projection. These checks
are not a sandbox and cannot undo an iterator's transaction changes. No retry,
autoflush override, transaction creation, commit, or rollback is introduced.

The Academy adapter now returns an immutable course fact, deliberately replacing
the legacy attached ORM return. Actual CLI callers consume id/slug/version after
commit, and the end-to-end caller consumes id/discipline; these value uses retain
their shape. This is not ORM alias, mutation, expiration, or automatic refresh
parity: an earlier fact does not change when a later import updates the same
course. Current-state consumers must explicitly reread through public APIs.
Old Academy business assertions are retained with scoped public count reads.
Real migrated PostgreSQL ordering, staging, caller outcome, RLS, duplicate-number,
transaction, tuple compatibility and statement-trace canaries are authored only.
Hosted execution and installed Academy qualification remain required; this source
change adds no migration, metadata, dependency, lock or release identity.


### Lab Activity identity and staging ports

`ActivityCreate` mirrors the mutable, plain-coercing `ActivityWrite` contract
without a caller-supplied `id`. Type, threshold, assessment mode, grading and
weight are required; only nullable fields have absence defaults. Explicit legacy
zero, signed, out-of-range and nonfinite float values are not tightened here.
`create_activity_identity(db, payload)` adds through the Activity owner and
flushes the entire caller Session once. The mapped UUID INSERT default generates
the identity; only `row.id` is read after flush. It returns the UUID, without
projecting an ActivityFact or introducing a transaction/savepoint/commit.

`stage_activity_patch(db, ActivityPatch(...))` performs one ordinary tenant/id
full-row SELECT with existing caller autoflush behavior. A missing or RLS-hidden
row returns `False`; a present row receives only provided fields and returns
`True`. There is no ending flush, refresh, populate-existing or fact projection.
Existing create/update/patch ports retain their returns, missing-row errors,
assignment order and flush behavior. Bank import shares private raw add/assignment
helpers while retaining its query, raw policy, order and flush/error boundaries.
Historical snapshot imports remain unchanged.

Academy's lab importer owns parsing, render/hash, raw Template limits and
versioning. Explicit YAML threshold values still pass through `float` before the
Template query; null and malformed values fail there. Only an absent YAML key
requests `learning_lab_pass_threshold` from product Settings. New Activity rows
also receive Settings assessment mode/grading/weight. Strict Settings reads are
lazy and cached per import call, with no-autoflush confined to that new read.
Unavailable or malformed configuration intentionally introduces a new failure
dependency for missing thresholds and new rows. It is not included in source
hashes, and changed/unchanged explicit-threshold imports need no Settings read.
The fixed `lab` type remains the action contract. Final caller Session flush,
including empty directories, and product Template identities remain unchanged.

These are source-only canaries. The migrated restricted-role PostgreSQL tests
are written, not executed locally. Academy's retained legacy admin fixture and
public-boundary observations do not qualify installed canonical Activity to
product LabTemplate/Kernel identity assembly. That end-to-end qualification
remains a release/adoption gate.


### 2026-10-19: pending caller scalars and current clean SQL values

The scheduling update, Success Queue transition lookup and certificate record
read use one ORM entity plus its table scalar projection from the same SQL row.
Scheduling and Success Queue retain their row locks. After ordinary caller
autoflush, only untouched scalar keys are assigned their current SQL values
with public SQLAlchemy committed-value assignment. Caller-touched fields retain
their values and history, including equal assignments, nulls and pending keys.
Clean stale fields refresh even when another field on the target is pending.
Missing SQL rows keep their existing not-found/None behavior.

Caller transaction, savepoint, validation, explicit flush and actual Kernel audit
ownership remain with the existing owners. No refresh, early flush, transaction
boundary, fallback attachment or result-level idempotence API is introduced.
The dependency floor is exactly the published Kernel >=0.1.0a106; the unchanged
Learning development identity is 0.1.0a1+dev, not a release allocation. This is a
source-only repair with authored PostgreSQL canaries. Independent source review,
combined-source hosted qualification and the exact installed pair remain required.
