# Compatibility - dotmac-connector-smtp 0.1.0a1

| Surface | Contract |
|---|---|
| Distribution | `dotmac-connector-smtp` |
| Connector key | `smtp` |
| Version | `0.1.0a1` (unreleased, release held back) |
| Capability | `email.send.v1` (no domain contract is published yet, so no digest is claimed) |
| Mode | `DELIVERY` |
| SPI | `>=1.7,<2.0` |
| Egress | no provider hosts (deny-all); destination is an operator-bound network target |
| Fleet ceiling | `tcp` 587 only |
| Secret binding | `smtp_password` (optional; required whenever a username is configured) |

## Floors, and why

- **dotmac-integration >=0.1.0a20.** The first published release carrying SPI
  1.7: `FleetTargetDeclaration`, `require_network_target`,
  `bound_network_targets` and the reserved `_network_targets` config key. SPI
  1.7 is also what a manifest must declare to state a fleet ceiling, which this
  connector needs (an empty ceiling would make every installation unusable).
  The declared floor is a published version, so the lock and the release gate
  agree.
- **dotmac-kernel: none.** The moved logic imports no kernel type (the three
  receipt-status literals in `classify.py` are restated and pinned by test), so
  the connector does not depend on the kernel at all; `dotmac-integration`
  carries its own kernel floor. The import-linter contract "Connector plugins
  depend only on the integration SPI" lists this package, and a test forbids
  `dotmac_kernel` and any second HTTP/transport client.
- Standard library only otherwise (`smtplib`, `ssl`, `email`, `socket`).

## Engine dependency (open): raise the floor before release

The `dotmac-integration` floor must rise to **a22** (the release that contains
the DELIVERY `_network_targets` fix) before this connector is released; a20 is
not enough for a working send.

## Engine dependency detail

The connector reads the operator's bound targets through
`bound_network_targets(request.config)`. `dispatch.prepare` does not yet put
`_network_targets` into a DELIVERY handler's config, so a real dispatch is
refused with `network_target_refused` (fail closed) until the engine does. This
is an engine change, not a connector one; it is not made here.

## Configuration (revision fields; none has a default)

`host` (TLS server name), `address` (IP literal the connection is made to),
`port`, `username` (empty means no login), `from_address`. STARTTLS is not a
setting: it is always required, with certificate and hostname verification and
TLS 1.2 minimum. Settings and secrets are referenced by name only.

## Outcome contract

| situation | outcome |
|---|---|
| 250 after DATA | `SUCCEEDED` (`provider_reference` is the Message-ID) |
| any failure after the DATA payload left | `RECONCILIATION_REQUIRED` (never retried) |
| RCPT 5.1.1/2/3/6/10, other permanent refusals before DATA, non-ASCII recipient | `TERMINAL` |
| connection, TLS, greeting, STARTTLS not offered, 4xx, soft 5.2/5.4 before DATA | `RETRYABLE` |
| bad configuration, off-target endpoint, missing/rejected credential, MAIL-stage refusal | `RETRYABLE` |
| malformed payload, attachment that cannot be resolved | `TERMINAL` |

Outcomes carry fixed codes and the SMTP reply code only: never a reply body, an
address, a message part or the password.

## Timeout budget (moved from dotmac-delivery-smtp, unchanged)

Both must be finite and > 0: 15 s per operation and 40 s for `data_timeout`,
which is reset to 15 s as soon as DATA returns. `data_timeout` is in force for
THREE waits inside `smtplib` `data()` (the 354 go-ahead, the payload send, the
final reply) and up to 11 other waits get `timeout` (TCP connect, greeting,
EHLO, STARTTLS reply, TLS handshake, EHLO, AUTH up to 2 replies, MAIL, RCPT,
QUIT). Nominal budget: 11 x `timeout` + 3 x `data_timeout`; the defaults give
11 x 15 + 3 x 40 = 285. These are per-operation budgets, not a wall-clock limit
(a server trickling bytes resets each one).

**Hard cap:** `SEND_DEADLINE_SECONDS` = 240 caps the whole send by watchdog (real worst case about 255 s: a TLS handshake in progress is not interruptible); the pump must check its lease against the connector deadline. **Lease coupling:** whatever leases one attempt must hold the lease for longer
than this budget, or a second worker may reclaim it and double-send. The kernel
relay's `stale_lease_seconds` is 300 (285 < 300); the Integrator outbound pump
(work package B5) must use a longer lease. A caller passing explicit timeouts
must keep inside the budget; it is NOT enforced here, and enforcement (a
construction-time refusal, lease renewal or an attempt watchdog) is a
follow-up. Raise the lease before raising these.

## Other limits

- STARTTLS submission only (no implicit-TLS port 465 mode).
- Attachments: 10 MiB each, 20 MiB total, at most 10, resolved by an
  assembly-supplied `AttachmentResolver`. The shipped plugin binds none, so a
  message with attachments is `TERMINAL` (`attachment_refused`) until one is.
- Bounce coverage is synchronous only; asynchronous DSNs are not captured.

## Governance ADR 0011: net-zero transfer (Michael, approved 2026-11-22)

The surface MOVES, it is not added. `dotmac-delivery-smtp` (never released) is
deleted in the same change that introduces this package, with its tests and the
fingerprint-pinned conserved exclusion for
`test_data_reply_uses_the_long_timeout`; `.dotmac/standards-profile.json`
`external_connector_surface.baselines.outbound_transport` goes 1 to 0. The
connector, being a locked source distribution with a connector entry point, is
outside the engineering-standards count. The comms-writer ratchet (ADR-0006 5f)
carries one `allowed` entry for this package's two `direct_smtp` findings,
replacing delivery-smtp's. The recorded 2026-11-18 transfer (0 to 1) therefore
returns to 0; Academy's own count is lowered with cutover evidence in its work
package A4/B15.
