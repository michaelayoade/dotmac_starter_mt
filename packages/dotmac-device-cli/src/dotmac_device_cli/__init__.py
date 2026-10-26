"""Stateless device CLI knowledge and dialogue logic (ADR-0077).

Capability profiles (layered data), a dialect interface per vendor, the
prompt/pager/continuation/confirmation state machine run over an injected
channel, a response classifier, vendor-neutral typed operations and the limiter
policy over an injected lock store.

This package performs no I/O. It imports no socket, TLS, SSH, subprocess,
database or HTTP module and never sees a credential: the host authenticates and
passes an interactive channel in. Real device transcripts never enter this
public repository; the corpus under ``tests/transcripts`` is synthetic.
"""

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

__version__ = "0.1.0a1"

__all__ = [
    "CliSession", "Clock", "DetectOutcome", "DeviceBusy", "Dialect", "Evidence",
    "LockStore", "LogoutResult", "MonotonicClock", "NoProfile", "Ok", "OntInventory",
    "OntRef", "OntRow", "OutcomeUnknown", "Priority", "Profile", "ProfileError",
    "ProfileRegistry", "Reason", "RefError", "ShellChannel", "State", "Unreadable",
    "Unsupported", "acquire_slot", "builtin_registry", "intent_key", "list_onts",
    "ont_detail", "service_ports", "slot_key", "version",
]  # fmt: skip
