"""Concurrency-safe allocation and formatting of configured document series.

Public surface. Everything else is internal and may change without a major
bump — see `COMPATIBILITY.md` in the repository root policy.

The module allocates and formats. It does not decide what a number MEANS,
which documents require one, whether a series may have gaps, or when a fiscal
period begins. Those belong to the owner that issues the document.
"""

from dotmac_numbering.models import (
    PLATFORM_TABLES,
    RESET_POLICIES,
    SCHEMA,
    TENANT_TABLES,
    AllocationReceipt,
    NumberSeries,
    PlatformAllocationReceipt,
    PlatformNumberSeries,
)
from dotmac_numbering.service import (
    MAX_VALUE,
    Allocation,
    NumberingError,
    advance_to_at_least,
    allocate,
    format_number,
    period_for,
    preview,
    request_fingerprint,
)

__version__ = "0.1.0a1"

__all__ = [
    "MAX_VALUE",
    "PLATFORM_TABLES",
    "RESET_POLICIES",
    "SCHEMA",
    "TENANT_TABLES",
    "Allocation",
    "AllocationReceipt",
    "NumberSeries",
    "NumberingError",
    "PlatformAllocationReceipt",
    "PlatformNumberSeries",
    "__version__",
    "advance_to_at_least",
    "allocate",
    "format_number",
    "period_for",
    "preview",
    "request_fingerprint",
]
