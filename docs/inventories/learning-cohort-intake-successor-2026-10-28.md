# Learning cohort intake: 0.1.0a4 source successor

Prepared 2026-10-28. **Source allocation candidate only; not published,
installable, adopted or deployed.** Package metadata, public version export,
ModuleManifest and the workspace directory-package lock row declare
`0.1.0a4`. This document accompanies the additive cohort intake owner API.

## Immutable source and publication facts

Source base: `michaelayoade/dotmac_starter_mt` protected main
`e9c1be690cfd58c64ae75a07a9a58d46d18743ba`. Every Starter baseline and
catalog-generator input was fetched with the GitHub connector at that exact
revision; the user's edited Rust checkout is not a source.

Learning a3 is positively released, immutable, and remains available through
its own evidence. The protected-main
[module release ledger](module-release-verifications.json) records:

- tag `dotmac-learning-v0.1.0a3`;
- annotated tag object `6ea5de47f07c83f03bc5ebdbb67ef9cc15c4a664`;
- peeled source commit `7f8f1e6646e531154450370e51dbede46723cc60`;
- verified release/source run `40484794529`;
- wheel `dotmac_learning-0.1.0a3-py3-none-any.whl`, SHA256
  `11a0393f7b43dc51618b1ff51fc90cfa1fda39da4c53bd75a22d08ae07428d0c`;
- smoke dependency Kernel a107, wheel SHA256
  `4a2aed86e4feb4b6060f956d0d2b1ba9dee0cd66da033cb96d03a35cb89b62fc`.

The old allocation snapshot `88688911` and allocation-era unpublished prose
are historical; neither negates this release. No a3 tag, artifact, receipt,
release ledger row or migration is changed here.

The release coordinator observed only a1/a2/a3 matching Learning tags, with
no a4 tag, as of **2026-10-28T20:39:02Z**. This absence is time-bounded;
the release coordinator owns its refresh before integration/publication.
The new a4 unpublished row records an allocated source state, not a predicted
artifact. No a4 wheel hash, release run or release verification row is invented.

## Product-first inventory and typed writer claims

| Product | Immutable evidence | Scope and result |
| --- | --- | --- |
| Academy | `bde50c940192714cdb0608652e94417199565f64`, `app/cli.py::_set_entrance_bank` lines 614–633 and `::_set_auto_accept` lines 688–706; CLI file SHA256 `8603322ae78095b2824404075343383c640f45b42074823c1be56875a1719138` | Existing assignments are the behavior source and concrete demand. Both remain product writers until installed released-owner adoption proves their retirement. The package's effective Academy revalidation and typed qualifying-source row advance together; historical source_revisions stay unchanged. |
| ERP | Prior full-scope audit `537f0ccba846c1ea3304805ed17a4d3b9711596b`; new bounded local intake inspection at `153cc04a9e08fd7936f7e91d37e694ad932bead3` | No entrance-bank/threshold/Learning-import matches over app/tests/pyproject. Read existing training assessment models/services and Academy training_sync integration. No matching intake setter or second consumer found. Preserve the existing full-scope legacy_writer row at its historical audit pin; a bounded negative search is not a whole-domain re-audit or legacy-writer retirement. |
| Sub | Prior full-scope audit `13afd67e43ec946e5e25564628806ce4ddd7441c`; new bounded local intake inspection at `c7707880232c9298ef89c24a1aea66d35bd3a1e6` | Same scoped search found no matching intake writer; broader Cohort names refer to notification/migration/subscription concepts. Preserve the prior full-scope no_writer row at its existing audit pin. Runtime demand and unseen branches remain unknown. |

No matching CLI-specific tests were found in Academy's scoped test search.
The behavior is ported from its two concrete canonical assignments, with new
cross-tenant-first canaries. The shared owner is an extension of the existing
audited Learning package. There is no second assessment engine, new module,
parallel writer or product-specific branch.

The package dossier retains exactly one typed row per external inventoried
product. Its status remains `audit-complete`, contract_consumers remains empty,
candidate_consumers remains Academy alone, and adoption_evidence remains empty.
A new pin alone will not establish adoption.

## Contract, policy and compatibility

Add `CohortIntakePatch(ParticipationCommand)` and
`patch_cohort_intake(Session, patch) -> None`, exported at the package root.
The existing ParticipationCommand owns tenant UUID parsing and extra-field
refusal. Require tenant_id and cohort_id. Accept only these nullable fields:

| Field | Storage meaning | Omitted | Explicit None |
| --- | --- | --- | --- |
| entrance_bank_id | Logical bank UUID, not a published-version UUID | Preserve | Clear |
| entrance_time_limit_minutes | Integer minutes | Preserve | Clear |
| auto_accept_threshold | Float fraction supplied after product policy validation | Preserve | Clear |

`model_fields_set` and `model_dump(exclude_unset=True)` preserve selected-field
intent. No status, delivery mode, name, identity, below-threshold action, unit
conversion or default policy is added. An empty patch checks scoped existence
and changes nothing.

A normal tenant+cohort SELECT refuses missing or foreign-tenant rows with
`ParticipationNotFound("cohort not found for tenant")`. Before assigning any
selected fields, a selected nonnull bank UUID must resolve to QuestionBank.id
in that same tenant, otherwise the owner raises
`ParticipationNotFound("entrance bank not found for tenant")`. This is an
intentional safety tightening over legacy raw UUID assignment, with identical
missing/foreign refusal detail. No bank version/state/suitability or automatic
publish/repoint decision is introduced.

Cohort.entrance_bank_id has **no physical FK**. The lookup proves existence at
that statement's snapshot, not FK-level referential integrity or deletion-race
protection. No row lock or DDL is added; stronger concurrent reference policy
requires separate review.

The owner uses existing `_transaction` / `_unchanged` guards. It requires an
already active caller root/savepoint and performs no explicit flush, commit,
rollback, savepoint or autobegin. Standard SELECT autoflush remains: invalid
pending caller state may flush/fail before the lookup; errors propagate for
caller rollback. Stage-only means no ending flush, not SQL-free execution.
No no_autoflush or populate_existing option changes source identity-map policy.

Academy keeps trusted offline operator authorization, UUID lookup, tenant
selection, bank suitability, threshold [0,1] validation, messages and commits.
The DTO is not authorization. Library numeric inputs are deliberately not
normalized or constrained by new product policy.

Caller retirement mapping after a qualified a4 release is installed:

- `_set_entrance_bank`: always supply bank UUID. Omit minutes when the CLI
  option is absent; when supplied, pass `minutes or None`. Zero clears,
  nonzero integers retain existing meaning. The source parser help says
  omission is untimed, but actual code preserves a prior value on omission;
  this candidate preserves actual code.
- `_set_auto_accept`: preserve product range validation, then always supply
  threshold, including explicit None to disable auto-accept.
- Retain required product reads for tenant resolution/messages, replace both
  canonical assignments with owner calls, and prove installed per-caller
  behavior before declaring those writers retired.

## Source allocation and package coherence

The public root grows from 598 to **600** exports; existing owner bindings and
all existing APIs retain their implementation. Only the new candidate copy's
`__version__` literal differs from the independently reviewed root file;
the frozen reviewed five-file directory is preserved byte-for-byte.
The reviewed cohort functions and contracts are unchanged. The hosted test has
one import-group blank line corrected for the actual repository layout; both
test files retain AST equality with the frozen review. No test behavior changes.

Storage remains **28 tables / 298 fields**, `mod_learning`, code/short_code
`learning`, migration prefix `lr`, branch `learning`, single head
`lr_0010_question_bank_versions`. No Module UID/allocation, Kernel namespace
ledger/digest, prerequisite or migration byte changes. Kernel floor remains
`>=0.1.0a106`; this API consumes no new Kernel facility.

The workspace `poetry.lock` candidate changes only Learning's directory-package
version literal a3 to a4. Dependencies, Python range, root pyproject and root
content-hash are unchanged. This is a proposed metadata edit, **not output of
a local Poetry resolution**. Hosted pinned Poetry 2.4.1 regeneration/check and
the path-package lock guard must validate the result; any differing producer
output is reviewed before integration.

The installed-pair verifier's exact export extent and receipt move 598 to 600,
with the corresponding synthetic owner fixture extent. Its binding, tenancy,
migration, data and installed-provenance checks are preserved. The public-owner
contract test adds both new owner identities. New canaries are authored and
unexecuted locally.

The module is already release-allowlisted; no allowlist or release authority
input changes. No fresh release-authority digest, artifact receipt or module
release verification is fabricated. Connector-only released-manifest-digests
does not apply to this ModuleManifest; its historical ledger remains unchanged.
The separate old inventory-draft dossier remains historical; package
EXTRACTION.toml is the current owner record.

## Hosted acceptance and later release path

All local validation here is static: Python AST, TOML/JSON structure, exact blob
and SHA256 comparisons, pinned Ruff 0.6.9, and authorized static Markdown catalog
generation. No local tests, repo test/guard scripts, dependency installation,
Docker, package build, remote branch/commit/PR, workflow dispatch or publication.

Before a source PR can merge: independent metadata/support review; complete
required hosted checks for its synthetic merge head/base (quality, unit,
PostgreSQL integration, installed Learning pair, floor proof, allocation and
release-provenance/append-only checks, engineering standards and all other
required contexts). Rerun when head or base changes. The new canaries must
actually execute, not merely collect or skip. Source-static results establish
none of these runtime facts.

After source integration, resulting protected main must be truthful and green.
An explicitly authorized release uses **.github/workflows/release-module.yml**,
on the exact then-current protected main, with inputs:

- `module=dotmac-learning`
- `version=0.1.0a4`
- `compose_kernel`: empty by default; when composition is selected, an exact
  already-published Kernel version such as the consumer's verified a107.
- `compose_with`: empty by default; if required for an actual consumer set,
  only its already-published exact sibling pins, with compose_kernel required.
  No sibling set is invented by this candidate.

`release-facility.yml` is scoped to universal facilities and allowlists only
Deployment Foundation; it is not Learning's publication path. Its
CandidateArtifact/Disposition mechanics are not transplanted into module
records. The immutable-version rule still requires a new identity here.

The release captain refreshes tags/main and holds the named merge freeze.
The module workflow resolves the existing allowlist and exact version, builds
once, inspects and smokes artifacts, qualifies the same bytes against published
dependency wheels, reasserts current main immediately before registry upload,
installs back/compares published bytes, registers the package and requested
composition, then tags. Its generated oracle-backed release-record PR must
remove only a4's unpublished row, add real immutable release/migration evidence
and merge with complete required checks. Verify truthful green protected main
before closing the freeze. Never rerun publication to repair bookkeeping.

Only then may Academy pin/install the released a4 artifact, qualify the real
operator/CLI paths and retire the two assignments. Product migration/cutover,
populated recovery, all other caller retirement and production approval remain
their existing separate gates. This source candidate grants none of them.
