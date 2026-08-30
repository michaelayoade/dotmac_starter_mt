"""Pinned tool identity — the trust anchor must not be whatever `PATH` says.

`ComposeHostEffects` shelled out to `docker`, `git` and `pg_dump` as **bare
names**. A bare name is not an identity: it is a lookup, resolved at exec time
against an environment variable, and the answer depends on who set `PATH` and
what is earlier in it.

That matters more here than in ordinary code, because these are not
conveniences — `docker image inspect` is how this facility learns what digest a
host is running, and `git status --porcelain` is how it learns whether the
deploy tree was modified. Both are **evidence-gathering** commands. A caller who
controls which binary answers them controls what the deployment believes about
the world, and every downstream check inherits that answer.

## Two layers, because one cannot do both jobs

**Absolute-path enforcement** is a pure string check with no filesystem access,
so :class:`~.providers.compose_host.ComposeHostEffects` can do it at
construction, always, including in tests. It removes the entire
`PATH`-resolution class: after this, the binary is named, not searched for.

**Integrity verification** — exists, regular, executable, not writable by
anyone but its owner, in a directory not writable by anyone but its owner, and
optionally matching a recorded digest — needs the real filesystem. It runs on
the production path (`cli._build_effects`) rather than at construction, because
a unit test driving a scripted fake runner never execs anything and must not
need a real `/usr/bin/docker` present to run.

Neither layer is decorative. Absolute-path-only still lets someone replace the
file at that path; integrity checks alone still leave `PATH` deciding which
file gets checked.

## Why the parent directory is checked

A binary can be immutable and still be swapped, by renaming something over it.
`rename(2)` needs write permission on the DIRECTORY, not on the file, so a
world-writable `/usr/local/bin` defeats a mode-755 root-owned `docker` inside
it. Checking the file's mode alone is the check that looks right and is not.

## Digest pinning is optional, and that is deliberate

A recorded digest is the strongest form, but it has to be maintained: every
legitimate `docker` upgrade changes it, and a guard that fails on routine
patching gets disabled. So a digest is honoured when supplied and its absence
is not an error — while the ownership and mode checks, which survive upgrades
untouched, are always on.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Final

from .errors import PreconditionFailed

__all__ = [
    "DEFAULT_TOOLS",
    "require_absolute_tool",
    "resolve_tool",
]

#: The HOST binaries this facility executes, with documented defaults that are
#: overridable per deployment (`AGENTS.md` § "Everything by config"). Absolute,
#: so the default itself is not a `PATH` lookup — a default of `"docker"` would
#: mean the guard only protects deployments that had already thought about it.
#:
#: `pg_dump` is deliberately ABSENT, and its absence is a scope statement rather
#: than an oversight. It is invoked as
#: `docker compose exec -T <db> pg_dump …`, so it is resolved inside the
#: CONTAINER's filesystem by the container's `PATH`. The host's `PATH` cannot
#: influence which binary that is, so the premise this module rests on — "an
#: attacker who controls `PATH` controls the evidence" — simply does not hold
#: there. Pinning it to a host path would also be wrong on its own terms:
#: `/usr/bin/pg_dump` need not exist in a postgres image, which commonly ships
#: it under a versioned directory. What governs that binary is the image digest,
#: which is pinned elsewhere and is the right owner for it.
DEFAULT_TOOLS: Final[dict[str, str]] = {
    "docker": "/usr/bin/docker",
    "git": "/usr/bin/git",
}


def require_absolute_tool(configured: str, *, what: str) -> str:
    """Refuse a tool that `PATH` would have to resolve. Pure; no filesystem.

    Cheap enough to run at construction time in every environment, which is
    what lets it be unconditional rather than something the production path
    remembers to call.
    """
    value = str(configured).strip()
    if not value:
        raise PreconditionFailed(
            f"{what} is empty. A deployment that cannot say which binary it "
            "runs cannot say what its evidence means"
        )
    if not value.startswith("/"):
        raise PreconditionFailed(
            f"{what} is {value!r}, which PATH would have to resolve. Which "
            "binary that names depends on who set PATH and what is earlier in "
            "it — and this tool's output is treated as evidence about the "
            "host, so whoever chooses the binary chooses the evidence. Give an "
            "absolute path"
        )
    if ".." in Path(value).parts:
        raise PreconditionFailed(
            f"{what} is {value!r}, which traverses. An absolute path with a "
            "`..` in it is not a stable identity for a binary"
        )
    return value


def _refuse_if_writable_by_others(path: Path, *, what: str, kind: str) -> None:
    """Refuse a path any non-owner can replace.

    Group is included with other. "Only trusted people are in that group" is a
    claim about staffing, not about the filesystem, and it is exactly the kind
    of claim that stops being true without anyone editing this file.
    """
    mode = path.stat().st_mode
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise PreconditionFailed(
            f"{what}: the {kind} {path} is writable by group or other "
            f"(mode {stat.filemode(mode)}). Anyone who can write it can "
            "replace the binary this deployment trusts for its evidence"
        )


def resolve_tool(configured: str, *, what: str, expected_sha256: str = "") -> str:
    """Verify a pinned tool on the real filesystem, or refuse.

    Returns the path so a caller can use the return value rather than the
    input — a verified value and an unverified one should not be the same
    variable.
    """
    value = require_absolute_tool(configured, what=what)
    path = Path(value)

    try:
        info = path.stat()
    except OSError as exc:
        raise PreconditionFailed(
            f"{what}: cannot stat {path}: {exc}. A tool that is not there is "
            "not a tool that can be trusted later"
        ) from exc

    if not stat.S_ISREG(info.st_mode):
        raise PreconditionFailed(
            f"{what}: {path} is not a regular file " f"({stat.filemode(info.st_mode)})"
        )
    if not os.access(path, os.X_OK):
        raise PreconditionFailed(f"{what}: {path} is not executable")

    _refuse_if_writable_by_others(path, what=what, kind="binary")
    # `rename(2)` needs write permission on the DIRECTORY, not the file, so a
    # writable parent defeats an immutable binary inside it.
    _refuse_if_writable_by_others(path.parent, what=what, kind="parent directory")

    if expected_sha256:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        wanted = expected_sha256.removeprefix("sha256:").strip().lower()
        if digest != wanted:
            raise PreconditionFailed(
                f"{what}: {path} is sha256:{digest}, not the pinned "
                f"sha256:{wanted}. Either the binary was replaced or the pin "
                "is stale — both need a person, and neither may be guessed at "
                "here"
            )
    return value
