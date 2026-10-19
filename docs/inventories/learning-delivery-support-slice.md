# Delivery, support and remaining learning read ownership

## Retained product bridges still delegate domain writes

Account availability is a separate trusted product observation. Academy's
`Person.status` is explicitly account lifecycle (`active` or `suspended`);
Kernel `Party.is_active` describes identity lifecycle and cannot substitute for
that state. Source account-active population filters in Success Queue and
instructor reads require a tenant-bound nonexecuting `ActiveAccountSelection`
supplied by Academy, composed in the original SQL statement. SuccessQueue filters
membership; instructor projects membership and retains suspended/inactive rows. Source facilitator eligibility receives an explicit account-active
observation alongside the existing instructor-role observation. Check scope
before SQL. Places without the source account filter remain unfiltered; do not
infer account availability from Party activity, credentials or enrollment.
Canaries cover both active-identity/unavailable-account and inactive-identity/
available-account cases independently. These observations do not authorize an
operation or move account lifecycle storage into Learning.

The full 156-app-file/three-script caller census includes adapters as well as
domain service implementations. Retaining a product adapter does not retain a
second learning writer. Academy `lab_lifecycle.grade` keeps live checks, instance
validation and `LabInstance.last_active_at`, but delegates Submission/Score,
learning events and required completion/certificate consequences to
`record_lab_result` with its already-validated `GradedResult`. Template threshold
selection remains with the lab producer; the supplied grade already carries its
fraction and passed decision.

Academy `admissions.enroll_applicant` keeps applicant/onboarding/intake checks,
identity reuse/provisioning and applicant transition. Its Enrollment operation
belongs to the canonical roster owner: look up tenant/cohort/person; when absent,
insert active student with the supplied track and flush; when present, preserve
status and role, change only a differing track and flush; unchanged track adds
no explicit flush. This requires a narrow typed ensure command. `bulk_enroll`
has different reactivation and offering side effects and is not substituted.
No administrator predicate, offering creation, audit or identity writer is added
to this source intent. Product authorization remains at the existing Academy
boundary; Learning uses the caller transaction and existing genuine identity FKs.

Account invitations, CLI bank/curriculum reconciliation, lab Activity import,
the demo seed and direct route mutations must likewise delegate their exact
learning intents. Teaching/account/instructor/learner routes, admission intake
and assessment email, HR/learner digests, search, reports and reminders obtain
learning facts through public APIs. Adapter rendering, authentication and
external-provider ownership do not exempt their learning SQL from retirement.

This audited source plan continues the full Academy extraction. The prepared
assessment slice has 23 tables and 212 persisted fields; four more tables bring
the complete storage scope to 27 tables and 260 fields. No delivery/support code,
hosted acceptance, release or Academy cutover is claimed by this inventory.
The preceding 21-table source is PR888 at
`bfece9cf096670ce6d43c68f1edc079872bdde64`; assessment remains prepared separately.

The accepted source is Academy `0968881fc40ad86b3abfea2fc40044dd2dbf47c9`.
The complete 173-file evidence closure includes all 156 app Python files, all
three scripts, twelve matching tests and the two Academy ownership documents.
Its manifest SHA256 is
`d18f365e4f20649396e33793e721019f131211c16f4d3061f7e03080061d4c01`;
all Git blobs and byte counts verify. Git blob IDs and SHA256 values are separate
fields. The earlier 119-file closure supplies all 62 source migrations, manifest
SHA256 `0c6f477bcdb8477a6e34c6b15809f71f7382c2cc8ce3b43e72c387daa6879a37`.

## Storage and writer disposition

| Table | Fields including timestamps | Required source contract and owner |
|---|---:|---|
| offering_activities | 8 | Composite offering/activity cascading FKs, unique tenant/offering/activity and nullable release/due times. The complete app/scripts search found no production write API. Preserve model, reads and transfer; do not invent a pacing command. |
| class_sessions | 15 | Composite cascading cohort FK, tenant-scoped identity, timetable/instructor indexes and source lengths/defaults. Optional offering/instructor UUIDs remain logical references. scheduling.py owns session create/update/cancel and cohort delivery-mode changes. |
| announcements | 9 | Nullable cohort FK and required author-person FK; translate source person ownership to genuine Kernel Party/profile references. Preserve stored Markdown and rendered HTML. announcements.py owns create/delete and visibility; there is no update command. |
| success_queue_entries | 16 | Required person FK; nullable cohort/assignee/resolver UUIDs remain unconstrained logical references. success_queue.py owns sweep/upsert/auto-resolution and transitions. |

Migrations, not ORM drift, determine storage. Source 0046 creates partial unique
`uq_success_queue_open_person_reason` on tenant/person/reason WHERE status='open',
and server defaults for supporting_facts, severity, recommended_action, status
and score_hint. Acknowledged duplicates remain legal. Sweep is not generally
concurrency-safe merely because this index exists. Source 0026's cohort
delivery_mode field is already in the participation slice; do not add it twice.
Follow Enrollment/Event's accepted identity translation: tenant-safe Party FK plus
genuine PartyPerson existence FK for announcement author and success person. This
is not literal legacy People DDL parity and does not prove Academy identity
adoption; stable identity mapping remains an activation gate. Logical session
instructor and queue assignee/resolver references remain unconstrained.
All four tables retain tenant-root references, source identity/timestamps,
forced tenant RLS, grants and actual historical values. Do not invent identity,
status, role or positivity constraints.

Add bounded delivery_models.py and an additive storage revision after the finally
integrated Learning head. Kernel tenant-audit verification has its separate
revision and published-provider ordering; do not collide with or fake that
lineage. V5 transfer extends preflight to all 27 tables/260 fields, full empty
destination/catalog qualification, actual FK/unique closure before the first
insert, and independent current fields/keys/deletes/outside-extent readback.
Reuse Float8Bits for score_hint and JsonbText for unrestricted supporting_facts.
Preserve V1/V2/V3/V4 scopes, rendered HTML and resolved history. Logical UUIDs are
not invented FK closure obligations. No transaction outcomes, retries or hidden
repair inside transfer. Storage equality alone does not prove product reversal.

## Commands, audit and delivery boundaries

Reuse the existing prepared cohort creation/removal owners, attempt policy,
assessment reads, roster/access, events and progress. Cohort creation still stages
without flush. Product authorization supplies trusted tenant/actor observations
separately from client payloads. Source instructor routes use the global
instructor guard; support accepts instructor or admin. Do not invent cohort
assignment authorization. Instructor course-authoring eligibility uses the source
admin observation and active instructor-enrollment/offering queries.

Scheduling retains validation/error order, unset-versus-clear updates, inclusive
join boundaries, self-paced-to-blended promotion and learner cohort membership.
Source update mutates fields before validating the resulting interval; failure
requires caller rollback. Only changed session updates have source audit behavior.
Join eligibility is a domain policy; session/lab providers stay in Academy.

Announcement creation receives Academy-rendered Markdown/HTML, adds and flushes,
selects the source audience, invokes an explicitly supplied fanout adapter inside
the source optional savepoint, catches delivery failure, then writes REQUIRED
Kernel audit in the caller Session. Audience-query and audit failures propagate.
The initial flush stays before the savepoint; no success claim after partial
required failure. Targeted fanout selects active students, while tenant-wide
fanout selects active enrollment of any role; tenant-wide visibility includes
people without enrollment. Delete of a missing row is an unaudited no-op.

Success queue owns all five rule computations, sweep/upsert/auto-resolution,
transition predicates, severity order, segment membership and message-command
sequencing. Source setting coercion/defaults become explicit typed terms supplied
by Academy. Sweep does not audit despite its source docstring. Assignment remains
allowed on resolved entries; acknowledge/resolve retain narrower predicates.
Success transition writes required audit before its final explicit flush.
Scheduling update flushes first, then audits only when changes exist.

Use two narrow callable protocols, with no provider registry/framework:
AnnouncementFanout(Session, announcement_fact, recipient_ids) delegates the
notification operation after Learning selects audience and owns the optional
failure/savepoint boundary. SegmentDelivery(Session, recipient_fact, message_fact,
action_id) delegates the existing notification-to-email-outbox pair. Learning
validates subject/body, selects membership, creates the action ID, invokes each
recipient sequentially, then performs required audit and flush. Segment delivery
has NO catch or savepoint; its failures propagate. Preserve counts, idempotency
keys and audit even for zero recipients. Both adapters are explicit wiring.

Audit uses the actual Kernel writer, trusted explicit actor/source context and
optional Party enrichment, without callback/fallback audit ownership. In addition
to attempt_granted, declare announcement.created, scheduling.update_session,
success_queue.acknowledge, success_queue.assign, success_queue.resolve and
success_queue.message_segment. Their tenant storage prerequisite follows the
Kernel-first publication and consumer-conversion plan.

## Remaining domain reads required for a thin Academy

| Learning owner | Domain computation and selection to extract |
|---|---|
| pacing | Applicable overrides and readable/submittable decisions. |
| agenda and todo | Candidate/time selection, chronological order/limits, deadline buckets, recent-feedback eligibility and course windows; supplied timezone data for local-day computation. |
| gradebook, reports and analytics | Weighted/attempted grades, cohort matrix, transcript selection and item analysis. |
| insights | Activity/velocity, trends, discipline strengths, blockers, funnel/work states and comparison. |
| learner_dashboard | Offering/effective-deadline choice, touch/resume/continue, card states/semantic actions, next activity/deadline/session, progress and feedback. |
| instructor_reads | Cohort/workspace/roster/facilitator facts, course/track choices, active counts, progress/risk and authorable-course eligibility. |
| support_reads | At-risk course calculations, learning CSV/admin-report aggregates, weekly learner activity and reminder-source facts. |
| dashboards | Legacy cohort_overview missing-completion-as-zero and course-scoped score inactivity, distinct from insights.cohort_overview. |
| catalog_reads | catalog_course_by_slug and catalog_chapter_by_number are indexed tenant/course-bound fact reads without authorization or commit; my_courses, all_courses, course_completion and course_structure; prepared curriculum only ports public_catalog. Continue selection uses the first activity per chapter, unlike dashboard resume. |
| search | Source staff/access eligibility, SQL matching and per-category caps; Academy renders snippets. |
| product-support learning facts | Public facts replace Event/Completion/Enrollment/Score SQL in hr_digest.snapshot, reengagement.stranded_learners and email._queue_first_pass_intents. Authentication availability, stranded-account recipients, opt-outs and email delivery remain Academy. |

Use detached frozen facts, ordered tuples, numbers, timestamps, reason/state codes
and semantic navigation targets. Academy converts them to URLs, labels and
presentation dictionaries. Port existing algorithms; do not create a generic
query layer or silently replace rankings with a shared algorithm.

Characterize material source differences: agenda can select another cohort's
offering for an accessible course; todo deadlines require active STUDENT
membership while pacing accepts any active role. Todo removes an activity after
any passing score and limits recent scores before filtering feedback; its
feedback omits grants while dashboard progress includes them. Dashboard cards use
effective access extensions, progress deadlines fall back to raw offering end.
Insight and dashboard trends use different windows/thresholds. Transcript and
item-analysis best-score policies differ from assessment ranking. Inactive people
are excluded from sweep but their old entries are not auto-resolved merely for
leaving its population. Retain first-match/tie behavior and unspecified SQL ties.

Academy keeps Markdown/HTML/email/CSV/iCalendar rendering, token custody, HTTP
parsing/time localization, route authorization, templates, URLs, notification and
email-outbox writers, recipient preferences, reminder frequency/quiet hours/dedup/
nudge caps, scheduler execution, labs, admissions and ERP/provider adapters.
Mixed services split deliberately: at-risk computation moves but delivery stays;
learner_digest.week_activity moves but email recipients/rendering stay;
admin_reports learning aggregates move but admissions/admin-recipient metrics and
email composition stay; CSV aggregate computation moves but serialization stays;
ReminderLog and reminder occurrence/delivery policy retain their Academy owner.

After qualified adoption Academy domain paths must contain no SQL or duplicate
learning computation against these 27 tables. Explicit assembly FK declarations
and fenced adoption/migration adapters retain their separate ownership. Replacing
model imports alone does not establish thinness or single-writer retirement.
Before claiming exhaustive retirement, check every app/scripts caller against
its explicit domain-versus-adapter disposition; the service list is not a
substitute for that source and adoption census.

## Composable enrollment reads for coupled recipient selection

The planned public contract is a narrow nonexecuting
`audience.active_student_person_ids_select` relation: one distinct UUID column, required tenant, active student enrollment,
and optional cohort. It accepts no Session, account/authentication expression,
request-supplied SQL or authorization token. After qualified publication, Academy
services may embed this relation in their existing account/authentication recipient
statement;
Learning retains every enrollment table reference and predicate. Composition
adds no session, isolation change, eager materialization or delivery policy.

At accepted source 0968881, learner digest, reminder sweep and stranded-learner
selection couple account and enrollment observations in one SQL statement.
Preserve their distinct/unique materialization, optional cohort, unspecified or
email ordering, historical credential/session exclusions and exact write points.
HR digest intentionally uses separate audience/count/auth/window/name queries;
do not combine them. Frozen account-ID observations do not prove statement
snapshot parity. SuccessQueue and instructor coupled selections still require
separate source-preserving composition proof; this enrollment relation alone
cannot qualify their cutovers.

Characterize the three actual installed Academy selectors before replacing them.
Hosted proof must cover a single composed SELECT, active/suspended account status
independent of identity activity, duplicate enrollments, role/status/cohort scope,
non-bypass tenant isolation, credential-only and expired-session exclusions,
caller autoflush and subsequent required-write rollback. A real concurrent atomic
account/enrollment transition must expose the two-read hybrid and exclude it from
the composed statement. Module relation tests alone are not adoption evidence.

## Coupled account selection for support and instructor reads

The prepared `account_contracts.ActiveAccountSelection` replaces the unpublished
materialized account population contract in the five coupled entry points:
SuccessQueue sweep, segment population and segment messaging, plus instructor
cohort summaries and workspaces. It carries only a tenant UUID and a nonexecuting
one-column `Select[tuple[UUID]]` from the trusted product account lifecycle owner.
The product builds its explicit active-account predicate; Learning checks the
wrapper tenant before SQL and embeds the relation in the original statement.
It is not request SQL, a credential, authorization token, callback framework,
Session owner or cached population. No materialized fallback, locking, retry or
isolation change is added.

SuccessQueue applies membership in the original enrollment/person WHERE clause.
Instructor instead projects membership as a boolean in its two student roster
statements: suspended accounts and inactive enrollments remain visible, while
current/risk/count/progress eligibility uses the projected value. Facilitator
roster reads retain the original three-entity query and no account filter.
Preserve source enrollment multiplicity, SuccessQueue surname-only ordering,
instructor surname/first-name/email ordering, pagination, query sequence and
ordinary caller autoflush. Segment messaging passes the same
selection through its required delivery and audit sequence.

A UUID relation alone does not prove complete correspondence between legacy
Person accounts and genuine Kernel Party/PartyPerson rows, stable IDs, tenant
consistency or authoritative names/email. These remain installed Academy identity
adoption prerequisites. A Starter-only account table may characterize relation
composition and a real concurrent atomic account/enrollment transition; it is
not evidence of Academy account migration or adoption. Never use Party.is_active
as a surrogate for account active/suspended state.

Characterize source one-statement snapshot behavior before changing the callers:
active account plus dropped enrollment transitions atomically to suspended
account plus active enrollment. Neither legitimate state qualifies; a split
population/roster read can produce a hybrid. Hosted PostgreSQL must expose that
old hybrid and exclude it from the composed SuccessQueue selection/instructor
count, while retaining the instructor row. Capture the actual account predicate
in the roster SELECT, restricted-role tenant isolation, missing/foreign scopes,
identity/account cross states, duplicate cohorts, caller autoflush and required
writer rollback. Actual installed Academy selectors and identity correspondence
still require independent qualification.

Facilitator assignment keeps scoped `FacilitatorEligibility`: its source reads
cohort, Person/status, roles and enrollment separately. Do not invent an atomic
account/role/enrollment guarantee or change its isolation. Preserve admin-first,
cohort/person/account/role refusal precedence and trusted product observation
ordering on the original caller Session.

## Remaining source writer intents

The accepted source closure includes `app/cli.py`; scripts alone are not a
complete writer census. `_load_banks` (189–276) delegates bank loading then
upserts Activity by tenant/course/bank, preserves existing fields except title
and threshold, and applies question pool, maximum attempts and assessment mode
only when those policy keys are present. `bank_loader.load_bank` itself does not
own Activity policy. A separate import intent and provided-field Activity update
must preserve that boundary and omitted-versus-explicit-null meaning. Existing
full replacement reference APIs retain their meaning.

`_load_curriculum` (1048–1138) is the canonical prerequisite writer. Planned
Learning reconciliation owns tenant course lookup, source slug resolution,
self/cycle/missing validation before mutations, all declared edge additions and
stale-edge deletion only where the dependent course matches the supplied
discipline. Declared cross-discipline edges are not silently scoped away. Unknown
course names are checked only while traversing declared requirements, preserving
source empty-requirements behavior. Duplicate declarations collapse into a set.
Academy retains YAML parsing, CLI messages and dry-run/commit/rollback boundaries;
Learning stages the diff without completing or flushing the transaction.

`account_invitations._apply_assignment` (67–138) needs one module-owned enrollment
assignment intent. Preserve new member role mapping, unconditional protection
against instructor-to-student downgrade, existing canonical admin-gated
reactivation, optional track assignment and its offering ensure, then selected
course offering create/reactivation in source order. Product-resolved cohort,
track, course and person identity and account/auth/invitation lifecycle remain
Academy-owned. Return role/reactivation observations for product descriptions;
do not move invitation issuance, rendering or account eligibility into Learning.
Do not add unconditional flushes, audits, savepoints or concurrency guarantees.

`lab_content.import_labs` (190–270) retains file decoding, rendering, source hash,
LabTemplate storage/versioning and topology/provider authority in Academy. Only
its Activity create and selected-field update delegate to Learning; a missing
existing Activity remains source-tolerated. Demo seed selected-field changes and
course-status changes likewise must not reset omitted reference fields. Narrow
staged patches require exact tenant/id scope and no source transaction completion.
No LabTemplate model, storage or provider import API is added to Learning.

Characterize these intents before implementation, including populated non-bypass
tenant isolation, omitted/null updates, role preservation/reactivation refusals,
source autoflush and offering order, prerequisite pre-mutation refusal and scoped
deletes. Hosted execution, actual CLI/invitation/lab adapter adoption and full
writer retirement remain required after source preparation.

## Installed package floor proof preparation

The existing hosted `kernel-floors` lane remains the execution owner. Preserve
legacy manifest probing, add a Learning-only `registry` mode, and a closed
`tenant-audit` manifest/extra-import mode for Fulfillment and Template Studio.
Fulfillment declares a real logical durable-timer dependency; its API floor probe
does not claim standalone registration or remove that dependency. Do not enroll
any of the three until a sufficient Kernel is actually published and its literal
floor and matching lock are set together. Unknown modes, unsupported consumers
or malformed entries must refuse.

Build each module wheel once and install that exact wheel in both isolated
Python environments. Use `python -I`, require loaded package origins inside each
venv and outside the checkout, exact selected metadata/runtime versions, and
retain module/Kernel wheel hashes and peeled tag commits. Derive predecessor from
a complete published tag set, fail closed on missing evidence, and cache Kernel
wheel builds across entries without relying on shell subshell state. Tagged-source
compatibility is distinct from registry wheel identity; retain the separate
publication/install-back byte verification.

For Learning, the sufficient environment must construct the actual default
`ModuleRegistry((module,))`, verify all current 27 declared tables and the genuine
shipped namespace allocation. The immediately previous published Kernel must
refuse the unchanged Learning wheel only for its exact missing public tenant-audit
API. Independently inspect the installed shipped ledger for allocation absence or
presence; do not infer namespace absence from ImportError or substitute a manifest,
ledger or registry. Published-source floors and immutable migration successors for
all three audit consumers remain gated by actual Kernel publication.

Collect Kernel/namespace provenance before importing any target package. Classify
target package, manifest and declared extras together, since an eager package
initializer can itself encounter the missing audit API. The `tenant-audit` mode
proves installed API compatibility only; it does not compose a fake provider or
claim a dependency-incomplete ModuleRegistry succeeded.

Hosted canaries must expose unknown modes, broad/wrong-symbol refusal, package
origin/version mismatches, absent tag evidence, changed module wheel identity and
omitted registration/catalog checks. Static source review is not floor execution
or publication proof. No numeric version, floor, artifact or release enrollment
is allocated by this preparation.

## Qualification and adoption gates

Write characterization and actual non-bypass tenant-isolation canaries first.
The supplied source tests are incomplete for dashboard resume/selection, distinct
trends/rankings, all support rules/transitions, exact time boundaries, partial
uniqueness and historical JSON/float representations. Announcement source tests
also lack partial fanout rollback followed by successful required audit; the old
scheduling isolation test uses an administrator and is not an app-role RLS proof.
Add actual audit/registry failures and caller-transaction continuity for both
callback paths. All execution is hosted CI; local work is static only.

Implement storage/V5, scheduling/pacing/announcements, grade/read foundations,
dashboard/insights and support, then replace Academy domain callers with released
public APIs. Independent review and exact head/base hosted checks precede merges;
verify resulting main. Kernel effect publication/floor proof, module release and
installed-package execution remain separate gates. Stable identity, incoming
references, lab worker ACL/RLS, historical ERP ACK provenance, writer fences and
compatible reversal preserving accepted new facts/deletes precede activation and
retirement. No second consumer, forced Rust rewrite, deployment or speedup claim.


Academy certificate outbox attachment reads use public `get_course` and
`get_certificate(db, tenant_id=..., person_id=..., course_id=...)`. The latter
returns the existing detached `CertificateFact` or None, with no completion
eligibility check, issuance, lock, or commit. Academy owns historical recipient
contact projection and PDF rendering/delivery. Source only; hosted tests not run,
future Learning and Kernel dependencies UNALLOCATED/UNRELEASED.
