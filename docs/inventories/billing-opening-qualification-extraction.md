# Opening import qualification source inventory

Inventory written before implementation, 2026-10-14, against Starter
`8709449a1129aab3c8e39dcd6b6b7238ddeddfe0` (unaccepted PR 831 source).
This is a bounded PORT-DELTA over existing Billing owners, not production
qualification, publication, adoption, or authority-transfer evidence.

## Existing sources and retained owners

The product-first source coordinates and source dispositions remain in
[billing-opening-receivables-extraction.md](billing-opening-receivables-extraction.md),
[billing-opening-cash-import-extraction.md](billing-opening-cash-import-extraction.md),
and `packages/dotmac-billing/EXTRACTION.toml`. Sub's canonical settlement
resolver and export supply observed lifecycle components; ERP's opening GL
and invoice importers have different guarantees and do not supply immutable
operational opening authority. Neither a mutable export nor regenerable PDF
is authentic original documentary evidence. No product issuer is ported here.

| Existing owner | Reused behavior | PORT-DELTA binding |
|---|---|---|
| `opening_receivable_contracts.py` | Closed V2 command, complete manifest, verification request and result | Frozen source-evidence and exact-command grant envelopes |
| `opening_receivable_validation.py` | Command digest, complete manifest fingerprint, full-manifest preflight and preview verification seam | Pure qualification against independently retained source and freshly verified grant |
| `_opening_values.py` | Frozen recursively closed evidence validation | New envelopes inherit `OpeningCashValue` |
| Existing opening cash/AR tests | Valid mixed cash/AR, zero-residual lots, documentary and historical-discharge fixtures | Focused denial canaries reuse these fixtures without financial recalculation |

There is no new financial resolver, schema, version, writer, source issuer,
transport, evidence capture, or trust root. The full manifest is compared,
including unrelated partitions and zero-residual rows. Even mutually matching
candidate/provider content must pass the existing canonical preflight.

## Authority obligations and failure semantics

Trusted composition injects both providers; request payloads cannot select
them. `load_source(scope=..., capture=...)` must load independently retained,
authenticated complete evidence, never merely echo the submitted manifest.
The source provider owns authentic issuer identity, provenance and custody,
original documentary/tax and historical-discharge mappings, independent cash
confirmation, complete population and writer-fence/control evidence. The grant
provider verifies a real issuer's current grant for this exact operation,
command, scope and authority; it owns signature/trust-root verification,
revocation, expiry, replay/custody and applicable current authorization.
Proof references are opaque provider outputs, not self-attesting strings.
Matching fingerprints establish equality only; they do not establish truth.

Every call obtains a fresh grant and source result without caching, including
identical retries. `None` is authoritative denial. Missing providers raise
`OpeningVerificationUnavailable`; provider unavailability propagates that
exception. Programmer errors propagate rather than becoming denials. This
library does not claim to implement issuer authentication or its obligations.

## Consumption and remaining gates

`qualify_opening_account_import_v2` is a reusable library API consumed through
the existing preview verification seam in focused tests. A narrow test-only
adapter delegates import qualification and has a separately owned inverse
stub. The function does not implement the dual-method
`OpeningAccountImportVerifierV2`: real inverse grants require their own fresh
authority and must never reuse an import grant or be replaced by deny-all code.
A published production consumer remains pending; no production adoption or
zero-consumer-rule waiver is claimed. This source remains DRAFT until genuine
consumer wiring is accepted; focused test consumption does not resolve that gate.

Account/namespace mapping, authentic originals, independent cash, actual
issuer/trust root and custody remain external gates. Sub may supply raw
observations and trusted wiring only after the exact published package is
available. Complete rehearsals, financial blocker closure, opening seal,
legacy-writer retirement and exact source/release approvals remain separate.
Validation is hosted-CI-only; local pinned Ruff is static evidence only.
