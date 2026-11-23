"""The authored OpenBao Agent configs are checked, never written (ruling R3).

D2 (a renderer in the agent) was replaced by app-authored templates (W11). The
enrolment kit installs them root-owned; the agent only `lstat`s each one (root
owner, not writable by group or others, in a root-owned directory that is not
writable by others) and REPORTS its digest, so the observation records exactly
which authored bytes the role will run with.
"""

from __future__ import annotations

import re
import stat
from pathlib import Path

from .canon import digest
from .config import AgentConfig
from .files import read_regular
from .materials import RoleSpec
from .refusal import Refusal

LIMIT = 256 * 1024
#: "Continuously active" proves a working login ONLY if the OpenBao Agent EXITS
#: when authentication fails; by default `auto_auth` retries with backoff and the
#: process stays up. A config without BOTH settings could let a unit whose new
#: secret_id never worked pass the settle check, so its predecessors would be
#: destroyed while still in use.
REQUIRED_SETTINGS = ("exit_on_err", "exit_on_retry_failure")


def missing_exit_settings(text: str) -> list[str]:
    """Names of the required settings that are not set to `true` on a live
    (uncommented) line."""
    live = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(("#", "//"))
    )
    return [
        name
        for name in REQUIRED_SETTINGS
        if not re.search(rf"(?m)^\s*{name}\s*=\s*true\s*(?:#.*|//.*)?$", live)
    ]


def config_path(config: AgentConfig, secrets_namespace: str, role: str) -> Path:
    return config.openbao_config_dir / f"{secrets_namespace}-{role}.hcl"


def check_authored_configs(
    config: AgentConfig,
    secrets_namespace: str,
    roles: tuple[RoleSpec, ...],
    *,
    required_uid: int = 0,
) -> dict[str, str]:
    """`{role: sha256:<hex>}` for every role, or refuse."""
    digests: dict[str, str] = {}
    for spec in roles:
        path = config_path(config, secrets_namespace, spec.role)
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
            content = read_regular(path, limit=LIMIT)
        except Refusal:
            raise Refusal("openbao_config_invalid") from None
        missing = missing_exit_settings(content.decode("utf-8", errors="replace"))
        if missing:
            raise Refusal("openbao_config_invalid", missing[0])
        digests[spec.role] = digest(content)
    return digests
