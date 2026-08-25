"""No publishable package may contain a secret-shaped filename.

The release workflow already refuses such a wheel. That check runs at *publish*
time, which is far too late to be the only one: `dotmac-integration` carried a
`secrets.py` through four merged PRs, a full green CI matrix, its own release
eligibility review, and a kernel release — and failed on the first line of the
build job, after `main` had already been advanced four times.

Nothing was wrong with the release check. The problem was WHERE it lived: a gate
that can only fail after the thing it guards has been merged is a gate that
reports history rather than preventing it.

So the same predicate runs here, at PR time, over the source tree.

## Deliberately the same code, not the same idea

`secret_shaped()` is imported from `scripts/release_module.py` rather than
reimplemented. Two copies of a name-shape list drift, and the drift is silent in
the worst direction: the local copy relaxes, PRs go green, and the release check
becomes the only real gate again — which is exactly the state this file exists
to leave.

## The false positive is the point

The marker list matches `secrets.` and `credentials.`, which are ordinary Python
module names. That is not a defect. A NAME check cannot distinguish a module
ABOUT secret handling from a file CONTAINING secret material, so it assumes the
worse of the two: a false positive costs a rename, a false negative puts a
credential in an immutable published artefact.

When this fires on a legitimately-named module, **rename the module**. Do not
add an exemption here. `dotmac-integration`'s `secrets.py` became
`secret_refs.py` — a more accurate name anyway, since references are the only
thing it handles — rather than weakening a security check to accommodate a
filename.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from release_module import SECRET_SHAPED_MARKERS, secret_shaped  # noqa: E402

# Only what a wheel actually contains. A package directory also holds `.venv`,
# `dist/`, and build metadata — scanning those would flag third-party files that
# never ship (`certifi/cacert.pem`, `pydantic_settings/.../secrets.py`) and turn
# this into noise nobody reads, which is how a real hit gets waved through.
EXCLUDED_PARTS = frozenset(
    {"__pycache__", ".venv", "venv", "dist", "build", ".pytest_cache", ".mypy_cache"}
)


def _shipped_files(package_dir: Path) -> list[str]:
    source = package_dir / "src"
    root = source if source.is_dir() else package_dir
    return [
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file()
        and not EXCLUDED_PARTS & set(path.parts)
        and not any(part.endswith(".egg-info") for part in path.parts)
    ]


def _publishable_packages() -> list[tuple[str, Path]]:
    """Every package a release workflow may publish, read from the allowlists
    rather than from a glob — so the parametrized failure names the lane that
    would have published the offending wheel.

    ALL THREE lanes. `.github/release-modules.json` is the stateful one;
    `.github/release-adapters.json` is the stateless-protocol-adapter lane added
    for `dotmac-auth-oidc` (ADR-0006, 2026-08-14 amendment), and
    `.github/release-connectors.json` is the discovered connector-plugin lane.
    A release allowlist
    that this scan did not read would be a second way to publish a wheel nobody
    checked — and the adapter lane runs the very same `secret_shaped` predicate
    at release time, so leaving it out here would recreate exactly the
    fails-only-after-merge gap this file exists to close.
    """
    packages: list[tuple[str, Path]] = []
    for allowlist_file, key in (
        (".github/release-modules.json", "modules"),
        (".github/release-adapters.json", "adapters"),
        (".github/release-connectors.json", "connectors"),
    ):
        allowlist = json.loads((REPO_ROOT / allowlist_file).read_text(encoding="utf-8"))
        packages += [
            (name, REPO_ROOT / entry["package_dir"])
            for name, entry in allowlist[key].items()
        ]
    return packages


@pytest.mark.parametrize(
    ("distribution", "package_dir"), _publishable_packages(), ids=lambda v: str(v)
)
def test_no_publishable_package_contains_a_secret_shaped_name(
    distribution: str, package_dir: Path
) -> None:
    names = _shipped_files(package_dir)
    assert names, f"{distribution}: no files found under {package_dir}"

    problems = secret_shaped(names)
    assert not problems, (
        f"{distribution} would be refused at publish time:\n  "
        + "\n  ".join(problems)
        + "\n\nRENAME the file. Do not add an exemption — see this module's "
        "docstring for why the false positive is the intended bias."
    )


def _every_package() -> list[Path]:
    return sorted(
        directory
        for directory in (REPO_ROOT / "packages").iterdir()
        if (directory / "pyproject.toml").is_file()
    )


@pytest.mark.parametrize("directory", _every_package(), ids=lambda p: p.name)
def test_every_package_is_covered_not_only_the_allowlisted_ones(
    directory: Path,
) -> None:
    """No package in this repository escapes the scan, whatever its lane.

    This test used to enumerate `("dotmac-kernel", "dotmac-ui")` — the two that
    publish through their own workflows and are therefore absent from the module
    allowlist. An enumeration closes yesterday's hole and reopens tomorrow's: it
    is a list somebody must remember to extend, and the packages that most need
    scanning are precisely the NEW ones nobody has thought about yet.

    `dotmac-auth-oidc` proved the point. It is a fourth shape — a stateless
    protocol adapter — and it sat absent from BOTH allowlists for as long as its
    pilot was unproven. Under the old enumeration it would have been unscanned
    for exactly that period: unmonitored rather than exempt, the gap ADR-0018
    names. It has since joined the adapter allowlist (2026-08-15), which is
    precisely why naming packages was the wrong design — membership moves, and
    the scan must not.

    So the scan is now the COMPLEMENT: every directory under `packages/` with a
    `pyproject.toml`. A package added tomorrow is covered on the commit that
    adds it, in whichever lane it eventually joins or none at all.
    """
    names = _shipped_files(directory)
    assert names, f"{directory.name}: no files found under {directory}"
    problems = secret_shaped(names)
    assert not problems, (
        f"{directory.name} would be refused at publish time:\n  "
        + "\n  ".join(problems)
        + "\n\nRENAME the file. Do not add an exemption — see this module's "
        "docstring for why the false positive is the intended bias."
    )


def test_the_complement_reaches_the_packages_the_allowlists_do_not() -> None:
    """Sensitivity proof for the generalisation above (ADR-0018).

    "Every package is covered" is only meaningful if the complement is actually
    non-empty — otherwise the parametrization is just the allowlist again under
    a broader name, and the gap would be back the moment a package left a lane.
    """
    scanned = {directory.name for directory in _every_package()}
    allowlisted = {name for name, _ in _publishable_packages()}
    unlisted = scanned - allowlisted
    assert {"dotmac-kernel", "dotmac-ui"} <= scanned

    # The named example was `dotmac-auth-oidc` until 2026-08-15, when its pilot
    # ran and it joined the adapter allowlist — and this assertion failing is
    # what forced the question, which is the guard working rather than the guard
    # being in the way. Its own message asked for another unlisted package, so:
    #
    # `dotmac-app-sync` is the current real complement member. It remains out of
    # the adapter lane until the destination-owned pilot proof in its dossier
    # exists. Template Studio used to stand here, but its supply-only module
    # release was authorized on 2026-08-25; a moving example is deliberate,
    # because the assertion failing is what keeps the complement honest.
    assert "dotmac-app-sync" in unlisted, (
        "dotmac-app-sync joined a release allowlist — re-aim this proof at "
        "whatever is still unlisted, or the complement stops proving anything"
    )
    assert len(unlisted) >= 3, sorted(unlisted)


# ── Sensitivity proof (ADR-0018) ────────────────────────────────────────────


def test_the_scan_reaches_a_real_file_tree() -> None:
    """A scan over an empty list passes. The parametrization must have found
    packages, and each must have yielded files."""
    packages = _publishable_packages()
    assert len(packages) >= 6, packages
    for distribution, package_dir in packages:
        assert package_dir.is_dir(), f"{distribution}: {package_dir} missing"
        assert any(n.endswith(".py") for n in _shipped_files(package_dir)), distribution


@pytest.mark.parametrize(
    "planted",
    [
        "dotmac_thing/secrets.py",
        "dotmac_thing/credentials.json",
        "dotmac_thing/id_rsa",
        "config/.env",
        "certs/server.pem",
        "certs/server.key",
    ],
)
def test_the_detector_bites_on_each_marker(planted: str) -> None:
    """Every marker is exercised, so a marker silently dropped from the shared
    list fails here rather than going unnoticed until a wheel ships."""
    assert secret_shaped([planted]), planted


def test_the_marker_list_is_not_empty() -> None:
    """`secret_shaped()` over an empty marker tuple returns nothing for every
    input, which would make every assertion above pass while enforcing nothing."""
    assert len(SECRET_SHAPED_MARKERS) >= 6, SECRET_SHAPED_MARKERS
