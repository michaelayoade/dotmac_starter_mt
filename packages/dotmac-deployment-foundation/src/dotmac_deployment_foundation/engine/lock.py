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

import dataclasses
import errno
import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from ..errors import LockUnavailableError, PreconditionFailed

__all__ = [
    "DEFAULT_LOCK_DIR",
    "DeploymentLockHeld",
    "LockUnavailableError",
    "deployment_lock",
    "lock_path",
]

DEFAULT_LOCK_DIR = "/var/lock"


class _HeldWitness:
    """Proof that :func:`deployment_lock` produced this value, not a caller."""

    __slots__ = ()


_HELD: Final = _HeldWitness()


@dataclasses.dataclass(eq=False)
class DeploymentLockHeld:
    """Evidence, carried as a value, that this process holds ``product``'s lock.

    ## Why a token rather than a convention

    Until this type existed the rule "the caller holds the lock around the whole
    run" was written in three comments — `cli.py`'s two deploy paths and
    :meth:`Executor._do_acquire_lock`, which returned the sentence *"held by the
    caller for the duration of the run"* without ever asking. `Executor` took no
    lock, named no lock and checked no lock, so any second caller — a script, a
    worker, an embedder, a future subcommand — could construct a fully
    authorized executor and mutate a host with nothing serialising it. The
    2026-07-12 incident that produced `deployment_lock` in the first place was
    two concurrent deployments; the guard against it was a comment.

    So the lock now yields a value, that value is a required argument of every
    mutating entry point, and it can only be obtained INSIDE the ``with`` block.
    This is the `authorization._Witness` idiom applied to a different question:
    there, a caller with no grant has nothing to construct an `Executor` with;
    here, a caller holding no lock has nothing to call `run` with.

    ## Why it goes dead on exit

    A dataclass captured inside the block and used after it would be a lock
    token for a lock nobody holds — the exact "lock-shaped gap between the check
    and the mutation" `cli.py:524` warns about, wearing a type. :attr:`live` is
    cleared in `deployment_lock`'s ``finally`` BEFORE the descriptor is
    unlocked, so a token that escapes its block refuses rather than lies.

    ## Why it names its product

    `lock_path` is per-product deliberately: two products deploying at once is
    fine and must not serialise. That makes "a token" insufficient on its own —
    holding ACME's lock says nothing about deploying BETA — so
    :meth:`require_held` compares the product as well as the liveness.
    """

    #: Positional and first, with no default, so a hand-built token cannot be
    #: mistaken for an ordinary constructor call in review.
    witness: _HeldWitness
    product: str
    path: Path
    #: Cleared when the ``with`` block exits. Not a property of the file: the
    #: file outlives every holder by design (`deployment_lock` never unlinks
    #: it), so liveness is a property of THIS acquisition and nothing else.
    live: bool = True

    def __post_init__(self) -> None:
        if self.witness is not _HELD:
            raise PreconditionFailed(
                "a DeploymentLockHeld may only be produced by deployment_lock(). "
                "A hand-built token is a deployment that serialised itself "
                "against nothing, which is the exact failure this type exists "
                "to make impossible to write by accident"
            )

    def require_held(self, *, product: str) -> Path:
        """Refuse unless this token is a live hold on ``product``'s lock.

        Returns the lock path so a caller can report the real file rather than
        reconstructing it — the same reason
        `test_the_helper_and_the_context_manager_agree_on_the_path` exists.
        """
        if not self.live:
            raise PreconditionFailed(
                f"the deployment lock on {self.product!r} was released before "
                "this point: the token was captured inside the `with` block and "
                "used after it exited. Nothing is serialising this run, and a "
                "token that outlives its hold is worse than no token — it reads "
                "in a diff exactly like a real one"
            )
        if self.product != product:
            raise PreconditionFailed(
                f"this token holds the deployment lock for {self.product!r}, "
                f"and the work in hand is {product!r}. The lock is per-product "
                "deliberately — two products deploying to one host must not "
                "serialise — so holding one product's lock authorises nothing "
                "about another's"
            )
        return self.path


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
) -> Iterator[DeploymentLockHeld]:
    """Hold ``product``'s exclusive deployment lock for the duration of the block.

    Raises :class:`LockUnavailableError` immediately when another deployment
    holds it. The error names the contender.

    Yields a :class:`DeploymentLockHeld` rather than the path. The path is still
    reachable (``held.path``) and still the same inode, but the value a caller
    now carries out of this block is EVIDENCE of the hold, because the mutating
    entry points require one. A `Path` proves nothing: it can be constructed by
    anybody, at any time, holding nothing.

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
        held = DeploymentLockHeld(_HELD, product=product, path=path)
        try:
            yield held
        finally:
            # Cleared BEFORE the unlock, not after. Between `LOCK_UN` and this
            # assignment the token would claim a hold that had already ended,
            # and that window is exactly the shape of bug the token exists to
            # close. Ordering it this way makes the dead interval strictly
            # larger than the unheld one, which is the safe direction.
            held.live = False
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        os.close(handle)
