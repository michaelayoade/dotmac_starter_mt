# Learning participation source boundary

Observed 2026-10-17 at immutable Academy
`0968881fc40ad86b3abfea2fc40044dd2dbf47c9`. This is a source audit and planned
port contract, not qualification, publication, installed adoption or cutover.
The full learning extraction remains 27 tables. This slice adds seven to the
fourteen prepared result/curriculum/reading tables in the same learning lineage.

The four source model files are `app/models/cohort.py`, `track.py`, `offering.py`
and `prerequisite.py`. All 62 accepted Academy migration files were inspected.
The selected tables are cohorts, enrollments, tracks, track_courses,
cohort_tracks, course_offerings and course_prerequisites: 45 declared fields
including IDs plus fourteen timestamps, or 59 persisted fields. Every table
retains tenant RLS, FORCE RLS and application/platform CRUD grants.

Preserve the actual uniqueness, lengths, defaults, nullable fields and composite
tenant references. Enrollment's nullable cohort/track membership FK has default
NO ACTION and MATCH SIMPLE semantics. Cohort entrance bank is a logical UUID;
no new FK or threshold/status/order check is implied. Audience remains nullable,
is limited to staff/external when present, and staff requires an employee_ref.
The persisted enrollment indexes are `(tenant_id, audience)` and
`(tenant_id, access_ends_at)`, following migrations 0049/0050 rather than the
incomplete ORM index declarations. Migration 0036's legacy track/offerings data
backfill belongs to Academy adoption; fresh module DDL must not rewrite product
tables or manufacture replacement legacy records.

Academy's actual enrollment-person FK targets legacy
`public.people(tenant_id, id)`. Fresh module storage will bind the same learner
UUID meaning to genuine Kernel `public.parties(tenant_id, id)` plus
`public.party_persons(party_id)`, using the existing Kernel tenancy, Party and
PartyPerson prerequisites before DDL, as the learning event owner already does.
This is an explicit identity reference translation, not literal source DDL
parity or evidence that Academy identity adoption is complete. No local People
table, facade, fabricated profile or employee_ref substitution is allowed.
Academy cutover still requires qualified stable-UUID identity mapping and the
existing identity adoption programme; source-only fixtures prove no such cutover.

Reusable behavior includes roster activation/transitions, track membership and
dense course ordering, offering creation, explicit audience classification,
prerequisites and entitlement/window decisions. Source null-track legacy access,
locked-course visibility, selected-track restrictions and per-enrollment end
extension must be preserved. Do not silently introduce Track/CohortTrack status
checks absent from entitlement queries. Directory resolution and authorized
caller identity remain Kernel/product boundaries. Admissions supplies an
approved learner/cohort/track command; screening, applicant state, entrance
policy, credentials, role/invitation handling and provider calls remain adapters.
ERP employee/requirements/credential ownership and lossless ACK handoff remain
separate; no cross-application database reads are introduced.

Incoming references from applicant cohort-track, class-session/announcement
cohort and offering-activity offering rows require Academy assembly rebinding
under the writer fence after transfer. Logical applicant cohort/person,
class-session offering/instructor and lab course/activity UUIDs gain no invented
FK. Fresh module migrations do not reach those product tables. Services use the
existing caller transaction, never its commit/rollback authority. An eventual
full-slice transfer must preserve all 21 tables/194 fields and accepted current
facts with independent readback; V1/V2 remain scoped compatibility APIs, not
full participation cutover proofs.

Source characterizations include roster, tracks, audience_hr, entitlements,
prerequisites, cohort isolation, admissions enrollment/isolation and the existing
attempt-policy reader tests. The existing pure exam/grading engine is ported
without replacement alongside this work; sitting/grant state and attempt policy
remain a subsequent coherent port. No successor version is allocated here and
no released version may be reissued.

## Prepared source continuation (2026-10-17)

The seven storage models/lr_0003 and roster/audience/tracks/access commands are
prepared with typed commands, detached facts and trusted scoped product authority.
Full21 V3 transfer and its hosted canaries are authored; independent review and
hosted qualification must precede acceptance. The original 97-file immutable
source manifest remains unchanged. Supplemental immutable helpers are
`app/services/lookups.py` (Git blob `6681f3ffe5265bda6f4ea38d3aba12d2c299a1f5`)
and `app/services/identity.py` (`d6e368e4591c6483e7c62d138501a66d807b25c6`),
both at accepted Academy `0968881fc40ad86b3abfea2fc40044dd2dbf47c9`.
They confirm exact scoped lookup errors and trim/lower/btrim email behavior.

This is not complete thin-product adoption. Direct cohort creation and facilitator
removal in `app/web/instructor.py`, plus remaining product domain reads/writers,
still require extraction/adapters and retirement proof. Existing exam/grading
ports do not substitute for the remaining attempt-policy/persistence, delivery
and support slices of the full27 goal. No source-only verification establishes
legacy identity adoption, ERP ACK handoff, actual lab-worker grants, reference
rebinding, writer fences or compatible reverse transfer of accepted new facts.
