"""Staffed inbox operations."""

from dotmac_inbox_operations.contracts import (
    AdmitToQueue,
    AssignConversation,
    AssignmentStatus,
    Conflict,
    CreateQueue,
    CreateRoutingRule,
    InboxOperationsError,
    PresenceState,
    PromoteFromQueue,
    QueueEntryStatus,
    SetAgentPresence,
)
from dotmac_inbox_operations.manifest import module
from dotmac_inbox_operations.migrations import versions_dir
from dotmac_inbox_operations.models import (
    ConversationAssignment,
    InboxAgentPresence,
    InboxQueue,
    InboxQueueEntry,
    InboxRoundRobinCursor,
    InboxRoutingRule,
    InboxWorkflowEvent,
)
from dotmac_inbox_operations.service import (
    admit_to_queue,
    assign_conversation,
    cancel_queue_entry,
    create_queue,
    create_routing_rule,
    next_round_robin_agent,
    promote_from_queue,
    set_agent_presence,
)

__version__ = "0.1.0a2"
__all__ = [
    "AdmitToQueue",
    "AssignConversation",
    "AssignmentStatus",
    "Conflict",
    "ConversationAssignment",
    "CreateQueue",
    "CreateRoutingRule",
    "InboxAgentPresence",
    "InboxOperationsError",
    "InboxQueue",
    "InboxQueueEntry",
    "InboxRoundRobinCursor",
    "InboxRoutingRule",
    "InboxWorkflowEvent",
    "PresenceState",
    "PromoteFromQueue",
    "QueueEntryStatus",
    "SetAgentPresence",
    "__version__",
    "admit_to_queue",
    "assign_conversation",
    "cancel_queue_entry",
    "create_queue",
    "create_routing_rule",
    "module",
    "next_round_robin_agent",
    "promote_from_queue",
    "set_agent_presence",
    "versions_dir",
]
