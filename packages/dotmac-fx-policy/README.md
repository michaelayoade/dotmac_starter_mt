# dotmac-fx-policy

Tenant-scoped ownership of effective FX-rate observations, their source
provenance, source-selection policy, and immutable determination evidence.

The module accepts observations from product-owned importers through typed
commands. It performs no provider network access and stores no provider
credentials. Kernel owns `Money`, `Currency`, and immutable exchange-rate value
types; Billing owns applied invoice snapshots; Accounting owns revaluation and
GL consequences; Tax owns tax decisions.

The package is audit-complete but uncomposed and unpublished. ERP is the first
candidate adopter through an explicit backfill, shadow comparison, one-writer
cutover, and retirement of its local `core_fx` decision paths.
