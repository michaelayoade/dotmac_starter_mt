"""The authored OpenBao Agent configs are checked, never written (ruling R3).

D2 (a renderer in the agent) was replaced by app-authored templates (W11). The
enrolment kit installs them root-owned; the agent only `lstat`s each one (root
owner, not writable by group or others, in a root-owned directory that is not
writable by others) and REPORTS its digest, so the observation records exactly
which authored bytes the role will run with.
"""

from __future__ import annotations

import hashlib
import json
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


_DESTINATION = re.compile(r'(?m)^\s*destination\s*=\s*"([^"]+)"')
_VARIANT = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


def _live(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(("#", "//"))
    )


def rendered_file_set(content: str) -> frozenset[str]:
    """The file names an authored job config renders into `secrets/`. A config that
    also renders a `meta` file (the sealed-copy trigger) is refused: an import role
    never feeds the last-known-good path."""
    names: set[str] = set()
    for destination in _DESTINATION.findall(_live(content)):
        parts = destination.split("/")
        if "meta" in parts:
            raise Refusal("openbao_config_invalid", "meta_render")
        if "secrets" in parts:
            names.add(parts[-1])
    return frozenset(names)


#: ONE constant for the job render unit naming (P-SEC is still confirming it):
#: one authored static config and one `Type=oneshot` unit per (role, material set).
#: Serving roles keep `<secrets_namespace>-<role>`.
JOB_UNIT_NAMING = "<secrets_namespace>-<role>-<S12>"


def material_set_id(materials: tuple[str, ...]) -> str:
    """S12: the first 12 hex of sha256(canonical_json(sorted lower-case names))."""
    # COMPACT canonical JSON, pinned here on purpose: spaced separators would give a
    # different id (a50fd593917c for the two-name set) and the units would not match.
    names = sorted(m.lower() for m in materials)
    text = json.dumps(names, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:12]


def job_unit_stem(config: AgentConfig, role: str, materials: tuple[str, ...]) -> str:
    return f"{config.secrets_namespace}-{role}-{material_set_id(materials)}"


def job_config_stem(
    config: AgentConfig,
    role: str,
    materials: tuple[str, ...],
    *,
    required_uid: int = 0,
) -> str:
    """RQ-4/N3: the authored static config for THIS material set, or a refusal with
    nothing minted. Its rendered file set must equal the job's declared materials
    (an owner-only job never has the legacy DSN rendered). Returns the render unit's
    instance stem. The config passes the same root-owned and exit-settings checks as
    a serving config, and renders no `meta` file."""
    stem = job_unit_stem(config, role, materials)
    key = stem.removeprefix(config.secrets_namespace + "-")
    path = config_path(config, config.secrets_namespace, key)
    if not path.is_file():
        raise Refusal("job_files_invalid", "no_unit_for_set")
    check_authored_configs(
        config,
        config.secrets_namespace,
        (RoleSpec(key, True),),
        required_uid=required_uid,
    )
    content = read_regular(path, limit=LIMIT).decode("utf-8", errors="replace")
    if rendered_file_set(content) != frozenset(m.lower() for m in materials):
        raise Refusal("job_files_invalid", "config_set_mismatch")
    return stem
