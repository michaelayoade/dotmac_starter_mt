"""The surface conformance kit, run over the durable-timers staff surface."""

from __future__ import annotations

from dataclasses import replace

from dotmac_durable_timers.manifest import module
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from dotmac_kernel.testing import surface_conformance as kit
from dotmac_kernel.testing.surface_conformance import assert_surface_conformance


def _spec_for_prefix(prefix: str):
    # The kit's own composition plus the explicit plane selection a dual-plane
    # module requires; nothing else is added.
    spec = kit._build_spec(module, (), prefix, None)
    return replace(
        spec,
        module_planes=(
            ModulePlaneSelection(module="durable_timers", planes=(ModulePlane.TENANT,)),
        ),
    )


def test_durable_timers_conforms_under_admin_and_root_prefixes() -> None:
    report = assert_surface_conformance(module, spec_for_prefix=_spec_for_prefix)
    assert report.routes_checked > 0
    assert report.templates_checked > 0
    assert report.nav_items_checked > 0
