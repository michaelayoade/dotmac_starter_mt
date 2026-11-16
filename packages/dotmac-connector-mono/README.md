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

## Integration checklist

- Pin `dotmac-connector-mono` in the consumer and compose its exported `dotmac_connector_mono.PLUGIN` through the host’s connector configuration.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-integration`.

### Executable reference

[Example: connector version is one fact on every public surface](../../tests/architecture/test_mono_connector_boundary.py#L29). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/architecture/test_mono_connector_boundary.py::test_connector_version_is_one_fact_on_every_public_surface -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
