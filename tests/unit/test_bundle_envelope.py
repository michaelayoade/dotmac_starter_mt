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
"""

from __future__ import annotations

import importlib.util
import json
import sys
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
