# Content, documents and analytics

- Short explanations of package responsibilities, not a claim of production readiness.
- Check the [catalog](../MODULE_CATALOG.md) for installation, release and adoption evidence.
- A **tenant** is a customer workspace; the **platform** is the operator control plane.
- [All module groups](index.md).

## Files (`dotmac-files`)

- **What it does:** Validates stored files and manages physical storage lifecycle and repair evidence.
- **Use it when:** Storing and retrieving opaque file content safely.
- **Keep in mind:** It does not decide what an invoice, record or document means.
- **Details:** [Package guide](../../packages/dotmac-files/README.md) · [Source and contract](../../packages/dotmac-files/EXTRACTION.toml).

## Documents (`dotmac-documents`)

- **What it does:** Manages controlled document identities, versions and document lifecycle.
- **Use it when:** Maintaining approved versions independently of filenames and folders.
- **Keep in mind:** File bytes and retention authority remain separate.
- **Details:** [Package guide](../../packages/dotmac-documents/README.md) · [Source and contract](../../packages/dotmac-documents/EXTRACTION.toml).

## Records (`dotmac-records`)

- **What it does:** Applies record declarations, retention schedules, holds and disposition rules.
- **Use it when:** Preserving or disposing of governed business records.
- **Keep in mind:** A file is not automatically a declared record; retention needs explicit policy.
- **Details:** [Package guide](../../packages/dotmac-records/README.md) · [Source and contract](../../packages/dotmac-records/EXTRACTION.toml).

## Document rendering (`dotmac-document-rendering`)

- **What it does:** Turns fixed business facts and selected templates into HTML or PDF output.
- **Use it when:** Producing reproducible issued documents.
- **Keep in mind:** It does not look up live business data, store files or decide invoice meaning.
- **Details:** [Package guide](../../packages/dotmac-document-rendering/README.md) · [Source and contract](../../packages/dotmac-document-rendering/EXTRACTION.toml).

## Template studio (`dotmac-template-studio`)

- **What it does:** Authors, versions, publishes and renders notification and document templates.
- **Use it when:** Letting tenants manage reusable message templates.
- **Keep in mind:** Rendering a message does not send it or schedule a campaign.
- **Details:** [Package guide](../../packages/dotmac-template-studio/README.md) · [Source and contract](../../packages/dotmac-template-studio/EXTRACTION.toml).

## Forms (`dotmac-forms`)

- **What it does:** Versions forms and validates submissions against a published definition.
- **Use it when:** Collecting structured input with traceable form versions.
- **Keep in mind:** A submitted answer is evidence, not automatic authority for a business change.
- **Details:** [Package guide](../../packages/dotmac-forms/README.md) · [Source and contract](../../packages/dotmac-forms/EXTRACTION.toml).

## Surveys (`dotmac-surveys`)

- **What it does:** Manages survey questions, invitations, responses and aggregate feedback.
- **Use it when:** Collecting customer or participant feedback.
- **Keep in mind:** It does not own the customer lifecycle or general campaign delivery.
- **Details:** [Package guide](../../packages/dotmac-surveys/README.md) · [Source and contract](../../packages/dotmac-surveys/EXTRACTION.toml).

## Content (`dotmac-content`)

- **What it does:** Manages editorial plans, content items, variants and creative references.
- **Use it when:** Preparing and organizing content before publication.
- **Keep in mind:** Campaign audiences and publication outcomes are separate.
- **Details:** [Package guide](../../packages/dotmac-content/README.md) · [Source and contract](../../packages/dotmac-content/EXTRACTION.toml).

## Sites (`dotmac-sites`)

- **What it does:** Manages website pages, revisions, navigation and local release snapshots.
- **Use it when:** Assembling a versioned website for release.
- **Keep in mind:** It does not provide domain registration, hosting or publication transport.
- **Details:** [Package guide](../../packages/dotmac-sites/README.md) · [Source and contract](../../packages/dotmac-sites/EXTRACTION.toml).

## Publishing (`dotmac-publishing`)

- **What it does:** Tracks publication requests, attempts and reconciled delivery outcomes.
- **Use it when:** Publishing prepared content to selected targets.
- **Keep in mind:** It does not author the source content or implement provider clients.
- **Details:** [Package guide](../../packages/dotmac-publishing/README.md) · [Source and contract](../../packages/dotmac-publishing/EXTRACTION.toml).

## Campaigns (`dotmac-campaigns`)

- **What it does:** Manages campaigns, frozen audiences and ordered recipient progression.
- **Use it when:** Coordinating outbound campaigns and nurture sequences.
- **Keep in mind:** Consent policy and provider delivery are supplied by other owners.
- **Details:** [Package guide](../../packages/dotmac-campaigns/README.md) · [Source and contract](../../packages/dotmac-campaigns/EXTRACTION.toml).

## Media observations (`dotmac-media-observations`)

- **What it does:** Records external media facts, hierarchy and period metrics with source evidence.
- **Use it when:** Keeping reproducible observations from external media systems.
- **Keep in mind:** Observed metrics do not authorize publishing or change local content ownership.
- **Details:** [Package guide](../../packages/dotmac-media-observations/README.md) · [Source and contract](../../packages/dotmac-media-observations/EXTRACTION.toml).

## Analytics (`dotmac-analytics`)

- **What it does:** Builds aggregate metrics and rebuildable projections from declared domain facts.
- **Use it when:** Reporting consistently across domain-owned metric definitions.
- **Keep in mind:** It does not replace the source domain's authoritative records.
- **Details:** [Package guide](../../packages/dotmac-analytics/README.md) · [Source and contract](../../packages/dotmac-analytics/EXTRACTION.toml).

## Web analytics (`dotmac-web-analytics`)

- **What it does:** Records privacy-admitted first-party website events and analytical projections.
- **Use it when:** Measuring website behavior with declared event and privacy rules.
- **Keep in mind:** It is distinct from general business analytics and external media observations.
- **Details:** [Package guide](../../packages/dotmac-web-analytics/README.md) · [Source and contract](../../packages/dotmac-web-analytics/EXTRACTION.toml).

## Ai operations (`dotmac-ai-operations`)

- **What it does:** Records AI policy, execution attempts, advisory insights and acknowledgement evidence.
- **Use it when:** Using AI with traceable inputs, outcomes and human decisions.
- **Keep in mind:** AI advice does not itself authorize a business action; provider I/O remains external.
- **Details:** [Package guide](../../packages/dotmac-ai-operations/README.md) · [Source and contract](../../packages/dotmac-ai-operations/EXTRACTION.toml).

## Compliance reporting (`dotmac-compliance-reporting`)

- **What it does:** Assembles regulatory evidence packs and tracks filing lifecycle.
- **Use it when:** Preparing traceable reporting evidence for a period and jurisdiction.
- **Keep in mind:** A complete evidence pack is not a guarantee of legal compliance.
- **Details:** [Package guide](../../packages/dotmac-compliance-reporting/README.md) · [Source and contract](../../packages/dotmac-compliance-reporting/EXTRACTION.toml).

## Imports (`dotmac-imports`)

- **What it does:** Tracks file imports, validation previews, approved apply runs and resumable progress.
- **Use it when:** Importing business data with a review step and reliable retries.
- **Keep in mind:** The destination domain owns field rules and actual business mutations.
- **Details:** [Package guide](../../packages/dotmac-imports/README.md) · [Source and contract](../../packages/dotmac-imports/EXTRACTION.toml).
