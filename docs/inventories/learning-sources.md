# Academy learning extraction: source inventory and boundaries

Observed 2026-10-16. This inventory supports the user-authorized extraction of
`dotmac-learning`; it records source and proposed cutover requirements, not a
published module, installed adoption, database migration or deployment.

## Source coordinates and authority

| Repository | Immutable audit coordinate | Finding |
|---|---|---|
| `dotmac_academy_app` | `a99a395e066db4eea3ec1d73219ab5dd006c1d1d` | Qualifying learning source and first intended adopter. Learning services and tables remain local; Kernel is pinned to `0.1.0a38`. |
| `dotmac_erp` | `537f0ccba846c1ea3304805ed17a4d3b9711596b` | Duplicate LMS code is nonauthoritative under Academy ADR 0004. Employment, requirements, credentials, in-person training and rebuildable Academy projections retain their distinct ERP ownership. |
| `dotmac_sub` | `13afd67e43ec946e5e25564628806ce4ddd7441c` | No learning lifecycle writer or matching consumer was found in the complete default-branch tree and targeted symbol searches. This is a scoped observation at this revision. |
| `dotmac_starter_mt` | `2e0c04ce61c50c95472fb26ad1867d5099edb832` | No learning distribution or namespace exists at this coordinate. Forms and Documents have different contracts and do not own learning decisions. |

ERP learning paths were first read at `93d7e111d9278b8377bac311e78308c99f51da79`.
The scoped diff through cached `d1c1c7975eead7cb716fc4d590dabe97b6eebcdd`
was empty; the remote comparison to the audit coordinate changed no learning,
training, assessment, certificate or Academy path. Sub's separate `dev` at
`bd593ba9d311a087f0c3abb9d2300220fe7d7af3` also showed no matching paths;
it is not the default branch and is not substituted for `main`.

Academy ADR 0004 assigns Academy sole authority for curriculum, enrolment,
activity, assessment, progress, completion and certificate issue. ERP decides
employment and training requirements and receives learning projections. Stable
`employee_ref` and explicit `audience` cross that boundary; email is not
identity. Extracting implementation into a package does not transfer these
operational facts to ERP or make several deployments co-own an aggregate.
Academy is a sufficient first consumer; a future adopter will prove additional
reuse rather than authorize the initial extraction.

## Accepted source correction checkpoint

The immutable audit coordinates above remain the initial evidence. Academy
PR #177 merged at `61f69194d6591c6a2d13472a5e38c3a5ecf34454`, retaining the
reviewed `b07e89455ea4ec691e99f01a2fe6fb47dab58357` tree. Hosted PR and push
CI passed; PR job `117333097511` reports 1,394 passed and 2 skipped. Resulting
main CI `39012406153` and engineering standards `39012406191` both succeeded.
The accepted correction includes final-lab completion, aggregate/certificate
locking, worker least privilege, admission locks compatible with real People
foreign keys, and lab queue partial-index inference under generic prepared plans.
The optional-consequence SQL-failure regressions also passed. This is source
qualification, not learning-package publication or Academy module adoption.
The dossier records this accepted-source revalidation while preserving the
initial audit pins.

The reviewed draft dossier is [learning-extraction-dossier.toml](learning-extraction-dossier.toml).
It deliberately lives outside `packages/` until allocation is merged. Move it
to `packages/dotmac-learning/EXTRACTION.toml` with implementation, retaining
these initial audit coordinates and adding accepted-source revalidation.
The [immutable Kernel entry proposal](../superpowers/specs/2026-10-16-immutable-kernel-lineage-adoption.md)
is source-only design for a prerequisite; it is not an accepted executor,
target selection, custody approval or passed adoption gate.

## Learning source and proposed module scope

The inspected Academy tree has 47 mapped tables in 31 model files and 61
migration files. The following groups identify the reusable learning domain;
the package's eventual manifest and migration inventory must enumerate the
exact selected tables, rather than treating this design table as composition.

| Concern | Source models / tables | Behavior and source services |
|---|---|---|
| Curriculum | `course.py`: `courses`, `chapters`; `assessment.py`: `question_banks`, `questions`, `activities` | `authoring.py`, `bank_loader.py`, content ingestion and publication. Content files remain assembly inputs. |
| Results and attempts | `assessment.py`: `submissions`, `scores`; `attempt.py`: `activity_attempts`, `attempt_grants` | `assessment.py`, `attempts.py`, `attempt_policy.py`, `exam_engine.py`, pure grading. Preserve one owner for selection, disclosure and attempt grants. |
| Learning participation | `cohort.py`: `cohorts`, `enrollments`; `track.py`: `tracks`, `track_courses`, `cohort_tracks`; `offering.py`: `course_offerings`; `prerequisite.py`: `course_prerequisites` | `roster.py`, `entitlements.py`, track ordering and prerequisite checks. Admissions supplies an authorized enrolment command, not direct module-table writes. |
| Reading and learner annotations | `reading.py`: `chapter_reads`, `subtopic_reads`; `bookmark.py`: `chapter_bookmarks`, `chapter_notes` | `reading_progress.py`, `bookmarks.py`; retain heading validation, durable reading and idempotent subtopic completion. |
| Completion and certificates | `completion.py`: `course_completions`; `certificate.py`: `certificates` | `completion.py`, certificate issuance in `certificates.py`, `gradebook.py`. Presentation and delivery remain adapters. |
| Learning activity evidence | `learning_event.py`: `learning_events` | `learning_events.py`, learning analytics and report facts. Preserve existing deduplication and explicitly characterize best-effort event behavior. |
| Learning delivery | `pacing.py`: `offering_activities`; `class_session.py`: `class_sessions`; `announcement.py`: `announcements` | Pacing, timetable and cohort/course announcement decisions; delivery and rendered agenda/email stay outside the module. |
| Learner support | `success_queue.py`: `success_queue_entries` | `insights.py`, `success_queue.py`; risk facts, explainable interventions and their lifecycle require an explicit disposition in the full extraction, not silent retention as a second local learning engine. |

The remaining boundaries are explicit:

- `tenants`, `tenant_domains`, `people`, `user_credentials`, `auth_sessions`,
  `roles`, `person_roles` and `audit_events` are the existing identity/security
  estate. Learning must not copy them. Kernel adoption uses the existing
  identity/facet programme and preserves learner UUID meaning.
- `applicants`, `onboarding_tasks` and `tenant_entrance_defaults` are admissions
  and applicant-policy state. Both applicant and enrolled-learner exam adapters
  consume the same exam decisions without collapsing those identities.
- `lab_templates`, `lab_instances` and `lab_operations` retain lab-provider and
  execution ownership. The learning activity/result contract consumes lab
  results, includes them in completion and never provisions infrastructure.
- `notifications`, `email_outbox`, `reminder_log` and `reminder_preferences`
  retain delivery/recipient-policy adapter ownership. Learning eligibility and
  inactivity facts come from the domain; no SMTP, provider client or second
  retry engine moves into the package.
- `account_tokens` and `platform_settings` do not become learning identity or
  settings implementations. Product configuration binds existing facilities.

ERP reference paths are `app/models/people/training/learning_assessment.py`,
`app/services/people/training/learning_assessment.py`,
`app/models/people/training/academy.py`,
`app/services/people/training/academy.py`, and
`app/services/dotmac_academy/{training_sync,events}.py`.
`tests/services/test_dotmac_academy_training_sync.py` and the training event /
assignment tests establish their different projection and in-person meanings;
they do not establish a second authoritative LMS consumer.

## First complete vertical and current behavior

The acceptance path is activity result -> best-result projection -> course
completion -> certificate record. Curriculum parent records needed to preserve
tenant-safe references are part of the storage dependency closure even before
their authoring operations are ported. A completion-only helper leaving result
writers and serialization decisions local is an intermediate step.

- MCQ submission writes `Submission` and `Score` in
  `assessment.submit_activity`, then recomputes completion. Instructor/manual
  grading uses `assessment.override_score`. Lab results are written by
  `lab_lifecycle.grade`, invoked by the leased `lab_operations.run_claimed`
  worker. Provider execution remains outside learning.
- `best_scores_for` ranks by fraction, pass status, override source and creation
  time. A lower correction currently cannot displace a higher score. Preserve
  that observed policy unless a separately evidenced policy change is made.
- Completion counts every course activity, including chapterless assessments
  and labs. Zero activities is incomplete. Recalculation can clear
  `completed_at` on regression, despite the current "stamp once" docstring.
- Existing certificate issue returns the prior certificate before checking
  current completion; completion regression does not revoke it. Certificate
  presentation, email consent, notifications, delivery and ERP synchronization
  remain product adapters.
- Subtopic completion uses `ON CONFLICT` against its unique constraint.
  Once-per-subject learning events use a partial unique index; daily views have
  a documented select/insert duplicate race. These are different contracts.

Existing characterization sources include
`tests/services/test_assessment_service.py`, `test_manual_grading.py`,
`test_completion.py`, `test_certificates.py`, `test_learning_events.py`,
`test_reading_progress.py`, `test_lab_lifecycle.py`,
`test_lab_operations_worker.py`, `test_lab_worker_privileges.py`, and
`tests/web/test_labs_web.py`. Listing a test is not evidence it has run at a new
revision.

### Initial implementation closure

The proposed first manifest owns exactly seven tenant tables in `mod_learning`:
`courses`, `activities`, `submissions`, `scores`, `course_completions`,
`certificates` and `learning_events`. Preserve their identifiers and existing
tenant-safe internal references. `Activity.bank_id` currently has no foreign
key and remains a reference supplied by the existing assessment owner until
question-bank storage moves. Inventory and migrate every inbound reference
from the later-slice tables; do not strand their current constraints.

The module owns result append and attempt-number allocation, best-result
queries, aggregate serialization, completion, certificate issuance and learning
evidence. All MCQ, override and lab producers call typed result commands;
Academy must stop inserting these domain rows. Narrow course/activity
persistence commands receive the existing authoring decisions without creating
a second authoring policy. The existing `exam_engine`, grading and
`attempt_policy` stay their decision owners until their designated port.
Caller sessions retain transaction ownership; commands flush without committing.
Moving result serialization ahead of result append requires explicit concurrent
attempt qualification against the characterized source.

Initial progress means best-result and course progress. Chapter/subtopic
reading, question selection, attempt grants, enrolment/access, learning delivery
and learner support remain explicit later slices of the full goal.

Learning events currently have a real composite People foreign key. Preserve
the tenant/person and lifecycle semantics through Kernel's existing tenant/Party
and PartyPerson subtype constraint pattern and `PARTY_PERSON_CATALOG_V1`.
Do not silently drop the constraint, invent a learner table or retain Academy's
identity fork. Genuine Kernel person fixtures qualify package construction;
they cannot establish Academy's identity migration or adoption.

Certificate rendering, consent, delivery and ERP transport remain Academy
adapters consuming typed domain facts in the caller transaction. Preserve
required result/certificate atomicity and optional-adapter savepoint isolation.
`CourseCompletion.erp_synced_at` is delivery checkpoint state: adoption must
preserve its values in an explicitly Academy-owned checkpoint keyed by the
completion identity, or an evidenced existing checkpoint owner. ERP delivery
must stop updating module-owned completion rows. Do not introduce another
delivery or outbox engine.

Hosted proof must cover real RLS/constraints/roles, first creation, mixed
admission/certificate locking, result-number serialization, correction ranking,
final labs, replay/regression, populated transfer and rollback. No consumer or
adoption claim is added until the released package is consumed and displaced
writers are retired.

### First-slice writer retirement and reference closure

Source searches cover production Python under `app/`, scripts and migrations;
tests are characterization evidence separately. They establish source closure,
not deployed populations. The writer list distinguishes the initial pinned audit from
PR #177's accepted additions at `61f69194`. The corrected writer paths are
revalidated against the merged source tree; they remain local until adoption.

| Selected state | Audited writer / accepted source-fix addition | Required disposition |
|---|---|---|
| Courses | `services/authoring.py:create_course`, `services/content_import.py:import_manual` through instructor/CLI callers | Delegate typed course persistence; retain existing authoring decisions until their port. |
| Activities | `cli.py:_load_banks`, `services/lab_content.py:import_labs` | Delegate typed activity persistence, preserving bank and lab references. |
| Submissions and scores | `services/assessment.py:submit_activity`, `override_score`; `services/lab_lifecycle.py:grade` through the leased worker | Delegate result commands; preserve automatic/manual/lab distinctions and caller transactions. |
| Completion | Audited `services/completion.py:recompute_completion`; PR #177 adds `services/completion_aggregate.py:ensure_locked_completion` and the direct lab-grade recompute call | Port the qualified aggregate writer. Assessment and lab grade are both recompute callers in the accepted source. No additional worker reconciliation hook was found. |
| Certificates | `services/certificates.py:issue_certificate`, called by completion and certificate download | Delegate issuance; retain PDF rendering and product response adapters. |
| Learning events | `services/learning_events.py:record` / `emit` | Port the sole row writer and its deduplication/query contract. Assessment, attempts, labs, certificates, catalogue and learning routes must delegate. |
| ERP delivery checkpoint | `services/erp_sync.py:push_completion` writes `erp_synced_at`; `sync_pending` and CLI `_erp_training_sync` select pending delivery | Preserve existing checkpoint values in the Academy-owned delivery owner; stop mutation of the learning completion row. |

`scripts/seed_academy_demo.py` also creates course/activity rows. It is a demo
adapter and must consume the selected module commands after adoption. Historical
remediation in migration `0041_review_remediation.py` updates submissions, and
`0042_course_listed.py` backfills courses. Preserve released migration bytes;
fresh installation still executes the actual history before the explicit
successor handoff. Worker grants in 0056 and accepted 0061 must be reconciled
against the selected final schema and exact column privileges.

Inbound composite course references exist in `chapters`, `course_offerings`,
both sides of `course_prerequisites` and `track_courses`. `chapter_bookmarks`
and `chapter_notes` use simple course-ID FKs: preserve and explicitly verify
their existing semantics rather than claiming composite tenant enforcement.
Within the first slice, completion/certificate course references and
submission/activity/score references also require exact preservation.
`activity_attempts`, `attempt_grants` and `offering_activities` have composite
activity references. All remaining local model declarations and live incoming
constraints must point to the actual canonical destination relations.

Logical course references also exist in question banks, activities and lab
templates; logical activity references exist in lab templates/instances.
These are not existing FK guarantees. No inbound FK/model reference into scores,
completion, certificates or learning events was found by the scoped search.

Read retirement covers catalogue/search/entitlements/tracks/agenda/bookmarks;
assessment, gradebook, reports, analytics and dashboards; learner digest/todo,
risk/insights/success queue/reminders/email; ERP/HR/admin/CSV reports and email
outbox; and the catalogue, learning, accounts, instructor, bookmarks, lab,
gradebook, reports and admin-home web adapters. Module-owned bulk queries and
typed facts replace selected-table direct ORM reads while later-slice decisions
retain their explicit current owners. Preserve event queries `last_activity_at`,
`weekly_buckets`, `active_person_ids` and `kind_counts`; no second event ledger
or local learning projection writer is introduced.

### Canonical storage handoff

The initial `lr_0001` always creates its declared canonical schema and tables
after genuine Kernel prerequisite verification. It has no source detection,
Academy branch, stamp, existence skip or alternate adopted-table interpretation.
After the identity gate, the released canonical lineage may install an empty,
inactive destination alongside the legacy tables.

Academy then stops every affected writer and reads its source through its own
migration owner. A narrow learning-owned import participant validates seven
explicit mappings and inserts courses, activities, submissions, scores,
completions, certificates and events in dependency order using the same caller
transaction. Refuse unexplained destination contents or invalid source mappings.
Preserve IDs, timestamps, attempt numbers, score source/reasons, serials, JSON
and deduplication facts. Import performs no grading, recomputation, event
emission, certificate reissuance, delivery or commit. Transfer the ERP checkpoint
through its Academy owner in that same controlled operation.

Verify tenant-partitioned key sets and complete canonical row contents, target
constraints/RLS/grants and every incoming reference; counts alone are weak
evidence. Rebind incoming FKs with their original tenant components, delete
actions and deferrability, update reader bindings, then switch to the released
owner once. Refuse unaccounted references before removing old tables. Any
temporarily retained recovery copy is sealed and cannot receive operational or
mirrored writes.

Hosted proof includes fresh installation independent of Academy; populated
transfer; cross-tenant/orphan/partial-destination/privilege refusal; exact FK
destination and delete behavior; no independent concurrent source/target writers;
and the actual adopted lab, certificate, event and mixed-lock paths. Pre-switch
failure rolls back import/rebinding while the canonical empty destination may
remain inactive. After new target writes, rollback needs a proved reverse
mapping for the complete accepted state of all seven tables, including new or
edited courses/activities, results, completion updates/regression, certificates
and events, plus product checkpoints and affected dependent references.
Reverse FK and code handoff occurs under another writer stop, or use the
approved restore/replay boundary. Prove post-cutover course/activity changes,
completion regression, a new result and certificate, checkpoints and dependent
references survive reversal. The rollback baseline is the compatible,
identity-adopted assembly before the learning handoff, not the original a38
image. An image rollback alone cannot preserve new facts.

## Prerequisite source correction

At the Academy audit coordinate a passing final lab never invokes completion:
the check route queues a lab operation, the worker calls `lab_lifecycle.grade`,
and polling reads the score. No searched caller or settlement hook recomputes
completion. This contradicts ADR 0004's lab requirement and must be corrected
explicitly before porting the vertical.

Academy PR #177 supplies that correction at accepted merge
`61f69194d6591c6a2d13472a5e38c3a5ecf34454`, qualified by hosted PR/push CI
and resulting main CI. Its aggregate lock uses the
unique tenant/learner/course completion row, including concurrent creation,
instead of granting the lab worker identity-update permission. Migration 0061
adds only the necessary course reads, completion inserts/state-column updates
and certificate insertion. Existing notification/event/email intent grants are
retained. Required certificate persistence shares lab settlement's transaction;
optional notification/email errors use savepoints. Actual-role PostgreSQL,
concurrency, retry, tenant and rollback tests passed hosted qualification.
Both submission and attempt-close admission locks use `FOR NO KEY UPDATE`,
retaining admission exclusion while permitting certificate event/notification
People FK checks. Real crossed-lock tests cover direct submission and attempt
close followed by submission in the same session, asserting persisted
consequences as well as successful commits.

Other observed gaps—manual grading's missing `work_graded` event, highest-score
correction semantics and dashboard resume selection—are separately recorded
behavior. They are not silently changed as part of extraction.

## Namespace, lineage and Kernel prerequisites

Starter allocation PR #873 reserves `learning` / `mod_learning` / `lr` /
branch `learning`, at `8fe888f687a02f5d20cafb279704cd7ad0a8575d`. It contains
no learning source or migrations. Allocation must be merged into the source
branch's merge base before package source is introduced, per AGENTS rule 14.
The current development marker is `0.1.0a105+dev`; neither it nor allocation
is a published Kernel floor.

The new namespace must ship in an authorized, verified Kernel release before
learning can declare a pinnable floor. Floor qualification must register the
learning namespace and import its service, and fail with the preceding
published Kernel. An import-only check that misses namespace registration is
insufficient.

Academy still pins Kernel a38 and mounts legacy browser routers. Current Kernel
disables that legacy mounting and requires a Kernel `Party` authentication
principal plus explicit browser/API composition policy. Full learning adoption
therefore depends on the existing Academy identity and UI-05 facet cutover.
At Sub's audit coordinate,
`tests/integration/test_kernel_lineage_rehearsal.py` still pins
`EXPECTED_FIRST_FAILURE = "0001_initial_tenant_schema"` and asserts that exact
failure. `docs/runbooks/KERNEL_LINEAGE_MINIMIZED_REHEARSAL.md` says its minimized
rehearsal does not authorize moving that ratchet, and
`docs/PLATFORM_ADOPTION_LEDGER.md` still requires collision disposition,
projected-row and grant parity, and an atomic 0001 pass. The full reference
lineage proof is therefore unfulfilled; green checks retaining an expected
failure do not establish adoption. Starter ADR 0017 retains Sub as the
tenant-plane/RLS reference adopter. The older staging measurements in
`docs/inventories/sub-lineage-dispositions.md` are not a fresh database
attestation.

Academy still has no completed Party/facet cutover. The existing identity
programme, PR #122, has an accepted strategy with design-only implementation
state. Its current candidate is `32b9634edcf3ba2180510d6a9edf32a824548439`,
refreshed against the accepted `61f69194`/0061 source and still under qualification:
the refreshed identity ADR is 0009, preserving the current lab-worker ADR 0008.
It records 28 human references (11 physical, 17 logical), including nullable
`lab_operations.requested_by`; `claimed_by` remains a worker fencing string.
The complete unchanged Kernel lineage and genuine independent history must
precede runtime. The historical stamp/partial-chain bridge is explicitly
superseded and rejected. No entry executor or identity adoption is claimed.
PR #129's separate guard-only change also supplies no adoption evidence.
These dependencies require implementation and fresh hosted qualification;
historical records cannot substitute for that.
Do not manufacture a Party wrapper, lower the learning floor, copy the ledger
into Academy or open a competing browser/authentication owner.

Learning commands carry typed tenant-scoped learner/actor references. A package
imports neither `app.models` nor another product, and never reads another
deployment's database. Tenant/role prerequisites use the existing logical
bindings; package migrations cannot name Academy revision IDs.

## Adoption, retirement and proof obligations

Create the canonical destination through the unchanged module lineage, then
perform the explicit seven-table lossless import and incoming-reference
rebinding described above. Retire the old operational tables rather than
retaining a second store. Qualify the exact catalogue/reference graph, fresh
installation and populated handoff. Partial destination contents, unexpected
structure, conflicting ownership and missing RLS/grants refuse. No adopted-table
branch in `lr_0001`, stamping, blanket skip or silent FK removal.

The writer switch stops affected request/background writers, performs the
qualified migration, composes the released package, and removes displaced local
writers. Avoid dual writes. Before new writes, a verified reverse handoff must
restore the prior schema, data, references and code binding under that stop;
after new writes it needs a rehearsed reverse transfer
that preserves accepted records/events or a proved recovery/replay boundary.
An old image alone is not a schema rollback.

Completion evidence must cover the full goal: released artifacts and registry
verification; Academy installed-package execution; fresh install and populated
adoption; actual-role tenant isolation; duplicate/concurrent results and first
creation; override ranking; lab completion; completion regression; certificate
idempotency; stable IDs/references; RLS/grants; rollback; retired local writers;
protected merges and resulting main CI. Publication, a pin or a narrow helper
test proves none of the other obligations by itself. No production deployment
or measured speedup is claimed.
