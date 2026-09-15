"""`scripts/bundle_envelope.py`: the dependency-bundle envelope's canonical
serialization and hashing core.

Loaded the same way `test_host_attester_producer.py` loads `host_attester.py`
— by file location, registered in `sys.modules` before execution (the fix
`d2a2e9e0` made to this repository's own test suite: a module executed
before registration cannot resolve its own `from __future__ import
annotations` postponed evaluation correctly under some import orders).

`canonical_json_bytes` has ZERO tests today in `dotmac_erp`, despite every
plan and bundle digest depending on its exact byte output. Each test below
proves exactly ONE property, asserting exact bytes (never a property of the
output), so a regression names which property broke. Each docstring states
the implementation change that would make the test fail.

## Slice 1a-ii: archive and member hash verification

`test_archive_digest_matches`, `test_archive_digest_mismatch_is_refused`,
`test_member_hash_verification_refuses_a_mismatch`, and
`test_member_hash_verification_refuses_a_missing_file` are ported from
`dotmac_erp` `tests/architecture/test_dependency_bundle.py` at the same
pinned commit as the module docstring — the accumulated adversarial
evidence travels with the code, not just its behaviour. ERP has no test
naming `_refuse_malformed_manifest_run` or
`_refuse_malformed_bundle_manifest_shape` directly (both are exercised only
indirectly there, through `extract_verified_bundle`, which this slice does
not port); the refusal tests for those two functions below are new.

## Slice 1a-iii: safe extraction

Every test below naming `extract_verified_bundle` is ported from the same
ERP file at the same pinned commit — the 20 tests are the accumulated
adversarial evidence for the one sanctioned extraction entry point:
duplicate members, absolute paths, `..` traversal, symlinks, case
collisions, oversize members, unlisted members, manifest-declared members
absent from the archive, compression-ratio bombs, and atomicity on both the
accept and refuse paths. `test_a_clean_bundle_extracts_and_publishes_atomically`
is the mandatory accept-direction control: without it, a verifier that
refuses every archive would pass all nineteen refusal tests for the wrong
reason.

## Slice 1a-iv: the local PEP 503 index — NOT a verbatim port

`build_local_index`'s new signature (`ExpectedArtifactSet`, the verified
manifest, the extracted directory — never a caller-assembled package map)
is a deliberate interface change, not a port; see the module docstring's
"Slice 1a-iv" for the gap this closes. Of ERP's 17 tests naming
`build_local_index` (`tests/architecture/test_dependency_bundle.py` at the
same pinned commit), the ones below whose property survives the interface
change are ADAPTED to the new signature (destination pre-existence,
malformed/overlong/unnormalised package names, unsafe filenames, HTML/URL
escaping in anchors, staging-phase `OSError` translation). One
(`test_build_local_index_is_atomic_on_failure_nothing_is_published`) is
DROPPED in its original shape: its scenario was a caller-supplied
`source_path` that does not exist, which the new signature cannot express
— every file this function copies now comes from `extracted_dir`, whose
membership is itself proven by reconciliation step 2 before any staging
begins. Its atomicity property is instead re-proven against the failure
mode that still exists under the new signature: an `OSError` raised
mid-staging (`test_build_local_index_is_atomic_on_failure_during_staging`).
The non-string-package-key test is adapted, not dropped: `ExpectedArtifact`
is a dataclass with no runtime type enforcement, so a non-string
`package_normalised_name` can still reach `_normalise_pep503_name`, which
now carries its own `isinstance` guard precisely so that case is translated
rather than raising a raw `TypeError`.

New reconciliation plants (Michael's required set): a clean multi-package
accept-direction control, missing artifact, extra artifact, wrong package
association, wrong hash, duplicate entry, and — the property step 4 exists
to prove — a file tampered with AFTER extraction but before
`build_local_index` runs, which only the rehash (never the manifest's
recorded digest) can catch.

## Slice 1a-v: the canonical envelope constructor

`create_bundle_manifest` has no ERP test to port from directly usable
as-is: ERP's own `tests/architecture/test_dependency_bundle.py` exercises
its `create_bundle_manifest` through a `DependencySurface`, which this
module's version never accepts (see the module docstring's "Slice 1a-v").
Every test below is new, built against Michael's required set: the
mandatory clean-construction accept-direction control, a manifest ->
`_refuse_malformed_bundle_manifest_shape` round-trip, the four closure
refusals (expected-not-acquired, acquired-not-expected,
archive-has-an-unlisted-member, archive-missing-a-declared-member), a
tampered-after-planning acquired file proving the constructor computes
rather than accepts a hash, a tampered archive member proving the same
for the archive side, and the two non-finite-value plants for the
input-domain gate (`package_normalised_name`, `plan_digest`).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import urllib.parse
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"


def _load_bundle_envelope():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "bundle_envelope", SCRIPTS / "bundle_envelope.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BUNDLE_ENVELOPE = _load_bundle_envelope()
canonical_json_bytes = BUNDLE_ENVELOPE.canonical_json_bytes
sha256_hex = BUNDLE_ENVELOPE.sha256_hex
BundleEnvelopeError = BUNDLE_ENVELOPE.BundleEnvelopeError
BundleVerificationError = BUNDLE_ENVELOPE.BundleVerificationError
verify_archive_digest = BUNDLE_ENVELOPE.verify_archive_digest
verify_member_hashes = BUNDLE_ENVELOPE.verify_member_hashes
_refuse_malformed_manifest_run = BUNDLE_ENVELOPE._refuse_malformed_manifest_run
_refuse_malformed_bundle_manifest_shape = (
    BUNDLE_ENVELOPE._refuse_malformed_bundle_manifest_shape
)
MANIFEST_SCHEMA_VERSION = BUNDLE_ENVELOPE.MANIFEST_SCHEMA_VERSION
ExtractionError = BUNDLE_ENVELOPE.ExtractionError
extract_verified_bundle = BUNDLE_ENVELOPE.extract_verified_bundle
_extract_zip_members = BUNDLE_ENVELOPE._extract_zip_members
_is_within = BUNDLE_ENVELOPE._is_within
ExpectedArtifact = BUNDLE_ENVELOPE.ExpectedArtifact
ExpectedArtifactSet = BUNDLE_ENVELOPE.ExpectedArtifactSet
build_local_index = BUNDLE_ENVELOPE.build_local_index
_normalise_pep503_name = BUNDLE_ENVELOPE._normalise_pep503_name
create_bundle_manifest = BUNDLE_ENVELOPE.create_bundle_manifest


def test_canonical_json_bytes_sorts_keys():
    """Keys inserted out of alphabetical order still serialize sorted.

    Breaks if `sort_keys=True` is dropped (or flipped to `False`): a plain
    dict preserves Python 3.7+ insertion order, so the un-sorted call would
    instead produce `b'{"b":1,"a":2}\\n'` and this exact-bytes assertion
    would fail.
    """

    document = {"b": 1, "a": 2}

    assert canonical_json_bytes(document) == b'{"a":2,"b":1}\n'


def test_canonical_json_bytes_uses_compact_separators():
    """No space after `,` or `:`.

    Breaks if `separators=(",", ":")` is dropped: `json.dumps`'s default
    separators are `(", ", ": ")`, which would produce
    `b'{"a": [1, 2], "b": 3}\\n'` instead of the compact form asserted here.
    """

    document = {"a": [1, 2], "b": 3}

    assert canonical_json_bytes(document) == b'{"a":[1,2],"b":3}\n'


def test_canonical_json_bytes_escapes_non_ascii():
    """A non-ASCII character serializes as a `\\uXXXX` escape, not raw UTF-8.

    Breaks if `ensure_ascii=True` is dropped (or flipped to `False`):
    `json.dumps(..., ensure_ascii=False)` would emit the raw UTF-8 encoding
    of U+00E9 (`b"\\xc3\\xa9"`) instead of the seven-byte ASCII escape
    sequence `b'\\u00e9'` asserted here — unambiguous because the two byte
    sequences share no prefix.
    """

    document = {"a": "\u00e9"}

    assert canonical_json_bytes(document) == b'{"a":"\\u00e9"}\n'


def test_canonical_json_bytes_ends_with_exactly_one_newline():
    """Output ends with exactly one trailing `\\n` — not zero, not two.

    Breaks if the `+ "\\n"` is dropped (last byte would be `}`, failing the
    first assertion) or duplicated to `+ "\\n\\n"` (the second-to-last byte
    would also be `\\n`, failing the second assertion).
    """

    output = canonical_json_bytes({"a": 1})

    assert output[-1:] == b"\n"
    assert output[-2:-1] != b"\n"


def test_canonical_json_bytes_key_order_does_not_affect_output():
    """Two documents differing only in key insertion order produce IDENTICAL
    bytes.

    This is the accept-direction control for the four property tests above:
    without it, an implementation that always returned a constant byte
    string, or that raised on every input, would satisfy every "breaks if
    X is dropped" assertion above for the wrong reason (there would be no
    input on which the function is required to actually agree with itself).
    Breaks if canonicalization stops being order-independent — e.g. if
    `sort_keys=True` were replaced by anything that leaks insertion order
    into the output.
    """

    first = {"a": 1, "b": 2}
    second = {"b": 2, "a": 1}

    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_sha256_hex_known_answer():
    """Known-answer vector for `sha256_hex`, independent of `hashlib`'s own
    test suite.

    Breaks if the digest is computed over the wrong input (e.g. hashing a
    `str` instead of `bytes`, or hashing something other than `data`
    verbatim), or if a different hash algorithm is substituted.
    """

    assert (
        sha256_hex(b"abc")
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert (
        sha256_hex(b"")
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


# ── exception hierarchy ───────────────────────────────────────────────


def test_bundle_verification_error_is_a_bundle_envelope_error():
    """`BundleVerificationError` is a subclass of the generic
    `BundleEnvelopeError` root.

    Breaks if `BundleVerificationError` is changed back to inherit
    `Exception` directly instead of `BundleEnvelopeError` — the two-root
    split this module's docstring describes would then be undeclared in
    code, even though the docstring still claimed it.
    """

    assert issubclass(BundleVerificationError, BundleEnvelopeError)


def test_catching_bundle_envelope_error_catches_a_raised_verification_error():
    """A handler that catches `BundleEnvelopeError` genuinely catches a
    `BundleVerificationError` actually raised at runtime — not just an
    `issubclass` relationship on paper. This is the exact behaviour ERP's
    CLI refusal handler will depend on at cutover.

    Breaks if `BundleVerificationError` stops inheriting
    `BundleEnvelopeError` (or inherits it only nominally through some
    broken MRO): the `except BundleEnvelopeError` clause below would then
    not fire, and the raised error would propagate past it uncaught.
    """

    caught: BaseException | None = None
    try:
        raise BundleVerificationError("boom")
    except BundleEnvelopeError as exc:
        caught = exc

    assert isinstance(caught, BundleVerificationError)


def test_bundle_envelope_error_inherits_exception_directly():
    """`BundleEnvelopeError`'s only base is `Exception` itself — no ERP
    class, and no other intermediate class, sits above it.

    Breaks if `BundleEnvelopeError` is ever changed to inherit from some
    other class instead of `Exception` (e.g. re-parented under an ERP
    `DependencyBundleError` import, which is specifically the re-export
    design this module's docstring rules out): `__bases__` would then no
    longer be exactly `(Exception,)`.
    """

    assert BundleEnvelopeError.__bases__ == (Exception,)


# ── archive digest ────────────────────────────────────────────────────


def test_archive_digest_matches(tmp_path: Path):
    """The accept-direction control for the refusal test below: a verifier
    that refuses every archive (or always raises) would pass every
    refusal test in this module for the wrong reason. This is the only
    test proving `verify_archive_digest` actually accepts a correct
    digest and returns it.

    Breaks if `verify_archive_digest` is changed to always raise, to
    return a value other than the computed digest, or to compare against
    the wrong bytes.
    """

    archive = tmp_path / "bundle.zip"
    archive.write_bytes(b"hello world")
    expected = sha256_hex(b"hello world")

    assert verify_archive_digest(archive, expected) == expected


def test_archive_digest_mismatch_is_refused(tmp_path: Path):
    """A digest that does not match the archive's actual bytes is refused.

    Breaks if the comparison is dropped, inverted, or short-circuited
    (e.g. `digest != expected_sha256` replaced by a check that never
    fires), which would let `verify_archive_digest` return a wrong
    digest as though it matched.
    """

    archive = tmp_path / "bundle.zip"
    archive.write_bytes(b"hello world")

    with pytest.raises(BundleVerificationError, match="digest mismatch"):
        verify_archive_digest(archive, "0" * 64)


# ── member hashes ─────────────────────────────────────────────────────


def test_member_hash_verification_refuses_a_mismatch(tmp_path: Path):
    """An extracted file whose content hash disagrees with the expected
    per-member hash is refused.

    Breaks if the per-member digest comparison is dropped or inverted,
    which would let a file with the wrong content pass as verified.
    """

    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "a.whl").write_bytes(b"AAAA")

    with pytest.raises(BundleVerificationError, match="hash mismatch"):
        verify_member_hashes(dest, {"a.whl": sha256_hex(b"different")})


def test_member_hash_verification_refuses_a_missing_file(tmp_path: Path):
    """An expected member absent from disk is refused rather than silently
    skipped.

    Breaks if the `path.is_file()` existence check is dropped, which
    would make `path.read_bytes()` raise an unrelated, uncaught `OSError`
    instead of this function's own `BundleVerificationError` naming the
    missing member.
    """

    dest = tmp_path / "out"
    dest.mkdir()

    with pytest.raises(BundleVerificationError, match="missing"):
        verify_member_hashes(dest, {"a.whl": sha256_hex(b"AAAA")})


# ── manifest run shape ────────────────────────────────────────────────


def test_refuse_malformed_manifest_run_accepts_a_well_formed_run():
    """The accept-direction control for the refusal tests below: a check
    that refuses every `run` dict would pass every refusal test for the
    wrong reason.

    Breaks if `_refuse_malformed_manifest_run` is changed to raise
    unconditionally, or to check a field this well-formed fixture does
    not satisfy.
    """

    run = {
        "repository_id": 1,
        "run_id": 2,
        "run_attempt": 1,
        "artifact_id": 3,
        "artifact_run_id": 4,
        "repository_full_name": "dotmac/erp",
        "workflow_path": ".github/workflows/build.yml",
        "artifact_name": "bundle",
        "environment_name": "production",
        "trusted_workflow_sha": "a" * 40,
    }

    _refuse_malformed_manifest_run(run)


def test_refuse_malformed_manifest_run_refuses_the_null_sha():
    """`trusted_workflow_sha` equal to the all-zero null SHA is refused even
    though it matches the 40-hex commit-SHA shape.

    Breaks if the `trusted_sha == _NULL_SHA` check is dropped, leaving
    only the regex match — the null SHA is exactly 40 hex characters and
    would otherwise pass.
    """

    run = {
        "repository_id": 1,
        "run_id": 2,
        "run_attempt": 1,
        "artifact_id": 3,
        "artifact_run_id": 4,
        "repository_full_name": "dotmac/erp",
        "workflow_path": ".github/workflows/build.yml",
        "artifact_name": "bundle",
        "environment_name": "production",
        "trusted_workflow_sha": "0" * 40,
    }

    with pytest.raises(BundleVerificationError, match="null SHA"):
        _refuse_malformed_manifest_run(run)


def test_refuse_malformed_manifest_run_refuses_a_boolean_for_a_positive_int_field():
    """A `bool` value for a positive-int field is refused, even though
    `isinstance(True, int)` is `True` in Python.

    Breaks if the `isinstance(value, bool)` exclusion is dropped from the
    int-field check, which would let `run_id: True` silently pass as the
    integer `1`.
    """

    run = {
        "repository_id": 1,
        "run_id": True,
        "run_attempt": 1,
        "artifact_id": 3,
        "artifact_run_id": 4,
        "repository_full_name": "dotmac/erp",
        "workflow_path": ".github/workflows/build.yml",
        "artifact_name": "bundle",
        "environment_name": "production",
        "trusted_workflow_sha": "a" * 40,
    }

    with pytest.raises(BundleVerificationError, match=r"run\.run_id"):
        _refuse_malformed_manifest_run(run)


# ── bundle manifest shape ─────────────────────────────────────────────


def test_refuse_malformed_bundle_manifest_shape_accepts_a_well_formed_manifest():
    """The accept-direction control for the refusal test below: a check
    that refuses every manifest would pass the refusal test for the
    wrong reason.

    Breaks if `_refuse_malformed_bundle_manifest_shape` is changed to
    raise unconditionally, or to check a field this well-formed fixture
    does not satisfy.
    """

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "plan_digest": "a" * 64,
        "archive_sha256": "b" * 64,
        "run": {
            "repository_id": 1,
            "run_id": 2,
            "run_attempt": 1,
            "artifact_id": 3,
            "artifact_run_id": 4,
            "repository_full_name": "dotmac/erp",
            "workflow_path": ".github/workflows/build.yml",
            "artifact_name": "bundle",
            "environment_name": "production",
            "trusted_workflow_sha": "c" * 40,
        },
    }

    _refuse_malformed_bundle_manifest_shape(manifest)


def test_refuse_malformed_bundle_manifest_shape_refuses_a_wrong_schema_version():
    """A `schema_version` other than the recognised `MANIFEST_SCHEMA_VERSION`
    is refused before `plan_digest`, `archive_sha256`, or `run` are ever
    examined.

    Breaks if the `schema_version != MANIFEST_SCHEMA_VERSION` check is
    dropped or loosened (e.g. to accept any int), which would let a
    manifest produced under an incompatible future schema be treated as
    this one.
    """

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION + 1,
        "plan_digest": "a" * 64,
        "archive_sha256": "b" * 64,
        "run": {},
    }

    with pytest.raises(BundleVerificationError, match="schema_version"):
        _refuse_malformed_bundle_manifest_shape(manifest)


# ── extraction: exception hierarchy ─────────────────────────────────────


def test_extraction_error_is_a_bundle_envelope_error():
    """`ExtractionError` is a subclass of the generic `BundleEnvelopeError`
    root, per Michael's ruling — not of ERP's `DependencyBundleError`.

    Breaks if `ExtractionError` is re-parented under some other class (in
    particular an ERP-imported `DependencyBundleError`, the re-export
    design this module's docstring rules out): `issubclass` would then
    fail.
    """

    assert issubclass(ExtractionError, BundleEnvelopeError)


# ── extraction: fixtures ─────────────────────────────────────────────────


def _make_zip(
    tmp_path: Path, members: dict[str, bytes], *, name: str = "bundle.zip"
) -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        for member_name, data in members.items():
            archive.writestr(member_name, data)
    return path


#: A FULLY shape-valid `run` record — `extract_verified_bundle` checks every
#: one of these fields itself via `_refuse_malformed_bundle_manifest_shape`,
#: so every manifest fixture below must carry a complete one, not an empty
#: placeholder `{}`.
_VALID_MANIFEST_RUN: dict = {
    "repository_full_name": "michaelayoade/dotmac_erp",
    "repository_id": 1141216651,
    "workflow_path": ".github/workflows/dependency-bundle-produce.yml",
    "run_id": 111,
    "run_attempt": 1,
    "trusted_workflow_sha": "a" * 40,
    "artifact_id": 222,
    "artifact_name": "erp-dependency-bundle-x",
    "artifact_run_id": 111,
    "environment_name": "forgejo-registry-read-main",
}


def _manifest_for(members: dict[str, bytes]) -> dict:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "plan_digest": "a" * 64,
        "archive_sha256": "b" * 64,
        "members": {
            name: {
                "sha256": sha256_hex(data),
                "size": len(data),
                "package": "dotmac-kernel",
            }
            for name, data in members.items()
        },
        "run": dict(_VALID_MANIFEST_RUN),
    }


def _mutate(manifest: dict, mutation) -> dict:
    mutated = json.loads(json.dumps(manifest))
    mutation(mutated)
    return mutated


# ── extraction: only entry point ─────────────────────────────────────────


def test_extract_verified_bundle_is_the_only_public_entry_point():
    """`extract_verified_bundle` is the only sanctioned entry point;
    `_extract_zip_members` exists but stays private, and no other
    extraction function exists under a different name.

    Breaks if a differently-named public wrapper (e.g. `safe_extract_zip`)
    is (re)introduced, or if `_extract_zip_members`/`extract_verified_bundle`
    are renamed or removed.
    """

    assert not hasattr(BUNDLE_ENVELOPE, "safe_extract_zip")
    assert hasattr(BUNDLE_ENVELOPE, "_extract_zip_members")
    assert hasattr(BUNDLE_ENVELOPE, "extract_verified_bundle")


# ── extraction: the accept-direction control ─────────────────────────────


def test_a_clean_bundle_extracts_and_publishes_atomically(tmp_path: Path):
    """THE MANDATORY ACCEPT-DIRECTION CONTROL: a well-formed archive and a
    manifest that genuinely describes it extracts successfully and its
    bytes land on disk under `dest_dir`.

    Breaks if `extract_verified_bundle` (or anything it calls) is changed
    to refuse every archive unconditionally, or if the returned member
    list / the extracted bytes stop matching what was actually written —
    a verifier that raises for every input passes all nineteen refusal
    tests below for the wrong reason without this test.
    """

    members = {"a.whl": b"AAAA", "b.whl": b"BBBBBB"}
    archive = _make_zip(tmp_path, members)
    dest = tmp_path / "out"

    extracted = extract_verified_bundle(archive, dest, _manifest_for(members))

    assert sorted(extracted) == ["a.whl", "b.whl"]
    assert (dest / "a.whl").read_bytes() == b"AAAA"


# ── extraction: destination and filesystem refusals ──────────────────────


def test_extraction_refuses_a_pre_existing_destination(tmp_path: Path):
    """`dest_dir` already existing is refused before any extraction work
    begins — this function materialises a fresh tree, never merges into
    or overwrites one.

    Breaks if the `dest_dir.exists()` guard at the top of
    `extract_verified_bundle` is dropped, which would let extraction
    proceed and attempt to publish over (or into) an existing directory.
    """

    members = {"a.whl": b"AAAA"}
    archive = _make_zip(tmp_path, members)
    dest = tmp_path / "out"
    dest.mkdir()

    with pytest.raises(ExtractionError, match="already exists"):
        extract_verified_bundle(archive, dest, _manifest_for(members))


def test_extraction_refuses_when_its_parent_directory_cannot_be_created(
    tmp_path: Path,
):
    """Parent-directory creation (`dest_dir.parent.mkdir(...)`) is
    translated into `ExtractionError`, not left as a raw `OSError`.

    Breaks if the try/except wrapping `dest_dir.parent.mkdir(...)` is
    removed: a `NotADirectoryError` (from a path component that is
    actually a file, as constructed below) would then propagate
    uncaught, and `pytest.raises(ExtractionError)` would not catch it.
    """

    members = {"a.whl": b"AAAA"}
    archive = _make_zip(tmp_path, members)
    blocking_file = tmp_path / "not-a-directory"
    blocking_file.write_bytes(b"this is a file, not a directory")
    dest = blocking_file / "nested" / "out"

    with pytest.raises(ExtractionError, match="cannot create parent directory"):
        extract_verified_bundle(archive, dest, _manifest_for(members))


def test_extraction_refuses_when_the_staging_directory_cannot_be_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`tempfile.mkdtemp` failing (no space, no permission, a directory
    raced away) is translated into `ExtractionError`, not left as a raw
    `OSError`. `dest_dir.parent` genuinely exists here — unlike the test
    above, this isolates the `mkdtemp` call site from the parent-creation
    one.

    Breaks if the try/except around the `tempfile.mkdtemp(...)` call is
    removed: the simulated `OSError` below would then propagate uncaught.
    """

    members = {"a.whl": b"AAAA"}
    archive = _make_zip(tmp_path, members)
    dest = tmp_path / "out"

    def _raise(*args, **kwargs):
        raise OSError("simulated: cannot create staging directory")

    monkeypatch.setattr(BUNDLE_ENVELOPE.tempfile, "mkdtemp", _raise)

    with pytest.raises(ExtractionError, match="cannot create a staging directory"):
        extract_verified_bundle(archive, dest, _manifest_for(members))


# ── extraction: manifest shape sensitivity proof ──────────────────────────


@pytest.mark.parametrize(
    "reason,mutation",
    [
        (
            "wrong schema_version",
            lambda m: m.__setitem__("schema_version", MANIFEST_SCHEMA_VERSION - 1),
        ),
        (
            "missing schema_version",
            lambda m: m.__delitem__("schema_version"),
        ),
        (
            "non-hex plan_digest",
            lambda m: m.__setitem__("plan_digest", "not-hex"),
        ),
        (
            "missing plan_digest",
            lambda m: m.__delitem__("plan_digest"),
        ),
        (
            "non-hex archive_sha256",
            lambda m: m.__setitem__("archive_sha256", "not-hex"),
        ),
        (
            "run is not a dict",
            lambda m: m.__setitem__("run", "not-a-dict"),
        ),
        (
            "run missing a required field",
            lambda m: m["run"].__delitem__("run_id"),
        ),
        (
            "run has a negative coordinate",
            lambda m: m["run"].__setitem__("run_id", -1),
        ),
        (
            "run has the null trusted_workflow_sha",
            lambda m: m["run"].__setitem__("trusted_workflow_sha", "0" * 40),
        ),
        (
            "a member is missing its package field",
            lambda m: m["members"]["a.whl"].__delitem__("package"),
        ),
    ],
)
def test_extract_verified_bundle_refuses_every_malformed_manifest_field(
    tmp_path: Path, reason: str, mutation
):
    """Sensitivity proof: `extract_verified_bundle` shape-checks
    `schema_version`, `plan_digest`, `archive_sha256`, `run`, and each
    member's own `package` field — not just `members` — BEFORE any
    extraction work begins. Plants a defect in each field, one at a time,
    holding everything else fixed at a fully valid manifest; the near-miss
    half (the same shape, unmutated, still succeeds) is
    `test_a_clean_bundle_extracts_and_publishes_atomically`.

    Breaks if `_refuse_malformed_bundle_manifest_shape` (or the `run`/
    `members`-level checks it delegates to) stops checking the mutated
    field, or if `extract_verified_bundle` stops calling it before
    extraction — either would let the malformed manifest through, or
    would leave a partial `out` directory on disk.
    """

    members = {"a.whl": b"AAAA"}
    archive = _make_zip(tmp_path, members)
    manifest = _mutate(_manifest_for(members), mutation)

    with pytest.raises(BundleVerificationError):
        extract_verified_bundle(archive, tmp_path / "out", manifest)
    assert not (tmp_path / "out").exists(), reason


# ── extraction: atomicity on failure ──────────────────────────────────────


def test_extraction_is_atomic_on_failure_nothing_is_published(tmp_path: Path):
    """A hash mismatch on one member refuses the WHOLE extraction — no
    partial `dest_dir`, and no stray staging directory left behind beside
    it.

    Breaks if the `except BaseException: shutil.rmtree(staging_dir, ...);
    raise` cleanup around `_extract_zip_members`/`verify_member_hashes` in
    `extract_verified_bundle` is dropped, which would leave the staging
    directory (or worse, a partially-published `dest_dir`) on disk after
    the raise.
    """

    archive = _make_zip(tmp_path, {"good.whl": b"GOOD", "bad.whl": b"BAD"})
    bad_manifest = _manifest_for({"good.whl": b"GOOD", "bad.whl": b"BAD"})
    bad_manifest["members"]["bad.whl"]["sha256"] = sha256_hex(b"WRONG")
    dest = tmp_path / "out"

    with pytest.raises(BundleVerificationError, match="hash mismatch"):
        extract_verified_bundle(archive, dest, bad_manifest)

    assert not dest.exists()
    assert list(tmp_path.iterdir()) == [
        p for p in tmp_path.iterdir() if p.name == archive.name
    ]


# ── extraction: member-safety refusals ─────────────────────────────────────


def test_a_duplicate_member_name_is_refused(tmp_path: Path):
    """A ZIP with two entries sharing the exact same member name is
    refused rather than silently keeping the last one written.

    Breaks if the `len(names) != len(set(names))` duplicate check is
    dropped.
    """

    archive = tmp_path / "dup.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.whl", b"AAAA")
        zf.writestr("a.whl", b"BBBB")

    with pytest.raises(ExtractionError, match="duplicate"):
        extract_verified_bundle(
            archive, tmp_path / "out", _manifest_for({"a.whl": b"AAAA"})
        )


def test_an_absolute_path_member_is_refused(tmp_path: Path):
    """A member name that is an absolute path (e.g. `/etc/passwd`) is
    refused.

    Breaks if the `name.startswith("/")`/`Path(name).is_absolute()` check
    is dropped, which would let a member attempt to write outside the
    staging directory via an absolute target.
    """

    archive = _make_zip(tmp_path, {"/etc/passwd": b"x"})

    with pytest.raises(ExtractionError, match="absolute path"):
        extract_verified_bundle(
            archive, tmp_path / "out", _manifest_for({"/etc/passwd": b"x"})
        )


def test_a_traversal_member_is_refused(tmp_path: Path):
    """A member name containing a `..` path segment is refused.

    Breaks if the `".." in Path(name).parts` check is dropped, which
    would let a member escape the staging directory via a relative
    traversal segment.
    """

    archive = _make_zip(tmp_path, {"../evil": b"x"})

    with pytest.raises(ExtractionError, match="traversal"):
        extract_verified_bundle(
            archive, tmp_path / "out", _manifest_for({"../evil": b"x"})
        )


def test_a_symlink_member_is_refused(tmp_path: Path):
    """A ZIP member whose external attributes declare it a symlink is
    refused, regardless of what its declared content bytes are.

    Breaks if the `stat.S_ISLNK(mode)` check (derived from
    `info.external_attr >> 16`) is dropped, which would let a symlink
    member be written as a regular file containing its link target text,
    or interpreted as a real symlink by a later consumer.
    """

    archive_path = tmp_path / "link.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        zf.writestr(info, "target")

    with pytest.raises(ExtractionError, match="symlink"):
        extract_verified_bundle(
            archive_path, tmp_path / "out", _manifest_for({"link": b"target"})
        )


def test_case_colliding_members_are_refused(tmp_path: Path):
    """Two members differing only in case (`A.whl` vs `a.whl`) are
    refused — a case-insensitive filesystem would silently collapse them
    into one file, disagreeing with what the manifest verified.

    Breaks if the `seen_lower` case-insensitive collision check is
    dropped.
    """

    archive = _make_zip(tmp_path, {"A.whl": b"A", "a.whl": b"a"})

    with pytest.raises(ExtractionError, match="collide case-insensitively"):
        extract_verified_bundle(
            archive, tmp_path / "out", _manifest_for({"A.whl": b"A", "a.whl": b"a"})
        )


def test_resolved_target_aliasing_is_refused(tmp_path: Path):
    """`a.whl` and `./a.whl` are different literal member names but
    resolve to the SAME filesystem target under `staging_dir` — refused
    rather than letting the second silently overwrite the first.

    Breaks if the `seen_resolved` resolved-path collision check is
    dropped.
    """

    archive_path = tmp_path / "alias.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("a.whl", "AAAA")
        zf.writestr("./a.whl", "BBBB")
    manifest = _manifest_for({"a.whl": b"AAAA", "./a.whl": b"BBBB"})

    with pytest.raises(ExtractionError, match="SAME target path"):
        extract_verified_bundle(archive_path, tmp_path / "out", manifest)


def test_an_oversized_member_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A member whose declared size exceeds `MAX_MEMBER_BYTES` is refused
    before any bytes are extracted.

    Breaks if the `declared_size > MAX_MEMBER_BYTES` check is dropped.
    """

    monkeypatch.setattr(BUNDLE_ENVELOPE, "MAX_MEMBER_BYTES", 3)
    archive = _make_zip(tmp_path, {"big.whl": b"AAAAAA"})

    with pytest.raises(ExtractionError, match="cap"):
        extract_verified_bundle(
            archive, tmp_path / "out", _manifest_for({"big.whl": b"AAAAAA"})
        )


def test_an_unlisted_member_is_refused(tmp_path: Path):
    """An archive member whose name the verified manifest does not
    mention is refused, even alongside other members that ARE listed.

    Breaks if the `name not in expected_members` check is dropped, which
    would let an archive smuggle in extra, unverified content.
    """

    archive = _make_zip(tmp_path, {"a.whl": b"AAAA", "sneaky.sh": b"#!/bin/sh\n"})

    with pytest.raises(ExtractionError, match="not named in the verified"):
        extract_verified_bundle(
            archive, tmp_path / "out", _manifest_for({"a.whl": b"AAAA"})
        )


def test_a_manifest_expected_member_missing_from_the_archive_is_refused(
    tmp_path: Path,
):
    """A member the manifest declares but the archive does not actually
    contain is refused, rather than silently extracting only the members
    that happen to be present.

    Breaks if the `for expected_name in expected_members: if expected_name
    not in names` completeness check is dropped.
    """

    archive = _make_zip(tmp_path, {"a.whl": b"AAAA"})
    manifest = _manifest_for({"a.whl": b"AAAA"})
    manifest["members"]["missing.whl"] = {
        "sha256": "c" * 64,
        "size": 10,
        "package": "dotmac-kernel",
    }

    with pytest.raises(ExtractionError, match="does not contain"):
        extract_verified_bundle(archive, tmp_path / "out", manifest)


def test_a_corrupted_archive_is_refused_not_a_raw_badzipfile(tmp_path: Path):
    """Bytes that are not a valid ZIP archive at all are refused as
    `ExtractionError`, not left as a raw `zipfile.BadZipFile`.

    Breaks if the try/except wrapping `zipfile.ZipFile(archive_path)` is
    dropped, which would let `BadZipFile` propagate uncaught.
    """

    archive = tmp_path / "corrupt.zip"
    archive.write_bytes(b"this is not a zip file at all")
    manifest = _manifest_for({"a.whl": b"AAAA"})

    with pytest.raises(ExtractionError, match="cannot open"):
        extract_verified_bundle(archive, tmp_path / "out", manifest)


def test_the_aggregate_member_count_cap_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """More than `MAX_MEMBER_COUNT` members in one archive is refused,
    independent of any single member's own size.

    Breaks if the `len(infos) > MAX_MEMBER_COUNT` check is dropped.
    """

    monkeypatch.setattr(BUNDLE_ENVELOPE, "MAX_MEMBER_COUNT", 3)
    members = {f"f{i}.whl": b"X" for i in range(5)}
    archive = _make_zip(tmp_path, members)

    with pytest.raises(ExtractionError, match="member cap"):
        extract_verified_bundle(archive, tmp_path / "out", _manifest_for(members))


def test_the_aggregate_total_size_cap_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The sum of every member's declared size exceeding
    `MAX_TOTAL_UNCOMPRESSED_BYTES` is refused, even though each individual
    member is small and legal on its own.

    Breaks if the running `total_declared_size > MAX_TOTAL_UNCOMPRESSED_BYTES`
    check is dropped.
    """

    monkeypatch.setattr(BUNDLE_ENVELOPE, "MAX_TOTAL_UNCOMPRESSED_BYTES", 5)
    members = {"a.whl": b"AAAA", "b.whl": b"BBBB"}
    archive = _make_zip(tmp_path, members)

    with pytest.raises(ExtractionError, match="aggregate"):
        extract_verified_bundle(archive, tmp_path / "out", _manifest_for(members))


def test_the_compression_ratio_cap_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A member whose declared uncompressed size divided by its compressed
    size exceeds `MAX_COMPRESSION_RATIO` is refused as a suspected zip
    bomb, even though its declared size alone is under every other cap.

    Breaks if the `ratio > MAX_COMPRESSION_RATIO` check (or the
    `info.compress_size > 0` guard around it) is dropped.
    """

    monkeypatch.setattr(BUNDLE_ENVELOPE, "MAX_COMPRESSION_RATIO", 2)
    payload = b"A" * 100_000
    archive_path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bomb.whl", payload)
    manifest = _manifest_for({"bomb.whl": payload})

    with pytest.raises(ExtractionError, match="compression ratio"):
        extract_verified_bundle(archive_path, tmp_path / "out", manifest)


# ── local PEP 503 index: fixtures ─────────────────────────────────────────

PLAN_DIGEST = "d" * 64


def _bli_scenario(
    tmp_path: Path, plan_digest: str, files_by_package: dict[str, dict[str, bytes]]
) -> tuple:
    """Builds a fully self-consistent `(ExpectedArtifactSet, bundle_manifest,
    extracted_dir)` triple: every file is actually written under a fresh
    `extracted` directory, and both the plan and the manifest agree on its
    package, filename, and (correct) sha256. Individual tests mutate one of
    the three returned values to plant exactly one disagreement."""

    extracted = tmp_path / "extracted"
    extracted.mkdir(exist_ok=True)
    artifacts = []
    members: dict = {}
    for pkg, files in files_by_package.items():
        for filename, data in files.items():
            target = extracted / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            digest = sha256_hex(data)
            artifacts.append(
                ExpectedArtifact(
                    package_normalised_name=pkg, filename=filename, sha256=digest
                )
            )
            members[filename] = {"sha256": digest, "size": len(data), "package": pkg}
    expected = ExpectedArtifactSet(plan_digest=plan_digest, artifacts=tuple(artifacts))
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "plan_digest": plan_digest,
        "archive_sha256": "b" * 64,
        "members": members,
        "run": dict(_VALID_MANIFEST_RUN),
    }
    return expected, manifest, extracted


# ── local PEP 503 index: the mandatory accept-direction control ──────────


def test_build_local_index_accepts_a_clean_multi_package_plan(tmp_path: Path):
    """MANDATORY ACCEPT-DIRECTION CONTROL: a plan, a manifest, and an
    extracted directory that all genuinely agree, across TWO packages,
    publishes successfully. Every other `build_local_index` test below
    asserts a refusal; without this one, an implementation that refused
    every reconciliation unconditionally would pass all of them for the
    wrong reason.

    Breaks if any reconciliation step is tightened into an unconditional
    refusal, or if the package map `build_local_index` builds itself stops
    grouping correctly-agreeing artifacts by package.
    """

    expected, manifest, extracted = _bli_scenario(
        tmp_path,
        PLAN_DIGEST,
        {"aaa-pkg": {"a.whl": b"AAAA"}, "bbb-pkg": {"b.whl": b"BBBBBB"}},
    )
    index_root = tmp_path / "index"

    build_local_index(index_root, expected, manifest, extracted)

    assert (index_root / "simple" / "aaa-pkg" / "a.whl").read_bytes() == b"AAAA"
    assert (index_root / "simple" / "bbb-pkg" / "b.whl").read_bytes() == b"BBBBBB"
    root_html = (index_root / "simple" / "index.html").read_text(encoding="utf-8")
    assert "aaa-pkg" in root_html
    assert "bbb-pkg" in root_html


# ── local PEP 503 index: destination and plan-shape refusals ─────────────


def test_build_local_index_refuses_a_pre_existing_destination(tmp_path: Path):
    """Ported from ERP's `test_build_local_index_refuses_a_pre_existing_
    destination`: `index_root` already existing is refused before any of
    the plan/manifest arguments are ever examined.

    Breaks if the `index_root.exists()` guard at the top of
    `build_local_index` is dropped.
    """

    index_root = tmp_path / "index"
    index_root.mkdir()
    expected = ExpectedArtifactSet(plan_digest=PLAN_DIGEST, artifacts=())
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "plan_digest": PLAN_DIGEST,
        "archive_sha256": "b" * 64,
        "members": {},
        "run": dict(_VALID_MANIFEST_RUN),
    }

    with pytest.raises(BundleVerificationError, match="already exists"):
        build_local_index(index_root, expected, manifest, tmp_path / "extracted")


def test_build_local_index_refuses_an_empty_plan(tmp_path: Path):
    """New plant: an `ExpectedArtifactSet` naming no artifacts is refused
    rather than silently publishing an empty index.

    Breaks if the `not expected.artifacts` guard is dropped.
    """

    expected = ExpectedArtifactSet(plan_digest=PLAN_DIGEST, artifacts=())
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "plan_digest": PLAN_DIGEST,
        "archive_sha256": "b" * 64,
        "members": {},
        "run": dict(_VALID_MANIFEST_RUN),
    }

    with pytest.raises(BundleVerificationError, match="no artifacts"):
        build_local_index(
            tmp_path / "index", expected, manifest, tmp_path / "extracted"
        )


def test_build_local_index_refuses_a_manifest_bound_to_a_different_plan(
    tmp_path: Path,
):
    """New plant: the manifest's own `plan_digest` disagreeing with
    `expected.plan_digest` is refused — a manifest produced for a
    different plan must never be published under this plan's index.

    Breaks if the `bundle_manifest.get("plan_digest") != expected.plan_digest`
    check is dropped.
    """

    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"aaa-pkg": {"a.whl": b"AAAA"}}
    )
    manifest["plan_digest"] = "e" * 64

    with pytest.raises(BundleVerificationError, match="plan_digest"):
        build_local_index(tmp_path / "index", expected, manifest, extracted)


# ── local PEP 503 index: the required reconciliation plants ──────────────


def test_build_local_index_refuses_a_missing_artifact(tmp_path: Path):
    """DESIGNED BREAK CONDITION: the plan names `a.whl` as expected, but
    the manifest is missing it — refused in the "expected but absent"
    direction of the plan<->manifest reconciliation. A second, unrelated
    artifact (`b.whl`) is kept in both the plan and the manifest so the
    manifest's `members` dict stays non-empty; an empty manifest would be
    refused earlier, by the unrelated "carries no members" guard, for the
    wrong reason.

    Breaks if the `missing_from_manifest` check is dropped.
    """

    expected, manifest, extracted = _bli_scenario(
        tmp_path,
        PLAN_DIGEST,
        {"aaa-pkg": {"a.whl": b"AAAA"}, "bbb-pkg": {"b.whl": b"BBBBBB"}},
    )
    del manifest["members"]["a.whl"]

    with pytest.raises(BundleVerificationError, match="does not contain"):
        build_local_index(tmp_path / "index", expected, manifest, extracted)


def test_build_local_index_refuses_an_extra_artifact(tmp_path: Path):
    """DESIGNED BREAK CONDITION: the manifest (and the extracted directory)
    carry a file the plan never named — refused in the "present but
    unexpected" direction of the plan<->manifest reconciliation.

    Breaks if the `extra_in_manifest` check is dropped.
    """

    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"aaa-pkg": {"a.whl": b"AAAA"}}
    )
    (extracted / "sneaky.whl").write_bytes(b"SNEAK")
    manifest["members"]["sneaky.whl"] = {
        "sha256": sha256_hex(b"SNEAK"),
        "size": 5,
        "package": "aaa-pkg",
    }

    with pytest.raises(BundleVerificationError, match="does not expect"):
        build_local_index(tmp_path / "index", expected, manifest, extracted)


def test_build_local_index_refuses_a_wrong_package_association(tmp_path: Path):
    """DESIGNED BREAK CONDITION: the RIGHT filename, but the manifest
    associates it with a DIFFERENT package than the plan expects.

    Breaks if the per-filename `manifest_package != artifact.package_
    normalised_name` comparison is dropped.
    """

    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"aaa-pkg": {"a.whl": b"AAAA"}}
    )
    manifest["members"]["a.whl"]["package"] = "bbb-pkg"

    with pytest.raises(BundleVerificationError, match="is associated with package"):
        build_local_index(tmp_path / "index", expected, manifest, extracted)


def test_build_local_index_refuses_a_wrong_hash_between_plan_and_manifest(
    tmp_path: Path,
):
    """DESIGNED BREAK CONDITION: the manifest's recorded sha256 for `a.whl`
    disagrees with the plan's expected sha256, even though both name the
    same file and package.

    Breaks if the per-filename `manifest_sha256 != artifact.sha256`
    comparison is dropped.
    """

    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"aaa-pkg": {"a.whl": b"AAAA"}}
    )
    manifest["members"]["a.whl"]["sha256"] = sha256_hex(b"DIFFERENT")

    with pytest.raises(
        BundleVerificationError, match="disagreeing with the plan's expected"
    ):
        build_local_index(tmp_path / "index", expected, manifest, extracted)


def test_build_local_index_refuses_a_duplicate_plan_entry(tmp_path: Path):
    """DESIGNED BREAK CONDITION: the plan names `a.whl` twice. Refused
    outright rather than silently deduplicated — an ambiguous plan must
    never be treated as though it agreed with itself.

    Breaks if the `artifact.filename in plan_by_filename` duplicate check
    is dropped.
    """

    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"aaa-pkg": {"a.whl": b"AAAA"}}
    )
    duplicated = ExpectedArtifactSet(
        plan_digest=PLAN_DIGEST, artifacts=expected.artifacts + expected.artifacts
    )

    with pytest.raises(BundleVerificationError, match="twice"):
        build_local_index(tmp_path / "index", duplicated, manifest, extracted)


def test_build_local_index_refuses_a_file_tampered_after_extraction(tmp_path: Path):
    """THE STEP-4 SENSITIVITY PROOF. The plan and the manifest agree
    exactly — both name `a.whl`'s ORIGINAL sha256. Only the bytes actually
    on disk changed, after extraction was verified, before
    `build_local_index` ran. Reconciliation steps 1-3 alone see no
    disagreement at all (the manifest's recorded hash never changed); only
    the rehash-against-current-bytes in step 4 reads what is actually on
    disk RIGHT NOW and catches this.

    Breaks if the rehash-and-compare loop is removed, or replaced with a
    comparison against the manifest's or the plan's already-recorded
    digest instead of `source_path.read_bytes()` computed fresh: the
    tampered file would then publish successfully under a digest that no
    longer describes its own bytes — exactly the gap the module docstring
    says ERP's original `build_local_index` left open.
    """

    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"aaa-pkg": {"a.whl": b"AAAA"}}
    )
    (extracted / "a.whl").write_bytes(b"TAMPERED")

    with pytest.raises(BundleVerificationError, match="changed after verification"):
        build_local_index(tmp_path / "index", expected, manifest, extracted)
    assert not (tmp_path / "index").exists()


# ── local PEP 503 index: symlink and nested-filename boundary plants ─────


def test_build_local_index_refuses_a_symlink_to_matching_bytes_outside_the_tree(
    tmp_path: Path,
):
    """THE ENUMERATION SENSITIVITY PROOF (live-link direction).

    An expected member is not a regular file at all: it is a symlink whose
    target lives OUTSIDE `extracted_dir` entirely, and whose target bytes
    are byte-IDENTICAL to what the plan expects. Before this fix,
    `Path.is_file()` followed the link, so reconciliation step 2's
    enumeration saw a normal file named `a.whl`; the plan<->manifest
    closure (step 1) never touches the filesystem at all so it was
    unaffected either way; and step 4's rehash read `source_path
    .read_bytes()`, which ALSO follows the link and got the same matching
    bytes — every check this function performs would have passed, and
    `build_local_index` would have gone on to copy bytes read from outside
    the verified tree.

    Breaks if the enumeration in reconciliation step 2 goes back to
    `Path.is_file()` (or any other predicate that resolves through a
    symlink) instead of `Path.lstat()` + `stat.S_ISREG`.
    """

    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"aaa-pkg": {"a.whl": b"AAAA"}}
    )
    outside_dir = tmp_path / "outside_the_verified_tree"
    outside_dir.mkdir()
    outside_target = outside_dir / "not_actually_a.whl"
    outside_target.write_bytes(b"AAAA")  # identical bytes to the plan's expectation
    (extracted / "a.whl").unlink()
    os.symlink(outside_target, extracted / "a.whl")

    # Prove the pre-fix claim directly: the rehash step, taken alone, agrees
    # with the plan — because `read_bytes()` follows the symlink too. If
    # enumeration did not refuse the link by kind, nothing downstream in
    # this function would ever catch it.
    assert sha256_hex((extracted / "a.whl").read_bytes()) == sha256_hex(b"AAAA")

    with pytest.raises(BundleVerificationError, match="not a regular file"):
        build_local_index(tmp_path / "index", expected, manifest, extracted)
    assert not (tmp_path / "index").exists()


def test_build_local_index_refuses_a_dangling_symlink_not_named_by_the_plan(
    tmp_path: Path,
):
    """THE ENUMERATION SENSITIVITY PROOF (dangling-link direction).

    A SECOND, unexpected entry sits in `extracted_dir` alongside the
    genuine, correctly-named `a.whl`: a dangling symlink the plan and the
    manifest never named. Before this fix, `Path.is_file()` returns
    `False` for a dangling symlink, so it was silently OMITTED from
    `extracted_names` rather than counted — which means the "extracted
    directory contains an entry the manifest does not name" check never
    saw it at all, not merely failed to flag it. `build_local_index` would
    have published successfully with the rogue link still sitting in the
    tree.

    Breaks if enumeration goes back to a predicate that omits an entry it
    cannot resolve, instead of refusing every non-regular, non-directory
    entry by name regardless of whether its target exists.
    """

    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"aaa-pkg": {"a.whl": b"AAAA"}}
    )
    os.symlink(extracted / "does_not_exist.whl", extracted / "sneaky_dangling.whl")

    with pytest.raises(BundleVerificationError, match="not a regular file"):
        build_local_index(tmp_path / "index", expected, manifest, extracted)
    assert not (tmp_path / "index").exists()


def test_build_local_index_refuses_a_nested_filename_before_reading_its_bytes(
    tmp_path: Path,
):
    """THE VALIDATE-BEFORE-READ SENSITIVITY PROOF.

    The plan names `nested/evil.whl` (a shape `build_local_index` must
    refuse as an unsafe filename), and the plan's recorded hash matches
    what was ORIGINALLY written to disk — but the file's bytes are then
    corrupted, deliberately, AFTER `_bli_scenario` wrote them. If filename
    validation ran only where it used to (step 5, immediately before
    joining onto the staging directory), this function would first reach
    step 2's enumeration and step 4's rehash, read the corrupted bytes at
    `extracted/nested/evil.whl`, and fail with the step-4 tamper message
    ("changed after verification") — never reaching the bare-filename
    refusal at all. Validating the filename's shape FIRST, while
    `plan_by_filename` is built, means this function refuses the name
    before it is ever used to read a file — proven here by asserting the
    error is the bare-filename refusal, not the tamper refusal a read
    would have produced.

    Breaks if the bare-filename shape check moves back to (or is only
    present in) reconciliation step 5, after step 2/step 4 have already
    read the file.
    """

    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"aaa-pkg": {"nested/evil.whl": b"AAAA"}}
    )
    (extracted / "nested" / "evil.whl").write_bytes(b"CORRUPTED-AFTER-WRITE")

    with pytest.raises(BundleVerificationError, match="not a safe bare filename"):
        build_local_index(tmp_path / "index", expected, manifest, extracted)
    assert not (tmp_path / "index").exists()


# ── local PEP 503 index: ported from ERP, adapted to the new signature ───


def test_build_local_index_refuses_an_unnormalised_package_name(tmp_path: Path):
    """Ported/adapted from ERP's `test_build_local_index_refuses_an_
    unnormalised_package_key`: `expected.artifacts[*].package_normalised_
    name` must already be PEP-503-normalised — `build_local_index` refuses
    to normalise it silently on the caller's behalf.

    Breaks if the `canonical_name != artifact.package_normalised_name`
    check is dropped.
    """

    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"Dotmac_Kernel": {"wheel.whl": b"wheel bytes"}}
    )

    with pytest.raises(BundleVerificationError, match="not PEP-503-normalised"):
        build_local_index(tmp_path / "index", expected, manifest, extracted)
    assert not (tmp_path / "index").exists()


def test_build_local_index_refuses_an_invalid_pep503_name_rather_than_crashing(
    tmp_path: Path,
):
    """Ported/adapted from ERP's `test_build_local_index_refuses_an_
    invalid_pep503_key_rather_than_crashing`: a name that only NORMALISES
    to something invalid (an edge separator, `-dotmac-kernel-`) is refused
    with `BundleVerificationError`, not the raw `ValueError`
    `_normalise_pep503_name` raises.

    Breaks if `_normalise_pep503_name`'s edge-separator check is dropped,
    or if `build_local_index` stops translating its `ValueError`.
    """

    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"-dotmac-kernel-": {"wheel.whl": b"wheel bytes"}}
    )

    with pytest.raises(BundleVerificationError, match="not a valid PEP 503 name"):
        build_local_index(tmp_path / "index", expected, manifest, extracted)


def test_build_local_index_refuses_a_package_name_that_is_a_filesystem_path(
    tmp_path: Path,
):
    """Ported/adapted from ERP's `test_build_local_index_refuses_a_
    package_key_that_is_a_filesystem_path`: a package name containing a
    path separator is refused by `_normalise_pep503_name`'s charset check
    before it ever reaches `Path.__truediv__` — an absolute right-hand
    operand would otherwise REPLACE the left side entirely and escape the
    staging directory.

    Breaks if the input-charset check in `_normalise_pep503_name` is
    dropped.
    """

    escape_target = tmp_path / "escaped"
    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {str(escape_target): {"wheel.whl": b"wheel bytes"}}
    )

    with pytest.raises(BundleVerificationError, match="not a valid PEP 503 name"):
        build_local_index(tmp_path / "index", expected, manifest, extracted)
    assert not escape_target.exists()
    assert not (tmp_path / "index").exists()


def test_build_local_index_refuses_a_filename_containing_a_path_separator(
    tmp_path: Path,
):
    """Ported/adapted from ERP's `test_build_local_index_refuses_a_
    filename_containing_a_path_separator`: a plan filename containing `/`
    is refused before it is ever joined onto `pkg_dir`. Refused earlier
    still, now: the same bare-filename shape check runs while
    `plan_by_filename` is built, before enumeration or the rehash ever
    reads anything named by it (see
    `test_build_local_index_refuses_a_nested_filename_before_reading_its_bytes`
    for the test that pins that ordering specifically).

    Breaks if the filename-safety check (`"/" in filename` etc.) is
    dropped.
    """

    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"aaa-pkg": {"evil/isolated.whl": b"wheel bytes"}}
    )

    with pytest.raises(BundleVerificationError, match="not a safe bare filename"):
        build_local_index(tmp_path / "index", expected, manifest, extracted)


def test_build_local_index_escapes_html_metacharacters_in_anchors(tmp_path: Path):
    """Ported/adapted from ERP's `test_build_local_index_escapes_html_
    metacharacters_in_anchors`: a filename carrying `<`, `>`, `&`, `"`
    (but no `/`, which is refused outright by the separator check above)
    must not inject markup into the resolver-facing package index page.

    Breaks if `html.escape` is dropped from the anchor-text/href
    construction.
    """

    filename = 'inject"><script>alert(1)<script>.whl'
    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"aaa-pkg": {filename: b"wheel bytes"}}
    )

    build_local_index(tmp_path / "index", expected, manifest, extracted)

    html_text = (tmp_path / "index" / "simple" / "aaa-pkg" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "<script>" not in html_text
    assert "&lt;script&gt;" in html_text


def test_build_local_index_url_encodes_a_hash_character_in_the_href(tmp_path: Path):
    """Ported/adapted from ERP's `test_build_local_index_url_encodes_a_
    hash_character_in_the_href`: `html.escape` does not touch `#`, so a
    filename like `pkg#x.whl` must be URL-quoted before it reaches the
    href, or a resolver following the link would request `pkg`, not the
    staged file.

    Breaks if the href goes back to being built from bare
    `html.escape(filename, ...)` instead of
    `urllib.parse.quote(filename, safe="")`.
    """

    filename = "pkg#x.whl"
    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"aaa-pkg": {filename: b"wheel bytes"}}
    )

    build_local_index(tmp_path / "index", expected, manifest, extracted)

    html_text = (tmp_path / "index" / "simple" / "aaa-pkg" / "index.html").read_text(
        encoding="utf-8"
    )
    href_path_segment = html_text.split('href="', 1)[1].split("#sha256=", 1)[0]
    assert href_path_segment == urllib.parse.quote(filename, safe="")
    assert (tmp_path / "index" / "simple" / "aaa-pkg" / filename).is_file()


def test_build_local_index_url_encodes_a_literal_percent_in_the_href(tmp_path: Path):
    """Ported/adapted from ERP's `test_build_local_index_url_encodes_a_
    literal_percent_in_the_href`: a filename that already looks
    percent-encoded must have its own `%` re-encoded (`%` -> `%25`), or a
    client decoding the href once would read the embedded sequence back as
    a literal traversal spelling.

    Breaks if the filename is not quoted before reaching the href.
    """

    filename = "%2e%2e-not-actually-traversal.whl"
    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"aaa-pkg": {filename: b"wheel bytes"}}
    )

    build_local_index(tmp_path / "index", expected, manifest, extracted)

    html_text = (tmp_path / "index" / "simple" / "aaa-pkg" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "%252e%252e" in html_text
    assert f'href="{filename}' not in html_text


def test_build_local_index_refuses_an_overlong_charset_valid_package_name(
    tmp_path: Path,
):
    """Ported/adapted from ERP's `test_build_local_index_refuses_an_
    overlong_charset_valid_package_name`: a name built entirely from
    characters `_normalise_pep503_name` permits, but too long for the
    filesystem to accept as one path component, is never rejected by the
    charset/shape checks — only the filesystem itself refuses it, with a
    raw `OSError` (`ENAMETOOLONG`), which must be translated.

    Breaks if the `try/except OSError` around `pkg_dir.mkdir(...)` is
    dropped.
    """

    overlong_name = "a" * 4096
    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {overlong_name: {"wheel.whl": b"wheel bytes"}}
    )

    with pytest.raises(
        BundleVerificationError, match="cannot create package directory"
    ):
        build_local_index(tmp_path / "index", expected, manifest, extracted)
    assert not (tmp_path / "index").exists()


def test_build_local_index_refuses_when_its_parent_directory_cannot_be_created(
    tmp_path: Path,
):
    """Ported/adapted from ERP's `test_build_local_index_refuses_when_its_
    parent_directory_cannot_be_created`: `index_root.parent.mkdir(...)`
    failing (here, a path component that is actually a file) is
    translated into `BundleVerificationError`, not left as a raw
    `OSError`.

    Breaks if the try/except wrapping `index_root.parent.mkdir(...)` is
    removed.
    """

    blocking_file = tmp_path / "not-a-directory"
    blocking_file.write_bytes(b"this is a file, not a directory")
    index_root = blocking_file / "nested" / "index"
    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"aaa-pkg": {"wheel.whl": b"wheel bytes"}}
    )

    with pytest.raises(BundleVerificationError, match="cannot create parent directory"):
        build_local_index(index_root, expected, manifest, extracted)


def test_build_local_index_is_atomic_on_failure_during_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Adapted from ERP's `test_build_local_index_is_atomic_on_failure_
    nothing_is_published` AND `test_build_local_index_refuses_when_a_
    package_index_cannot_be_written`. ERP's original scenario for the
    first (a caller-supplied `source_path` that does not exist) cannot be
    expressed under the new signature at all: every file this function
    copies now comes from `extracted_dir`, whose membership is already
    proven, in both directions, by reconciliation step 2 before any
    staging work begins — there is no longer a way to reach the staging
    loop with a source file that is missing. This test re-proves the same
    atomicity property against the failure mode that DOES still exist
    under the new signature: an `OSError` raised mid-staging, after the
    first of two packages has already been written into the staging
    directory.

    Breaks if the `except BaseException: shutil.rmtree(staging_root, ...);
    raise` cleanup around the staging loop is dropped, which would leave a
    partially-staged directory (or worse, a partially-published
    `index_root`) on disk after the raise.
    """

    expected, manifest, extracted = _bli_scenario(
        tmp_path,
        PLAN_DIGEST,
        {"aaa-pkg": {"a.whl": b"AAAA"}, "bbb-pkg": {"b.whl": b"BBBBBB"}},
    )
    real_write_text = BUNDLE_ENVELOPE.Path.write_text

    def _maybe_raise(self, *args, **kwargs):
        if self.parent.name == "bbb-pkg":
            raise OSError("simulated: cannot write package index")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(BUNDLE_ENVELOPE.Path, "write_text", _maybe_raise)
    index_root = tmp_path / "index"

    with pytest.raises(BundleVerificationError, match="cannot write package index"):
        build_local_index(index_root, expected, manifest, extracted)

    assert not index_root.exists()
    leftover = [p.name for p in tmp_path.iterdir() if p.name != "extracted"]
    assert leftover == []


def test_build_local_index_refuses_when_the_staged_root_cannot_be_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Ported/adapted from ERP's `test_build_local_index_refuses_when_the_
    staged_root_cannot_be_listed`: listing the staged index root (to build
    the top-level `index.html`) is translated into
    `BundleVerificationError`, not left as a raw `OSError`.

    Breaks if the try/except wrapping `root_dir.iterdir()` is removed.
    """

    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"aaa-pkg": {"wheel.whl": b"wheel bytes"}}
    )
    real_iterdir = BUNDLE_ENVELOPE.Path.iterdir

    def _maybe_raise(self):
        if self.name == "simple":
            raise OSError("simulated: cannot list staged index root")
        return real_iterdir(self)

    monkeypatch.setattr(BUNDLE_ENVELOPE.Path, "iterdir", _maybe_raise)

    with pytest.raises(BundleVerificationError, match="cannot list staged index root"):
        build_local_index(tmp_path / "index", expected, manifest, extracted)


def test_build_local_index_refuses_when_the_root_index_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Ported/adapted from ERP's `test_build_local_index_refuses_when_the_
    root_index_cannot_be_written`: writing the top-level `index.html` (the
    LAST write of the staging phase) is translated into
    `BundleVerificationError`, not left as a raw `OSError`.

    Breaks if the try/except wrapping the root-level
    `(root_dir / "index.html").write_text(...)` call is removed.
    """

    expected, manifest, extracted = _bli_scenario(
        tmp_path, PLAN_DIGEST, {"aaa-pkg": {"wheel.whl": b"wheel bytes"}}
    )
    real_write_text = BUNDLE_ENVELOPE.Path.write_text

    def _maybe_raise(self, *args, **kwargs):
        if self.parent.name == "simple":
            raise OSError("simulated: cannot write root index")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(BUNDLE_ENVELOPE.Path, "write_text", _maybe_raise)

    with pytest.raises(BundleVerificationError, match="cannot write root index"):
        build_local_index(tmp_path / "index", expected, manifest, extracted)


def test_normalise_pep503_name_refuses_a_non_string_input_without_crashing():
    """Adapted from ERP's `test_build_local_index_refuses_a_non_string_
    package_key`. Under the new signature, a non-string
    `package_normalised_name` cannot reach this check through
    `build_local_index` itself without first disagreeing with the
    manifest's (always-string) `package` field — caught earlier as a
    wrong-package-association refusal, since `build_local_index` no
    longer accepts a raw caller-assembled dict whose keys could be
    anything. The property this test protects — a non-string input is
    translated to `ValueError`, never left as a raw `TypeError` — still
    lives entirely inside `_normalise_pep503_name`, so it is exercised
    directly, the same way this file already tests
    `_refuse_malformed_manifest_run` directly.

    Breaks if the `isinstance(name, str)` guard is dropped:
    `re.Pattern.match` raises a raw `TypeError` on a non-str/bytes-like
    object, which `pytest.raises(ValueError)` below does not catch.
    """

    with pytest.raises(ValueError, match="not a valid distribution name"):
        _normalise_pep503_name(123)  # type: ignore[arg-type]


# ── canonical bundle manifest: fixtures ───────────────────────────────────


def _cbm_scenario(
    tmp_path: Path, files_by_package: dict[str, dict[str, bytes]]
) -> tuple:
    """Builds a fully self-consistent `(ExpectedArtifactSet, acquired_files,
    archive_path, run)` quadruple: every file is actually written under a
    fresh `acquired` directory AND packed into a real ZIP at `archive_path`,
    and the plan agrees with both on package, filename, and (correct)
    sha256. Individual tests mutate one of the four returned values, or the
    archive/acquired files on disk, to plant exactly one disagreement —
    the same shape as `_bli_scenario` above, for the producer side instead
    of the consumer side."""

    acquired_dir = tmp_path / "acquired"
    acquired_dir.mkdir(exist_ok=True)
    artifacts = []
    archive_members: dict[str, bytes] = {}
    acquired_files: dict[str, Path] = {}
    for pkg, files in files_by_package.items():
        for filename, data in files.items():
            path = acquired_dir / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            digest = sha256_hex(data)
            artifacts.append(
                ExpectedArtifact(
                    package_normalised_name=pkg, filename=filename, sha256=digest
                )
            )
            acquired_files[filename] = path
            archive_members[filename] = data
    expected = ExpectedArtifactSet(plan_digest=PLAN_DIGEST, artifacts=tuple(artifacts))
    archive_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, data in archive_members.items():
            zf.writestr(filename, data)
    run = dict(_VALID_MANIFEST_RUN)
    return expected, acquired_files, archive_path, run


# ── canonical bundle manifest: the mandatory accept-direction control ─────


def test_create_bundle_manifest_accepts_a_clean_multi_file_plan(tmp_path: Path):
    """MANDATORY ACCEPT-DIRECTION CONTROL: a plan, acquired files, and an
    archive that all genuinely agree, across TWO packages, produces a
    manifest whose every field matches the real bytes on disk. Every other
    `create_bundle_manifest` test below asserts a refusal; without this
    one, an implementation that refused every input unconditionally would
    pass all of them for the wrong reason.

    Breaks if any reconciliation step is tightened into an unconditional
    refusal, or if a computed field (size, sha256, archive_sha256) stops
    matching the real bytes.
    """

    expected, acquired_files, archive_path, run = _cbm_scenario(
        tmp_path,
        {"aaa-pkg": {"a.whl": b"AAAA"}, "bbb-pkg": {"b.whl": b"BBBBBB"}},
    )

    manifest = create_bundle_manifest(
        expected=expected,
        acquired_files=acquired_files,
        archive_path=archive_path,
        run=run,
    )

    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["plan_digest"] == PLAN_DIGEST
    assert manifest["archive_sha256"] == sha256_hex(archive_path.read_bytes())
    assert manifest["members"]["a.whl"] == {
        "sha256": sha256_hex(b"AAAA"),
        "size": 4,
        "package": "aaa-pkg",
    }
    assert manifest["members"]["b.whl"] == {
        "sha256": sha256_hex(b"BBBBBB"),
        "size": 6,
        "package": "bbb-pkg",
    }
    assert manifest["run"]["repository_full_name"] == run["repository_full_name"]


def test_create_bundle_manifest_output_satisfies_the_shape_check(tmp_path: Path):
    """Round-trip: this function's own output must be exactly what
    `_refuse_malformed_bundle_manifest_shape` — the shape check the
    verifier side already relies on — accepts. `create_bundle_manifest`
    calls this same check internally before returning, so this test also
    guards against that internal call being removed.

    Breaks if `create_bundle_manifest` ever omits `schema_version`,
    `plan_digest`, `archive_sha256`, or `run`, or emits one in a shape
    `_refuse_malformed_bundle_manifest_shape` refuses (e.g. a `run` block
    missing a required field).
    """

    expected, acquired_files, archive_path, run = _cbm_scenario(
        tmp_path, {"aaa-pkg": {"a.whl": b"AAAA"}}
    )

    manifest = create_bundle_manifest(
        expected=expected,
        acquired_files=acquired_files,
        archive_path=archive_path,
        run=run,
    )

    _refuse_malformed_bundle_manifest_shape(manifest)  # must not raise


# ── canonical bundle manifest: three-way closure refusals ─────────────────


def test_create_bundle_manifest_refuses_an_expected_artifact_not_acquired(
    tmp_path: Path,
):
    """Plan <-> acquired closure, direction one: the plan expects `a.whl`,
    but it was never acquired.

    Breaks if the `missing = expected_names - acquired_names` check is
    dropped.
    """

    expected, acquired_files, archive_path, run = _cbm_scenario(
        tmp_path, {"aaa-pkg": {"a.whl": b"AAAA"}}
    )
    del acquired_files["a.whl"]

    with pytest.raises(BundleVerificationError, match="were not acquired"):
        create_bundle_manifest(
            expected=expected,
            acquired_files=acquired_files,
            archive_path=archive_path,
            run=run,
        )


def test_create_bundle_manifest_refuses_an_acquired_file_not_expected(
    tmp_path: Path,
):
    """Plan <-> acquired closure, direction two: a file was acquired that
    the plan never named.

    Breaks if the `extra = acquired_names - expected_names` check is
    dropped.
    """

    expected, acquired_files, archive_path, run = _cbm_scenario(
        tmp_path, {"aaa-pkg": {"a.whl": b"AAAA"}}
    )
    extra_path = archive_path.parent / "acquired" / "extra.whl"
    extra_path.write_bytes(b"EXTRA")
    acquired_files["extra.whl"] = extra_path

    with pytest.raises(BundleVerificationError, match="does not expect them"):
        create_bundle_manifest(
            expected=expected,
            acquired_files=acquired_files,
            archive_path=archive_path,
            run=run,
        )


def test_create_bundle_manifest_refuses_when_the_archive_has_an_unlisted_member(
    tmp_path: Path,
):
    """Acquired <-> archive closure, direction one: plan and acquired files
    agree with each other, but the archive contains one member neither of
    them names. A manifest must never claim closure NARROWER than what the
    archive actually contains.

    Breaks if the `extra_in_archive = archive_names - set(members)` check
    is dropped.
    """

    expected, acquired_files, archive_path, run = _cbm_scenario(
        tmp_path, {"aaa-pkg": {"a.whl": b"AAAA"}}
    )
    with zipfile.ZipFile(archive_path, "a") as zf:
        zf.writestr("sneaky.whl", b"SNEAKY")

    with pytest.raises(BundleVerificationError, match="which the plan does not name"):
        create_bundle_manifest(
            expected=expected,
            acquired_files=acquired_files,
            archive_path=archive_path,
            run=run,
        )


def test_create_bundle_manifest_refuses_when_the_archive_is_missing_a_member(
    tmp_path: Path,
):
    """Acquired <-> archive closure, direction two: the plan and the
    acquired files both name `extra.whl`, but the archive was built
    without it.

    Breaks if the `missing_from_archive = set(members) - archive_names`
    check is dropped.
    """

    expected, acquired_files, archive_path, run = _cbm_scenario(
        tmp_path, {"aaa-pkg": {"a.whl": b"AAAA", "extra.whl": b"BBBB"}}
    )
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.whl", b"AAAA")

    with pytest.raises(BundleVerificationError, match="does not contain"):
        create_bundle_manifest(
            expected=expected,
            acquired_files=acquired_files,
            archive_path=archive_path,
            run=run,
        )


# ── canonical bundle manifest: compute, never accept ───────────────────────


def test_create_bundle_manifest_refuses_when_acquired_bytes_disagree_with_the_plan(
    tmp_path: Path,
):
    """Proves the constructor COMPUTES the member hash from the real
    acquired bytes rather than accepting the plan's declared `sha256` on
    faith: the file on disk is tampered with AFTER the plan was built, so
    its real digest no longer matches `ExpectedArtifact.sha256`.

    Breaks if `actual_sha256` stops being computed via
    `sha256_hex(path.read_bytes())`, or if the comparison against
    `artifact.sha256` is dropped — either change would let a caller-
    declared hash reach the manifest without ever being checked against
    the real bytes.
    """

    expected, acquired_files, archive_path, run = _cbm_scenario(
        tmp_path, {"aaa-pkg": {"a.whl": b"AAAA"}}
    )
    (archive_path.parent / "acquired" / "a.whl").write_bytes(b"TAMPERED-AFTER-PLANNING")

    with pytest.raises(BundleVerificationError, match="but the plan expects"):
        create_bundle_manifest(
            expected=expected,
            acquired_files=acquired_files,
            archive_path=archive_path,
            run=run,
        )


def test_create_bundle_manifest_refuses_when_the_archive_member_disagrees(
    tmp_path: Path,
):
    """Proves the same computed-not-accepted property for the ARCHIVE side:
    the acquired file on disk is genuinely correct, but the archive was
    (re)built with different bytes under the same member name. The
    manifest must not be produced on the strength of the acquired file's
    hash alone — the archive's own content is independently re-hashed too.

    Breaks if the `member_sha256 = sha256_hex(archive.read(filename))`
    comparison against `record["sha256"]` is dropped.
    """

    expected, acquired_files, archive_path, run = _cbm_scenario(
        tmp_path, {"aaa-pkg": {"a.whl": b"AAAA"}}
    )
    # Same length as the acquired b"AAAA" (4 bytes) so the SIZE check
    # agrees and only the content-digest check can catch the disagreement
    # — a differently-sized payload would be caught by the size check
    # first and prove nothing about the digest comparison.
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.whl", b"ZZZZ")

    with pytest.raises(BundleVerificationError, match="content digest"):
        create_bundle_manifest(
            expected=expected,
            acquired_files=acquired_files,
            archive_path=archive_path,
            run=run,
        )


# ── canonical bundle manifest: the input-domain gate ───────────────────────


def test_create_bundle_manifest_refuses_a_non_finite_package_name(tmp_path: Path):
    """Michael's required input-domain gate. `json.dumps` (inside the
    UNMODIFIED `canonical_json_bytes`) permits `NaN`/`Infinity` by default,
    which are not strict JSON — so this constructor refuses a non-string
    `package_normalised_name` at its own boundary, before the value is
    ever placed into a member record, rather than changing the serializer.

    Breaks if the `isinstance(artifact.package_normalised_name, str)`
    guard is dropped: `float("nan")` would then reach
    `members[filename]["package"]`, and the unmodified
    `canonical_json_bytes` would silently emit the literal `NaN` token.
    """

    expected, acquired_files, archive_path, run = _cbm_scenario(
        tmp_path, {"aaa-pkg": {"a.whl": b"AAAA"}}
    )
    tampered = ExpectedArtifactSet(
        plan_digest=expected.plan_digest,
        artifacts=(
            ExpectedArtifact(
                package_normalised_name=float("nan"),  # type: ignore[arg-type]
                filename="a.whl",
                sha256=expected.artifacts[0].sha256,
            ),
        ),
    )

    with pytest.raises(
        BundleVerificationError, match="package_normalised_name must be a non-empty"
    ):
        create_bundle_manifest(
            expected=tampered,
            acquired_files=acquired_files,
            archive_path=archive_path,
            run=run,
        )


def test_create_bundle_manifest_refuses_a_non_finite_plan_digest(tmp_path: Path):
    """The second reachable channel for the same gate: `plan_digest` is
    copied verbatim from `ExpectedArtifactSet` into the manifest, so it is
    checked for the same `_SHA256_HEX` shape every other digest in this
    module is, before it is used for anything.

    Breaks if the `plan_digest` shape check at the top of
    `create_bundle_manifest` is dropped: `float("inf")` would then reach
    `manifest["plan_digest"]` directly (the internal
    `_refuse_malformed_bundle_manifest_shape` call would still catch it
    before `return`, but only because that check is ALSO duplicated there
    — this test targets the constructor's OWN boundary check, not that
    safety net).
    """

    expected, acquired_files, archive_path, run = _cbm_scenario(
        tmp_path, {"aaa-pkg": {"a.whl": b"AAAA"}}
    )
    tampered = ExpectedArtifactSet(
        plan_digest=float("inf"),  # type: ignore[arg-type]
        artifacts=expected.artifacts,
    )

    with pytest.raises(
        BundleVerificationError, match="plan_digest must be a 64-hex sha256 string"
    ):
        create_bundle_manifest(
            expected=tampered,
            acquired_files=acquired_files,
            archive_path=archive_path,
            run=run,
        )
