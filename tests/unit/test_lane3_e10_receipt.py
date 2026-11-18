"""E7 / U3 (option B): the typed E10 receipt input and what the launcher compares.

Input validation only: nothing here produces a receipt or verifies a signature.
"""

from __future__ import annotations

import base64
import hashlib
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
        "machine_id": support.MACHINE_ID,
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
        ({"machine_id": "f" * 32}, "receipt-machine-id-mismatch"),
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


# ── L2a: the receipt's enrolment must be the staged context's ───────────────


def _context(**fields: Any) -> bytes:
    import json

    return json.dumps({"host_enrolment_ref": support.ENROLMENT_UUID, **fields}).encode()


def test_a_context_naming_the_receipts_enrolment_passes() -> None:
    e10.require_enrolment_matches(_context(), support.make_receipt())


def test_a_context_naming_another_enrolment_refuses() -> None:
    other = "11111111-2222-4333-8444-555555555555"
    with pytest.raises(e10.ReceiptRefused) as refusal:
        e10.require_enrolment_matches(
            _context(host_enrolment_ref=other), support.make_receipt()
        )
    assert refusal.value.code == "receipt-enrolment-mismatch"


@pytest.mark.parametrize(
    ("context", "code"),
    [
        (b"", "execution-context-unreadable"),
        (b"not json", "execution-context-unreadable"),
        (b"[1, 2]", "execution-context-unreadable"),
        (b"x" * (1024 * 1024 + 1), "execution-context-unreadable"),
        ("{}", "execution-context-unreadable"),  # str, not bytes
        (b"{}", "execution-context-enrolment-missing"),
        (b'{"host_enrolment_ref": ""}', "execution-context-enrolment-missing"),
        (b'{"host_enrolment_ref": 5}', "execution-context-enrolment-missing"),
    ],
)
def test_an_unreadable_or_incomplete_context_refuses(context: Any, code: str) -> None:
    with pytest.raises(e10.ReceiptRefused) as refusal:
        e10.require_enrolment_matches(context, support.make_receipt())
    assert refusal.value.code == code


# ── L2c: Control's key encoding is unpadded base64url, and only that ────────


def test_one_key_encoded_both_ways_is_accepted_in_controls_form_only() -> None:
    # 0xFB 0xEF... use the characters that differ between the two alphabets
    raw = bytes([0xFB, 0xEF, 0xBE] * 10 + [0xFF, 0xFE])
    assert len(raw) == 32
    unpadded_url = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    padded_url = base64.urlsafe_b64encode(raw).decode()
    padded_standard = base64.b64encode(raw).decode()
    # the SAME 32 bytes, three different strings
    assert base64.urlsafe_b64decode(unpadded_url + "=") == raw
    assert base64.b64decode(padded_standard) == raw
    assert len({unpadded_url, padded_url, padded_standard}) == 3
    fingerprint = "sha256:" + hashlib.sha256(raw).hexdigest()

    accepted = support.make_receipt(
        incarnation_public_key_b64url=unpadded_url,
        incarnation_fingerprint=fingerprint,
    )
    assert accepted.incarnation_fingerprint == fingerprint
    for spelling in (padded_url, padded_standard):
        with pytest.raises(e10.ReceiptRefused) as refusal:
            support.make_receipt(
                incarnation_public_key_b64url=spelling,
                incarnation_fingerprint=fingerprint,
            )
        assert refusal.value.code == "incarnation-public-key-invalid"


def test_a_standard_alphabet_key_is_refused_even_without_padding() -> None:
    """Bytes whose standard encoding uses `+` or `/` differ from the url-safe form."""
    raw = bytes([0xFB] * 32)  # standard: '+' '/' characters; url-safe: '-' '_'
    standard = base64.b64encode(raw).decode().rstrip("=")
    url = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    assert standard != url and ("+" in standard or "/" in standard)
    fingerprint = "sha256:" + hashlib.sha256(raw).hexdigest()
    support.make_receipt(
        incarnation_public_key_b64url=url, incarnation_fingerprint=fingerprint
    )
    with pytest.raises(e10.ReceiptRefused) as refusal:
        support.make_receipt(
            incarnation_public_key_b64url=standard,
            incarnation_fingerprint=fingerprint,
        )
    assert refusal.value.code == "incarnation-public-key-invalid"


def test_vm_slot_is_optional_and_checked_when_given() -> None:
    assert support.make_receipt().vm_slot is None
    assert support.make_receipt(vm_slot="dotmacproxmox/102").vm_slot == (
        "dotmacproxmox/102"
    )
    for bad in ("", "node", "node/0", "node/abc", "node:22", 102):
        with pytest.raises(e10.ReceiptRefused) as refusal:
            support.make_receipt(vm_slot=bad)
        assert refusal.value.code == "receipt-vm-slot-invalid"
