"""The authored OpenBao Agent configs are checked, never written (ruling R3).

D2 (a renderer in the agent) was replaced by app-authored templates (W11). The
enrolment kit installs them root-owned; the agent only `lstat`s each one (root
owner, not writable by group or others, in a root-owned directory that is not
writable by others) and REPORTS its digest, so the observation records exactly
which authored bytes the role will run with.
"""

from __future__ import annotations

import stat
from pathlib import Path

from .canon import digest
from .config import AgentConfig
from .files import read_regular
from .materials import RoleSpec
from .refusal import Refusal

LIMIT = 256 * 1024


def config_path(config: AgentConfig, role: str) -> Path:
    return config.openbao_config_dir / f"{config.product}-{role}.hcl"


def check_authored_configs(
    config: AgentConfig, roles: tuple[RoleSpec, ...], *, required_uid: int = 0
) -> dict[str, str]:
    """`{role: sha256:<hex>}` for every role, or refuse."""
    digests: dict[str, str] = {}
    for spec in roles:
        path = config_path(config, spec.role)
        try:
            info = path.lstat()
            parent = path.parent.lstat()
        except OSError:
            raise Refusal("openbao_config_invalid") from None
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != required_uid
            or info.st_mode & 0o022
            or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != required_uid
            or parent.st_mode & 0o022
        ):
            raise Refusal("openbao_config_invalid")
        try:
            digests[spec.role] = digest(read_regular(path, limit=LIMIT))
        except Refusal:
            raise Refusal("openbao_config_invalid") from None
    return digests
