"""Engine-free transaction mechanics for caller-owned sessions.

Applications own their engines, session factories, and outer transaction
boundaries.  Services that receive one of those sessions may use this public
module to isolate an expected conflict without importing :mod:`dotmac_kernel.db`
and constructing the kernel reference assembly's database runtime.
"""

from dotmac_kernel._transactions import (
    ConflictSavepointPhase,
    ConflictSavepointProtocolError,
    ConflictSavepointState,
    claim_conflict_savepoint_commit,
    conflict_savepoint,
    conflict_savepoint_requires_rollback,
    current_conflict_savepoint,
)

__all__ = [
    "ConflictSavepointPhase",
    "ConflictSavepointProtocolError",
    "ConflictSavepointState",
    "claim_conflict_savepoint_commit",
    "conflict_savepoint",
    "conflict_savepoint_requires_rollback",
    "current_conflict_savepoint",
]
