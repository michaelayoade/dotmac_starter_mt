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
        launcher_evidence.evidence_finalization, "finalize_evidence", fake
    )
    calls = serialize_exposure_calls(())
    out = tmp_path / "bundle.age"
    result = launcher_evidence.finalize(
        probe_bytes=b'{"probe":true}',
        exposure_calls_b64=_b64(calls),
        encryption=object(),  # type: ignore[arg-type]
        output=out,
    )
    assert result == CIPHERTEXT
    assert seen["probe_evidence"] == b'{"probe":true}'
    assert seen["exposure_calls"] == calls
    assert seen["output"] == out


@pytest.mark.parametrize("value", [None, 7, "", "not base64!"])
def test_finalize_refuses_undecodable_calls(
    value: object, tmp_path: pathlib.Path
) -> None:
    with pytest.raises(launcher_evidence.EvidenceHandBackRefused) as refusal:
        launcher_evidence.finalize(
            probe_bytes=b"{}",
            exposure_calls_b64=value,
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
        launcher_evidence.evidence_finalization, "finalize_evidence", fake
    )
    reformatted = json.dumps(json.loads(serialize_exposure_calls(())), indent=2)
    for raw in (b'{"calls":[]}', reformatted.encode(), b"garbage"):
        with pytest.raises(launcher_evidence.EvidenceHandBackRefused) as refusal:
            launcher_evidence.finalize(
                probe_bytes=b"{}",
                exposure_calls_b64=_b64(raw),
                encryption=object(),  # type: ignore[arg-type]
                output=tmp_path / "x",
            )
        assert refusal.value.code == "exposure-calls-not-canonical"
    assert not called


# ── verify_receipt ──────────────────────────────────────────────────────────


def _receipt(**overrides: Any) -> RehearsalReceiptV2:
    content: dict[str, Any] = {
        "schema": "stub",
        "evidence_bundle_digest": receipt_v2.evidence_bundle_digest(CIPHERTEXT),
        "execution_run": RUN.as_document(),
        "controller_identity": FINGERPRINT,
        "lease_id": "run-1",
        "host_id": "host-a",
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
        )
    assert refusal.value.code == "receipt-invalid"
