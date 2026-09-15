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
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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
