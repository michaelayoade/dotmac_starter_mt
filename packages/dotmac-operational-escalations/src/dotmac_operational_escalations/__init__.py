"""Versioned operational escalation policy and instance owner."""

from dotmac_operational_escalations.contracts import (
    Conflict,
    DraftPolicyVersion,
    EscalationError,
    EscalationStatus,
    PolicyVersionState,
    RaiseEscalation,
    RegisterPolicy,
    SettleEscalation,
)
from dotmac_operational_escalations.manifest import module
from dotmac_operational_escalations.migrations import versions_dir
from dotmac_operational_escalations.models import (
    EscalationInstance,
    EscalationPolicy,
    EscalationPolicyVersion,
    PolicyVersionImmutableError,
)
from dotmac_operational_escalations.service import (
    acknowledge_escalation,
    activate_policy_version,
    cancel_escalation,
    draft_policy_version,
    raise_escalation,
    register_policy,
    resolve_escalation,
)

__version__ = "0.1.0a1"
__all__ = [
    "Conflict",
    "DraftPolicyVersion",
    "EscalationError",
    "EscalationInstance",
    "EscalationPolicy",
    "EscalationPolicyVersion",
    "EscalationStatus",
    "PolicyVersionImmutableError",
    "PolicyVersionState",
    "RaiseEscalation",
    "RegisterPolicy",
    "SettleEscalation",
    "__version__",
    "acknowledge_escalation",
    "activate_policy_version",
    "cancel_escalation",
    "draft_policy_version",
    "module",
    "raise_escalation",
    "register_policy",
    "resolve_escalation",
    "versions_dir",
]
