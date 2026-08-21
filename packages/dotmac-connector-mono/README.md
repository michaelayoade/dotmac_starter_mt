# dotmac-connector-mono

Stateless Mono Financial Data v2 adapter for the independently deployed Dotmac
Integrator. It polls `/v2/accounts/{id}/transactions`, preserves amounts in the
provider's lowest denomination, and emits
`banking.transaction.observation.v1` facts. It owns no banking row,
reconciliation, ledger decision, checkpoint, retry loop, secret store or
destination route.

The connector follows only same-origin pagination for the configured account.
It carries provider direction and balance as evidence; the receiving banking
owner decides what that evidence means.

Official protocol references:

- <https://docs.mono.co/docs/quickstart>
- <https://docs.mono.co/api>

`0.1.0a1` is declared but unreleased.
