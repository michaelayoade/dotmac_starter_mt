"""Execute every Markdown example in the dedicated examples tree on hosted CI."""

from __future__ import annotations

import doctest
from pathlib import Path


def test_documented_examples() -> None:
    root = Path(__file__).resolve().parents[2] / "docs/examples"
    pages = sorted(root.rglob("*.md"))
    assert pages, "The documented-example population must not silently disappear"
    for page in pages:
        result = doctest.testfile(str(page), module_relative=False, encoding="utf-8")
        assert result.attempted > 0, f"No executable examples in {page.name}"
        assert result.failed == 0, f"Documented example failed in {page.name}"
