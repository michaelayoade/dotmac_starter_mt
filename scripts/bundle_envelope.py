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

## Slice 1a-iii: safe extraction

`extract_verified_bundle` (the only sanctioned extraction entry point),
`_extract_zip_members`, `_is_within`, `ExtractionError`, and the extraction
size/count/ratio caps (`MAX_MEMBER_BYTES`, `MAX_MEMBER_COUNT`,
`MAX_TOTAL_UNCOMPRESSED_BYTES`, `MAX_COMPRESSION_RATIO`) are ported VERBATIM
in behaviour from the same ERP file at the same pinned commit. Per Michael's
ruling, `ExtractionError` joins beneath THIS module's `BundleEnvelopeError`
root — not beneath ERP's `DependencyBundleError`, and not re-exported; see
"Two roots, deliberately not one" above, which already named this as the
plan. This slice ports extraction exactly as ERP wrote it: a fresh, exclusive
staging directory, per-member and aggregate safety checks, then a single
atomic rename into `dest_dir`. It does not port the offline index or the
manifest constructor — deliberately not anticipated here either.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
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
    being folded into this one. `BundleVerificationError` and
    `ExtractionError` are both direct subclasses."""


class BundleVerificationError(BundleEnvelopeError):
    """A bundle, its run metadata, or its archive digest failed
    verification against an already-supplied expected value."""


class ExtractionError(BundleEnvelopeError):
    """A ZIP archive member is unsafe to extract, or extraction produced
    bytes that disagree with the verified manifest. Sits beneath THIS
    module's `BundleEnvelopeError` root, not ERP's `DependencyBundleError`
    — see the module docstring's "Two roots, deliberately not one"."""


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


# ── safe, private, atomic extraction ───────────────────────────────────

#: Per-member size cap during safe extraction — generous for a wheel/sdist
#: bundle, but bounded, so a crafted "small on disk, huge when read" member
#: cannot exhaust the extraction host. Checked against BOTH the ZIP's
#: declared size and the actual bytes read (the latter is the zip-bomb
#: guard: a lying declared size does not buy more).
MAX_MEMBER_BYTES = 200 * 1024 * 1024

#: Aggregate caps, independent of the per-member cap above: a bundle with
#: many small, individually-legal members can still exhaust the extraction
#: host on count or total size, and a highly-compressed member can pass the
#: per-member declared-size check while unpacking to something absurd
#: relative to what was actually transferred.
MAX_MEMBER_COUNT = 512
MAX_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.relative_to(base)
    except ValueError:
        return False
    return True


def _extract_zip_members(
    archive_path: Path, staging_dir: Path, expected_members: dict[str, int]
) -> list[str]:
    """Extract `archive_path` into `staging_dir`, refusing anything the
    verified bundle manifest did not name.

    PRIVATE: this writes into a caller-supplied staging directory and
    performs no publication step and no cleanup of its own — callers MUST
    go through `extract_verified_bundle`, which stages, verifies, and
    publishes atomically, and which cleans up a failed staging directory.
    Nothing outside this module should extract a ZIP without going through
    that verified path.

    `expected_members` is the exact `{member_name: declared_uncompressed_size}`
    mapping taken from the already-verified bundle manifest — never derived
    from the archive itself. Refuses: a duplicate member name, an absolute
    path, a `..` traversal segment, a symlink, a member whose RESOLVED
    target collides with another member's (e.g. `a.whl` and `./a.whl`), a
    member whose name is not in `expected_members`, an `expected_members`
    entry the archive does not contain, a size mismatch against the
    declared/verified size, a member over `MAX_MEMBER_BYTES`, more than
    `MAX_MEMBER_COUNT` members, more than `MAX_TOTAL_UNCOMPRESSED_BYTES` in
    aggregate, a member whose compression ratio exceeds
    `MAX_COMPRESSION_RATIO`, and — during extraction, independent of the
    declared size — any member whose actual bytes exceed the declared size
    (the zip-bomb guard: a lying declared size does not buy more).
    """

    staging_dir = staging_dir.resolve()
    try:
        staging_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExtractionError(
            f"cannot create staging directory {staging_dir}: {exc}"
        ) from exc
    extracted: list[str] = []
    try:
        archive = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ExtractionError(
            f"cannot open {archive_path} as a ZIP archive: {exc}"
        ) from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBER_COUNT:
            raise ExtractionError(
                f"archive contains {len(infos)} members, exceeding the "
                f"{MAX_MEMBER_COUNT}-member cap"
            )
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ExtractionError("archive contains a duplicate member name")

        seen_lower: dict[str, str] = {}
        seen_resolved: dict[Path, str] = {}
        total_declared_size = 0
        for info in infos:
            name = info.filename
            if name not in expected_members:
                raise ExtractionError(
                    f"archive member {name!r} is not named in the verified "
                    "bundle manifest"
                )
            lowered = name.lower()
            if lowered in seen_lower and seen_lower[lowered] != name:
                raise ExtractionError(
                    f"archive members {seen_lower[lowered]!r} and {name!r} "
                    "collide case-insensitively"
                )
            seen_lower[lowered] = name

            if (
                name.startswith("/")
                or name.startswith("\\")
                or Path(name).is_absolute()
            ):
                raise ExtractionError(f"archive member {name!r} uses an absolute path")
            if ".." in Path(name).parts:
                raise ExtractionError(
                    f"archive member {name!r} contains a path-traversal segment"
                )
            resolved_target = (staging_dir / name).resolve()
            if not _is_within(staging_dir, resolved_target):
                raise ExtractionError(
                    f"archive member {name!r} resolves outside the "
                    "destination directory"
                )
            if resolved_target in seen_resolved:
                raise ExtractionError(
                    f"archive members {seen_resolved[resolved_target]!r} and "
                    f"{name!r} resolve to the SAME target path "
                    f"({resolved_target}); a platform-separator or "
                    "relative-segment alias is refused"
                )
            seen_resolved[resolved_target] = name

            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ExtractionError(f"archive member {name!r} is a symlink")

            declared_size = info.file_size
            if declared_size != expected_members[name]:
                raise ExtractionError(
                    f"archive member {name!r} declares size {declared_size}, "
                    f"the verified manifest expects {expected_members[name]}"
                )
            if declared_size > MAX_MEMBER_BYTES:
                raise ExtractionError(
                    f"archive member {name!r} declares {declared_size} bytes, "
                    f"exceeding the {MAX_MEMBER_BYTES}-byte per-member cap"
                )
            if info.compress_size > 0:
                ratio = declared_size / info.compress_size
                if ratio > MAX_COMPRESSION_RATIO:
                    raise ExtractionError(
                        f"archive member {name!r} has a compression ratio of "
                        f"{ratio:.1f}, exceeding the {MAX_COMPRESSION_RATIO}x "
                        "cap; refusing as a suspected zip bomb"
                    )
            total_declared_size += declared_size
            if total_declared_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise ExtractionError(
                    "archive's aggregate declared uncompressed size exceeds "
                    f"the {MAX_TOTAL_UNCOMPRESSED_BYTES}-byte cap"
                )

        for expected_name in expected_members:
            if expected_name not in names:
                raise ExtractionError(
                    f"the verified bundle manifest expects member "
                    f"{expected_name!r}, which the archive does not contain"
                )

        running_total = 0
        for info in infos:
            target = staging_dir / info.filename
            written = 0
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(target, "wb") as sink:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        running_total += len(chunk)
                        if written > MAX_MEMBER_BYTES:
                            raise ExtractionError(
                                f"archive member {info.filename!r} exceeded the "
                                f"{MAX_MEMBER_BYTES}-byte cap while extracting "
                                "(declared size cannot be trusted; this is the "
                                "zip-bomb guard)"
                            )
                        if running_total > MAX_TOTAL_UNCOMPRESSED_BYTES:
                            raise ExtractionError(
                                "aggregate extracted bytes exceeded "
                                f"{MAX_TOTAL_UNCOMPRESSED_BYTES}; refusing (zip-"
                                "bomb guard)"
                            )
                        sink.write(chunk)
            except (zipfile.BadZipFile, OSError) as exc:
                raise ExtractionError(
                    f"cannot extract archive member {info.filename!r}: {exc}"
                ) from exc
            if written != info.file_size:
                raise ExtractionError(
                    f"archive member {info.filename!r} extracted "
                    f"{written} bytes but declared {info.file_size}"
                )
            extracted.append(info.filename)
    return extracted


def extract_verified_bundle(
    archive_path: Path, dest_dir: Path, bundle_manifest: dict[str, Any]
) -> list[str]:
    """The ONLY sanctioned way to extract a bundle archive.

    Extracts into a FRESH, EXCLUSIVE staging directory (never `dest_dir`
    directly), verifies every member's size (via `_extract_zip_members`)
    and content hash (via `verify_member_hashes`) THERE, and only then
    publishes the complete, verified tree to `dest_dir` with a single atomic
    rename. `dest_dir` must not already exist — this function materialises a
    fresh tree, it does not merge into or overwrite one. On ANY failure —
    an unsafe member, a hash mismatch, or an unexpected error — the staging
    directory is removed and NOTHING is written to `dest_dir`; a caller
    never observes a partially-extracted destination.

    STATED, NARROW RACE (not claimed to be closed): existence is checked
    both here and again immediately before the final rename, but there is
    no portable, dependency-free "rename unless the destination exists"
    primitive for a directory target in the Python standard library
    (POSIX `renameat2(..., RENAME_NOREPLACE)` is Linux-only and is not
    exposed by `os`). A concurrent process that creates an EMPTY `dest_dir`
    in the narrow window between the second check and `os.rename` would
    have it silently replaced, because POSIX `rename(2)` replacing an
    empty directory target is not an OS-level error. This function is
    atomic against sequential failure (a caller never sees a partial
    result); it is not a mutual-exclusion primitive against a concurrent,
    uncooperating writer targeting the SAME `dest_dir` — that is expected
    not to happen (each destination is expected to be named for its own
    bundle identity), and external locking is the caller's responsibility
    if it might.
    """

    if dest_dir.exists():
        raise ExtractionError(
            f"destination {dest_dir} already exists; extract_verified_bundle "
            "materialises a fresh tree and refuses to merge into or "
            "overwrite one"
        )
    _refuse_malformed_bundle_manifest_shape(bundle_manifest)
    members = bundle_manifest.get("members")
    if not isinstance(members, dict) or not members:
        raise BundleVerificationError("bundle manifest carries no members to extract")
    expected_sizes: dict[str, int] = {}
    expected_hashes: dict[str, str] = {}
    for name, record in members.items():
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("size"), int)
            or isinstance(record.get("size"), bool)
            or record["size"] <= 0
            or not isinstance(record.get("sha256"), str)
            or not _SHA256_HEX.match(record["sha256"])
            or not isinstance(record.get("package"), str)
            or not record.get("package")
        ):
            raise BundleVerificationError(
                f"bundle manifest member {name!r} is malformed: {record!r}"
            )
        expected_sizes[name] = record["size"]
        expected_hashes[name] = record["sha256"]

    try:
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExtractionError(
            f"cannot create parent directory for {dest_dir}: {exc}"
        ) from exc
    try:
        staging_dir = Path(
            tempfile.mkdtemp(
                prefix=f".{dest_dir.name}.staging.", dir=str(dest_dir.parent)
            )
        )
    except OSError as exc:
        raise ExtractionError(
            f"cannot create a staging directory beside {dest_dir}: {exc}"
        ) from exc
    try:
        extracted = _extract_zip_members(archive_path, staging_dir, expected_sizes)
        verify_member_hashes(staging_dir, expected_hashes)
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    if dest_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise ExtractionError(
            f"destination {dest_dir} was created concurrently while staging; "
            "refusing to publish over it"
        )
    try:
        os.rename(staging_dir, dest_dir)
    except OSError as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise ExtractionError(
            f"cannot publish extracted bundle to {dest_dir}: {exc}"
        ) from exc
    return extracted
