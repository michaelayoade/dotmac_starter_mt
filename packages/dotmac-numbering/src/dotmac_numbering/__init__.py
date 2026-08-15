"""Concurrency-safe allocation and formatting of configured document series.

The module allocates and formats. It does not decide what a number MEANS,
which documents require one, whether a series may have gaps, or when a fiscal
period begins — those belong to the owner issuing the document.

At-most-once execution is `dotmac_kernel.idempotency`'s, not this module's
(hard rule 23). The allocation receipt here is domain evidence: which number
was issued, for which period, against which business date.
"""

from dotmac_numbering.models import (
    IMMUTABLE_TABLES,
    NO_PERIOD,
    PLATFORM_TABLES,
    RESET_POLICIES,
    SCHEMA,
    TENANT_TABLES,
    AllocationReceipt,
    NumberSeries,
    PlatformAllocationReceipt,
    PlatformNumberSeries,
    PlatformSeriesCounter,
    PlatformSeriesRepair,
    SeriesCounter,
    SeriesRepair,
)
from dotmac_numbering.service import (
    ALLOCATE_SCOPE,
    MAX_DIGITS,
    MAX_VALUE,
    Allocation,
    NumberingError,
    Repair,
    SeriesConfiguration,
    advance_to_at_least,
    allocate,
    configure_series,
    format_number,
    period_for,
    preview,
)

__version__ = "0.1.0a1"

__all__ = [
    "ALLOCATE_SCOPE",
    "IMMUTABLE_TABLES",
    "MAX_DIGITS",
    "MAX_VALUE",
    "NO_PERIOD",
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
    "PlatformSeriesCounter",
    "PlatformSeriesRepair",
    "Repair",
    "SeriesConfiguration",
    "SeriesCounter",
    "SeriesRepair",
    "__version__",
    "advance_to_at_least",
    "allocate",
    "configure_series",
    "format_number",
    "period_for",
    "preview",
]
