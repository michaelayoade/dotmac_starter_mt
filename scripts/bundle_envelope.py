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

## Slice 1a-ii: archive and member hash verification

`verify_archive_digest`, `verify_member_hashes`,
`_refuse_malformed_manifest_run`, and `_refuse_malformed_bundle_manifest_shape`
are ported VERBATIM in behaviour from the same ERP file at the same pinned
commit. They verify bytes already on disk against an already-supplied
expected digest/hash/shape — no filesystem traversal beyond a single
`read_bytes()`, no ZIP handling, no extraction, no offline index, no
constructor. Those stay out of scope for later slices, deliberately not
anticipated here. `BundleVerificationError` is the one exception these
functions raise.

## Two roots, deliberately not one

Michael has ruled: this module owns a generic `BundleEnvelopeError` root,
and `BundleVerificationError` is one of its subclasses. ERP separately
keeps its own `DependencyBundleError` root for its product-POLICY refusals
(plan digest construction, lock rules, off-index approval — none of that
is ported here). These stay two distinct roots. A consumer adopting this
module — ERP included, at cutover — catches BOTH roots in its existing
refusal handler; this module's errors are NOT re-exported or aliased under
ERP's `DependencyBundleError`, and ERP's errors are not folded into this
one. Re-exporting under the other side's class was considered and is
specifically NOT the chosen design: it would make one exception name mean
two different, independently-evolving contracts depending on which module
imported it first.

`ExtractionError` (ZIP extraction, still unported) will join beneath
`BundleEnvelopeError` in a later slice. It is not ported yet, so it is not
declared here.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
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


class BundleEnvelopeError(Exception):
    """The generic root for every error this module raises. Starter-owned,
    product-policy-free, inheriting `Exception` directly — no ERP class sits
    above or below it. ERP keeps its own `DependencyBundleError` root for
    its product-policy refusals; a consumer catches both roots rather than
    this module re-exporting its errors under ERP's class, or ERP's errors
    being folded into this one. `BundleVerificationError` is the first
    subclass; `ExtractionError` joins beneath this root in a later slice,
    once extraction is actually ported."""


class BundleVerificationError(BundleEnvelopeError):
    """A bundle, its run metadata, or its archive digest failed
    verification against an already-supplied expected value."""


#: The bundle manifest's own schema version (see ERP's `create_bundle_manifest`,
#: not ported here — this slice only checks that a manifest DECLARES the
#: version this module recognises).
MANIFEST_SCHEMA_VERSION = 2

_SHA256_HEX = re.compile(r"\A[0-9a-f]{64}\Z")

_COMMIT_SHA = re.compile(r"\A[0-9a-f]{40}\Z")

_NULL_SHA = "0" * 40

_MANIFEST_RUN_POSITIVE_INT_FIELDS = (
    "repository_id",
    "run_id",
    "run_attempt",
    "artifact_id",
    "artifact_run_id",
)
_MANIFEST_RUN_NONEMPTY_STRING_FIELDS = (
    "repository_full_name",
    "workflow_path",
    "artifact_name",
    "environment_name",
)


def verify_archive_digest(archive_path: Path, expected_sha256: str) -> str:
    """Verify the OUTER archive's own bytes, before it is ever opened as a
    ZIP. `expected_sha256` must already be a verified value (e.g. bound to
    the artifact's GitHub-reported digest) — this function does not fetch
    or trust anything else."""

    if not isinstance(expected_sha256, str) or not _SHA256_HEX.match(expected_sha256):
        raise BundleVerificationError(
            "expected archive digest must be a 64-hex sha256 string"
        )
    try:
        data = archive_path.read_bytes()
    except OSError as exc:
        raise BundleVerificationError(
            f"cannot read archive {archive_path}: {exc}"
        ) from exc
    digest = sha256_hex(data)
    if digest != expected_sha256:
        raise BundleVerificationError(
            f"archive digest mismatch: expected {expected_sha256}, got {digest}"
        )
    return digest


def verify_member_hashes(dest_dir: Path, expected_hashes: dict[str, str]) -> None:
    """Verify every extracted file's content hash against the verified
    bundle manifest's per-member hash. Independent of extraction's size
    checks — this is the content proof, not the shape proof."""

    for name, expected_hex in expected_hashes.items():
        if not isinstance(expected_hex, str) or not _SHA256_HEX.match(expected_hex):
            raise BundleVerificationError(
                f"expected hash for {name!r} is not a 64-hex sha256 string"
            )
        path = dest_dir / name
        if not path.is_file():
            raise BundleVerificationError(
                f"expected extracted member {name!r} is missing on disk"
            )
        try:
            digest = sha256_hex(path.read_bytes())
        except OSError as exc:
            raise BundleVerificationError(
                f"cannot read extracted member {name!r}: {exc}"
            ) from exc
        if digest != expected_hex:
            raise BundleVerificationError(
                f"extracted member {name!r} hash mismatch: expected "
                f"{expected_hex}, got {digest}"
            )


def _refuse_malformed_manifest_run(run: Any) -> None:
    """Shape-validates a bundle manifest's OWN `run` dict — untrusted data
    read straight off disk/network, never an already-provenanced
    `RunMetadata`. This function never constructs a `RunMetadata` and
    never implies verification happened; it only refuses a `run` record
    too malformed to even be a candidate for later cross-checking (see
    ERP's `bind_bundle_to_candidate`, which separately cross-checks a
    VERIFIED `RunMetadata`'s `run_id`/`artifact_id` against this same
    dict — not ported here)."""

    if not isinstance(run, dict):
        raise BundleVerificationError("bundle manifest run must be a dict")
    for field_name in _MANIFEST_RUN_POSITIVE_INT_FIELDS:
        value = run.get(field_name)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise BundleVerificationError(
                f"bundle manifest run.{field_name} must be a positive "
                f"integer, got {value!r}"
            )
    for field_name in _MANIFEST_RUN_NONEMPTY_STRING_FIELDS:
        value = run.get(field_name)
        if not isinstance(value, str) or not value:
            raise BundleVerificationError(
                f"bundle manifest run.{field_name} must be a non-empty string"
            )
    trusted_sha = run.get("trusted_workflow_sha")
    if (
        not isinstance(trusted_sha, str)
        or trusted_sha == _NULL_SHA
        or not _COMMIT_SHA.match(trusted_sha)
    ):
        raise BundleVerificationError(
            "bundle manifest run.trusted_workflow_sha must be a 40-hex "
            "commit SHA, and not the all-zero null SHA"
        )


def _refuse_malformed_bundle_manifest_shape(bundle_manifest: dict[str, Any]) -> None:
    """The manifest-wide shape check ERP's `extract_verified_bundle` used to
    skip entirely: it read ONLY `members`, silently ignoring
    `schema_version`, `plan_digest`, `archive_sha256`, `run`, and each
    member's own `package` field — so a manifest malformed in any of
    those ways would still extract successfully, and the trust
    document's "fully shape-checked" claim was false for this function.
    Every field this function checks is refused BEFORE any extraction
    work begins, not discovered later by whichever downstream caller
    happens to read it (ERP's `bind_bundle_to_candidate` reads `run` too,
    but only after extraction has already published a tree — not ported
    here)."""

    schema_version = bundle_manifest.get("schema_version")
    if schema_version != MANIFEST_SCHEMA_VERSION:
        raise BundleVerificationError(
            f"bundle manifest schema_version must be {MANIFEST_SCHEMA_VERSION}, "
            f"got {schema_version!r}"
        )
    plan_digest = bundle_manifest.get("plan_digest")
    if not isinstance(plan_digest, str) or not _SHA256_HEX.match(plan_digest):
        raise BundleVerificationError(
            "bundle manifest plan_digest must be a 64-hex sha256 string"
        )
    archive_sha256 = bundle_manifest.get("archive_sha256")
    if not isinstance(archive_sha256, str) or not _SHA256_HEX.match(archive_sha256):
        raise BundleVerificationError(
            "bundle manifest archive_sha256 must be a 64-hex sha256 string"
        )
    _refuse_malformed_manifest_run(bundle_manifest.get("run"))
