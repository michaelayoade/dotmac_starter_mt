"""The runtime-brand adapter is assembly composition, not a new owner."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRESENTATION_ROOT = PROJECT_ROOT / "app" / "features" / "presentation"


def _imports(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _authority_crossings(sources: dict[str, str]) -> set[str]:
    crossings: set[str] = set()
    for name, source in sources.items():
        imports = _imports(source)
        has_kernel_branding = any(
            module == "dotmac_kernel.branding" for module in imports
        )
        has_ui = any(module == "dotmac_ui" for module in imports)
        if has_kernel_branding and has_ui:
            crossings.add(name)
    return crossings


def _presentation_sources() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in PRESENTATION_ROOT.glob("*.py")
    }


def test_only_the_service_crosses_from_resolved_branding_to_dotmac_ui() -> None:
    assert _authority_crossings(_presentation_sources()) == {"service.py"}


def test_template_studio_is_not_a_presentation_projection_dependency() -> None:
    imported = {
        module
        for source in _presentation_sources().values()
        for module in _imports(source)
    }
    assert not any(module.startswith("dotmac_template_studio") for module in imported)


def test_the_boundary_detector_still_bites() -> None:
    violating = {
        "service.py": (
            "from dotmac_kernel.branding import load_branding\n"
            "from dotmac_ui import BrandOverride\n"
        ),
        "web.py": (
            "from dotmac_kernel.branding import get_brand\n"
            "import dotmac_ui\n"
        ),
    }
    assert _authority_crossings(violating) == {"service.py", "web.py"}
    assert "dotmac_template_studio" in _imports("import dotmac_template_studio\n")
