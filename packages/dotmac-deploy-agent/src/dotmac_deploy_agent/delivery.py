"""D1 `secret_id` delivery (ruling R2, P-SEC s3.5-3.7). No chown, no capability.

For each role the agent writes `/run/dotmac/<product>/<role>/secret_id`, 0400, as
its OWN uid, then restarts `openbao-agent@<product>-<role>.service` through the
enrolled polkit rule. That unit runs as the container uid and reads the file only
through `LoadCredential=secret_id:<that path>`, a per-unit ramfs copy. Only the
OpenBao Agent ever reads a `secret_id`; the container reads rendered values.

The role directory is created at enrolment by `tmpfiles.d`; the agent never
creates it, and never takes a uid from a dispatch (the uid is an enrolment fact,
the unit's `User=`).

After the restart is confirmed, the PREVIOUS accessor is destroyed so a
delivered `secret_id` is never left valid beside its replacement.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .commands import Commands
from .config import AgentConfig
from .files import ensure_private_dir, remove_file, write_private
from .materials import RoleSpec
from .openbao import OpenBaoSession, mint_path
from .refusal import Refusal

SECRET_ID_FILE = "secret_id"  # noqa: S105  # nosec B105 - a file name, not a value


@dataclass(frozen=True)
class Delivered:
    """Names and booleans only. This is what may enter an observation."""

    role: str
    single_use: bool
    restarted: bool
    old_accessors_destroyed: int


def unit_name(secrets_namespace: str, role: str) -> str:
    """`openbao-agent@<secrets_namespace>-<role>.service`: the P-SEC instance name."""
    return f"openbao-agent@{secrets_namespace}-{role}.service"


def _require_role_dir(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError:
        raise Refusal("runtime_dir_unsafe") from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o022
    ):
        raise Refusal("runtime_dir_unsafe")


def _systemctl(
    config: AgentConfig, commands: Commands, *args: str, timeout: float = 60.0
) -> bool:
    try:
        result = commands.run(
            (str(config.systemctl_path), "--no-ask-password", *args), timeout=timeout
        )
    except Refusal:
        return False
    return result.exit_code == 0


def _accessor_file(config: AgentConfig, role: str) -> Path:
    return config.state_dir / "accessors" / f"{role}.json"


def _load_accessors(path: Path) -> list[str]:
    try:
        raw = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return []
    return [a for a in raw if isinstance(a, str)] if isinstance(raw, list) else []


def deliver_secret_ids(
    session: OpenBaoSession,
    config: AgentConfig,
    roles: tuple[RoleSpec, ...],
    commands: Commands,
    *,
    secrets_namespace: str,
    secrets_environment: str,
    dispatch_id: str,
) -> tuple[Delivered, ...]:
    ensure_private_dir(config.state_dir / "accessors")
    delivered: list[Delivered] = []
    for spec in roles:
        role_dir = config.run_dir / spec.role
        _require_role_dir(role_dir)
        wrapping = session.mint_wrapped(
            secrets_namespace=secrets_namespace,
            secrets_environment=secrets_environment,
            role=spec.role,
            single_use=spec.single_use,
            ttl_seconds=(
                config.migrate_secret_id_ttl_seconds
                if spec.single_use
                else config.runtime_secret_id_ttl_seconds
            ),
            wrap_ttl_seconds=config.wrap_ttl_seconds,
            dispatch_id=dispatch_id,
        )
        session.require_creation_path(
            wrapping, mint_path(secrets_namespace, secrets_environment, spec.role)
        )
        secret_id, accessor = session.unwrap(wrapping)
        target = role_dir / SECRET_ID_FILE
        write_private(target, secret_id.encode("utf-8"), 0o400)
        del secret_id

        unit = unit_name(secrets_namespace, spec.role)
        restarted = _systemctl(config, commands, "restart", unit)
        if not restarted:
            remove_file(target)  # a delivered id nobody consumed is not left behind
            raise Refusal("secret_delivery_failed")
        confirmed = _systemctl(config, commands, "is-active", "--quiet", unit)
        destroyed = 0
        if not spec.single_use and accessor is not None:
            destroyed = _rotate_accessor(
                session,
                config,
                (secrets_namespace, secrets_environment),
                spec.role,
                accessor,
                confirmed=confirmed,
            )
        delivered.append(Delivered(spec.role, spec.single_use, restarted, destroyed))
    return tuple(delivered)


def _rotate_accessor(
    session: OpenBaoSession,
    config: AgentConfig,
    naming: tuple[str, str],
    role: str,
    new_accessor: str,
    *,
    confirmed: bool,
) -> int:
    """Destroy older accessors once the Agent is up; keep the ones that could not
    be, so none is ever forgotten while still valid."""
    path = _accessor_file(config, role)
    pending = [a for a in _load_accessors(path) if a != new_accessor]
    kept: list[str] = []
    destroyed = 0
    for old in pending:
        if not confirmed:
            kept.append(old)
            continue
        try:
            session.destroy_accessor(
                secrets_namespace=naming[0],
                secrets_environment=naming[1],
                role=role,
                accessor=old,
            )
            destroyed += 1
        except Refusal:
            kept.append(old)
    write_private(path, json.dumps([*kept, new_accessor]).encode("utf-8"), 0o600)
    return destroyed
