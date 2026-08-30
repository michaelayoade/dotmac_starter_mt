"""The UI release proves an INSTALLED host can resolve and render components.

Source-tree rendering and wheel-content inspection are separate, insufficient
proofs: the former can hide missing package data, while the latter never asks a
host template loader to resolve the installed file. The release boundary must
exercise both the freshly built wheel and the bytes installed back from the
registry, while Jinja remains a HOST dependency rather than a `dotmac-ui`
runtime dependency.

Two things this file learned the hard way, both of which shape how it checks.

**A proof written twice drifts.** The workflow used to inline the smoke in both
stages, and this guard compensated by counting seam strings in the YAML. That
counts copies, not agreement. The smoke now lives in
`scripts/verify_ui_release_artifact.py`, and this guard requires BOTH stages to
invoke that one file.

**An enumerated proof goes stale silently.** The inline smoke exercised
`empty_state` by name. `map_frame` was published in the source tree and shipped
in no release proof at all: `dotmac-ui 0.1.0a7` went to the registry carrying
only `empty_state`, an ERP install-back later confirmed it, and nothing here
failed. So the REQUIRED SET is now derived from `dotmac_ui.COMPONENTS` — adding
a component to the contract fails this file until the release lane proves it.
"""

from __future__ import annotations

import re
from pathlib import Path

import dotmac_ui
import pytest

REPO = Path(__file__).resolve().parent.parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "release-ui.yml"
VERIFIER = REPO / "scripts" / "verify_ui_release_artifact.py"

#: The one file both release stages must run against their installed artifact.
VERIFIER_INVOCATION = "scripts/verify_ui_release_artifact.py"


def _archive_path(template: str) -> str:
    """Where a loader-relative template lands inside the built wheel."""
    return f"dotmac_ui/templates/{template}"


def _assert_every_published_component_is_in_the_wheel_gate(workflow: str) -> None:
    """The archive inspection must name every component the package publishes."""
    for component in dotmac_ui.COMPONENTS:
        archive_path = _archive_path(component.template)
        assert archive_path in workflow, (
            f"the release artifact inspection does not require {archive_path}; "
            f"`{component.macro}` is a published component that would ship "
            "unproven — this is exactly how 0.1.0a7 reached the registry with "
            "map_frame missing"
        )


#: An invocation of the proof by a VIRTUALENV interpreter — the runner's own
#: `python` would import the repository checkout, which is the whole failure
#: this lane exists to rule out. Deliberately not anchored to a fixed venv
#: directory: what matters is that the interpreter is not the runner's.
_VENV_INVOCATION = re.compile(rf"\S+/bin/python {re.escape(VERIFIER_INVOCATION)}")


def _venv_invocations(workflow: str) -> list[str]:
    return _VENV_INVOCATION.findall(workflow)


def _assert_both_stages_run_the_shared_proof(workflow: str) -> None:
    assert workflow.count(VERIFIER_INVOCATION) == 2, (
        "the built-wheel stage and the registry-installed stage must BOTH run "
        f"{VERIFIER_INVOCATION}; found "
        f"{workflow.count(VERIFIER_INVOCATION)} invocation(s)"
    )
    assert (
        workflow.count('pip install --quiet "jinja2==3.1.6"') == 2
    ), "both clean hosts must install Jinja independently of dotmac-ui"
    assert (
        workflow.count("--no-deps") == 2
    ), "both dotmac-ui installs must prove the package works without dependencies"
    venv_invocations = _venv_invocations(workflow)
    assert len(venv_invocations) == 2, (
        "each proof must run under the clean venv's own interpreter, so the "
        f"subject is the installed artifact; found {venv_invocations}"
    )


def _assert_the_proof_measures_the_installed_artifact(verifier: str) -> None:
    """The script must refuse a source checkout and check the real bytes."""
    for seam, why in (
        (
            "sysconfig",
            "the proof must locate this interpreter's site-packages to prove "
            "the import came from the installed artifact",
        ),
        (
            "is_relative_to",
            "the proof must assert the imported package lives inside "
            "site-packages, not merely that it imported",
        ),
        (
            "sha256",
            "the proof must recompute the published manifest digests from the "
            "installed bytes",
        ),
        (
            "module.COMPONENTS",
            "the component set must be read from the INSTALLED package, never "
            "from a hand-written list that can go stale",
        ),
        (
            "FileSystemLoader",
            "resolution must go through a host Jinja loader; archive "
            "inspection alone never proves the installed layout is addressable",
        ),
    ):
        assert seam in verifier, f"{VERIFIER.name} is missing `{seam}`: {why}"


def _assert_the_proof_covers_every_published_component(verifier: str) -> None:
    """Every declared macro needs a render case, or the proof refuses to run."""
    for component in dotmac_ui.COMPONENTS:
        assert f'"{component.macro}"' in verifier, (
            f"{VERIFIER.name} has no render case for `{component.macro}`; the "
            "proof would refuse at release time, which is later than here"
        )


def test_release_smokes_every_component_from_both_installed_artifacts() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    _assert_every_published_component_is_in_the_wheel_gate(workflow)
    _assert_both_stages_run_the_shared_proof(workflow)


def test_the_shared_proof_measures_the_installed_bytes() -> None:
    verifier = VERIFIER.read_text(encoding="utf-8")
    _assert_the_proof_measures_the_installed_artifact(verifier)
    _assert_the_proof_covers_every_published_component(verifier)


# --------------------------------------------------------------------------
# Sensitivity proofs. Each guard above passes over a currently-correct tree, so
# each needs a demonstration that it fails when the property it names is gone.
# --------------------------------------------------------------------------


def test_the_wheel_gate_guard_still_bites() -> None:
    """Dropping a component's template from the inspection must fail."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    dropped = _archive_path(dotmac_ui.COMPONENTS[-1].template)
    weakened = workflow.replace(dropped, "dotmac_ui/py.typed")
    with pytest.raises(AssertionError, match="does not require"):
        _assert_every_published_component_is_in_the_wheel_gate(weakened)


def test_the_two_stage_guard_still_bites() -> None:
    """One stage running the proof is not two stages running it."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    weakened = workflow.replace(VERIFIER_INVOCATION, "true", 1)
    with pytest.raises(AssertionError, match="must BOTH run"):
        _assert_both_stages_run_the_shared_proof(weakened)


def test_the_venv_interpreter_guard_still_bites() -> None:
    """Running the proof with the runner's python would import the checkout."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    # Derived from the workflow, not hardcoded: the sensitivity proof must
    # keep working when the venv path changes.
    real = _venv_invocations(workflow)[-1]
    weakened = workflow.replace(real, f"python {VERIFIER_INVOCATION}", 1)
    with pytest.raises(AssertionError, match="clean venv's own interpreter"):
        _assert_both_stages_run_the_shared_proof(weakened)


def test_the_installed_artifact_guard_still_bites() -> None:
    """A proof that no longer pins its subject to site-packages must fail."""
    verifier = VERIFIER.read_text(encoding="utf-8")
    weakened = verifier.replace("is_relative_to", "exists")
    with pytest.raises(AssertionError, match="is_relative_to"):
        _assert_the_proof_measures_the_installed_artifact(weakened)


def test_the_manifest_digest_guard_still_bites() -> None:
    """A proof that stops recomputing digests must fail."""
    verifier = VERIFIER.read_text(encoding="utf-8")
    weakened = verifier.replace("sha256", "md_five")
    with pytest.raises(AssertionError, match="sha256"):
        _assert_the_proof_measures_the_installed_artifact(weakened)


def test_the_component_coverage_guard_still_bites() -> None:
    """A published component with no render case must fail here, not at release."""
    verifier = VERIFIER.read_text(encoding="utf-8")
    dropped = dotmac_ui.COMPONENTS[-1].macro
    weakened = verifier.replace(f'"{dropped}"', '"retired_component"')
    with pytest.raises(AssertionError, match="no render case"):
        _assert_the_proof_covers_every_published_component(weakened)


def test_the_component_set_is_not_empty() -> None:
    """A check over an empty set passes for the wrong reason."""
    assert (
        dotmac_ui.COMPONENTS
    ), "no published components — every guard above is vacuous"
    assert len(dotmac_ui.COMPONENTS) >= 2, (
        "the derived-set guards were written because a SECOND component went "
        "unproven; with one component they cannot distinguish derivation from "
        "a hardcoded name"
    )
