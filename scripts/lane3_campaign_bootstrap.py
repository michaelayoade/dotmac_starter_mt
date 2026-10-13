"""Capture the measured external bootstrap proposal through the canonical DNS owner.

The proposal's historical status is not authorization. Its independently held
SHA-256 selects exact reviewed bytes; the campaign owner still freezes the
resulting manifest before activation. No network inventory is embedded here.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import stat
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from threading import Event
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lane3_handoff_controller as hc
import lane3_handoff_protocol as hp
import lane3_handoff_resolver as hr

CONFIG_SCHEMA = "dotmac.bootstrap-completion-proposal.v2"
MAX_CONFIG_BYTES = 262144
MAX_ORIGINS = 64
MAX_WORKERS = 8


class BootstrapRefused(RuntimeError):
    """A fixed refusal label without network inventory or exception text."""


@dataclass(frozen=True, repr=False)
class BootstrapConfig:
    resolvers: tuple[str, str]
    origins: tuple[tuple[str, hr.AliasPolicy], ...]
    proposal_sha256: str

    def __repr__(self) -> str:
        return "BootstrapConfig(<private>)"


def parse_config(value: Any) -> BootstrapConfig:
    """Parse finite per-original alias policy; no caller approval flag exists."""
    try:
        return _parse_config(value)
    except Exception:
        raise BootstrapRefused("bootstrap.config") from None


def _parse_config(value: Any) -> BootstrapConfig:
    keys = {
        "schema",
        "status",
        "inventory_sha256",
        "observation_sha256",
        "origins",
        "resolvers",
        "max_union_addresses",
        "minimum_ttl_seconds",
        "maximum_observation_age_seconds",
        "profile",
        "transport",
        "module_https",
        "new_origin_or_alias",
        "shared_ip_representative",
        "resolver_comparison",
        "shared_ip_limitation",
    }
    if type(value) is not dict or set(value) != keys:
        raise ValueError
    expected = {
        "schema": CONFIG_SCHEMA,
        "status": "PROPOSED_NOT_ADMITTED",
        "max_union_addresses": hp.MAX_ADDRESSES,
        "minimum_ttl_seconds": 1,
        "maximum_observation_age_seconds": hp.BOOTSTRAP_OBSERVATION_MAX_AGE_NS
        // 1_000_000_000,
        "profile": hp.BOOTSTRAP_OBSERVATION_PROFILE,
        "transport": "TCP",
        "module_https": [],
        "new_origin_or_alias": "STOP",
        "shared_ip_representative": (
            "lexicographically-smallest-resolving-approved-origin"
        ),
        "resolver_comparison": "equal-normalized-chain-address-union",
    }
    if any(
        type(value[key]) is not type(item) or value[key] != item
        for key, item in expected.items()
    ):
        raise ValueError
    for key in ("inventory_sha256", "observation_sha256"):
        hp.hex64(value[key])
    if (
        type(value["shared_ip_limitation"]) is not str
        or not value["shared_ip_limitation"]
    ):
        raise ValueError
    resolvers = value["resolvers"]
    if type(resolvers) is not list or len(resolvers) != 2:
        raise ValueError
    for resolver in resolvers:
        if (
            type(resolver) is not str
            or str(ipaddress.IPv4Address(resolver)) != resolver
            or not hr.globally_routable(ipaddress.IPv4Address(resolver))
        ):
            raise ValueError
    if len(set(resolvers)) != 2:
        raise ValueError
    rows = value["origins"]
    if type(rows) is not list or not 1 <= len(rows) <= MAX_ORIGINS:
        raise ValueError
    origins = {}
    for row in rows:
        if type(row) is not dict or set(row) != {"origin", "purpose", "aliases"}:
            raise ValueError
        origin = row["origin"]
        hp.origin(origin)
        if row["purpose"] != "runner-https" or origin in origins:
            raise ValueError
        aliases = hr.AliasPolicy.from_mapping(row["aliases"])
        if aliases.namespaces:
            raise ValueError
        origins[origin] = aliases
    return BootstrapConfig(
        tuple(resolvers), tuple(sorted(origins.items())), hp.digest(value)
    )


def load_config(path: Path, expected_sha256: str) -> BootstrapConfig:
    return replace(
        parse_config(hc.strict_json(_read_measured(path, expected_sha256))),
        proposal_sha256=expected_sha256,
    )


def load_manifest(path: Path, expected_sha256: str) -> dict[str, Any]:
    try:
        raw = _read_measured(path, expected_sha256)
        manifest = hc.validate_bootstrap(hc.strict_json(raw), hr.globally_routable)
        if hp.canonical_json(manifest) != raw:
            raise ValueError
        return manifest
    except Exception:
        raise BootstrapRefused("bootstrap.manifest") from None


def _read_measured(path: Path, expected_sha256: str) -> bytes:
    """Measure one private regular file without following the final symlink."""
    opened = None
    try:
        hp.hex64(expected_sha256)
        path = Path(path)
        if not path.is_absolute():
            raise ValueError
        opened = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(opened)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or not 0 < before.st_size <= MAX_CONFIG_BYTES
        ):
            raise ValueError
        with os.fdopen(opened, "rb", closefd=False) as stream:
            raw = stream.read(MAX_CONFIG_BYTES + 1)
        after = os.fstat(opened)
        if (
            len(raw) != before.st_size
            or (before.st_mtime_ns, before.st_ctime_ns, before.st_size)
            != (after.st_mtime_ns, after.st_ctime_ns, after.st_size)
            or hashlib.sha256(raw).hexdigest() != expected_sha256
        ):
            raise ValueError
        return raw
    except Exception:
        raise BootstrapRefused("bootstrap.config") from None
    finally:
        if opened is not None:
            os.close(opened)


@dataclass(frozen=True, repr=False)
class FrozenBootstrap:
    """Held immutable observations under the separately authorized capture age."""

    observations: tuple[hr.BootstrapObservation, ...]
    manifest_bytes: bytes
    digest: str
    proposal_sha256: str

    def __repr__(self) -> str:
        return "FrozenBootstrap(<private>)"

    @property
    def provenance(self) -> dict[str, Any]:
        return {
            "schema": "dotmac.bootstrap-provenance.v2",
            "profile": hp.BOOTSTRAP_OBSERVATION_PROFILE,
            "observation_deadline_monotonic_ns": self.observation_deadline_monotonic_ns,
            "proposal_sha256": self.proposal_sha256,
            "observations": [asdict(observation) for observation in self.observations],
        }

    @property
    def provenance_digest(self) -> str:
        return hp.digest(self.provenance)

    @property
    def manifest(self) -> dict[str, Any]:
        return hc.strict_json(self.manifest_bytes)

    @property
    def observation_deadline_monotonic_ns(self) -> int:
        return (
            min(
                observation.captured_at_monotonic_ns
                for observation in self.observations
            )
            + hp.BOOTSTRAP_OBSERVATION_MAX_AGE_NS
        )

    def remaining_ns(self, now_ns: int) -> int:
        return self.observation_deadline_monotonic_ns - now_ns

    def require_same(self, manifest: Any) -> None:
        if hp.canonical_json(manifest) != self.manifest_bytes:
            raise BootstrapRefused("bootstrap.manifest_changed")


def capture(
    config: BootstrapConfig,
    *,
    deadline_ns: int,
    clock_ns: Callable[[], int] = time.monotonic_ns,
    resolve: Callable[..., hr.BootstrapObservation] = hr.observe_bootstrap,
    routable: Callable[[Any], bool] = hr.globally_routable,
) -> FrozenBootstrap:
    """Resolve all approved origins twice within the inherited absolute budget.

    A bounded thread population overlaps DNS only. No retries, host effects or
    per-origin deadline extension occur. On refusal queued work is cancelled;
    in-flight TCP queries retain the resolver's bounded socket timeout.
    """

    stopped = Event()

    def remaining() -> float:
        left = deadline_ns - clock_ns()
        if stopped.is_set() or left <= 0:
            raise BootstrapRefused("bootstrap.deadline")
        return left / 1_000_000_000

    def one(
        origin: str, aliases: hr.AliasPolicy, resolver: str
    ) -> hr.BootstrapObservation:
        remaining()

        def exchange(query: bytes) -> bytes:
            ask = hr.tcp_exchange(
                resolver,
                deadline_s=min(hr.QUERY_DEADLINE_S, remaining()),
                deadline_ns=deadline_ns,
            )
            result = ask(query)
            remaining()
            return result

        snapshot = resolve(
            origin,
            resolver=resolver,
            aliases=aliases,
            max_addresses=hp.MAX_ADDRESSES,
            exchange=exchange,
            clock_ns=clock_ns,
            routable=routable,
        )
        remaining()
        if (
            not isinstance(snapshot, hr.BootstrapObservation)
            or snapshot.origin != origin
            or snapshot.resolver != resolver
            or type(snapshot.captured_at_monotonic_ns) is not int
            or not 0
            <= snapshot.captured_at_monotonic_ns
            <= snapshot.acquired_at_monotonic_ns
            <= clock_ns()
            or tuple(query.qtype for query in snapshot.queries)
            != (hr.TYPE_A, hr.TYPE_AAAA)
            or type(snapshot.ttl_s) is not int
            or snapshot.ttl_s <= 0
        ):
            raise BootstrapRefused("bootstrap.observation")
        for query in snapshot.queries:
            if not (
                isinstance(query, hr.QueryObservation)
                and snapshot.captured_at_monotonic_ns
                <= query.started_at_monotonic_ns
                <= query.received_at_monotonic_ns
                <= query.validated_at_monotonic_ns
                <= snapshot.acquired_at_monotonic_ns
            ):
                raise BootstrapRefused("bootstrap.observation")
        return snapshot

    remaining()
    pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    futures = []
    try:
        futures = [
            pool.submit(one, origin, aliases, resolver)
            for origin, aliases in config.origins
            for resolver in config.resolvers
        ]
        snapshots = [
            future.result() for future in as_completed(futures, timeout=remaining())
        ]
        remaining()
        snapshots.sort(key=lambda s: (s.origin, s.resolver))
        by_origin = {}
        for snapshot in snapshots:
            by_origin.setdefault(snapshot.origin, []).append(snapshot)
        representatives: dict[tuple[int, str], str] = {}
        for origin, _ in config.origins:
            left, right = by_origin[origin]
            if left.chain != right.chain:
                raise BootstrapRefused("bootstrap.dns_agreement")
            for address in set(left.addresses) | set(right.addresses):
                representatives[address] = min(
                    origin, representatives.get(address, origin)
                )
        manifest = hc.validate_bootstrap(
            {
                "schema": hc.BOOTSTRAP_SCHEMA,
                "destinations": [
                    {
                        "purpose": "runner-https",
                        "origin": origin,
                        "family": family,
                        "address": address,
                        "port": hp.BROKER_PORT,
                    }
                    for (family, address), origin in sorted(representatives.items())
                ],
            },
            routable,
        )
        raw = hp.canonical_json(manifest)
        frozen = FrozenBootstrap(
            tuple(snapshots), raw, hp.digest(manifest), config.proposal_sha256
        )
        if frozen.remaining_ns(clock_ns()) <= 0:
            raise BootstrapRefused("bootstrap.expired")
        remaining()
        return frozen
    except BootstrapRefused:
        raise
    except TimeoutError:
        raise BootstrapRefused("bootstrap.deadline") from None
    except Exception:
        raise BootstrapRefused("bootstrap.dns") from None
    finally:
        stopped.set()
        for future in futures:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
