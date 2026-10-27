# Changelog — dotmac-learning

## 0.1.0a3 — allocated source candidate, not yet published

This successor source is now allocated as `0.1.0a3`: `pyproject.toml`,
`dotmac_learning.__version__` and the module manifest move from the
`0.1.0a2+dev` PEP 440 local development marker (which allocated nothing) to
the bare successor `0.1.0a3`; the published `0.1.0a2` remains immutable and
unchanged. The source below — composable prerequisite conditions `lr_0008`,
composable policies `lr_0009`, versioned question banks `lr_0010` with
SnapshotV6 — merged in PR #923 at `cc4e5120` with hosted exact-head and
resulting-main CI green. Publication remains a separate release dispatch; no
tag, index entry, Academy adoption, backfill or writer retirement is claimed.
Exact installed-pair qualification and Academy adoption remain pending
separate gates. The `0.1.0a2` section below is retained as the historical record
for that candidate; where its counts or semantics conflict with this section,
this section governs the current source.

Current source facts (verified from the live tree): **28 tables and 298
persisted fields** in `mod_learning`, **598 root exports**, migration lineage
`lr_0001_learning` through `lr_0010_question_bank_versions` (single head
`lr_0010`), Kernel floor `>=0.1.0a106` unchanged.

### Composable prerequisites, policies and terms (Waves 0 + 1L)

- Prerequisites are composable tenant data, not hardcoded course lists:
  `CoursePrerequisite` carries a closed `condition_kind` catalog
  (`course_completed`, `certificate_held`, `course_grade_at_least`,
  `activity_passed`, `attempt_number_at_least`), typed-validated `params`, and a
  `group_key`; `Course.prerequisite_composition` is `all|any` across groups with
  ALL within a group. Defaults (`course_completed` / `g0` / `all`) reproduce the
  prior implicit-ALL behaviour exactly, and legacy `requires: []` declarations
  still parse to `course_completed` edges in `g0`/`all`. New exports:
  `PREREQUISITE_CONDITION_KINDS`, `PrerequisiteConditionDeclaration`.
  `unmet_prerequisites` keeps its `list[UUID]` shape as a derived projection of
  unmet course-scoped kinds; `require_course_open` denies on the internal unmet
  facts, so activity-only conditions still lock. Track gating is data
  (`Track.gating_mode`: `none|previous|all_previous`, default `previous`),
  evaluated in the same pass with the prior rule order preserved.
- Learner-visible policies are columns/terms with behaviour-preserving defaults:
  `Course.completion_fraction` (default `1.0`, in `(0,1]`), certificate
  `certificate_auto_issue` (default true) / `certificate_validity_days` (default
  NULL = never) with `Certificate.expires_at`, `Activity.scoring_policy`
  (`best|latest|first`, default `best`), `Activity.is_final` (default false,
  backfilled from the prior heuristic), `Activity.min_attempt_interval_seconds`
  (default `0`, range `0..2592000`), `CourseOffering.pacing_default_open`
  (default true) and `Cohort.auto_accept_below_action` (default `waitlisted`).
- Terms are explicit product-supplied inputs with no owner-side fallback:
  `FeedbackRevealTerms` (default map practice=immediate,
  graded=on_pass_or_exhausted, exam=never; `exam -> immediate` is refused as a
  bank-integrity invariant), `ProgressTrendTerms` (defaults 8/3/0.03),
  `CohortInsightTerms.half_fraction` (default `0.5`), `SuccessTerms`
  `enabled_rules`/`severity_by_rule` (defaults: all five rules enabled, current
  severities), and `CreateSessionCommand.promote_delivery_mode` (pass `True` to
  preserve the prior self-paced promotion). Learning owns no defaults beyond
  schema backfill mirrors; Academy Settings supply creation defaults at the
  composition call sites (Academy work, not in this candidate).

**Prospective-only warnings.** Changing `completion_fraction` writes only the
course row; stored completions change only when `recompute_completion` next runs
on a score write (lowering it can then complete a learner and, at the default,
issue a certificate). `scoring_policy` is read-time: a change affects the next
projection/recompute over existing scores, so `patch_activity`/`update_activity`
refuse a policy *change* on an activity with submissions; the raw
`stage_activity_patch` path is a documented trusted-import bypass of that guard.
Certificate validity applies only to certificates issued after it is set;
already-issued certificates are never rewritten and keep `expires_at` NULL.
The attempt cooldown applies only to admissions evaluated after the interval is
set above zero. A non-default `pacing_default_open` should be dry-run before
relying on it. None of these changes silently rewrites stored history at the
defaults.

### Question-bank system of record and SnapshotV6

- Additive `lr_0010` adds immutable `question_bank_versions`, the bank
  `current_version` pointer, and version pins on questions, activities and
  attempts, backfilling a published v1 per existing bank with a canonical
  content hash and pinning open attempts. The lifecycle is draft
  (create/copy/upsert/delete/discard) -> lint -> publish (lint-clean only,
  optimistic base-current check, never auto-repoints activities) -> explicit
  activity repoint -> retire. Attempts open, grade and read against their
  pinned bank version, with the historical NULL-version fallback retained for
  the frozen-import estate. Learner sittings use the key-free
  `sitting_questions` projection.
- YAML is now a transport format only: import lands **as a draft** (a UI draft
  is never overwritten; an untouched YAML draft is replaced only via the
  explicit replace flag; hash-equal content is a no-op; the document version
  field is ignored; import writes no Activity rows), and export produces the
  audited published document. The legacy destructive writers `load_bank` and
  `import_bank_with_activity` are fail-closed shims that raise
  `LegacyBankWriterRetired` without executing SQL.
- Prerequisite reconciliation now records row origin: it deletes or rewrites
  only `origin=yaml_import` rows and never rewrites app-authored (`origin=ui`)
  rows.
- `LearningSnapshotV6` (`export_snapshot_v6`/`import_snapshot_v6`/
  `verify_snapshot_v6`) is the current full transfer protocol: all 28 tables /
  298 fields, including every Wave 0/1L column, prerequisite origin and the
  bank-version pins. Frozen V1–V5 keep their original 7/14/21/23/27-table
  scopes and row contracts unchanged, and their export/verify paths **refuse**
  (rather than silently drop) when any column they do not carry holds a
  non-default value; default-only data remains transferable through them.

**Historical Git exposure.** Question-bank answer keys previously lived in Git
YAML. That exposure cannot be undone by deleting files, and no renewed secrecy
is claimed for that historical content: treat it as disclosed to anyone who has
had clone/fork access. This candidate records no answer content here; the
remediation is retiring and replacing exposed banks with newly authored items
(high-stakes banks first), not rewriting Git history.

### Breaking constructor/API changes (in-series, exhaustive)

Existing callers must be updated; there are no compatibility shims:

- `reveal_feedback(..., terms=...)` and `progress_overview(...,
  progress_trend=..., feedback_terms=...)` gain required keyword arguments;
  `timeline_for_person` threads the required feedback terms through.
- Required fields: `SuccessTerms.enabled_rules`/`severity_by_rule`,
  `CohortInsightTerms.half_fraction`, `BankActivityTerms`
  `new_activity_scoring_policy`/`new_activity_min_attempt_interval_seconds`,
  `CreateTrackCommand.gating_mode`, `CreateSessionCommand.promote_delivery_mode`
  (strict bool), `ManualImportHeader` four `new_course_*` policy fields, and
  `AdmissionObservation.min_attempt_interval_seconds`/`last_attempt_at`.
- Required `pacing_default_open` on `CreateTrackCommand`,
  `AddTrackCoursesCommand`, `BulkEnrollCommand` and
  `EnsureInvitationEnrollmentCommand`, and as a required keyword on
  `ensure_track_offerings`/`assign_enrollment_track` (written only when an
  offering is created).
- Terms-object tightening: `entitlement()` reads
  `min_attempt_interval_seconds` unconditionally, so a pre-candidate-shaped
  terms object raises `AttributeError` instead of being defaulted.
- `load_bank`/`import_bank_with_activity` raise `LegacyBankWriterRetired`;
  bank writes go through the draft/import-as-draft lifecycle with a trusted
  `BankAuditContext`. Changing an activity's pinned bank (including to `None`)
  through the reference patch/stage paths is refused; use the explicit repoint
  command.
- Additive, non-breaking: all new columns carry server defaults; appended
  defaulted fact fields (`CertificateFact.expires_at`, the `CourseFact`/
  `ActivityFact`/`TrackFact` policy fields, `Entitlement.next_allowed_at`); the
  optional `now` keyword on access reads; legacy `requires: []` parsing.

## 0.1.0a2 — allocated source candidate, not yet published

Current correction, 2026-10-19: `0.1.0a1` was published and remains immutable.
The numbered section below records historical preparation notes; its former
"Unreleased" and publication-pending statements are superseded here.
This successor is now allocated as `0.1.0a2` (formerly the `+dev`
source-qualification marker, which allocated nothing) and retains the
actual published Kernel floor `>=0.1.0a106`. Publication remains a separate
release dispatch; no tag or index entry is claimed by this allocation.

- Preserve the full 27-table/260-field domain and all 520 existing root exports;
  add five reads: course by slug, chapter by number, account cohort names,
  certificate lookup and instructor cohort lookup.
- Require product-supplied admission/bulk/invitation initial-state snapshots and
  a genuine tenant-bound account-presence relation for bulk enrollment; preserve
  existing authority and existing-row facts.
- Add required `SessionUpdate.audit_event_id` and success transition/segment
  message effect receipts carrying actual audit UUIDs from one execution.
- Prepare additive `lr_0007` to remove Enrollment role/status defaults; retain
  all six released migrations unchanged.

Shared composition, independent composed review, hosted source/migration suites
and exact installed-pair qualification remain pending. Academy caller retirement
and adoption are not claimed.

## 0.1.0a1 — Unreleased

- Prepare the Academy-derived seven-table result, progress, completion,
  certificate and event slice, with typed course/activity persistence.
- Preserve source ranking, attempt allocation, final-lab completion,
  certificate reuse and event semantics within the caller transaction.
- Add unit and real-migration PostgreSQL canaries for hosted qualification.
- Prepare caller-transaction snapshot import, current-state export and exact
  storage verification, preserving canonical JSONB text and float8 bits.

- Prepare seven further curriculum/reading tables in `lr_0002`, typed
  authoring/import/bank lifecycle commands and reading/annotation commands.
- Add an additive public surface preserving V1 names and bindings, plus V2
  fourteen-table transfer with 135 persisted fields and pre-insert FK closure.

The result and curriculum/reading source, V2 permission canaries and their public
integration have completed independent source review. The first slice has
matching hosted-generated metadata/lock, and the source PRs await hosted
qualification. The prepared twenty-one-table source still requires its reviewed
integration and hosted qualification. Registry publication, installed-package
execution, lossless Academy cutover/reversal and adoption remain incomplete.
No release artifact or deployment is claimed.

- Prepare participation storage in `lr_0003`, source-preserving roster/audience,
  tracks/access commands and full21/194-field V3 CURRENT transfer. Preserve
  V1/V2 scoped protocols and explicit fresh Enrollment identity translation.
- Port the existing pure exam engine/grader and their characterizations; the
  deterministic option shuffle retains its narrowly documented nonsecret RNG.
  All changes remain unpublished source pending hosted/installed qualification.
