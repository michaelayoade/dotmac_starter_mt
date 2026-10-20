# Changelog — dotmac-learning

## Unreleased development source — version unallocated

Current correction, 2026-10-19: `0.1.0a1` was published and remains immutable.
The numbered section below records historical preparation notes; its former
"Unreleased" and publication-pending statements are superseded here.
This successor uses `0.1.0a1+dev` solely for source qualification and retains the
actual published Kernel floor `>=0.1.0a106`.

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
