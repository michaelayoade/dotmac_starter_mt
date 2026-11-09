# Dotmac Starter RC1 developer evaluation authorization

Draft for review and signature by Michael Ayoade.

**Signature status** Draft; not authorized for download. A technically qualified
reduced candidate is identified below. Remaining kernel provenance decisions
and Michael Ayoade's supplemental signature are outstanding. Historical
candidates later in this document are not the current release scope.

**Original authorization date and record** [Complete and attach executed Schedules A and B.]

On 2026-11-09, the requester reconfirmed that Schedules A and B were signed
and said physical copies were unnecessary. Record this as requester-reported
execution. No executed text was inspected. The reported signature concerns
the original schedules. Coverage of the later production and hosted-app
permissions and additional code remains subject to the supplemental scope.

**Designated access approver** `olatejuakinlabi@gmail.com`, as confirmed
by the requester. This identifies the access administrator, separately
from Michael Ayoade's stated role as rights holder and grantor.

**Grantor and signing capacity** Michael Ayoade [Confirm personal capacity or identify the entity and authority.]

**Authorized distributor** [Confirm legal name.]

**Effective date** [Complete at signing.]

## Artifact scope

- Current reduced candidate is derived from RC1 source
  `5406b7381118be73f05ac818db0a2e5e5b10b9ba` by staging script commit
  `8f0b5199ee86063ec81bc8d090e69ae5f62b1616`. Its retained CI archive is
  `evaluation.tar`, SHA-256
  `31f1fb68e4dfefeaa0e2eeb2718b0649f0453cfe7b82a05e1279f69bb7953ee0`.
  The [exact retained manifest](starter-rc1-evaluation-final-files.txt)
  contains 1,865 files and 95 directory entries. Manifest SHA-256 is
  `b1900d99ba667c39cb264e4ee8a4e24312446bfe2b9d13272b99c80857684327`.
  This identifies the proposed scope for review; it is not a clearance assertion.
  The following original full-tree and GHCR identities are historical inputs,
  not additional artifacts covered by this reduced-source grant.
- Source candidate is the tracked tree at commit
  `5406b7381118be73f05ac818db0a2e5e5b10b9ba`. The internal candidate tar
  SHA-256 is `8e5f2dab2deba4d3a6d1c8ace2d577df6ddf707f9f5163fb71bae3a3acd044d6`.
  This digest identifies review input only. The
  [hold manifest](starter-rc1-source-hold-manifest.txt) records all 3,735
  tracked entries, with modes and Git object IDs. Every listed entry is on
  hold for this source release. Attach an approved file manifest and its
  final archive digest before signing.
- RC1 image is
  `ghcr.io/michaelayoade/dotmac_starter_mt@sha256:18fd3e6cd4ecb9b8e0dfa9a9c526b4423ed05adccddfce3ceb3b23af95d6d9a8`.
- Attach the final [developer evaluation terms](../LICENSING_REVIEW.md#developer-evaluation-terms-for-review)
  and applicable notice conditions.

## Supplemental permission

To the extent I hold or control the necessary rights, I authorize the named
distributor to reproduce, package, host and distribute the identified
artifacts worldwide at no charge, incorporating the cleared portions listed
in the attached Schedules A and B. I authorize the distributor to grant
recipients permission to download, copy, install, run, modify and
build the covered material for evaluation, testing, production use and
operation of hosted applications for their customers.
Recipients may use their modified copies for those purposes.
Customers may access the recipient's hosted application. Sublicensing and redistribution
by recipients are outside this grant. Separately licensed material retains
its applicable terms and notices.

This supplement must expressly include the additional retained portions in
the dated clearance table below before it can cover the reduced archive.
It does not presently authorize those unresolved portions. The separate Dotmac Sub and Dotmac ERP
applications, trademarks, unlisted material and other releases are outside
the grant. It supplements the original authorization for this evaluation
release and takes precedence for that purpose where the terms conflict.

**Signature** ____________________ **Date** ____________________

## Coverage review

The available authorization draft identifies Sub code and named adaptations
in Schedule A and two ERP network helper blocks in Schedule B. It describes
distribution in the pinned RC1 and customer installation, running and
backup. Public no-charge source downloads and recipient modification are
not expressly covered by that wording. Michael reported signing both
schedules. Compare the executed text and attachments before relying on it.

The [origin map](starter-rc1-code-provenance.md) covers four image package
trees. Before approving the source archive, review the full archive manifest
for release suitability and rights, including packages absent from the image.
Resolve or exclude unverified Sub and ERP material and rehash any changed
archive. Check third-party obligations for both downloads. A supplement for
the identified portions does not clear the whole tracked tree.

## Full-tree screening

### Reduced runtime review scope

The [runtime review manifest](starter-rc1-evaluation-runtime-review.txt)
narrows the review to 355 tracked entries from the RC1 assembly, its
migrations and the four package trees declared by the runtime manifest.
This excludes the other 97 package trees from the proposed runtime review.
It is a review input. The build inputs, evaluation terms and notices still
need to be added to an approved release manifest.

The assembly registers Custom Fields in `app/features/__init__.py` and
loads that registry in `app/assembly.py`. Its
`app/features/custom_fields/models.py` expressly credits an ERP port that
Schedule B does not list. Dropping the file would leave the registered
feature incomplete. Kernel machine authentication also remains open, and
Schedule A covers named UI token material while other UI slices still
need a disposition. Removing packages outside the runtime manifest does
not remove these blockers.

### Custom Fields disposition

Hold the Custom Fields model and service together. The pinned assembly's
architecture identifies the model source as ERP
`app/models/finance/automation/custom_field.py`. Starter commit
`96ee204f` introduced the definitions model. Commit `eab36b4f` introduced
the ERP service port, whose recorded input is
`app/services/finance/automation/custom_fields.py`. The candidate scope
must therefore account for both
`app/features/custom_fields/models.py` and
`app/features/custom_fields/service.py`, including the model definitions,
value validation and service adaptations. Schedule B currently identifies
only `app/net.py` portions. The Starter history establishes the port and
its destination. It leaves the exact ERP source revision and retained
portions to be verified against the upstream files before extending the
schedule. Preserve the later Starter validation changes when comparing.

Compared both upstream paths at ERP commit
`e286636baea382e3978e779acd3155cea27e82b8` with Starter RC1. This is a
verified comparison revision. The evidence does not tie it to the original
July port. Line numbers below are one-based and ignore line-ending changes.

| File | ERP lines | Starter RC1 lines | Finding |
| --- | --- | --- | --- |
| Model | 267–272, 280–331, 330–348, 355–368 | 204–209, 224–275, 287–305, 308–321 | Exact blocks within value validation and range handling. A matched block in a later ERP snapshot alone does not establish the direction of reuse. |
| Service | 46–51 | 124–129 | Exact documentation fragment. The Starter introduction commit separately identifies the service as a port. |

Custom Fields is excluded from the evaluation scope. Schedule B remains
limited to the existing network helper portions. The comparison identifies
matching text but does not establish clearance or the original upstream
revision. The reduced review manifest is a historical input and does not
authorize packaging Custom Fields. A buildable evaluation variant still
requires removal of the feature's registration, dependent call sites and
associated migration composition, followed by an archive-only hosted build.

An internal review tar now omits the 15 Custom Fields paths matched by
name. Its [verified file list](starter-rc1-reduced-review-files.txt) has
340 files and its SHA-256 is
`27620aff90e782511f1fef6688cf810eaf9721326ec5b5c183aeba61aa9cd16e`.
This is a review archive. It still contains assembly registration and
dependent Parties and Settings code, and omits migration revisions
referenced by the remaining migration chain. Its digest is evidence of
the physical exclusion only. The authorization remains on hold for a
buildable archive with these dependencies reconciled.

A subsequent isolated staging archive removed the feature registration,
Alembic model import, capability backfill, Settings domain exclusion and
Party detail panel. It omitted the feature files, admin templates and
assembly adoption migration, and made `a002_applied_licences` the assembly
root with the previous root's kernel dependency. This migration graph is
intended for a fresh evaluation database. It retains the kernel Party
JSONB compatibility column used by identity storage.

The staging archive contained [3,721 file entries](starter-rc1-evaluation-internal-files.txt)
and had SHA-256
`efb170f1a0ca1d327a129edd2b18b8ad1f789d364f73d23762fd9e835c141731`.
It retained the full RC1 package tree for the existing locked build, so
the other provenance exclusions still apply. GitHub CI build, migrations
and first login are pending. This checksum is a staging record and the
signature hold remains in effect.

The evaluation workflow was pushed to `codex/qualify-rc1-evaluation` at
`77725e8da03528d81458defb6fb7a83b3b4d3044`. The GitHub connector returned
no workflow runs or commit statuses for that revision when checked.
GitHub's direct API subsequently identified
[run 42236730780](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/42236730780)
on the pushed revision. The push trigger worked. The archive-only build,
fresh PostgreSQL migrations and first platform login passed.

The hosted job reported archive SHA-256
`a3f15bdf515057c767e7c608e32f8b0fd8b3d1ff0703eb5570d26763bc1fb132`.
The [3,725-file inventory](starter-rc1-evaluation-ci-files.txt) was
reconstructed from the pinned Git tree and the successful staging
script's exclusions and handoff inputs. The workflow did not retain the
runner's manifest as an artifact. This records the tested candidate.
Distribution clearance and the signature hold still apply.

### Machine authentication and UI disposition

| Tested material | Schedule comparison | Distribution disposition |
| --- | --- | --- |
| Kernel `machine_auth.py` | The credential inventory traces the header, scope and lifecycle behavior to Sub and identifies ERP behaviors the kernel deliberately rejected. Earlier source samples established no substantial exact blocks. Neither schedule lists this module. | Excluded from the proposed distribution scope pending a retained-code determination. Physical removal requires reconciling the kernel's public exports and the machine rotation module, which imports its hash helper. |
| UI semantic intent names in `tokens.py` | Schedule A identifies the retained Sub names. It does not cover the whole file, compiled CSS or other UI components. | Keep the names within the scope of the proposed supplement. The executed terms and rights evidence still need verification. |
| UI templates, CSS and component contracts | The UI dossier records ERP and Sub inputs for empty-state markup and identifies further map and dashboard references. Adoption evidence and the previous negative exact-match sample do not establish rights in the remaining slices. Schedule B lists only ERP network helper blocks. | Hold the UI package as a whole from the proposed distribution scope until those slices are resolved. The tested assembly imports the package and uses its stylesheet, so physical exclusion requires an assembly change. |

These are release-scope exclusions. The tested archive still contains the
files and its digest is unchanged. The current authorization is a review
draft. A replacement archive must implement the exclusions, account for
the other unresolved packages, and pass the hosted build and first-login
checks before its manifest and digest can replace the tested candidate's
evidence above.

Evaluation commit `4353612e6c4b49f26afb9cf0192dbcd5f2a4d48b` stages
physical removal of machine authentication and rotation and removes their
kernel exports. It retains credential storage and its migration lineage.
[Qualification run 42239076009](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/42239076009)
tests this intermediate candidate. UI and the remaining provenance holds
still prevent signing for distribution.

Run 42239076009 completed successfully. It built the source archive,
migrated a fresh PostgreSQL database and verified first platform-admin
login after the machine authentication exclusion. The runner reported
archive SHA-256
`e517bccf70d54fdc32413635b369b473f4b53f8a0eaa052dd4a665d109270bfc`.
The UI and other unresolved material remain inside this intermediate
archive. This records technical qualification, with distribution clearance
and final terms execution still pending.

Keep the source grant on hold until those portions are cleared or a
separately scoped assembly replaces or removes them with its registry,
migration and build inputs reconciled. The pinned Dockerfile installs the
full locked package set before pruning to the runtime subset. A reduced
archive needs a matching dependency lock and Git-hosted archive-only
build verification before recording a final digest.

All 101 package extraction dossiers reference Sub or ERP. A reference can
describe inventory or design precedent. It does not establish that code
was retained. The [provenance leads](starter-rc1-source-provenance-leads.txt)
record 57 matching source and documentation lines for follow-up, with
their pinned paths and line numbers. These include unscheduled ERP ports
in the assembly's custom-field validation and Approvals, ERP inputs in
Imports and Remita, and Sub inputs in Billing, Integration and deployment
locking. Schedule A and B list only the identified portions, so the
screening also leaves unscheduled portions of those same files on hold.

Removing flagged files alone would leave an unverified and potentially
unbuildable tree. The pinned Dockerfile installs the root Poetry dependency
set from the package tree before collecting the runtime subset. A smaller
evaluation archive therefore requires a reviewed build scope and a clean
build from the resulting archive. Keep the original image digest as an
identity record. Clear any outstanding provenance inside that image
separately, or qualify a replacement image under its own digest.

Evaluation commit 94cb1bb64b4353e2eb1433215db74e1ba269de94 removes
seven unused UI templates and their exported component declarations from
the staged archive: map frame, dashboard grid, dashboard panel, metric
card, notice banner, dashboard line chart and notification toast.
The empty-state component and shared stylesheet remain pending clearance.
The workflow now retains its archive and manifest after successful login.
This intermediate candidate requires its own CI result and checksum.

The optional UI exclusion candidate at commit
94cb1bb64b4353e2eb1433215db74e1ba269de94 passed qualification in
https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/42605728492.
The shared UI stylesheet, empty-state renderer and additional flagged
package material remain pending distribution clearance.

## Current reduced candidate — 2026-11-09

[Hosted qualification 42618705952](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/42618705952)
passed the archive-only Docker build, fresh PostgreSQL migrations and first
platform-admin API login. The retained artifact is `evaluation-clearance-review`,
artifact ID `13672879564`. The downloaded tar checksum was independently
checked against the job log. Its three package trees are `dotmac-kernel`,
`dotmac-template-studio` and `dotmac-ticketing`. A new Poetry lock was generated
for this subset in hosted CI and is included in the tested archive.

This is an API-only candidate. It excludes all 98 other package trees,
including `dotmac-ui`, the presentation feature, kernel browser templates
and static assets, Custom Fields' feature and adoption migration, and
`machine_auth.py` / `machine_rotation.py` plus their public exports. It keeps
the kernel's Party JSONB compatibility migration and credential storage
lineage; exclusion of an implementation does not imply deletion of all
similarly named storage or tests. It does not provide the original browser UI.
The original published RC1 container is not cleared by these exclusions.
The CI-built replacement container has not been published to GHCR.

### Remaining retained-code decisions

These rows are outside the portions expressly listed in the available
Schedules A and B draft. Michael's reported signatures have been recorded;
no claim is made that signed documents were inspected. The requester has
also stated that Michael owns the products and operated the root commits
and Claude sessions. That account is recorded without inventing a source
revision or an executed grant for additional material.

| Retained Starter path (relative to `packages/dotmac-kernel/src/dotmac_kernel/`) | Evidence and required disposition |
| --- | --- |
| `query.py` | Source identifies Sub `app/services/common.py` pagination and ordering as a port. Prior comparison at Sub `457882482cc938bfe70f48ca2fb282dab98beee6` found no substantial exact block; adapted expression remains outside A. Confirm ownership and expressly include the retained adaptation in the supplement, or remove it with its callers reconciled. |
| `migrations/verify.py` | Source explicitly credits ERP `20260813_tenant_projection` for the tenant catalogue contract. Exact supplying revision and covered source range remain unverified. Do not treat B's network-helper grant as coverage. |
| `migrations/versions/20260814_0024_external_identity_bindings.py` and associated binding implementation | Migration credits ERP `federated_identities` and Sub binding discriminator/evidence patterns. Exact original revisions and retained expression remain unverified. Both schedules need an explicit disposition for these portions. |
| `migrations/versions/20260810_0019_communication_consent.py` | Migration credits Sub suppression storage. A covers the named models and service portions, not this migration expressly. Include the retained migration adaptation in the supplemental scope after ownership confirmation. |
| Remaining portions of scheduled files; Ticketing assignment/linking inputs | Existing exact-block and negative-match samples are not full-file clearance. Retain a per-portion disposition for adapted expression; inventory-only behavior references do not themselves require a grant. |

The release remains blocked by these decisions. This table is an unresolved
annex for review, not a new claim that every row contains copyrightable
copied expression. Complete the source coordinates where available and
record either an independent/reference-only determination or Michael's
explicit authorization for the owned retained expression. A changed archive
requires a new manifest, checksum and hosted qualification.

### Public intake record

The public [access form](https://dotmac-starter-access.dotmac-fiber.chatgpt.site/)
records authenticated identity, exact accepted terms, version, SHA-256 and
server timestamp. The [approval console](https://dotmac-starter-access.dotmac-fiber.chatgpt.site/admin)
permits only `olatejuakinlabi@gmail.com` to approve, deny or revoke. Each
decision binds the request ID and terms hash. The download route additionally
requires a cleared release; production configuration is `CLEARED_RELEASE = null`
and contains no release artifact. Human-account acceptance and approval
still need end-to-end verification. Anonymous checks passed; commit
`857cb94f717d6dc1e4df815372b808782054791c` corrects the forged-header check's
shell formatting and reruns it. No signed-in flow has been falsely recorded
as verified.

Final verification update: [run 42620047170](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/42620047170)
at `cf738ceb0506841558f078c2e3e66e9f2829df6e` passed the synthetic
acceptance/approval/revocation checks, corrected live anonymous and forged-header
checks, archive build, fresh migrations and first platform-admin API login.
It reproduced the same archive SHA-256 recorded above. The synthetic tests
use the deployed Worker source snapshot and private in-memory records; they
do not substitute for a real-account hosting sign-in check. No downloads
were enabled or real applicants approved during verification.

The [separate supplemental authorization for signature](starter-reduced-rc1-supplement-for-signature.md)
binds this exact archive and manifest, expressly includes Michael-owned
retained adaptations beyond the earlier schedules, and discloses outstanding
source-coordinate checks. It remains unsigned. No release clearance or
signature is inferred from preparing it.
