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
import pytest

PACKAGES = Path(__file__).resolve().parents[2] / "packages"
_KERNEL = PACKAGES / "dotmac-kernel"
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


# ── A module's floor is the kernel release that allocated its schema ─────────

# Each installable module's `dotmac-kernel` floor, and the CHANGELOG heading that
# must document why that release exists. A module cannot be registered by a
# kernel predating its `MIGRATION_OWNER_LEDGER` row — `NamespaceRegistry
# .from_manifests` raises `UnallocatedNamespaceError` — so the floor is not a
# preference and drifting it produces a runtime boot failure, not a warning.
#
# The exact numbers matter beyond correctness: a42 and a43 belong to the
# upstream train (Audit R1, Application Directory / Workspace). Two branches
# minting one kernel version collide in the changelog and leave a consumer
# unable to say which a42 it pinned, so the vendor modules renumbered to a44/a45
# rather than the foundations renumbering around them.
LEDGER_ALLOCATION_RELEASES = {
    "dotmac-release-catalog": "0.1.0a44",
    "dotmac-entitlement-allocation": "0.1.0a45",
    # ADR-0022/ADR-0023. Files declares both planes, but the capability it
    # consumes (`platform_tables`, a53) landed BELOW its own namespace
    # allocation, so the allocation is the higher requirement and this is
    # the ordinary case rather than a capability-raised floor.
    "dotmac-files": "0.1.0a54",
}


def _kernel_changelog() -> str:
    return (PACKAGES / "dotmac-kernel" / "CHANGELOG.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("distribution", "release"), sorted(LEDGER_ALLOCATION_RELEASES.items())
)
def test_module_floor_is_the_release_that_allocated_its_schema(
    distribution: str, release: str
) -> None:
    manifest = tomllib.loads(
        (PACKAGES / distribution / "pyproject.toml").read_text(encoding="utf-8")
    )
    floor = manifest["tool"]["poetry"]["dependencies"]["dotmac-kernel"]
    assert floor == f">={release}", (
        f"{distribution} pins {floor!r}; its ledger row landed in {release}, and "
        "an earlier kernel cannot register the module at all"
    )


@pytest.mark.parametrize("release", sorted(LEDGER_ALLOCATION_RELEASES.values()))
def test_each_allocation_release_is_documented(release: str) -> None:
    """A floor pointing at a release the changelog never explains is a version
    number nobody can audit."""
    assert f"## {release} —" in _kernel_changelog()


def test_no_vendor_module_claims_an_upstream_train_version() -> None:
    """a42 and a43 are spoken for. This test is the reason the renumber cannot
    silently come back: it fails if any vendor module's floor lands on one."""
    contested = {"0.1.0a42", "0.1.0a43"}
    assert set(LEDGER_ALLOCATION_RELEASES.values()) & contested == set()


def test_the_kernel_is_at_least_every_module_floor() -> None:
    """The composed assembly ships ONE kernel, so it must satisfy every module
    installed beside it — otherwise the composition cannot boot."""
    declared = tomllib.loads(
        (PACKAGES / "dotmac-kernel" / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["poetry"]["version"]
    for release in LEDGER_ALLOCATION_RELEASES.values():
        assert (
            declared >= release
        ), f"kernel is {declared} but a module floors at {release}"
