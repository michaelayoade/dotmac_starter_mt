# Lane 3 B7 credential-negative claim matrix

Status: SOURCE ONLY. Live execution is NOT AUTHORIZED. Nothing in this
document or its validator contacts OpenBao, GitHub or any host, and nothing
here is admission evidence. It closes the planning gap for the "Credential
failure" row of `docs/LANE3_EXECUTION_TOPOLOGY.md` section 7 (line 367): each
wrong bound claim must be denied by the live Lane 3 **B7** OpenBao JWT role
`lane3-exposure-rehearsal`, and a Lane 3 token must not read Gate-0 or Control
Plane signer paths.

The Gate-0 issuer role's A8 refusal evidence belongs to a different role
(`rehearsal-issuer-protected`) and does not transfer. It is prior art only, for
how GitHub produces wrong claims and which claims move together.

## The applied B7 role

Claims come from the "Workflow identity" paragraph of
`docs/LANE3_EXECUTION_TOPOLOGY.md` (lines 230-238), the values in
`.github/lane3-execution.json` (lines 4-9) and `scripts/lane3_openbao_topology.py`
(`ROLE` line 24, `AUDIENCE` line 25, `KV_PATH` line 27, `MAX_TOKEN_TTL` line 29).
The role was applied and read back on 2026-10-10. The value-safe record of that
read-back is `docs/inventories/lane3-b7-applied-role-fixture.json` (claim names,
types and flags only; no policy text, no secrets), and the validator diffs the
matrix `applied_role` block and the source-derived claims against it. The
fixture is a record, not a live check.

Read-back facts:

- Bound claims (7, `bound_claims_type` string, exact match): `repository`,
  `repository_id`, `repository_owner_id`, `environment`, `ref`, `workflow_ref`,
  `event_name`. Audience (`bound_audiences`): `urn:dotmac:lane3:exposure-rehearsal`.
- `bound_subject`: none. `user_claim`: `sub` (login fails if `sub` is absent; all
  Lane 3 runs share one entity alias). No `claim_mappings`.
- `bound_issuer`: a mount-level setting on `auth/jwt` (the GitHub issuer), not a
  role field, and the mount is shared with the Gate-0 role
  `rehearsal-issuer-protected`. An issuer row cannot be produced without
  forging a token, so the issuer is covered by signature and JWKS validation,
  not by a row. Any change to `auth/jwt/config` re-opens every row.
- Token: batch (not renewable), ttl, max_ttl and explicit_max_ttl 300 seconds,
  `token_no_default_policy` true, sole policy `lane3-exposure-rehearsal`,
  `token_bound_cidrs` unset (looser than the CIDR-bound A6 AppRoles; an
  observation, not a row).
- Leeways (`clock_skew_leeway`, `expiration_leeway`, `not_before_leeway`):
  intended -1 (disabled) in the model but NOT read back. Plan for the OpenBao
  defaults as the worst case (expiration 150 s, clock skew 60 s).
- Policy: explicit deny on `gate0-ssh/*`, `secret/data/dotmac/platform-cp/*` and
  `secret/metadata/*`; everything else is implicitly denied (no default policy).

Not bound, so not rows: `sub`, `job_workflow_ref`, `workflow`, `actor`,
`run_id`, `sha`.

## Coupling (why a denial is not always credited to one claim)

A8 learned that a wrong `event_name` also changes `workflow_ref`, so a denial
there cannot be credited to `event_name` alone. The same mechanics apply here:

- `workflow_ref` is `<repository>/<workflow path>@<ref>`. Any repository name
  or ref change therefore changes `workflow_ref` too.
- `repository_id` and `repository_owner_id` are immutable. The only
  non-destructive way to present different values is a different repository,
  and an owner change changes the repository, its ID and `workflow_ref`.
- The protected Environment is `main`-only. A token with a wrong `ref` cannot
  come from it, so the witness job must use another Environment or none, which
  changes or drops the `environment` claim as well. If the Environment blocks
  the run instead, GitHub refused scheduling and no token exists: vacuous.
- The production workflow is `workflow_dispatch` only. A different
  `event_name` needs a second workflow file (as in A8), so `workflow_ref`
  changes with it.
- `environment` alone can be varied only with the A8 pattern: a witness job in
  the production workflow with `id-token: write`, plus a consumer step in the
  protected job. Topology line 83 allows `id-token: write` on the protected job
  only, so under the current topology `environment` is COUPLED with
  `workflow_ref` (second-file witness). See the DECISION FOR MICHAEL note in the
  environment row.

A coupled denial is credited to the whole changed set, never to one claim.
OpenBao names only the first mismatching claim in its error and that order is
not stable, so the named claim must lie inside the credited set and must never
be used to credit a single claim. Isolable rows: `workflow_ref` (second workflow
file on `main`, same Environment), `audience` and `expiry`.

**Every witness row depends on a consumer step.** OpenBao is reachable only over
WireGuard, so a token minted on a GitHub-hosted witness runner must be carried
(as an encrypted envelope, as A8 did) to the protected job, which performs the
login. That consumer step is a change to the protected workflow and is not made
here. The `audience` row needs a new OIDC request path in the protected job
instead.

## Matrix

Machine-readable form (authoritative for the validator; the prose above
explains it).

Evidence types accepted for a login negative:

- `openbao_audit_login_failure_for_role`: an OpenBao audit request/response pair
  with the same `request.id` where `request.path` is `auth/jwt/login`,
  `request.mount_accessor` equals the `jwt/` accessor read from `sys/auth`
  (to be verified on an existing A8 entry), and `request.data.role` (HMAC) is
  **equal** to the HMAC in the positive P0 login, whose response carries the
  plaintext role in `auth.metadata.role`. If that is not possible, it equals
  `sys/audit-hash/<device>` of the public string `lane3-exposure-rehearsal`,
  which needs a sudo token and is a Michael root step. The timestamp lies in
  the run window and `remote_address` matches the runner's egress (recorded
  privately). Audit strings stay HMAC'd; no unhashing and no log_raw.
- `no_token_issued`: the audit **response** entry with the same `request.id` has
  no `auth` block and a non-empty `error`, and the client saw HTTP 400. This is
  not an accessor test: batch tokens have no accessor, so "no accessor" holds
  for a successful login too and would be vacuous.
- `denial_reason_class`: OpenBao's plaintext error class (claim name only, never
  values), such as `claim "<name>" does not match any associated bound claim
  values`, `claim "environment" is missing`, `token is expired`, or an audience
  mismatch. REQUIRED for isolable rows; for coupled rows the named claim must be
  inside the credited set.

Vacuous and refused by the validator: GitHub scheduling refusals, Environment
protection blocks, fixtures, unit tests, local JWT decoding, log lines, workflow
logs, policy text review, a positive login.

<!-- matrix:begin -->
```json
{
  "schema": "Lane3B7CredentialNegativeMatrix.v2",
  "role": "lane3-exposure-rehearsal",
  "live_execution": "NOT_AUTHORIZED",
  "applied_role": {
    "source": "docs/inventories/lane3-b7-applied-role-fixture.json (B7 read-back 2026-10-10)",
    "bound_subject": null,
    "bound_claims_type": "string",
    "user_claim": "sub",
    "claim_mappings": null,
    "token_type": "batch",
    "token_no_default_policy": true,
    "token_bound_cidrs": null,
    "bound_issuer_shared_with": [
      "rehearsal-issuer-protected"
    ],
    "leeways_read_back": false,
    "worst_case_expiration_leeway_seconds": 150
  },
  "evidence_definitions": {
    "no_token_issued": {
      "audit_response_has_auth_block": false,
      "same_request_id": true,
      "http_status": 400,
      "accessor_based": false
    }
  },
  "audit_correlation": {
    "method": "hmac_equality_with_p0_login",
    "alternative": "sys_audit_hash_michael_root_step",
    "log_raw_permitted": false
  },
  "claims": [
    {
      "claim": "repository",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:231; .github/lane3-execution.json:4",
      "production": "Real run in a different repository (disposable, same owner), token minted for the fixed audience and carried to the protected job; repository, repository_id and workflow_ref all differ.",
      "isolable": false,
      "coupled_with": [
        "repository_id",
        "workflow_ref"
      ],
      "credited_to": [
        "repository",
        "repository_id",
        "workflow_ref"
      ],
      "denial_reason_named_claims": [
        "repository",
        "repository_id",
        "workflow_ref"
      ],
      "expected_outcome": "login_denied",
      "evidence": [
        "openbao_audit_login_failure_for_role",
        "no_token_issued",
        "denial_reason_class"
      ],
      "dependencies": [
        "witness_consumer_step"
      ]
    },
    {
      "claim": "repository_id",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:231; .github/lane3-execution.json:5",
      "production": "No isolated producer: the ID is immutable per repository. Same run as the repository row; credited jointly.",
      "isolable": false,
      "coupled_with": [
        "repository",
        "workflow_ref"
      ],
      "credited_to": [
        "repository",
        "repository_id",
        "workflow_ref"
      ],
      "denial_reason_named_claims": [
        "repository",
        "repository_id",
        "workflow_ref"
      ],
      "expected_outcome": "login_denied",
      "evidence": [
        "openbao_audit_login_failure_for_role",
        "no_token_issued",
        "denial_reason_class"
      ],
      "dependencies": [
        "witness_consumer_step"
      ]
    },
    {
      "claim": "repository_owner_id",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:231; .github/lane3-execution.json:6",
      "production": "Real run in a repository under a different owner; owner ID, repository, repository_id and workflow_ref all differ. A disposable second owner may not exist.",
      "isolable": false,
      "coupled_with": [
        "repository",
        "repository_id",
        "workflow_ref"
      ],
      "credited_to": [
        "repository",
        "repository_id",
        "repository_owner_id",
        "workflow_ref"
      ],
      "denial_reason_named_claims": [
        "repository",
        "repository_id",
        "repository_owner_id",
        "workflow_ref"
      ],
      "expected_outcome": "login_denied",
      "evidence": [
        "openbao_audit_login_failure_for_role",
        "no_token_issued",
        "denial_reason_class"
      ],
      "dependencies": [
        "witness_consumer_step"
      ]
    },
    {
      "claim": "environment",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:83,232; .github/lane3-execution.json:9",
      "production": "Under the current topology the only producer is a witness job in a second workflow file, so workflow_ref changes with it (wrong Environment and missing Environment are two variants). Isolating environment alone needs the A8-style widening of the production workflow, which topology line 83 forbids (id-token: write on the protected job only).",
      "isolable": false,
      "coupled_with": [
        "workflow_ref"
      ],
      "credited_to": [
        "environment",
        "workflow_ref"
      ],
      "denial_reason_named_claims": [
        "environment",
        "workflow_ref"
      ],
      "expected_outcome": "login_denied",
      "evidence": [
        "openbao_audit_login_failure_for_role",
        "no_token_issued",
        "denial_reason_class"
      ],
      "dependencies": [
        "witness_consumer_step"
      ],
      "decision": "DECISION FOR MICHAEL: approve or refuse an A8-style witness job (id-token: write) in the production workflow plus a consumer step in the protected job. Until approved this row is credited jointly with workflow_ref and is never credited to environment alone."
    },
    {
      "claim": "ref",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:232,83",
      "production": "Dispatch from a non-main branch. The protected Environment is main-only, so a token needs a witness Environment or none; workflow_ref (embeds the ref) and environment change with it.",
      "isolable": false,
      "coupled_with": [
        "workflow_ref",
        "environment"
      ],
      "credited_to": [
        "environment",
        "ref",
        "workflow_ref"
      ],
      "denial_reason_named_claims": [
        "environment",
        "ref",
        "workflow_ref"
      ],
      "expected_outcome": "login_denied",
      "evidence": [
        "openbao_audit_login_failure_for_role",
        "no_token_issued",
        "denial_reason_class"
      ],
      "dependencies": [
        "witness_consumer_step"
      ]
    },
    {
      "claim": "workflow_ref",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:83,232; .github/lane3-execution.json:7",
      "production": "Second workflow file on main using the production Environment (A8 witness pattern). Only workflow_ref differs among the bound claims; job_workflow_ref also differs but is not bound. The witness runs on a GitHub-hosted runner, so the token must be carried to the protected job for the login.",
      "isolable": true,
      "coupled_with": [],
      "credited_to": [
        "workflow_ref"
      ],
      "denial_reason_named_claims": [
        "workflow_ref"
      ],
      "expected_outcome": "login_denied",
      "evidence": [
        "openbao_audit_login_failure_for_role",
        "no_token_issued",
        "denial_reason_class"
      ],
      "dependencies": [
        "witness_consumer_step"
      ]
    },
    {
      "claim": "event_name",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:233",
      "production": "Witness workflow triggered by repository_dispatch (as in A8). The production workflow is workflow_dispatch only, so workflow_ref differs too; credited jointly.",
      "isolable": false,
      "coupled_with": [
        "workflow_ref"
      ],
      "credited_to": [
        "event_name",
        "workflow_ref"
      ],
      "denial_reason_named_claims": [
        "event_name",
        "workflow_ref"
      ],
      "expected_outcome": "login_denied",
      "evidence": [
        "openbao_audit_login_failure_for_role",
        "no_token_issued",
        "denial_reason_class"
      ],
      "dependencies": [
        "witness_consumer_step"
      ]
    },
    {
      "claim": "audience",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:234-235; scripts/lane3_openbao_topology.py:25",
      "production": "Production-identity run requests its token with a nonce audience that no OpenBao role binds and no other relying party accepts (never the Gate-0 audience; verify by listing roles in the read-back session). The shipped supplier (scripts/lane3_github_oidc.py:381,390) refuses other audiences, so the protected job needs a new, approved OIDC request path.",
      "isolable": true,
      "coupled_with": [],
      "credited_to": [
        "audience"
      ],
      "denial_reason_named_claims": [
        "audience"
      ],
      "expected_outcome": "login_denied",
      "evidence": [
        "openbao_audit_login_failure_for_role",
        "no_token_issued",
        "denial_reason_class"
      ],
      "dependencies": [
        "new_oidc_request_path"
      ],
      "probe_audience": "nonce_bound_by_no_role"
    },
    {
      "claim": "expiry",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:367; scripts/lane3_openbao_topology.py:29",
      "production": "Replay the positive-run token after exp plus leeway (+5 s margin, as A8). Worst-case leeway to plan for: the OpenBao defaults, because the applied leeways were not read back. The protected job has timeout-minutes: 5 (token exp is about 5 minutes after issue), so the replay cannot fit.",
      "isolable": true,
      "coupled_with": [],
      "credited_to": [
        "expiry"
      ],
      "denial_reason_named_claims": [
        "expiry"
      ],
      "expected_outcome": "login_denied",
      "evidence": [
        "openbao_audit_login_failure_for_role",
        "no_token_issued",
        "denial_reason_class"
      ],
      "status": "UNPRODUCIBLE_UNDER_CURRENT_TIMEOUT",
      "forbidden": [
        "forging",
        "jwt_export"
      ],
      "decision": "DECISION FOR MICHAEL: approve a longer job timeout for a dedicated proof run (it also touches the B6 CERT_TTL <= job-timeout reasoning), or accept this row as unprovable. Forging a token is forbidden and would prove nothing, since the signature check fails before exp is evaluated; exporting the JWT from the run is forbidden."
    }
  ],
  "path_probe_order": {
    "positive_control_after_last_denial": true
  },
  "path_readbacks": [
    "identity_policies_empty",
    "mounts_exist"
  ],
  "excluded_paths": [
    "gate0-ssh/public_key"
  ],
  "path_isolation": [
    {
      "path": "secret/data/dotmac/platform-cp/rehearsal-issuer/signing-key",
      "operation": "read",
      "source": "dotmac_platform_control_plane docs/design/gate0-d-implementation-spec.md:262,951@9e06e129d415d940387fc2a509c54e26f06c0466",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ]
    },
    {
      "path": "secret/data/dotmac/platform-cp/rehearsal-issuer/trust-state",
      "operation": "read",
      "source": "dotmac_platform_control_plane docs/design/gate0-d-implementation-spec.md:343,952@9e06e129d415d940387fc2a509c54e26f06c0466",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ]
    },
    {
      "path": "secret/data/dotmac/platform-cp/rehearsal-attester/signing-key",
      "operation": "read",
      "source": "dotmac_platform_control_plane docs/design/gate0-d-implementation-spec.md:951@9e06e129d415d940387fc2a509c54e26f06c0466",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ]
    },
    {
      "path": "secret/data/dotmac/platform-cp/rehearsal-attester/trust-state",
      "operation": "read",
      "source": "dotmac_platform_control_plane docs/design/gate0-d-implementation-spec.md:344,952@9e06e129d415d940387fc2a509c54e26f06c0466",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ]
    },
    {
      "path": "secret/data/dotmac/platform-cp/nonexistent-sentinel",
      "operation": "read",
      "source": "fixture:policy_denies",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ],
      "note": "Proves the prefix deny rather than a missing record."
    },
    {
      "path": "secret/metadata/dotmac/platform-cp/",
      "operation": "list",
      "source": "fixture:policy_denies",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ]
    },
    {
      "path": "secret/metadata/",
      "operation": "list",
      "source": "fixture:policy_denies",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ]
    },
    {
      "path": "gate0-ssh/sign/rehearsal-controller",
      "operation": "update",
      "source": "dotmac_platform_control_plane docs/design/gate0-d-implementation-spec.md:459,949@9e06e129d415d940387fc2a509c54e26f06c0466; gate0-issuer-execution scripts/a8_oidc_proof.py@c460414707e8d20f7dcf0703b65a5f533fb010f1",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ]
    },
    {
      "path": "gate0-ssh/config/ca",
      "operation": "read",
      "source": "fixture:policy_denies",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ]
    },
    {
      "path": "gate0-ssh/roles/rehearsal-controller",
      "operation": "read",
      "source": "fixture:policy_denies",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ]
    },
    {
      "path": "auth/approle/role/platform-cp-rehearsal-issuer/secret-id",
      "operation": "update",
      "source": "fixture:implicit_deny_no_default_policy",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ]
    },
    {
      "path": "auth/approle/role/platform-cp-rehearsal-issuer/role-id",
      "operation": "read",
      "source": "fixture:implicit_deny_no_default_policy",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ]
    },
    {
      "path": "auth/approle/role/platform-cp-rehearsal-attester/secret-id",
      "operation": "update",
      "source": "fixture:implicit_deny_no_default_policy",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ]
    },
    {
      "path": "auth/approle/role/platform-cp-rehearsal-attester/role-id",
      "operation": "read",
      "source": "fixture:implicit_deny_no_default_policy",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ]
    },
    {
      "path": "sys/policies/acl/lane3-exposure-rehearsal",
      "operation": "read",
      "source": "fixture:implicit_deny_no_default_policy",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ]
    },
    {
      "path": "sys/audit",
      "operation": "read",
      "source": "fixture:implicit_deny_no_default_policy",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ]
    },
    {
      "path": "sys/mounts",
      "operation": "read",
      "source": "fixture:implicit_deny_no_default_policy",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ]
    },
    {
      "path": "auth/token/lookup-self",
      "operation": "read",
      "source": "fixture:implicit_deny_no_default_policy",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ],
      "note": "No default policy, so the usual self-lookup grant is absent."
    },
    {
      "path": "auth/token/renew-self",
      "operation": "update",
      "source": "fixture:implicit_deny_no_default_policy",
      "expected_outcome": "http_403",
      "evidence": [
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token"
      ]
    }
  ],
  "cross_role_login": [
    {
      "role": "rehearsal-issuer-protected",
      "jwt_audience": "lane3",
      "operation": "present a valid Lane 3 JWT (Lane 3 audience) to the Gate-0 role on auth/jwt/login",
      "source": "dotmac_platform_control_plane docs/design/gate0-d-implementation-spec.md:216@9e06e129d415d940387fc2a509c54e26f06c0466",
      "expected_outcome": "login_denied",
      "evidence": [
        "openbao_audit_login_failure_for_role",
        "no_token_issued",
        "denial_reason_class"
      ],
      "note": "Weak on its own: refused on audience, so the Gate-0 bound claims are not exercised. A variant using the Gate-0 audience is NOT in this matrix and needs separate approval."
    }
  ],
  "gate0_decision_dependency": {
    "status": "PENDING_NOT_APPROVED",
    "rows": [
      {
        "id": "W1_gate0_exchange_data_deny",
        "status": "PENDING_NOT_APPROVED",
        "text": "Lane 3 token denied create/update/read on gate0-exchange/data/*, including the templated path built from the Lane 3 run's own run_id and run_attempt."
      },
      {
        "id": "W1_gate0_exchange_metadata_deny",
        "status": "PENDING_NOT_APPROVED",
        "text": "Lane 3 token denied read/list on gate0-exchange/metadata/*."
      },
      {
        "id": "W1_explicit_lane3_deny_stanza",
        "status": "PENDING_NOT_APPROVED",
        "text": "Explicit deny of gate0-exchange/* in the Lane 3 policy (a narrowing that changes the B7 policy text, so it needs a new read-back and fixture update)."
      },
      {
        "id": "W3_cross_role_rerun_c1",
        "status": "PENDING_NOT_APPROVED",
        "text": "Re-run the cross-role login row after W3 changes the Gate-0 role on the shared auth/jwt mount; any change to auth/jwt/config re-opens every row."
      },
      {
        "id": "W3_alias_no_run_id_metadata",
        "status": "PENDING_NOT_APPROVED",
        "text": "A Lane 3 token's alias carries no run_id metadata and its identity_policies stay empty, so a claim-templated Gate-0 policy can never resolve for Lane 3."
      },
      {
        "id": "W4_W5_approle_rows",
        "status": "PENDING_NOT_APPROVED",
        "text": "The AppRole secret-id and role-id rows become mandatory (already listed above; keep them if the decision is ruled)."
      },
      {
        "id": "V2_key_paths",
        "status": "PENDING_NOT_APPROVED",
        "text": "Add the v2 signer and trust-state paths and ?version=N reads once named; a v2 path outside secret/data/dotmac/platform-cp/ is not covered by the explicit deny (implicit deny still applies). Update REQUIRED_DENIED_PATHS and the fixture at the pinned CP revision."
      }
    ]
  },
  "live_probe_plan": {
    "status": "NOT_AUTHORIZED"
  }
}
```
<!-- matrix:end -->

## Path-isolation rows

A valid Lane 3 token (from a Michael-approved positive run) must receive 403
on each path in `path_isolation`. The Gate-0 and CP rows are pinned to commit
SHAs (CP `9e06e129d415d940387fc2a509c54e26f06c0466`, Gate-0 issuer execution
`c460414707e8d20f7dcf0703b65a5f533fb010f1`); rows derived from the applied
policy cite the fixture. There are no UNKNOWN path rows: the validator refuses
them. `gate0-ssh/public_key` is deliberately excluded because it is
unauthenticated and returns 200 whatever the policy says.

A 403 alone is weak: it can come from a typo, a missing mount, or a token that
had already expired. Each row therefore requires:

- the **positive control**, run **after the last denial** within the same
  token's 300 second life (status only, body discarded and never logged), on
  `secret/data/dotmac/starter/lane3/vantage-topology`
  (`scripts/lane3_openbao_topology.py:27`). That proves the token was live for
  every 403;
- the OpenBao audit record of the denied request, tied to the token by HMAC
  equality of `auth.client_token` with the entry of the P0 login response (equal
  HMACs on one device mean the same token, without unhashing), with
  `auth.token_policies == ["lane3-exposure-rehearsal"]`, token type batch and an
  empty error on the positive control;
- two read-backs (`path_readbacks`): the issued token's `identity_policies` is
  empty, and a root session confirms each denied path's mount exists (`gate0-ssh/`
  and kv-v2 `secret/`), so a 403 cannot be "no such mount".

A fixture, a policy text review or a log line is vacuous. The Lane 3 signing
roles (`lane3-ssh/sign/observer`, `lane3-ssh/sign/inside-jump`, topology doc
lines 247-248) are allowed paths. The operator orphan-token proofs that already
returned 403 on these paths used the same policy but not a JWT-issued token,
which can carry entity or group `identity_policies`; they are prior art, not
evidence.

Cross-role: a Lane 3 JWT presented to the Gate-0 role is refused on audience, so
the Gate-0 bound claims are not exercised. A variant that presents the Lane 3
production identity with the Gate-0 audience is NOT in this matrix: if it ever
succeeded it would yield a batch Gate-0 token that cannot be revoked. It needs
its own explicit approval.

## Pending Gate-0 runtime decision (NOT APPROVED)

The matrix key `gate0_decision_dependency` lists rows that become mandatory if
the proposed Gate-0 runtime decision (W1-W5 and v2 keys) is ruled. It is marked
PENDING_NOT_APPROVED and the validator refuses the matrix if any listed row is
dropped, so they cannot be silently lost: `gate0-exchange/` data and metadata
denies including the run_id-templated path, an explicit Lane 3 deny stanza for
`gate0-exchange/*` (needs a new read-back and fixture update), a cross-role
re-run after W3 changes the shared `auth/jwt` mount, an alias check that no
`run_id` metadata or identity policy resolves for Lane 3, the AppRole rows, and
the v2 signer and trust-state paths. Nothing here is approved.

## Live-probe plan (NOT AUTHORIZED)

Nothing below may run until Michael approves a specific window. This section
is text only; no workflow, token request or OpenBao call is added by this PR.

Preconditions the window would need, each currently unmet:

1. A fresh read-back of the applied role diffed against the fixture, including
   the three leeways, which were not read back on 2026-10-10.
2. The witness consumer step in the protected job and the witness workflows
   (second workflow file, a `repository_dispatch` trigger) approved and merged
   on the execution repository's protected `main`, plus the new OIDC request
   path for the audience row. None exist. Every witness row depends on the
   consumer step because OpenBao is reachable only over WireGuard.
3. A disposable other-repository and, if achievable, another owner for the
   repository, repository_id and owner rows.
4. Audit correlation by HMAC equality with the P0 login, or a Michael root
   `sys/audit-hash` step. The audit device stays non-raw.
5. Admission order from topology doc section 7 respected: runner shown idle,
   negatives queued, then the positive dispatch.

Execution, one row at a time, each with its own approval and its own
evidence record:

| Step | Action | Evidence the row must yield |
| --- | --- | --- |
| P0 | Positive run: log in, read topology (status only) | Audit success for the role, token batch, nonrenewable, TTL at most 300; this is the control, not a negative |
| N1 | Different repository token | Login denied; audit failure for the role; no token; credited to repository + repository_id + workflow_ref jointly |
| N2 | Different owner token (if available) | As N1; credited to the four-claim set |
| N3 | Wrong Environment, then missing Environment (second-file witness) | Two denials; credited to `environment` + `workflow_ref` jointly. DECISION FOR MICHAEL: crediting `environment` alone needs the A8-style production-workflow widening |
| N4 | Non-main ref with witness Environment | Denied; credited to ref + workflow_ref + environment |
| N5 | Second workflow file on main, production Environment | Denied; credited to `workflow_ref` only |
| N6 | `repository_dispatch` witness | Denied; credited to event_name + workflow_ref |
| N7 | Nonce audience bound by no role, from a production-identity run (never the Gate-0 audience) | Denied with an audience reason class; credited to `audience` |
| N8 | UNPRODUCIBLE under the 5 minute job timeout. DECISION FOR MICHAEL: a longer timeout for a proof run, or accept unproven | If approved: replay of the P0 JWT after exp plus worst-case leeway; denied with `token is expired`; credited to `expiry`. Forging and exporting the JWT are forbidden |
| C1 | Lane 3 JWT (Lane 3 audience) to `rehearsal-issuer-protected` | Denied; audit failure for that role; no token. Weak: refused on audience |
| I1 | P0 token tries each `path_isolation` row | 403 each, audit denial tied to the token by HMAC equality, then the positive control after the last denial inside the same 300 second life; read-backs for empty `identity_policies` and mount existence |

Every step: no token, JWT or response body in any log or artifact; results
recorded as status, run and audit coordinates only. A GitHub refusal to
schedule a job, an Environment block, or a missing run is recorded as "no
token produced", which is not a pass for any row.
