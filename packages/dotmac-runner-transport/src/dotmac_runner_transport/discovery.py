"""Entry-point discovery with fail-closed duplicate and shape checks."""

from __future__ import annotations

from importlib import metadata

from .adapter import RunnerTransportAdapter

__all__ = ["ADAPTER_GROUP", "AdapterDiscoveryError", "discover_adapter"]

ADAPTER_GROUP = "dotmac.runner_transport.adapters"


class AdapterDiscoveryError(RuntimeError):
    pass


def discover_adapter(
    key: str, *, entry_points: tuple[metadata.EntryPoint, ...] | None = None
) -> RunnerTransportAdapter:
    candidates = entry_points
    if candidates is None:
        candidates = tuple(metadata.entry_points(group=ADAPTER_GROUP))
    matches = [entry for entry in candidates if entry.name == key]
    if len(matches) != 1:
        raise AdapterDiscoveryError(
            f"adapter {key!r} resolves to {len(matches)} entry points; "
            "expected exactly one"
        )
    loaded = matches[0].load()
    if not isinstance(loaded, RunnerTransportAdapter):
        raise AdapterDiscoveryError(f"adapter {key!r} does not implement the protocol")
    if loaded.manifest.key != key:
        raise AdapterDiscoveryError(
            f"entry point key {key!r} disagrees with manifest {loaded.manifest.key!r}"
        )
    return loaded
