# dotmac-documents

Tenant-controlled document identity, immutable content versions, exact-version
renditions, document-scoped access/collaboration, acknowledgements and a
Documents-owned lifecycle.

Bytes stay in `dotmac-files`; approval verdicts stay in `dotmac-approvals`;
scheduled wake-ups stay in `dotmac-durable-timers`; record retention, holds and
disposition stay in `dotmac-records`. This package imports none of them. A
product assembly passes opaque ids, checksums and typed observations across the
boundaries.
