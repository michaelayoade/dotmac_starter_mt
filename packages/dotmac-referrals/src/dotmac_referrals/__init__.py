"""Public typed surface for ``dotmac-referrals``."""

from dotmac_referrals.contracts import (
    CaptureReferral,
    ContractError,
    CreateProgramme,
    IssueCode,
    RecordConversion,
)
from dotmac_referrals.manifest import module
from dotmac_referrals.migrations import versions_dir
from dotmac_referrals.service import (
    Conflict,
    NotFound,
    ReferralError,
    capture_referral,
    create_programme,
    issue_code,
    record_conversion,
)

__version__ = "0.1.0a1"

__all__ = [
    "CaptureReferral",
    "Conflict",
    "ContractError",
    "CreateProgramme",
    "IssueCode",
    "NotFound",
    "RecordConversion",
    "ReferralError",
    "__version__",
    "capture_referral",
    "create_programme",
    "issue_code",
    "module",
    "record_conversion",
    "versions_dir",
]
