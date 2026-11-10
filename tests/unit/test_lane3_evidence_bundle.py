"""The evidence-bundle manifest closes over its members or refuses.

A receipt binds bundle ciphertext by digest alone, so the manifest is
the only place bundle completeness is decidable. These tests pin the
whole-set rule at build and parse time and the member join.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import lane3_evidence_bundle as bundle  # noqa: E402

_PROBE = b'{"probe": "evidence"}\n'
_CALLS = b'{"calls": [], "schema": "lane3.exposure-calls.v1"}\n'


def _members() -> dict[str, bytes]:
    return {"probe-evidence.json": _PROBE, "exposure-calls.json": _CALLS}


def test_build_parse_and_member_join() -> None:
    manifest = bundle.build_manifest(_members())

    parsed = bundle.parse_manifest(manifest)

    assert parsed["probe-evidence.json"] == (
        hashlib.sha256(_PROBE).hexdigest(),
        len(_PROBE),
    )
    assert parsed[bundle.EXPOSURE_CALLS_MEMBER] == (
        hashlib.sha256(_CALLS).hexdigest(),
        len(_CALLS),
    )
    bundle.require_member(manifest, "exposure-calls.json", _CALLS)
    bundle.require_member(manifest, "probe-evidence.json", _PROBE)


def test_build_refuses_a_partial_bundle() -> None:
    with pytest.raises(bundle.EvidenceBundleRefused, match="missing members"):
        bundle.build_manifest({"probe-evidence.json": _PROBE})


def test_build_refuses_an_unknown_member() -> None:
    members = _members()
    members["notes.txt"] = b"hi"

    with pytest.raises(bundle.EvidenceBundleRefused, match="not members"):
        bundle.build_manifest(members)


def test_require_member_refuses_an_altered_payload() -> None:
    manifest = bundle.build_manifest(_members())

    with pytest.raises(bundle.EvidenceBundleRefused, match="does not join"):
        bundle.require_member(manifest, "exposure-calls.json", _CALLS + b" ")


def test_parse_refuses_reformatted_bytes() -> None:
    manifest = bundle.build_manifest(_members())
    pretty = json.dumps(json.loads(manifest), indent=2).encode("utf-8")

    with pytest.raises(bundle.EvidenceBundleRefused, match="canonical"):
        bundle.parse_manifest(pretty)


def test_parse_refuses_a_duplicate_member() -> None:
    document = json.loads(bundle.build_manifest(_members()))
    document["members"].append(document["members"][0])

    with pytest.raises(bundle.EvidenceBundleRefused, match="listed twice"):
        bundle.parse_manifest(json.dumps(document).encode("utf-8"))


def test_parse_refuses_unknown_schema_and_non_json() -> None:
    document = json.loads(bundle.build_manifest(_members()))
    document["schema"] = "lane3.evidence-bundle-manifest.v0"
    with pytest.raises(bundle.EvidenceBundleRefused, match="schema"):
        bundle.parse_manifest(json.dumps(document).encode("utf-8"))

    with pytest.raises(bundle.EvidenceBundleRefused, match="UTF-8 JSON"):
        bundle.parse_manifest(b"\xff no")
