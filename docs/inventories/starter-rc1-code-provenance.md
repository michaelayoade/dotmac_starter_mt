# Starter RC1 code provenance review

Scope is the four package trees in the [published image inventory](starter-rc1-image-inventory.json), at Starter commit `5406b7381118be73f05ac818db0a2e5e5b10b9ba`. The image excludes the Sub and ERP applications. This review concerns source carried inside the shipped Starter packages.

I compared pinned product files with the corresponding Starter files for the rows below. Line ranges identify contiguous exact source matches, excluding line ending differences. These are positive matches from targeted comparisons and do not enumerate every retained line or establish ownership.

| Shipped file | Pinned origin | Confirmed retained code |
| --- | --- | --- |
| Kernel `crud.py` | [Sub CRUD helpers](https://github.com/michaelayoade/dotmac_sub/blob/457882482cc938bfe70f48ca2fb282dab98beee6/app/services/crud.py#L54) | Sub lines 54–105 match Starter lines 67–118, with other matching blocks. |
| Kernel `branding.py` | [Sub branding configuration](https://github.com/michaelayoade/dotmac_sub/blob/457882482cc938bfe70f48ca2fb282dab98beee6/app/services/branding_config.py#L81) | Sub lines 81–95 and 97–112 match Starter lines 159–173 and 178–193. |
| Kernel `consent.py` | [Sub communication eligibility](https://github.com/michaelayoade/dotmac_sub/blob/457882482cc938bfe70f48ca2fb282dab98beee6/app/services/communication_eligibility.py#L285) | Sub lines 285–291 match Starter lines 371–377. The kernel identifies the service as a port. |
| Kernel `request_evidence.py` | [ERP network helpers](https://github.com/michaelayoade/dotmac_erp/blob/e286636baea382e3978e779acd3155cea27e82b8/app/net.py#L135) | ERP lines 135–142 match Starter lines 346–353, including the forwarded-header helper. ERP lines 104–108 also match Starter lines 319–323. |
| Template Studio `service.py` | [Sub notification renderer](https://github.com/michaelayoade/dotmac_sub/blob/457882482cc938bfe70f48ca2fb282dab98beee6/app/services/notification_template_renderer.py#L192) | Sub lines 192–196 match Starter lines 107–111. The service and dossier identify the single-brace renderer as a port. The dossier records ERP document generation as inventory only. |

Additional review is required for these shipped paths.

| Package | Evidence and remaining comparison |
| --- | --- |
| Kernel | The dossier and code identify Sub-derived query helpers, consent and delivery models, and Sub/ERP machine credentials. They also describe product-first credential lifecycle and settings work and ERP-derived migration and identity patterns. The sampled Sub query helper comparison found no substantial contiguous exact match under the same matching rule. Compare the remaining named source paths and revisions with the shipped files, including adapted code and migrations. [Kernel dossier](../../packages/dotmac-kernel/EXTRACTION.toml) |
| UI | The token source explicitly retains Sub semantic intent names verbatim. The dossier records Sub CSS and component sources, plus map and dashboard references. A comparison of the pinned Sub empty-state template with the shipped template found no substantial contiguous exact match. Trace CSS, markup and JavaScript by slice to distinguish retained material from independent implementation and consumer adoption. [UI dossier](../../packages/dotmac-ui/EXTRACTION.toml) |
| Ticketing | The dossier classifies the package as greenfield after inventory. Sub supplied lifecycle and other behavior references, and ERP is listed as inventory only. No copied Ticketing code was established in this review. Verify the shipped lifecycle, assignment and linking implementation against the recorded inputs before closing the rights review. [Ticketing dossier](../../packages/dotmac-ticketing/EXTRACTION.toml) |

## Follow-up comparisons

The follow-up compared eleven source and destination pairs at the pinned
revisions. It looked for identical contiguous blocks of four or more lines
with at least three substantive non-comment lines. It also checked the
corresponding extraction descriptions to distinguish adaptations from
inventory references.

| Shipped path | Pinned inputs checked | Disposition |
| --- | --- | --- |
| Kernel `consent_models.py`, `delivery_models.py` | Sub `app/models/notification.py` at `457882482cc938bfe70f48ca2fb282dab98beee6` | Documented adaptations of suppression and delivery models. No substantial exact block found in these comparisons. Include both in the Sub rights schedule. |
| Kernel `settings_resolver.py` | Sub and ERP `app/services/domain_settings.py` at `457882482cc938bfe70f48ca2fb282dab98beee6` and `0dc07e4b6dd36260c9510a7115dbdc656e2a19a5`, respectively | The ERP match was an import list. The shipped resolver explicitly credits Sub's `ensure_by_key` and resolution behavior. Treat this as a documented Sub adaptation and keep ERP settings provenance open for the remaining source paths. |
| Kernel `credential_lifecycle.py` | Sub `app/services/credential_recovery.py` at `7b5a7220a90a3c86b4afd339a1e3950dbcfddf2b` | Documented Sub implementation source. The sampled recovery file yielded no substantial exact block. Include the adaptation in the Sub rights schedule. |
| Kernel `machine_auth.py` | Sub `app/services/auth.py` and ERP `app/api/service_principal.py` at the same Sub and ERP pins used for settings | Documented extraction from both products. No substantial exact block found in the sampled files. Include the identified adaptation in both rights schedules, with each grantor confirming the portions it controls. |
| UI `tokens.py` | Sub `static/css/design-system.css` at the same Sub pin used for settings | Exact line comparison found no substantial block across CSS and Python. The shipped source explicitly retains Sub semantic intent names. Record the token mapping as provenance evidence and confirm the extent of any retained CSS or markup separately. |
| Ticketing `lifecycle.py`, `models.py` | Sub `app/services/ticket_validation.py` and `app/models/support.py`, and ERP `app/models/support/ticket.py`, at the same Sub and ERP pins used for settings | No substantial exact blocks found in the three sampled pairs. This supports keeping these files in the reference category pending review of the remaining assignment and linking inputs. |

Exact matching can miss rewritten or reorganized code. The follow-up adds no
confirmed verbatim blocks beyond the earlier findings. The source paths and
rights disposition for untested portions remain open.

## Evidence needed before customer distribution

1. Extend the origin map to the remaining shipped ports. Record the source revision, path, destination, retained or adapted material, applicable notice, and contributor or owner for each. Capture the license or file-specific terms in effect at the source revision. Author metadata and extraction ownership fields are attribution leads.
2. Retain Michael Ayoade's signed Starter statement against the exact RC1 source. For each confirmed or adapted Sub or ERP contribution, retain records showing the relevant rights holder and chain of title, including applicable contributor or contractor assignments. Where the commercial delivery relies on a grant, record written authorization covering the identified code, modifications, commercial redistribution in the Starter image, customer use under the proposed terms, and any notice conditions. Keep this authorization scoped to the reused material in Starter.
3. Have the legal reviewer document the disposition of each row and confirm rights for the intended public distribution. For material whose rights cannot be established, resolve its disposition before shipping. If the shipped bytes change, inventory and qualify the replacement image by its own digest.

## RC1 distribution authorization for review and signature

**Status** Michael Ayoade reports signing the RC1 distribution authorization
for both Schedule A and Schedule B. Retain the executed copy, actual
signature and effective dates, and attachments with the release records.
The signed RC1 authorship statement is separate supporting evidence.

**Grantor legal name** Michael Ayoade (`michaelayoade`), as reported by the owner  
**Starter distributor legal name** Michael Ayoade (`michaelayoade`), as reported by the owner  
**Effective date** ____________________ **Territory** ____________________  
**Term** ____________________ **Fees or royalty terms** ____________________

**Signed authorship statement date and storage reference** ____________________  
**Customer Starter terms version or attachment** ____________________

The grantor authorizes the Starter distributor, on a nonexclusive basis and
within the territory and term specified above, to reproduce, adapt, build,
package and commercially distribute the material expressly covered by the
signed schedules below as incorporated into Dotmac Starter 0.9.0 RC1.
The covered Starter source is `5406b7381118be73f05ac818db0a2e5e5b10b9ba`
and the covered image is
`ghcr.io/michaelayoade/dotmac_starter_mt@sha256:18fd3e6cd4ecb9b8e0dfa9a9c526b4423ed05adccddfce3ceb3b23af95d6d9a8`.
The distributor may authorize customers under its written Starter terms to
install, run and make backup copies of that release.

The grant extends only to the grantor's verified rights in the portions
specified below. It excludes standalone distribution of the material, the
Dotmac Sub and Dotmac ERP applications, and their names and logos. Ownership
remains with the applicable rights holders. The distributor will preserve
applicable notices and comply with the conditions attached to each signed
schedule. Later releases, additional portions and any authorization for the
earlier GHCR publication require a separate written record.

### Schedule A — Dotmac Sub retained and adapted portions

All source paths are relative to `dotmac_sub` at
`457882482cc938bfe70f48ca2fb282dab98beee6`. Destination paths are relative
to the covered Starter commit. The first table covers exact blocks. The
second covers only the Sub-derived material in the named adaptations.

| Source file and lines | Starter destination and lines |
| --- | --- |
| `app/services/crud.py`, 19–41, 54–105, 107–128 and 130–140 | `packages/dotmac-kernel/src/dotmac_kernel/crud.py`, 28–50, 67–118, 120–141 and 143–153, respectively |
| `app/services/branding_config.py`, 81–95 and 97–112 | `packages/dotmac-kernel/src/dotmac_kernel/branding.py`, 159–173 and 178–193, respectively |
| `app/services/communication_eligibility.py`, 285–291 | `packages/dotmac-kernel/src/dotmac_kernel/consent.py`, 371–377 |
| `app/services/notification_template_renderer.py`, 192–196 | `packages/dotmac-template-studio/src/dotmac_template_studio/service.py`, 107–111 |

| Source file | Starter destination and adapted portion |
| --- | --- |
| `app/models/notification.py` | `packages/dotmac-kernel/src/dotmac_kernel/consent_models.py` and `delivery_models.py` in the same directory. The documented adaptations are the `CommunicationSuppression` model and the Sub `NotificationDelivery` model as adapted into `CommunicationDelivery`. |
| `app/services/domain_settings.py` | `packages/dotmac-kernel/src/dotmac_kernel/settings_resolver.py`. Existing-row preservation in `ensure_by_key`. |
| `app/services/settings_spec.py`, 4865–4886 at `98fc0bd59eaf31b96af0de0f844529da070c9833` | `packages/dotmac-kernel/src/dotmac_kernel/settings_resolver.py`. Default fallback for invalid stored values exposed by `resolve_value`. |
| `app/services/notification_template_renderer.py`, 117–137, 145–150 and 184–220 | `packages/dotmac-template-studio/src/dotmac_template_studio/service.py`, 87–150. Single-brace placeholder extraction, validation and rendering. The ranges identify corresponding behavior and include adaptations. |
| `app/services/credential_recovery.py` at `7b5a7220a90a3c86b4afd339a1e3950dbcfddf2b` | `packages/dotmac-kernel/src/dotmac_kernel/credential_lifecycle.py`. The documented adaptation is the recovery behavior. The separately developed identity storage and credential assignment contracts are outside this entry. |
| `static/css/design-system.css` | `packages/dotmac-ui/src/dotmac_ui/tokens.py`. The source identifies the semantic intent names as retained from Sub. This entry covers the identified names. |

**Schedule A grantor** Michael Ayoade  
**Evidence of ownership or authority** __________________________________  
**Notice and conditions attachment** ____________________________________

### Schedule B — Dotmac ERP confirmed portions

All source paths are relative to `dotmac_erp` at
`e286636baea382e3978e779acd3155cea27e82b8`. Destination paths are relative
to the covered Starter commit. Each row covers the identified matching
blocks only.

| Source file and lines | Starter destination and lines |
| --- | --- |
| `app/net.py`, 104–108 and 135–142 | `packages/dotmac-kernel/src/dotmac_kernel/request_evidence.py`, 319–323 and 346–353, respectively |

**Schedule B grantor** Michael Ayoade  
**Evidence of ownership or authority** __________________________________  
**Notice and conditions attachment** ____________________________________

### Material awaiting more source evidence

Machine authentication follow-up checked the pinned Sub
`app/services/auth_dependencies.py::_api_key_principal` at `5c56b64a2`
against the RC1 `machine_auth.py`. The source map identifies the
`X-Api-Key` wire header and credential lifecycle checks as behavioral
precedents. The kernel implements its own typed principal and scope
enforcement. The ERP comparison in the source inventory identifies
empty-scope authorization, human identity coupling and SHA-256-only
hashing as rejected behaviors. Keep the machine authentication entry
outside the grant pending a retained-expression finding or a documented
review disposition. [Machine credential source inventory](machine-credential-sources.md)

Machine authentication cites both products. The code identifies Sub's API-key
header and declared-scope behavior, while several ERP behaviors are recorded
as rejected design inputs. The sampled Sub and ERP files showed no substantial
exact blocks. Establish a per-product map before adding a grant for any
retained expression in this module. ERP settings, migration and identity
references, the remaining Sub query and UI paths, and the unsampled Ticketing
paths also remain open. The schedules cover the identified portions rather
than entire source files.

### Ownership attachments required before signing

#### Commit attribution for the exact scheduled blocks

Michael Ayoade (`michaelayoade`) confirms that he made the `root`
commits identified in this RC1 review and ran the Claude sessions associated
with the reviewed contributions. This is his first-hand account of commit
and session operation. The original Git and GitHub attribution is retained
below. The remaining inquiry concerns the source of any material introduced
in those changes, including pre-existing code and contributions from others.

Git blame at the pinned product revisions traced the scheduled lines to their
last substantive changes. GitHub's commit records identify the linked author
accounts where available. This is evidence of commit attribution. Confirm who
authored or supplied the relevant material, including any imported code, before
treating a commit as proof of personal ownership.

| Scheduled block | Attribution and remaining evidence |
| --- | --- |
| Sub CRUD | 47 of 108 scheduled lines trace to [`b27a4b4e`](https://github.com/michaelayoade/dotmac_sub/commit/b27a4b4e39956721a4e2f8dfb2997ad99a60713a), linked to `michaelayoade` and carrying a Claude co-author trailer. The remaining 61 lines trace to [`dd4ef9e1`](https://github.com/michaelayoade/dotmac_sub/commit/dd4ef9e17827704a3951a334f86e6a83ed36f2cc) and [`7aab9ab4`](https://github.com/michaelayoade/dotmac_sub/commit/7aab9ab4996a428b72919b8cde653cccce2962b9), both labeled `root` without a linked GitHub author. Identify the operator and origin of those changes. |
| Sub consent | All seven scheduled lines trace to [`64be13a0`](https://github.com/michaelayoade/dotmac_sub/commit/64be13a01f76a75806cec4dc2c54b02bdb312ae8), a verified GitHub squash commit attributed to `michaelayoade` with Michael Ayoade and Claude co-author trailers. Keep the underlying PR record to support who supplied the particular lines. |
| Sub branding and notification renderer | Branding traces to [`942f3383`](https://github.com/michaelayoade/dotmac_sub/commit/942f3383b3f523cc1457e19e27597782cbe81d85) and the renderer to [`e34da553`](https://github.com/michaelayoade/dotmac_sub/commit/e34da553151207cbdca1d81b13ce0cf8b6bc5c78). Both are attributed to `claude` on GitHub. Record who directed and edited the work, the applicable tool terms and any pre-existing material used. The account label alone leaves the source of rights unresolved. |
| ERP request evidence | All 13 scheduled lines trace to [`4cd39945`](https://github.com/michaelayoade/dotmac_erp/commit/4cd39945a859bb2027ed4d429ede22b489505e31), labeled `root` with a Claude co-author trailer and no linked GitHub author. Identify the operator and where the helper code originated. |

#### Commit attribution for sampled Sub adaptations

These counts describe sampled product source lines. Adaptation changes
prevent using the counts as a measure of how much Starter code each author wrote.

| Source sample | Attribution |
| --- | --- |
| Notification model lines 233–265 and 468–520 | 53 of 86 lines trace to `64be13a0`, linked to `michaelayoade`. The other 33 trace to commits labeled `root` (`bf796c3f7`, `9c8dbbdc62`, `d95d6128a0`). |
| Settings lines 381–450 around `ensure_by_key` | 13 of 70 lines trace to `75e22a8222`, linked to `claude`. The other 57 trace to commits labeled `root` (`bf796c3f7`, `d702daddd2`, `dd4ef9e178`). This sample does not establish attribution for the separate fallback behavior in the schedule. |
| Credential recovery lines 416–517 and 705–866 | All 264 sampled lines trace to `823dfca82`, `9328bf62e` and `9fb95b15b`, each linked to `michaelayoade`. |

#### Renderer, settings fallback and UI token follow-up

| Source area | Authorship evidence |
| --- | --- |
| Renderer extraction, validation and substitution | Sub source lines 117–137, 145–150 and 184–220 trace to `root` commits [`4fa42ba82`](https://github.com/michaelayoade/dotmac_sub/commit/4fa42ba828c4b539b973de6cc298a72e986fcd30) and [`d702daddd`](https://github.com/michaelayoade/dotmac_sub/commit/d702daddd29fdf5638a00da5b0228992e256bd39), and Claude-labeled commits [`e34da5531`](https://github.com/michaelayoade/dotmac_sub/commit/e34da553151207cbdca1d81b13ce0cf8b6bc5c78) and [`caa257b03`](https://github.com/michaelayoade/dotmac_sub/commit/caa257b03f975ac7b215df35669b281b68e902e3). Keep Michael's operator confirmation alongside these records. |
| Settings fallback | The [initial Sub commit](https://github.com/michaelayoade/dotmac_sub/blob/bf796c3f7de1b6f960a89e161ca85781580ba8ff/app/services/settings_spec.py#L2008), labeled `root`, already contains fallback after invalid coercion, a disallowed value or an out-of-range integer. The [later root-authored refactor snapshot](https://github.com/michaelayoade/dotmac_sub/blob/dd4ef9e17827704a3951a334f86e6a83ed36f2cc/app/services/settings_spec.py#L1910) includes an integer type check on the range fallback. Schedule A pins the [pre-extraction Sub implementation](https://github.com/michaelayoade/dotmac_sub/blob/98fc0bd59eaf31b96af0de0f844529da070c9833/app/services/settings_spec.py#L4865). The later Sub pin delegates to the kernel. The snapshots establish that the behavior predates the Starter extraction. They do not attribute every intervening edit. |
| UI semantic intent names | The five names in Starter's `SEMANTIC_INTENTS` match Sub selector names at lines 99–101, 108–110, 117–119, 126–128 and 135–137. All 15 selector lines trace to [`b13d5b26d`](https://github.com/michaelayoade/dotmac_sub/commit/b13d5b26df24ac498952d4cd75b7d1198f2c81de), whose Git author is `michaelayoade`. This traces the names specifically. |

Michael Ayoade reports that he signed the dated RC1 authorship statement
covering the reviewed `root` commits and Claude sessions. Retain the signed
copy and its signature date with this record. This status applies to the
authorship statement. Michael separately reports signing the distribution
authorization for both schedules, as recorded above.

Preserve available session records, the terms applicable to the account
used, and records of any pre-existing inputs or contributions by others.
Attach any applicable licenses or assignments and list required notices.

Retain Michael's confirmation with the commit and PR records. For the
Claude-assisted contributions, record the applicable tool terms and any
pre-existing inputs or human contributions. For each unresolved contribution, retain evidence of personal authorship or
the applicable assignment or license if another person or entity supplied
protected material. Record any AI-assisted work and pre-existing inputs
separately. The adapted portions need contributor evidence specific to the
source material actually retained in Starter.

Michael Ayoade (`michaelayoade`) reports that he created the repository
and identifies repository creation as the basis of his ownership claim.
Record the repository creation evidence and identify the repositories it
covers. For each scheduled Sub or ERP portion, record the evidence that
Michael authored it or acquired rights from its author. Repository creation
and authorship of a scheduled contribution are separate facts to document.

- For each schedule, identify the legal rights holder and attach the records
  linking the original contributors to that holder, such as applicable
  employment IP provisions, contractor assignments, contributor agreements,
  or a prior license that expressly authorizes the proposed distribution.
- If Michael Ayoade signs for an entity, attach that entity's legal name and
  its record of his authority to sign this grant. If signing personally,
  attach the authorship or assignment records supporting personal ownership.
- Retain the signed Starter ownership statement with the RC1 commit and name
  the legal distributor. Attach each source revision's applicable terms and
  notices, plus any separate permission needed for third-party contributions.
- Record each grantor's covered schedule, commercial terms, and any prior
  publication authorization. Have the reviewer resolve the open material
  before treating this document as clearance for the entire RC1 image.

### Signature

**Schedules signed by this grantor** ☐ A ☐ B  
**Printed name** Michael Ayoade  
**Signing capacity and entity represented, if applicable** __________________  
**Signature** __________________________________ **Date** ________________

By signing, I authorize the RC1 distribution described above for the
selected schedules, to the extent of the rights I hold or am authorized
to exercise. If I hold those rights personally and distribute Starter in
my own name, my signature records my authorization in that capacity.

Confirm the signing capacity, complete the commercial terms and attach the
ownership records for both schedules before signing. If the records confirm
that Michael Ayoade personally holds the scheduled rights and distributes
Starter in his own name, record his authorization of Starter distribution
in that capacity. If the records identify a separate legal entity, update
the party fields and record the authority for each signature.
