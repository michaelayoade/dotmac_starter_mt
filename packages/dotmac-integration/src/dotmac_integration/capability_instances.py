"""Provider-neutral identity for one configured capability instance."""

from __future__ import annotations

import re
from typing import Final

__all__ = [
    "CAPABILITY_INSTANCE_REF_MAX_LENGTH",
    "CAPABILITY_INSTANCE_REF_PATTERN",
    "require_capability_instance_ref",
]

CAPABILITY_INSTANCE_REF_MAX_LENGTH: Final = 200
CAPABILITY_INSTANCE_REF_PATTERN: Final = r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$"
_CAPABILITY_INSTANCE_REF = re.compile(CAPABILITY_INSTANCE_REF_PATTERN, re.ASCII)


def require_capability_instance_ref(value: str) -> str:
    """Return one canonical instance reference or refuse it.

    The reference distinguishes several bindings of the same versioned
    capability.  It names an orchestrator-managed instance, not product or
    provider semantics.
    """

    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= CAPABILITY_INSTANCE_REF_MAX_LENGTH
        or _CAPABILITY_INSTANCE_REF.fullmatch(value) is None
    ):
        raise ValueError(
            "capability_instance_ref must be 1..200 ASCII characters and match "
            "^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$"
        )
    return value
