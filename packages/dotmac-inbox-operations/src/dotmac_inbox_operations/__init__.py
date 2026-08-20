"""Staffed inbox operations."""

from dotmac_inbox_operations.contracts import (
    AssignConversation,
    AssignmentStatus,
    Conflict,
    CreateQueue,
    CreateRoutingRule,
    InboxOperationsError,
    PresenceState,
    SetAgentPresence,
)
from dotmac_inbox_operations.manifest import module
from dotmac_inbox_operations.migrations import versions_dir
from dotmac_inbox_operations.models import (
    ConversationAssignment,
    InboxAgentPresence,
    InboxQueue,
    InboxRoutingRule,
    InboxWorkflowEvent,
)
from dotmac_inbox_operations.service import (
    assign_conversation,
    create_queue,
    create_routing_rule,
    set_agent_presence,
)

__version__ = "0.1.0a1"
__all__ = [
    "AssignConversation",
    "AssignmentStatus",
    "Conflict",
    "ConversationAssignment",
    "CreateQueue",
    "CreateRoutingRule",
    "InboxAgentPresence",
    "InboxOperationsError",
    "InboxQueue",
    "InboxRoutingRule",
    "InboxWorkflowEvent",
    "PresenceState",
    "SetAgentPresence",
    "__version__",
    "assign_conversation",
    "create_queue",
    "create_routing_rule",
    "module",
    "set_agent_presence",
    "versions_dir",
]
