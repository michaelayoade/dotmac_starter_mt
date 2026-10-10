"""Explicit HTTP-over-WireGuard adapter; never an automatic HTTPS fallback.

The approved launcher pins both public keys, inner IPs and interface from
independently reviewed configuration. Kernel metadata is re-read before every
connection and before sending credentials. Root/kernel/WireGuard configuration
remain trusted; these checks do not resist a malicious host administrator or
provide a route-lock/anti-race guarantee against that administrator.
"""

from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import os
import re
import subprocess
import time
from collections.abc import Callable

from lane3_openbao_topology import (
    MAX_RESPONSE,
    HttpsOpenBaoTransport,
    TopologySourceUnavailable,
)


def _refuse() -> TopologySourceUnavailable:
    return TopologySourceUnavailable("transport.wireguard")


def public_command(args: list[str]) -> str:
    """Only this adapter's fixed public-state commands; no shell or key dump."""
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
    )
    if result.returncode or len(result.stdout) > MAX_RESPONSE:
        raise _refuse()
    return result.stdout.strip()


class WireGuardOpenBaoTransport(HttpsOpenBaoTransport):
    """Source-bound HTTP to a fixed peer over a freshly checked encrypted route."""

    def __init__(
        self,
        *,
        endpoint_address: str,
        source_address: str,
        interface: str,
        expected_local_public_key: str,
        expected_peer_public_key: str,
        command: Callable[[list[str]], str] = public_command,
        clock: Callable[[], float] = time.time,
    ) -> None:
        try:
            self._destination = ipaddress.ip_address(endpoint_address)
            self._source = ipaddress.ip_address(source_address)
            if (
                str(self._destination) != endpoint_address
                or str(self._source) != source_address
                or self._destination.version != self._source.version
                or self._destination == self._source
                or re.fullmatch(r"wg[a-zA-Z0-9_-]{0,13}", interface) is None
            ):
                raise _refuse()
            for key in (expected_local_public_key, expected_peer_public_key):
                raw = base64.b64decode(key, validate=True)
                if len(raw) != 32 or base64.b64encode(raw).decode() != key:
                    raise _refuse()
            if expected_local_public_key == expected_peer_public_key:
                raise _refuse()
            self._host = endpoint_address
            self._port = 8200
            self._interface = interface
            self._local_key = expected_local_public_key
            self._peer_key = expected_peer_public_key
            self._command = command
            self._clock = clock
            self._guard()
        except Exception:
            raise _refuse() from None

    def _wg(self, field: str) -> str:
        prefix = [] if os.geteuid() == 0 else ["/usr/bin/sudo", "-n"]
        return self._command([*prefix, "/usr/bin/wg", "show", self._interface, field])

    def _guard(self) -> None:
        try:
            if self._wg("public-key") != self._local_key:
                raise _refuse()
            allowed = self._wg("allowed-ips")
            claims: list[str] = []
            expected_networks = []
            for line in allowed.splitlines():
                fields = line.split()
                if len(fields) < 2:
                    raise _refuse()
                networks = [
                    ipaddress.ip_network(v.strip(","), strict=True)
                    for v in fields[1:]
                    if v != "(none)"
                ]
                if fields[0] == self._peer_key:
                    expected_networks.extend(networks)
                if any(
                    self._destination.version == n.version and self._destination in n
                    for n in networks
                ):
                    claims.append(fields[0])
            if (
                claims != [self._peer_key]
                or not expected_networks
                or any(n.prefixlen != n.max_prefixlen for n in expected_networks)
            ):
                raise _refuse()
            matching = [
                line.split()
                for line in self._wg("latest-handshakes").splitlines()
                if line.split() and line.split()[0] == self._peer_key
            ]
            if len(matching) != 1 or len(matching[0]) != 2:
                raise _refuse()
            stamp = int(matching[0][1])
            if stamp <= 0 or not 0 <= self._clock() - stamp <= 180:
                raise _refuse()
            addresses = json.loads(
                self._command(
                    [
                        "/usr/sbin/ip",
                        "-json",
                        "address",
                        "show",
                        "dev",
                        self._interface,
                    ]
                )
            )
            if not any(
                a.get("local") == str(self._source) and a.get("scope") == "global"
                for link in addresses
                for a in link.get("addr_info", [])
            ):
                raise _refuse()
            routes = json.loads(
                self._command(
                    [
                        "/usr/sbin/ip",
                        "-json",
                        "route",
                        "get",
                        str(self._destination),
                        "from",
                        str(self._source),
                    ]
                )
            )
            if (
                not isinstance(routes, list)
                or len(routes) != 1
                or routes[0].get("dev") != self._interface
                or routes[0].get("from", routes[0].get("prefsrc", routes[0].get("src")))
                != str(self._source)
            ):
                raise _refuse()
        except Exception:
            raise _refuse() from None

    def _connection(self) -> http.client.HTTPConnection:
        self._guard()
        return http.client.HTTPConnection(
            self._host,
            self._port,
            timeout=5.0,
            source_address=(str(self._source), 0),
        )

    def _before_send(self) -> None:
        self._guard()
