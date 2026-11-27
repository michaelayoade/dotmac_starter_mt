"""The surface conformance kit, run over the reference module (Template Studio)."""

from __future__ import annotations

from dotmac_kernel.testing.surface_conformance import assert_surface_conformance
from dotmac_template_studio import module


def test_template_studio_conforms_under_admin_and_root_prefixes() -> None:
    report = assert_surface_conformance(module)
    assert report.prefixes == ("/admin", "/")
    assert report.routes_checked > 0
    assert report.templates_checked > 0
    assert report.nav_items_checked > 0
