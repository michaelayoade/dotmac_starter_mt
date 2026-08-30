"""The controller must be the released controller, not one the target supplied.

Every other guard in this facility — the authorization seam, the signed
evidence, the ancestry refusal, the pinned toolchain — assumes the code
performing the check is the code that was reviewed and released. Nothing
established that.

The controller runs *against* a staged deploy tree, with that tree's contents
under the control of whoever can write to the host. Python resolves imports
through `sys.path`, and a directory early on that path wins. So a
`dotmac_deployment_foundation/` directory inside the staged tree — or a
`sitecustomize.py`, or a `.pth` file — silently replaces the facility with an
edited copy whose `authorize()` returns a grant for anything.

The result is a deployment that passes every gate, because the gates are the
attacker's. And it looks completely normal: same command, same output, same
green log lines.

## What is actually checkable, and what is theatre

A process cannot meaningfully re-verify itself after the fact. By the time this
module's code runs, whatever was going to be shadowed already has been, and a
compromised copy would simply not call these functions or would return `True`
from them.

So this is NOT "the controller verifies its own integrity". It is narrower and
honest: **before a run does anything irreversible, refuse if the loaded
facility is coming from somewhere it should not be.** That catches the ordinary
accident and the unsophisticated attack — a stale copy in the deploy tree, a
developer's `PYTHONPATH` left set, a `pip install -e` pointing at a working
directory on a production host. It does not defeat an adversary who already
controls the interpreter, and claiming otherwise would be the exact kind of
overreach this codebase keeps refusing.

The strong version of the property lives outside the process: the launcher
digest recorded in the release receipt, compared by whoever starts the run.
:func:`refuse_untrusted_launcher` supports that by exposing the loaded module's
real path and digest so a caller can compare them with the receipt — the
comparison it cannot make about itself.

## Why the staged tree specifically

Not "an unexpected path" in general: a facility legitimately runs from a venv,
from site-packages, from an editable install during development, and
enumerating good locations produces a guard that fails constantly and gets
disabled. What is never legitimate is the facility being loaded from *inside
the directory it is about to deploy*, because that directory is the input, and
an input that supplies its own validator is not an input that was validated.
"""

from __future__ import annotations

import dataclasses
import hashlib
import sys
from pathlib import Path

from .errors import PreconditionFailed

__all__ = [
    "LauncherIdentity",
    "loaded_facility_root",
    "refuse_untrusted_launcher",
]


def loaded_facility_root() -> Path:
    """Where the currently-imported facility actually lives on disk.

    Derived from this module's own `__file__` rather than from a configured
    value, because the question is "what got loaded", and a configured answer
    to that question is the thing being checked.
    """
    return Path(__file__).resolve().parent


@dataclasses.dataclass(frozen=True, slots=True)
class LauncherIdentity:
    """What a caller needs in order to compare this process with a receipt."""

    #: Absolute path of the loaded package directory.
    root: str
    #: sha256 over the package's own `.py` files, in sorted path order.
    digest: str
    #: The interpreter running it, so an unexpected venv is visible.
    executable: str


def _package_digest(root: Path) -> str:
    """A stable digest over the facility's own source.

    Sorted relative paths, each hashed with its path as well as its bytes, so
    that renaming a file changes the digest — a content-only hash would treat
    `authorize.py` moved to `authorize.py.bak` plus a new `authorize.py` as no
    change at all.
    """
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def identify_launcher() -> LauncherIdentity:
    root = loaded_facility_root()
    return LauncherIdentity(
        root=str(root),
        digest=f"sha256:{_package_digest(root)}",
        executable=sys.executable,
    )


def refuse_untrusted_launcher(
    *,
    deploy_dir: str | Path,
    expected_digest: str = "",
) -> LauncherIdentity:
    """Refuse to run if the facility was loaded from the tree being deployed.

    Returns the identity so a caller that HAS a recorded launcher digest can
    record or compare it; pass `expected_digest` to have the comparison made
    here.
    """
    identity = identify_launcher()
    root = Path(identity.root)
    staged = Path(deploy_dir).resolve()

    if root == staged or staged in root.parents:
        raise PreconditionFailed(
            f"the deployment facility is loaded from {root}, which is inside "
            f"the staged deploy tree {staged}. That tree is the INPUT to this "
            "run and is writable on the target, so every check this process is "
            "about to perform would be defined by the thing being checked. "
            "Install the facility outside the tree it deploys"
        )

    # A staged directory earlier on `sys.path` than the real installation has
    # not shadowed us THIS time — we already resolved elsewhere — but it will
    # shadow the next import of any submodule not yet loaded, which is most of
    # them under lazy imports.
    for entry in sys.path:
        if not entry:
            continue
        candidate = Path(entry).resolve()
        if candidate == staged or staged in candidate.parents:
            raise PreconditionFailed(
                f"sys.path contains {candidate}, inside the staged deploy tree "
                f"{staged}. Modules not yet imported would be resolved from "
                "the tree being deployed — the shadowing has not happened yet "
                "only because those imports have not happened yet"
            )

    if expected_digest:
        wanted = expected_digest.strip().lower()
        if not wanted.startswith("sha256:"):
            wanted = f"sha256:{wanted}"
        if identity.digest != wanted:
            raise PreconditionFailed(
                f"the loaded facility is {identity.digest}, not the released "
                f"{wanted}. The code about to run is not the code that was "
                "reviewed, whatever the reason"
            )
    return identity
