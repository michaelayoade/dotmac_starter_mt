"""D1 `secret_id` delivery (ruling R2, P-SEC s3.5-3.7). No chown, no capability.

For each role the agent writes `/run/dotmac/<secrets_namespace>/<role>/secret_id`,
0400, as its OWN uid, then restarts `openbao-agent@<secrets_namespace>-<role>.service`
through the enrolled polkit rule. ONE naming key, `secrets_namespace`, names every
per-role host artefact (role dir, unit, authored HCL, drop-in, tmpfiles): it is
pinned in the root-owned config and the signed plan must equal it. That unit runs
as the container uid and reads the file only through `LoadCredential=secret_id:`.

## Accessor lifecycle (no valid id is ever left untracked, none destroyed early)

1. After unwrap, the new accessor is recorded as the `candidate` BEFORE the file
   is written or the unit restarted. A restart that fails destroys it.
2. Older accessors (`current`, an earlier candidate) move to `retire`. They are
   NOT destroyed in this run: `systemctl is-active` is true right after exec, long
   before a login can have succeeded.
3. On a LATER cycle, once the unit has been continuously active since the restart
   (same `NRestarts` and `ActiveEnterTimestampMonotonic`, for `settle_seconds`),
   the candidate is promoted and every `retire` accessor is destroyed.
4. State that cannot be read is a refusal, never "empty".

A single-use (migrate) id is consumed by its one login and is not tracked; the
launcher removes its file after the run.
"""

from __future__ import annotations

import logging
import os
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .commands import Commands
from .config import AgentConfig
from .files import remove_file, write_private
from .journal import AccessorStore, Candidate
from .materials import RoleSpec
from .openbao import OpenBaoSession, mint_path
from .refusal import Refusal

LOG = logging.getLogger("dotmac.deploy_agent")
SECRET_ID_FILE = "secret_id"  # noqa: S105  # nosec B105 - a file name, not a value
_SHOW = (
    "--property=ActiveState",
    "--property=NRestarts",
    "--property=ActiveEnterTimestampMonotonic",
    "--property=ActiveEnterTimestamp",
    "--timestamp=unix",
)


@dataclass(frozen=True)
class Delivered:
    """Names and booleans only. This is what may enter an observation."""

    role: str
    single_use: bool
    restarted: bool


def unit_name(secrets_namespace: str, role: str) -> str:
    """`openbao-agent@<secrets_namespace>-<role>.service`: the P-SEC instance name."""
    return f"openbao-agent@{secrets_namespace}-{role}.service"


def _require_role_dir(path: Path) -> None:
    """tmpfiles.d made it; the agent owns it and it is traverse-only."""
    try:
        info = path.lstat()
    except OSError:
        raise Refusal("runtime_dir_unsafe") from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o066
    ):
        raise Refusal("runtime_dir_unsafe")


def _systemctl(
    config: AgentConfig, commands: Commands, *args: str, timeout: float = 60.0
) -> bytes | None:
    try:
        result = commands.run(
            (str(config.systemctl_path), "--no-ask-password", *args), timeout=timeout
        )
    except Refusal:
        return None
    return result.stdout if result.exit_code == 0 else None


def unit_state(
    config: AgentConfig, commands: Commands, unit: str
) -> dict[str, str] | None:
    out = _systemctl(config, commands, "show", *_SHOW, unit)
    if out is None:
        return None
    state: dict[str, str] = {}
    for line in out.decode("utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            state[key] = value
    return state


def active_enter_epoch(info: dict[str, str] | None) -> int | None:
    """When the unit last entered `active`, as epoch seconds (`@N` under
    `--timestamp=unix`), or None when unknown."""
    value = (info or {}).get("ActiveEnterTimestamp", "")
    if value.startswith("@") and value[1:].isdigit():
        return int(value[1:])
    return None


def _promote_issued(
    config: AgentConfig, commands: Commands, role: str, candidate: Candidate
) -> Candidate | None:
    """A crash after a SUCCESSFUL restart but before the `restarted` save leaves an
    `issued` candidate that the unit is actually running. If the unit entered
    `active` at or after the candidate was minted (or that cannot be told), treat
    it as running: record it as `restarted` so it is superseded, not destroyed.
    Returns None when the unit never re-entered `active` after the mint."""
    info = unit_state(config, commands, unit_name(config.secrets_namespace, role))
    entered = active_enter_epoch(info)
    if entered is not None and entered < candidate.at:
        return None
    return Candidate(
        candidate.accessor,
        "restarted",
        candidate.at,
        (info or {}).get("NRestarts"),
        (info or {}).get("ActiveEnterTimestampMonotonic"),
    )


def _destroy(
    session: OpenBaoSession, config: AgentConfig, role: str, accessor: str
) -> bool:
    try:
        session.destroy_accessor(
            secrets_namespace=config.secrets_namespace,
            secrets_environment=config.secrets_environment,
            role=role,
            accessor=accessor,
        )
    except Refusal:
        return False
    return True


def deliver_secret_ids(
    session: OpenBaoSession,
    config: AgentConfig,
    roles: tuple[RoleSpec, ...],
    commands: Commands,
    *,
    store: AccessorStore,
    dispatch_id: str,
    clock: Callable[[], float] = time.time,
) -> tuple[Delivered, ...]:
    ns, env = config.secrets_namespace, config.secrets_environment
    delivered: list[Delivered] = []
    for spec in roles:
        role_dir = config.role_dir(spec.role)
        _require_role_dir(role_dir)
        target = role_dir / SECRET_ID_FILE
        unit = unit_name(ns, spec.role)
        state = store.load(spec.role)  # refuses corrupt state

        if not spec.single_use:
            # A candidate stuck at `issued` is a crash between unwrap and a
            # confirmed restart. If the unit re-entered `active` after the mint it
            # IS running it: promote (it is superseded below, destroyed only once
            # a newer id is stable). Otherwise the unit never saw it: drop the
            # file and the accessor.
            if state.candidate is not None and state.candidate.stage == "issued":
                promoted = _promote_issued(config, commands, spec.role, state.candidate)
                if promoted is not None:
                    state.candidate = promoted
                else:
                    remove_file(target)
                    if not _destroy(
                        session, config, spec.role, state.candidate.accessor
                    ):
                        state.retire.append(state.candidate.accessor)
                    state.candidate = None
            # Everything the unit may be running is now older than what we mint.
            superseded = [state.current] if state.current else []
            if state.candidate is not None:
                superseded.append(state.candidate.accessor)
            state.retire = sorted({*state.retire, *superseded})
            state.current, state.candidate = None, None
            store.save(spec.role, state)

        wrapping = session.mint_wrapped(
            secrets_namespace=ns,
            secrets_environment=env,
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
        session.require_creation_path(wrapping, mint_path(ns, env, spec.role))
        secret_id, accessor = session.unwrap(wrapping)
        if not spec.single_use:
            if accessor is None:
                raise Refusal("secret_delivery_failed")
            # Tracked BEFORE the file or the restart: a crash here cannot leave a
            # valid, untracked secret_id behind.
            state.candidate = Candidate(accessor, "issued", int(clock()))
            store.save(spec.role, state)
        write_private(target, secret_id.encode("utf-8"), 0o400)
        del secret_id

        if _systemctl(config, commands, "restart", unit) is None:
            remove_file(target)  # no file for a unit nobody restarted
            if state.candidate is not None:
                if not _destroy(session, config, spec.role, state.candidate.accessor):
                    state.retire.append(state.candidate.accessor)
                state.candidate = None
                store.save(spec.role, state)
            raise Refusal("secret_delivery_failed")
        if state.candidate is not None:
            info = unit_state(config, commands, unit) or {}
            state.candidate = Candidate(
                state.candidate.accessor,
                "restarted",
                int(clock()),
                info.get("NRestarts"),
                info.get("ActiveEnterTimestampMonotonic"),
            )
            store.save(spec.role, state)
        delivered.append(Delivered(spec.role, spec.single_use, True))
    return tuple(delivered)


def remove_single_use_files(config: AgentConfig, roles: tuple[RoleSpec, ...]) -> None:
    """A migrate secret_id is spent by its one login: do not leave the file."""
    for spec in roles:
        if spec.single_use:
            remove_file(config.role_dir(spec.role) / SECRET_ID_FILE)


def _stable(
    config: AgentConfig, commands: Commands, role: str, candidate: Candidate, now: int
) -> bool:
    if (
        candidate.stage != "restarted"
        or candidate.nrestarts is None
        or candidate.active_enter is None
        or now - candidate.at < config.settle_seconds
    ):
        return False
    info = unit_state(config, commands, unit_name(config.secrets_namespace, role))
    return (
        info is not None
        and info.get("ActiveState") == "active"
        and info.get("NRestarts") == candidate.nrestarts
        and info.get("ActiveEnterTimestampMonotonic") == candidate.active_enter
    )


def settle_accessors(
    open_session: Callable[[], OpenBaoSession],
    config: AgentConfig,
    commands: Commands,
    store: AccessorStore,
    *,
    clock: Callable[[], float] = time.time,
) -> int:
    """Later-cycle retirement: promote a candidate the unit has run on, unbroken,
    for `settle_seconds`, then destroy every older accessor. Returns the number
    destroyed. A unit that failed, restarted or is not yet old enough keeps its
    predecessors valid."""
    now = int(clock())
    eligible: list[str] = []
    for role in store.roles():
        state = store.load(role)
        if state.candidate is not None and state.candidate.stage == "issued":
            promoted = _promote_issued(config, commands, role, state.candidate)
            if promoted is not None:  # the unit is running it: track that
                state.candidate = promoted
                store.save(role, state)
        if state.candidate is not None and _stable(
            config, commands, role, state.candidate, now
        ):
            eligible.append(role)
    if not eligible:
        return 0
    session = open_session()
    destroyed = 0
    try:
        for role in eligible:
            state = store.load(role)
            if state.candidate is None:
                continue  # nothing to promote (state changed under us)
            kept: list[str] = []
            # Promoting the candidate replaces `current`: the id it held is older
            # than a candidate the unit has now run on, so it retires too, never
            # silently forgotten while still valid.
            older = list(state.retire)
            if state.current and state.current != state.candidate.accessor:
                older.append(state.current)
            for old in sorted(set(older)):
                if _destroy(session, config, role, old):
                    destroyed += 1
                else:
                    kept.append(old)
            state.current = state.candidate.accessor
            state.candidate, state.retire = None, kept
            store.save(role, state)
            LOG.info("accessors settled role=%s destroyed_total=%d", role, destroyed)
    finally:
        session.revoke()
    return destroyed
