"""The exclusive deployment lock.

Ported from `dotmac_sub:scripts/deploy.sh:117-140`, which took the lock after an
incident on 2026-07-12: two deployments ran concurrently, each started a
`pg_dump`, and the host went to load 52 on 16 cores with ten minutes of 502s.
The lock is step 1 of every plan and is not optional, and the Starter's
hand-port of that script dropped it entirely (inventory defect D16) — which is
exactly the class of loss a hand-port produces and a versioned distribution
does not.

Three properties the shell version has that are easy to lose in a rewrite:

- **Non-blocking.** `flock -n`. A deployment that WAITS for the lock is a
  deployment that starts an hour later, unattended, against a tree the operator
  has since changed. Refusing immediately is the safe answer.
- **The refusal names the contender.** `pgrep -af` in the original. Knowing a
  lock is held is useless; knowing which process holds it is actionable.
- **It survives a crash.** An advisory `fcntl` lock is released by the kernel
  when the holding process dies, so a killed deployment does not leave a lock
  file that blocks every future one. A lock implemented as "does this file
  exist" does exactly that, and someone eventually deletes it by hand at 3am
  without checking, which reintroduces the race the lock was for.

Deliberately `fcntl`-based rather than a subprocess call to `flock(1)`: the
original had to check that `flock` was installed and fail if not, and there is
no reason to inherit a dependency on a binary when the same syscall is in the
standard library.
"""

from __future__ import annotations

import errno
import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..errors import LockUnavailableError

__all__ = ["DEFAULT_LOCK_DIR", "LockUnavailableError", "deployment_lock", "lock_path"]

DEFAULT_LOCK_DIR = "/var/lock"


def lock_path(
    product: str, *, directory: str | os.PathLike[str] = DEFAULT_LOCK_DIR
) -> Path:
    """Where ``product``'s deployment lock lives.

    Per-product rather than global: two different products deploying to one
    host at once is fine and should not serialise, while two deployments of the
    SAME product at once is the incident.

    The directory is RESOLVED. Mutual exclusion is a property of an inode, not
    of a string, so two callers naming the same directory by different paths —
    a relative path and an absolute one, or a symlink and its target — must
    arrive at the same file or they will each take "the lock" and both proceed.
    """
    return Path(directory).resolve() / f"dotmac_{product}_deploy.lock"


def _holder_description(path: Path) -> str:
    """Whatever we can say about who holds the lock, without guessing.

    The file records the holding pid and a human label when it was taken. If it
    is empty or unreadable — a lock taken by an older version, or a truncated
    write — say so plainly rather than inventing a holder.
    """
    try:
        recorded = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "the holder could not be read"
    if not recorded:
        return "the holder is unrecorded"
    pid_text = recorded.split(None, 1)[0]
    try:
        pid = int(pid_text)
    except ValueError:
        return f"recorded holder {recorded!r}"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return (
            f"recorded holder {recorded!r}, whose process is GONE — the lock is "
            "held by a different process that inherited the descriptor, or the "
            "file is stale while a live holder has its own handle"
        )
    except PermissionError:
        return f"recorded holder {recorded!r} (running, owned by another user)"
    return f"recorded holder {recorded!r} (running)"


@contextmanager
def deployment_lock(
    product: str,
    *,
    directory: str | os.PathLike[str] = DEFAULT_LOCK_DIR,
    label: str = "",
) -> Iterator[Path]:
    """Hold ``product``'s exclusive deployment lock for the duration of the block.

    Raises :class:`LockUnavailableError` immediately when another deployment
    holds it. The error names the contender.

    The file is never deleted on release. Deleting it opens a window in which
    one process has unlinked the path while another has already opened the same
    inode, and both then believe they hold the lock — the classic lockfile race.
    An empty file costs nothing.
    """
    path = lock_path(product, directory=directory)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LockUnavailableError(
            f"cannot create lock directory {path.parent}: {exc}"
        ) from exc

    # O_NOFOLLOW, and then a regular-file check on the descriptor we actually
    # got. The lock lives in a world-writable directory (`/var/lock`), so the
    # path can be replaced by a symlink between deployments by anyone with a
    # login on the host. Following it would put the lock — and the pid we write
    # into it — on some other file, and `flock` on the wrong inode succeeds
    # cheerfully while excluding nobody.
    #
    # The two checks are not redundant. O_NOFOLLOW refuses a symlink AT the
    # final component; the fstat refuses everything else a path can be — a
    # FIFO, a device, a directory — which O_NOFOLLOW is perfectly happy to
    # open. Checking the descriptor rather than the path is what closes the
    # TOCTOU: `path` may already name something different by the time a
    # `Path.is_file()` answered.
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_NOFOLLOW", 0)  # absent on some platforms; not fatal
    try:
        handle = os.open(path, flags, 0o644)
    except OSError as exc:
        raise LockUnavailableError(
            f"cannot open deployment lock {path}: {exc}. A symlink at this "
            "path is refused rather than followed — the lock would otherwise "
            "be taken on a file of someone else's choosing"
        ) from exc
    try:
        if not stat.S_ISREG(os.fstat(handle).st_mode):
            raise LockUnavailableError(
                f"deployment lock {path} is not a regular file. Refusing: "
                "an advisory lock on a FIFO or a device excludes nothing, so "
                "proceeding would serialise nothing while appearing to"
            )
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            raise LockUnavailableError(
                f"another deployment of {product!r} holds {path} — "
                f"{_holder_description(path)}. Deployments are serialised "
                "deliberately: two concurrent runs each start a backup and each "
                "migrate, which is how one host reached load 52 with ten minutes "
                "of 502s"
            ) from exc
        os.ftruncate(handle, 0)
        os.write(handle, f"{os.getpid()} {label or product}\n".encode())
        os.fsync(handle)
        try:
            yield path
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        os.close(handle)
