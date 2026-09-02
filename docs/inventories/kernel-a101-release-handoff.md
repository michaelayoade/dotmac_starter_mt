# Kernel 0.1.0a101 release handoff

Status: ready for the separately authorized a101 release transition. The
separated publisher/verifier facility and the a101 import-boundary repair are
both on protected `main`.

Kernel 0.1.0a100 is published and tagged but is not adoptable: a clean install
of its declared dependency set cannot import the public `create_app` symbol
when a product supplies database URLs but has not installed its PostgreSQL
driver. The package-root import itself is database-free. This is a
long-standing public import-boundary defect observed identically in a98, a99
and a100, not an a100 regression. The a101 repair keeps the driver in the
product deployment and makes the application-factory import database-free.

The a101 release is single-dispatch:

1. Confirm the publisher/verifier separation and durable-record facility are
   on protected `main`.
2. Confirm import-boundary repair `6f1a2a47` is an ancestor of protected
   `main`.
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
