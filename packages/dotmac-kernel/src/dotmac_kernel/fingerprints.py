"""Canonical payload fingerprints, with no persistence attached.

`fingerprint_of` answers one question — "is this the same request?" — and needs
nothing but the payload to answer it. It lived in `dotmac_kernel.idempotency`
because that is where the first caller was, not because it belongs there:
importing that module pulls in the ORM models and the database-backed execution
ledger, so a caller that only wants a canonical digest pays for an owner it will
never use.

That is a real constraint rather than a tidiness argument. A STATELESS module —
one whose whole contract is that it has no models, no lineage and no session —
cannot import the ledger without falsifying that claim, and
`tests/architecture/test_packages_import_without_a_database.py` is where the
claim is proven. `dotmac-document-rendering` is the first such caller: it
fingerprints an immutable fact to give a rendered document provenance, and it
must be importable with no database in reach at all.

So the function moves here and `dotmac_kernel.idempotency` re-exports it. Every
existing caller keeps working unchanged; a caller that needs only identity can
now say so in its import.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def fingerprint_of(payload: Any) -> str:
    """A stable SHA256 over `payload`, for detecting a key reused with a
    different request.

    Stability is the whole point, so the encoding is pinned: sorted keys and
    compact separators, meaning dict ordering and incidental whitespace cannot
    change the digest. Objects exposing `model_dump` (pydantic models, without
    importing pydantic here) are dumped in JSON mode first; anything else
    non-serializable falls back to `str`, so a UUID or Decimal fingerprints
    consistently instead of raising.
    """
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["fingerprint_of"]
