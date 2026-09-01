# Kernel 0.1.0a101 release handoff

Status: blocked until the separated publisher/verifier facility and the a101
import-boundary repair have both reached protected `main`.

Kernel 0.1.0a100 is published and tagged but is not adoptable: a clean install
of its declared dependency set cannot import the package surface because the
root import reaches a product-owned PostgreSQL driver. The a101 repair keeps
that driver in the product deployment and makes the kernel's package-root and
application-factory imports database-free.

The a101 release is single-dispatch:

1. Merge the publisher/verifier separation and durable-record facility.
2. Rebase and merge the import-boundary repair.
3. Authorize a101 in a dedicated authorization-only commit, then allocate it
   in the immediate child commit.
4. Dispatch the publisher once from that exact protected-main source.
5. If the publisher fails before retaining artifact bytes, burn a101 and
   authorize a successor. Do not reuse the version.
6. Once artifact bytes exist, never rebuild or rerun the publisher for a101.
   Resume only from those retained bytes through a separately reviewed
   no-build publication path.
7. Independently verify the retained bytes against the registry, then create
   the annotated tag and durable evidence record. Adoption remains blocked
   until the evidence record reaches protected `main`.

No version coordinate in this handoff allocates, publishes or tags a101.
