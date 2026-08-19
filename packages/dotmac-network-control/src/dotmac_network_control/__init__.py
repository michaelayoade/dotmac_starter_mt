"""Public Network Control service surface."""

from dotmac_network_control.manifest import module
from dotmac_network_control.migrations import versions_dir
from dotmac_network_control.service import (
    ControlConflict,
    ControlError,
    ControlNotFound,
    approve_command,
    lookup_commands,
    mark_dispatched,
    query_dispatches,
    query_execution_evidence,
    reconcile_command,
    record_execution_observation,
    recover_command,
    reject_command,
    request_command,
)

__version__ = "0.1.0a1"
from dotmac_network_control.models import (
    ALL_MODELS,
    SCHEMA,
)

from dotmac_network_control.contracts import (
    CommandState,
    ExecutionOutcome,
    MarkDispatched,
    RecordExecutionObservation,
    RequestCommand,
)

__all__ = [
    "__version__",
    "ALL_MODELS",
    "approve_command",
    "CommandState",
    "ControlConflict",
    "ControlError",
    "ControlNotFound",
    "ExecutionOutcome",
    "lookup_commands",
    "mark_dispatched",
    "MarkDispatched",
    "module",
    "query_dispatches",
    "query_execution_evidence",
    "reconcile_command",
    "record_execution_observation",
    "RecordExecutionObservation",
    "recover_command",
    "reject_command",
    "request_command",
    "RequestCommand",
    "SCHEMA",
    "versions_dir",
]
