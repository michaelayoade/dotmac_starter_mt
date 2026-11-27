"""Route inventory for an assembly's committed snapshot test.

``surface_selection`` is granular to a module's API routers and a surface's
routes: a NEW module or surface adds nothing, but a new route inside an
already-selected module or surface mounts. Each assembly therefore commits a
snapshot of what it mounts, and CI fails in the PR that bumps a pin if the set
changes::

    from dotmac_kernel.testing.route_inventory import route_inventory

    def test_mounted_routes_match_the_committed_snapshot() -> None:
        actual = [list(r) for r in route_inventory(app)]
        assert actual == json.loads(SNAPSHOT.read_text())

Named router sets (finer selection) are a later contract-generation item.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute


def route_inventory(app: FastAPI) -> list[tuple[str, str, str]]:
    """Every mounted ``(method, path, route name)``, sorted and deduplicated."""
    return sorted(
        {
            (method, route.path, route.name)
            for route in app.routes
            if isinstance(route, APIRoute)
            for method in (route.methods or set())
        }
    )


__all__ = ["route_inventory"]
