"""Service payloads must be typed — `Any` payloads are banned in services.

Covers the assembly's own features AND every installed module package: a module
is the less-reviewed code of the two, so exempting it would put the weaker
standard exactly where the stronger one is needed.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FEATURES = PROJECT_ROOT / "app" / "features"
MODULE_PACKAGE_ROOTS: tuple[Path, ...] = (
    PROJECT_ROOT / "packages/dotmac-template-studio/src/dotmac_template_studio",
)


def _service_files() -> list[Path]:
    return sorted(FEATURES.glob("*/service.py")) + sorted(
        root / "service.py"
        for root in MODULE_PACKAGE_ROOTS
        if (root / "service.py").is_file()
    )


def test_service_scan_is_not_vacuous() -> None:
    """Assert on the set walked — a glob that finds nothing must fail loudly."""
    scanned = {p.parent.name for p in _service_files()}
    assert "dotmac_template_studio" in scanned, (
        "the installed module's service is not being scanned — "
        f"walked: {sorted(scanned)}"
    )


def test_no_any_typed_payloads_in_services() -> None:
    offenders: list[str] = []
    for service in _service_files():
        text = service.read_text(encoding="utf-8")
        for match in re.finditer(r"payload:\s*Any\b", text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{service.relative_to(PROJECT_ROOT)}:{line}")
    assert not offenders, "Any-typed payloads:\n" + "\n".join(offenders)
