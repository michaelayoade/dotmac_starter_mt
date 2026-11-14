"""Hosted real-signature consumer tests; private proof production stays separate."""

from __future__ import annotations

import base64
import copy
import dataclasses
import json
import os
import pathlib
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
import yaml
from dotmac_deployment_foundation.rehearsal import ExecutionRunBindingV1

from tests.unit.test_deployment_foundation_rehearsal_receipt_v2 import (
    REVISION,
    Subject,
    subject,  # noqa: F401 - shared pytest fixture
)
from tests.unit.test_lane3_offline_signature import (
    canonical,
    command,
    signed,  # noqa: F401 - real hosted synthetic-key fixture
)
from tests.unit.test_require_rehearsal_organization_oracle import (
    REPO_ID,
    RUN,
    RUN_ID,
    TOPOLOGY,
    _build,
    _main,
    _network,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import recheck_offline_result as recheck  # noqa: E402
import require_rehearsal as oracle  # noqa: E402
from lane3_offline_result import (  # noqa: E402
    NAMESPACE,
    OfflineResultCoordinates,
    OfflineResultRefused,
    OfflineResultUnavailable,
)


def _receipt(subject_value: Subject, run_id: int = RUN_ID):
    return _build(
        subject_value,
        execution_run=ExecutionRunBindingV1(
            repository_id=REPO_ID, run_id=run_id, run_attempt=1
        ),
    )


def _public_evidence(signed_case, receipt):
    build, _, _, key, _ = signed_case
    original, _, policy = build()
    document = json.loads(original)
    expected = OfflineResultCoordinates(
        repository_id=REPO_ID,
        run_id=RUN_ID,
        run_attempt=1,
        receipt_digest=receipt.sha256_digest(),
        ciphertext_digest=receipt.content["evidence_bundle_digest"],
        candidate_artifact_digest=receipt.foundation_artifact_digest,
        release_revision=REVISION,
    )
    document["coordinates"] = dataclasses.asdict(expected)
    payload = canonical(document)
    signature = command("-Y", "sign", "-f", str(key), "-n", NAMESPACE, input=payload)
    return payload, signature, policy


def _inputs(monkeypatch, payload, signature):
    monkeypatch.setenv(oracle._RESULT_ENV, base64.b64encode(payload).decode("ascii"))
    monkeypatch.setenv(
        oracle._SIGNATURE_ENV, base64.b64encode(signature).decode("ascii")
    )


def test_main_authenticates_real_signature_before_success_and_retention(
    subject: Subject,  # noqa: F811
    signed,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    receipt = _receipt(subject)
    payload, signature, policy = _public_evidence(signed, receipt)
    _network(monkeypatch, receipt.canonical_bytes())
    _inputs(monkeypatch, payload, signature)
    monkeypatch.setattr(oracle, "_load_offline_policy", lambda: policy)
    retained = tmp_path / "public-evidence"
    monkeypatch.setattr(oracle, "_OFFLINE_EVIDENCE_DIR", retained)
    assert _main(tmp_path, TOPOLOGY) == oracle.EXIT_OK
    assert {p.name for p in retained.iterdir()} == {
        "offline-result.json",
        "offline-result.sshsig",
    }
    assert (retained / "offline-result.json").read_bytes() == payload
    assert (retained / "offline-result.sshsig").read_bytes() == signature


def test_old_signed_claim_cannot_follow_a_new_selected_receipt(
    subject: Subject,  # noqa: F811
    signed,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    payload, signature, policy = _public_evidence(signed, _receipt(subject))
    newer = _receipt(subject, RUN_ID + 1)
    run = copy.deepcopy(RUN)
    run["id"] = RUN_ID + 1
    _network(monkeypatch, newer.canonical_bytes(), run=run)
    _inputs(monkeypatch, payload, signature)
    monkeypatch.setattr(oracle, "_load_offline_policy", lambda: policy)
    retained = tmp_path / "public-evidence"
    monkeypatch.setattr(oracle, "_OFFLINE_EVIDENCE_DIR", retained)
    assert _main(tmp_path, TOPOLOGY) == oracle.EXIT_REFUSED
    assert not retained.exists()


def test_invalid_public_input_refuses_without_private_diagnostics_or_retention(
    subject: Subject,  # noqa: F811
    signed,  # noqa: F811
    tmp_path,
    monkeypatch,
    capsys,
):
    _, signature, policy = _public_evidence(signed, _receipt(subject))
    _network(monkeypatch, _receipt(subject).canonical_bytes())
    _inputs(monkeypatch, b'{"PRIVATE_SENTINEL":true}\n', signature)
    monkeypatch.setattr(oracle, "_load_offline_policy", lambda: policy)
    retained = tmp_path / "public-evidence"
    monkeypatch.setattr(oracle, "_OFFLINE_EVIDENCE_DIR", retained)
    assert _main(tmp_path, TOPOLOGY) == oracle.EXIT_REFUSED
    assert "PRIVATE_SENTINEL" not in capsys.readouterr().err
    assert not retained.exists()


@pytest.mark.parametrize("encoded", ["!!!!", "YR==", "YQ==\n", "é", "YWI="])
def test_transport_rejects_invalid_noncanonical_or_over_limit_base64(
    monkeypatch, encoded
):
    monkeypatch.setenv(oracle._RESULT_ENV, encoded)
    with pytest.raises(OfflineResultRefused):
        oracle._decode_public_input(oracle._RESULT_ENV, 1)


def test_transport_missing_is_unavailable_and_valid_is_exact(monkeypatch):
    monkeypatch.delenv(oracle._RESULT_ENV, raising=False)
    with pytest.raises(OfflineResultUnavailable):
        oracle._decode_public_input(oracle._RESULT_ENV, 1)
    monkeypatch.setenv(oracle._RESULT_ENV, "YQ==")
    assert oracle._decode_public_input(oracle._RESULT_ENV, 1) == b"a"


def test_missing_fixed_revocation_file_is_unavailable(tmp_path, monkeypatch):
    root = REPO / "docs" / "inventories"
    for name in (
        "lane3-offline-verifier-policy.json",
        "lane3-offline-verifier.allowed_signers",
    ):
        (tmp_path / name).write_bytes((root / name).read_bytes())
    monkeypatch.setattr(oracle, "_OFFLINE_POLICY_ROOT", tmp_path)
    with pytest.raises(OfflineResultUnavailable):
        oracle._load_offline_policy()


def test_release_transport_is_environment_only_and_retains_only_public_files():
    workflow = yaml.safe_load(
        (REPO / ".github" / "workflows" / "release-facility.yml").read_text()
    )
    dispatch = workflow.get("on", workflow.get(True))["workflow_dispatch"]["inputs"]
    assert not dispatch["offline_result_b64"]["required"]
    assert not dispatch["offline_signature_b64"]["required"]
    steps = workflow["jobs"]["build"]["steps"]
    gate = next(step for step in steps if step.get("id") == "rehearsal")
    assert gate["env"][oracle._RESULT_ENV] == "${{ inputs.offline_result_b64 }}"
    assert gate["env"][oracle._SIGNATURE_ENV] == "${{ inputs.offline_signature_b64 }}"
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            assert "inputs.offline_result_b64" not in str(step.get("run", ""))
            assert "inputs.offline_signature_b64" not in str(step.get("run", ""))
    retained = next(
        step
        for step in steps
        if step.get("name")
        == "Retain authenticated public offline verification evidence"
    )
    assert set(retained["with"]["path"].splitlines()) == {
        ".rehearsal/receipt.json",
        ".rehearsal/evidence-bundle.age",
        ".rehearsal/source-status.md",
        ".rehearsal/source-artifact.json",
        ".rehearsal/offline-result.json",
        ".rehearsal/offline-result.sshsig",
    }
    assert retained["with"]["if-no-files-found"] == "error"


def test_result_expiring_during_crypto_is_refused_before_retention(
    subject: Subject,  # noqa: F811
    signed,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    receipt = _receipt(subject)
    payload, signature, policy = _public_evidence(signed, receipt)
    _inputs(monkeypatch, payload, signature)
    monkeypatch.setattr(oracle, "_load_offline_policy", lambda: policy)
    retained = tmp_path / "public-evidence"
    monkeypatch.setattr(oracle, "_OFFLINE_EVIDENCE_DIR", retained)
    times = iter((signed[2], signed[2] + timedelta(hours=2)))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            return next(times)

    monkeypatch.setattr(oracle, "datetime", Clock)
    claim = json.loads(payload)["coordinates"]
    with pytest.raises(OfflineResultRefused):
        oracle._require_authenticated_offline_result(OfflineResultCoordinates(**claim))
    assert not retained.exists()


def test_publisher_recheck_refuses_after_a_valid_build_result_expires(
    subject: Subject,  # noqa: F811
    signed,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    receipt = _receipt(subject)
    payload, signature, policy = _public_evidence(signed, receipt)
    _inputs(monkeypatch, payload, signature)
    monkeypatch.setattr(oracle, "_load_offline_policy", lambda: policy)
    monkeypatch.setattr(oracle, "_OFFLINE_EVIDENCE_DIR", tmp_path / "public")
    coordinates = OfflineResultCoordinates(**json.loads(payload)["coordinates"])
    oracle._require_authenticated_offline_result(coordinates)
    for field, name in recheck.EXPECTED_ENV.items():
        monkeypatch.setenv(name, str(getattr(coordinates, field)))
    monkeypatch.setenv("GITHUB_SHA", coordinates.release_revision)

    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return signed[2] + timedelta(hours=2)

    monkeypatch.setattr(oracle, "datetime", ExpiredClock)
    assert recheck.main() == 1


def test_recheck_missing_or_wrong_release_outputs_never_skip(monkeypatch):
    for name in recheck.EXPECTED_ENV.values():
        monkeypatch.delenv(name, raising=False)
    assert recheck.main() == 2
    for field, name in recheck.EXPECTED_ENV.items():
        monkeypatch.setenv(
            name,
            "1" if field in {"repository_id", "run_id", "run_attempt"} else "a" * 40,
        )
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    assert recheck.main() == 1


def test_recheck_path_does_not_import_candidate_sdk():
    program = """
import builtins, runpy
original = builtins.__import__
attempted = []
def no_sdk(name, *args, **kwargs):
    if name.startswith('dotmac_deployment_foundation'):
        attempted.append(name)
        raise AssertionError('candidate SDK imported by public recheck')
    return original(name, *args, **kwargs)
builtins.__import__ = no_sdk
namespace = runpy.run_path(
    'scripts/recheck_offline_result.py', run_name='offline_import_probe'
)
assert namespace['main']() == 2
assert not attempted
"""
    environment = dict(os.environ)
    for field, name in recheck.EXPECTED_ENV.items():
        environment[name] = (
            "1" if field in {"repository_id", "run_id", "run_attempt"} else "a" * 40
        )
    environment["GITHUB_SHA"] = "a" * 40
    completed = subprocess.run(  # noqa: S603 - fixed hosted import-trap program
        [sys.executable, "-c", program],
        cwd=REPO,
        env=environment,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0


def test_publication_rechecks_after_install_and_before_first_write():
    workflow = yaml.safe_load(
        (REPO / ".github" / "workflows" / "release-facility.yml").read_text()
    )
    step = next(
        step
        for step in workflow["jobs"]["publish"]["steps"]
        if step.get("name") == "Publish to the Forgejo private index"
    )
    run = step["run"]
    assert (
        run.index("pip install")
        < run.index("assert_current_main.sh")
        < run.index("scripts/recheck_offline_result.py")
        < run.index("twine upload")
    )
    for field, name in recheck.EXPECTED_ENV.items():
        assert step["env"][name] == "${{ needs.build.outputs.offline_" + field + " }}"
    outputs = workflow["jobs"]["build"]["outputs"]
    assert {"offline_" + field for field in recheck.EXPECTED_ENV} <= set(outputs)
