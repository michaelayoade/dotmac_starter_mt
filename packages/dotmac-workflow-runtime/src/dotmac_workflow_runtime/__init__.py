"""Public typed surface for ``dotmac-workflow-runtime``."""

from dotmac_workflow_runtime.contracts import (
    ContractError,
    RepairCommand,
    SettleCheckpoint,
    StartExecution,
)
from dotmac_workflow_runtime.manifest import module
from dotmac_workflow_runtime.migrations import versions_dir
from dotmac_workflow_runtime.service import (
    CheckpointUnavailable,
    ExecutionReceipt,
    ExecutionUnavailable,
    WorkflowConflict,
    WorkflowError,
    claim_checkpoint,
    record_repair,
    settle_checkpoint,
    start_execution,
)

__version__ = "0.1.0a1"

__all__ = [
    "CheckpointUnavailable",
    "ContractError",
    "ExecutionReceipt",
    "ExecutionUnavailable",
    "RepairCommand",
    "SettleCheckpoint",
    "StartExecution",
    "WorkflowConflict",
    "WorkflowError",
    "__version__",
    "claim_checkpoint",
    "module",
    "record_repair",
    "settle_checkpoint",
    "start_execution",
    "versions_dir",
]
