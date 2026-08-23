"""The UI release proves an installed host can resolve and render components.

Source-tree rendering and wheel-content inspection are separate, insufficient
proofs: the former can hide missing package data, while the latter never asks a
host template loader to resolve the installed file. The release boundary must
exercise both the freshly built wheel and the bytes installed back from the
registry, while Jinja remains a HOST dependency rather than a `dotmac-ui`
runtime dependency.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "release-ui.yml"
TEMPLATE_ARCHIVE_PATH = "dotmac_ui/templates/dotmac_ui/components/empty_state.html"
REQUIRED_ARCHIVE_PATHS = (
    TEMPLATE_ARCHIVE_PATH,
    "dotmac_ui/templates/dotmac_ui/components/map_frame.html",
    "dotmac_ui/templates/dotmac_ui/components/catalog_grid.html",
    "dotmac_ui/templates/dotmac_ui/components/list_surface.html",
    "dotmac_ui/templates/dotmac_ui/components/recent_activity.html",
    "dotmac_ui/static/dotmac-ui/dotmac-ui-behaviors-1.js",
)


def _assert_component_release_proof(workflow: str) -> None:
    for archive_path in REQUIRED_ARCHIVE_PATHS:
        assert (
            archive_path in workflow
        ), f"release artifact inspection does not require {archive_path}"
    assert (
        workflow.count('pip install --quiet "jinja2==3.1.6"') == 2
    ), "both clean hosts must install Jinja independently of dotmac-ui"
    assert (
        workflow.count("--no-deps") == 2
    ), "both dotmac-ui installs must prove the package works without dependencies"
    for seam in (
        "from jinja2 import Environment, FileSystemLoader, StrictUndefined",
        "dotmac_ui.template_dir()",
        "dotmac_ui.EMPTY_STATE",
        "dotmac_ui.COMPONENTS",
        "behavior_script_path()",
        "createRepeatableFields",
        '"No invoices"',
    ):
        assert (
            workflow.count(seam) >= 2
        ), f"the built-wheel and registry-installed smokes must both exercise {seam}"


def test_release_smokes_the_component_from_both_installed_artifacts() -> None:
    _assert_component_release_proof(WORKFLOW.read_text(encoding="utf-8"))


def test_the_release_component_proof_guard_still_bites() -> None:
    """Sensitivity proof: registry-only asset checks are not enough."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    weakened = workflow.replace("dotmac_ui.template_dir()", "Path('/checkout')", 1)
    with pytest.raises(AssertionError, match="built-wheel and registry-installed"):
        _assert_component_release_proof(weakened)
