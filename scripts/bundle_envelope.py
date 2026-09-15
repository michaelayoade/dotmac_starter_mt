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

## Slice 1a-iv: the local PEP 503 index — NOT a verbatim port

`build_local_index` is deliberately NOT ported unchanged from ERP. ERP's
version takes a caller-ASSEMBLED `packages: dict[str, list[tuple[str, str,
Path]]]` mapping and, by its own docstring's admission, "does not itself
trust the hash; it only copies bytes and records the hash fragment PEP 503
uses for its own integrity check." That was safe only because nothing in
ERP called it in production. Moving this into a shared consumer surface
creates that first caller, so the deferral expires here.

This slice's `build_local_index` instead takes a typed `ExpectedArtifactSet`
(the TRUSTED plan — package/filename/sha256 triples a PRODUCT derives from
an immutable candidate snapshot; this module never computes that plan
itself), the already-verified bundle manifest, and the directory the
archive was extracted into. Before publishing anything it reconciles PLAN
against MANIFEST (both directions), MANIFEST against the files actually on
disk (both directions), refuses a duplicate or empty plan, and — the part
the old signature could not do at all — RE-HASHES every file it is about to
copy against the plan's own expected digest, rather than trusting the
manifest's recorded hash or any earlier verification pass. Only after all
of that does it build the PEP 503 package map itself; a caller no longer
assembles one.

**A typed value is not provenance.** `ExpectedArtifactSet` being a
dataclass with named fields does not make its contents trustworthy — the
type system cannot stop a caller constructing one from candidate-supplied
data and handing it to this function as though it were the product's own
plan. What makes an `ExpectedArtifactSet` trustworthy is entirely how the
calling PRODUCT code obtained it (from an immutable candidate snapshot,
never from anything a candidate submitted directly); this module receives
the set and reconciles against it, and never derives one itself.

Errors here raise `BundleVerificationError`, beneath this module's
`BundleEnvelopeError` root — never ERP's `ManifestError` or
`DependencyBundleError`, which are product-policy classes and are not
ported (see "Two roots, deliberately not one" above).
`_normalise_pep503_name` ports the validating behaviour from ERP's
`scripts/dependency_normalisation.py` `normalise_name` — the validating
form (charset-checked input, edge-separator refused in the normalised
output), with Starter's additional `isinstance(name, str)` refusal — not
the total, never-raising `normalise_name_for_identity` form; that module
is not otherwise ported here, since nothing else in this slice needs
repository-URL normalisation or identity-only comparison.

## Slice 1a-v: the canonical envelope constructor

`create_bundle_manifest` is the producer-side counterpart to
`extract_verified_bundle`/`build_local_index`, and — per Michael's ruling —
it is deliberately NOT a verbatim port of ERP's `create_bundle_manifest`
(`scripts/dependency_bundle.py` at the same pinned commit). ERP's version
takes a `DependencySurface` and computes the plan digest itself from
`pyproject.toml`/`poetry.lock`; that half is ERP PRODUCT POLICY and does
not move. This module's version instead takes an already-established
`ExpectedArtifactSet` — the same typed plan `build_local_index` already
reconciles against, reused rather than re-invented — plus the files a
caller actually acquired, the archive those files were packed into, and
already-shaped run metadata. It never computes a plan; it only PROVES the
acquired files and the archive agree with the plan it was handed, and
computes every recorded binding (member size, member hash, archive digest)
from real bytes on disk.

Closure is three-way and BOTH directions on each edge: every artifact the
plan expects must have been acquired, and every acquired file must be
named by the plan (plan <-> acquired); every acquired file's bytes must
actually be present in the archive under its exact planned name with a
matching size and content digest, and the archive must contain nothing
the plan does not expect (acquired <-> archive contents). This is a
deliberate strengthening over ERP's original, which only checked the
acquired-into-archive direction — the archive-has-an-extra-member
direction is new here because a manifest that claims closure narrower
than what the archive actually contains is exactly the gap "the archive
contains exactly those members" (this slice's own requirement) rules out.

Errors here raise `BundleVerificationError`, beneath this module's
`BundleEnvelopeError` root, same as every other function in this file.

### The input-domain gate: `json.dumps` permits NaN/Infinity by default

`canonical_json_bytes` is ported VERBATIM (see "Byte-for-byte
compatibility is the whole point" above) and stays that way — changing
`json.dumps`'s `allow_nan` behaviour would change valid digest bytes two
repositories must agree on, which is exactly the kind of change that
docstring forbids. Python's default `json.dumps` happily serializes
`float("nan")`/`float("inf")` as the bare tokens `NaN`/`Infinity`, which
are not strict JSON — so the gate has to sit somewhere else.

Chosen gate: refuse a non-finite (or any non-string) value at the
CONSTRUCTOR'S boundary, before it is ever placed into the manifest this
function builds, rather than a generic recursive "walk the emitted
document and call `math.isfinite`" scan after the fact. This works because
every leaf value `create_bundle_manifest` writes into the manifest is
either computed by this function itself from real bytes (`sha256_hex`
output, `len(data)` — both always str/int, never a float) or copied from a
caller-supplied value: `ExpectedArtifactSet.plan_digest` and
`ExpectedArtifact.package_normalised_name` are the only two such values
(`ExpectedArtifact.filename` and `.sha256` are read but never copied
verbatim into a value position — `filename` becomes a dict KEY, and a
mismatched `.sha256` is refused before anything is written). Both of those
two values are required to be non-empty strings before this function uses
them for anything, which makes a float — finite or not — structurally
unreachable in the emitted manifest through this path. `run`'s numeric
fields are already closed the same way by the existing, UNMODIFIED
`_refuse_malformed_manifest_run`, whose `isinstance(value, int)` checks
already exclude every float, non-finite or not — reused here, not
re-implemented.

## `build_local_index`'s disk enumeration asked what a symlink resolves to

`build_local_index`'s reconciliation step 2 used to enumerate
`extracted_dir` with `Path.is_file()`, which FOLLOWS a symlink — it answers
"what does this resolve to", not "what is this entry". A live symlink
replacing an expected member, pointing OUTSIDE `extracted_dir` at bytes
that happen to match the plan's expected hash, enumerated identically to
the real file it replaced: plan<->manifest closure, manifest<->disk
closure, and even the step-4 rehash all passed unchanged (the rehash reads
through the link too), and this function went on to copy bytes read from
outside the verified tree. A DANGLING symlink failed the opposite way:
`is_file()` returns `False` for it, so it was silently OMITTED from the
enumerated set rather than refused — invisible to the "extra file" side of
the same closure check, not merely unverified.

The fix enumerates with `Path.lstat()` (never follows a link) and
`stat.S_ISREG`/`stat.S_ISDIR` on the result: a directory is skipped as
structure, and anything that is neither a directory nor a regular file — a
symlink, dangling or not — is refused BY NAME. This is the same class of
defect already fixed elsewhere in this fleet: a disk-state predicate asked
what a path resolves to, when the predicate needed to resolve through
nothing at all.

A second, independent fix in the same function: the plan filename's shape
(no path separator, not absolute, not `.`/`..`) used to be validated only
in step 5, immediately before it was joined onto a staging directory — by
which point step 2's enumeration and step 4's rehash had already read the
file at that (possibly nested) path. The check now runs first, while
`plan_by_filename` is built, so a rejected name is refused before this
function ever reads a byte named by it.

## `create_bundle_manifest` accepted an archive its own extractor would refuse

An accepted manifest must not describe an archive `extract_verified_bundle`
rejects — the constructor is a producer whose output is a trust boundary,
and its closure claim was false wherever it stayed silent on a shape
`_extract_zip_members` enforces. Two confirmed gaps, closed here:

1. The archive-closure check computed `set(archive.namelist())` directly.
   `zipfile` keeps only the LAST entry for a repeated member name, and
   `set()` silently collapses the duplicate before this function ever sees
   it — so an archive with two `pkg_a-1.0.whl` entries produced an ACCEPTED
   manifest, while `_extract_zip_members`'s own `len(names) !=
   len(set(names))` check refuses the same archive outright. Fixed by
   checking the RAW `namelist()` for a duplicate first, mirroring
   `_extract_zip_members`'s own check, before any set-collapsing occurs.
2. `expected_by_filename` accepted any non-empty string as
   `ExpectedArtifact.filename`, including one containing `/`, `\\`, `..`,
   or an absolute path, and wrote it straight into `members[filename]` —
   `build_local_index` refuses exactly those shapes. Fixed by validating
   the filename's shape BEFORE any acquired bytes are read, mirroring the
   check `build_local_index` already applies to the identical plan
   filename, and applying the same before-any-read ordering that
   function's own nested-filename fix established.

The same review asked whether every OTHER extraction-time admissibility
rule was similarly open. Two more were: two plan filenames differing only
by case (`Foo.whl` / `foo.whl`) built two distinct, non-duplicate manifest
members that `_extract_zip_members`'s `seen_lower` check refuses at
extraction; and a plan naming more artifacts than `MAX_MEMBER_COUNT`, an
acquired file over `MAX_MEMBER_BYTES`, or an acquired total over
`MAX_TOTAL_UNCOMPRESSED_BYTES` produced a manifest describing an archive
`_extract_zip_members`'s own caps refuse. This function now refuses all
three at construction, using the exact same thresholds and comparisons.
Two further caps are ALSO enforced against the archive itself, using the
same `ZipInfo` this function already reads to verify size and content: a
member whose `external_attr` declares it a symlink (refused regardless of
what its declared content bytes are, the same as extraction), and a member
whose compression ratio exceeds `MAX_COMPRESSION_RATIO` (the same
zip-bomb guard extraction applies, using the same `file_size /
compress_size` comparison).

Not enforced here, deliberately: an archive member using an absolute path,
containing a `..` segment, or aliasing another member's resolved target.
All three are moot at this function's boundary rather than merely
unchecked — the archive<->members closure above already requires
`archive_names == set(members)` (a bijection on exact member-name
strings), and every name in `members` is now a validated bare filename (no
separators, not absolute, not `.`/`..`); an archive member using any of
those shapes can never equal a bare filename, so it necessarily falls into
the existing "archive contains an extra member the plan does not name"
refusal. Adding a second, redundant check for the same three shapes would
not close any additional gap.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import stat
import tempfile
import urllib.parse
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
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
    # Bind the outer bytes before opening the ZIP or creating a staging tree.
    # Member verification alone cannot detect a substituted archive whose
    # contents happen to satisfy the supplied member records.
    verify_archive_digest(archive_path, bundle_manifest["archive_sha256"])
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


# ── local PEP 503 materialisation ──────────────────────────────────────


@dataclass(frozen=True)
class ExpectedArtifact:
    """One file the TRUSTED plan says must be published — the reference
    shape is ERP's `PlannedArtifact`. See the module docstring's "A typed
    value is not provenance": being a dataclass does not make an instance
    trustworthy. Trust comes from how the calling PRODUCT derived it (an
    immutable candidate snapshot), never from this type existing."""

    package_normalised_name: str
    filename: str
    sha256: str


@dataclass(frozen=True)
class ExpectedArtifactSet:
    """The plan `build_local_index` reconciles every published artifact
    against. `plan_digest` is compared against the verified manifest's OWN
    recorded `plan_digest`, so a manifest produced for a different plan can
    never be published under this one's index. Same warning as
    `ExpectedArtifact`: well-typed is not trusted — this module never
    derives an `ExpectedArtifactSet` itself, only receives and reconciles
    against one a trusted caller already built."""

    plan_digest: str
    artifacts: tuple[ExpectedArtifact, ...]


# ── canonical bundle manifest ──────────────────────────────────────────


def create_bundle_manifest(
    *,
    expected: ExpectedArtifactSet,
    acquired_files: Mapping[str, Path],
    archive_path: Path,
    run: Mapping[str, Any],
    schema_version: int = MANIFEST_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Build the canonical bundle manifest by COMPUTING every binding from
    `expected` and the ACTUAL FILES/archive on disk — never accepting a
    member size, a member hash, or the archive digest as an
    independently-reported scalar. See the module docstring's "Slice 1a-v"
    for why this takes an already-established `ExpectedArtifactSet` rather
    than computing a plan itself (that half is product policy and does not
    move here), and its "input-domain gate" subsection for why every value
    copied verbatim from `expected` is required to be a non-empty string
    before it is used for anything.

    `acquired_files` maps each expected filename to the real path a caller
    downloaded it to; this function reads and hashes every one of those
    files itself. `archive_path` is likewise the real outer archive file:
    `archive_sha256` is computed from its own bytes here, and the archive
    is opened as a ZIP to prove — member by member — that it actually
    contains every acquired file under its exact expected name, with the
    exact size and content digest just computed from the real file on
    disk, and NOTHING ELSE: an archive member the plan does not expect is
    refused exactly as an acquired file the plan does not expect is, so a
    manifest can never claim closure either broader or narrower than what
    is actually on disk and in the archive.
    """

    if schema_version != MANIFEST_SCHEMA_VERSION:
        raise BundleVerificationError(
            f"schema_version must be {MANIFEST_SCHEMA_VERSION}, got {schema_version!r}"
        )
    if not isinstance(expected.plan_digest, str) or not _SHA256_HEX.match(
        expected.plan_digest
    ):
        raise BundleVerificationError(
            "expected artifact set plan_digest must be a 64-hex sha256 "
            f"string, got {expected.plan_digest!r}"
        )
    if not expected.artifacts:
        raise BundleVerificationError(
            "expected artifact set names no artifacts; refusing to "
            "construct a manifest for an empty plan"
        )

    expected_by_filename: dict[str, ExpectedArtifact] = {}
    seen_lower_filenames: dict[str, str] = {}
    for artifact in expected.artifacts:
        if not isinstance(artifact.filename, str) or not artifact.filename:
            raise BundleVerificationError(
                "expected artifact filename must be a non-empty string, "
                f"got {artifact.filename!r}"
            )
        # Validate the filename's SHAPE here, before it is ever used to read
        # an acquired file's bytes (below) or open an archive member by that
        # name (further below) — a rejected name must never reach a read.
        # Mirrors the bare-filename check `build_local_index` applies to the
        # identical plan filename, applied here first for the identical
        # reason that function's own nested-filename fix established.
        if (
            "/" in artifact.filename
            or "\\" in artifact.filename
            or artifact.filename in (".", "..")
            or Path(artifact.filename).is_absolute()
        ):
            raise BundleVerificationError(
                f"expected artifact filename {artifact.filename!r} is not a "
                "safe bare filename (no path separators, not absolute, not "
                "'.' or '..')"
            )
        if not isinstance(artifact.package_normalised_name, str) or not (
            artifact.package_normalised_name
        ):
            raise BundleVerificationError(
                f"expected artifact {artifact.filename!r} "
                "package_normalised_name must be a non-empty string, got "
                f"{artifact.package_normalised_name!r}"
            )
        try:
            canonical_package_name = _normalise_pep503_name(
                artifact.package_normalised_name
            )
        except ValueError as exc:
            raise BundleVerificationError(
                f"expected artifact {artifact.filename!r} package_normalised_name "
                f"is not a valid PEP 503 name: {exc}"
            ) from exc
        if canonical_package_name != artifact.package_normalised_name:
            raise BundleVerificationError(
                f"expected artifact {artifact.filename!r} package_normalised_name "
                f"is not PEP-503-normalised: "
                f"{artifact.package_normalised_name!r}"
            )
        if artifact.filename in expected_by_filename:
            raise BundleVerificationError(
                f"expected artifact set names {artifact.filename!r} twice; "
                "duplicate entries are refused"
            )
        lowered_filename = artifact.filename.lower()
        if lowered_filename in seen_lower_filenames:
            raise BundleVerificationError(
                f"expected artifacts {seen_lower_filenames[lowered_filename]!r} "
                f"and {artifact.filename!r} collide case-insensitively; the "
                "archive extractor refuses this pairing even though the "
                "names differ"
            )
        seen_lower_filenames[lowered_filename] = artifact.filename
        expected_by_filename[artifact.filename] = artifact

    if len(expected_by_filename) > MAX_MEMBER_COUNT:
        raise BundleVerificationError(
            f"the plan names {len(expected_by_filename)} artifacts, "
            f"exceeding the {MAX_MEMBER_COUNT}-member cap the archive "
            "extractor enforces"
        )

    expected_names = set(expected_by_filename)
    acquired_names = set(acquired_files)
    missing = expected_names - acquired_names
    if missing:
        raise BundleVerificationError(
            f"the plan expects {sorted(missing)}, which were not acquired"
        )
    extra = acquired_names - expected_names
    if extra:
        raise BundleVerificationError(
            f"{sorted(extra)} were acquired but the plan does not expect "
            "them; every acquired file must be accounted for by the plan"
        )

    members: dict[str, dict[str, Any]] = {}
    total_acquired_size = 0
    for filename, artifact in sorted(expected_by_filename.items()):
        path = acquired_files[filename]
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise BundleVerificationError(
                f"cannot read acquired file {filename!r} at {path}: {exc}"
            ) from exc
        actual_sha256 = sha256_hex(data)
        actual_size = len(data)
        if actual_sha256 != artifact.sha256:
            raise BundleVerificationError(
                f"{filename!r} was acquired with digest {actual_sha256}, "
                f"but the plan expects {artifact.sha256}"
            )
        if actual_size <= 0:
            raise BundleVerificationError(f"{filename!r} is empty on disk")
        if actual_size > MAX_MEMBER_BYTES:
            raise BundleVerificationError(
                f"{filename!r} is {actual_size} bytes, exceeding the "
                f"{MAX_MEMBER_BYTES}-byte per-member cap the archive "
                "extractor enforces"
            )
        total_acquired_size += actual_size
        if total_acquired_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise BundleVerificationError(
                "the acquired files' aggregate size exceeds the "
                f"{MAX_TOTAL_UNCOMPRESSED_BYTES}-byte cap the archive "
                "extractor enforces"
            )
        members[filename] = {
            "sha256": actual_sha256,
            "size": actual_size,
            "package": artifact.package_normalised_name,
        }

    try:
        archive_bytes = archive_path.read_bytes()
    except OSError as exc:
        raise BundleVerificationError(
            f"cannot read archive {archive_path}: {exc}"
        ) from exc
    archive_sha256 = sha256_hex(archive_bytes)

    try:
        with zipfile.ZipFile(archive_path) as archive:
            archive_namelist = archive.namelist()
            if len(archive_namelist) != len(set(archive_namelist)):
                raise BundleVerificationError(
                    f"archive {archive_path} contains a duplicate member "
                    "name; zipfile keeps only the last entry for a "
                    "repeated name, so set(archive.namelist()) would "
                    "silently collapse it and this manifest would claim "
                    "closure over a member the extractor refuses outright"
                )
            archive_names = set(archive_namelist)
            missing_from_archive = set(members) - archive_names
            if missing_from_archive:
                raise BundleVerificationError(
                    f"archive {archive_path} does not contain acquired "
                    f"member(s) {sorted(missing_from_archive)}; a bundle "
                    "manifest must not claim closure over a file the "
                    "archive lacks"
                )
            extra_in_archive = archive_names - set(members)
            if extra_in_archive:
                raise BundleVerificationError(
                    f"archive {archive_path} contains "
                    f"{sorted(extra_in_archive)}, which the plan does not "
                    "name; a bundle manifest must not claim closure "
                    "narrower than the archive's actual contents"
                )
            for filename, record in members.items():
                info = archive.getinfo(filename)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise BundleVerificationError(
                        f"archive member {filename!r} is a symlink; the "
                        "extractor refuses a symlink member regardless of "
                        "what its declared content bytes are"
                    )
                if info.file_size != record["size"]:
                    raise BundleVerificationError(
                        f"archive member {filename!r} declares size "
                        f"{info.file_size}, but the acquired file on disk "
                        f"was {record['size']} bytes"
                    )
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > MAX_COMPRESSION_RATIO:
                        raise BundleVerificationError(
                            f"archive member {filename!r} has a compression "
                            f"ratio of {ratio:.1f}, exceeding the "
                            f"{MAX_COMPRESSION_RATIO}x cap the archive "
                            "extractor enforces; refusing as a suspected "
                            "zip bomb"
                        )
                member_sha256 = sha256_hex(archive.read(filename))
                if member_sha256 != record["sha256"]:
                    raise BundleVerificationError(
                        f"archive member {filename!r} content digest "
                        f"{member_sha256} does not match the acquired "
                        f"file's digest {record['sha256']}"
                    )
    except (zipfile.BadZipFile, OSError) as exc:
        raise BundleVerificationError(
            f"cannot open {archive_path} as a ZIP archive to verify it "
            f"contains the acquired files: {exc}"
        ) from exc

    _refuse_malformed_manifest_run(run)
    manifest: dict[str, Any] = {
        "schema_version": schema_version,
        "plan_digest": expected.plan_digest,
        "archive_sha256": archive_sha256,
        "members": members,
        "run": {
            "repository_full_name": run["repository_full_name"],
            "repository_id": run["repository_id"],
            "workflow_path": run["workflow_path"],
            "run_id": run["run_id"],
            "run_attempt": run["run_attempt"],
            "trusted_workflow_sha": run["trusted_workflow_sha"],
            "artifact_id": run["artifact_id"],
            "artifact_name": run["artifact_name"],
            "artifact_run_id": run["artifact_run_id"],
            "environment_name": run["environment_name"],
        },
    }
    _refuse_malformed_bundle_manifest_shape(manifest)
    return manifest


_PEP503_NAME_RUNS = re.compile(r"[-_.]+")

#: The only characters a distribution name may ever contain (PEP 503 /
#: packaging's name grammar). Checked on the INPUT, before normalisation —
#: this is what makes a path separator, or any other unexpected character,
#: a refusal rather than a value that normalises to itself and is silently
#: trusted as "already valid".
_PEP503_VALID_NAME_CHARACTERS = re.compile(r"\A[A-Za-z0-9._-]+\Z")


def _normalise_pep503_name(name: str) -> str:
    """PEP 503 normalisation, porting ERP's validating behaviour with
    Starter's additional non-string refusal, from
    `scripts/dependency_normalisation.py` `normalise_name` (the validating
    form): runs of `-`, `_`, `.` collapse to one `-`, lower-cased. Raises
    `ValueError` if `name` is not a string, contains any character outside
    `[A-Za-z0-9._-]`, or if the normalised result starts or ends with `-`
    — no valid distribution name can start or end with a separator, and
    PEP 503 normalisation does not strip one."""

    if not isinstance(name, str) or not _PEP503_VALID_NAME_CHARACTERS.match(name):
        raise ValueError(
            f"{name!r} is not a valid distribution name; only ASCII "
            "letters, digits, '.', '_', and '-' are permitted"
        )
    normalised = _PEP503_NAME_RUNS.sub("-", name).lower()
    if normalised.startswith("-") or normalised.endswith("-"):
        raise ValueError(
            f"{name!r} normalises to {normalised!r}, which starts or ends "
            "with a separator; PEP 503 normalisation does not strip this, "
            "and no valid distribution name can start or end with one"
        )
    return normalised


def build_local_index(
    index_root: Path,
    expected: ExpectedArtifactSet,
    bundle_manifest: dict[str, Any],
    extracted_dir: Path,
) -> None:
    """Materialise a local PEP 503 "simple" index under `index_root/simple`,
    atomically — the ONLY sanctioned way to publish one.

    See the module docstring's "Slice 1a-iv" for why this is NOT a verbatim
    port of ERP's `build_local_index`: this version never accepts a
    caller-assembled package map. It instead reconciles three independently
    obtained inputs before copying a single byte:

    1. PLAN (`expected`) against MANIFEST (`bundle_manifest["members"]`),
       in both directions — every artifact the plan names must appear in
       the manifest with the same package and the same hash, and the
       manifest must name nothing the plan does not expect.
    2. MANIFEST against the files actually present under `extracted_dir`,
       in both directions — every member the manifest names must exist on
       disk, and no extra file may be present.
    3. Duplicates and emptiness — a plan naming one filename twice is
       refused outright, and an empty plan or an empty manifest is refused
       rather than silently publishing nothing.
    4. Every file about to be copied is RE-HASHED from its current bytes on
       disk and compared against the PLAN's own expected digest — never
       the manifest's recorded digest, and never a hash trusted from an
       earlier verification pass. This is what catches a file modified
       AFTER extraction but before this function runs; steps 1-3 alone
       cannot, because the manifest's recorded hash does not change when
       the file on disk does.

    Only after all four steps pass does this function build the PEP 503
    package map itself, grouping by `expected`'s own
    `package_normalised_name`. Publication then follows the exact same
    atomic shape as `extract_verified_bundle`: a fresh, exclusive staging
    directory, then a single atomic rename. `index_root` must not already
    exist — this function materialises a fresh tree, it never merges into
    or overwrites one.

    Every refusal here raises `BundleVerificationError`, this module's own
    root — never ERP's `ManifestError` or `DependencyBundleError`.

    STATED, NARROW RACE (not claimed to be closed) — identical to
    `extract_verified_bundle`'s: existence is checked both here and again
    immediately before the final rename, with no portable, dependency-free
    "rename unless the destination exists" primitive for a directory target
    in the Python standard library.
    """

    if index_root.exists():
        raise BundleVerificationError(
            f"destination {index_root} already exists; build_local_index "
            "materialises a fresh tree and refuses to merge into or "
            "overwrite one"
        )

    if not expected.artifacts:
        raise BundleVerificationError(
            "expected artifact set names no artifacts; refusing to "
            "publish an empty index"
        )

    _refuse_malformed_bundle_manifest_shape(bundle_manifest)
    if bundle_manifest.get("plan_digest") != expected.plan_digest:
        raise BundleVerificationError(
            f"bundle manifest plan_digest {bundle_manifest.get('plan_digest')!r} "
            "disagrees with the expected artifact set's plan_digest "
            f"{expected.plan_digest!r}"
        )
    members = bundle_manifest.get("members")
    if not isinstance(members, dict) or not members:
        raise BundleVerificationError("bundle manifest carries no members to publish")

    # ── reconciliation step 1 & 3a: the plan itself, duplicates refused ──
    plan_by_filename: dict[str, ExpectedArtifact] = {}
    for artifact in expected.artifacts:
        # Validate the filename's SHAPE here, before this name is ever used
        # to enumerate `extracted_dir` (step 2), to rehash a file (step 4),
        # or to derive a staging path (step 5) — a rejected name must never
        # reach a read. This is the same bare-filename check step 5 applies
        # when it joins `pkg_dir / filename`; it is applied HERE first so
        # that check never has the chance to run after a read has already
        # happened.
        if not isinstance(artifact.filename, str) or not artifact.filename:
            raise BundleVerificationError(
                "expected artifact filename must be a non-empty string, "
                f"got {artifact.filename!r}"
            )
        if (
            "/" in artifact.filename
            or "\\" in artifact.filename
            or artifact.filename in (".", "..")
            or Path(artifact.filename).is_absolute()
        ):
            raise BundleVerificationError(
                f"filename {artifact.filename!r} is not a safe bare filename "
                "(no path separators, not absolute, not '.' or '..')"
            )
        if artifact.filename in plan_by_filename:
            raise BundleVerificationError(
                f"expected artifact set names {artifact.filename!r} twice; "
                "duplicate entries are refused"
            )
        plan_by_filename[artifact.filename] = artifact

    manifest_by_filename: dict[str, tuple[str, str]] = {}
    for name, record in members.items():
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("sha256"), str)
            or not _SHA256_HEX.match(record["sha256"])
            or not isinstance(record.get("package"), str)
            or not record.get("package")
        ):
            raise BundleVerificationError(
                f"bundle manifest member {name!r} is malformed: {record!r}"
            )
        manifest_by_filename[name] = (record["package"], record["sha256"])

    # ── reconciliation step 1: plan <-> manifest, both directions ────────
    missing_from_manifest = set(plan_by_filename) - set(manifest_by_filename)
    if missing_from_manifest:
        raise BundleVerificationError(
            f"the plan expects {sorted(missing_from_manifest)}, which the "
            "verified manifest does not contain"
        )
    extra_in_manifest = set(manifest_by_filename) - set(plan_by_filename)
    if extra_in_manifest:
        raise BundleVerificationError(
            f"the verified manifest names {sorted(extra_in_manifest)}, "
            "which the plan does not expect"
        )
    for filename, artifact in plan_by_filename.items():
        manifest_package, manifest_sha256 = manifest_by_filename[filename]
        if manifest_package != artifact.package_normalised_name:
            raise BundleVerificationError(
                f"{filename!r} is associated with package {manifest_package!r} "
                "in the verified manifest, but the plan expects package "
                f"{artifact.package_normalised_name!r}"
            )
        if manifest_sha256 != artifact.sha256:
            raise BundleVerificationError(
                f"{filename!r} carries sha256 {manifest_sha256} in the "
                "verified manifest, disagreeing with the plan's expected "
                f"sha256 {artifact.sha256}"
            )

    # ── reconciliation step 2: manifest <-> extracted files, both directions
    #
    # `Path.is_file()` FOLLOWS a symlink — it asks what the link resolves
    # to, not what the entry itself is. That makes it the wrong predicate
    # here: a live symlink pointing OUTSIDE `extracted_dir`, to bytes that
    # happen to match the expected hash, would enumerate identically to the
    # real file it replaced (plan<->manifest closure, manifest<->disk
    # closure, and the step-4 rehash all pass unchanged, because the rehash
    # reads THROUGH the link too), and this function would then copy bytes
    # from outside the verified tree. A DANGLING symlink is worse in the
    # other direction: `is_file()` returns `False` for it, so it would be
    # silently OMITTED from `extracted_names` rather than refused — a hole
    # in a set this function trusts for BOTH directions of closure, not an
    # entry the "extra file" check below ever gets a chance to see.
    #
    # `Path.lstat()` never follows a symlink, so `stat.S_ISREG` on its mode
    # tells us what the entry itself is, not what it points to. A directory
    # is skipped as structure; anything that is neither a directory nor a
    # regular file — a symlink, dangling or not, a fifo, a device node —
    # is refused BY NAME here, rather than silently filtered out of the set
    # or transparently resolved through.
    extracted_names: set[str] = set()
    try:
        for entry in extracted_dir.rglob("*"):
            relative_name = entry.relative_to(extracted_dir).as_posix()
            mode = entry.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise BundleVerificationError(
                    f"extracted entry {relative_name!r} is not a regular "
                    "file (refusing a symlink or other special file by "
                    "name, rather than silently omitting or following it)"
                )
            extracted_names.add(relative_name)
    except OSError as exc:
        raise BundleVerificationError(
            f"cannot list extracted directory {extracted_dir}: {exc}"
        ) from exc
    manifest_names = set(manifest_by_filename)
    missing_on_disk = manifest_names - extracted_names
    if missing_on_disk:
        raise BundleVerificationError(
            f"the verified manifest expects {sorted(missing_on_disk)}, "
            "which the extracted directory does not contain"
        )
    extra_on_disk = extracted_names - manifest_names
    if extra_on_disk:
        raise BundleVerificationError(
            f"the extracted directory contains {sorted(extra_on_disk)}, "
            "which the verified manifest does not name"
        )

    # ── reconciliation step 4: rehash every file about to be copied ──────
    # Never trust a recorded hash — not the manifest's, and not one
    # verified at an earlier point in time. This is the only step that can
    # catch a file modified on disk after extraction was verified.
    for filename, artifact in plan_by_filename.items():
        source_path = extracted_dir / filename
        try:
            actual_sha256 = sha256_hex(source_path.read_bytes())
        except OSError as exc:
            raise BundleVerificationError(
                f"cannot read extracted file {filename!r} to publish: {exc}"
            ) from exc
        if actual_sha256 != artifact.sha256:
            raise BundleVerificationError(
                f"extracted file {filename!r} hashes to {actual_sha256} on "
                "disk right now, disagreeing with the plan's expected "
                f"sha256 {artifact.sha256}; refusing to publish a file "
                "that changed after verification"
            )

    # ── step 5: build the PEP 503 package map ourselves ──────────────────
    packages: dict[str, list[tuple[str, str, Path]]] = {}
    for filename, artifact in sorted(plan_by_filename.items()):
        try:
            canonical_name = _normalise_pep503_name(artifact.package_normalised_name)
        except ValueError as exc:
            raise BundleVerificationError(
                "expected artifact package name "
                f"{artifact.package_normalised_name!r} is not a valid PEP "
                f"503 name: {exc}"
            ) from exc
        if canonical_name != artifact.package_normalised_name:
            raise BundleVerificationError(
                "expected artifact package name "
                f"{artifact.package_normalised_name!r} is not "
                "PEP-503-normalised"
            )
        packages.setdefault(canonical_name, []).append(
            (filename, artifact.sha256, extracted_dir / filename)
        )

    # ── stage and publish, atomically — same shape as extract_verified_bundle
    try:
        index_root.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BundleVerificationError(
            f"cannot create parent directory for {index_root}: {exc}"
        ) from exc
    try:
        staging_root = Path(
            tempfile.mkdtemp(
                prefix=f".{index_root.name}.staging.", dir=str(index_root.parent)
            )
        )
    except OSError as exc:
        raise BundleVerificationError(
            f"cannot create a staging directory beside {index_root}: {exc}"
        ) from exc

    try:
        root_dir = staging_root / "simple"
        try:
            root_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BundleVerificationError(
                f"cannot create index root {root_dir}: {exc}"
            ) from exc
        for canonical_name, files in packages.items():
            pkg_dir = root_dir / canonical_name
            resolved_pkg_dir = pkg_dir.resolve()
            if not _is_within(root_dir.resolve(), resolved_pkg_dir):
                raise BundleVerificationError(
                    f"package {canonical_name!r} resolves outside the index directory"
                )
            try:
                pkg_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                # A charset-valid but overlong name (every character
                # permitted by `_normalise_pep503_name`, but the whole
                # string longer than the filesystem's per-component limit)
                # is never rejected by the checks above — only the
                # filesystem itself refuses it, with `OSError`
                # (`ENAMETOOLONG`), which must be translated here rather
                # than left to escape this function raw.
                raise BundleVerificationError(
                    f"cannot create package directory for {canonical_name!r}: {exc}"
                ) from exc
            anchors = []
            for filename, digest_hex, source_path in sorted(files, key=lambda t: t[0]):
                # A plan filename must be a bare filename: no path
                # separator, not absolute, not `.`/`..`. Without this,
                # `pkg_dir / filename` can write outside `pkg_dir` —
                # `Path.__truediv__` REPLACES the left side entirely when
                # the right side is absolute. Normal calls have already
                # passed the identical check while building
                # `plan_by_filename`; this second check is a defensive
                # invariant guard and is unreachable end-to-end unless that
                # earlier plan-ingress validation is bypassed.
                if (
                    not filename
                    or "/" in filename
                    or "\\" in filename
                    or filename in (".", "..")
                    or Path(filename).is_absolute()
                ):
                    raise BundleVerificationError(
                        f"filename {filename!r} is not a safe bare filename "
                        "(no path separators, not absolute, not '.' or '..')"
                    )
                try:
                    (pkg_dir / filename).write_bytes(source_path.read_bytes())
                except OSError as exc:
                    raise BundleVerificationError(
                        f"cannot stage {filename!r}: {exc}"
                    ) from exc
                # `html.escape` and URL-quoting solve two DIFFERENT
                # problems: one stops a filename from breaking out of the
                # HTML attribute/text context, the other stops it from
                # being reinterpreted as part of the URL's own grammar once
                # a resolver requests the href. Applied URL-quote first,
                # HTML-escape second; the anchor TEXT is HTML-escaped only.
                href_filename = html.escape(
                    urllib.parse.quote(filename, safe=""), quote=True
                )
                safe_filename_text = html.escape(filename, quote=True)
                safe_digest = html.escape(digest_hex, quote=True)
                anchors.append(
                    f'<a href="{href_filename}#sha256={safe_digest}">'
                    f"{safe_filename_text}</a><br/>"
                )
            try:
                (pkg_dir / "index.html").write_text(
                    "<!DOCTYPE html><html><body>\n"
                    + "\n".join(anchors)
                    + "\n</body></html>\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                raise BundleVerificationError(
                    f"cannot write package index for {canonical_name!r}: {exc}"
                ) from exc
        try:
            package_names = sorted(p.name for p in root_dir.iterdir() if p.is_dir())
        except OSError as exc:
            raise BundleVerificationError(
                f"cannot list staged index root {root_dir}: {exc}"
            ) from exc
        root_anchors = [
            f'<a href="{html.escape(name, quote=True)}/">'
            f"{html.escape(name, quote=True)}</a><br/>"
            for name in package_names
        ]
        try:
            (root_dir / "index.html").write_text(
                "<!DOCTYPE html><html><body>\n"
                + "\n".join(root_anchors)
                + "\n</body></html>\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise BundleVerificationError(
                f"cannot write root index at {root_dir}: {exc}"
            ) from exc
    except BaseException:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise

    if index_root.exists():
        shutil.rmtree(staging_root, ignore_errors=True)
        raise BundleVerificationError(
            f"destination {index_root} was created concurrently while "
            "staging; refusing to publish over it"
        )
    try:
        os.rename(staging_root, index_root)
    except OSError as exc:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise BundleVerificationError(
            f"cannot publish staged index to {index_root}: {exc}"
        ) from exc
