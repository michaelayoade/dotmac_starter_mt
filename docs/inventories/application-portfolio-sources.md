# Application-portfolio sources — the audit behind a greenfield module

**As of:** 2026-08-12
**Commits audited:** `dotmac_vendor_control_plane` eb667fa
(`feat/v6-slice2-applied-state-admission`), `dotmac_sub` 73c9d9003
(`integration/kernel-adoption`), `dotmac_erp` 0f4b1698
(`feat/kernel-ui-contract-alignment`), `dotmac_academy_app` 5072e4a
(`feat/adopt-dotmac-ui-a3`)
**Why this exists:** hard rule 22 and ADR-0006's product-first amendment forbid
adding shared behaviour before inventorying ERP and Sub. `greenfield-after-inventory`
in `packages/dotmac-application-directory/EXTRACTION.toml` is a claim that this
audit was run and found no qualifying source. This file is that claim's evidence.

## The question

Does any Dotmac repository already own **the tenant's portfolio of connected
applications** — a per-tenant record of which applications that customer has, how
to reach each one, which tenant it corresponds to inside the target, and what
lifecycle state the binding is in?

Not "does anything mention applications". The unit under audit is the binding,
with its admin surface, API audience, descriptor version/digest, lifecycle and
reconciliation state.

## Method

Two sweeps, both mechanical:

1. Name sweep for a portfolio-shaped model across all four repositories:
   `application_binding`, `app_binding`, `connected_app`, `application_directory`,
   `applicationportfolio`.
2. Attribute sweep for the columns such a table cannot avoid:
   `application_code`, `admin_url`, `api_audience`, plus `class .*Application`.

## Result: no owner exists

**Sweep 1 returned nothing** in any of the four repositories.

**Sweep 2 returned three files, all false positives** — every match is
"application" in the sense of *applying* a thing, not a piece of software:

| Repo | File | Match | Verdict |
|---|---|---|---|
| `dotmac_erp` | `app/models/people/leave/leave_application.py` | a staff leave request | unrelated |
| `dotmac_sub` | `app/models/billing.py:118` | `AccountCreditApplicationPolicy` | unrelated |
| `dotmac_sub` | `app/models/billing.py:211` | `TaxApplication` | unrelated |
| `dotmac_sub` | `app/models/billing.py:1021` | `CreditNoteApplication` | unrelated |

`dotmac_academy_app` matched neither sweep.

## The vendor control plane has no application portfolio to retire

Its assembly composes exactly eight features
(`src/vendor_cp/assembly.py:33`): `console`, `accounts`, `offers`, `approvals`,
`contracts`, `allocations`, `licensing`, `provisioning`. None is a portfolio.

The nearest neighbour is `allocations`, and it is worth stating precisely why it
is not the same unit — it is the model most likely to be mistaken for one.

```
Allocation            — "an immutable projection of an activated contract
                         version's entitlement"
  contract_id           FK to the commercial contract
  customer_ref          opaque commercial identity
  content_hash          freeze of the derived entitlement
  status                'staged' only
AllocationEntry
  capability_code       one entitled capability
  quantity
```
(`src/vendor_cp/allocations/models.py:35`, `:65`)

An allocation answers *what has this customer bought, under which contract*. It
carries no application instance, no admin URL, no API audience, no target-local
tenant reference, no descriptor digest, and no lifecycle beyond `staged`. It is
keyed to a contract, not to a running application a person can be sent to.

That difference is the ADR-0021 §2 boundary in table form: the vendor plane
issues **commercial availability**; the portfolio records **connected
instances**. Same customer, different question, different owner.

## What this authorises, and what it does not

**Authorises** `source_mode = "greenfield-after-inventory"` for
`dotmac-application-directory`: the inventory ran, and there is no qualifying
production implementation to port. Under ADR-0006 that is the only honest
alternative to `product-first`, and it is not a licence to skip the audit — it
is the audit's result.

**Does not authorise** the module absorbing any of the following, each of which
has an owner today:

| Concept | Owner | Why not the directory |
|---|---|---|
| commercial entitlement, quantities | vendor CP `allocations` / `contracts` | ADR-0021 §2 — the vendor plane issues availability |
| local capability evaluation | `dotmac_kernel.entitlements` | per-deployment, explainable, already built |
| signed licence delivery | `dotmac_kernel.licensing` | ADR-0007/WS8 |
| deployment and provisioning state | vendor CP `provisioning` | a deployment is not a binding |
| who may enter an application | the target application | ADR-0021 §3 — visibility is not authorization |

## Local-copy retirement

**None.** There is no existing implementation in any repository, so this module
retires nothing. Recorded explicitly because `local_copy_retirement` is a
required dossier field and an empty answer must be a measured one rather than an
omission.

## Consumer position

First and only consumer at introduction: the Tenant Workspace
(`dotmac_workspace`). Under ADR-0017 §1 that makes the module work in progress
until the Workspace actually runs it, which is what the dossier's
`audit-complete` status records. It reaches `approved` when a second independent
consumer is on the same contract, per
`tests/architecture/test_product_first_extraction.py`.
