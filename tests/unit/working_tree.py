"""Which Python files are in THIS working tree. One answer, for every sweep.

Several architecture-style checks in this suite walk the tree and ask a question
about every file: which classes implement a protocol, which modules canonicalize
bytes, which modules publish into a store. They must all agree about what "every
file" means, and until now they did not — one asked git, the others used
`Path.rglob`.

That disagreement is not cosmetic. A primary checkout carries sibling
`*_worktree/` directories, and `rglob` sweeps every one of them, so a local run
reports results **from other lanes' branches**: a false red sends a lane after a
defect that is not in its tree, and a false green lets a stale worktree mask a
real gap in the tree under test.

**Asks git, and does not skip by NAME.** A `*_worktree/` exclusion fails the
moment a checkout is named differently. What actually makes a directory a
separate checkout is its own `.git`, and git already knows: measured,
`ls-files --cached --others --exclude-standard` emits a nested checkout as a bare
directory entry and lists NO file inside it — for both a nested `git init` and a
real `git worktree add`, whose `.git` is a file rather than a directory.

**`--others` is deliberate.** Tracked-only would drop a file that exists in this
tree but is not yet staged, which is the false-green direction one step smaller.
`--exclude-standard` keeps ignored build output out.

A missing or failing git REFUSES rather than falling back to `rglob`: the
fallback is the silent revert this module exists to prevent.

Measured: in a clean checkout this returns exactly what the old walk did, so CI
is unaffected and only the local over-reach is removed. That equality is
deliberately NOT asserted as a test — it holds only where there is no sibling
checkout, so the assertion would fail in precisely the tree this serves.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

_LS_FILES = ("ls-files", "-z", "--cached", "--others", "--exclude-standard")


def python_files(root: Path = REPO) -> list[Path]:
    """Every `.py` file under `root`, excluding any nested checkout.

    `root` may be the repository or any directory inside it; `git -C` resolves
    the enclosing repository and lists that subtree, so a sweep scoped to one
    package and a sweep scoped to the whole tree get the same treatment.
    """
    result = subprocess.run(  # noqa: S603 # nosec B603 — fixed argv, no shell
        ["git", "-C", str(root), *_LS_FILES],  # noqa: S607 # nosec B607
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:  # pragma: no cover - refuses, never degrades
        raise RuntimeError(
            f"git ls-files failed in {root}: "
            f"{result.stderr.decode(errors='replace')}. This sweep refuses "
            "rather than falling back to a directory walk, which would silently "
            "restore the sibling-checkout over-reach"
        )
    names = result.stdout.decode("utf-8", errors="replace").split("\0")
    return sorted(root / name for name in names if name.endswith(".py"))
