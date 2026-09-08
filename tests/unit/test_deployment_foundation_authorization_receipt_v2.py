"""Focused consumer-contract tests; no executor is constructed here."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.provenance import (
    AttestedAuthorizationReceiptV2,
    AuthorizationReceiptV2,
    attest_authorization_receipt_v2,
)


def _receipt(**overrides: object) -> AuthorizationReceiptV2:
    fields: dict[str, object] = {
        "authorization_envelope_digest": "sha256:" + "a" * 64,
        "dispatch_envelope_digest": "sha256:" + "b" * 64,
        "authorization_id": "authorization-1",
        "dispatch_id": "dispatch-1",
        "authorization_signer_key_id": "control-authorize-1",
        "authorization_signer_algorithm": "ed25519",
        "authorization_signer_public_key_fingerprint": "sha256:" + "c" * 64,
        "dispatch_signer_key_id": "control-dispatch-1",
        "dispatch_signer_algorithm": "ed25519",
        "dispatch_signer_public_key_fingerprint": "sha256:" + "d" * 64,
        "product_code": "starter",
        "environment": "staging",
        "target_id": "target-1",
        "target_ref": "staging-1",
        "operation": "deploy",
        "release_ref": "release-1",
        "rollout_ref": "rollout-1",
        "plan_id": "plan-1",
        "approval_decision_ref": "decision-1",
        "authorization_issued_at": "2026-09-07T12:00:00Z",
        "authorization_expires_at": "2026-09-07T13:00:00Z",
        "dispatch_issued_at": "2026-09-07T12:30:00Z",
        "execution_sequence": 4,
        "attempt_no": 2,
        "descriptor_digest": "sha256:" + "e" * 64,
        "execution_plan_digest": "sha256:" + "f" * 64,
        "control_plan_digest": "sha256:" + "0" * 64,
    }
    fields.update(overrides)
    return AuthorizationReceiptV2(**fields)  # type: ignore[arg-type]


def _inputs() -> dict[str, object]:
    # These are independently authored execution inputs.  Do not derive them
    # from _receipt(): doing so would make the test unable to detect a reflected
    # receipt-only implementation.
    return {
        "now": datetime(2026, 9, 7, 12, 30, tzinfo=UTC),
        "product_code": "starter",
        "environment": "staging",
        "target_id": "target-1",
        "target_ref": "staging-1",
        "operation": "deploy",
        "release_ref": "release-1",
        "rollout_ref": "rollout-1",
        "plan_id": "plan-1",
        "approval_decision_ref": "decision-1",
        "execution_sequence": 4,
        "attempt_no": 2,
        "descriptor_digest": "sha256:" + "e" * 64,
        "execution_plan_digest": "sha256:" + "f" * 64,
        "control_plan_digest": "sha256:" + "0" * 64,
    }


class PairVerifier:
    def __init__(self, receipt: AuthorizationReceiptV2) -> None:
        self.receipt = receipt

    def attest_pair(
        self,
        *,
        authorization_material: Mapping[str, Any],
        dispatch_material: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if authorization_material != {"authorization": "signed"}:
            raise PreconditionFailed("authorization material was not attested")
        if dispatch_material != {"dispatch": "signed"}:
            raise PreconditionFailed("dispatch material was not attested")
        return self.receipt.as_document()


def _verified(
    receipt: AuthorizationReceiptV2 | None = None,
) -> AttestedAuthorizationReceiptV2:
    receipt = receipt or _receipt()
    return attest_authorization_receipt_v2(
        {"authorization": "signed"},
        {"dispatch": "signed"},
        attester=PairVerifier(receipt),
    )


def test_accepts_attested_pair_consumer_values_and_matches_execution_subject() -> None:
    receipt = _receipt()
    _verified(receipt).require_execution_inputs(**_inputs())
    assert receipt.product_code == "starter"


@pytest.mark.parametrize(
    "field,value",
    [
        ("product_code", None),
        ("execution_sequence", True),
        ("attempt_no", 0),
        ("descriptor_digest", "not-a-digest"),
        ("authorization_signer_public_key_fingerprint", "sha256:" + "d" * 64),
        ("authorization_envelope_digest", "sha256:" + "b" * 64),
        ("authorization_issued_at", "2026-09-07T12:00:00"),
    ],
)
def test_refuses_malformed_or_non_distinct_terms(field: str, value: object) -> None:
    with pytest.raises(SpecError):
        _receipt(**{field: value})


def test_defaults_are_absent_from_the_successor_contract() -> None:
    with pytest.raises(TypeError):
        AuthorizationReceiptV2()  # type: ignore[call-arg]


def test_document_round_trip_is_canonical_and_unknown_keys_refuse() -> None:
    receipt = _receipt()
    restored = AuthorizationReceiptV2.from_document(receipt.as_document())
    assert restored.canonical_bytes() == receipt.canonical_bytes()
    with pytest.raises(SpecError):
        AuthorizationReceiptV2.from_document({**receipt.as_document(), "unexpected": 1})
    with pytest.raises(SpecError, match="repeats key 'plan_id'"):
        AuthorizationReceiptV2.from_json(
            receipt.canonical_bytes().decode().removesuffix("}")
            + ',"plan_id":"duplicate"}'
        )


def test_liveness_includes_the_dispatch_boundary_and_injected_clock() -> None:
    receipt = _receipt()
    inputs = _inputs()
    _verified(receipt).require_execution_inputs(**inputs)
    with pytest.raises(PreconditionFailed):
        _verified(receipt).require_execution_inputs(
            **{**inputs, "now": datetime(2026, 9, 7, 12, 29, 59, tzinfo=UTC)}
        )
    with pytest.raises(PreconditionFailed):
        _verified(receipt).require_execution_inputs(
            **{**inputs, "now": datetime(2026, 9, 7, 13, 0, tzinfo=UTC)}
        )


def test_liveness_refuses_before_an_execution_input_mismatch() -> None:
    inputs = {
        **_inputs(),
        "now": datetime(2026, 9, 7, 13, 0, tzinfo=UTC),
        "approval_decision_ref": "wrong-decision",
    }
    with pytest.raises(PreconditionFailed, match="expired"):
        _verified().require_execution_inputs(**inputs)


def test_raw_v2_receipt_cannot_invoke_the_execution_gate() -> None:
    receipt = _receipt()
    assert not hasattr(receipt, "require_execution_inputs")
    with pytest.raises(PreconditionFailed):
        AttestedAuthorizationReceiptV2(object(), receipt)


def test_attested_wrapper_exposes_neither_receipt_nor_reusable_witness() -> None:
    attested = _verified()
    assert not dataclasses.is_dataclass(attested)
    assert not hasattr(attested, "receipt")
    assert not hasattr(attested, "witness")
    with pytest.raises(TypeError):
        dataclasses.replace(attested)  # type: ignore[type-var]


def test_both_control_documents_must_pass_through_the_pair_verifier() -> None:
    verifier = PairVerifier(_receipt())
    with pytest.raises(PreconditionFailed):
        attest_authorization_receipt_v2(
            {"authorization": "forged"},
            {"dispatch": "signed"},
            attester=verifier,
        )
    with pytest.raises(PreconditionFailed):
        attest_authorization_receipt_v2(
            {"authorization": "signed"},
            {"dispatch": "forged"},
            attester=verifier,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("dispatch_issued_at", "2026-09-07T11:59:00Z"),
        ("dispatch_issued_at", "2026-09-07T13:00:00Z"),
        ("operation", "recover"),
    ],
)
def test_refuses_invalid_authorization_window_or_unknown_operation(
    field: str, value: str
) -> None:
    with pytest.raises(SpecError):
        _receipt(**{field: value})


@pytest.mark.parametrize("field", tuple(name for name in _inputs() if name != "now"))
def test_refuses_each_independent_execution_subject_mismatch(field: str) -> None:
    values = _inputs()
    values[field] = (
        3
        if field in {"execution_sequence", "attempt_no"}
        else "sha256:" + "1" * 64
        if field.endswith("digest")
        else "different"
    )
    with pytest.raises(PreconditionFailed):
        _verified().require_execution_inputs(**values)  # type: ignore[arg-type]
