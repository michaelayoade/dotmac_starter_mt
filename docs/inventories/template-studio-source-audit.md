# Template Studio source audit — ERP documents vs Sub notifications

**As of:** 2026-08-10
**Starter:** `8472b9ee` (working tree on `docs/product-first-extraction`)
**ERP:** `3c86b5a9` (`chore/governance-b1dfd82-schema-v3`)
**Sub:** `5d6f115b7` (`feat/hold-boot-secrets`)

The audit `packages/dotmac-template-studio/EXTRACTION.toml` blocks on, and that
[`module-extraction-sources.md`](module-extraction-sources.md) § "Template Studio
source audit" scopes. It executes steps 1–3 of the extraction procedure: name the
contract, inventory both products, and select the source implementation with the
strongest matching behavioural proof.

This is a comparison document. No code, schema, or migration changed to produce
it, and it does not by itself approve any extraction — the two-consumer,
same-contract, named-owner and cutover gate still applies.

**Its outcome is accepted** as ADR-0006 § "Decision amendment — 2026-08-10 (audit
scope, and the communication capability map)". This document is the evidence; the
ADR is the decision. Where they disagree, the ADR wins and this file is stale.

## Method — what product-first does and does not decide

Product-first extraction is an **implementation-sourcing** rule: when the
foundation needs a capability, prefer the production-used implementation with the
strongest behavioural proof over rebuilding it. It is *not* a **scope-definition**
rule. What the foundation needs is set by ADR-0003 — this repo is the strategic
foundation for new SaaS, dedicated, self-hosted, OEM and single-tenant
deployments — not by what ERP and Sub happen to have built.

The distinction matters here because reading the two backwards produces a wrong
answer. "These two implementations do not share a contract" is a finding about
*implementations*. "There is no contract in this space" is a claim about the
*capability*, and it does not follow. This audit reaches the first conclusion and
explicitly declines the second: the outcome below therefore has two parts, one
answering *"can this package own both products' templates?"* and one answering
*"what does the foundation actually need here, and who already built it?"*

## Headline finding

**The two products' implementations do not share a contract, so the package's
`kind ∈ {notification, document}` merge cannot stand.** ERP document templates
and Sub notification templates share the word "template" and almost nothing a
single owner could hold. They disagree on the placeholder *language*, on the
rendering *engine class*, on the identity *dimension*, on what a missing variable
*means*, and on what the render produces.

The three placeholder contracts are mutually exclusive today:

| | Syntax | Engine | Missing variable |
|---|---|---|---|
| **Sub** | `{var}` — single brace, closed allowlist per send context | `str.replace` loop over context keys | live event path **suppresses the send**; preview leaves it literal; save time **rejects** unknown names |
| **ERP** | `{{ var }}`, `{% if %}`, `\| format_date`, inline HTML/CSS | Jinja2 `SandboxedEnvironment`, `autoescape=True` | `Undefined` → renders empty, never rejected |
| **Studio** | `{{ var }}` — double brace, substitution only | `re.sub`, deliberately *not* a template engine | `strict=True` raises; flag off leaves it literal |

The collision is not stylistic. Sub **actively rejects double braces at save
time** (`notification_template_renderer.validate_template_text`) because that
syntax previously leaked a literal `{{amount}}` to customers — the live renderer
fills single braces only. Double braces are Template Studio's *only* syntax. And
Studio cannot render `{% if %}`, `| format_date`, or the HTML letter wrapper that
every ERP letter body is built from; those would pass through verbatim into a
PDF.

So the package as built is a third design that is neither product's, adopted by
neither, and unable to hold either product's existing rows. That is the
similarity-driven merge ADR-0006 § 5 forbids.

What this does **not** show is that templating-and-sending has no extractable
contract. It shows that one table with a `kind` discriminator is the wrong shape
for it. The capability a new product needs on day one — author a message, render
it safely, decide whether it may be sent, choose a channel, send it, prove what
was sent — is real, coherent, and already built: Sub implements five of those six
decisions and ERP implements three. The correct unit is several modules with one
named owner each, not one package and not one merge.

## Dimension-by-dimension comparison

The audit dimensions are the ones `module-extraction-sources.md` mandates.

### Stable identity

| | Key | Vocabulary |
|---|---|---|
| Sub | `(code, channel)` unique — no tenant column | `NotificationChannel`, 10-member Python enum; `code` normalised `lower()` + spaces→`_` |
| ERP | `(organization_id, template_type, template_name)` unique | `TemplateType`, **43-member native PostgreSQL enum** `document_template_type` spanning finance, HR, payroll and projects |
| Studio | `(tenant_id, kind, slug)` unique | `kind`, 2-value CHECK constraint; `slug` regex-validated as the caller-facing address |

**Sub keys by channel; ERP keys by document type; Studio keys by slug and
demotes channel to a nullable, non-unique `String(20)` attribute.** Sub's seeder
depends on the channel dimension — it seeds the referral-reward template for
`push` *and* `email` under one code — which Studio's identity model cannot
represent. This is a structural blocker, not a migration detail.

ERP's 43-member native enum is the same ADR-0008 non-conformance as the
`SettingDomain` enums the settings cutover is currently repairing, and needs the
same `ALTER TYPE`-avoiding repair.

### Versioning and immutability

- **Sub:** none. `NotificationTemplate.body` is overwritten in place. No history.
- **ERP:** `version` INTEGER, `template.version += 1` on every edit
  (`services/finance/automation/web.py:1267`) while `template_content` is
  overwritten. `GeneratedDocument.template_version` records *"generated with
  v7"* — **but v7's content no longer exists anywhere**, so the traceability
  record names a revision that cannot be reproduced. The rendered artifact
  survives (`file_path`, `content_hash`, `context_snapshot`); the template does
  not.
- **Studio:** real immutable revisions, a published pointer, and
  `update_version` refuses to edit a published revision.

Studio is the only implementation with the versioning contract, which means
**there is no production-proven source to port on this dimension** — and neither
product's code has ever asked for it. If versioning survives the contract
decision, the port source is ERP's *workflow-rule* version history
(`get_rule_versions`: `version_number`, `change_summary`, `changed_by`), which is
proven code applied to the wrong entity, not a greenfield design.

### Publish / activation semantics

- **Sub:** `is_active` only. Selection is `(code, channel)` plus a JSON
  `conditions` match.
- **ERP:** `is_active` + `is_default` per (org, type). **`is_default` carries only
  an index, no unique constraint**, and `get_template` does
  `order_by(is_default.desc())` then `db.scalar()` — with two defaults the winner
  is arbitrary. Real defect; do not carry forward.
- **Studio:** `is_active` + `published_version`, with `manage` and `publish` split
  as separate permissions.

### Channels and document kinds

Sub: 10 channels, of which email/SMS/WhatsApp have live readiness checks and
delivery paths; WhatsApp bodies are **provider-owned markers**, not authored text
(`sync_whatsapp_registry_templates` mirrors Meta-approved templates into rows).
ERP: 43 document/email types, PDF via WeasyPrint plus page size, orientation,
margins, header/footer JSONB and CSS. Studio: 2 kinds, one nullable channel
string, no output format concept.

A shared vocabulary that must hold 43 ERP types *and* 10 Sub channels *and* stay
open for the next product is a declaration registry, not an enum or a CHECK
constraint — ADR-0008 again. All three implementations get this wrong today.

### Tenant / organization scoping

- **Studio:** `tenant_id NOT NULL`, composite `(tenant_id, template_id)` FK, RLS
  `ENABLE` + `FORCE` with grants, from migration `ts_0001`. Strongest of the
  three by a wide margin.
- **ERP:** `organization_id` column plus service-level `WHERE`. **No RLS policy
  exists on `automation.document_template` or `automation.generated_document`** —
  isolation is convention, in a codebase that does have RLS elsewhere. Separately,
  `mark_as_sent` / `mark_as_final` / `supersede_document` take no organization
  argument and mutate by primary key; today they are reached only with an
  internally-derived id (one caller, `offer_letter_service.py:369`), so this is a
  latent gap rather than a live exposure.
- **Sub:** none — single-tenant deployment by design.

This is the one dimension where the starter is unambiguously the qualifying
implementation.

### Seeding

- **Sub:** `_seed_missing_notification_templates` — upsert by `(code, channel)`,
  creates if missing, **never clobbers an operator's edit**, and derives the
  referral-reward template from the executable `EVENT_NOTIFICATION_SPECS` so the
  editable DB row cannot fork from the canonical spec. Plus provider-registry sync
  for WhatsApp.
- **ERP:** `seed_hr_letter_templates` / `HRLetterService._ensure_default_templates`
  create per-org rows from `default_letter_templates.py`.
- **Studio:** **none.** No mechanism for a module or product to declare a default
  template at all.

Both products need seeding on day one, and Sub's is the better source:
upsert-by-identity, never-clobber, derived-from-executable-spec.

### Delivery integration

- **Sub:** large and load-bearing — `CommunicationIntentRecord` (durable reason,
  policy context, `dedupe_key`, `suppression_reasons`), `Notification` (queue,
  retry, status), `NotificationDelivery` (provider message ids, bounce/reject),
  `CommunicationSuppression` (the consent ledger whose whole point is that
  unsubscribe means *marketing only* and is never permission to stop sending an
  invoice), a channel-policy matrix, and per-channel readiness checks.
- **ERP:** `generate_email` returns `(subject, body, from_name)` — rendering only;
  delivery lives elsewhere.
- **Studio:** `render_published` returns `(subject, body)`. No delivery.

**The shared contract must stop at rendering.** Studio's return shape is right and
matches ERP's. Sub's intent/suppression/channel-policy machinery is
subscriber-consent and network policy — product domain a shared module must not
absorb — but it is also what makes Sub's notifications trustworthy, so it needs
its own dossier rather than riding on this one.

### Generated-document traceability

ERP only, and it is the strongest thing ERP has here: `GeneratedDocument` with
`content_hash`, `context_snapshot`, `file_path`, `document_number`, polymorphic
`(entity_type, entity_id)`, and a `DRAFT → FINAL / SENT / SUPERSEDED / VOIDED`
lifecycle with `superseded_by`. Ten integration tests cover it.
`_sanitize_context_for_snapshot` (drops any key containing password / secret /
token / key / credential before persisting) is product-neutral and worth porting
on its own merits regardless of what happens to the rest.

Sub's analogue is `Notification` + `NotificationDelivery`. Studio has nothing.

### Permissions

- **ERP:** a single coarse `require_finance_access` on every document-template
  route — **including HR offer, termination and warning letters**. Wrong owner: an
  HR letter template is not finance data.
- **Sub:** site-wide admin web routes.
- **Studio:** four declared permissions plus a `template_studio.use` capability,
  with `manage` and `publish` deliberately split — the only implementation that
  expresses *"authoring a draft and changing what customers receive are different
  decisions."*

### Migration and cutover shape

| | Namespace | Lineage | Cutover cost |
|---|---|---|---|
| ERP | `automation` | shared Alembic | 43-member native enum needs the `ALTER TYPE`-avoiding repair; no RLS to preserve; rich-document columns have no target |
| Sub | `public` | shared Alembic | no tenant column → backfill a single tenant row (a topology per ADR-0003, fine); **every template body and every seeded spec would need a syntax rewrite** |
| Studio | `mod_tstudio` | own lineage | RLS from revision 1 |

Sub's body rewrite is the expensive one and it is the wrong direction: it would
re-introduce the exact syntax that leaked to customers.

## Behaviour proofs available to port

All seven paths named in the dossier exist and were verified at the commits above.

| Repository | Test | Tests | What it proves |
|---|---|---|---|
| Sub | `tests/test_notification_template_renderer.py` | 7 | the single-brace contract, double-brace rejection, unknown-name rejection, and that validation is **context-aware** (automated vs bulk) |
| Sub | `tests/test_settings_seed_services.py` | 49 | seeding, including upsert-by-identity |
| Sub | `tests/test_email_services.py` | 24 | email delivery integration |
| ERP | `tests/integration/services/test_document_generator.py` | 10 | template selection, HTML render, context-snapshot sanitisation, PDF generation, and the sent/final/superseded lifecycle |
| ERP | `tests/services/test_workflow_engine.py` | 41 | workflow engine incl. rule version history |
| ERP | `tests/e2e/test_automation.py` | 33 | end-to-end automation |
| Starter | `tests/architecture/test_template_studio_module.py` | — | the module's structural contract |

The 7 Sub renderer tests are the tightest behavioural proof of a template
contract in either product, and they are exactly the tests that encode the defect
Studio's syntax would reopen.

## Audit outcome, part 1 — the package as built

**Template Studio is not qualified as a reusable owner of both kinds, and no
single source implementation is selected for the merged contract, because that
contract does not hold.** Narrowly:

1. **`kind=document` cannot be served by this package.** ERP documents need Jinja
   control flow, filters, HTML and page geometry; Studio forbids a template engine
   as a deliberate security posture. Reconciling those is a rewrite of one side,
   not a port. Drop documents from the package's scope.
2. **The notification half has a real, narrow contract, and Sub is the source
   implementation** — single-brace substitution, a closed per-send-context
   placeholder allowlist validated at save time, unresolved-placeholder
   suppression on the live path, and upsert-by-identity seeding derived from
   executable specs. That is the code and the 7 tests to port first. The package
   must re-base its renderer on that contract; keeping double braces would
   re-introduce the exact defect Sub fixed.
3. **Three starter contributions survive into the re-based module**, because
   nothing in either product has them: tenant-scoped RLS from migration 1, the
   `manage` / `publish` permission split, and immutable versioning (ported from
   ERP's proven workflow-rule version history, not designed fresh).

## Audit outcome, part 2 — the capability map

The capability the foundation needs is larger than templating, and is mostly
already built in Sub. Six decisions, six owners. Template Studio is one of them,
not the container for the rest.

| Owner | The one decision it owns | Qualifying source | Proof available |
|---|---|---|---|
| **template studio** | what the message *says* | Sub's renderer (single-brace, allowlisted) | 7 renderer tests |
| **consent / suppression** | may we contact this address on this channel | Sub's `CommunicationSuppression` | seed + email suites |
| **channel policy** | which channels carry this class of message | Sub's matrix — **mechanism only** | partial |
| **delivery / outbox** | queue, retry, provider result, dedupe | Sub's `Notification` + `NotificationDelivery` | `test_email_services.py` (24) |
| **document generation** | render → PDF → durable record | ERP's `DocumentGeneratorService` + `GeneratedDocument` | 10 integration tests |
| **product domain** | *which* documents/notifications exist and what they mean | stays in ERP and Sub | — |

Three of these were nearly mis-scoped as product-specific. They are not:

- **Consent is a legal decision, not an ISP one.** Erasure requests, hard bounces
  and unsubscribe apply to every product that sends email, including an ERP that
  only ever sends invoices and offer letters. Sub already solved the hard part —
  scope `marketing` vs `all`, keyed on the **address** rather than the person,
  with the explicit rule that unsubscribe is never permission to stop sending
  someone their invoice. A second product reimplementing that is precisely the
  build-once violation the foundation exists to prevent, and the failure mode is
  a billing incident rather than a cosmetic drift.
- **`GeneratedDocument` is the only concrete implementation of the SOT criterion
  *"every projection has provenance"* in either product** — `content_hash`,
  sanitised `context_snapshot`, `superseded_by` lifecycle. That is foundation
  material. WeasyPrint's native dependencies (cairo, pango) are an argument for
  shipping it as an optional `core=False` module with its own extra, not an
  argument for leaving it in ERP.
- **Channel policy generalises at the mechanism, not the vocabulary.** Event class
  → channel set, operator-editable, with a system default, is generic; Sub's
  categories are not. Same declaration-registry split ADR-0008 mandates, and the
  same split this audit already recommends for the 43-member `TemplateType` enum.
  This is the weakest of the three — the mechanism is entangled with subscriber
  and reseller notions in a way the other two are not.

**Sequencing is a constraint, not a preference: consent must be answerable before
delivery exists.** Otherwise the first product to send on the new stack does it
without a suppression check, and the legal gate gets retrofitted behind live
traffic.

### Boundaries each owner must not cross

- Template studio renders and returns `(subject, body)`. It does not decide
  whether to send, to whom, or over what.
- Consent answers one question about an address and a channel. It does not queue,
  route, or render.
- Delivery does not decide eligibility — it asks consent.
- Document generation produces an artifact and its provenance record. It does not
  own what an invoice or an offer letter *means*.
- **ERP finance/HR entity semantics and Sub subscriber/network policy stay in the
  products.** This is the one genuine out-of-scope item.

## What this unblocks

Notifications proceeds against part 1 (2) and the capability map: the contract is
named, the source implementation selected, boundaries explicit, and the ordering
constraint stated. Each row above needs **its own `EXTRACTION.toml`** rather than
inheriting Template Studio's — they are separate contracts with separate
consumers, and bundling them under one dossier would repeat the merge error this
audit exists to catch.

## Product defects found (report regardless of extraction)

These are as-built defects in the audited source, independent of whether anything
is extracted. None is a starter change.

1. **ERP** — `document_template.is_default` has an index but no unique
   constraint, and `get_template` picks by `order_by(is_default.desc())` +
   `scalar()`. Two defaults for one (org, type) give an arbitrary winner.
2. **ERP** — `automation.document_template` and `automation.generated_document`
   have no RLS policy; organization isolation rests on service-level `WHERE`
   clauses. The `GeneratedDocument` status mutators additionally take no
   organization argument (latent: one internal caller today).
3. **ERP** — `document_template_type` is a 43-member native PostgreSQL enum: the
   same ADR-0008 non-conformance, and the same repair recipe, as the
   `SettingDomain` enums in the settings cutover.
4. **ERP** — editing a template overwrites `template_content` while bumping
   `version`, so `GeneratedDocument.template_version` names a revision whose
   content no longer exists. The traceability record is not reproducible.

## Not covered

Campaign/marketing templates in either product; ERP's `recurring` transaction
templates (a different "template" entirely, sharing only the word); the vendor
control plane and `dotmac_crm`, which were not in the dossier's scope.
