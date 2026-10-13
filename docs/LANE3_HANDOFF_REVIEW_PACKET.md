# Lane 3 handoff installation review packet

This change bundles launcher admission, the installation/recovery mechanism,
its hosted verification, and the matching independent-checker pin proposal.
Source acceptance does not authorize a protected workflow run or declare
Gate 0 passed. No live installation is performed by this PR.

## Source identities

- Starter controller source merged in #795:
  `ffbd669c244d107ba17cb9d78912270c4d5f096d`.
- Launcher #10 merge:
  `0f3db810bca29a9b0a6f82319a7ecef39cf53a3e`.
- The launcher retains its reviewed nine-module supplier pin:
  `f867f20a2d17372172ca3a0d09a5eb4a72b62961`.

The installer and management entry point belong to this change's reviewed
revision. Controller bytes, job supplier bytes, and independent admission
have distinct owners and digests. An installer release must not silently
replace the launcher's supplier pin with its own source commit.

## Independent-checker proposal — not applied

For dotmac-tech/lane3-source-isolation draft #1, propose exactly:

| Field | Proposed value |
| --- | --- |
| baseline | `0f3db810bca29a9b0a6f82319a7ecef39cf53a3e` |
| `.github/workflows/lane3-exposure-rehearsal.yml` blob | `4edb7bfee47230a3b55c1d3b8ec61cafc1651abb` |

Keep every other policy field unchanged, including source-policy workflow
blob `3845ed01fe73f5c6d297d6a2ce2e2a783932f9e1`. This proposal does not edit
that repository. Its AGENTS.md requires external review of the exact update
before changing policy.json. The checker remains inactive; N3 is not claimed
as executed or its structural substitute adopted.

## Installation acceptance

Review the installer and management boundary together with the protected
input manifest. Independently supply the admission record, finite broker
policy, host identity/firewall restoration dependency, controller bytes,
supplier installation record, archive digest and coordinator configuration.
No per-run approval or credential value belongs in this packet.

Require hosted transaction, drift, interruption and rollback tests; exact
installed-byte and ownership readback; boot reconciliation before management
is enabled; and refusal throughout an incomplete installation. A failed
cleanup must retain containment and report the failure rather than mark the
host ready.

Hosted synthetic identity, kernel WireGuard and systemd checks have bounded
claims. They do not establish an actual fleet reboot, production OpenBao
exchange, independently accepted source policy, or the complete B8 matrix.
Those observations belong to the separately reviewed controlled live packet.

## Protected execution

After exact-head hosted checks, review and merge, stage and verify immutable
installation bytes without dispatching a job. The controlled proof window
requires its specific Environment review, finite approved origins and rollback
budget. Retain expiry, interruption, lock-collision, firewall-baseline and
cleanup evidence. CP readiness and remaining Gate-0 obligations still apply.
`admission_evidence` remains null and `0.4.0a2` remains unallocated.
