# Money flows: source inventory

Inventory for the `dotmac-money-flows` extraction dossier
(`packages/dotmac-money-flows/EXTRACTION.toml`).

## Qualifying source: Sub

`dotmac_sub@9e545a357adb1185e550edc4d04190e4da2d0893`:

- `app/services/billing/starter_settlement.py`
- `app/services/billing/starter_manual_settlement.py`
- `app/services/billing/starter_reseller_settlement.py`

Each is a back-half that records provider or manual cash evidence and then
accepts and allocates it through Billing inside one caller-owned transaction.
The package re-homes that composition as a tenant-plane flow that stores
instance state only; the preserved behaviour proof is the three matching Sub
integration suites under `tests/integration/`.

## Kernel adjacency (not an equivalent)

The kernel owns outbox delivery and database roles; Durable Timers owns timer
identity. None composes cash acceptance across Payments and Billing, so none is
an equivalent. The package uses only the kernel and published owner surfaces.

## ERP: inventoried, no equivalent writer

`dotmac_erp@b912fd4ef474915d62758e3083742e42924ac3e3`. ERP's receivables
payment path (`app/services/finance/ar/customer_payment.py`) is its own ledger
write path, not a composition of separately owned Payments and Billing records.
ERP is `inventory_only`: somebody looked; it writes nothing this package owns.
