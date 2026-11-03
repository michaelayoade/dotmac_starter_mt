# cutover-release-1: money-pack cutover aggregate

As of 2026-11-03, integration branch `cutover-release-1`, created from `main`
at `eafeabef`. This is the vehicle (ST-0) for the Starter constituents of the
money-pack cutover. Every constituent lands as a PR whose base is
`cutover-release-1`, not `main`. The aggregate PR to `main` follows an exact-head
review of the final head.

This is source preparation. It is not publication, installation, adoption or
deployment. Release dispatches happen only after the aggregate merges, run one
at a time, and each release record merges before the next dispatch. Nothing in
this file authorizes a dispatch.

Status values: `planned`, `draft` (PR open as a draft), `ready`, `merged`.
PR links are filled in on the aggregate PR description as constituents open.

| ID | Package | Scope | PR | Status |
| --- | --- | --- | --- | --- |
| ST-0 | aggregate | Integration branch and this inventory | TBD | draft |
| ST-1 | docs/adr | Bounded ADR 0024 section 2 ruling for composition packages; ADR 0020 wording amendments; fulfillment candidate-consumer and app-sync claim updates | TBD | planned |
| ST-2 | dotmac-billing | a11 primitives: credit-note excess, discounts, write-off as policy, per-document exposure and withholding queries, ordered accounting-fact query, mixed `service_ref` refusal | TBD | planned |
| ST-3 | dotmac-billing | a11 opening authority: held-account admission, seal authority-switch participant, opening-lane assembly authority port | TBD | planned |
| ST-4 | dotmac-billing | a11 opening drafter and adapter: rules as data, reseller account mapping, draft handling, verification gate, O(N) adapter, Starter-native verifier | TBD | planned |
| ST-5 | dotmac-payments | a4: refund instruction and cash-outcome observation, with migration | TBD | draft |
| ST-6 | dotmac-durable-timers | a2 fresh build: changelog, adoption plan rewritten to saved tests plus backtest, PostgreSQL proofs re-run; no runtime change | TBD | draft |
| ST-7 | dotmac-collections | a2 fresh build per the existing proposal (kernel floor a89, no runtime change) plus an X1 unknown-due-date conformance fixture | TBD | draft |
| ST-8 | dotmac-service-access-policy | a2: versioned access input and stale-input refusal, with a pure public comparison helper | TBD | planned |
| ST-9 | dotmac-imports | 0.2.0a2: manifest-file loader over existing leases, checkpoints and row applier | TBD | planned |
| ST-10 | flows package (name proposed) | Skeleton, table allowlist with architecture test, and the settle flow | TBD | planned |
| ST-11 | flows package | Funded-charge flow, access signal emission | TBD | planned |
| ST-12 | flows package | Governed adjustment, refund, cash reversal, attention-required read, X1 exposure-to-observation translator | TBD | planned |
| ST-13 | flows package | Opening lane assembly: imports row applier to billing opening import, with the product-supplied link-writer port | TBD | planned |
| ST-14 | dotmac-connector-paystack | 0.1.0a3 release, after verifying the scope of the unreleased refund change | TBD | planned |
| ST-15 | connector distribution (name proposed) | Accounting-facts connector on the integration SPI | TBD | planned |
| ST-16 | posting package (name proposed) | Accounting posting state machine, GL map, closed-period blocking | TBD | planned |

Release dispatch order after the aggregate merges: durable-timers a2,
collections a2, service-access-policy a2, imports 0.2.0a2; the remaining
packages follow their own gates.
