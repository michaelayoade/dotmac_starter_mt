"""Every releasable module states its version in three places, and they agree.

A module carries its version on three independent surfaces, and nothing kept
them in step:

1. `packages/<dir>/pyproject.toml` — what the wheel is PUBLISHED as. The
   release workflow reads this and nothing else, so it is the authority.
2. `<import_name>.__version__` — what a RUNTIME reports. The Integrator
   assembly's `health.py` prints `dotmac_integration.__version__`; a support
   bundle, a log line, and a defect workaround all branch on this value.
3. `ModuleManifest.version` — what the composed kernel's installed-module
   INVENTORY reports (`ModuleRegistry.inventory()`), i.e. the version an
   operator sees next to the `alembic_version` rows that module owns.

Each is read by a different audience, so a drift is not cosmetic: it makes a
deployment lie about which code it is running, to whoever happens to be asking.
The lie survives exactly as long as nobody diffs three files by hand.

It had survived a while. When this test was written, four of the seven
releasable modules disagreed with themselves:

| distribution | pyproject | `__version__` | manifest |
|---|---|---|---|
| dotmac-release-catalog | 0.1.0a3 | 0.1.0a3 | **0.1.0a2** |
| dotmac-ticketing | 0.1.0a2 | **0.1.0a1** | 0.1.0a2 |
| dotmac-imports | 0.1.0a2 | **0.1.0a1** | 0.1.0a2 |
| dotmac-integration | 0.1.0a3 | 0.1.0a3 | **0.1.0a1** |

Note the drift is in a different position each time — which is why the
per-module tests that existed (`test_files_module.py`'s pairwise check, added
after files drifted the same way) did not generalise. `dotmac-files` had a
guard; the other six did not, and three of them were broken.

**Discovery, not enumeration.** The module set comes from
`.github/release-modules.json` — the closed allowlist that already decides what
may be published. A module named here is a module whose version is a public
fact, so adding an entry there enrols it in this gate automatically. No name is
written in this file; a hand-maintained list would go stale exactly when a new
module is added, which is the moment it matters.

**No skipping.** A module whose manifest exposes no version, or whose package
exposes no `__version__`, FAILS here rather than being quietly passed over. A
surface that cannot answer "what version am I" is the same defect as one that
answers wrongly — a skip would hide a module with no version at all, which is
strictly worse than one with a stale version.

Compares checked-in SOURCES, never `importlib.metadata.version`: in an editable
install the installed metadata can itself be stale, so asserting against it
would let every surface be wrong together.
"""

from __future__ import annotations

import importlib
import json
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST_PATH = REPO_ROOT / ".github" / "release-modules.json"


def _allowlist() -> dict[str, dict]:
    return json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))["modules"]


def _distributions() -> list[str]:
    return sorted(_allowlist())


# ── The comparison, as a pure function ───────────────────────────────────────
#
# Factored out of the live test so the sensitivity proof below can drive it with
# a version deliberately wrong in each of the three positions. A proof that only
# perturbs one position proves the gate notices ONE surface, and the drift table
# above shows real drift lands in all three.


def _disagreements(
    distribution: str,
    *,
    distribution_version: object,
    runtime_version: object,
    manifest_version: object,
) -> tuple[str, ...]:
    """Every way this module's three version surfaces fail to agree.

    Empty means consistent. `None` — a surface that does not exist — is a
    disagreement in its own right, not an excuse to skip the comparison.
    """
    problems: list[str] = []
    surfaces = {
        "pyproject [tool.poetry] version": distribution_version,
        "__version__": runtime_version,
        "ModuleManifest.version": manifest_version,
    }
    for label, value in surfaces.items():
        if value is None:
            problems.append(
                f"{distribution}: {label} is missing — a releasable module must "
                "state its version on all three surfaces"
            )
        elif not isinstance(value, str) or not value.strip():
            problems.append(f"{distribution}: {label} is not a version: {value!r}")

    if problems:
        return tuple(problems)

    if len({distribution_version, runtime_version, manifest_version}) > 1:
        problems.append(
            f"{distribution} disagrees with itself: pyproject declares "
            f"{distribution_version!r} (what the wheel PUBLISHES as), "
            f"__version__ reports {runtime_version!r} (what a running "
            f"deployment logs), ModuleManifest.version reports "
            f"{manifest_version!r} (what the composed inventory shows). "
            "pyproject is the authority — bump the other two to match it in "
            "the same change."
        )
    return tuple(problems)


def _pyproject_version(package_dir: str) -> str | None:
    data = tomllib.loads(
        (REPO_ROOT / package_dir / "pyproject.toml").read_text(encoding="utf-8")
    )
    return data.get("tool", {}).get("poetry", {}).get("version")


# ── The gate ─────────────────────────────────────────────────────────────────


def test_the_allowlist_yields_modules_to_check() -> None:
    """Specificity for the parametrized test below.

    A parametrize over an empty sequence passes vacuously, so an allowlist that
    failed to parse — or a key rename — would turn this whole file green while
    checking nothing.
    """
    distributions = _distributions()
    assert len(distributions) >= 5, distributions
    for distribution in distributions:
        entry = _allowlist()[distribution]
        assert {"package_dir", "import_name", "manifest_attr"} <= set(entry)


@pytest.mark.parametrize("distribution", _distributions())
def test_the_three_version_surfaces_agree(distribution: str) -> None:
    entry = _allowlist()[distribution]
    package = importlib.import_module(entry["import_name"])
    manifest = getattr(package, entry["manifest_attr"], None)
    assert manifest is not None, (
        f"{entry['import_name']} exposes no {entry['manifest_attr']!r} — the "
        "release verifier resolves that attribute on the installed wheel"
    )

    problems = _disagreements(
        distribution,
        distribution_version=_pyproject_version(entry["package_dir"]),
        runtime_version=getattr(package, "__version__", None),
        manifest_version=getattr(manifest, "version", None),
    )
    assert not problems, "\n".join(problems)


# ── Sensitivity proof ────────────────────────────────────────────────────────

_ALIGNED = {
    "distribution_version": "0.1.0a7",
    "runtime_version": "0.1.0a7",
    "manifest_version": "0.1.0a7",
}


def test_the_comparison_accepts_an_aligned_module() -> None:
    """Without this the proofs below are satisfied by a check that always
    fails."""
    assert _disagreements("dotmac-example", **_ALIGNED) == ()


@pytest.mark.parametrize(
    "position", ["distribution_version", "runtime_version", "manifest_version"]
)
def test_the_comparison_catches_drift_in_every_position(position: str) -> None:
    """SENSITIVITY PROOF, once per surface.

    Real drift has landed in all three positions (see the table in the module
    docstring), and a check that compares, say, only `__version__` against
    `pyproject` would have passed for `dotmac-integration` and
    `dotmac-release-catalog` while both shipped a stale manifest.
    """
    drifted = {**_ALIGNED, position: "0.1.0a6"}
    problems = _disagreements("dotmac-example", **drifted)
    assert problems, f"drift in {position} was not detected"
    assert "disagrees with itself" in problems[0]


@pytest.mark.parametrize(
    "position", ["distribution_version", "runtime_version", "manifest_version"]
)
def test_a_missing_surface_fails_loudly_rather_than_being_skipped(
    position: str,
) -> None:
    """A module that cannot state its version is not a module this gate has
    nothing to say about."""
    absent = {**_ALIGNED, position: None}
    problems = _disagreements("dotmac-example", **absent)
    assert problems, f"a missing {position} was tolerated"
    assert "is missing" in problems[0]
