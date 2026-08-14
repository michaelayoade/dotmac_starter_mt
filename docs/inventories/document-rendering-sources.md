# Document rendering — extraction sources

Dated 2026-08-14. As-built characterisation of what already exists in the
fleet, written before any rendering code, per hard rule 24 (product-first
extraction). **Facts, not mandates**, and explicitly not a licence to extract
(ADR-0006 § "The extraction rule").

The target is capability area **P8a** in
`docs/inventories/billing-sources.md` § 3 — *"Document rendering, a
document-generation owner separate from billing and Template Studio"* — which
that table records as **missing** and **gap-listed**. ADR-0017 decision 2's
moratorium holds it; nothing here is authorized.

Scope: **invoice, credit-note, receipt and statement** rendering. Quote
documents and regulatory report packs are characterised where they are the
strongest available evidence, and are explicitly marked out of the first slice.

Contracts and invariants:
`docs/superpowers/specs/2026-08-14-document-rendering-contracts.md`.
Dossier content: `docs/inventories/document-rendering-extraction-dossier.md`.

---

## 0. The boundary, and the two documents that already fix it

**Template Studio is not the invoice-content authority, and this is already
checked in.** `docs/inventories/template-studio-source-audit.md` § *Audit
outcome, part 1* concludes:

> **`kind=document` cannot be served by this package.** ERP documents need
> Jinja control flow, filters, HTML and page geometry; Studio forbids a
> template engine as a deliberate security posture. Reconciling those is a
> rewrite of one side, not a port. **Drop documents from the package's scope.**

Its part-2 capability map then lists **document generation** as a *separate*
owner (*"render → PDF → durable record"*, source: ERP's
`DocumentGeneratorService` + `GeneratedDocument`, 10 integration tests) with
the boundary *"Document generation produces an artifact and its provenance
record. It does not own what an invoice or an offer letter means."* The same
section directs each capability-map row to carry **its own `EXTRACTION.toml`**
rather than inheriting Template Studio's. This inventory is that row's
evidence.

**`dotmac-files` owns bytes, not documents.** ADR-0022 § 3 is explicit:
*"Generated PDFs and documents belong to their rendering/document domain; the
result may be stored through `dotmac-files` after it is generated."*
ADR-0020 A5 completes the triangle: billing *"emits immutable document facts
and stops. The assembly connects a rendering owner (P8a, still a genuine gap)
to `dotmac-files`; billing does not import `dotmac-files`, per A1."*

So the fleet has already decided three of the four owners. The missing one is
this workstream.

---

## 1. What Sub renders today

Source repository: `/Users/michaelayoade/Downloads/management/dotmac_sub`
(readable; `.claude/worktrees/` copies were excluded as stale). Every path and
line number below was read.

| Document | Renderer | Bytes persisted? | Template artifact? | Tests |
|---|---|---|---|---|
| Invoice | `app/services/billing_invoice_pdf.py` (1435 lines) | yes — `StoredFile` **and** raw S3 **and** local disk | **no** — an f-string in Python | 15 |
| Payment receipt | `app/services/billing_payment_receipts.py` | **no** — inline `Response` only | **no** — f-string | 4 |
| Account statement | `app/services/web_billing_statements.py` | **no** — HTMX fragment, CSV, e-mail body | n/a — no PDF exists | 6 |
| **Credit note** | **none** | — | — | **0** |
| Quote (adjacent) | `app/services/sales/quote_documents.py` | yes — `StoredFile` via `stage_upload` | **no** — f-string | 14 |
| NCC regulatory pack (out of scope) | `app/web/admin/reports.py:1887-1924` | no | no | 1 partial |

Two facts about that table are load-bearing.

**There is no template artifact anywhere in Sub.** Every renderer builds its
HTML as a Python f-string with inline `<style>` — `_render_invoice_html`
occupies `billing_invoice_pdf.py:295-464`, of which roughly 75 lines are CSS.
There is nothing to version, nothing to select, and nothing a non-engineer can
change. The nearest thing to a template version is a hand-edited module
constant:

```python
# app/services/billing_invoice_pdf.py:51
INVOICE_PDF_TEMPLATE_REFRESHED_AT = datetime(2026, 3, 18, 9, 0, tzinfo=UTC)
```

Every stored PDF completed before that timestamp is treated as stale
(`_is_export_fresh`, `:945-955`). That is the entire template-versioning
mechanism in the product.

**Credit notes have no document at all.** `credit_note` intersected with
`pdf|render|html|weasyprint|write_pdf` returns zero hits across `app/`. There
are admin forms (`templates/admin/billing/credit_form.html`,
`credit_apply_confirm.html`, `credit_issue_confirm.html`, `credits.html`) and a
list-facet row (`web_billing_documents.py:98-119`), and nothing a customer can
be handed. `CreditNote.credit_number` (`app/models/billing.py:936`) is nullable
and **no code anywhere assigns it** — there is no `next_credit_number`
generator to match `next_invoice_number`. Since ADR-0020 § 1 puts credit notes
squarely in `dotmac-billing`'s authoritative scope, and since the accepted
correction mechanism for an issued invoice *is* a credit note
(`2026-08-14-billing-vendor-cp-sub-cutover.md` § *Documents and coverage*),
this is the largest single hole in the fleet's document surface.

### 1.1 Invoice PDF — entry points and control flow

| Entry point | File | Behaviour |
|---|---|---|
| Customer portal | `app/web/customer/routes.py:783-885` | Three branches: stream the cached export; else render inline via `generate_export_now`; else `queue_export` and 303-redirect with `?pdf_notice=generating`. Both streaming failures are swallowed at `logger.debug` (`:840-845`, `:869-874`). |
| Admin actions | `app/services/web_billing_invoice_actions.py:51,64,78,102-124` | download, regenerate, cache hit/miss recording |
| Celery task | `app/tasks/invoice_pdf.py:16` | `billing_invoice_pdf.process_export(export_id)` |
| Cache admin | `app/services/web_billing_invoice_cache.py:42,130,135` | dashboard stats, `clear_cache` |
| E-mail attachment | `app/services/communication_attachments.py:114` | `generate_export_now` + `stream_export` |
| Bulk | `bulk_queue_pdf_exports` | classifies `ready` / `queued` / `missing` |

State lives in `InvoicePdfExport` (`app/models/billing.py:856-889`):
`status ∈ {queued, processing, completed, failed}`, `celery_task_id`,
`file_path`, `file_size_bytes`, `error`, `completed_at`. **No checksum column,
no media type, no renderer version, no template version.**

---

## 2. Where rendering recalculates something billing owns

Each of these is a second writer of *meaning*, and each is a reason this
module exists. Reported honestly, including where the answer is "it does not".

### 2.1 The invoice renderer does **not** recompute money — record that plainly

`_money` (`billing_invoice_pdf.py:85-87`) is `f"{amount:,.2f}"` over a value
coerced with `Decimal(str(...))`. Every figure on the invoice PDF is read from
a persisted column: `invoice.subtotal`, `.discount_amount`, `.tax_total`,
`.total`, `.balance_due` (`:442-445`, `:411`) and per line `line.quantity`,
`.unit_price`, `.amount` (`:228-231`). No `sum()`, no addition. This is the
best-behaved renderer in the set, and a port should not "fix" it.

**But it prints a coverage quantity on an issued document, and regenerates
whenever the row moves.** `balance_due` appears as the hero figure — *"Balance
Due"*, `billing_invoice_pdf.py:411` and again at `:719` in the PIL path — and
`_is_export_fresh` invalidates the stored artifact whenever
`invoice.updated_at` passes `export.completed_at`:

```python
# app/services/billing_invoice_pdf.py:945-955
def _is_export_fresh(invoice: Invoice, export: InvoicePdfExport) -> bool:
    ...
    if export.completed_at < INVOICE_PDF_TEMPLATE_REFRESHED_AT:
        return False
    invoice_updated = invoice.updated_at or invoice.created_at
    if invoice_updated and export.completed_at < invoice_updated:
        return False
```

So "the invoice PDF" is not an artifact of an issuance event — it is a
regenerated live view of the current invoice row. Download the same invoice
before and after a payment lands and the two PDFs disagree about what the
customer owes, and both claim to be *the* invoice number they carry. That is
the immutability defect the target contract closes (contracts spec § 8, I1),
and it is a second writer of *meaning* even though no arithmetic happens.

### 2.2 The receipt renderer recomputes allocation at render time — the flagship finding

`billing_payment_receipts.py:127` calls
`billing_service.payments.application_summary(db, payment)`, which recomputes
on every single render:

```python
# app/services/billing/payments.py:3865-3885
amount_received = round_money(to_decimal(payment.amount))
invoice_amount_applied = round_money(
    sum(
        (to_decimal(allocation.amount)
         for allocation in payment.allocations
         if allocation.is_active),
        Decimal("0.00"),
    )
)
...
unallocated_credit=round_money(
    max(Decimal("0.00"), amount_received - invoice_amount_applied)
),
```

and, one hop further:

```python
# app/services/billing/payments.py:3821-3828
return max(
    Decimal("0.00"),
    round_money(
        to_decimal(settlement.unallocated_amount)
        - prepaid_consumed
        - to_decimal(consumed)
    ),
)
```

plus `_payment_prepaid_service_amount` (`:3831-3852`), a `sum()` over renewal
events combined with `max(settlement_amount, outcome_amount)`.

**A receipt is the customer's evidence of what happened on a date.** Four of
its five figures — Amount Credited, Applied to Invoices, Applied to Service,
Account Credit Remaining (`billing_payment_receipts.py:308-312`) — are derived
from live allocation rows at the moment of printing. Reallocate that payment
six months later, reprint the receipt, and it says something different about a
past event, under the same reference. Nothing stores what the receipt said the
first time, because the receipt is never persisted at all (§ 3).

The renderer also carries three copies of the same coercion rule
(`:211-214`, `:335-338`, `:412-415`), one per fallback path.

### 2.3 The statement service recomputes everything, and rounds differently

`web_billing_statements.py` derives opening balance, per-currency period delta,
closing balance and every running balance from ledger rows on each request:

```python
# app/services/web_billing_statements.py:120-129
for entry in entries:
    currency = display_format.currency_code(entry.currency)
    amounts[currency] = amounts.get(currency, Decimal("0.00")) + _signed_amount(entry)
```
```python
# :168-175
closing_balance=(closing := opening + period),
# :183-189
running_balance = running_by_currency.get(currency, Decimal("0.00")) + signed
```

with the sign flip in `app/services/customer_financial_ledger.py:70-73`.

**There is no `round_money` or `quantize` anywhere in this file**, unlike the
receipt path which rounds at every step. Two money surfaces in the same product
therefore round differently. This is exactly the divergence ADR-0016 already
measured for coverage (`docs/inventories/billing-sources.md` § 4.2: *"twelve
sites, seven rules"*), reproduced in the presentation layer.

A statement is arguably a report rather than a document, and this is the
strongest argument for keeping statements **out of the first slice**
(contracts spec Q3): its authoritative input is a *period*, not an issued
document, so it needs a different producer contract from
`InvoiceDocumentFactV1`.

### 2.4 A renderer invents a document number

```python
# app/services/billing/payment_receipt_identity.py:8-15
def payment_receipt_reference(payment_id, stored_receipt_number=None) -> str:
    stored = str(stored_receipt_number or "").strip()
    if stored:
        return stored if stored.startswith("#") else f"#{stored}"
    return f"#RCP-{payment_id.hex[:8].upper()}"
```

`Payment.receipt_number` is declared (`app/models/billing.py:1324`) and
**never written by anything in `app/`** — every reference is a read. Line 15
is therefore the path taken for every receipt ever issued: a 32-bit value
derived from a UUID prefix, with no sequence, no uniqueness constraint, no
gapless policy and no persistence. Two payments colliding on eight hex
characters produce two receipts with the same reference.

Contrast the invoice path, which does it correctly and outside every renderer:
`next_invoice_number(db)` (`app/services/billing/invoices.py:303`, called from
`invoices.py:706,1519,2123`, `billing_automation.py:1902,2277`,
`crm_api.py:1481`) and `numbering.generate_required_number`
(`invoices.py:2246`, `invoice_draft_authoring.py:811`).

Softer variants of the same mistake — substituting a UUID prefix where a
number belongs — appear at `billing_invoice_pdf.py:64,242,256,535,680`
(`invoice.invoice_number or str(invoice.id)`),
`billing_payment_receipts.py:145-146` (`f"INV-{str(...)[:8].upper()}"`), and
`web_billing_documents.py:63,86,109`.

**This is P4's work, not rendering's.** `docs/inventories/billing-sources.md`
P4 records document numbering as gap-listed with *"ERP has five
implementations today"*; Sub adds a sixth here, inside a renderer.

### 2.5 A renderer resolves live settings that should have been snapshotted

Three values are looked up at render time and therefore change retroactively on
every re-render:

| Value | Resolved at | Consequence |
|---|---|---|
| Seller legal name, address, e-mail, phone | `billing_invoice_pdf.py:247-254` — `resolve_brand(db, subscriber_id=…)` + `company_info_service.get_company_info(db)` | a rebrand rewrites every historical invoice |
| Bank details (name, account name, account number, sort code) | `:266` and again `:865` — `invoice_bank_details_service.get_invoice_bank_details(db, currency=…)` | **re-printing a two-year-old invoice prints today's bank account** |
| Logo | `:141-167` — `settings_spec.resolve_value(db, SettingDomain.comms, "sidebar_logo_url")` | ditto |

The bank-details case is a money-misdirection defect, not a cosmetic one. It is
why the contracts spec asks billing for a `payment_instructions` snapshot
(spec § 5, R1) rather than proposing that rendering read settings.

Sub's quote path already solved this — `_snapshot` freezes bank details into an
immutable `QuoteDocumentSnapshot` and **fails closed** without them
(`quote_documents.py`, proven by
`test_quote_document_fails_closed_without_eligible_bank_details`). The pattern
exists in the product; it was never applied to invoices.

---

## 3. Where rendering writes bytes through its own storage path

`file_uploads` is Sub's storage owner — the `UnifiedFileUploadService`
singleton at `app/services/file_storage.py:555` (`upload` 311, `stage_upload`
370, `stream_file` 498). `docs/inventories/files-sources.md` and ADR-0022 name
it as a qualifying source for `dotmac-files`.

**The invoice renderer writes through it and then reads around it, three
ways:**

| Path | Line | What it is |
|---|---|---|
| `file_uploads.upload(domain="generated_docs", entity_type="invoice_pdf_export", …)` | `billing_invoice_pdf.py:1383` | the correct path |
| `Path(export.file_path).exists()` / `_stream_local_file(path)` | `:1237`, `:1331-1333` | a **local filesystem** branch |
| `get_s3_storage().exists(export.file_path)` | `:1241` | a **direct S3** branch bypassing the owner |
| `get_s3_storage().stream(export.file_path)` | `:1341` | ditto |

`export.file_path` is therefore polymorphic — a `StoredFile` storage key, an
absolute local path, or a raw S3 key — and three readers guess which. ADR-0022
§ 1 gives `dotmac-files` *"a provider binding and trusted immutable object
key"*; a column that may hold any of three things is the opposite shape, and
it is why `export_file_exists` (`:1221-1243`) has to try all three and
`maybe_finalize_stalled_export` (`:1246-1278`) exists to repair the result.

**The receipt renderer stores nothing at all.** `build_receipt_pdf` returns
bytes straight into a `Response` (`app/web/admin/billing_payments.py:679-687`,
`app/web/customer/routes.py:953-961`). Combined with § 2.2, this means the
receipt a customer downloaded is not merely un-reproducible — it was never
recorded.

**The quote renderer does it correctly**, and is the reference:
`file_uploads.stage_upload(db=…, domain="generated_docs",
entity_type="quote_pdf_export", …)` at `quote_documents.py:755`, with a
content-addressed replay guard (`:737-744`) so an identical snapshot returns
the existing `StoredFile` rather than writing a second one.

Adjacent direct-disk writers found while sweeping, listed for the `dotmac-files`
owner rather than for this workstream: `payment_proofs.py:335-338` (transfer
receipts — *financial evidence* — written to a local directory and referenced
by filesystem path), `avatar.py:46-47`, `file_upload.py:328` (a second parallel
upload service), `web_system_export_tool.py:795-802`.

---

## 4. The silent fallback cascade — the defect that most needs not to be ported

`_build_pdf_bytes` tries WeasyPrint, and on **any** exception falls through to
a Pillow-drawn bitmap PDF, which on any further exception falls through to a
hand-built minimal text PDF:

```python
# app/services/billing_invoice_pdf.py:1296-1309
def _build_pdf_bytes(db: Session, invoice: Invoice) -> bytes:
    html_content = _render_invoice_html(invoice, db)
    try:
        _ensure_weasyprint_pydyf_compat()
        from weasyprint import HTML
        return HTML(string=html_content).write_pdf()
    except Exception as exc:
        logger.info(
            "WeasyPrint export failed for invoice %s; using branded PDF fallback: %s",
            invoice.id, exc,
        )
        return _build_branded_fallback_pdf(db, invoice)
```

The three paths **truncate line items differently**:

| Path | Line-item limit | Source |
|---|---|---|
| WeasyPrint HTML | all | `:222-234` |
| Pillow bitmap | **7**, then a marker | `:788`, `:824-830` |
| Minimal text PDF | **30**, then a marker | `:522`, `:546-553` |

A customer with 40 line items receives a legally different invoice depending on
which native libraries happen to be installed on the host that served the
request, and the only signal is a `logger.info`. The same cascade exists in the
receipt renderer (`billing_payment_receipts.py:701-710`, then `:381-385`).

`app/web/admin/reports.py:1913-1918` has the most extreme form — a bare
`except Exception:` with **no logging at all**, silently returning
`text/html` to a caller that requested `.pdf`. Its inline comment claims *"No
silent fabrication"*, which is true of the content and false of the media type.

The target contract refuses this shape by name (contracts spec § 7): there is
no `fallback` outcome, and `engine_unavailable` is a retryable failure.

Related fragility worth recording: `_ensure_weasyprint_pydyf_compat`
(`:193-219`) monkey-patches `pydyf.PDF.__init__` at runtime to survive a
version skew between two transitively pinned packages, and
`quote_documents.py:520-541` is a near-byte-identical second copy of the same
shim. Two implementations of one workaround, in one repository.

---

## 5. Locale, currency and timezone — what exists, and the P9 boundary

### 5.1 There is no i18n. Stated plainly.

`grep -rn -e babel -e Babel -e gettext -e i18n` over Sub's `app/` and
`pyproject.toml` returns **zero hits**. No catalogues, no `.po`/`.mo` files, no
`Accept-Language` handling, no message IDs.

`Subscriber.locale` exists (`app/models/subscriber.py:291`, `String(16)`), is
captured in six admin/portal forms and offered as `en-NG` / `en-US` / none
(`templates/customer/profile/index.html:311-315`) — **and is read by no
rendering path anywhere.** It is a dead preference field.

### 5.2 Currency display: one canonical formatter, and three renderers that bypass it

```python
# app/services/display_format.py:24-32
_CURRENCY_SYMBOLS: dict[str, str] = {
    "NGN": "₦", "USD": "$", "EUR": "€", "GBP": "£",
    "KES": "KSh", "GHS": "₵", "ZAR": "R",
}
```

with `currency_symbol()` (`:61-64`, unknown code falls back to the code),
`format_money()` (`:72-101`), `format_currency_amount()` (`:104-114`),
`format_currency_groups()` (`:117-139`), exposed to Jinja at
`app/web/brand_globals.py:186`. It is well tested (`tests/test_display_format.py`
covers all seven symbols plus JPY code-fallback).

**None of the document renderers use it.** Four independent reimplementations:

| Site | Rule |
|---|---|
| `billing_invoice_pdf.py:49` | `NAIRA_SIGN = "₦"`, interpolated **unconditionally** into subtotal, discount, tax, total, balance due and every line (`:229-230,411,442-445`) while `invoice.currency` is echoed only as a `Currency: XXX` label (`:391`) |
| `billing_payment_receipts.py:206` | `"₦" if currency == "NGN" else f"{currency} "` |
| `templates/customer/billing/receipt.html:6` | the same rule again, in Jinja |
| `web_billing_documents.py:43` | `f"{currency or 'NGN'} {…:,.2f}"` |

**An invoice with `currency="USD"` renders `₦` amounts**, and the test suite
actively requires it: `test_render_invoice_html_uses_naira_sign` asserts
`"₦" in html`. `tests/architecture/test_display_format_ownership.py` exists to
force services onto the canonical formatter, and
`billing_invoice_pdf.py` / `billing_payment_receipts.py` are **not in its
`PILOT_SERVICES` list** — the two document renderers are exactly the surfaces
the ownership guard does not reach.

`NGN` as an identifier or default is a C5 forbidden name
(`docs/superpowers/plans/2026-08-11-billing-subscriptions-collections.md` C5).
Four sites, one of them enforced by a test.

### 5.3 Timezone: four coexisting conventions

| Convention | Where | Reads the setting? |
|---|---|---|
| db-resolved | `display_format.display_timezone(db)` / `format_timestamp(value, db)` (`:142-181`), default `Africa/Lagos` | **yes** |
| global template patch | `app/timezone.py:13-32` + `install_template_timezone_localization`, which monkey-patches `Jinja2Templates.TemplateResponse` so *every* datetime in *any* template context becomes Lagos | no — hardcoded |
| portal filters | `app/web/customer/branding.py:51-52` `_PORTAL_DISPLAY_TZ` / `"WAT"`, filters `portal_date`/`portal_datetime` | no — hardcoded |
| raw + a literal label | `billing_payment_receipts.py:234,245,246,344,374,463,660,675` — `strftime` on the model value, with the string `"UTC"` appended | no |

Consequence: **the same payment timestamp renders differently in the portal
HTML receipt (WAT, via the template patch) and the portal PDF receipt (the same
instant, labelled "UTC")**. Of the eight rendering-adjacent files swept, only
`web_document_discount_report.py:220,311` uses the db-resolved formatter.

### 5.4 The P9 boundary, precisely

`docs/inventories/billing-sources.md` P9 is *partial*: *"`display.py` resolves
tenant timezone and date/datetime formats. There are no locale catalogs, stable
message IDs, or currency-display rules."*

| Concern | Owner |
|---|---|
| Tenant timezone; `date_format` / `datetime_format` strings | `dotmac_kernel.display` — **exists today**, and its docstring scopes it to the web portal |
| Placing fraction digits from an amount's `minor_units` | **rendering** — no catalogue needed |
| Rendering a date in the document's declared zone using declared format strings | **rendering** |
| Currency **symbol** and its position | **P9** — does not exist |
| Locale-correct digit grouping (lakh grouping, space vs dot) | **P9** |
| Label translation, stable message IDs, pluralization | **P9** |
| Resolving a BCP-47 tag against a catalogue | **P9** |

Rendering's stopgap until P9 lands is stated in the contracts spec § 6: money
renders as `<ISO-4217 code> <exact decimal>`, and **a label's language is a
property of the template version, not of a lookup** — a second language is a
second template version. Deliberately dumber than a locale layer, and correct
in every deployment rather than subtly wrong in most.

---

## 6. Parity-test inventory

The tests that must be ported and keep passing, and — worth more — the areas
with **no adequate source test at all**.

### 6.1 Must port

**`tests/test_billing_invoice_pdf_storage.py` (15 tests).** The strongest
source proof in scope.

| Test | Behaviour it proves |
|---|---|
| `test_process_export_uploads_invoice_pdf_to_s3_metadata` | a completed export writes bytes to storage, sets `file_size_bytes == len(bytes)`, and creates exactly one non-deleted `StoredFile` |
| `test_export_file_exists_and_stream_export_uses_s3` | streaming returns exactly the bytes that were stored |
| `test_queue_export_ignores_non_subscriber_requested_by_id` | an unknown requester is nulled; the task is enqueued with `correlation_id=f"invoice_pdf_export:{id}"` |
| `test_queue_export_reuses_queued_export_without_task_id_with_correlated_enqueue` | a second queue call returns the **same** export row — no duplicate artifact |
| `test_render_invoice_html_includes_branding_and_company_info` | brand + company info reach the document (port the *intent*; the assertion is on CSS-token strings and must be re-based on a template artifact) |
| `test_render_invoice_outputs_bank_details` | HTML and text paths agree on bank details — the only cross-path parity assertion that exists |
| `test_invoice_detail_context_includes_bank_details` | bank details come from the `collection_accounts` **table**, not a settings blob |
| `test_render_invoice_html_uses_naira_sign` | **port inverted.** Proves the currency defect of § 5.2; the target asserts the ISO code |
| `test_text_fallback_uses_ngn_and_truncates_with_marker` | latin-1 safety + the truncation marker |
| `test_pil_fallback_renders_currency_and_truncation_marker` | the PIL path draws the marker |
| `test_completed_export_before_template_refresh_is_stale` | **port inverted.** A template bump invalidating an issued artifact is exactly the immutability defect (§ 2.1) |
| `test_generate_export_now_uses_current_renderer` | a forced regeneration does not serve a stale artifact |
| `test_build_pdf_bytes_with_weasyprint_pydyf_compat` | the real engine produces `%PDF-` bytes, `len > 1500` |
| `test_render_invoice_html_falls_back_to_brand_legal_name` | never renders the placeholder `"Your Company"` |
| `test_direct_bank_transfer_context_surfaces_owned_accounts` | (not a rendering test) |

**`tests/test_billing_statement_service.py` (6 tests).** Opening/period/closing
and per-currency separation, `has_multiple_currencies`, `"NGN 100.00"` /
`"USD -20.00"` display strings, exclusion of archived-mirror and
`affects_customer_position=False` rows, exclusion of internal repair entries,
and the row→invoice authoritative link. Port **only if statements stay in
scope** (Q3); the arithmetic these prove belongs to billing, not to rendering.

**`tests/test_quote_documents_and_delivery.py` (14 tests).** Out of the
document scope but the **strongest structural source in the fleet** — see § 8.
Port the *pattern* proofs: `test_pdf_export_is_content_addressed_and_audited_once`
(same snapshot ⇒ same export id + fingerprint, `replayed is True`, exactly one
audit event), `test_stored_quote_payment_url_must_match_secure_company_route`
(the stored snapshot is **re-validated on read**),
`test_quote_document_fails_closed_without_eligible_bank_details`,
`test_active_draft_quote_exports_bank_only_without_customer_portal_identity`
(re-render from the stored snapshot), `test_payment_section_follows_totals_and_precedes_footer`
(a real layout-ordering assertion on the HTML string).

**Receipt tests (4).** `test_receipt_pdf_falls_back_when_weasyprint_fails`
ports **inverted** — the target refuses a silent fallback (§ 4). The two route
tests (`media_type == "application/pdf"`, body starts `%PDF-`,
`content-disposition` carries `receipt-RCP-TEST.pdf`) port as-is, and
`test_admin_receipt_pdf_route_returns_pdf_response` is the only admin/customer
parity assertion — on headers, not bytes.

**Attachment-boundary tests.**
`test_invoice_pdf_attachment_reference_is_durable_on_email_delivery` proves an
e-mail stores only `[{"kind":"invoice_pdf","entity_id":…,"filename":…,
"content_type":"application/pdf"}]` — a **reference**, no bytes in the
database. `test_resolve_invoice_pdf_fails_closed_on_account_mismatch` proves a
cross-subscriber request raises before any PDF is generated. Both are
delivery-boundary proofs (ADR-0024 § 6) and belong to the delivery owner, but
they constrain what rendering hands over.

### 6.2 No adequate source test — saying so is the finding

| Gap | Evidence |
|---|---|
| **Determinism** — no test renders twice and compares anything | The nearest is `test_pdf_export_is_content_addressed_and_audited_once`, and it proves *input* determinism only: `_fingerprint` (`quote_documents.py:515-517`) is a SHA-256 over the **snapshot**, and `_render_pdf` is monkey-patched to a constant in that test, so the second call never renders. Nothing anywhere pins `/CreationDate` or object ordering. |
| **Checksum / SHA-256 of output** | None. The only `checksum` in the suites is a fixture input (`checksum="a" * 64`) that is never asserted. Byte-length assertions are loose lower bounds only: `len > 1500` (twice), `len > 2000`. |
| **Failure never alters the invoice** | None. `billing_invoice_pdf.py:1409-1432` — the whole `db.rollback()` → re-fetch → mark `failed` sequence — is **untested**: `grep InvoicePdfExportStatus.failed tests/` returns zero hits. Nothing asserts the invoice row is untouched after a render crash. |
| **Regeneration after the stored file is deleted** | None. `is_export_cache_valid` has **zero** test references; no test deletes an object from the fake store and re-requests. |
| **Credit-note document** | None — there is no renderer to test (§ 1). |
| **Non-NGN rendering** | None, for invoice or receipt. The receipt's non-NGN branch (`billing_payment_receipts.py:206`) is never exercised. |
| **HTML-vs-fallback content parity** | The three invoice paths are asserted independently; only `test_render_invoice_outputs_bank_details` compares two of them, and only on bank-detail strings — **never on money figures or line counts**, which is precisely where they diverge (§ 4). |
| **The customer portal PDF route** | `app/web/customer/routes.py:783` has no test — including its three-branch cache/generate/queue logic and `maybe_finalize_stalled_export`, which has zero test references. |
| **Statement CSV route, statement e-mail route, reports/statements page** | All three untested. |

### 6.3 The architecture guards do not reach document rendering

Four guards were checked. None of them constrains a rendered artifact.

- `tests/architecture/test_ui_no_template_derived_totals.py` — regex
  `\{%-?\s*set\s+(\w[\w.]*)\s*=\s*(\w[\w.]*)\s*\+` requiring **both names
  textually identical**. Misses `-`, misses `{% set a = b + c %}`, misses
  `| sum`, misses inline `{{ x.total + y.total }}`. Jinja only — and every
  document renderer in Sub is a **Python f-string**, so this guard cannot see
  any of them.
- `tests/architecture/test_template_projection_boundary.py` — wider on one
  axis (catches `-`), narrower on another (only matches dotted `NS.FIELD`
  names). Same Jinja-only blind spot. Has a proper two-directional baseline
  (`template_business_arithmetic_baseline.txt`).
- `tests/architecture/test_quote_document_delivery_boundary.py` — the most
  substantive: forbids `db.commit(` / `db.rollback(` in the quote renderer,
  forbids `PartyContactPoint` / `send_email(` / `smtp` in the delivery module,
  and **forbids the literal test bank data from ever appearing in
  `quote_documents.py`**. But every assertion is a whole-file substring grep:
  a rename (`db.commit(` → `session.commit(`) passes silently, and
  `"smtp" not in text.lower()` trips on a code comment.
- `tests/architecture/test_ui_documentation_authority.py` — prose-consistency
  only; enforces nothing about rendering.

Two consequences for the target. First, **the Jinja-arithmetic guards are the
wrong shape** for this capability, because the recomputation happens in
Python service code, not in templates — which is why the contracts spec makes
the money guard structural (a `FormattedAmount` that carries the fact field it
came from and therefore cannot be constructed from a computed value) rather
than another regex. Second, `test_communication_eligibility_ownership.py:57`
already lists `billing_payment_receipts.py` in `LEDGER_BYPASS_BACKLOG` — the
receipt renderer is a **known, sanctioned** direct-transport caller that
bypasses the consent ledger. That is a delivery-boundary debt, and it is one
more reason rendering must not send.

---

## 7. Retirement inventory

What retires from Sub, in what order, and the ADR-0018 two-directional ratchet
that proves it. **This is a sequence, not a schedule** — none of it starts
before ADR-0017's P11 gate and an accepted rendering slice.

### 7.1 What retires

| # | Path | Retires into | Note |
|---|---|---|---|
| R1 | `billing_invoice_pdf.py:222-464` `_render_invoice_html` | a versioned template artifact + the renderer port | the f-string and its 75 lines of inline CSS |
| R2 | `billing_invoice_pdf.py:471-519` `_build_simple_pdf`, `:589-932` `_build_branded_fallback_pdf`, `:529-586` `_render_invoice_text_lines` | **nothing — deleted, not ported** | the divergent fallbacks of § 4 |
| R3 | `billing_invoice_pdf.py:193-219` + `quote_documents.py:520-541` (the duplicated pydyf shim) | the renderer adapter, once | a pinned engine adapter owns its own compatibility |
| R4 | `billing_invoice_pdf.py:1296-1309` `_build_pdf_bytes` | `DocumentRenderer.render` | |
| R5 | `billing_invoice_pdf.py:1064-1114` cache metrics in `domain_settings` | ordinary metrics | a read-modify-write counter in a settings table is a lost-update race and a settings-as-data abuse (ADR-0011) |
| R6 | `InvoicePdfExport` model + `invoice_pdf_exports` table + `app/tasks/invoice_pdf.py` | `dotmac_kernel.idempotency` + the outbox | a status column with `queued/processing/completed/failed` is a second at-most-once mechanism (ADR-0014, hard rule 23), and it already has the predicted stuck-`processing` failure, worked around by `maybe_finalize_stalled_export` and a 20-second `STALE_EXPORT_SECONDS` heuristic |
| R7 | `billing_invoice_pdf.py:1237,1241,1331-1341` local-disk and direct-S3 read branches | `dotmac-files` only | the polymorphic `file_path` of § 3 |
| R8 | `billing_payment_receipts.py` render + fallback (`:327-710`) | the renderer port | and the receipt becomes a **persisted** artifact |
| R9 | `payment_receipt_identity.py:15` fabricated `#RCP-` reference | **P4 numbering**, invoked by billing at issuance | not rendering's, and not a UUID prefix |
| R10 | `billing_payment_receipts.py:127` render-time `application_summary` call | an immutable receipt fact from billing | § 2.2 |
| R11 | Live settings lookups: `billing_invoice_pdf.py:141-167` (logo), `:247-254` (seller), `:266,865` (bank) | snapshots on the fact (spec § 5, R1–R3) | |
| R12 | Currency-symbol reimplementations: `billing_invoice_pdf.py:49`, `billing_payment_receipts.py:206`, `templates/customer/billing/receipt.html:6`, `web_billing_documents.py:43` | the ISO-code stopgap, then P9 | C5 forbidden name |
| R13 | Raw-`strftime`-with-a-`"UTC"`-label in the receipt PDF (8 sites) | the document's declared timezone | § 5.3 |

**Deliberately NOT retiring in this workstream:**
`app/services/sales/quote_documents.py` (a sales document, not a receivable —
different producer, different authority; see § 8),
`app/web/admin/reports.py:1887-1924` (a regulatory report pack, not a legal
document — though its silent `.pdf → text/html` degradation should be fixed by
its own owner), `web_billing_documents.py` (an inventory list projection that
renders no document), `document_delivery.py` (an e-mail envelope; ADR-0024
§ 6 transport), `web_document_discount_report.py` (a report, and the only
sweep file already using the canonical formatter and the db-resolved
timezone — leave it alone).

### 7.2 Order

1. **R11 + R9 first, in billing, before any rendering code exists.** Snapshots
   and numbering are producer-side. Until the fact is self-sufficient,
   rendering cannot be a pure function of it and none of the invariants hold.
   This ordering is not optional: it is what makes I5 (repair by re-rendering)
   true rather than aspirational.
2. **R1 + R4 + R3, invoice only, shadow.** Render from the fact beside the
   legacy path; compare `DocumentProjectionV1` digests section by section — not
   bytes, not extracted text (contracts spec § 5); nothing
   is served from the new path.
3. **R2.** Delete the fallbacks once the shadow proves the engine path is
   reliable. Deleting them *first* would be a production regression; deleting
   them *last* is how they survive forever.
4. **R7 + R6.** Storage collapses to `dotmac-files`; the export table and its
   task retire into idempotency + outbox. R7 before R6, because the readers
   must stop guessing before the row can go.
5. **R12 + R13** ride along with R1 (they are template and formatting
   concerns).
6. **R8 + R10, receipts.** Later, because it needs a receipt *fact* billing
   does not emit today (spec § 5, R4).
7. **Credit notes are new build, not retirement** — there is nothing to
   retire (§ 1). They are the cleanest first slice for that reason, and the
   one the fleet most needs.
8. **R5** any time; independent.

### 7.3 The ratchet

Reference implementation shape:
`tests/architecture/test_external_connector_ratchet.py` plus
`docs/inventories/external-connector-baseline.json` — a sweep script, a frozen
JSON baseline, and tests that fail when a count moves in **either** direction.
Per ADR-0018 and hard rule 25: *"Fail when the count moves in EITHER direction:
upward is new debt, downward is progress that must be recorded by lowering the
number."*

Proposed `scripts/document_render_sweep.py` +
`docs/inventories/document-render-baseline.json`, counting per repository:

| Metric | Detector |
|---|---|
| `engine_call_sites` | `weasyprint`, `HTML(string=`, `write_pdf(`, `ImageDraw`, and hand-built `%PDF-` byte construction, **outside** the rendering module |
| `render_path_storage_writes` | `file_uploads.upload(`, `stage_upload(`, `get_s3_storage()`, `open(..., "wb")`, `write_bytes(` reachable from a rendering entry point |
| `render_time_money_derivations` | a call to a `*_summary`/`*_balance`/`application_summary` service, or `sum(`/`+`/`-` on a name matching `_amount\|_total\|_tax\|_balance\|_due`, inside a rendering module |
| `render_path_settings_reads` | `resolve_value(`, `get_company_info(`, `resolve_brand(`, `get_invoice_bank_details(` inside a rendering module |
| `fabricated_document_numbers` | `str(<id>)[:N]`, `.hex[:N]`, `f"#RCP-`, `f"INV-{str(` inside a rendering module |
| `currency_symbol_literals` | `₦`, `$`, `€`, `£` and `"NGN"` as a default, outside the canonical formatter |
| `fallback_renderer_paths` | an `except` block whose body returns document bytes |

**Entry-point families, not one directory** (ADR-0018 § 1): the sweep covers
`app/services/**`, `app/web/**`, `app/tasks/**`, `scripts/**`, and any CLI or
worker module — because `app/tasks/invoice_pdf.py` is an entry point today and
a sweep scoped to `app/services/` would miss it.

**Sensitivity proof** (the guard must be able to fail): a test writes a
temporary module under each scanned family containing `HTML(string=x).write_pdf()`,
a `get_s3_storage()` call, and `f"#RCP-{uid.hex[:8]}"`, re-runs the sweep, and
asserts every corresponding count **rose**. A ratchet that cannot detect a new
violation is a number, not a guard.

**Abstention, stated honestly:** like the external-connector ratchet, this
sweep measures a sibling repository and must **abstain** — not score zero —
when `dotmac_sub` is not checked out. Scoring a missing repo as zero would
report the duplication as solved.

**"Grandfathered" stays distinct from "reviewed and correct"** (ADR-0018 § 4).
The baseline JSON is *known-wrong and shrinking*. Any per-line
`# document-render: allow` marker, if one is ever needed, means *this is
genuinely fine here* and must not be reachable by copying a baseline entry.

---

## 8. The best source in the fleet is a quote, not an invoice

Recorded because the product-first procedure requires naming the qualifying
implementation, and the honest answer is not the obvious one.

`app/services/sales/quote_documents.py` is the only renderer in Sub that gets
the **structure** right, and it gets right nearly every invariant the target
contract needs:

| Target invariant | What quote documents already does |
|---|---|
| Render from an immutable snapshot, not live rows | `_snapshot` (`:469-511`) freezes lines, totals, bank details and brand into `QuoteDocumentSnapshot`, serialized with `to_storage` (`:138`) |
| Determinism anchored on the input | `snapshot_fingerprint = sha256(serialized snapshot)` (`_fingerprint`, `:515-517`) |
| Content-addressed, replayable | export id is `uuid5(NAMESPACE_URL, f"dotmac-sub:quote-pdf:{quote.id}:{fingerprint}")` (`:749-752`); an identical snapshot returns the existing `StoredFile`, `replayed=True` (`:737-744`) |
| One storage owner | `file_uploads.stage_upload` (`:755`) — no S3 or disk bypass |
| **No silent fallback** | `renderer_unavailable` (`:695-697`) and `invalid_pdf` (`:700-701`) both **raise** |
| Fails closed on missing required data | `bank_details_unavailable` when no `CollectionAccount` exists |
| The stored snapshot is re-validated on read | `_validate_quote_payment_url`, proven by `test_stored_quote_payment_url_must_match_secure_company_route` |
| Renders no transaction | `test_quote_document_delivery_boundary.py` forbids `db.commit(`/`db.rollback(` in the file |
| Re-renderable from the stored snapshot | `test_active_draft_quote_exports_bank_only_without_customer_portal_identity` |

Its defects, so the port starts from the corrected shape (hard rule 24, step 3):

- `_money` (`:321-322`) applies `quantize(Decimal("0.01"))` — **rounding inside
  the formatter**. A formatter that rounds can change a figure; the target
  formats an already-exact `minor_units`-correct amount.
- `discounted_subtotal` (`app/models/sales.py:623-628`) computes
  `max(0, subtotal - discount_amount)` as a model **property**, evaluated
  during snapshot capture — so one derived figure is computed by the
  render path after all. On replay it is read back from JSON with a silent
  fallback `or value["subtotal"]` (`:274-276`).
- No template artifact; still an f-string.
- Reference is the raw quote UUID (`:673`) — no document number.
- Hardcoded English month names (`%d %B %Y`, `:325-326`).

**A quote is a sales document, not a receivable**, so it is out of the first
slice (its producer is a sales authority, not `dotmac-billing`, and stretching
`InvoiceDocumentFactV1` to cover it would repeat the merge error the
Template Studio audit exists to catch). But the **snapshot → fingerprint →
content-addressed store → no-fallback** pattern is the qualifying structural
source, and it is already production-used and tested with 14 tests. The
invoice path, despite being the larger and better-known implementation, is the
weaker source: it has no snapshot, three storage paths, three renderers and a
mutable artifact.

---

## 9. Cross-repository note

ERP's `DocumentGeneratorService` + `GeneratedDocument` is named by
`docs/inventories/template-studio-source-audit.md` as document generation's
qualifying source, with 10 integration tests covering *"template selection,
HTML render, context-snapshot sanitisation, PDF generation, and the
sent/final/superseded lifecycle"*, and `GeneratedDocument` carrying
`content_hash`, sanitised `context_snapshot`, `file_path`, `document_number`
and `superseded_by`. That audit calls it *"the only concrete implementation of
the SOT criterion 'every projection has provenance' in either product"*.

**This inventory did not read ERP.** It is scoped to Sub, which the task named
as the source repository, and asserting ERP's shape second-hand would be
exactly the "same shape built twice, on the strength of a name collision"
error ADR-0020 A4 records. **Reading `dotmac_erp`'s
`DocumentGeneratorService`, `GeneratedDocument` and its 10 integration tests is
a prerequisite for the dossier's `source_paths` to be complete** — recorded as
the dossier's first `next_action`, not glossed over here.

Two things are worth flagging from the Template Studio audit's own findings
regardless: ERP's `automation.document_template` and
`automation.generated_document` have **no organization filter** in their status
mutators, and `DocumentTemplate` has no `version` column, so
`GeneratedDocument.template_version` *"names a revision that cannot be
reproduced"* — the same provenance hole this contract closes with
`template_version` + `projection_digest`.

---

## 10. Defects found, reported rather than fixed

Reported per hard rule 24's product-first procedure and ADR-0018's spirit; none
is this workstream's to fix, and none is edited here.

1. **`Payment.receipt_number` is never written.** Every receipt reference in
   production is a 32-bit UUID prefix with no uniqueness guarantee
   (`payment_receipt_identity.py:15`). Collidable.
2. **Re-printing an invoice prints today's bank details**
   (`billing_invoice_pdf.py:266,865`). Money-misdirection risk.
3. **An issued invoice PDF regenerates when the invoice row changes**
   (`_is_export_fresh`, `:945-955`), so the same invoice number can produce
   contradictory documents over time.
4. **The silent renderer cascade truncates line items at 7 or 30** depending on
   which native libraries are installed (§ 4). Customer-visible, host-dependent.
5. **A `.pdf` request can silently return `text/html`** with no log line at all
   (`app/web/admin/reports.py:1913-1918`).
6. **A USD invoice renders `₦` amounts** (`billing_invoice_pdf.py:49`), and a
   test asserts it.
7. **The HTML receipt and the PDF receipt disagree about the timezone** of the
   same payment (§ 5.3), and about how many summary rows exist (5 vs 3).
8. **The receipt's four money figures are recomputed at print time**
   (§ 2.2), so a reprint after reallocation contradicts the original — which
   was never stored.
9. **Cache metrics are read-modify-written into `domain_settings`**
   (`:1064-1114`) — a lost-update race, and settings used as a counter store.
10. **`payment_proofs.py:335-338` writes financial evidence to local disk**
    and stores a filesystem path as the reference. Adjacent to this workstream;
    belongs to the `dotmac-files` adoption.
11. **The pydyf compatibility shim exists twice**
    (`billing_invoice_pdf.py:193-219`, `quote_documents.py:520-541`).
12. **`Subscriber.locale` is captured in six forms and read by nothing.**
