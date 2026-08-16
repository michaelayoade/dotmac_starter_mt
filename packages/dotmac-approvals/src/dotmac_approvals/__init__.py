"""`dotmac-approvals` — one decision, and never the transition that follows.

> Has the required set of eligible actors approved **this exact content** under
> **this exact policy revision**, and is that decision still valid?

That is the whole of it. The module answers
`pending | approved | rejected | cancelled` and emits an event; the subject's
owner performs its own guarded transition. Approving a payment does not post it.

Boundary: ADR-0026, "Approvals decide approval, never the transition".
Source evidence: the A1 audit and its 24-row disposition ledger under
`docs/inventories/`.

Public surface, by file:

- `contracts` — the frozen vocabulary and every typed refusal;
- `policy` — the rules, pure and shared by both planes;
- `service` — the two explicitly named plane surfaces;
- `outbox` — the optional adapter onto the kernel's transactional outbox;
- `models`, `manifest` — persistence and registration;
- `migrations` — `versions_dir()`, where a consuming assembly finds the `ap`
  lineage to compose into its `version_locations`.

What this package deliberately does not contain: Money, currency or FX (routing
is the domain's — ADR-0026 § 7a), a subject-type vocabulary (the consuming
module declares its own — § 4), and any import of a consuming domain.
"""

from dotmac_approvals.contracts import (
    Actor,
    ApprovalError,
    ApprovalEvent,
    ApprovalLevel,
    ApprovalState,
    ApproverKind,
    ContentChanged,
    DecisionAction,
    DuplicateDecision,
    Evaluation,
    InvalidPolicy,
    MFARequired,
    NotEligible,
    NotRequester,
    PolicyNotFound,
    PolicyRevision,
    PolicyVersionExists,
    RecordedDecision,
    RequestNotPending,
    SelfApprovalRefused,
    SoDRule,
    SoDViolation,
)
from dotmac_approvals.manifest import module
from dotmac_approvals.migrations import versions_dir

__version__ = "0.1.0a5"

__all__ = [
    "Actor",
    "ApprovalError",
    "ApprovalEvent",
    "ApprovalLevel",
    "ApprovalState",
    "ApproverKind",
    "ContentChanged",
    "DecisionAction",
    "DuplicateDecision",
    "Evaluation",
    "InvalidPolicy",
    "MFARequired",
    "NotEligible",
    "NotRequester",
    "PolicyNotFound",
    "PolicyRevision",
    "PolicyVersionExists",
    "RecordedDecision",
    "RequestNotPending",
    "SelfApprovalRefused",
    "SoDRule",
    "SoDViolation",
    "__version__",
    "module",
    "versions_dir",
]
