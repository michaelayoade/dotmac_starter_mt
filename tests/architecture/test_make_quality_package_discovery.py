"""Every package source root enrolls in quality checks by existing."""

from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = PROJECT_ROOT / "Makefile"


def _make_sources(root: Path, variable: str) -> set[str]:
    rule = f"_print-sources:\n\t@echo $({variable})\n"
    result = subprocess.run(  # noqa: S603 - fixed make binary and static rule
        [  # noqa: S607 - repository build tool, no shell or user input
            "make",
            "-s",
            "-f",
            str(MAKEFILE),
            "-f",
            "-",
            "_print-sources",
        ],
        cwd=root,
        input=rule,
        text=True,
        capture_output=True,
        check=True,
    )
    return set(result.stdout.split())


def test_quality_targets_consume_the_discovered_connector_roots() -> None:
    source = MAKEFILE.read_text(encoding="utf-8")
    assert "$(wildcard packages/dotmac-connector-*/src/*)" in source
    assert "$(CONNECTOR_SOURCES)" in source.split("type-check:", 1)[1]
    assert "$(CONNECTOR_SOURCES)" in source.split("security:", 1)[1]
    expected = {
        str(path.relative_to(PROJECT_ROOT))
        for path in (PROJECT_ROOT / "packages").glob("dotmac-connector-*/src/*")
        if path.name != "__pycache__"
    }
    assert _make_sources(PROJECT_ROOT, "CONNECTOR_SOURCES") == expected


def test_quality_targets_consume_every_discovered_module_root() -> None:
    source = MAKEFILE.read_text(encoding="utf-8")
    assert "$(wildcard packages/dotmac-*/src/*)" in source
    assert "$(MODULE_SOURCES)" in source.split("type-check:", 1)[1]
    assert "$(MODULE_SOURCES)" in source.split("security:", 1)[1]
    expected = {
        str(path.relative_to(PROJECT_ROOT))
        for path in (PROJECT_ROOT / "packages").glob("dotmac-*/src/*")
        if path.name != "__pycache__"
        and not path.parts[-3].startswith("dotmac-connector-")
    }
    assert _make_sources(PROJECT_ROOT, "MODULE_SOURCES") == expected


def test_a_new_connector_source_root_enrolls_without_a_makefile_edit(
    tmp_path: Path,
) -> None:
    planted = (
        tmp_path / "packages/dotmac-connector-planted/src/dotmac_connector_planted"
    )
    planted.mkdir(parents=True)

    assert _make_sources(tmp_path, "CONNECTOR_SOURCES") == {
        "packages/dotmac-connector-planted/src/dotmac_connector_planted"
    }


def test_a_new_module_source_root_enrolls_without_a_makefile_edit(
    tmp_path: Path,
) -> None:
    planted = tmp_path / "packages/dotmac-planted/src/dotmac_planted"
    planted.mkdir(parents=True)

    assert _make_sources(tmp_path, "MODULE_SOURCES") == {
        "packages/dotmac-planted/src/dotmac_planted"
    }
