# Request evidence context — product-first source inventory

Date: 2026-09-04

This inventory records the product-first evidence (hard rule 22 / ADR-0006's
2026-08-08 amendment) for `dotmac_kernel.request_evidence`, the kernel's
implementation of the Foundation's `request_evidence_context` concern: one owner
for the per-request record of **who** a request appears to be, **from where** it
arrived, and **under what correlation** it is logged.

It is repository-local evidence for implementation. It is **not** a release,
adoption, deployment, or production-state claim. Nothing here asserts that any
product composes the facility, and no profile binding is declared — see
"Deliberately not declared" below.

## Audited revisions

Measured at exact commits, never at a branch.

| Repository | Revision |
| --- | --- |
| `dotmac_starter_mt` | `0aa374c5` (branch base; see `EXTRACTION.toml` for the pin) |
| `dotmac_erp` | `e286636baea382e3978e779acd3155cea27e82b8` |
| `dotmac_sub` | `7c1d271dad54ad178fb90083305363f855622175` |

Sibling repositories were read through `git show` at those commits. No sibling
working tree was touched. `dotmac_erp`'s working checkout was on
`feat/po-status-single-owner`, which PREDATES both repairs recorded below — a
read of the checked-out tree would have inventoried the defective version, which
is the reason the pin is a commit and not a path.

`e286636b` is the effective ERP pin because it descends from `b917e787`
(verified by `git merge-base --is-ancestor`), so one commit carries both
repairs. `b917e787` is named separately where the context repair is discussed,
because the two failures are independent and a reader tracing one should not
have to unpick the other.

## What ERP had, and what was ported

`dotmac_erp:app/net.py` is the qualifying source for the trusted-proxy half. It
decides whether a forwarded client address, scheme or host may be believed, and
it has SIX production consumers — rate limiting (`app/middleware/rate_limit.py`),
CSRF (`app/web/csrf.py`), the password-reset URL (`app/api/auth_flow.py`), the HR
API (`app/api/people/hr.py`), the employee web surface
(`app/services/people/hr/web/employee_web.py`) and, since `b917e787`, the audit
trail through `app/services/audit_listener.py`.

Ported unchanged, including both repairs that landed with it:

* **A bare address derives its prefix from the address family.** The original
  appended `/32` regardless of family, so a bare `::1` parsed cleanly into
  `::/32` — 7.9e28 addresses, trusted, silently, with `strict=False` masking the
  host bits without complaint. It PARSED, so the malformed-entry refusal never
  saw it, and the direction was fail-OPEN. `::1` is the natural loopback proxy
  on a dual-stack host and ERP's `docker-compose.yml` already published the app
  service on it.
* **An explicit prefix is honoured as written.** `2001:db8::/32` survives. A
  "refuse every IPv6 entry" implementation satisfies every host-route assertion
  while quietly deleting legitimate configuration; that near-miss is carried in
  the parity suite rather than left to review.
* **A malformed entry refuses loudly.** The original caught the `ValueError` and
  `continue`d. That fails CLOSED for forwarded-header trust — a dropped entry
  trusts nobody — but SILENTLY for deployment correctness, and it destroys client
  provenance: the operator believes they configured a proxy they did not, and
  every one of the six consumers above then reads the proxy's address as the
  client's.

`dotmac_erp:app/observability.py` is the qualifying source for the context half,
and its defect at `b917e787` is the reason the kernel shape is what it is:
`actor_id_var.set()` was guarded by `if actor_id:` and nothing was ever reset, so
an anonymous request DECLINED to write and inherited whichever actor was
authenticated on that worker previously. A live cross-request identity leak
reaching every audit row, log line and field-tracker attribution.

## What was deliberately NOT ported

* **The import-time environment read.** ERP computes `_TRUSTED_PROXY_NETWORKS`
  from `os.getenv` when `app.net` is first imported, so the set cannot change
  without restarting and `monkeypatch.setenv` after import is inert — ERP's own
  parity tests had to patch the parsed constant and say so. ERP recorded this as
  a finding for whichever kernel contract carried the behaviour
  (`dotmac_erp:docs/inventories/2026-09-04-erp-proxy-trust-readback-hole.md`).
  The kernel takes a typed `TrustedProxyPolicy`; nothing in the module reads the
  environment, and an architecture test asserts that by AST.
* **JWT decoding inside the middleware.** ERP's middleware decodes a bearer token
  with `JWT_SECRET` read from the environment. The kernel has one authentication
  seam (`dotmac_kernel.deps.authenticate_request`) and does not grow a second, so
  the actor arrives through a product-supplied `actor_resolver` or through
  `bind_actor` after authentication has run. The default is anonymous, written
  explicitly.
* **`BaseHTTPMiddleware`.** See below — this one is a correctness difference, not
  a preference.

## Why pure ASGI is a correctness requirement

`BaseHTTPMiddleware` runs each request in its own anyio task, and a task runs in
a COPY of the context. That grants contextvar isolation for free, so a
concurrency proof written against it passes even when the underlying context
handling is broken. ERP measured exactly that and recorded it: its
`test_concurrent_requests_cannot_inherit_each_others_actor` was GREEN against the
unrepaired middleware, along with three other tests, and "a suite of only those
four would have been fully green against a live cross-request identity leak."

The kernel facility is a bare ASGI callable, so the isolation has to come from
its own `finally` resets. The same fact constrains how its tests are written:
`asyncio` copies the context per Task, so a task-based concurrency proof STILL
cannot fail. `tests/unit/test_request_evidence.py` therefore asserts that
directly — `test_the_concurrency_proof_alone_would_pass_against_the_broken_shape`
drives the planted broken creator through the concurrency assertion and requires
it to pass — and carries two proofs that CAN fail: a sequential
anonymous-after-authenticated leak, and a nested overlap inside one context.

## Sub, and the anti-pattern that is not carried forward

`dotmac_sub` is not a qualifying source for this concern — it has no
trusted-proxy resolver and no request evidence context. It is inventoried for
two shapes the extraction must not reproduce:

* `app/services/audit_helpers.py:704-710` recovers an actor kind by splitting an
  identifier on a separator (`actor_kind = prefix.lower() if separator else
  None`), making identity a parsing accident of whatever string a caller sent.
  The kernel refuses a bare string in scope state rather than coercing it.
* `app/api/crm.py` identifies its caller by the PRESENCE of an `integration:crm`
  scope, so identity becomes a side effect of authorization. The kernel carries
  kind, id and scopes as three independent facts, and derives none from another.

## One vocabulary owner

`EVIDENCE_ACTOR_KINDS` is DERIVED at runtime from `dotmac_kernel.audit`'s
`ACTOR_TYPES` (`system`, `user`, `api_key`, `service`) plus one addition,
`anonymous`, so a fifth audit kind cannot leave it behind. The addition is made
here rather than proposed to the audit contract deliberately: `ACTOR_TYPES`
answers "who performed this audited operation" and `resolve_audit_actor` is fatal
without an answer, while this set answers "who does this request appear to be",
whose honest answer is sometimes nobody. Widening the audit contract to admit an
anonymous actor would change what an audit row may assert, and that is a decision
for the audit contract's owner.

## Deliberately not declared

No `request_evidence_context` profile binding is registered, no provider entry
point is declared, and the Platform CP verifier is untouched. A binding whose
only consumer is a test is absent; the declaration belongs to the change that
brings an installed artifact and real assembly wiring with it. ERP is the named
first adopter and Sub follows.

## Open, and not claimed as covered

* **Two writers of `request_id_var`.** `dotmac_kernel.middleware.observability
  .ObservabilityMiddleware` also writes `dotmac_kernel.logging.request_id_var`.
  The two are ALTERNATIVES, not layers, and adoption must retire or reconcile one
  of them. Nothing composes the new facility today, so no runtime duplication
  exists — but no guard would catch it if a future assembly installed both.
* **The declared plan is not compared with the process.** ERP's own inventory
  records that `deploy/product.toml`'s `[ingress] trusted_proxies` is not
  compared with what the process actually parsed, and that its rendered compose
  does not carry `TRUSTED_PROXY_IPS` at all — so the rendered path reaches the
  lost-provenance state with no typo whatsoever, and the loud refusal cannot
  catch it, because unset is empty and empty is valid by design. Taking the
  policy as typed configuration removes the environment read; it does not, on its
  own, prove the declaration reaches the constructor. That readback belongs to
  the adoption change, and no guard here claims to cover it.
