"""Canonical bytes and typed digests for runner transport documents."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from enum import Enum
from typing import Any

__all__ = ["canonical_bytes", "typed_sha256"]


def _project(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            field.name: _project(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_project(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _project(item) for key, item in sorted(value.items())}
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        raise TypeError("floats are not canonical runner policy values")
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _project(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def typed_sha256(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"
