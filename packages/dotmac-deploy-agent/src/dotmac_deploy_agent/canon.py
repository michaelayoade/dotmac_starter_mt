"""Canonical bytes and digests. One definition, used by every signed or bound value."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Final

DIGEST: Final = re.compile(r"sha256:[0-9a-f]{64}")


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(data: bytes) -> str:
    return "sha256:" + sha256_hex(data)
