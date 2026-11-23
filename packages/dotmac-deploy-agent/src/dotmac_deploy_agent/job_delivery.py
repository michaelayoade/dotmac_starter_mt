"""Job-role secret_ids (A5, A7): minted for one dispatched job, tracked, purged.

Never called for a serving deploy, at boot or from a timer: the only caller is the
job dispatch path (waiting on Control's job-plan keys), and the stale purge only
ever REMOVES. The mint body is `metadata` only; the AppRole's own 15 min / 1 use
applies. The accessor is recorded BEFORE the file is written, so a crash cannot
leave a valid, untracked `secret_id` behind.
"""

from __future__ import annotations

import json
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
from .materials import ImageSpec
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
    try:
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
    except Refusal as refusal:
        if refusal.code == "secret_delivery_failed":
            # A refusal (403 or otherwise) of a JOB mint is never a delivery-identity
            # fault: it fails this job only. The serving roles, their files and
            # their accessors are untouched, and nothing is purged because of it.
            raise Refusal("job_mint_refused") from None
        raise  # the wrapping alarms keep their own codes
    if accessor is None:
        raise Refusal("secret_delivery_failed")  # untrackable: never write it
    state.retire.append(accessor)
    store.save(role, state)
    write_private(role_dir / SECRET_ID_FILE, secret_id.encode("utf-8"), 0o400)
    del secret_id
    return accessor[:8]


def purge_unit_name(secrets_namespace: str, role: str) -> str:
    return f"dotmac-job-purge@{secrets_namespace}-{role}.service"


def purge_job_role(
    config: AgentConfig,
    commands: Commands,
    store: AccessorStore,
    role: str,
    session: OpenBaoSession | None,
) -> int:
    """After a job, at agent start and before every mint.

    The agent removes only its OWN `secret_id`. The rendered files sit in
    `secrets/`, which the agent never lists, reads or deletes in: the static
    `dotmac-job-purge@<ns>-<role>` unit removes them as the container uid (started
    through the enrolled polkit rule). Then every tracked accessor is destroyed (a
    no-op once consumed, a kill if not). Each step runs even if the one before
    failed; an accessor that cannot be destroyed stays tracked. Raises after the
    steps if the purge unit could not be started."""
    try:
        remove_file(config.role_dir(role) / SECRET_ID_FILE)
    except OSError:
        raise Refusal("runtime_dir_unsafe") from None  # not ours to remove
    purged = (
        _systemctl(
            config, commands, "start", purge_unit_name(config.secrets_namespace, role)
        )
        is not None
    )
    state = store.load(role)
    kept = state.retire
    if session is not None:
        kept = [a for a in state.retire if not _destroy(session, config, role, a)]
        state.retire = kept
        store.save(role, state)
    LOG.info(
        "job role purged role=%s tracked=%d purge_unit=%s", role, len(kept), purged
    )
    if not purged:
        raise Refusal("job_files_invalid", "purge_unit_failed")
    return len(kept)


def render_job_credentials(
    config: AgentConfig,
    commands: Commands,
    role: str,
    names: tuple[str, ...],
    config_stem: str | None = None,
) -> tuple[str, ...]:
    """Render once, then verify WITHOUT listing or reading `secrets/`.

    Starts the static one-shot `openbao-agent@<ns>-<role>` (it logs in with the one
    secret_id, renders and exits), requires `Result=success` and `ExecMainStatus=0`
    from `systemctl show` as the render-and-login signal, then `lstat`s each
    DECLARED `secrets/<lower(name)>`: a regular, non-empty file, mode 0400, owned
    by the enrolled uid. Narrowing to the declared files is Foundation's per-file
    read-only mounts, not a deletion here. Returns the verified names."""
    unit = (
        f"openbao-agent@{config_stem}.service"
        if config_stem
        else unit_name(config.secrets_namespace, role)
    )
    if _systemctl(config, commands, "start", unit) is None:
        raise Refusal("job_files_invalid", "render_unit_failed")
    shown = _systemctl(
        config, commands, "show", "--property=Result,ExecMainStatus", unit
    )
    fields = dict(
        line.split("=", 1)
        for line in (shown or b"").decode("utf-8", "replace").splitlines()
        if "=" in line
    )
    if fields.get("Result") != "success" or fields.get("ExecMainStatus") != "0":
        raise Refusal("job_files_invalid", "render_not_successful")
    uid = config.job_secrets_uid if config.job_secrets_uid is not None else os.geteuid()
    secrets = config.role_dir(role) / "secrets"
    verified: list[str] = []
    for material in names:
        name = material.lower()
        if not _FILENAME.fullmatch(name):
            raise Refusal("job_files_invalid")
        try:
            info = (secrets / name).lstat()
        except OSError:
            raise Refusal("job_files_invalid") from None
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o400
            or info.st_uid != uid
            or info.st_size == 0
        ):
            raise Refusal("job_files_invalid")
        verified.append(name)
    return tuple(verified)


def check_images_present(
    config: AgentConfig, commands: Commands, images: tuple[ImageSpec, ...]
) -> None:
    """A role job runs an image that is already on the host: confirm each planned
    image by DIGEST with a read-only `docker image inspect`, or refuse. Nothing is
    transferred or pulled."""
    for image in images:
        reference = f"{image.planned_repository}@{image.planned_digest}"
        try:
            result = commands.run(
                (
                    str(config.docker_path),
                    "image",
                    "inspect",
                    "--format",
                    "{{json .RepoDigests}}",
                    reference,
                ),
                timeout=60.0,
            )
            listed = json.loads(result.stdout) if result.exit_code == 0 else None
        except (Refusal, ValueError):
            raise Refusal("job_image_absent") from None
        if not isinstance(listed, list) or reference not in listed:
            raise Refusal("job_image_absent")
