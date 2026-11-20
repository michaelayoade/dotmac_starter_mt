# Finance and money

- Short explanations of package responsibilities, not a claim of production readiness.
- Check the [catalog](../MODULE_CATALOG.md) for installation, release and adoption evidence.
- A **tenant** is a customer workspace; the **platform** is the operator control plane.
- [All module groups](index.md).

## Accounting (`dotmac-accounting`)

- **What it does:** Manages charts of accounts, fiscal periods and balanced journal postings.
- **Use it when:** Building accounting records and financial reporting foundations.
- **Keep in mind:** It is not the invoice, payment-provider or physical-stock owner.
- **Details:** [Package guide](../../packages/dotmac-accounting/README.md) · [Source and contract](../../packages/dotmac-accounting/EXTRACTION.toml).

## Banking (`dotmac-banking`)

- **What it does:** Records bank accounts and statements, matches movements and tracks reconciliation.
- **Use it when:** Reconciling bank evidence with local financial records.
- **Keep in mind:** A statement observation does not itself authorize a payment.
- **Details:** [Package guide](../../packages/dotmac-banking/README.md) · [Source and contract](../../packages/dotmac-banking/EXTRACTION.toml).

## Billing (`dotmac-billing`)

- **What it does:** Owns invoices, credit notes, payment allocations and receivable balances.
- **Use it when:** Recording what a customer owes and how confirmed money is allocated.
- **Keep in mind:** It is not the general ledger, subscription scheduler or payment gateway.
- **Details:** [Package guide](../../packages/dotmac-billing/README.md) · [Source and contract](../../packages/dotmac-billing/EXTRACTION.toml).

## Collections (`dotmac-collections`)

- **What it does:** Applies collections policies and tracks collection cases from financial exposure.
- **Use it when:** Following up overdue obligations and deciding collection responses.
- **Keep in mind:** It reads financial facts; it does not own invoice amounts or move money.
- **Details:** [Package guide](../../packages/dotmac-collections/README.md) · [Source and contract](../../packages/dotmac-collections/EXTRACTION.toml).

## Expenses (`dotmac-expenses`)

- **What it does:** Tracks spending requests, expense claims, receipts and reimbursement eligibility.
- **Use it when:** Managing employee or operational expenses.
- **Keep in mind:** Eligibility is separate from actually paying or posting the reimbursement.
- **Details:** [Package guide](../../packages/dotmac-expenses/README.md) · [Source and contract](../../packages/dotmac-expenses/EXTRACTION.toml).

## Fixed-asset finance (`dotmac-finance`)

- **What it does:** Manages asset financial books, depreciation, impairment and accounting consequences.
- **Use it when:** Accounting for the financial value of fixed assets.
- **Keep in mind:** Physical asset custody belongs to Assets.
- **Details:** [Package guide](../../packages/dotmac-finance/README.md) · [Source and contract](../../packages/dotmac-finance/EXTRACTION.toml).

## Exchange-rate policy (`dotmac-fx-policy`)

- **What it does:** Stores exchange-rate observations and records which rate a policy selected.
- **Use it when:** Making reproducible currency-conversion decisions.
- **Keep in mind:** It does not fetch provider rates or define the shared Money value type.
- **Details:** [Package guide](../../packages/dotmac-fx-policy/README.md) · [Source and contract](../../packages/dotmac-fx-policy/EXTRACTION.toml).

## Payables (`dotmac-payables`)

- **What it does:** Owns supplier bills, recognized liabilities, credits and payment obligations.
- **Use it when:** Tracking money the business owes suppliers.
- **Keep in mind:** Procurement owns buying decisions; payment execution remains separate.
- **Details:** [Package guide](../../packages/dotmac-payables/README.md) · [Source and contract](../../packages/dotmac-payables/EXTRACTION.toml).

## Payments (`dotmac-payments`)

- **What it does:** Tracks payment intent, transfer proof and correlation with confirmed cash evidence.
- **Use it when:** Connecting a requested payment to trustworthy payment observations.
- **Keep in mind:** Provider connectors move or observe provider data; Billing allocates settled money.
- **Details:** [Package guide](../../packages/dotmac-payments/README.md) · [Source and contract](../../packages/dotmac-payments/EXTRACTION.toml).

## Payroll (`dotmac-payroll`)

- **What it does:** Calculates pay, deductions and employer liabilities from configured rules.
- **Use it when:** Preparing evidence-backed payroll runs.
- **Keep in mind:** Employment identity and bank payment execution remain separate.
- **Details:** [Package guide](../../packages/dotmac-payroll/README.md) · [Source and contract](../../packages/dotmac-payroll/EXTRACTION.toml).

## Tax (`dotmac-tax`)

- **What it does:** Determines taxes from configured policy and tracks reporting and return obligations.
- **Use it when:** Applying effective-dated tax rules and preserving determination evidence.
- **Keep in mind:** Country rules need configuration and qualification; this is not automatic legal compliance.
- **Details:** [Package guide](../../packages/dotmac-tax/README.md) · [Source and contract](../../packages/dotmac-tax/EXTRACTION.toml).

## Usage (`dotmac-usage`)

- **What it does:** Records normalized meter observations, corrections and usage totals.
- **Use it when:** Tracking consumption for reporting or later charging.
- **Keep in mind:** It does not collect raw network sessions or decide prices.
- **Details:** [Package guide](../../packages/dotmac-usage/README.md) · [Source and contract](../../packages/dotmac-usage/EXTRACTION.toml).

## Usage charging (`dotmac-usage-rating`)

- **What it does:** Applies effective rating rules to usage and produces pre-tax obligations.
- **Use it when:** Turning measured consumption into chargeable amounts.
- **Keep in mind:** It does not own collection, invoices, tax or payments.
- **Details:** [Package guide](../../packages/dotmac-usage-rating/README.md) · [Source and contract](../../packages/dotmac-usage-rating/EXTRACTION.toml).

## Money-flow coordination (`dotmac-money-flows`)

- **What it does:** Coordinates settlement and prepaid funded-charge flows over the financial owners.
- **Use it when:** Applying confirmed money or available credit through a defined multi-owner flow.
- **Keep in mind:** It stores flow state and references, not money balances; each financial owner retains its authority.
- **Details:** [Package guide](../../packages/dotmac-money-flows/README.md) · [Source and contract](../../packages/dotmac-money-flows/EXTRACTION.toml).
