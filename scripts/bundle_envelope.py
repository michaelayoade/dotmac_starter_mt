"""The dependency-bundle envelope's canonical serialization and hashing core.

## Why this lives in `scripts/`, not inside `dotmac_kernel` or a new package

Michael has ruled that ERP's generic dependency-bundle envelope (currently
`dotmac_erp/scripts/dependency_bundle.py`) moves here as a shared,
secret-free, SHA-pinned surface, while ERP keeps its dependency-POLICY (plan
digest construction, lock rules, off-index approval). This is the first and
smallest slice: the pure serialization/hashing primitives, with no policy, no
filesystem, no network. Later slices bring archive verification, extraction,
an offline index, and a constructor — deliberately not anticipated here.

Every package under `packages/` is wired into this repository's root
`pyproject.toml` as an editable path dependency and built/published by its
own `.github/workflows/` entry (see `dotmac-ui`, `dotmac-kernel`). "SHA-pinned"
means a consumer such as ERP fetches this exact file at a pinned Starter
commit — the same shape `scripts/host_attester.py`'s own docstring describes
for a capability that must reach a consumer without dragging in a whole
package's dependency and release surface. Following that precedent, this
module is a standalone, dependency-free file under `scripts/`, loaded by
tests the same way `test_host_attester_producer.py` loads
`host_attester.py`: by file location, registered in `sys.modules` before
execution. It is not wired into any package's `pyproject.toml`, and it stays
that way until a later slice's ADR says otherwise.

## Byte-for-byte compatibility is the whole point

`canonical_json_bytes` and `sha256_hex` are ported VERBATIM in behaviour from
`dotmac_erp` `scripts/dependency_bundle.py` at `origin/main`
(`b449c4d82fdb6c19d2c9e26eab8ef85ba50528ed`). Every plan digest and bundle
digest ERP computes must agree with what a Starter-side verifier computes
from the same input — a "better" separator, escaping, or newline choice here
would silently break every digest comparison across the two repositories.

Note for a future reader: `dotmac_kernel.capability_contract` already
defines a PRIVATE `_canonical_json_bytes` for capability-schema hashing. That
function uses `ensure_ascii=False` and no trailing newline — it is a
different canonicalization for a different contract, not an earlier version
of this one. Do not unify them; they encode different, independently
load-bearing byte layouts.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_bytes(document: Any) -> bytes:
    """Canonical UTF-8 JSON: sorted keys, compact separators, trailing
    newline. Comments, TOML whitespace, and key order never survive into
    this — they are gone by the time a dataclass exists."""

    return (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """The sha256 hex digest of `data`."""

    return hashlib.sha256(data).hexdigest()
