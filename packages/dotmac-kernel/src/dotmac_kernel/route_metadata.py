"""Pure route-dependency metadata keys shared by declarations and guards."""

from typing import Final

CAPABILITY_CODE_ATTR: Final[str] = "dotmac_capability_code"
PERMISSION_CODE_ATTR: Final[str] = "dotmac_permission_code"

__all__ = ["CAPABILITY_CODE_ATTR", "PERMISSION_CODE_ATTR"]
