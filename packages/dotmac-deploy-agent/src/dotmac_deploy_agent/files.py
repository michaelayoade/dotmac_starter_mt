"""Small, explicit filesystem primitives: private directories, atomic private files."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .refusal import Refusal


def require_private_dir(path: Path) -> None:
    """A real directory, ours, with no group/other access."""
    try:
        info = path.lstat()
    except OSError:
        raise Refusal("runtime_dir_unsafe") from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
    ):
        raise Refusal("runtime_dir_unsafe")


def ensure_private_dir(path: Path) -> None:
    """Create `path` (0700) if absent, then require it private. Parent must exist."""
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        raise Refusal("runtime_dir_unsafe") from None
    require_private_dir(path)


def write_private(path: Path, data: bytes, mode: int) -> None:
    """Write `data` to `path` atomically with `mode`; never follows a symlink."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.pending")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            mode,
        )
    except OSError:
        raise Refusal("runtime_dir_unsafe") from None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def remove_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def read_regular(path: Path, *, limit: int) -> bytes:
    """Read a regular, non-symlink file of at most `limit` bytes, else refuse."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise Refusal("runtime_dir_unsafe") from None
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise Refusal("runtime_dir_unsafe")
        return stream.read(limit + 1)
