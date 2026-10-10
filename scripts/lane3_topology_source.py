"""Where Lane 3 gets its private vantage topology: ONE injected seam, fail-closed.

``docs/LANE3_EXECUTION_TOPOLOGY.md`` § 4 moves the private topology out of
repository variables and dispatch inputs into one OpenBao KV v2 record,
``secret/dotmac/starter/lane3/vantage-topology`` (``lane3.vantage-topology.v1``,
parsed by :mod:`lane3_topology`). This module is the only place Lane 3 code
obtains it.

The reader is INJECTED. ``openbao_source`` prepares the real B7 JWT/KV
reader, but authenticated deployment composition and live provisioning are
not established by this source change. :class:`RefusingKvTopologySource`
remains the default and refuses as UNANSWERABLE: the topology cannot be read
here yet, which is different from "the topology is wrong". There is no
fallback to repository variables, dispatch inputs or a file.

Binding is by the verified plan's Fleet ``host_id``. The entry's ``address`` is
the target TRANSPORT and its ``far_end`` is where the observer reads what the
target saw (the parser already refuses ``far_end != address``). A missing
entry, an address claimed by more than one ``host_id``, a slot or version that
contradicts the caller, or any parse refusal raises BEFORE any target
connection.

Every refusal names a FIELD, never a value. :class:`BoundTopology` hides every
value from ``repr``. Evidence carries :meth:`BoundTopology.evidence`: the KV
version, the ``probe_vantage_ref`` and the parser's count-only structure.
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import sys
from collections.abc import Mapping
from typing import Any, Final, Protocol

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import lane3_topology as lt

#: Process exit statuses, matching the rest of Lane 3: 1 refused, 2 unanswerable.
EXIT_REFUSED: Final = 1
EXIT_UNANSWERABLE: Final = 2


class TopologyBindingRefused(ValueError):
    """The record was read but cannot bind this run. Names a field only."""

    def __init__(self, field: str) -> None:
        super().__init__(f"Lane 3 topology binding refused at {field}")
        self.field = field


class TopologySourceUnavailable(RuntimeError):
    """The record cannot be read here (no reader is provisioned). UNANSWERABLE."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Lane 3 topology record unavailable: {reason}")
        self.reason = reason


@dataclasses.dataclass(frozen=True)
class TopologyReading:
    """One read of the record: its decoded ``data`` and its KV version."""

    record: Mapping[str, Any] = dataclasses.field(repr=False)
    kv_version: int


class TopologySource(Protocol):
    def read(self) -> TopologyReading:
        """Return the current record and version, or raise."""


class RefusingKvTopologySource:
    """The unconfigured production seam: it never reads and always refuses.

    Explicit ``openbao_source`` composition supplies the B7 reader. This
    default does not assume its role, record or authenticated transport has
    been deployed; no ambient token, endpoint or fallback is used.
    """

    def read(self) -> TopologyReading:
        raise TopologySourceUnavailable(
            "the B7 authenticated topology reader is not provisioned or configured"
        )


@dataclasses.dataclass(frozen=True)
class BoundTopology:
    """The record bound to one Fleet host. Every value is hidden from ``repr``."""

    host_id: str
    kv_version: int
    target_address: str = dataclasses.field(repr=False)
    far_end: str = dataclasses.field(repr=False)
    proxmox_slot: str = dataclasses.field(repr=False)
    probe_host: str = dataclasses.field(repr=False)
    probe_user: str = dataclasses.field(repr=False)
    inside_vantage: str = dataclasses.field(repr=False)
    jump_principal: str = dataclasses.field(repr=False)
    observer_principal: str = dataclasses.field(repr=False)
    probe_ports: tuple[int, ...] = dataclasses.field(repr=False)
    probe_vantage_ref: str = dataclasses.field(repr=False)
    structure: Mapping[str, Any] = dataclasses.field(repr=False)

    def evidence(self) -> dict[str, Any]:
        """The value-free public evidence: version, vantage reference, counts."""
        return {
            "record": lt.RECORD_PATH,
            "kv_version": self.kv_version,
            "probe_vantage_ref": self.probe_vantage_ref,
            "structure": dict(self.structure),
            "host_id": self.host_id,
        }


def _version(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise TopologyBindingRefused("kv_version")
    return value


def parse_reading(
    reading: TopologyReading, *, jump_principal: str | None = None
) -> lt.VantageTopologyV1:
    """Parse one reading, or refuse (field only). No binding yet."""
    _version(reading.kv_version)
    return lt.parse_topology_record(reading.record, jump_principal=jump_principal)


def bind(
    reading: TopologyReading,
    *,
    host_id: str,
    jump_principal: str | None = None,
    expected_version: int | None = None,
) -> BoundTopology:
    """Bind the record to the plan's Fleet ``host_id``, or refuse before any contact."""
    if not isinstance(host_id, str) or not host_id or host_id.strip() != host_id:
        raise TopologyBindingRefused("host_id")
    version = _version(reading.kv_version)
    if expected_version is not None and version != expected_version:
        # The shell and the runner read the record separately; a different
        # version between the two reads is a different topology.
        raise TopologyBindingRefused("kv_version")
    topo = parse_reading(reading, jump_principal=jump_principal)
    entry = topo.targets.get(host_id)
    if entry is None:
        raise TopologyBindingRefused("targets.<host_id>")
    claimants = [h for h, t in topo.targets.items() if t.address == entry.address]
    if len(claimants) != 1:
        # One address named by two Fleet hosts is an ambiguous binding: the
        # lease, the observer and the release would not agree on which host.
        raise TopologyBindingRefused("targets.<host_id>.address")
    return BoundTopology(
        host_id=host_id,
        kv_version=version,
        target_address=entry.address,
        far_end=entry.far_end,
        proxmox_slot=entry.proxmox_slot,
        probe_host=topo.probe_vantage.host,
        probe_user=topo.probe_vantage.ssh_user,
        inside_vantage=topo.inside_vantage.host,
        jump_principal=topo.inside_vantage.jump_principal,
        observer_principal=topo.observer_principal,
        probe_ports=topo.probe_ports,
        probe_vantage_ref=topo.probe_vantage_ref(version),
        structure=topo.structure(),
    )


#: What the shell's pre-runner steps may receive, and nothing else.
SHELL_FIELDS: Final = (
    ("TOPOLOGY_VERSION", "kv_version"),
    ("TOPOLOGY_TARGET", "target_address"),
    ("TOPOLOGY_PROBE_HOST", "probe_host"),
    ("TOPOLOGY_OBSERVER_USER", "observer_principal"),
)


def shell_lines(bound: BoundTopology) -> list[str]:
    """``KEY=value`` lines for ``lane3_rehearse.sh``; read, never echoed."""
    return [f"{key}={getattr(bound, attr)}" for key, attr in SHELL_FIELDS]


def openbao_source(
    *, transport: Any, jwt_supplier: Any, expected_version: int
) -> TopologySource:
    """Explicit launcher composition; never selects ambient credentials or endpoints.

    The same factory creates fresh sources for the shell resolver and runner.
    Both must receive the same approved KV version. Unconfigured CLI execution
    still refuses; deployment must inject the source into main/topology_src.
    """
    from lane3_openbao_topology import OpenBaoTopologySource

    return OpenBaoTopologySource(
        transport=transport,
        jwt_supplier=jwt_supplier,
        expected_version=expected_version,
    )


def github_openbao_source(
    *,
    transport: Any,
    expected_version: int,
    jwt_request_url: str,
    jwt_request_token: str,
    oidc_broker_origin: str,
    oidc_pinned: Any = None,
) -> TopologySource:
    """Bind the real Actions supplier to B7 using an independently approved broker.

    Arguments come from the protected launcher/configuration, never dispatch
    fields. This does not activate the default source or alter job permissions.

    ``oidc_pinned`` is an optional ``lane3_github_oidc.PinnedBrokerTarget`` built
    from a verified handoff grant (``lane3_handoff_client``). When given, the
    token request connects only to that grant's numeric address. When omitted,
    the legacy connect-time hostname resolution is kept for existing callers
    that have no handoff grant.
    """
    from lane3_github_oidc import GithubOidcSupplier

    return openbao_source(
        transport=transport,
        expected_version=expected_version,
        jwt_supplier=GithubOidcSupplier(
            request_url=jwt_request_url,
            request_token=jwt_request_token,
            approved_origin=oidc_broker_origin,
            pinned=oidc_pinned,
        ),
    )


def wireguard_github_openbao_source(
    *,
    expected_version: int,
    jwt_request_url: str,
    jwt_request_token: str,
    oidc_broker_origin: str,
    endpoint_address: str,
    source_address: str,
    interface: str,
    expected_local_public_key: str,
    expected_peer_public_key: str,
    oidc_pinned: Any = None,
) -> TopologySource:
    """Explicit private-tunnel composition; no auto-discovery or permission grant."""
    from lane3_wireguard_topology import WireGuardOpenBaoTransport

    transport = WireGuardOpenBaoTransport(
        endpoint_address=endpoint_address,
        source_address=source_address,
        interface=interface,
        expected_local_public_key=expected_local_public_key,
        expected_peer_public_key=expected_peer_public_key,
    )
    return github_openbao_source(
        transport=transport,
        expected_version=expected_version,
        jwt_request_url=jwt_request_url,
        jwt_request_token=jwt_request_token,
        oidc_broker_origin=oidc_broker_origin,
        oidc_pinned=oidc_pinned,
    )


def default_source() -> TopologySource:
    return RefusingKvTopologySource()


def main(argv: list[str] | None = None, *, source: TopologySource | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lane3_topology_source.py",
        description="Resolve the Lane 3 vantage topology for one Fleet host.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    resolve = sub.add_parser("resolve", help="emit the shell fields for one host")
    resolve.add_argument("--host-id", required=True, help="the Fleet host_id")
    args = parser.parse_args(argv)
    src = source if source is not None else default_source()
    try:
        bound = bind(src.read(), host_id=args.host_id)
    except TopologySourceUnavailable as exc:
        print(f"UNANSWERABLE: {exc}", file=sys.stderr)
        return EXIT_UNANSWERABLE
    except (TopologyBindingRefused, lt.TopologyRecordRefused) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    sys.stdout.write("\n".join(shell_lines(bound)) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
