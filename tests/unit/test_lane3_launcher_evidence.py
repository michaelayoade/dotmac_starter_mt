"""E7 / T3: the launcher encrypts the evidence and verifies the receipt binding.

Real encryption needs the enrolled recipient and a pinned tool, so the
finalizer is replaced here by a recorder. What is pinned is what the launcher
decides: which bytes it hands over, what it refuses before it does, and that a
receipt not bound to ITS ciphertext, run, lease, key and host never passes.
"""

from __future__ import annotations

import base64
import json
import pathlib
import sys
from typing import Any

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lane3_launcher_evidence as launcher_evidence  # noqa: E402
import lane3_receipt_v2 as receipt_v2  # noqa: E402
from dotmac_deployment_foundation.rehearsal import (  # noqa: E402
    ExecutionRunBindingV1,
    RehearsalReceiptV2,
)
from lane3_exposure_evidence import serialize_exposure_calls  # noqa: E402

from tests.unit.lane3_negative_support import proof_bytes  # noqa: E402

CIPHERTEXT = b"age-encryption.org/v1\n-> X25519 example\n\nbody"
RUN = ExecutionRunBindingV1(repository_id=1, run_id=2, run_attempt=1)
FINGERPRINT = "SHA256:" + "A" * 43


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


# ── finalize ────────────────────────────────────────────────────────────────


def test_finalize_hands_the_launchers_probe_bytes_and_the_calls_to_the_encryptor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    seen: dict[str, Any] = {}

    def fake(**kwargs: Any) -> bytes:
        seen.update(kwargs)
        return CIPHERTEXT

    monkeypatch.setattr(
        launcher_evidence.evidence_finalization, "finalize_evidence_v2", fake
    )
    calls = serialize_exposure_calls(())
    out = tmp_path / "bundle.age"
    result = launcher_evidence.finalize(
        probe_bytes=b'{"probe":true}',
        exposure_calls_b64=_b64(calls),
        negative_compensation=proof_bytes(),
        encryption=object(),  # type: ignore[arg-type]
        output=out,
    )
    assert result == CIPHERTEXT
    assert seen["probe_evidence"] == b'{"probe":true}'
    assert seen["exposure_calls"] == calls
    assert seen["negative_compensation"] == proof_bytes()
    assert seen["output"] == out


@pytest.mark.parametrize("value", [None, 7, "", "not base64!"])
def test_finalize_refuses_undecodable_calls(
    value: object, tmp_path: pathlib.Path
) -> None:
    with pytest.raises(launcher_evidence.EvidenceHandBackRefused) as refusal:
        launcher_evidence.finalize(
            probe_bytes=b"{}",
            exposure_calls_b64=value,
            negative_compensation=proof_bytes(),
            encryption=object(),  # type: ignore[arg-type]
            output=tmp_path / "x",
        )
    assert refusal.value.code == "exposure-calls-invalid"


def test_finalize_refuses_calls_that_are_not_the_canonical_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    called = False

    def fake(**_: Any) -> bytes:  # pragma: no cover - must not run
        nonlocal called
        called = True
        return CIPHERTEXT

    monkeypatch.setattr(
        launcher_evidence.evidence_finalization, "finalize_evidence_v2", fake
    )
    reformatted = json.dumps(json.loads(serialize_exposure_calls(())), indent=2)
    for raw in (b'{"calls":[]}', reformatted.encode(), b"garbage"):
        with pytest.raises(launcher_evidence.EvidenceHandBackRefused) as refusal:
            launcher_evidence.finalize(
                probe_bytes=b"{}",
                exposure_calls_b64=_b64(raw),
                negative_compensation=proof_bytes(),
                encryption=object(),  # type: ignore[arg-type]
                output=tmp_path / "x",
            )
        assert refusal.value.code == "exposure-calls-not-canonical"
    assert not called


@pytest.mark.parametrize("proof", [None, b"", b"{}", b"garbage"])
def test_finalize_refuses_a_missing_or_malformed_negative_proof(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, proof: object
) -> None:
    def fake(**_: Any) -> bytes:  # pragma: no cover - must not run
        raise AssertionError("encrypted without a proof")

    monkeypatch.setattr(
        launcher_evidence.evidence_finalization, "finalize_evidence_v2", fake
    )
    with pytest.raises(launcher_evidence.EvidenceHandBackRefused) as refusal:
        launcher_evidence.finalize(
            probe_bytes=b"{}",
            exposure_calls_b64=_b64(serialize_exposure_calls(())),
            negative_compensation=proof,  # type: ignore[arg-type]
            encryption=object(),  # type: ignore[arg-type]
            output=tmp_path / "x",
        )
    assert refusal.value.code == "negative-compensation-invalid"


# ── verify_receipt ──────────────────────────────────────────────────────────


CODES = (
    "external_positive_v6",
    "external_negative_v6",
    "external_v4",
    "closed_port_behaviour",
    "private_from_source",
    "privileged_vantage_refused",
)
SENT_ROWS = [
    {
        "code": code,
        "status": "executed_passed",
        "detail": f"measured {code}",
        "evidence": [f"probe:{code}"],
    }
    for code in CODES
]
BINDINGS: dict[str, str] = {
    "foundation_revision": "a" * 40,
    "foundation_artifact_digest": "sha256:" + "c" * 64,
    "fixture_digest": "sha256:" + "f" * 64,
    "probe_vantage_ref": "vantage@1",
    "execution_plan_digest": "sha256:" + "d" * 64,
}


def _receipt(**overrides: Any) -> RehearsalReceiptV2:
    content: dict[str, Any] = {
        "schema": "stub",
        "evidence_bundle_digest": receipt_v2.evidence_bundle_digest(CIPHERTEXT),
        "execution_run": RUN.as_document(),
        "controller_identity": FINGERPRINT,
        "lease_id": "run-1",
        "host_id": "host-a",
        "results": [dict(row) for row in SENT_ROWS],
        **BINDINGS,
    }
    content.update(overrides)
    return RehearsalReceiptV2(content=content)


def _verify(
    monkeypatch: pytest.MonkeyPatch,
    receipt: RehearsalReceiptV2,
    **overrides: Any,
) -> RehearsalReceiptV2:
    monkeypatch.setattr(
        RehearsalReceiptV2, "from_json", classmethod(lambda cls, payload: receipt)
    )
    raw = receipt.canonical_bytes()
    arguments: dict[str, Any] = {
        "receipt_b64": _b64(raw),
        "receipt_digest": receipt.sha256_digest(),
        "ciphertext": CIPHERTEXT,
        "execution_run": RUN,
        "controller_identity": FINGERPRINT,
        "lease_id": "run-1",
        "host_id": "host-a",
        "sent_rows": SENT_ROWS,
        "foundation_revision": BINDINGS["foundation_revision"],
        "foundation_artifact": BINDINGS["foundation_artifact_digest"],
        "fixture_digest": BINDINGS["fixture_digest"],
        "probe_vantage_ref": BINDINGS["probe_vantage_ref"],
        "plan_digest": BINDINGS["execution_plan_digest"],
    }
    arguments.update(overrides)
    return launcher_evidence.verify_receipt(**arguments)


def test_a_receipt_bound_to_the_launchers_values_passes(monkeypatch) -> None:
    receipt = _receipt()
    assert _verify(monkeypatch, receipt) is receipt


@pytest.mark.parametrize(
    ("override", "code"),
    [
        (
            {"evidence_bundle_digest": "sha256:" + "0" * 64},
            "evidence-bundle-digest-mismatch",
        ),
        (
            {"execution_run": {"repository_id": 1, "run_id": 3, "run_attempt": 1}},
            "execution-run-mismatch",
        ),
        ({"controller_identity": "SHA256:" + "B" * 43}, "controller_identity-mismatch"),
        ({"lease_id": "run-2"}, "lease_id-mismatch"),
        ({"host_id": "host-b"}, "host_id-mismatch"),
    ],
)
def test_each_binding_mismatch_refuses(
    monkeypatch, override: dict[str, Any], code: str
) -> None:
    with pytest.raises(launcher_evidence.EvidenceHandBackRefused) as refusal:
        _verify(monkeypatch, _receipt(**override))
    assert refusal.value.code == code


def test_a_digest_the_receipt_does_not_have_refuses(monkeypatch) -> None:
    with pytest.raises(launcher_evidence.EvidenceHandBackRefused) as refusal:
        _verify(monkeypatch, _receipt(), receipt_digest="sha256:" + "1" * 64)
    assert refusal.value.code == "receipt-digest-mismatch"


def test_bytes_that_are_not_the_receipts_canonical_form_refuse(monkeypatch) -> None:
    receipt = _receipt()
    monkeypatch.setattr(
        RehearsalReceiptV2, "from_json", classmethod(lambda cls, payload: receipt)
    )
    padded = receipt.canonical_bytes() + b" "
    with pytest.raises(launcher_evidence.EvidenceHandBackRefused) as refusal:
        launcher_evidence.verify_receipt(
            receipt_b64=_b64(padded),
            receipt_digest=receipt.sha256_digest(),
            ciphertext=CIPHERTEXT,
            execution_run=RUN,
            controller_identity=FINGERPRINT,
            lease_id="run-1",
            host_id="host-a",
            sent_rows=SENT_ROWS,
            foundation_revision=BINDINGS["foundation_revision"],
            foundation_artifact=BINDINGS["foundation_artifact_digest"],
            fixture_digest=BINDINGS["fixture_digest"],
            probe_vantage_ref=BINDINGS["probe_vantage_ref"],
            plan_digest=BINDINGS["execution_plan_digest"],
        )
    assert refusal.value.code == "receipt-not-canonical"


def test_a_plaintext_bundle_is_refused(monkeypatch) -> None:
    with pytest.raises(launcher_evidence.EvidenceHandBackRefused) as refusal:
        _verify(monkeypatch, _receipt(), ciphertext=b'{"plain":"text"}')
    assert refusal.value.code == "ciphertext-not-age"


@pytest.mark.parametrize("expected", ["controller_identity", "lease_id", "host_id"])
def test_an_empty_launcher_value_refuses_rather_than_matching(
    monkeypatch, expected: str
) -> None:
    with pytest.raises(launcher_evidence.EvidenceHandBackRefused):
        _verify(monkeypatch, _receipt(**{expected: ""}), **{expected: ""})


def test_undecodable_receipt_refuses() -> None:
    with pytest.raises(launcher_evidence.EvidenceHandBackRefused) as refusal:
        launcher_evidence.verify_receipt(
            receipt_b64="not base64!",
            receipt_digest="x",
            ciphertext=CIPHERTEXT,
            execution_run=RUN,
            controller_identity=FINGERPRINT,
            lease_id="run-1",
            host_id="host-a",
            sent_rows=SENT_ROWS,
            foundation_revision="x",
            foundation_artifact="x",
            fixture_digest="x",
            probe_vantage_ref="x",
            plan_digest="x",
        )
    assert refusal.value.code == "receipt-invalid"


# ── H3: what the target assembled must equal what the launcher sent and holds ──


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("foundation_revision", "foundation_revision-mismatch"),
        ("foundation_artifact_digest", "foundation_artifact_digest-mismatch"),
        ("fixture_digest", "fixture_digest-mismatch"),
        ("probe_vantage_ref", "probe_vantage_ref-mismatch"),
        ("execution_plan_digest", "execution_plan_digest-mismatch"),
    ],
)
def test_each_independently_held_binding_must_match_exactly(
    monkeypatch, field: str, code: str
) -> None:
    with pytest.raises(launcher_evidence.EvidenceHandBackRefused) as refusal:
        _verify(monkeypatch, _receipt(**{field: "tampered"}))
    assert refusal.value.code == code


def _rows_with(index: int, **changes: Any) -> list[dict[str, Any]]:
    rows = [dict(row) for row in SENT_ROWS]
    rows[index] = {**rows[index], **changes}
    return rows


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "executed_failed"},
        {"status": "blocked"},
        {"detail": "a better story than the one measured"},
        {"evidence": ["probe:other"]},
        {"evidence": []},
    ],
)
def test_a_changed_launcher_row_is_refused_not_passed(
    monkeypatch, changes: dict[str, Any]
) -> None:
    receipt = _receipt(results=_rows_with(2, **changes))
    with pytest.raises(launcher_evidence.EvidenceHandBackRefused) as refusal:
        _verify(monkeypatch, receipt)
    assert refusal.value.code == "external-rows-mismatch"


def test_a_failed_row_the_target_upgraded_to_passed_is_refused(monkeypatch) -> None:
    sent = _rows_with(0, status="executed_failed")
    receipt = _receipt()  # the target assembled all six as passed
    with pytest.raises(launcher_evidence.EvidenceHandBackRefused) as refusal:
        _verify(monkeypatch, receipt, sent_rows=sent)
    assert refusal.value.code == "external-rows-mismatch"


def test_a_missing_launcher_row_is_refused(monkeypatch) -> None:
    receipt = _receipt(results=[dict(row) for row in SENT_ROWS[:-1]])
    with pytest.raises(launcher_evidence.EvidenceHandBackRefused) as refusal:
        _verify(monkeypatch, receipt)
    assert refusal.value.code == "external-rows-mismatch"


def test_a_duplicated_launcher_row_is_refused(monkeypatch) -> None:
    receipt = _receipt(results=[*SENT_ROWS, dict(SENT_ROWS[0])])
    with pytest.raises(launcher_evidence.EvidenceHandBackRefused) as refusal:
        _verify(monkeypatch, receipt)
    assert refusal.value.code == "external-rows-mismatch"


def test_rows_the_target_owns_are_not_this_checks_business(monkeypatch) -> None:
    own = {
        "code": "digest_equality",
        "status": "executed_passed",
        "detail": "target measured",
        "evidence": [],
    }
    receipt = _receipt(results=[*SENT_ROWS, own])
    assert _verify(monkeypatch, receipt) is receipt


def test_an_empty_sent_set_refuses_rather_than_matching_nothing(monkeypatch) -> None:
    with pytest.raises(launcher_evidence.EvidenceHandBackRefused) as refusal:
        _verify(monkeypatch, _receipt(), sent_rows=[])
    assert refusal.value.code == "external-rows-not-supplied"
