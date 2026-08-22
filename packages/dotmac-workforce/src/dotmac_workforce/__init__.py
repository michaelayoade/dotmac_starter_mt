"""Workforce scheduling, capacity, and dispatch."""

from dotmac_workforce.contracts import (
    AddTeamMember,
    CertifyWorkerSkill,
    Conflict,
    CreateShift,
    CreateSkill,
    CreateTeam,
    DispatchWork,
    RecordAvailability,
    WorkforceError,
)
from dotmac_workforce.manifest import module
from dotmac_workforce.migrations import versions_dir
from dotmac_workforce.models import (
    DispatchDecision,
    TeamMembership,
    WorkerSkill,
    WorkforceAvailability,
    WorkforceShift,
    WorkforceSkill,
    WorkforceTeam,
)
from dotmac_workforce.service import (
    add_team_member,
    certify_worker_skill,
    create_shift,
    create_skill,
    create_team,
    dispatch_work,
    record_availability,
)

__version__ = "0.1.0a1"
__all__ = [
    "AddTeamMember",
    "CertifyWorkerSkill",
    "Conflict",
    "CreateShift",
    "CreateSkill",
    "CreateTeam",
    "DispatchDecision",
    "DispatchWork",
    "RecordAvailability",
    "TeamMembership",
    "WorkerSkill",
    "WorkforceAvailability",
    "WorkforceError",
    "WorkforceShift",
    "WorkforceSkill",
    "WorkforceTeam",
    "__version__",
    "add_team_member",
    "certify_worker_skill",
    "create_shift",
    "create_skill",
    "create_team",
    "dispatch_work",
    "module",
    "record_availability",
    "versions_dir",
]
