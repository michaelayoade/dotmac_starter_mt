# Compatibility

`dotmac-connector-paystack 0.1.0a3` implements dotmac-integration SPI
`>=1.4,<2.0` under connector key `paystack`, in INGRESS, POLL and DELIVERY
modes (DELIVERY carries refunds only; intent, payout and customer
capabilities are not declared). The floor is 1.4 rather than 1.3 because each capability declares its
OWN mode set; without that a settlement observer's binding would carry the
authority to move money, and conformance would call the ingress factory for a
delivery-only contract.

| capability | modes |
|---|---|
| `payments.settlement.observation.v1` | INGRESS, POLL |
| `payments.refund.v1` | DELIVERY |

It declares required `webhook_signing_secret` and optional
`webhook_signing_previous_secret` bindings plus optional `api_secret_key`,
which is required whenever a POLL or DELIVERY capability is bound. Egress is
exactly `api.paystack.co`; the a1 and a2 manifests stay adoptable (a2 is preserved as `SETTLEMENT_MANIFEST`).

The `dotmac-integration` floor is `>=0.1.0a14`, the first release carrying SPI 1.4;
the a2 floor of `>=0.1.0a11` (SPI 1.3) cannot load this manifest.

The DELIVERY capability takes a configuration that REQUIRES
`timeout_seconds` (1–60). A hidden default on a money path is a deployment
decision nobody made.

The public Python surface is `MANIFEST`, `PLUGIN`, `PaystackConnector`,
`PaystackDeliveryHandler`, `PaystackOperations`, `OperationResult`,
`OperationOutcome`, `OPERATIONS`, `ACTIONS_BY_CAPABILITY`,
`OUTBOUND_CAPABILITY_IDS` and `__version__`.

## Refund reference and the lookup result

A refund's `merchant_note` is the engine idempotency key, verbatim. It must match
`^[a-z0-9][a-z0-9-]{15,63}$` or the refund is refused before anything is sent
(`idempotency_key_shape`); ingress emits `refund_reference` only for a note of
the same shape. Payments' `rf1-` plus 32 hex instruction reference qualifies, so
the Integrator enqueues a refund with `idempotency_key = provider_reference`.
This shape is fixed once a3 is tagged.

`find_refund` is its own delivery with its own key. Its `params` are
`transaction` and a required `refund_reference` of the same shape. A SUCCEEDED
lookup carries this `Outcome.result` (bounded strings; `refund` carries none):

| field | meaning |
|---|---|
| `matches` | `"0"` not found as of this read, `"1"` found |
| `refund_id` | the provider's refund id (only when `matches` is `"1"`) |
| `refund_status` | the provider's status token, verbatim (only when `matches` is `"1"`) |

More than one match, a truncated list or an unreadable list is not SUCCEEDED
(`reconciliation_required` or retryable) and carries no result. Zero matches is
"not found as of this read", not proof the refund never landed: the caller waits
a settle interval since the ambiguous send before acting on it. Payments
publishes this shape as the capability's `result_schema` in the wiring change.

**Any `result_schema` published for `payments.refund.v1` MUST ACCEPT NULL.** The
engine skips the result check only when the capability has no schema and the
result is `None`, and there is one schema per capability. A schema that demanded
the lookup shape above would reject a successful `refund` (which carries
`result=None`), leave it in flight, and let the lease expire so the delivery is
claimed and POSTed again: repeated refunds. `refund_status` is bounded: a status
token longer than 32 characters is dropped rather than truncated.
