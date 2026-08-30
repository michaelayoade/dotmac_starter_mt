"""The deployment lock must lock the file it names, or refuse.

Mutual exclusion is a property of an INODE. Every guarantee `deployment_lock`
offers — that two deployments of one product cannot run at once, that the
`ExposureTransaction` holding it is alone in rewriting shared firewall chains —
reduces to "both contenders ended up on the same file". A lock that quietly
lands on a *different* file still returns, still writes a pid, and still lets
the caller proceed; it simply excludes nobody. That is the failure mode worth
testing, because it is silent.

Two ways the path can stop naming what the caller meant, and the default
`DEFAULT_LOCK_DIR` of `/var/lock` makes both reachable by anyone with a login
on the host:

1. **A symlink at the final component.** `os.open` follows it by default, so
   the lock is taken on a file of the attacker's choosing, while the refusal
   message and the recorded holder both name the intended path.
2. **Something that is not a regular file** — a FIFO, a device, a directory.
   `O_NOFOLLOW` opens these happily; only an `fstat` on the descriptor rejects
   them, and an advisory lock on a FIFO excludes nothing.

And one way two honest callers miss each other: naming the same directory by
different strings. `lock_path()` resolves, so a symlinked directory and its
target agree.

Found by the #507 supersession audit — that PR's controller slice depended on
the lock actually holding. The gap is real against current `main` regardless of
that PR's fate, so it is fixed here on its own reasoning rather than ported.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotmac_deployment_foundation.engine.lock import deployment_lock, lock_path
from dotmac_deployment_foundation.errors import LockUnavailableError


def test_the_lock_is_taken_on_a_regular_file(tmp_path: Path) -> None:
    """The baseline. Without this the refusals below could be refusing everything."""
    with deployment_lock("acme", directory=tmp_path) as path:
        assert path.is_file()
        assert path.read_text().split()[0] == str(os.getpid())


def test_a_second_holder_is_refused_while_the_first_holds(tmp_path: Path) -> None:
    """The property the whole module exists for, proven before it is hardened."""
    with deployment_lock("acme", directory=tmp_path):
        with pytest.raises(LockUnavailableError, match="another deployment"):
            with deployment_lock("acme", directory=tmp_path):
                pass  # pragma: no cover - the point is that we never get here


def test_different_products_do_not_serialise(tmp_path: Path) -> None:
    """Sensitivity's other half: the lock must not refuse things it should allow."""
    with deployment_lock("acme", directory=tmp_path):
        with deployment_lock("other", directory=tmp_path):
            pass


def test_a_symlinked_lock_path_refuses_rather_than_following_it(
    tmp_path: Path,
) -> None:
    """The attack: plant a symlink where the lock goes.

    Without `O_NOFOLLOW` this SUCCEEDS and locks `elsewhere`, so the assertion
    that matters is not only that we raised — it is that the decoy was never
    written to.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("untouched")
    planted = lock_path("acme", directory=tmp_path)
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.symlink_to(elsewhere)

    with pytest.raises(LockUnavailableError, match="symlink|cannot open"):
        with deployment_lock("acme", directory=tmp_path):
            pass  # pragma: no cover

    assert elsewhere.read_text() == "untouched", (
        "the lock followed the symlink and wrote its pid into the target — "
        "it is excluding nobody while reporting success"
    )


def test_a_fifo_at_the_lock_path_refuses(tmp_path: Path) -> None:
    """`O_NOFOLLOW` does not catch this one; only the fstat does."""
    path = lock_path("acme", directory=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(path)

    with pytest.raises(LockUnavailableError, match="not a regular file|cannot open"):
        with deployment_lock("acme", directory=tmp_path):
            pass  # pragma: no cover


def test_lock_path_resolves_so_two_names_for_one_directory_agree(
    tmp_path: Path,
) -> None:
    """A symlinked lock DIRECTORY must not yield two different lock files."""
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    assert lock_path("acme", directory=alias) == lock_path("acme", directory=real)


def test_two_names_for_one_directory_actually_exclude_each_other(
    tmp_path: Path,
) -> None:
    """The consequence of the above, driven through the real lock."""
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    with deployment_lock("acme", directory=real):
        with pytest.raises(LockUnavailableError, match="another deployment"):
            with deployment_lock("acme", directory=alias):
                pass  # pragma: no cover


def test_the_helper_and_the_context_manager_agree_on_the_path(tmp_path: Path) -> None:
    """`deployment_lock` must not rebuild the path independently.

    Two constructions of one path is a divergence waiting to happen: a caller
    that inspects `lock_path()` would then be looking at a different file from
    the one actually locked.
    """
    with deployment_lock("acme", directory=tmp_path) as held:
        assert held == lock_path("acme", directory=tmp_path)
