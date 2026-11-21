"""S8b: evidence-bundle manifest v2, its archive, and v1 left untouched."""

from __future__ import annotations

import io
import json
import pathlib
import subprocess
import sys
import tarfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import lane3_evidence_bundle as bundle  # noqa: E402
import lane3_evidence_finalization as final  # noqa: E402

PROBE = b'{"probe":true}'
CALLS = b'{"calls":[]}'
NEGATIVE = b'{"proof":true}'


def _members() -> dict[str, bytes]:
    return {
        "probe-evidence.json": PROBE,
        "exposure-calls.json": CALLS,
        "negative-compensation.json": NEGATIVE,
    }


# --- the manifest -------------------------------------------------------------


def test_the_v2_manifest_has_exactly_three_members_and_its_own_schema() -> None:
    manifest = bundle.build_manifest_v2(_members())
    document = json.loads(manifest)
    assert document["schema"] == "lane3.evidence-bundle-manifest.v2"
    assert [m["name"] for m in document["members"]] == sorted(_members())
    assert bundle.parse_manifest_v2(manifest).keys() == bundle.BUNDLE_MEMBERS_V2
    bundle.require_plaintext_bundle_v2(manifest, _members())


def test_a_v1_manifest_is_refused_by_the_v2_reader_and_the_reverse() -> None:
    v1 = bundle.build_manifest(
        {
            k: v
            for k, v in _members().items()
            if k in {"probe-evidence.json", "exposure-calls.json"}
        }
    )
    with pytest.raises(bundle.EvidenceBundleRefused):
        bundle.parse_manifest_v2(v1)
    v2 = bundle.build_manifest_v2(_members())
    with pytest.raises(bundle.EvidenceBundleRefused):
        bundle.parse_manifest(v2)
    with pytest.raises(bundle.EvidenceBundleRefused):
        bundle.require_plaintext_bundle(v2, _members())


def test_v1_keeps_its_closed_two_member_set() -> None:
    assert bundle.BUNDLE_MEMBERS == {"probe-evidence.json", "exposure-calls.json"}
    with pytest.raises(bundle.EvidenceBundleRefused):
        bundle.build_manifest(_members())


@pytest.mark.parametrize("missing", sorted(_members()))
def test_a_partial_v2_bundle_is_refused_whole(missing: str) -> None:
    members = _members()
    del members[missing]
    with pytest.raises(bundle.EvidenceBundleRefused):
        bundle.build_manifest_v2(members)


def test_an_extended_v2_bundle_is_refused() -> None:
    with pytest.raises(bundle.EvidenceBundleRefused):
        bundle.build_manifest_v2({**_members(), "extra.json": b"{}"})


def test_a_changed_member_or_a_reformatted_manifest_is_refused() -> None:
    manifest = bundle.build_manifest_v2(_members())
    changed = {**_members(), "negative-compensation.json": b'{"proof":false}'}
    with pytest.raises(bundle.EvidenceBundleRefused):
        bundle.require_plaintext_bundle_v2(manifest, changed)
    with pytest.raises(bundle.EvidenceBundleRefused):
        bundle.require_plaintext_bundle_v2(manifest + b" ", _members())


# --- the archive ------------------------------------------------------------------


def test_the_v2_archive_reads_four_fixed_headers_in_order() -> None:
    raw = final._archive_v2(PROBE, CALLS, NEGATIVE)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        assert [m.name for m in archive.getmembers()] == [
            "manifest.json",
            "probe-evidence.json",
            "exposure-calls.json",
            "negative-compensation.json",
        ]
    manifest, members = final.read_plaintext_evidence_archive_v2(raw)
    assert manifest == bundle.build_manifest_v2(members)
    assert members == _members()


def test_the_caller_chooses_the_version_each_reader_refuses_the_other() -> None:
    v1 = final._archive(PROBE, CALLS)
    v2 = final._archive_v2(PROBE, CALLS, NEGATIVE)
    with pytest.raises(final.FinalizationRefused):
        final.read_plaintext_evidence_archive_v2(v1)
    with pytest.raises(final.FinalizationRefused):
        final.read_plaintext_evidence_archive(v2)
    assert final.read_plaintext_evidence_archive(v1)[1]["probe-evidence.json"] == PROBE


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw[:-1],
        lambda raw: raw + b"\0" * 512,
        lambda raw: raw.replace(
            b"negative-compensation.json", b"negative-compensatioN.json"
        ),
    ],
)
def test_a_noncanonical_v2_archive_is_refused(mutate) -> None:
    raw = final._archive_v2(PROBE, CALLS, NEGATIVE)
    with pytest.raises(final.FinalizationRefused):
        final.read_plaintext_evidence_archive_v2(mutate(raw))


def test_the_negative_member_must_be_a_json_object() -> None:
    for bad in (b"[]", b"", b"not json", b"x" * (final.MAX_MEMBER_BYTES + 1)):
        with pytest.raises(final.FinalizationRefused):
            final._archive_v2(PROBE, CALLS, bad)


# --- finalization --------------------------------------------------------------------


def test_finalize_evidence_v2_encrypts_the_v2_archive_and_v1_stays_v1(
    monkeypatch, tmp_path
) -> None:
    policy = tmp_path / "policy.json"
    from tests.unit.test_lane3_evidence_finalization import _policy

    policy.write_bytes(_policy())
    monkeypatch.setattr(final, "_POLICY", policy)
    monkeypatch.setattr(final, "_require_tool", lambda digest: None)
    context = final.prepare_encryption()
    seen: list[bytes] = []

    def run(args, **kwargs):
        seen.append(kwargs["input"])
        kwargs["stdout"].write(final._AGE_HEADER + b"transport")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(final.subprocess, "run", run)
    final.finalize_evidence_v2(
        probe_evidence=PROBE,
        exposure_calls=CALLS,
        negative_compensation=NEGATIVE,
        context=context,
        output=tmp_path / "v2.age",
    )
    final.finalize_evidence(
        probe_evidence=PROBE,
        exposure_calls=CALLS,
        context=context,
        output=tmp_path / "v1.age",
    )
    assert seen[0] == final._archive_v2(PROBE, CALLS, NEGATIVE)
    assert seen[1] == final._archive(PROBE, CALLS)
    final.read_plaintext_evidence_archive_v2(seen[0])
    with pytest.raises(final.FinalizationRefused):
        final.read_plaintext_evidence_archive_v2(seen[1])


def test_finalize_evidence_v2_refuses_a_bad_proof_member_before_any_process(
    monkeypatch, tmp_path
) -> None:
    from tests.unit.test_lane3_evidence_finalization import _policy

    policy = tmp_path / "policy.json"
    policy.write_bytes(_policy())
    monkeypatch.setattr(final, "_POLICY", policy)
    monkeypatch.setattr(final, "_require_tool", lambda digest: None)
    context = final.prepare_encryption()
    monkeypatch.setattr(final.subprocess, "run", lambda *a, **k: pytest.fail("ran"))
    with pytest.raises(final.FinalizationRefused):
        final.finalize_evidence_v2(
            probe_evidence=PROBE,
            exposure_calls=CALLS,
            negative_compensation=b"[]",
            context=context,
            output=tmp_path / "x.age",
        )
    assert not (tmp_path / "x.age").exists()


def test_no_launcher_module_calls_the_v1_finalizer() -> None:
    """Stated deviation (architect ruling Q2): v1 `finalize_evidence` stays for the
    legacy controller-half runner only and retires with it. Its bundle can never
    publish (E9 refuses v1), and no `lane3_launcher*` module may call it."""
    import ast

    modules = sorted((REPO / "scripts").glob("lane3_launcher*.py"))
    assert modules
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(
                    node.func, "id", None
                )
                assert name != "finalize_evidence", path.name
