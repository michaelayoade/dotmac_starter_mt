# dotmac-connector-linkedin

First-party ingress-only LinkedIn plugin for Dotmac Integrator. It verifies the
exact request bytes using the prefix only in the signed material (the header is
the lowercase digest), answers subscription challenges, and normalizes official
batched organization-social plus single Lead Sync notifications into provider-neutral
observations. It performs no provider egress and makes no contact, campaign,
qualification, assignment, ticket, or publication decision.

## Integration checklist

- Pin `dotmac-connector-linkedin` in the consumer and compose its exported `dotmac_connector_linkedin.PLUGIN` through the host’s connector configuration.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-integration`.

### Executable reference

[Example: version is one fact](../../tests/architecture/test_linkedin_connector_boundary.py#L28). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/architecture/test_linkedin_connector_boundary.py::test_version_is_one_fact -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
