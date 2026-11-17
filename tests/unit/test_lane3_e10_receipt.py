"""E7 / U3 (option B): the typed E10 receipt input and what the launcher compares.

Input validation only: nothing here produces a receipt or verifies a signature.
"""

from __future__ import annotations

import base64
import pathlib
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lane3_e10_receipt as e10  # noqa: E402

from tests.unit import lane3_e10_support as support  # noqa: E402

STORE_TIME = datetime(2026, 11, 17, 5, 0, 0, tzinfo=UTC)


def _match(receipt: Any, **overrides: Any) -> None:
    arguments: dict[str, Any] = {
        "ssh_fingerprint": support.SSH_FINGERPRINT,
        "alias": "vm-alpha",
        "expected_host_incarnation": support.INCARNATION_FINGERPRINT,
        "host_id": "host-a",
        "store_time": STORE_TIME,
    }
    arguments.update(overrides)
    receipt.require_matches(**arguments)


def test_a_well_formed_receipt_agreeing_with_every_input_passes() -> None:
    _match(support.make_receipt())


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"ssh_fingerprint": "SHA256:" + "C" * 43}, "receipt-ssh-fingerprint-mismatch"),
        ({"alias": "vm-beta"}, "receipt-alias-mismatch"),
        (
            {"expected_host_incarnation": support.OTHER_INCARNATION_FINGERPRINT},
            "receipt-incarnation-mismatch",
        ),
        ({"host_id": "host-b"}, "receipt-host-id-mismatch"),
    ],
)
def test_each_disagreement_with_an_independent_input_refuses(
    override: dict[str, Any], code: str
) -> None:
    with pytest.raises(e10.ReceiptRefused) as refusal:
        _match(support.make_receipt(), **override)
    assert refusal.value.code == code


def test_the_ssh_pin_and_the_incarnation_are_unrelated_values() -> None:
    """Option B: the SSH host key and the incarnation key are different keys. The
    receipt links them; neither is derived from, or compared with, the other."""
    assert support.SSH_FINGERPRINT != support.INCARNATION_FINGERPRINT
    receipt = support.make_receipt()
    assert receipt.ssh_host_key_fingerprint.startswith("SHA256:")
    assert receipt.incarnation_fingerprint.startswith("sha256:")


@pytest.mark.parametrize("value", ["", None, "SHA256:" + "A" * 43, "sha256:short", 5])
def test_the_expected_incarnation_must_be_cps_form(value: object) -> None:
    with pytest.raises(e10.ReceiptRefused) as refusal:
        _match(support.make_receipt(), expected_host_incarnation=value)
    assert refusal.value.code == "host-incarnation-required"


def test_the_incarnation_validator_accepts_only_sha256_hex() -> None:
    assert (
        e10.validate_incarnation_fingerprint(support.INCARNATION_FINGERPRINT)
        == support.INCARNATION_FINGERPRINT
    )
    for bad in ("sha256:" + "A" * 64, "SHA256:" + "0" * 64, "sha256:" + "0" * 63, ""):
        with pytest.raises(e10.ReceiptRefused):
            e10.validate_incarnation_fingerprint(bad)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("vm_alias", "Has Space", "receipt-vm-alias-invalid"),
        ("vm_alias", "", "receipt-vm-alias-invalid"),
        ("host_id", "", "receipt-host-id-invalid"),
        ("receipt_ref", "", "receipt-receipt-ref-invalid"),
        ("machine_id", "XYZ", "receipt-machine-id-invalid"),
        (
            "ssh_host_key_fingerprint",
            "sha256:" + "0" * 64,
            "receipt-ssh-fingerprint-invalid",
        ),
        ("incarnation_fingerprint", "sha256:short", "host-incarnation-required"),
        ("enrolment_uuid", "not-a-uuid", "receipt-enrolment-uuid-invalid"),
        (
            "enrolment_uuid",
            support.ENROLMENT_UUID.upper(),
            "receipt-enrolment-uuid-invalid",
        ),
        (
            "incarnation_key_destroyed_at",
            datetime(2026, 11, 17, 4, 0, 0),  # naive
            "receipt-destruction-time-invalid",
        ),
        (
            "incarnation_key_destroyed_at",
            "yesterday",
            "receipt-destruction-time-invalid",
        ),
    ],
)
def test_a_malformed_receipt_field_refuses(field: str, value: Any, code: str) -> None:
    with pytest.raises(e10.ReceiptRefused) as refusal:
        support.make_receipt(**{field: value})
    assert refusal.value.code == code


@pytest.mark.parametrize(
    "key",
    [
        "",
        support.INCARNATION_B64URL + "=",  # padded
        base64.urlsafe_b64encode(b"x" * 31).decode().rstrip("="),  # wrong length
        base64.b64encode(support.INCARNATION_RAW).decode(),  # not url-safe / padded
        "!!!",
    ],
)
def test_the_incarnation_public_key_must_be_a_canonical_32_byte_key(key: str) -> None:
    with pytest.raises(e10.ReceiptRefused) as refusal:
        support.make_receipt(incarnation_public_key_b64url=key)
    assert refusal.value.code == "incarnation-public-key-invalid"


def test_the_incarnation_fingerprint_must_identify_the_receipts_own_key() -> None:
    with pytest.raises(e10.ReceiptRefused) as refusal:
        support.make_receipt(
            incarnation_fingerprint=support.OTHER_INCARNATION_FINGERPRINT
        )
    assert refusal.value.code == "incarnation-fingerprint-does-not-identify-the-key"


def test_every_receipt_field_is_required_with_no_default() -> None:
    import dataclasses

    for field in dataclasses.fields(e10.E10ReceiptRef):
        assert field.default is dataclasses.MISSING, field.name
        assert field.default_factory is dataclasses.MISSING, field.name


def test_a_key_destroyed_after_the_store_time_refuses() -> None:
    receipt = support.make_receipt(
        incarnation_key_destroyed_at=STORE_TIME + timedelta(seconds=1)
    )
    with pytest.raises(e10.ReceiptRefused) as refusal:
        _match(receipt)
    assert refusal.value.code == "incarnation-key-not-destroyed-before-launch"


def test_a_key_destroyed_exactly_at_the_store_time_is_accepted() -> None:
    _match(support.make_receipt(incarnation_key_destroyed_at=STORE_TIME))
