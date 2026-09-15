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
"""

from __future__ import annotations

import importlib.util
import sys
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
