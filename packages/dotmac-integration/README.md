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

**Released: `0.1.0a1` through `0.1.0a5`.** Releases a2–a4 implement SPI 1.1;
a5 implements SPI 1.2 verification evidence. `0.1.0a6` and the additive
SPI 1.2 provisioning contract are on `main` and unreleased. See `CHANGELOG.md`,
which is the authority on what has
and has not shipped.

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
| `PROVISION` | `ProvisionPlugin` | `provisioning_handler_for` | `ProvisioningHandler` |

SPI 1.2's provisioning handler exposes typed
`plan`/`apply`/`observe`/`cancel` operations. The module persists the approved
plan and exact connector/configuration provenance, materializes secrets only at
the invocation boundary, and records ambiguous provider outcomes for
reconciliation rather than retrying an effect it cannot prove did not happen.
Every binding also carries a required provider-neutral
`capability_instance_ref` (1..200 ASCII characters matching
`^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$`). This distinguishes several configured
instances of the same versioned capability without entering product-owned
operation inputs. Legacy rows remain unassigned and fail activation/provisioning
until an operator disables and explicitly assigns them through
`assign_capability_instance_ref`.

A connector declaring `PROVISION` must attach the product owner's exact kernel
`CapabilityContractSnapshot` and every referenced canonical
`CapabilitySchemaDocument` to each `CapabilityDeclaration`. The
snapshot names the owner and schema version, pins the input and output schema
identity for all four operations, and supplies the complete typed configuration,
endpoint and activation/evidence declarations. The declaration refuses missing,
duplicate or ref/digest-mismatched schema bytes, and the manifest digest embeds
both contract and schemas rather than a connector-authored subset. Binding activation
and provisioning intake use `verify_capability_configuration` to enforce the
same contract without product/provider branches; secret-reference fields live
only in `secret_refs`, and the verifier returns value-free provenance.
`create_draft`/`adopt_manifest` also accept the exact connector distribution
digest already admitted by the Release Catalog. PROVISION activation refuses a
missing or non-canonical artifact pin; config revisions never alter it.

All four operation schema pairs are executable. PLAN validates every proposed
step input against the held `plan` input schema before it records a command;
the connector's repr-suppressed plan evidence must satisfy the held output
schema, and only its canonical digest enters the immutable PLAN result receipt.
OBSERVE and CANCEL derive an immutable provider-neutral `target` from the
durable original step input, copying only top-level fields declared by the held
operation input schema. Their successful output evidence is schema-validated;
receipts retain only a canonical full-evidence digest and the schema-derived
`public_non_secret` projection. `CANCELLED` and `NOT_FOUND` are successful
cancel outcomes for this validation boundary. Installation config and held
secret references remain separate SPI request fields and are never copied into
an operation target.

An APPLY command also separates what approval can know from what execution
produces later. `provisioning_command_template_digest` binds the exact local
steps, sorted prerequisite capability-binding ids and the public
`PrerequisiteEvidenceBinding` dataflow into the approved plan.
`PrerequisiteReceiptPin` then names the immutable latest succeeded receipt for
each upstream binding at dispatch. Receipt pins are signed and part of command
identity, but are not placed in the earlier plan hash: predicting a receipt
digest before provider execution would be impossible. Intake locks and verifies
every pinned upstream operation before any downstream row or provider effect.
At apply preparation it locks and re-verifies the exact upstream receipt chains,
copies only schema-declared `public_non_secret` values into the target input,
validates the resolved input against the held Draft 2020-12 schema, and persists
its digest without rewriting the signed command. Connector apply output is
likewise schema-validated; receipts retain only its public projection plus the
digest of the complete result, never arbitrary or secret-classified values.

PLAN settlement writes one immutable `ProvisioningCommandReceipt`, readable by
`read_provisioning_plan_receipt`. APPLY must bind that exact local receipt as
well as the separately verified assembly receipt before it can create an
operation. The same intake check corroborates the exact local installation,
connector artifact, manifest, configuration revision, product-owned capability
contract and `DEFAULT_POLICY_DIGEST`; a signed command cannot relabel any one
of those local facts. Operation receipts are hash chained and project
the immutable capability instance, plus `step_key` and
`provider_operation_ref` from immutable receipt columns. Both
are null only for operation-level receipts such as `command_accepted`.

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
