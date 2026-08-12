# Federated identity (OIDC) sources — ERP is the only implementation

**As of:** 2026-08-12
**Commits audited:** `dotmac_erp` 0f4b1698 (`feat/kernel-ui-contract-alignment`),
`dotmac_sub` 73c9d9003, `dotmac_vendor_control_plane` eb667fa,
`dotmac_academy_app` 5072e4a
**Status:** source audit complete; **production adoption unverified** (see the
open gate below). Extraction itself is blocked by ADR-0017 until the kernel
lineage runs in a product database.

## Why this audit exists now

ADR-0021 records that the Workspace builds no proprietary identity provider and
uses external OIDC. ADR-0006's product-first amendment then makes the qualifying
production implementation the mandatory starting point. ERP holds the fleet's
only tested OIDC implementation, so the audit and the parity suite can begin
immediately even though the extraction cannot.

## What ERP has

`app/services/sso/oidc.py` — 409 lines. Its own docstring states the boundary
the extraction must preserve:

> The provider authenticates a user and signs an ID token. ERP maps the
> provider's opaque `(issuer, subject)` identity to a local person, then issues
> an ERP-owned session. Provider roles, permissions, sessions, and cookies are
> never authoritative in ERP.

| Element | Where | Note |
|---|---|---|
| Authorization Code + PKCE | `app/services/sso/oidc.py` | |
| Discovery + JWKS validation | same | |
| ID-token algorithm allowlist | `_ALLOWED_ID_TOKEN_ALGORITHMS` | `RS256/384/512`, `ES256/384/512` — **HMAC families deliberately absent**, which is what stops the classic `alg` confusion attack |
| Signed state cookie, 600s TTL | `OIDC_STATE_COOKIE`, `OIDC_STATE_TTL_SECONDS` | |
| `(issuer, subject)` → local person | `FederatedIdentity`, `app/models/auth.py:98` | |
| External roles ignored | by construction | the property that makes the IdP replaceable |
| Local session issued after validation | `app/services/auth_web.py` | |

`FederatedIdentity` carries two unique constraints, and both matter:
`uq_federated_identities_issuer_subject` (one external identity binds to at most
one person) and `uq_federated_identities_person_issuer` (one person holds at
most one identity per issuer). Table created by
`alembic/versions/20260720_add_federated_identity.py`.

**Consumers:** `app/web/auth.py`, `app/api/auth_flow.py`,
`app/services/auth_web.py`, `app/services/sot_relationships.py`.

**Configuration:** eight `OIDC_*` environment knobs (`app/config.py:133-141`) —
enabled, issuer, client id, client secret, discovery URL, redirect URI, scopes,
request timeout.

**Tests:** `tests/test_oidc_boundary.py`, four tests:

- `test_start_login_uses_authorization_code_pkce_and_signed_state`
- `test_complete_login_maps_issuer_subject_to_local_person_only`
- `test_complete_login_rejects_unlinked_external_identity`
- `test_binding_can_be_reenabled_idempotently`

Those four are the parity suite. The third is the load-bearing one: an
unlinked external identity is **refused**, not just-in-time provisioned. An
extraction that quietly adds JIT provisioning would turn "authenticated by the
IdP" into "authorized in the product", which is exactly the boundary the
docstring claims and ADR-0021 §2 depends on.

## The open gate: is it on in production?

**Not answerable from the repository, and it must not be assumed.**
`app/config.py:133` reads

```python
oidc_enabled: bool = os.getenv("OIDC_ENABLED", "false").lower() == "true"
```

The default is **off**, and the live value is environment-supplied. So the code
is implemented, wired into two front doors, migrated and tested — but whether
any production ERP deployment authenticates through it is a fact that lives on
the ERP host, not in git.

**Next action:** read `OIDC_ENABLED` (and whether `OIDC_ISSUER` is populated) in
the ERP production environment. This requires Michael to name the host; no
inference from historical environment mappings.

Why the answer changes the plan:

- **Enabled in production** → ERP is the mandatory product-first source. The
  extraction ports this implementation and these four tests, and ERP is the
  first cutover.
- **Not enabled anywhere** → there is no production implementation, so
  `product-first` does not apply and the work is
  `greenfield-after-inventory` *informed by* ERP's code. Materially different
  dossier, and a materially weaker claim about the design being proven.

Recording the distinction rather than resolving it, because guessing here would
put an unearned `product-first` in a dossier.

## No second implementation exists

`dotmac_sub`, `dotmac_vendor_control_plane` and `dotmac_academy_app` contain no
OIDC/OAuth authorization-code implementation. There is nothing to reconcile and
no competing vocabulary — unusually for this fleet, this capability has exactly
one source.

## Target shape, when the gate opens

Two pieces, per ADR-0021:

1. **A kernel authentication-provider / binding contract** — the seam a product
   implements to accept an externally authenticated subject and bind it to a
   local identity. Contract only; the kernel ships no provider client.
2. **A small `dotmac-auth-oidc` adapter distribution** — discovery, JWKS, PKCE,
   the algorithm allowlist, state handling. Depends on `httpx`/`jose`, which is
   precisely why it is a separate distribution rather than kernel surface: the
   kernel must not acquire an HTTP client.

Both are blocked by ADR-0017 today. The audit above, and porting the four
parity tests against a contract stub, are not.

## Defects not to carry forward

Small, but they are the kind of thing an extraction silently inherits:

- `from app.services.auth_flow import _jwt_secret` — a private cross-module
  import. The extracted adapter must take its signing material as a parameter,
  not reach into a sibling's private name.
- `try: from datetime import UTC except ImportError` — a Python 3.10 shim. The
  kernel floors at 3.11, where `datetime.UTC` always exists.
- The state cookie's TTL is a module constant rather than configuration. It
  should be a declared setting with a documented default, per the
  everything-by-config rule.
