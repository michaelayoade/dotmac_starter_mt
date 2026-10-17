# dotmac-learning

Starter owns this reusable learning domain. The initial slice covers activity
results, best-result projections, course progress, completion, certificate
issuance and learning events, including course/activity reference persistence.
A further source slice adds curriculum lifecycle, question-bank validation and
replacement, chapter/subtopic reading and learner annotations. Enrolment/access,
assessment, delivery and support remain subsequent slices of the full
27-table extraction. Academy is the intended first adopter.

This is prepared source, pending hosted qualification and publication. Academy
has not adopted this module. See `EXTRACTION.toml` for source provenance,
ownership, migration and retirement gates.

The package uses the caller's SQLAlchemy Session and transaction. Commands flush;
the application boundary commits or rolls back. Compose its `module` manifest
and `versions_dir()` lineage, bind the three declared Kernel prerequisites, and
use genuine Kernel tenant, Party and PartyPerson records. The prepared manifest owns fourteen
tables in `mod_learning` and the independent `learning` Alembic branch, beginning
at `lr_0001_learning` and continuing with `lr_0002_curriculum_reading`. It creates no runtime engine or session.

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

Academy retains grading, exam selection, attempt-policy decisions, authorization,
lab execution/operation authority and external-provider adapters. The MCQ path
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
