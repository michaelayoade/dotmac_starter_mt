# Digital Media source audit

- **As of:** 2026-08-19
- **Starter:** `2790c8d1fd03e189d075aafc16874bc88a854f3e`
- **dotmac_mkt:** `7f14ee598ceefed7ac3ba0963e5a36f5c4c5082d`
- **ERP:** `0f4b1698ddbf27a04f4562ecdaf8b93f19c3debf`
- **Sub:** `91c1ec477b3af37931424bced856a16bbc2c6d3f`
- **CRM:** `c64b5aa0f7902b52e7ef73cf26f3f88687ed849d`
- **Backoffice:** `fcdd8270262dea2a78d0d4d8c4116c1e8b7b3b2d`
- **Academy:** `71b87b2abfb1dc5c1db540faca257004a8c2bf9c`
- **Academy app:** `40423a07a4eaa6172a36997f3276cb7a79dda343`
- **Workspace:** `c72fe304d3c8b2a2741d111379e4c4ab0af5da57`
- **Vendor control plane:** `e6b2bbee815cf9fd3ce99ceed0ff3a1f5763f057`
- **Integrator:** `783baf23cbf5129ef18763f97f646f684e6db3a3`
- **Method:** read only from the pinned Git trees. Dirty ERP, CRM and Vendor CP
  worktree content was excluded. The already-audited Mkt coordinate and source
  paths were rechecked through the clean Starter marketing train at
  `0c55cc480445e7f345c0695dc5ed382ee7a265b3` because no Mkt checkout is present
  on this workstation.
- **Decision:** the dossier's governed source mode is `product-first`: the small
  reusable-asset identity and creative relation already present in Mkt are the
  mandatory seed. Immutable revision history, rights evaluation, technical
  observations, renditions, access grants and Records-aware disposition are
  audited greenfield additions. No audited product implements a qualifying DAM.

This inventory is characterization evidence. ADR-0033 is the authority for the
boundary and `packages/dotmac-digital-media/EXTRACTION.toml` is the executable
extraction dossier.

## Owner and boundaries

`dotmac-digital-media` owns tenant-scoped reusable image, video, audio and rich
media identity; immutable source revisions; descriptive metadata; extractor
observations; collections and classification; rights and usage enforcement;
derived rendition identity/provenance; media-specific access and review; and
append-only lifecycle, rights, access and transformation evidence.

| Concern | Owner / seam |
|---|---|
| Opaque bytes, checksum admission, safety scan and physical deletion | `dotmac-files`; Digital Media stores only opaque file UUIDs, supplied checksums, media type and byte length. |
| Provider APIs, scanners, metadata extractors and transcoders | Integrator connector or stateless adapter. The module receives typed observations/results and never imports a provider SDK. |
| Editorial role, order, caption and context-specific alt text | `dotmac-content`; its current raw creative file references migrate to exact opaque media-revision references. |
| Document embedding position and purpose | `dotmac-documents`, pinned to an exact media revision or rendition. |
| Publication target, release intent and delivery outcome | `dotmac-publishing`. |
| Advertising/social hierarchy and performance metrics | `dotmac-media-observations`; it does not own uploaded assets or renditions. |
| Consent/model-release validity | Named legal/privacy owner. Digital Media enforces the exact decision evidence/ref supplied to it. |
| Retention, hold, preservation and disposition | `dotmac-records` only after an exact media revision is declared as a record. |
| Authentication/global permissions | Kernel/assembly prerequisite; Digital Media decides media-specific grants. |
| Search index | Rebuildable projection; module rows remain authoritative. |

No sibling module is imported and no cross-module foreign key is created. Every
cross-owner identity is opaque and every observation carries provenance.

## Product evidence

### dotmac_mkt — qualifying narrow source

The Mkt source audit pins `app/models/asset.py`, `asset_service.py`,
`post_asset_service.py`, Campaign/Post creative relations and
`tests/test_marketing_models.py` / `test_marketing_services.py`. It proves that a
reusable asset has identity independent from its contextual Post/Campaign role
and that consuming content owns role, order, caption and contextual alt text.

That is the initial behavior source, not a complete DAM. Mkt has no immutable
source-revision lineage, rights schedule, exact/perceptual duplicate evidence,
extractor provenance, transformation recipe/engine lineage, stale-rendition
repair, media access evaluator or Records declaration boundary. Those features
therefore use greenfield-after-inventory canaries rather than being falsely
described as ports.

The parked `dotmac-content` package currently stores `file_ref` on
`ContentPlanCreative` and `ContentItemCreative`. Its eventual adoption must
change that opaque coordinate to an exact Digital Media revision reference;
Content retains role, caption, alt text and ordering. Neither package imports
the other.

### ERP — domain documents, branding and avatars only

ERP has broad file upload/storage plus domain-owned attachments and generated
documents: finance attachments, vehicle documents, disciplinary-case
documents, HR handbooks, resumes, learning material, support attachments,
avatars and branding assets. `file_upload.py`/`storage.py` are already source
evidence for `dotmac-files`; the relations belong to their domains. No media
library, reusable revision lineage, rights engine or rendition catalogue exists.

### Sub — communication and field evidence only

Sub contains stored files, avatars/branding, Team Inbox media, communication
attachments, field evidence, firmware images and voice transcription. These are
domain attachment or transport relations. `team_inbox_media.py`, field services
and storage services do not provide a reusable DAM identity, rights policy or
rendition lifecycle. Sub is a later candidate consumer for regulatory photos,
incident recordings and reusable communication media, not an implementation
source.

### CRM — attachment transport and public-media delivery only

CRM has ticket/field/inbox attachments, avatar/branding helpers, public-media
delivery and upload validation. It contributes negative boundary evidence:
public URLs and provider attachment ids are delivery observations, never media
identity. No qualifying DAM or rights/rendition owner exists.

### Academy products

The Academy app embeds lesson media, produces certificates and sends generated
attachments. These are consumer and delivery surfaces. The Academy repository
has no generic media owner. Neither has immutable revisions, rights or
transformation provenance.

### Backoffice, Workspace, Vendor CP and Integrator

Backoffice and Workspace have no media owner. Vendor CP's licence artefacts are
not creative media. Integrator remains the external connector control plane and
must host provider-specific import, extraction and transformation plugins; its
current assembly has no DAM behavior to port.

### Existing Starter media candidates

The clean `agent/dotmac-media-observations` candidate at
`b166b3d99698ee19fa2fd87406c6f34f001b9e1e` owns immutable observations about
remote advertising/social entities and metrics. Its tables contain no uploaded
asset, rights, crop, thumbnail or transcode owner.

The clean marketing train at
`0c55cc480445e7f345c0695dc5ed382ee7a265b3` contains parked Content and
Publishing candidates. Content owns editorial context around a creative;
Publishing owns release intent/outcomes. Neither is DAM.

## Preserved behavior and greenfield additions

Preserved from Mkt:

- stable reusable asset identity apart from a filename or one content item;
- opaque stored-file references;
- ordered, role-bearing consumer relationships owned by the consumer; and
- provider-neutral media type and descriptive metadata.

Added only after the negative inventory above:

- immutable numbered revisions and a canonical current pointer;
- exact checksum and perceptual-fingerprint duplicate evidence;
- typed extractor observations with source checksum and extractor version;
- immutable rights versions and deterministic territory/channel/purpose/window
  evaluation;
- collection/classification, grants, annotations and saved selections;
- exact-source rendition recipes with engine/version and idempotent repair; and
- typed usage observations plus a Records-aware disposition answer.

## Cutover and reuse gate

Backoffice is the recommended first adopter because it is the selected owner
assembly for the parked marketing modules and has no competing DAM. Before it
can become a contract consumer it must pin a released package, compose the
tenant lineage in its own database, migrate every selected Mkt Asset and
creative relation, verify file UUID/checksum parity, shadow reads, and retire
Mkt's reusable asset writer. Installing the module without that retirement is
not adoption.

Content migration is coordinated but authority remains split: the DAM mapping
creates exact media revision ids, then Content rewrites only its contextual
references. There is no shared transaction or cross-database FK. Sub and
Academy may adopt the same exact release later with their own rows.

The module is therefore built and proven in Starter but deliberately not
composed by the reference assembly. Release, first-adopter composition and
writer retirement require separate authorization and evidence.

## Parallel allocation integration gate

Current `origin/main` publishes kernel `0.1.0a73`; this isolated branch must use
the next free allocation release, `0.1.0a74`. Other unmerged Starter worktrees
also propose a74 and ADR number 0033. They are not authoritative until merged.
Whichever branch lands second must rebase, take the next free kernel release and
ADR number, update its package floor and lock, and rerun the complete migration
gate. Parallel branches must never be combined by copying ledger rows.
