# Constrained Nextcloud management protocol v1

This is the closed HTTP surface consumed by `dotmac-connector-nextcloud`
0.1.0a1. It is deliberately not an arbitrary Nextcloud path proxy and not an
`occ`, shell, command, argv, or host-execution API.

## Transport

`management_endpoint` is a public-routable HTTPS origin with an optional fixed
sub-path. The connector validates DNS immediately before every request, refuses
any local or non-global answer, disables environment proxies, and never follows
a redirect. `management_secret_ref` resolves to the complete pre-created value
for the HTTP `Authorization` header. It is passed separately from the JSON body.

For OIDC `apply` only, `client_secret_ref` resolves to caller-created material
sent as `X-Dotmac-Held-Client-Secret`. No plan, observe, or cancel call receives
that material. Neither value may appear in an OCS envelope, evidence, error,
exception, receipt, or log.

Every request is `POST`, carries `OCS-APIRequest: true`, requests JSON with
`format=json`, and targets exactly one route:

| Capability | Route prefix |
|---|---|
| `collaboration.application.lifecycle.v1` | `/ocs/v2.php/apps/dotmac_managed/api/v1/application-lifecycle` |
| `collaboration.file-roundtrip.lifecycle.v1` | `/ocs/v2.php/apps/dotmac_managed/api/v1/file-roundtrip-lifecycle` |
| `collaboration.user-group-quota.lifecycle.v1` | `/ocs/v2.php/apps/dotmac_managed/api/v1/user-group-quota-lifecycle` |
| `collaboration.user-oidc.configuration.lifecycle.v1` | `/ocs/v2.php/apps/dotmac_managed/api/v1/user-oidc-configuration-lifecycle` |

The final segment is exactly `plan`, `apply`, `observe`, or `cancel`. No caller
can supply a path or verb outside this table.

## Request documents

All documents are JSON objects. The `target` is the exact owner-schema input;
for observe/cancel it is Integration's schema-restricted immutable projection
from the durable original step. `installation_context` contains only declared
non-secret configuration (`backup_storage_ref` and `release_channel_ref` where
the application contract requires them).

- Plan: `command_id`, `plan_hash`, `step_key`, `target`,
  `installation_context`.
- Apply: the plan fields plus `operation_ref` and `idempotency_key`.
- Observe: `command_id`, `operation_ref`, `plan_hash`, `step_key`,
  `provider_operation_ref`, `target`, `installation_context`.
- Cancel: the observe fields plus `reason` and `idempotency_key`.

One plan request contains exactly one composable owner action. The management
surface must compare the idempotency key with the exact request fingerprint;
same key/same body is a replay, while same key/different body is a collision.
The connector owns no replay or retry ledger.

## Response documents

The HTTP body is always an OCS object. OCS meta status `100` or a 2xx value is
success; OCS 429/5xx is retryable; an OCS 404 is `not_found`; authentication,
authorization and other rejections are terminal. Redirects are always terminal.
A timeout during apply/cancel is ambiguous; a plan/observe timeout is retryable.

Successful plan data is:

```json
{"evidence": {}}
```

The evidence object must validate against the exact product-owned plan output
schema. Apply/observe/cancel data is:

```json
{
  "status": "succeeded",
  "provider_operation_ref": "stable-provider-operation-reference",
  "evidence": {},
  "error_code": null
}
```

`status` is one Integration `ProvisionResultStatus` value. Evidence for a
successful status must validate against that capability and operation's held
output schema. Any password, token, credential, private key, client secret,
management secret, recovery code, or authorization value is forbidden output.

## Enablement gate

The route existing is not enough. The facade must pass isolated acceptance for
every activation check declared by the exact managed-collaboration snapshot,
including backup/rollback evidence, stable user identity, exact-user file
roundtrip, and the complete immutable-subject/no-JIT/no-email-linking/S256/
audience-azp/backchannel-logout/session-provenance/revocation OIDC policy.
Until that evidence exists, the connector wheel is reviewable but the binding
must remain disabled.
