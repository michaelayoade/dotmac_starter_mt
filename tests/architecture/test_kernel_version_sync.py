"""`dotmac_kernel.__version__` must equal the distribution version.

The kernel carries its version in TWO places that nothing kept in sync:
`packages/dotmac-kernel/pyproject.toml`'s `[tool.poetry] version` (what the
wheel is published as) and the `__version__` literal in
`dotmac_kernel/__init__.py` (what a consumer reads at runtime).

Caught during the 0.1.0a9 release prep: the pyproject bump landed, the literal
did not, and the floor-proof probe cheerfully printed ``kernel 0.1.0a8`` while
exercising a wheel built as ``0.1.0a9``. Every gate still passed, because no
test compared them.

That drift is worse than cosmetic. `__version__` is the value a consumer logs,
reports in a support bundle, or branches on when working around a known kernel
defect — so a stale literal makes a deployment lie about which kernel it is
running, and the lie survives exactly as long as nobody diffs two files by
hand.

Deliberately compares against the pyproject SOURCE rather than
`importlib.metadata.version`: in an editable install the installed metadata can
itself be stale, so asserting against it would let both values be wrong
together — which is the failure this test exists to catch.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import dotmac_kernel

_KERNEL = Path(__file__).resolve().parents[2] / "packages" / "dotmac-kernel"
_PYPROJECT = _KERNEL / "pyproject.toml"


def _declared_version() -> str:
    data = tomllib.loads(_PYPROJECT.read_text())
    return data["tool"]["poetry"]["version"]


def test_runtime_version_matches_the_distribution_version() -> None:
    declared = _declared_version()
    assert dotmac_kernel.__version__ == declared, (
        f"dotmac_kernel.__version__ is {dotmac_kernel.__version__!r} but "
        f"pyproject declares {declared!r} — bump BOTH: "
        "packages/dotmac-kernel/pyproject.toml and "
        "packages/dotmac-kernel/src/dotmac_kernel/__init__.py"
    )


def test_the_version_is_a_pep440_release_or_prerelease() -> None:
    """A malformed version fails the publish late, after the build job has
    already run; catching the shape here keeps that failure local."""
    assert re.fullmatch(
        r"\d+\.\d+\.\d+(?:a|b|rc)?\d*", dotmac_kernel.__version__
    ), f"not a PEP 440 version: {dotmac_kernel.__version__!r}"
