# Machine credentials — ERP and Sub, inventoried before the kernel facility

**Dated 2026-08-22.** Product-first inventory (ADR-0006 amendment, hard rule 24)
for a proposed `dotmac-kernel` machine-authentication facility. Two products
already authenticate machines with an `X-Api-Key` header, and they disagree
about what the credential means. That disagreement is the argument for one
owner, and it is the reason this document exists before any kernel code.

Sources read at `dotmac_sub@5c56b64a2` and `dotmac_erp@0f4b1698`.

## Why a kernel facility rather than a third local one

`dotmac-isp` needs to serve an authenticated destination descriptor to the
Integrator (ADR-0010 gate 2b). The kernel offers `require_tenant`,
`require_user_auth`, `require_role` and `require_platform_admin` — all
human/tenant-actor auth — and ISP runs with `platform_surface_enabled=False`,
so the platform-admin guard is not even available to it.

Writing a third implementation in ISP would make three fleet mechanisms for one
capability, and the two that exist already demonstrate how that ends: they have
diverged on the single most security-relevant question in the design.

## The compatible precedent, from Sub

These are the behaviours the kernel facility should PRESERVE. They are Sub's,
and they are right.

| Behaviour | Where |
| --- | --- |
| `X-Api-Key` header as the machine credential | `app/services/auth_dependencies.py::_api_key_principal` |
| Access is EXACTLY the key's `scopes`; the principal carries `roles: []`, so there is no administrator shortcut | same, and the docstring says so explicitly |
| `is_active`, `revoked_at`, and `expires_at` all checked in the lookup predicate | same |
| A first-class principal rather than a human impersonation | same |

## What must NOT be carried forward

### Sub

**1. Unsalted SHA-256 whenever no secret is configured.**
`hash_api_key` returns `_legacy_sha256(value)` when `_api_key_hmac_secret()` is
`None`, and `hash_api_key_candidates` always appends the legacy form so a
pre-migration row still authenticates. The comment says prod always has the key
— but the fallback is a property of the CODE, not of the environment, and a
guard that depends on configuration to be safe is not a guard.

**2. The verification key is derived from a product encryption key.**
`_api_key_hmac_secret` calls `credential_crypto.get_encryption_key()` — the
Fernet key connector-credential encryption already depends on — and derives a
subkey. A separate subkey is better than reuse, but it still means rotating the
connector encryption key invalidates every machine credential, and the two
concerns cannot be operated independently.

**3. Authentication writes and COMMITS during a GET.**
`_touch_api_key_last_used` ends in `db.commit()`. `_maybe_upgrade_api_key_hash`
rehashes a legacy key on successful auth and, per its own docstring, is
"committed unconditionally". A read request that commits is a transaction
boundary the caller did not ask for, and it puts authentication telemetry inside
the request's own transaction.

**4. The system-user association is documented, not enforced.**
`subscriber_id` is optional; when absent the key's own id becomes both
`subscriber_id` and `person_id`. Documentation associates machine keys with a
system user; neither the code nor the tests require it.

### ERP

ERP is an independent implementation, not a copy, and it carries more that must
not survive.

**5. An unscoped key grants EVERYTHING.**

```python
def has_scope(self, scope: str) -> bool:
    """An unscoped key (NULL/empty scopes) grants everything — that's the
    grandfathered default."""
    if not self.scopes:
        return True
```

This is the finding that matters most. **The two products mean opposite things
by an empty scope list**: Sub restricts a key to exactly its scopes, ERP treats
an empty list as unrestricted. A credential migrated or mis-created with no
scopes is inert in one product and omnipotent in the other.

**6. Plain unsalted SHA-256, with no HMAC path at all.**
`hash_api_key` is `hashlib.sha256(value.encode()).hexdigest()`. Sub at least has
a preferred HMAC form; ERP has only the form Sub calls legacy.

**7. A machine credential requires a human `person_id`.**
`app/api/service_principal.py` refuses a key without one and then loads the
`Person`. This is the inverse of Sub's gap — ERP enforces a coupling that a
machine principal should not have — and it is why the kernel type must be its
own principal rather than a human wearing a service label.

**8. Authentication-time telemetry, again.** `_last_used_is_stale(...)` on the
GET path, the same shape as Sub's.

## The disagreement, stated plainly

| Question | Sub | ERP |
| --- | --- | --- |
| Empty scopes mean | nothing is authorized | **everything** is authorized |
| Hash | HMAC-SHA256, falling back to unsalted SHA-256 | unsalted SHA-256 only |
| Verification key | derived from the connector encryption key | none |
| Human user required | documented, not enforced | **enforced** |
| Writes during authentication | `last_used_at` + rehash, committed | `last_used_at` |

Five rows, five divergences, one capability. Neither implementation qualifies as
the extraction source unchanged; Sub is the behavioural reference for the WIRE
(header, scope semantics, revocation and expiry) and neither is a reference for
the storage, key custody or transaction behaviour.

## What the kernel facility owes

Directed 2026-08-22, and recorded here so the release can be checked against it:

- a tenant-scoped, RLS-protected machine credential;
- `X-Api-Key` verification against a DEDICATED held key — not a key borrowed
  from another purpose;
- exact declared scopes, and **empty scopes authorize nothing**;
- active, expiry and revocation checks;
- a typed `MachinePrincipal`;
- `require_machine_scope(...)` operating inside an already-established tenant
  scope;
- no implicit administrator access, no wildcard scope, no human-user
  requirement, no commit, no rollback, and no authentication-time telemetry
  write.

Canary coverage owed: wrong tenant, revoked, expired, unknown and unscoped
credentials, plus secret-leakage and transaction-boundary tests.

## Adoption is REISSUANCE, not a hash migration

Correcting an earlier draft of this section, which said each product would
migrate its stored hashes. **It cannot.**

A stored digest — Sub's HMAC or either product's SHA-256 — contains neither the
raw key nor any material from which the new dedicated-key HMAC could be
computed. That is the property the hashing exists for. There is no conversion,
no dual-read window that eventually rewrites rows, and no backfill: every
credential in both products must be **reissued**.

That changes the shape of both cutovers from a migration into a credential
rotation with an operator on the other end of it, and it is why neither product
retires on the kernel release.

### Sub

Compatible authorization semantics — scope-exact, no admin shortcut — so nothing
about its access model has to change. It still needs full reissuance, because
its verification key is derived from the connector-credential encryption key and
the kernel's is a dedicated held secret. The two cannot agree on a digest, so
existing rows cannot authenticate against the new facility whatever else is
true.

### ERP

The more careful cutover, and the steps are not optional:

1. **Inventory every active null/empty-scoped key by IDENTIFIER only** — never a
   secret value, and never a digest that could be replayed against a weak hash.
2. **Assign each an owner and explicit minimum scopes.** An empty list is not a
   fact about the key; it is the absence of one, and somebody has to supply it.
3. **Revoke unused keys; reissue retained ones** into the kernel facility.
4. **Treat every unresolved active key as a CUTOVER BLOCKER.** Not a warning,
   not a follow-up: a key nobody can account for is exactly the credential the
   empty-scope default made dangerous.
5. **Never translate an empty scope list into a wildcard or "all current
   permissions."** That would carry ERP's defect through the extraction wearing
   a kernel badge — and it is the tempting shortcut precisely because it makes
   the migration look clean.
6. **Run a bounded old/new verification transition with a CHECKED retirement
   deadline.** The legacy verifier stays in ERP for that window and never enters
   the kernel; the kernel has one scheme and no fallback, which is the whole
   point of extracting it.
