# dotmac-integration

The external connector control plane (ADR-0024): installations, immutable
configuration revisions, capability bindings, secret references, and a versioned
connector SPI.

The module holds no provider knowledge. It contains no provider enum, no import
list and no `if provider == ...` branch (ADR-0024 § 7) — a connector is an
independently released distribution the module discovers through entry points,
and everything the module knows about one it learned from a declaration it
verified.

## Release state

**Released: `0.1.0a1` through `0.1.0a9`; declared: `0.1.0a10`.** Releases
a2–a4 implement SPI 1.1; a5–a9 implement SPI 1.2; a10 adds SPI 1.3's
manifest-owned secret-binding and egress declarations. See `CHANGELOG.md`,
which is the authority on what has and has not shipped. a8 adds indexed,
revisioned shadow-comparison evidence; a9 adds finite replay-evidence retention
and durable legal-hold history.

Capability `config_schema` declarations are executable contracts, not catalog
metadata. A revision is accepted only when it matches every capability bound to
the installation; a new revision or changed binding invalidates activation
until static and live connection validation succeed again. Configuration
identity includes both content and `schema_version`.

## Where to read what

| | |
|---|---|
| The SPI, and what it promises | `COMPATIBILITY.md` |
| What has shipped | `CHANGELOG.md` |
| The as-built module | the docstring in `src/dotmac_integration/__init__.py` |
| Extraction provenance | `EXTRACTION.toml` |

## The SPI in one page

A connector distribution registers one entry point in the
`dotmac_integration.connectors` group, resolving to a plugin that satisfies
`ConnectorPlugin` — identity, metadata, `validate_connection` — plus one
protocol per mode it declares:

| Mode | Protocol | Factory | Returns |
|---|---|---|---|
| `DELIVERY` | `DeliveryPlugin` | `handler_for` | `CapabilityHandler` |
| `INGRESS` | `IngressPlugin` | `ingress_handler_for` | `IngressHandler` |
| `POLL` | `PollPlugin` | `poll_handler_for` | `PollHandler` |

A declared mode is a promise the module verifies at discovery, in both
directions and including the shape of the handler that comes back. A mode
declared and not implemented fails at the first dispatch; one implemented and
not declared never gets its workers started. Both used to pass.

Ingress connectors receive one immutable `IngressRequest` — the raw bytes, the
headers and the query params, preserved exactly and handed to all three hooks as
the same object, so what was authenticated and what was interpreted are provably
the same bytes. They hand back an `Acknowledgement`, which carries the response
**body** and **media type** and deliberately cannot carry a **status code**: a
connector must be able to satisfy a provider's exact handshake format without
being able to lie about whether the engine accepted the request.

SPI 1.2 lets `verify` return a `VerificationResult`: acceptance plus positions
in an ordered active-secret set, with no field capable of carrying a secret
name, reference or value. The assembly may observe that provider-neutral result
for rotation metrics. SPI 1.1 boolean results remain valid.

The module also carries product delivery provenance without owning product
meaning. A destination application publishes one authenticated
`ProductPortDescriptorSnapshot`; `reconcile_product_port_descriptor` verifies
and appends the snapshot to the destination revision history. Claimed receipts
carry their durable `provider_event_id` into `ProductRequest`, and
`InboundDisposition.RECORD_ONLY` closes transport evidence that must never
enter the product consequence worker. These are engine contracts, not provider
branches.

## Certifying a connector

```python
from dotmac_integration.conformance import (
    assert_connector_conforms,
    assert_plugin_conforms,
)
from my_connector import MANIFEST, PLUGIN


def test_manifest_conforms() -> None:
    assert_connector_conforms(MANIFEST)


def test_plugin_conforms() -> None:
    assert_plugin_conforms(PLUGIN)
```

The conformance kit is shipped as library code rather than left in this repo's
test tree, because a kit that lives in the host's tests cannot be imported by the
distribution it is meant to certify. It reaches no network and needs no
credentials.
