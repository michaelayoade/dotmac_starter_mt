"""The committed module catalogue is a generated view, never a second owner.

The package dossiers, manifests, pyprojects and release allowlist each own a
different fact.  ``docs/MODULE_CATALOG.md`` joins those facts for discovery;
this gate prevents the convenient human view from becoming a stale competing
registry.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG = PROJECT_ROOT / "docs" / "MODULE_CATALOG.md"
GENERATOR = PROJECT_ROOT / "scripts" / "module_catalog.py"
PACKAGES = PROJECT_ROOT / "packages"


def _run_catalog_check(output: Path = CATALOG) -> subprocess.CompletedProcess[str]:
    # Fixed interpreter and checked-in script; no value crosses a shell.
    return subprocess.run(  # noqa: S603
        [sys.executable, str(GENERATOR), "--check", "--output", str(output)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_committed_module_catalog_matches_its_machine_sources() -> None:
    """Any manifest/dossier/release change regenerates the discovery view."""
    assert GENERATOR.is_file(), "scripts/module_catalog.py is missing"
    assert CATALOG.is_file(), "docs/MODULE_CATALOG.md is missing"

    result = _run_catalog_check()

    assert result.returncode == 0, result.stderr


def test_module_catalog_guard_detects_a_stale_committed_copy(tmp_path: Path) -> None:
    """Sensitivity proof: a plausible one-cell drift must make the check RED."""
    assert GENERATOR.is_file(), "scripts/module_catalog.py is missing"
    assert CATALOG.is_file(), "docs/MODULE_CATALOG.md is missing"

    stale = tmp_path / "MODULE_CATALOG.md"
    current = CATALOG.read_text(encoding="utf-8")
    stale.write_text(
        current.replace("`reuse-proven`", "`audit-complete`", 1),
        encoding="utf-8",
    )

    result = _run_catalog_check(stale)

    assert result.returncode == 1
    assert "is stale" in result.stderr


def test_catalog_has_one_detail_entry_per_shared_distribution() -> None:
    """A new package cannot exist while remaining undiscoverable."""
    assert CATALOG.is_file(), "docs/MODULE_CATALOG.md is missing"
    catalog = CATALOG.read_text(encoding="utf-8")
    distributions = sorted(
        path.name
        for path in PACKAGES.iterdir()
        if path.is_dir() and (path / "pyproject.toml").is_file()
    )

    assert distributions, "no shared distributions found"
    for distribution in distributions:
        heading = f"### [`{distribution}`]("
        assert (
            catalog.count(heading) == 1
        ), f"{distribution} must have exactly one catalogue detail entry"


def test_catalog_keeps_capability_installation_sets_and_selection_apart() -> None:
    """ADR-0028's whole point: three questions, three columns.

    Collapsing capability into intent is the confusion ADR-0028 supersedes
    ADR-0027 to remove, so the catalogue must never render them as one fact.
    """
    header = next(
        line
        for line in CATALOG.read_text(encoding="utf-8").splitlines()
        if line.startswith("| Distribution |")
    )
    for column in (
        "Module capability",
        "Supported installation sets",
        "This assembly installs",
    ):
        assert column in header, f"catalogue lost the {column!r} column"


def test_selection_column_separates_not_installed_from_not_selected() -> None:
    """A module the assembly does not compose owes it no plane selection.

    Without this distinction the catalogue would flag every uninstalled
    distribution as an invalid assembly, and a guard that cries wolf gets
    ignored precisely when it is right.
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import module_catalog

    installed = module_catalog.assembly_installed(PROJECT_ROOT)
    assert installed, "assembly composes no modules — parser or assembly changed"

    records = {r.distribution: r for r in module_catalog.discover_modules(PROJECT_ROOT)}
    selections = module_catalog.assembly_selections(PROJECT_ROOT)

    uninstalled = next(
        r
        for r in records.values()
        if r.persistence_plane not in {"stateless", "n/a"}
        and r.distribution.removeprefix("dotmac-").replace("-", "_") not in installed
    )
    cell = module_catalog._selection_cell(uninstalled, selections, installed)
    assert cell == "not installed here", (
        f"{uninstalled.distribution} is not composed by this assembly, so it must "
        f"read 'not installed here' rather than {cell!r}"
    )


def test_installation_sets_are_never_blank_for_a_stateful_module() -> None:
    """Absent `supported_plane_sets` means ATOMIC, not unknown.

    Rendering it blank would reproduce the omission-reads-as-intent ambiguity
    that ADR-0028 § 2 exists to make impossible.
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import module_catalog

    for record in module_catalog.discover_modules(PROJECT_ROOT):
        if record.persistence_plane in {"stateless", "n/a"}:
            continue
        assert record.installation_sets, (
            f"{record.distribution} renders no installation set; a stateful "
            "module is atomic by default, never unknown"
        )


def test_catalog_reads_the_connector_release_lane() -> None:
    """A connector allowlist row may not render as 'not allowlisted'."""
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import module_catalog

    records = {r.distribution: r for r in module_catalog.discover_modules(PROJECT_ROOT)}
    connector = records["dotmac-connector-whatsapp"]
    assert connector.release_policy == "connector allowlist"
    assert connector.persistence_plane == "n/a"
