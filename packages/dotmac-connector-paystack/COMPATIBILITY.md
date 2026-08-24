# Compatibility

`dotmac-connector-paystack 0.1.0a2` implements dotmac-integration SPI
`>=1.4,<2.0` under connector key `paystack`, in INGRESS, POLL and DELIVERY
modes. The floor is 1.4 rather than 1.3 because each capability declares its
OWN mode set; without that a settlement observer's binding would carry the
authority to move money, and conformance would call the ingress factory for a
delivery-only contract.

| capability | modes |
|---|---|
| `payments.settlement.observation.v1` | INGRESS, POLL |
| `payments.intent.v1` | DELIVERY |
| `payments.refund.v1` | DELIVERY |
| `payments.payout.v1` | DELIVERY |
| `payments.customer.v1` | DELIVERY |

It declares required `webhook_signing_secret` and optional
`webhook_signing_previous_secret` bindings plus optional `api_secret_key`,
which is required whenever a POLL or DELIVERY capability is bound. Egress is
exactly `api.paystack.co`; the a1 manifest stays adoptable.

Every DELIVERY capability takes the same configuration and REQUIRES
`timeout_seconds` (1–60). A hidden default on a money path is a deployment
decision nobody made.

The public Python surface is `MANIFEST`, `PLUGIN`, `PaystackConnector`,
`PaystackDeliveryHandler`, `PaystackOperations`, `OperationResult`,
`OperationOutcome`, `OPERATIONS`, `ACTIONS_BY_CAPABILITY`,
`OUTBOUND_CAPABILITY_IDS` and `__version__`.

## Known limitation — a command's reply

DELIVERY is fire-and-forget by SPI contract: `Outcome` carries a provider
reference, an HTTP status and an error code, and there is no durable
command-response seam through which a checkout URL, a resolved account name or
a new recipient code can reach the product. Those replies are available today
only through the synchronous `PaystackOperations` surface, whose
`OperationResult.reply` carries them. This is the same gap
`dotmac-connector-remita` records for RRR issuance, and it is stated rather
than worked around.
