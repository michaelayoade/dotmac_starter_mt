"""A valid E10 receipt input, for tests. Builds nothing but the typed input."""

from __future__ import annotations

import base64
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lane3_e10_receipt import E10ReceiptRef  # noqa: E402

INCARNATION_RAW = bytes(range(32, 64))
INCARNATION_B64URL = base64.urlsafe_b64encode(INCARNATION_RAW).decode().rstrip("=")
INCARNATION_FINGERPRINT = "sha256:" + hashlib.sha256(INCARNATION_RAW).hexdigest()
OTHER_INCARNATION_FINGERPRINT = "sha256:" + "0" * 64
SSH_FINGERPRINT = "SHA256:" + "B" * 43
MACHINE_ID = "0123456789abcdef0123456789abcdef"
ENROLMENT_UUID = "6f1c2f4e-52a8-4d0e-9a1b-2f0c7e6d9b11"
DESTROYED_AT = datetime(2026, 11, 17, 4, 0, 0, tzinfo=UTC)


def make_receipt(**overrides: Any) -> E10ReceiptRef:
    values: dict[str, Any] = {
        "vm_alias": "vm-alpha",
        "host_id": "host-a",
        "machine_id": MACHINE_ID,
        "ssh_host_key_fingerprint": SSH_FINGERPRINT,
        "incarnation_public_key_b64url": INCARNATION_B64URL,
        "incarnation_fingerprint": INCARNATION_FINGERPRINT,
        "enrolment_uuid": ENROLMENT_UUID,
        "incarnation_key_destroyed_at": DESTROYED_AT,
        "receipt_ref": "e10-receipt-0001",
    }
    values.update(overrides)
    return E10ReceiptRef(**values)
