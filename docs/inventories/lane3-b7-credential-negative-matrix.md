# Lane 3 B7 credential-negative claim matrix

Status: SOURCE ONLY. Live execution is NOT AUTHORIZED. Nothing in this
document or its validator contacts OpenBao, GitHub or any host, and nothing
here is admission evidence. It closes the planning gap for the "Credential
failure" row of `docs/LANE3_EXECUTION_TOPOLOGY.md` section 7 (line 367): each
wrong bound claim must be denied by the live Lane 3 **B7** OpenBao JWT role
`lane3-exposure-rehearsal`, and a Lane 3 token must not read Gate-0 or Control
Plane signer paths.

The Gate-0 issuer role's A8 refusal evidence belongs to a different role
(`rehearsal-issuer-protected`) and does not transfer. It is used here only as
prior art for how GitHub produces wrong claims and for which claims move
together.

## What the role definition is, and is not

The B7 role definition in source is the "Workflow identity" paragraph of
`docs/LANE3_EXECUTION_TOPOLOGY.md` (lines 230-238), the values in
`.github/lane3-execution.json` (lines 4-9) and the constants in
`scripts/lane3_openbao_topology.py` (`ROLE` line 24, `AUDIENCE` line 25,
`KV_PATH` line 27, `MAX_TOKEN_TTL` line 29). The names are marked "proposed" in
the topology document. The provisioning script (`b67-model.py`) is kept outside
this repository, so the **provisioned** role is UNKNOWN until a read-back.
`scripts/lane3_b7_credential_negatives.py` derives the claim list from those
three files and refuses on disagreement between them.

Bound claims (equality, `bound_claims`): `repository`, `repository_id`,
`repository_owner_id`, `environment`, `ref`, `workflow_ref`, `event_name`.
Audience (`bound_audiences`, not a bound claim): `urn:dotmac:lane3:exposure-rehearsal`.
Token: batch, nonrenewable, lifetime at most 300 seconds, sole policy
`lane3-exposure-rehearsal` (reader checks, `scripts/lane3_openbao_topology.py`
lines 206-216). Expiry is not a bound claim; section 7 requires it as a row.

Not bound in source, so not rows: `sub`, `job_workflow_ref`, `workflow`,
`actor`, `run_id`, `sha`. UNKNOWN: whether the provisioned role sets
`bound_subject`, `bound_issuer`, `expiration_leeway`, `not_before_leeway` or
`clock_skew_leeway`; none is in source.

## Coupling (why a denial is not always credited to one claim)

A8 learned that a wrong `event_name` also changes `workflow_ref`, so a denial
there cannot be credited to `event_name` alone. The same mechanics apply here:

- `workflow_ref` is `<repository>/<workflow path>@<ref>`. Any repository name
  or ref change therefore changes `workflow_ref` too.
- `repository_id` and `repository_owner_id` are immutable. The only
  non-destructive way to present different values is a different repository,
  which also changes `repository` and `workflow_ref`.
- The protected Environment is `main`-only. A token with a wrong `ref` cannot
  come from it, so the witness job must use another Environment or none, which
  changes or drops the `environment` claim as well. If the Environment blocks
  the run instead, GitHub refused scheduling and no token exists: vacuous.
- The production workflow is `workflow_dispatch` only. A different
  `event_name` needs a second workflow file (as in A8), so `workflow_ref`
  changes with it. Adding a trigger to the production workflow to isolate
  `event_name` would weaken the production control and is not proposed.

A coupled denial is credited to the whole changed set, never to one claim.
Isolable rows: `environment`, `workflow_ref` (second workflow file on `main`,
same Environment), `audience`, `expiry`.

## Matrix

Machine-readable form (authoritative for the validator; the prose above
explains it). Evidence types accepted: `openbao_audit_login_failure_for_role`
(an OpenBao audit record of a failed login on `auth/jwt/login` for this role),
`no_token_issued` (no `auth.client_token` in the response, no token accessor in
audit), and optionally `denial_reason_class` (OpenBao's claim-mismatch message
class, recorded without values). Vacuous and refused by the validator: GitHub
scheduling refusals, Environment protection blocks, fixtures, unit tests, local
JWT decoding, log lines, workflow logs, policy text review, a positive login.

Note: audit records hash request strings by default, so tying a failure to the
role needs the audit-hash of the role name or raw audit logging. How the later
window does that is UNKNOWN and must be decided before it runs.

<!-- matrix:begin -->
```json
{
  "schema": "Lane3B7CredentialNegativeMatrix.v1",
  "role": "lane3-exposure-rehearsal",
  "live_execution": "NOT_AUTHORIZED",
  "claims": [
    {
      "claim": "repository",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:231; .github/lane3-execution.json:4",
      "production": "Real run in a different repository (disposable, same owner) minted for the fixed audience; repository, repository_id and workflow_ref all differ.",
      "isolable": false,
      "coupled_with": ["repository_id", "workflow_ref"],
      "credited_to": ["repository", "repository_id", "workflow_ref"],
      "expected_outcome": "login_denied",
      "evidence": ["openbao_audit_login_failure_for_role", "no_token_issued", "denial_reason_class"]
    },
    {
      "claim": "repository_id",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:231; .github/lane3-execution.json:5",
      "production": "No isolated producer: the ID is immutable per repository. Same run as the repository row; credited jointly.",
      "isolable": false,
      "coupled_with": ["repository", "workflow_ref"],
      "credited_to": ["repository", "repository_id", "workflow_ref"],
      "expected_outcome": "login_denied",
      "evidence": ["openbao_audit_login_failure_for_role", "no_token_issued", "denial_reason_class"]
    },
    {
      "claim": "repository_owner_id",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:231; .github/lane3-execution.json:6",
      "production": "Real run in a repository under a different owner; owner ID, repository, repository_id and workflow_ref all differ. UNKNOWN whether a disposable second owner is available.",
      "isolable": false,
      "coupled_with": ["repository", "repository_id", "workflow_ref"],
      "credited_to": ["repository", "repository_id", "repository_owner_id", "workflow_ref"],
      "expected_outcome": "login_denied",
      "evidence": ["openbao_audit_login_failure_for_role", "no_token_issued", "denial_reason_class"]
    },
    {
      "claim": "environment",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:232; .github/lane3-execution.json:9",
      "production": "Witness job in the execution repository on main, same workflow identity, with a differently named Environment (wrong) and with no Environment (missing); two variants, both must be denied. A8 produced this isolation for the Gate-0 role.",
      "isolable": true,
      "coupled_with": [],
      "credited_to": ["environment"],
      "expected_outcome": "login_denied",
      "evidence": ["openbao_audit_login_failure_for_role", "no_token_issued", "denial_reason_class"]
    },
    {
      "claim": "ref",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:232; .github/lane3-execution.json (ref fixed to refs/heads/main in section 7 line 83)",
      "production": "Dispatch from a non-main branch. The protected Environment is main-only, so a token needs a witness Environment or none; workflow_ref (embeds the ref) and environment change with it.",
      "isolable": false,
      "coupled_with": ["workflow_ref", "environment"],
      "credited_to": ["environment", "ref", "workflow_ref"],
      "expected_outcome": "login_denied",
      "evidence": ["openbao_audit_login_failure_for_role", "no_token_issued", "denial_reason_class"]
    },
    {
      "claim": "workflow_ref",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:83,232; .github/lane3-execution.json:7",
      "production": "Second workflow file on main in the execution repository using the production Environment (the A8 witness-workflow pattern). Only workflow_ref differs among the bound claims; job_workflow_ref also differs but is not bound.",
      "isolable": true,
      "coupled_with": [],
      "credited_to": ["workflow_ref"],
      "expected_outcome": "login_denied",
      "evidence": ["openbao_audit_login_failure_for_role", "no_token_issued", "denial_reason_class"]
    },
    {
      "claim": "event_name",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:233",
      "production": "Witness workflow triggered by repository_dispatch (as in A8). The production workflow is workflow_dispatch only, so workflow_ref differs too; credited jointly.",
      "isolable": false,
      "coupled_with": ["workflow_ref"],
      "credited_to": ["event_name", "workflow_ref"],
      "expected_outcome": "login_denied",
      "evidence": ["openbao_audit_login_failure_for_role", "no_token_issued", "denial_reason_class"]
    },
    {
      "claim": "audience",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:234-235; scripts/lane3_openbao_topology.py:25",
      "production": "Real production-identity run requests its OIDC token with a different audience. The shipped supplier (scripts/lane3_github_oidc.py:381,390) refuses any other audience, so the probe needs its own approved request path.",
      "isolable": true,
      "coupled_with": [],
      "credited_to": ["audience"],
      "expected_outcome": "login_denied",
      "evidence": ["openbao_audit_login_failure_for_role", "no_token_issued", "denial_reason_class"]
    },
    {
      "claim": "expiry",
      "source": "docs/LANE3_EXECUTION_TOPOLOGY.md:367; scripts/lane3_openbao_topology.py:29",
      "production": "Replay a token the role accepted earlier after exp plus the role's leeway. Leeway on the provisioned role is UNKNOWN; the wait must exceed it. All other claims are valid by construction.",
      "isolable": true,
      "coupled_with": [],
      "credited_to": ["expiry"],
      "expected_outcome": "login_denied",
      "evidence": ["openbao_audit_login_failure_for_role", "no_token_issued", "denial_reason_class"]
    }
  ],
  "path_isolation": [
    {
      "path": "secret/data/dotmac/platform-cp/rehearsal-issuer/signing-key",
      "operation": "read",
      "source": "dotmac_platform_control_plane docs/design/gate0-d-implementation-spec.md:262,951",
      "expected_outcome": "http_403",
      "evidence": ["openbao_audit_permission_denied_for_token", "http_403", "positive_control_same_token"]
    },
    {
      "path": "secret/data/dotmac/platform-cp/rehearsal-issuer/trust-state",
      "operation": "read",
      "source": "dotmac_platform_control_plane docs/design/gate0-d-implementation-spec.md:343,952",
      "expected_outcome": "http_403",
      "evidence": ["openbao_audit_permission_denied_for_token", "http_403", "positive_control_same_token"]
    },
    {
      "path": "secret/data/dotmac/platform-cp/rehearsal-attester/signing-key",
      "operation": "read",
      "source": "dotmac_platform_control_plane docs/design/gate0-d-implementation-spec.md:951",
      "expected_outcome": "http_403",
      "evidence": ["openbao_audit_permission_denied_for_token", "http_403", "positive_control_same_token"]
    },
    {
      "path": "secret/data/dotmac/platform-cp/rehearsal-attester/trust-state",
      "operation": "read",
      "source": "dotmac_platform_control_plane docs/design/gate0-d-implementation-spec.md:344,952",
      "expected_outcome": "http_403",
      "evidence": ["openbao_audit_permission_denied_for_token", "http_403", "positive_control_same_token"]
    },
    {
      "path": "gate0-ssh/sign/rehearsal-controller",
      "operation": "update (sign)",
      "source": "dotmac_platform_control_plane docs/design/gate0-d-implementation-spec.md:459,949; gate0-issuer-execution scripts/a8_oidc_proof.py (SIGN)",
      "expected_outcome": "http_403",
      "evidence": ["openbao_audit_permission_denied_for_token", "http_403", "positive_control_same_token"]
    },
    {
      "path": "UNKNOWN",
      "operation": "any other Gate-0 or CP path (KV metadata, list, sys/policies, auth/token/renew-self)",
      "source": "not enumerated in source; the Lane 3 policy text (topology doc line 238) denies 'everything else' without a list",
      "status": "UNKNOWN"
    }
  ],
  "cross_role_login": [
    {
      "role": "rehearsal-issuer-protected",
      "operation": "present a valid Lane 3 JWT (Lane 3 audience) to the Gate-0 role on auth/jwt/login",
      "source": "dotmac_platform_control_plane docs/design/gate0-d-implementation-spec.md:216",
      "expected_outcome": "login_denied",
      "evidence": ["openbao_audit_login_failure_for_role", "no_token_issued", "denial_reason_class"]
    }
  ],
  "live_probe_plan": {
    "status": "NOT_AUTHORIZED"
  }
}
```
<!-- matrix:end -->

## Path-isolation rows

A valid Lane 3 token (from a Michael-approved positive run) must receive 403
on each path above. A 403 alone is weak: it can come from a typo, a missing
mount or a token that was already expired. Each row therefore also requires
the **positive control**: the same token, within its 300 second life, succeeds
on the one path it is allowed (`secret/data/dotmac/starter/lane3/vantage-topology`,
`scripts/lane3_openbao_topology.py:27`); status only, the body is discarded
and never logged. Evidence also needs the OpenBao audit record of the denied
request for that token. A fixture, a policy text review or a log line is
vacuous. The Lane 3 signing roles (`lane3-ssh/sign/observer`,
`lane3-ssh/sign/inside-jump`, topology doc lines 247-248) are allowed paths and
out of scope for denial. The Gate-0 and CP path strings come from another
repository, not from this one; they must be re-read from their sources before
the window.

## Live-probe plan (NOT AUTHORIZED)

Nothing below may run until Michael approves a specific window. This section
is text only; no workflow, token request or OpenBao call is added by this PR.

Preconditions the window would need, each currently unmet or UNKNOWN:

1. B7 role, policy and audit device provisioned and read back (bound claims,
   audience, token type, TTL, leeway), with the read-back diffed against this
   matrix. Today the role is proposed in source only.
2. Lane 3 execution repository witness workflows approved and merged on its
   protected `main` (second workflow file, a `repository_dispatch` trigger, an
   Environment variant, an audience variant). None exist.
3. A disposable other-repository and, if achievable, another owner for the
   repository, repository_id and owner rows.
4. An audit-correlation method that works with hashed audit strings.
5. Admission order from topology doc section 7 respected: runner shown idle,
   negatives queued, then the positive dispatch.

Execution, one row at a time, each with its own approval and its own
evidence record:

| Step | Action | Evidence the row must yield |
| --- | --- | --- |
| P0 | Positive run: log in, read topology (status only) | Audit success for the role, token batch, nonrenewable, TTL at most 300; this is the control, not a negative |
| N1 | Different repository token | Login denied; audit failure for the role; no token; credited to repository + repository_id + workflow_ref jointly |
| N2 | Different owner token (if available) | As N1; credited to the four-claim set |
| N3 | Wrong Environment, then missing Environment | Two denials; credited to `environment` only; other six bound claims shown equal by decoding the JWT (decode is expectation only, not evidence) |
| N4 | Non-main ref with witness Environment | Denied; credited to ref + workflow_ref + environment |
| N5 | Second workflow file on main, production Environment | Denied; credited to `workflow_ref` only |
| N6 | `repository_dispatch` witness | Denied; credited to event_name + workflow_ref |
| N7 | Wrong audience from a production-identity run | Denied; credited to `audience` |
| N8 | Replay the P0 JWT after exp plus the role's leeway | Denied with an expiry reason class; credited to `expiry` |
| C1 | Lane 3 JWT to `rehearsal-issuer-protected` | Denied; audit failure for that role; no token |
| I1 | P0 token reads each Gate-0 and CP path above | 403 each, audit denial for the token, positive control on the allowed path in the same lifetime |

Every step: no token, JWT or response body in any log or artifact; results
recorded as status, run and audit coordinates only. A GitHub refusal to
schedule a job, an Environment block, or a missing run is recorded as "no
token produced", which is not a pass for any row.
