"""Explicit per-module persistence-plane composition.

A prerequisite binding answers *where an effect comes from*.  It cannot also
answer *which part of a module this product intends to install*: Vendor CP
physically composes the kernel tenant catalogue while intentionally operating
only platform approval state.  These declarations keep those facts separate.
"""

from __future__ import annotations

import importlib
import os
import re
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

MODULE_PLANES_ENV_VAR: Final[str] = "DOTMAC_MODULE_PLANE_SELECTIONS"
_MODULE_CODE_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class ModulePlane(StrEnum):
    """A persistence plane a stateful module declares."""

    TENANT = "tenant"
    PLATFORM = "platform"


class ModulePlaneSelectionError(ValueError):
    """The assembly's module-plane composition is absent or incoherent."""


def _normalise_planes(planes: Iterable[ModulePlane | str]) -> tuple[ModulePlane, ...]:
    raw = tuple(planes)
    try:
        normalised = tuple(ModulePlane(plane) for plane in raw)
    except ValueError as exc:
        raise ModulePlaneSelectionError(
            f"unknown module plane {exc.args[0]!r}; expected 'tenant' or 'platform'"
        ) from exc
    if not normalised:
        raise ModulePlaneSelectionError("a module plane selection cannot be empty")
    if len(set(normalised)) != len(normalised):
        rendered = [plane.value for plane in normalised]
        raise ModulePlaneSelectionError(
            f"module planes contain a duplicate: {rendered}"
        )
    return tuple(sorted(normalised, key=lambda plane: plane.value))


@dataclass(frozen=True, slots=True)
class ModulePlaneSelection:
    """One assembly's explicit persistence-plane choice for one module."""

    module: str
    planes: Sequence[ModulePlane | str]

    def __post_init__(self) -> None:
        if not _MODULE_CODE_RE.fullmatch(self.module):
            raise ModulePlaneSelectionError(
                "module selection requires a lowercase module code, "
                f"got {self.module!r}"
            )
        object.__setattr__(self, "planes", _normalise_planes(self.planes))


def declared_planes(manifest: object) -> tuple[ModulePlane, ...]:
    """The physical planes a manifest owns, without inferring table shape."""

    planes: list[ModulePlane] = []
    if tuple(getattr(manifest, "tables", ())):
        planes.append(ModulePlane.TENANT)
    if tuple(getattr(manifest, "platform_tables", ())):
        planes.append(ModulePlane.PLATFORM)
    return tuple(sorted(planes, key=lambda plane: plane.value))


def supported_plane_sets(manifest: object) -> tuple[tuple[ModulePlane, ...], ...]:
    """Every plane combination the module's single lineage promises to build.

    An empty manifest declaration preserves the historical atomic contract:
    every declared plane is installed together.  A module becomes selectable
    only by listing more than one supported combination explicitly.
    """

    explicit = tuple(getattr(manifest, "supported_plane_sets", ()))
    if explicit:
        return tuple(_normalise_planes(planes) for planes in explicit)
    default = declared_planes(manifest)
    return (default,) if default else ()


def validate_module_plane_selections(
    manifests: Iterable[object], selections: Iterable[ModulePlaneSelection]
) -> tuple[ModulePlaneSelection, ...]:
    """Validate an assembly declaration and return its immutable form."""

    manifests = tuple(manifests)
    selections = tuple(selections)
    by_code = {
        str(getattr(manifest, "code", "")): manifest
        for manifest in manifests
        if getattr(manifest, "code", None)
    }
    selected: dict[str, ModulePlaneSelection] = {}
    for selection in selections:
        if selection.module in selected:
            raise ModulePlaneSelectionError(
                f"module {selection.module!r} has more than one plane selection"
            )
        manifest = by_code.get(selection.module)
        if manifest is None:
            raise ModulePlaneSelectionError(
                f"plane selection names unknown module {selection.module!r}"
            )
        supported = supported_plane_sets(manifest)
        if len(supported) <= 1:
            raise ModulePlaneSelectionError(
                f"module {selection.module!r} has an atomic plane contract and "
                "does not accept an assembly plane selection"
            )
        if tuple(selection.planes) not in supported:
            rendered = [[plane.value for plane in planes] for planes in supported]
            requested = [ModulePlane(plane).value for plane in selection.planes]
            raise ModulePlaneSelectionError(
                f"module {selection.module!r} does not support planes "
                f"{requested}; supported: {rendered}"
            )
        selected[selection.module] = selection

    for code, manifest in by_code.items():
        supported = supported_plane_sets(manifest)
        if len(supported) > 1 and code not in selected:
            raise ModulePlaneSelectionError(
                f"selectable module {code!r} has no plane selection in this assembly"
            )
    return selections


_lock = threading.RLock()
_installed: dict[str, ModulePlaneSelection] = {}


def install_module_plane_selections(
    selections: Iterable[ModulePlaneSelection],
) -> None:
    """Install one assembly's selections before Alembic builds its graph."""

    resolved: dict[str, ModulePlaneSelection] = {}
    for selection in selections:
        if selection.module in resolved:
            raise ModulePlaneSelectionError(
                f"module {selection.module!r} has more than one plane selection"
            )
        resolved[selection.module] = selection
    with _lock:
        _installed.clear()
        _installed.update(resolved)


def installed_module_plane_selections() -> tuple[ModulePlaneSelection, ...]:
    with _lock:
        return tuple(sorted(_installed.values(), key=lambda item: item.module))


def autoload_module_plane_selections() -> bool:
    """Load ``module.path:ATTRIBUTE`` named by the graph-command environment."""

    spec = os.environ.get(MODULE_PLANES_ENV_VAR, "").strip()
    if not spec:
        return False
    module_path, separator, attribute = spec.partition(":")
    if not separator or not module_path or not attribute:
        raise ModulePlaneSelectionError(
            f"{MODULE_PLANES_ENV_VAR}={spec!r} must be 'module.path:ATTRIBUTE'"
        )
    module = importlib.import_module(module_path)
    selections = getattr(module, attribute)
    install_module_plane_selections(selections)
    return True


def selected_module_planes(module: str) -> frozenset[ModulePlane]:
    """The planes this assembly explicitly selected for ``module``."""

    with _lock:
        selection = _installed.get(module)
    if selection is None and autoload_module_plane_selections():
        with _lock:
            selection = _installed.get(module)
    if selection is None:
        raise ModulePlaneSelectionError(
            f"selectable module {module!r} has no installed plane selection"
        )
    return frozenset(ModulePlane(plane) for plane in selection.planes)


__all__ = [
    "MODULE_PLANES_ENV_VAR",
    "ModulePlane",
    "ModulePlaneSelection",
    "ModulePlaneSelectionError",
    "autoload_module_plane_selections",
    "declared_planes",
    "install_module_plane_selections",
    "installed_module_plane_selections",
    "selected_module_planes",
    "supported_plane_sets",
    "validate_module_plane_selections",
]
