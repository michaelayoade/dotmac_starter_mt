"""Assembly surface selection (Starter parity programme, surface contract 1.2).

A module ships API routers and contract-2 web surfaces; it never decides that
an assembly serves them.  Without a selection, a pin bump silently mounts
whatever a new module release adds.  ``ProductAssemblySpec.surface_selection``
makes the assembly state, per module, which ``api_routers`` and which named
``web_surfaces`` it serves.  Anything not named stays unmounted.

* :class:`SurfaceSelection` lists ``(module code, surface code)`` pairs and the
  module codes whose ``api_routers`` are served.
* :data:`SELECT_ALL` is the explicit "everything the installed modules
  declare" sentinel (the reference app uses it deliberately).
* Contract-1 ``web_routers``/``nav`` are selected with the surface code
  ``"legacy"`` (``("module", "legacy")``), valid only for a module that has
  ``web_routers``.  Under an explicit selection they mount only if named.
* ``None`` on the spec keeps the pre-selection behaviour for assemblies that
  have not adopted this contract yet (a logged, visible compatibility mode, not
  a safe default).  An assembly that sets a selection is fail-closed: an entry
  naming an absent module, surface or router set refuses the boot, and
  unselected surfaces and routers are never mounted.

Granularity (known limitation).  API routers are selected per MODULE and routes
per SURFACE: a new module, a new surface, or a surface/module the assembly never
named adds nothing, but a new route or router inside a module or surface that IS
selected mounts.  That residue is caught by the assembly's committed
route-inventory snapshot test (``dotmac_kernel.testing.route_inventory``).
Named router sets are a later contract-generation item, recorded here on
purpose.

Import-safe: pure data over manifests; no engine, no I/O, no FastAPI.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dotmac_kernel.modules import ModuleManifest


#: The surface code naming a module's contract-1 `web_routers`/`nav`.
LEGACY_SURFACE = "legacy"


class SurfaceSelectionError(ValueError):
    """The assembly's surface selection is malformed or names something absent."""


@dataclass(frozen=True)
class SurfaceSelection:
    """Which module surfaces and API router sets an assembly serves."""

    surfaces: Collection[tuple[str, str]] = ()
    api_routers: Collection[str] = ()
    select_all: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.select_all, bool):
            raise SurfaceSelectionError("select_all must be a bool")
        pairs: list[tuple[str, str]] = []
        for entry in tuple(self.surfaces):
            if (
                not isinstance(entry, tuple)
                or len(entry) != 2
                or not all(isinstance(part, str) and part for part in entry)
            ):
                raise SurfaceSelectionError(
                    f"surface selection entry {entry!r} must be a "
                    "(module code, surface code) pair of non-empty strings"
                )
            pairs.append(entry)
        routers: list[str] = []
        for code in tuple(self.api_routers):
            if not isinstance(code, str) or not code:
                raise SurfaceSelectionError(
                    f"api router selection {code!r} must be a module code"
                )
            routers.append(code)
        if len(set(pairs)) != len(pairs):
            raise SurfaceSelectionError("surface selection repeats an entry")
        if len(set(routers)) != len(routers):
            raise SurfaceSelectionError("api router selection repeats a module")
        if self.select_all and (pairs or routers):
            raise SurfaceSelectionError(
                "SELECT_ALL is exclusive: it cannot also list entries"
            )
        object.__setattr__(self, "surfaces", frozenset(pairs))
        object.__setattr__(self, "api_routers", frozenset(routers))

    def validate_against(self, manifests: Sequence[ModuleManifest]) -> None:
        """Refuse any entry naming an absent module, surface or router set."""
        if self.select_all:
            return
        by_code = {manifest.code: manifest for manifest in manifests}
        problems: list[str] = []
        for module, surface in sorted(self.surfaces):
            manifest = by_code.get(module)
            if manifest is None:
                problems.append(f"surface {module}/{surface}: module not installed")
            elif surface == LEGACY_SURFACE:
                if not manifest.web_routers:
                    problems.append(
                        f"surface {module}/{surface}: module has no contract-1 "
                        "web_routers"
                    )
            elif surface not in {s.code for s in manifest.web_surfaces}:
                problems.append(
                    f"surface {module}/{surface}: module has no such surface"
                )
        for module in sorted(self.api_routers):
            manifest = by_code.get(module)
            if manifest is None:
                problems.append(f"api routers {module}: module not installed")
            elif not manifest.api_routers:
                problems.append(f"api routers {module}: module declares none")
        if problems:
            raise SurfaceSelectionError(
                "surface_selection names absent declarations: " + "; ".join(problems)
            )

    def apply(self, manifests: Sequence[ModuleManifest]) -> list[ModuleManifest]:
        """Return the manifests reduced to what is selected (never widened)."""
        if self.select_all:
            return list(manifests)
        reduced: list[ModuleManifest] = []
        for manifest in manifests:
            surfaces = tuple(
                s
                for s in manifest.web_surfaces
                if (manifest.code, s.code) in self.surfaces
            )
            routers = manifest.api_routers if manifest.code in self.api_routers else ()
            legacy = (manifest.code, LEGACY_SURFACE) in self.surfaces
            web_routers = manifest.web_routers if legacy else ()
            nav = manifest.nav if legacy else ()
            if (
                surfaces == tuple(manifest.web_surfaces)
                and routers == tuple(manifest.api_routers)
                and web_routers == tuple(manifest.web_routers)
            ):
                reduced.append(manifest)
            else:
                reduced.append(
                    dataclasses.replace(
                        manifest,
                        web_surfaces=surfaces,
                        api_routers=routers,
                        web_routers=web_routers,
                        nav=nav,
                    )
                )
        return reduced

    def unselected(self, manifests: Sequence[ModuleManifest]) -> tuple[str, ...]:
        """Human-readable names of everything declared but not selected."""
        if self.select_all:
            return ()
        out: list[str] = []
        for manifest in manifests:
            if manifest.api_routers and manifest.code not in self.api_routers:
                out.append(f"{manifest.code}:api")
            if manifest.web_routers and (manifest.code, LEGACY_SURFACE) not in (
                self.surfaces
            ):
                out.append(f"{manifest.code}/{LEGACY_SURFACE}")
            out.extend(
                f"{manifest.code}/{s.code}"
                for s in manifest.web_surfaces
                if (manifest.code, s.code) not in self.surfaces
            )
        return tuple(out)


SELECT_ALL = SurfaceSelection(select_all=True)

__all__ = [
    "LEGACY_SURFACE",
    "SELECT_ALL",
    "SurfaceSelection",
    "SurfaceSelectionError",
]
