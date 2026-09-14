"""Public surface for ``dotmac-ai-operations``."""

from dotmac_ai_operations.advisory import (
    AIAcknowledgementAttribution,
    AIActionDecision,
    AIActionProposal,
    AIAdvisoryInvocation,
    AIAdvisoryResult,
    AICapabilityExposureRef,
    AIEvidenceBinding,
    AIExecutionObservation,
    InvalidCapabilityIdError,
    InvalidContractDigestError,
)
from dotmac_ai_operations.contracts import AIOperationIntent, AttemptInput, InsightInput
from dotmac_ai_operations.manifest import module
from dotmac_ai_operations.migrations import versions_dir
from dotmac_ai_operations.service import (
    AIOperationRefused,
    AuthoritativeAcknowledgement,
    acknowledge_insight,
    activate_policy_version,
    authoritative_acknowledgement,
    create_insight,
    create_policy,
    publish_policy_version,
    record_attempt,
    start_operation,
)

__version__ = "0.1.0a2"

__all__ = [
    "__version__",
    "AIAcknowledgementAttribution",
    "AIActionDecision",
    "AIActionProposal",
    "AIAdvisoryInvocation",
    "AIAdvisoryResult",
    "AICapabilityExposureRef",
    "AIEvidenceBinding",
    "AIExecutionObservation",
    "AIOperationIntent",
    "AIOperationRefused",
    "AttemptInput",
    "AuthoritativeAcknowledgement",
    "InsightInput",
    "InvalidCapabilityIdError",
    "InvalidContractDigestError",
    "acknowledge_insight",
    "activate_policy_version",
    "authoritative_acknowledgement",
    "create_insight",
    "create_policy",
    "module",
    "publish_policy_version",
    "record_attempt",
    "start_operation",
    "versions_dir",
]
