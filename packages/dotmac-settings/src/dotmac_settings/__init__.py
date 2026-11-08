"""Validated operator settings over the kernel ``domain_settings`` store.

A consuming assembly declares its keys (defaults + validators), optional
whole-object override keys, relation rules and an environment overlay;
:class:`SettingsStore` then enforces strict reads, unknown-key refusal,
stored-NULL handling, override expansion with split-row refusal, and
compare-and-swap writes for override keys. The package owns no tables
and names no product.
"""

from __future__ import annotations

from typing import Final

from .manifest import module
from .store import KeySpec, OverrideSpec, SettingsStore, WholeObjectConflict

__version__: Final = "0.1.0a1"

__all__ = [
    "KeySpec",
    "OverrideSpec",
    "SettingsStore",
    "WholeObjectConflict",
    "__version__",
    "module",
]
