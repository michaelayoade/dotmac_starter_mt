"""D1 `secret_id` delivery: write role files for the OpenBao Agent and the container."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .files import remove_file, write_private
from .materials import RoleBindings
from .openbao import OpenBaoClient
from .refusal import Refusal

SECRET_ID_FILE = "secret_id"  # noqa: S105  # nosec B105 - a file name
ROLE_ID_FILE = "role_id"


@dataclass(frozen=True)
class Delivered:
    """Names only. This is what may enter an observation."""

    role: str
    single_use: bool


def _ensure_role_dir(path: Path, *, shared: bool) -> None:
    try:
        path.mkdir(mode=0o755 if shared else 0o700, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise Refusal("runtime_dir_unsafe")
        os.chmod(path, 0o755 if shared else 0o700)
    except OSError:
        raise Refusal("runtime_dir_unsafe") from None
    (path / "secrets").mkdir(mode=0o755 if shared else 0o700, exist_ok=True)


def _place(path: Path, data: bytes, container_uid: int | None) -> None:
    write_private(path, data, 0o400)
    if container_uid is not None and container_uid != os.geteuid():
        # The agent is non-root. Handing a file to the container uid needs a
        # capability this package's unit deliberately does not grant: if the
        # chown is not permitted the delivery fails closed (open question for WP6).
        try:
            os.chown(path, container_uid, -1)
        except OSError:
            remove_file(path)
            raise Refusal("secret_chown_failed") from None


def deliver_secret_ids(
    client: OpenBaoClient, roles: tuple[RoleBindings, ...], run_dir: Path
) -> tuple[Delivered, ...]:
    delivered: list[Delivered] = []
    for role in roles:
        shared = role.container_uid is not None and role.container_uid != os.geteuid()
        role_dir = run_dir / role.role
        _ensure_role_dir(role_dir, shared=shared)
        role_id = client.role_id(role.role)
        _place(role_dir / ROLE_ID_FILE, role_id.encode("utf-8"), role.container_uid)
        secret_id = client.unwrap(client.wrapped_secret_id(role.role))
        _place(role_dir / SECRET_ID_FILE, secret_id.encode("utf-8"), role.container_uid)
        del secret_id
        delivered.append(Delivered(role.role, role.single_use))
    return tuple(delivered)
