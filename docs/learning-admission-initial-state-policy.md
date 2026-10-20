# Admission enrollment initial-state policy

This source-only API intentionally requires a product policy snapshot:

```python
from dotmac_learning import AdmissionEnrollmentInitialState, ensure_admission_enrollment

initial_state = AdmissionEnrollmentInitialState(
    role_in_cohort=configured_role,
    status=configured_status,
)
fact = ensure_admission_enrollment(db, command, initial_state=initial_state)
```

`AdmissionEnrollmentInitialState` is frozen. Both fields are required strict
builtin strings, with at most 20 Unicode characters to match the unconstrained
`String(20)` columns. Empty, whitespace and custom values remain valid and are
preserved verbatim. There is no default, fallback, normalization or invented enum.
An explicitly configured `student`/`active` snapshot preserves prior default
behavior; a configured `instructor`/`dropped` snapshot creates those actual facts.
The snapshot is neither authorization nor instructor eligibility evidence and
must be resolved by trusted product composition, never a browser-selected role.

The existing `EnsureAdmissionEnrollmentCommand` continues to carry intent only.
The required keyword snapshot supplies only new-row role/status. Existing rows
retain role, status, audience, employee reference and access metadata; only a
changed track is assigned, with the existing explicit flush. An unchanged track
has no explicit flush; caller query autoflush and transaction boundaries remain
unchanged. Missing or malformed snapshots are refused even for an existing row.
Tenant isolation, genuine Kernel PartyPerson foreign keys and caller rollback
responsibility remain unchanged. This is one writer, not a second enrollment API.

This breaks the former call signature and requires a new future Learning release
and a coordinated Academy consumer port. Released a1 is immutable and must never
be republished. Version allocation, generated public catalog regeneration and
installed assembly qualification belong to the release coordinator. No generated
catalog, lock, release authority, package version or released migration is edited
by this source increment.

The closed 247-file owner capsule contains actual calls only in
`tests/test_learning_admission_enrollment.py`, now ported explicitly. The unowned
`tests/unit/test_learning_delivery_public_surface.py` contains an owner identity
reference, not a call. `docs/ARCHITECTURE.md` also contains a descriptive reference.
The real Academy caller is ported separately in its own source capsule.

Hosted tests are authored for required/frozen/strict policy, explicit default and
configured instructor/dropped creation, existing active/waitlisted/dropped/inactive
student/instructor metadata preservation, unchanged-track autoflush, non-bypass
tenant isolation, genuine person/composite foreign keys and caller root/nested
rollback. They have not been executed locally. Runtime/import/test/type/SQL/DB,
build/install, network shell and Docker qualification remain hosted CI only.

## Historical admission-increment checkpoint

The following paragraph records the earlier admission-only increment. Its source
work inventory is superseded by the dated current statement below.

This admission increment does not close the settings-only defaults goal. Remaining
work includes prospective retirement of Enrollment ORM/server defaults with a new
migration preserving existing rows (never edit released `lr_0003`), explicit bulk
and invitation initial-state policy, and a complete fixture/constructor census.
Facilitator instructor/activation and explicit activation-command effects retain
their guarded semantics. Full writer retirement, hosted release/installed assembly
qualification, remaining product business-policy census and ERP integration
acknowledgment recovery remain owed.

## Current source statement — 2026-10-19

The complete Learning255 continuation now source-prepares required bulk and
invitation snapshots, genuine tenant-bound bulk account presence, the complete
base constructor census, and additive `lr_0007` removal of the two Enrollment
ORM/server defaults. Released migrations and existing accepted enrollment facts
are preserved. This port declares `0.1.0a1+dev` for source qualification; published
a1 is immutable and no numeric successor is allocated. Shared composition,
independent composed review, hosted suites and the exact installed Kernel/Learning
pair remain unqualified. Settings-owned product policy, full Academy caller
retirement and ERP integration/recovery remain owed.
