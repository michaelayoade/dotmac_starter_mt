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
import re
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
    return f"dotmac-secrets-purge@{secrets_namespace}-{role}.service"


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
    `dotmac-secrets-purge@<ns>-<role>` unit removes them as the container uid (started
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
            config,
            commands,
            "start",
            purge_unit_name(config.secrets_namespace, role),
            timeout=PURGE_START_TIMEOUT,
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


#: Setup plus teardown of a job (mint, render, verify, purge, accessor destroy) has
#: to fit the 120 s lease margin. The systemd steps carry hard timeouts that sum to
#: well under it, leaving the rest for the OpenBao round trips (about eight calls).
RENDER_START_TIMEOUT: Final = 30.0
SHOW_TIMEOUT: Final = 5.0
PURGE_START_TIMEOUT: Final = 20.0  # runs twice: before the mint and after the job
LOADED_TIMEOUT: Final = 5.0
SYSTEMD_BUDGET_SECONDS: Final = (
    LOADED_TIMEOUT + RENDER_START_TIMEOUT + SHOW_TIMEOUT + 2 * PURGE_START_TIMEOUT
)
_INVOCATION: Final = re.compile(r"[0-9a-f]{32}")


def render_unit(config: AgentConfig, role: str, config_stem: str | None) -> str:
    return (
        f"openbao-agent@{config_stem}.service"
        if config_stem
        else unit_name(config.secrets_namespace, role)
    )


#: Each job render unit's `Description=` ends with this key and its material names,
#: sorted, lower-case and comma-separated. The agent cross-checks it, so two material
#: sets that map to one unit (an S12 collision) are refused rather than mixed.
DESCRIPTION_KEY: Final = "materials="


def require_render_unit(
    config: AgentConfig,
    commands: Commands,
    role: str,
    config_stem: str | None,
    materials: tuple[str, ...] = (),
) -> None:
    """Before anything is minted: the render unit for this material set exists, and
    its `Description` lists exactly this set."""
    shown = _systemctl(
        config,
        commands,
        "show",
        "--property=LoadState,Description",
        render_unit(config, role, config_stem),
        timeout=LOADED_TIMEOUT,
    )
    fields = dict(
        line.split("=", 1)
        for line in (shown or b"").decode("utf-8", "replace").splitlines()
        if "=" in line
    )
    if fields.get("LoadState") != "loaded":
        raise Refusal("job_files_invalid", "no_unit_for_set")
    description = fields.get("Description", "")
    listed = (
        description.rpartition(DESCRIPTION_KEY)[2]
        if DESCRIPTION_KEY in description
        else None
    )
    if listed is None or listed.split(",") != sorted(m.lower() for m in materials):
        raise Refusal("job_files_invalid", "unit_set_collision")


def render_job_credentials(
    config: AgentConfig,
    commands: Commands,
    role: str,
    names: tuple[str, ...],
    config_stem: str | None = None,
) -> tuple[tuple[str, ...], str]:
    """Render once. The agent never looks inside `secrets/` (0700, container uid).

    Starts the static one-shot `openbao-agent@<stem>` (it logs in with the one
    secret_id, renders and exits). The unit's own `ExecStartPost`, run as the
    container uid, checks each declared file and that NO undeclared file exists, and
    fails the unit otherwise. The agent reads only `Result`, `ExecMainStatus` and
    `InvocationID`: the id (32 hex) proves to Foundation that the result is from THIS
    dispatch's render. Returns `(sorted declared file names, InvocationID)`."""
    unit = render_unit(config, role, config_stem)
    if (
        _systemctl(config, commands, "start", unit, timeout=RENDER_START_TIMEOUT)
        is None
    ):
        raise Refusal("job_files_invalid", "render_unit_failed")
    shown = _systemctl(
        config,
        commands,
        "show",
        "--property=Result,ExecMainStatus,InvocationID",
        unit,
        timeout=SHOW_TIMEOUT,
    )
    fields = dict(
        line.split("=", 1)
        for line in (shown or b"").decode("utf-8", "replace").splitlines()
        if "=" in line
    )
    invocation = fields.get("InvocationID", "")
    if (
        fields.get("Result") != "success"
        or fields.get("ExecMainStatus") != "0"
        or not _INVOCATION.fullmatch(invocation)
    ):
        raise Refusal("job_files_invalid", "render_not_successful")
    declared = tuple(sorted(material.lower() for material in names))
    if not all(_FILENAME.fullmatch(name) for name in declared):
        raise Refusal("job_files_invalid")
    return declared, invocation


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
