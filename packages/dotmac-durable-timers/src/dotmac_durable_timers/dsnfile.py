"""Read a database DSN from a governed secret file (never the environment).

Under the deploy design secrets reach a container only as tmpfs files mounted
read-only.  The runner therefore takes the PATH on its command line (or in a
``*_FILE`` environment variable) and reads the value itself.  The value is
never an argument, never logged and never part of an error message.

A file is accepted only when it is a regular file (not a symlink), mode exactly
0400, owned by the running user or root.  The file is opened with
``O_NOFOLLOW`` and every check is made on the open descriptor, so the path
cannot be swapped between the check and the read.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


class DsnFileError(RuntimeError):
    """The DSN file is missing, unsafe or empty. Names the path, never the value."""


def read_dsn_file(path: str | os.PathLike[str]) -> str:
    target = os.fspath(path)
    try:
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise DsnFileError(
            f"cannot open DSN file {target!r}: {exc.strerror or type(exc).__name__} "
            "(it must exist and must not be a symlink)"
        ) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise DsnFileError(f"DSN file {target!r} is not a regular file")
        if stat.S_IMODE(info.st_mode) != 0o400:
            raise DsnFileError(
                f"DSN file {target!r} has mode {stat.S_IMODE(info.st_mode):04o}; "
                "it must be exactly 0400"
            )
        if info.st_uid not in (os.geteuid(), 0):
            raise DsnFileError(
                f"DSN file {target!r} is owned by uid {info.st_uid}; it must be "
                "owned by the running user or root"
            )
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(65536 + 1)
    finally:
        os.close(fd)
    if len(raw) > 65536:
        raise DsnFileError(f"DSN file {target!r} is implausibly large")
    try:
        value = raw.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError:
        raise DsnFileError(f"DSN file {target!r} is not valid UTF-8 text") from None
    if not value:
        raise DsnFileError(f"DSN file {target!r} is empty")
    if "\n" in value or "\r" in value:
        raise DsnFileError(f"DSN file {target!r} must hold exactly one line")
    return value


def resolve_dsn(path_arg: str | None, env_name: str) -> str:
    """The DSN from ``path_arg`` or, failing that, the file named by the
    ``env_name`` variable. There is deliberately no plain-value form."""
    path = path_arg or os.environ.get(env_name)
    if not path:
        raise DsnFileError(
            f"no DSN file: pass its path as an argument or set {env_name} to the path"
        )
    return read_dsn_file(Path(path))


__all__ = ["DsnFileError", "read_dsn_file", "resolve_dsn"]
