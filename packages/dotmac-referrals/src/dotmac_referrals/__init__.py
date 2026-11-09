"""Public typed surface for ``dotmac-referrals``."""

from dotmac_referrals.contracts import (
    SERVICE_LOCATION_REF_PATTERN,
    CaptureReferral,
    CaptureReferralV2,
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
    capture_referral_v2,
    create_programme,
    issue_code,
    record_conversion,
)

__version__ = "0.1.0a2"

__all__ = [
    "SERVICE_LOCATION_REF_PATTERN",
    "CaptureReferral",
    "CaptureReferralV2",
    "Conflict",
    "ContractError",
    "CreateProgramme",
    "IssueCode",
    "NotFound",
    "RecordConversion",
    "ReferralError",
    "__version__",
    "capture_referral",
    "capture_referral_v2",
    "create_programme",
    "issue_code",
    "module",
    "record_conversion",
    "versions_dir",
]
