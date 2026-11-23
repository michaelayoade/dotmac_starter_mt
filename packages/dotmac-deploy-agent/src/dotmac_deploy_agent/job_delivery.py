"""Job-role secret_ids (A5, A7): minted for one dispatched job, tracked, purged.

Never called for a serving deploy, at boot or from a timer: the only caller is the
job dispatch path (waiting on Control's job-plan keys), and the stale purge only
ever REMOVES. The mint body is `metadata` only; the AppRole's own 15 min / 1 use
applies. The accessor is recorded BEFORE the file is written, so a crash cannot
leave a valid, untracked `secret_id` behind.
"""

from __future__ import annotations

import logging
import os
import re
import stat
from typing import Final

from .commands import Commands
from .config import AgentConfig
from .delivery import (
    SECRET_ID_FILE,
    _destroy,
    _require_role_dir,
    _systemctl,
    unit_name,
)
from .files import remove_file, write_private
from .journal import AccessorStore
from .openbao import OpenBaoSession, mint_path
from .refusal import Refusal

LOG = logging.getLogger(__name__)
_FILENAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def deliver_job_secret_id(
    session: OpenBaoSession,
    config: AgentConfig,
    role: str,
    store: AccessorStore,
    *,
    dispatch_id: str,
) -> str:
    """Mint, verify, unwrap and write one job `secret_id`; returns the accessor's
    8-character prefix (the receipt never carries more)."""
    ns, env = config.secrets_namespace, config.secrets_environment
    role_dir = config.role_dir(role)
    _require_role_dir(role_dir)
    state = store.load(role)
    if state.retire:
        # A previous run's accessor is still live: one job at a time, never stack.
        raise Refusal("secret_delivery_failed")
    wrapping = session.mint_wrapped(
        secrets_namespace=ns,
        secrets_environment=env,
        role=role,
        single_use=True,
        ttl_seconds=0,  # unused: a job mint sends metadata only
        wrap_ttl_seconds=config.wrap_ttl_seconds,
        dispatch_id=dispatch_id,
        job=True,
    )
    session.require_creation_path(wrapping, mint_path(ns, env, role))
    secret_id, accessor = session.unwrap(wrapping)
    if accessor is None:
        raise Refusal("secret_delivery_failed")  # untrackable: never write it
    state.retire.append(accessor)
    store.save(role, state)
    write_private(role_dir / SECRET_ID_FILE, secret_id.encode("utf-8"), 0o400)
    del secret_id
    return accessor[:8]


def purge_job_role(
    config: AgentConfig,
    store: AccessorStore,
    role: str,
    session: OpenBaoSession | None,
    rendered: tuple[str, ...] = (),
) -> int:
    """Remove the `secret_id` and the named rendered files, then destroy every
    tracked accessor (a no-op once consumed, a kill if not). Files go even when
    OpenBao is down; an accessor that cannot be destroyed stays tracked."""
    role_dir = config.role_dir(role)
    for name in (SECRET_ID_FILE, *rendered):
        if not _FILENAME.fullmatch(name):
            raise Refusal("runtime_dir_unsafe")  # never a path
        try:
            remove_file(role_dir / name)
        except OSError:
            raise Refusal("runtime_dir_unsafe") from None  # not ours to remove
    state = store.load(role)
    if session is None:
        return len(state.retire)
    kept = [a for a in state.retire if not _destroy(session, config, role, a)]
    state.retire = kept
    store.save(role, state)
    LOG.info("job role purged role=%s tracked=%d", role, len(kept))
    return len(kept)


def render_job_credentials(
    config: AgentConfig,
    commands: Commands,
    role: str,
    names: tuple[str, ...],
) -> tuple[str, ...]:
    """Render once, then verify. Starts the static `openbao-agent@<ns>-<role>`
    (it logs in with the one secret_id, renders and exits) and checks every named
    file: a regular file, mode 0400, owned by the enrolled uid. The role directory
    is the job's: this only reads it. Returns the verified file names (never
    values)."""
    if (
        _systemctl(config, commands, "start", unit_name(config.secrets_namespace, role))
        is None
    ):
        raise Refusal("job_files_invalid")
    uid = config.job_secrets_uid if config.job_secrets_uid is not None else os.geteuid()
    role_dir = config.role_dir(role)
    verified: list[str] = []
    for material in names:
        name = material.lower()
        if not _FILENAME.fullmatch(name):
            raise Refusal("job_files_invalid")
        try:
            info = (role_dir / name).lstat()
        except OSError:
            raise Refusal("job_files_invalid") from None
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o400
            or info.st_uid != uid
        ):
            raise Refusal("job_files_invalid")
        verified.append(name)
    return tuple(verified)
