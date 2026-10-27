"""Stateless device CLI knowledge and dialogue logic (ADR-0077).

Capability profiles (layered data), a dialect interface per vendor, the
prompt/pager/continuation/confirmation state machine run over an injected
channel, a response classifier, vendor-neutral typed operations and the limiter
policy over an injected lock store.

This package performs no I/O. It imports no socket, TLS, SSH, subprocess,
database or HTTP module and never sees a credential: the host authenticates and
passes an interactive channel in. Real device transcripts never enter this
public repository; the corpus under ``tests/transcripts`` is synthetic.

This module is the SINGLE CANONICAL public-surface manifest, deliberately
shaped like ``dotmac_kernel.__init__``, ``dotmac_ui.__init__`` and
``dotmac_auth_oidc.__init__`` so the packages are governed the same way:
``SUPPORTED_MODULES`` (which submodules are public), ``INTERNAL_MODULES``
(explicitly private), each supported module's own ``__all__``, and the curated
top-level ``__all__`` re-exported below.
"""

from __future__ import annotations

from typing import Final

from dotmac_device_cli.core.channel import Clock, MonotonicClock, ShellChannel
from dotmac_device_cli.core.classifier import OntInventory
from dotmac_device_cli.core.dialect import Dialect, OntRef, OntRow, RefError
from dotmac_device_cli.core.limiter import (
    DeviceBusy,
    LockStore,
    Priority,
    acquire_slot,
    intent_key,
    slot_key,
)
from dotmac_device_cli.core.ops import list_onts, ont_detail, service_ports, version
from dotmac_device_cli.core.profile import NoProfile, Profile, ProfileError
from dotmac_device_cli.core.registry import (
    DetectOutcome,
    ProfileRegistry,
    builtin_registry,
)
from dotmac_device_cli.core.results import (
    Evidence,
    Ok,
    OutcomeUnknown,
    Reason,
    Unreadable,
    Unsupported,
)
from dotmac_device_cli.core.session import CliSession, LogoutResult, State

# Not read from `importlib.metadata`: the package must import from a bare source
# checkout, where installed metadata may be absent or stale. Kept in sync with
# pyproject by `tests/architecture/test_device_cli_package.py`.
__version__: Final[str] = "0.1.0a1"

# The exhaustive list of submodules a consumer may import from. A name is public
# only if it is also in that module's own `__all__`.
SUPPORTED_MODULES: Final[frozenset[str]] = frozenset(
    {
        "dotmac_device_cli.conformance",
        "dotmac_device_cli.core.channel",
        "dotmac_device_cli.core.classifier",
        "dotmac_device_cli.core.dialect",
        "dotmac_device_cli.core.layers",
        "dotmac_device_cli.core.limiter",
        "dotmac_device_cli.core.ops",
        "dotmac_device_cli.core.profile",
        "dotmac_device_cli.core.registry",
        "dotmac_device_cli.core.results",
        "dotmac_device_cli.core.session",
        "dotmac_device_cli.dialects",
        "dotmac_device_cli.dialects.huawei",
        "dotmac_device_cli.dialects.zte",
        "dotmac_device_cli.testing",
    }
)

# No submodule is private by declaration: the package ships no `_`-prefixed
# module. Private names are the `_`-prefixed names inside the modules above.
INTERNAL_MODULES: Final[frozenset[str]] = frozenset()

__all__ = [
    "INTERNAL_MODULES", "SUPPORTED_MODULES",
    "CliSession", "Clock", "DetectOutcome", "DeviceBusy", "Dialect", "Evidence",
    "LockStore", "LogoutResult", "MonotonicClock", "NoProfile", "Ok", "OntInventory",
    "OntRef", "OntRow", "OutcomeUnknown", "Priority", "Profile", "ProfileError",
    "ProfileRegistry", "Reason", "RefError", "ShellChannel", "State", "Unreadable",
    "Unsupported", "__version__", "acquire_slot", "builtin_registry", "intent_key",
    "list_onts", "ont_detail", "service_ports", "slot_key", "version",
]  # fmt: skip
