# Billing opening trusted providers — DRAFT source extraction

Status: source-only design and hosted-CI canaries; no installed custody transport,
production enrollment, publication, or cutover authority. Finance accountability
and Michael exact-command approval are the approved design basis; no individual
Finance custodian or actual key is enrolled by this source.

Inventory before implementation: existing Billing opening_qualification.py owns
provider composition, opening_receivable_validation.py owns complete manifest
fingerprints, exact command digests, financial blockers and preview. Existing
opening_window_queries.get_opening_window owns persisted window lookup; assembly
must inject a reader using the caller's Session. Kernel semantic_encoding owns
cv1 framing. cryptography's Ed25519 public-key primitive owns signature checks.
No qualifying production Billing signing/custody implementation exists; deployment,
Numbering and licensing authority cannot be repurposed. The 2026-10-14 authority
proposal and canonical billing-opening-import-evidence-contract-20261013 and
dotmac-billing-cutover-orchestration-2026-10-14 records were inspected; no scope
denial occurred. This is a narrow continuation of existing Billing owners.

New closed, frozen V1 envelopes bind the whole independently retained V2 manifest
and immutable proof coordinates, or an exact V2 import command, retained source
attestation digest and window identity. Separate source/grant public keys, current
policy, current revocation and validity checks are mandatory. Every invocation
rereads trusted ports. Qualifier authenticates source before asking for the final
fresh grant. The grant provider reloads and reauthenticates its cross-bound source
after issuance/window reads, then checks BOTH source and grant against one final
shared policy/revocation/time snapshot without external reads between them. Preview consumes
no authorization. Exact authorized retries may
reach importer replay even after sealing; this provider does not own admission.

The custody port MUST independently authenticate immutable object versions and
original issue-time provenance. Returned bytes are checked against signed SHA256
bindings and coordinates. A signed label/checksum cannot establish original
provenance. Current regenerated documents and missing originals are denied.
Coverage includes all population identities, every receivable and cash lot,
including other partitions and zero residuals, source/fence/controls and all
original, tax/FX, discharge, confirmation, collection and consumption references.
Financial values are never recalculated here.

None means an authenticated denial. Missing configuration, custody availability
or revocation freshness raises OpeningVerificationUnavailable. Malformed config
and programming errors escape. No signer/private key, generic authorization
framework, new database, used-grant table or local test execution is introduced.
Port implementations and named-owner/enrollment/custody evidence remain separate
operational prerequisites; unit seams prove enforcement, never live custody.

Validation: tests authored before implementation for Git-hosted CI only. Local
validation is limited to pinned Ruff 0.6.9 formatting/lint and source inspection;
no runtime imports, dependencies, database or Docker use is authorized.
