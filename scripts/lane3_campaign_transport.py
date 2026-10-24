"""Durable, one-shot B8 SSH transport window; no account or SSH custody work.

Production CLI accepts only cleanup of a fixed session. Opening is a typed API
used by the root coordinator after protected metadata has been staged.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
import pathlib
import pwd
import re
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

NS = 1_000_000_000
CAP_SECONDS = 300
REMOVAL_MARGIN_SECONDS = 45
LOCK_WAIT_SECONDS = 5
TABLE = "dotmac_egress"
CHAIN = "output"
ANCHOR = "dropped-egress"
TAG = "dotmac-b8-ssh"
HELPER = "/usr/local/libexec/dotmac-lane3-campaign/lane3_campaign_transport.py"
METADATA = "/etc/dotmac-lane3-campaign/transport.json"
HOST_CONFIG = "/etc/dotmac-lane3-handoff/host.json"
STATE_ROOT = "/run/dotmac-lane3-campaign/transport"
OWNER_ROOT = "/var/lib/dotmac-lane3-campaign/identities"


class Refused(RuntimeError):
    """Fixed redacted failure reason."""


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


def hex_value(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value) is not None
    )


@dataclass(frozen=True)
class Destination:
    purpose: str
    address: str
    port: int = 22


@dataclass(frozen=True)
class Manifest:
    user: str
    uid: int
    gid: int
    destinations: tuple[Destination, ...]

    @classmethod
    def parse(cls, doc: dict[str, Any]) -> Manifest:
        try:
            if (
                set(doc)
                != {"scope", "runner_user", "runner_uid", "runner_gid", "destinations"}
                or doc["scope"] != "b8-ssh-vm124"
                or not isinstance(doc["runner_user"], str)
                or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,30}", doc["runner_user"])
                or doc["runner_user"] == "root"
                or any(
                    type(doc[x]) is not int or not 0 < doc[x] < 2**32 - 1
                    for x in ("runner_uid", "runner_gid")
                )
                or type(doc["destinations"]) is not list
                or not 3 <= len(doc["destinations"]) <= 6
            ):
                raise ValueError
            rows = []
            seen = set()
            counts = {"target": 0, "jump": 0, "probe": 0}
            for row in doc["destinations"]:
                if (
                    set(row) != {"purpose", "address", "port"}
                    or row["purpose"] not in counts
                    or type(row["port"]) is not int
                    or row["port"] != 22
                    or not isinstance(row["address"], str)
                ):
                    raise ValueError
                address = ipaddress.ip_address(row["address"])
                if (
                    str(address) != row["address"]
                    or address.is_unspecified
                    or address.is_multicast
                    or address.is_loopback
                    or address.is_link_local
                    or "%" in row["address"]
                    or row["address"] in seen
                ):
                    raise ValueError
                seen.add(row["address"])
                counts[row["purpose"]] += 1
                rows.append(Destination(**row))
            if any(not 1 <= count <= 2 for count in counts.values()):
                raise ValueError
            return cls(
                doc["runner_user"], doc["runner_uid"], doc["runner_gid"], tuple(rows)
            )
        except (ValueError, TypeError, KeyError):
            raise Refused("manifest-invalid") from None

    def document(self) -> dict[str, Any]:
        return {
            "scope": "b8-ssh-vm124",
            "runner_user": self.user,
            "runner_uid": self.uid,
            "runner_gid": self.gid,
            "destinations": [
                {"purpose": d.purpose, "address": d.address, "port": d.port}
                for d in self.destinations
            ],
        }

    @property
    def digest(self) -> str:
        return digest(self.document())


@dataclass(frozen=True)
class Binding:
    session_id: str
    manifest_sha256: str
    approval_sha256: str
    boot_id: str
    helper_sha256: str
    host_config_sha256: str

    def __post_init__(self) -> None:
        if (
            not hex_value(self.session_id, 32)
            or any(
                not hex_value(x, 64)
                for x in (
                    self.manifest_sha256,
                    self.approval_sha256,
                    self.helper_sha256,
                    self.host_config_sha256,
                )
            )
            or not isinstance(self.boot_id, str)
            or not re.fullmatch(
                r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", self.boot_id
            )
        ):
            raise Refused("binding-invalid")

    def document(self) -> dict[str, str]:
        return {
            "session_id": self.session_id,
            "manifest_sha256": self.manifest_sha256,
            "approval_sha256": self.approval_sha256,
            "boot_id": self.boot_id,
            "helper_sha256": self.helper_sha256,
            "host_config_sha256": self.host_config_sha256,
        }


@dataclass(frozen=True)
class Receipt:
    session_id: str
    manifest_sha256: str
    baseline_sha256: str
    rule_count: int
    deadline_ns: int
    verdict: str = "OPEN"


class Backend(Protocol):
    def operation(self) -> contextlib.AbstractContextManager[None]: ...
    def guard_operation(self) -> contextlib.AbstractContextManager[None]: ...
    def clock_ns(self) -> int: ...
    def attest(self, binding: Binding) -> None: ...
    def revocation(self, binding: Binding) -> None: ...
    def processes_absent(self, uid: int) -> bool: ...
    def guard(self, uid: int, comment: str) -> None: ...
    def identity(self, user: str) -> tuple[int, int]: ...
    def owner_active(self, binding: Binding, manifest: Manifest) -> None: ...
    def chain(self) -> tuple[dict[str, Any], list[dict[str, Any]]]: ...
    def load(self) -> dict[str, Any] | None: ...
    def save(self, state: dict[str, Any], create: bool = False) -> None: ...
    def arm(self, deadline_ns: int) -> None: ...
    def timer_verified(self, deadline_ns: int) -> bool: ...
    def install(self, specs: list[dict[str, Any]], uid: int, anchor: int) -> None: ...
    def delete(self, handles: list[int]) -> None: ...
    def disarm(self) -> None: ...


def stable(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **{k: v for k, v in r.items() if k not in ("handle", "expr")},
            "expr": [e for e in r.get("expr", []) if "counter" not in e],
        }
        for r in rules
    ]


def expected_rule(spec: dict[str, Any], uid: int, *, handle: int = 1) -> dict[str, Any]:
    proto = "ip" if ipaddress.ip_address(spec["address"]).version == 4 else "ip6"
    pairs = [
        ({"meta": {"key": "skuid"}}, uid),
        ({"payload": {"protocol": proto, "field": "daddr"}}, spec["address"]),
        ({"payload": {"protocol": "tcp", "field": "dport"}}, 22),
        ({"ct": {"key": "state"}}, "new"),
    ]
    return {
        "family": "inet",
        "table": TABLE,
        "chain": CHAIN,
        "handle": handle,
        "comment": spec["comment"],
        "expr": [
            {"match": {"op": "==", "left": left, "right": right}}
            for left, right in pairs
        ]
        + [{"counter": {"packets": 0, "bytes": 0}}, {"accept": None}],
    }


def exact(rule: dict[str, Any], spec: dict[str, Any], uid: int) -> bool:
    expected = expected_rule(spec, uid)
    clean = stable([rule])[0]
    # nft uses equality or membership and, with -n, the kernel NEW state bit
    # NF_CT_STATE_BIT(IP_CT_NEW) = 1 << (2 + 1) = 8. Accept only that exact int.
    expr = clean.get("expr", [])
    if len(expr) == 5 and set(expr[3]) == {"match"}:
        match = expr[3]["match"]
        if (
            set(match) == {"left", "op", "right"}
            and match.get("left") == {"ct": {"key": "state"}}
            and match.get("op") in ("==", "in")
            and (
                match.get("right") in ("new", ["new"], {"set": ["new"]})
                or (type(match.get("right")) is int and match["right"] == 8)
            )
        ):
            expr[3] = {
                "match": {"op": "==", "left": {"ct": {"key": "state"}}, "right": "new"}
            }
    return (
        clean == stable([expected])[0]
        and type(rule.get("handle")) is int
        and rule["handle"] > 0
    )


def expected_guard(comment: str, uid: int, *, handle: int = 1) -> dict[str, Any]:
    return {
        "family": "inet",
        "table": TABLE,
        "chain": CHAIN,
        "handle": handle,
        "comment": comment,
        "expr": [
            {"match": {"op": "==", "left": {"meta": {"key": "skuid"}}, "right": uid}},
            {"counter": {"packets": 0, "bytes": 0}},
            {"drop": None},
        ],
    }


def exact_guard(rule: dict[str, Any], session: str, uid: int) -> bool:
    return (
        stable([rule]) == stable([expected_guard(f"{TAG}:{session}:guard", uid)])
        and type(rule.get("handle")) is int
        and rule["handle"] > 0
    )


class Transport:
    def __init__(self, binding: Binding, manifest: Manifest, backend: Backend):
        # Reparse even typed caller values: dataclass construction is not approval.
        self.manifest = Manifest.parse(manifest.document())
        self.binding = binding
        self.backend = backend
        if manifest.digest != binding.manifest_sha256:
            raise Refused("manifest-binding")

    def _chain(self) -> tuple[list[dict[str, Any]], int]:
        chain, rules = self.backend.chain()
        anchors = [r for r in rules if r.get("comment") == ANCHOR]
        if (
            chain.get("policy") != "drop"
            or chain.get("hook") != "output"
            or len(anchors) != 1
            or type(anchors[0].get("handle")) is not int
            or anchors[0]["handle"] <= 0
        ):
            raise Refused("baseline-unproven")
        self._chain_shape = {k: v for k, v in chain.items() if k != "handle"}
        return rules, anchors[0]["handle"]

    def _owned(self, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            r
            for r in rules
            if str(r.get("comment", "")).startswith(
                TAG + ":" + self.binding.session_id + ":"
            )
        ]

    def _receipt(self, state: dict[str, Any], count: int) -> Receipt:
        return Receipt(
            self.binding.session_id,
            self.binding.manifest_sha256,
            state["baseline_sha256"],
            count,
            state["deadline_ns"],
            "CLOSED"
            if state["status"] == "closed"
            else "GUARDED"
            if state["status"] == "guarded"
            else "OPEN",
        )

    def _identity(self) -> None:
        self.backend.owner_active(self.binding, self.manifest)
        uid, gid = self.backend.identity(self.manifest.user)
        if (
            type(uid) is not int
            or type(gid) is not int
            or uid <= 0
            or gid <= 0
            or (uid, gid) != (self.manifest.uid, self.manifest.gid)
        ):
            raise Refused("identity-unproven")

    def _specs(self) -> list[dict[str, Any]]:
        return [
            {"address": d.address, "comment": f"{TAG}:{self.binding.session_id}:{i}"}
            for i, d in enumerate(self.manifest.destinations)
        ]

    def _state(self) -> dict[str, Any]:
        self.backend.revocation(self.binding)
        state = self.backend.load()
        if (
            not state
            or state.get("binding") != self.binding.document()
            or state.get("manifest") != self.manifest.document()
            or state.get("schema") != "dotmac.b8-ssh-state.v1"
            or state.get("specs") != self._specs()
            or state.get("phase") not in ("not-started", "started", "finished")
            or type(state.get("started_ns")) is not int
            or type(state.get("deadline_ns")) is not int
            or state["deadline_ns"] != state["started_ns"] + CAP_SECONDS * NS
            or state.get("timer_ns")
            != state["deadline_ns"] - REMOVAL_MARGIN_SECONDS * NS
            or state.get("baseline_sha256")
            != digest(
                {"chain": state.get("chain_shape"), "rules": state.get("baseline")}
            )
        ):
            raise Refused("state-unproven")
        return state

    def initialize_closed(self) -> Receipt:
        try:
            with self.backend.operation():
                self.backend.attest(self.binding)
                self._identity()
                if self.backend.load() is not None:
                    raise Refused("already-started")
                rules, _ = self._chain()
                if self._owned(rules) or not self.backend.processes_absent(
                    self.manifest.uid
                ):
                    raise Refused("closure-unproven")
                state = self._new_state(rules, 0)
                state["status"] = "closed"
                state["phase"] = "not-started"
                self.backend.save(state, create=True)
                return self._receipt(state, 0)
        except Refused:
            raise
        except Exception:
            raise Refused("closure-unproven") from None

    def _new_state(self, rules: list[dict[str, Any]], now: int) -> dict[str, Any]:
        return {
            "schema": "dotmac.b8-ssh-state.v1",
            "binding": self.binding.document(),
            "manifest": self.manifest.document(),
            "started_ns": now,
            "deadline_ns": now + CAP_SECONDS * NS,
            "timer_ns": now + (CAP_SECONDS - REMOVAL_MARGIN_SECONDS) * NS,
            "baseline": stable(rules),
            "chain_shape": self._chain_shape,
            "baseline_sha256": digest(
                {"chain": self._chain_shape, "rules": stable(rules)}
            ),
            "specs": self._specs(),
            "handles": [],
            "status": "pending",
            "phase": "started",
        }

    def open(self) -> Receipt:
        self._accept_attempted = False
        try:
            with self.backend.operation():
                try:
                    return self._open_locked()
                except Exception:
                    if self._accept_attempted:
                        self._failed_open_cleanup()
                    raise
        except Refused:
            raise
        except Exception:
            raise Refused("open-unproven") from None

    def _failed_open_cleanup(self) -> bool:
        try:
            self._cleanup_locked(disarm=False)
            return True
        except Exception:
            return False

    def _open_locked(self) -> Receipt:
        try:
            self.backend.attest(self.binding)
            existing = self.backend.load()
            if existing is not None:
                existing = self._state()
                if (
                    existing.get("phase") != "not-started"
                    or existing.get("status") != "closed"
                ):
                    raise Refused("already-started")
            self._identity()
            rules, anchor = self._chain()
            if existing is not None and (
                stable(rules) != existing["baseline"]
                or self._chain_shape != existing["chain_shape"]
            ):
                raise Refused("baseline-unproven")
            if self._owned(rules):
                raise Refused("rule-collision")
            specs = self._specs()
            now = self.backend.clock_ns()
            state = self._new_state(rules, now)
            self.backend.save(state, create=existing is None)
            try:
                self.backend.arm(state["timer_ns"])
            except Exception:  # noqa: S110 - independent readback reconciles loss
                pass
            if not self.backend.timer_verified(state["timer_ns"]):
                raise Refused("timer-unproven")
            state["status"] = "armed"
            self.backend.save(state)
            self.backend.attest(self.binding)
            self._identity()
            if self.backend.clock_ns() >= state["deadline_ns"]:
                raise Refused("deadline-invalid")
            before, anchor = self._chain()
            if (
                stable(before) != state["baseline"]
                or self._chain_shape != state["chain_shape"]
            ):
                raise Refused("baseline-unproven")
            state["status"] = "installing"
            self.backend.save(state)
            self._accept_attempted = True
            try:
                self.backend.install(specs, self.manifest.uid, anchor)
            except Exception:  # noqa: S110 - readback owns the outcome
                pass
            after, _ = self._chain()
            owned = self._owned(after)
            if (
                len(owned) != len(specs)
                or any(
                    sum(exact(r, s, self.manifest.uid) for r in owned) != 1
                    for s in specs
                )
                or stable([r for r in after if r not in owned]) != state["baseline"]
                or self._chain_shape != state["chain_shape"]
                or not self.backend.timer_verified(state["timer_ns"])
                or self.backend.clock_ns() >= state["deadline_ns"]
            ):
                raise Refused("open-unproven")
            state["status"] = "open"
            state["handles"] = [r["handle"] for r in owned]
            self.backend.save(state)
            return self._receipt(state, len(owned))
        except Refused:
            raise
        except Exception:
            raise Refused("open-unproven") from None

    def contain(self) -> Receipt:
        try:
            with self.backend.operation():
                state = self._state()
                if state["status"] == "closed":
                    return self._closed_receipt(state)
                rules = self._ensure_guard(state)
                owned = self._owned(rules)
                accepts = [
                    r
                    for r in owned
                    if not exact_guard(r, self.binding.session_id, self.manifest.uid)
                ]
                if any(
                    sum(exact(r, s, self.manifest.uid) for s in state["specs"]) != 1
                    for r in accepts
                ):
                    raise Refused("ownership-unproven")
                if accepts:
                    self.backend.delete([r["handle"] for r in accepts])
                _, after = self.backend.chain()
                guards = self._owned(after)
                if len(guards) != 1 or not exact_guard(
                    guards[0], self.binding.session_id, self.manifest.uid
                ):
                    raise Refused("cleanup-unproven")
                state["status"] = "guarded"
                state["handles"] = [guards[0]["handle"]]
                self.backend.save(state)
                return self._receipt(state, 1)
        except Refused:
            raise
        except Exception:
            raise Refused("cleanup-unproven") from None

    def _closed_receipt(self, state: dict[str, Any]) -> Receipt:
        chain, rules = self.backend.chain()
        if (
            self._owned(rules)
            or stable(rules) != state["baseline"]
            or {k: v for k, v in chain.items() if k != "handle"} != state["chain_shape"]
            or not self.backend.processes_absent(self.manifest.uid)
        ):
            raise Refused("closure-unproven")
        return self._receipt(state, 0)

    def cleanup(self) -> Receipt:
        try:
            with self.backend.operation():
                return self._cleanup_locked()
        except Refused as error:
            if str(error) == "lock-busy":
                state = self._state()
                if state["status"] == "closed":
                    raise Refused("cleanup-unproven") from None
                self._ensure_guard(state)
                raise Refused("guarded-unproven") from None
            raise
        except Exception:
            raise Refused("cleanup-unproven") from None

    def _ensure_guard(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        with self.backend.guard_operation():
            return self._ensure_guard_locked(state)

    def _ensure_guard_locked(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        # Re-read after the guard lock: closed state can permit UID release and
        # must never gain a late guard from a stale emergency-cleanup snapshot.
        state = self._state()
        if state["status"] == "closed":
            raise Refused("cleanup-unproven")
        _, rules = self.backend.chain()
        guards = [
            r
            for r in self._owned(rules)
            if r.get("comment") == f"{TAG}:{self.binding.session_id}:guard"
        ]
        if not guards:
            try:
                self.backend.guard(
                    self.manifest.uid, f"{TAG}:{self.binding.session_id}:guard"
                )
            except Exception:  # noqa: S110 - exact readback handles a lost guard response
                pass
            _, rules = self.backend.chain()
            guards = [
                r
                for r in self._owned(rules)
                if r.get("comment") == f"{TAG}:{self.binding.session_id}:guard"
            ]
        if (
            len(guards) != 1
            or not exact_guard(guards[0], self.binding.session_id, self.manifest.uid)
            or not rules
            or rules[0].get("handle") != guards[0]["handle"]
        ):
            raise Refused("ownership-unproven")
        return rules

    def _cleanup_locked(self, *, disarm: bool = True) -> Receipt:
        try:
            state = self._state()
            if state["status"] == "closed":
                return self._closed_receipt(state)
            rules = self._ensure_guard(state)
            owned = self._owned(rules)
            accepts = [
                r
                for r in owned
                if not exact_guard(r, self.binding.session_id, self.manifest.uid)
            ]
            if any(
                sum(exact(r, s, self.manifest.uid) for s in state["specs"]) != 1
                for r in accepts
            ):
                raise Refused("ownership-unproven")
            if accepts:
                self.backend.delete([r["handle"] for r in accepts])
            chain, after = self.backend.chain()
            guards = self._owned(after)
            if len(guards) != 1 or not exact_guard(
                guards[0], self.binding.session_id, self.manifest.uid
            ):
                raise Refused("cleanup-unproven")
            state["status"] = "guarded"
            state["handles"] = [guards[0]["handle"]]
            self.backend.save(state)
            if (
                not self.backend.processes_absent(self.manifest.uid)
                or stable([r for r in after if r not in guards]) != state["baseline"]
                or {k: v for k, v in chain.items() if k != "handle"}
                != state["chain_shape"]
            ):
                raise Refused("guarded-unproven")
            with self.backend.guard_operation():
                self.backend.delete([guards[0]["handle"]])
                chain, after = self.backend.chain()
                if (
                    self._owned(after)
                    or stable(after) != state["baseline"]
                    or {k: v for k, v in chain.items() if k != "handle"}
                    != state["chain_shape"]
                ):
                    raise Refused("cleanup-unproven")
                state["status"] = "closed"
                state["phase"] = "finished"
                state["handles"] = []
                self.backend.save(state)
                if disarm:
                    self.backend.disarm()
            return self._receipt(state, 0)
        except Refused:
            raise
        except Exception:
            raise Refused("cleanup-unproven") from None


class SystemBackend:
    """Root-only fixed filesystem, nft chain and exact transient timer owner."""

    def __init__(self, binding: Binding):
        self.binding = binding
        self.table = TABLE
        self.chain_name = CHAIN
        self.directory = pathlib.Path(STATE_ROOT) / binding.session_id
        self.helper = pathlib.Path(HELPER)
        self.metadata = pathlib.Path(METADATA)

    def operation(self) -> contextlib.AbstractContextManager[None]:
        return self._lock(".lock")

    def guard_operation(self) -> contextlib.AbstractContextManager[None]:
        return self._lock(".guard.lock")

    @contextlib.contextmanager
    def _lock(self, name: str) -> Iterator[None]:
        if os.geteuid() != 0:
            raise Refused("root-required")
        parent = pathlib.Path(STATE_ROOT)
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._private_directory(parent)
        fd = os.open(
            parent / name,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise Refused("lock-unproven")
            until = self.clock_ns() + LOCK_WAIT_SECONDS * NS
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if self.clock_ns() >= until:
                        raise Refused("lock-busy") from None
                    time.sleep(0.02)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    @property
    def unit(self) -> str:
        return "dotmac-b8-ssh-expire-" + self.binding.session_id

    def clock_ns(self) -> int:
        return time.clock_gettime_ns(time.CLOCK_MONOTONIC)

    @staticmethod
    def read(path: pathlib.Path, mode: int | None = None) -> bytes:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or info.st_mode & 0o022
                or info.st_size > 262144
                or (mode is not None and stat.S_IMODE(info.st_mode) != mode)
            ):
                raise Refused("file-unproven")
            return os.read(fd, 262145)
        finally:
            os.close(fd)

    @staticmethod
    def run(argv: list[str], data: bytes | None = None) -> str:
        try:
            result = subprocess.run(
                argv, input=data, capture_output=True, timeout=2, check=False
            )
            if result.returncode:
                raise Refused("command-unproven")
            return result.stdout.decode().strip()
        except Exception:
            raise Refused("command-unproven") from None

    @staticmethod
    def validate_host_config(raw: bytes, binding: Binding) -> None:
        try:
            doc = json.loads(raw)
            if (
                hashlib.sha256(raw).hexdigest() != binding.host_config_sha256
                or doc.get("schema") != "dotmac.lane3.handoff-host.v1"
                or doc.get("firewall")
                != {
                    "family": "inet",
                    "table": TABLE,
                    "chain": CHAIN,
                    "anchor_comment": ANCHOR,
                }
            ):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise Refused("host-config-unproven") from None

    def attest(self, binding: Binding) -> None:
        self.validate_host_config(self.read(pathlib.Path(HOST_CONFIG), 0o600), binding)
        if (
            os.geteuid() != 0
            or pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            != binding.boot_id
            or hashlib.sha256(self.read(self.helper, 0o444)).hexdigest()
            != binding.helper_sha256
            or json.loads(self.read(self.metadata, 0o600))
            != {"schema": "dotmac.b8-ssh-approval.v1", **binding.document()}
        ):
            raise Refused("binding-unproven")

    def revocation(self, binding: Binding) -> None:
        # Original root-owned journal + manifest supply revocation authority.
        # Mutable opening approvals/config/helper bytes cannot veto containment.
        if (
            os.geteuid() != 0
            or pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            != binding.boot_id
        ):
            raise Refused("revocation-unproven")

    def processes_absent(self, uid: int) -> bool:
        try:
            for process in pathlib.Path("/proc").iterdir():
                if process.name.isdecimal():
                    try:
                        status = (process / "status").read_text()
                    except FileNotFoundError:
                        continue
                    for line in status.splitlines():
                        if line.startswith("Uid:") and uid in [
                            int(x) for x in line.split()[1:]
                        ]:
                            return False
            return True
        except (OSError, ValueError):
            return False

    def guard(self, uid: int, comment: str) -> None:
        if (
            type(uid) is not int
            or uid <= 0
            or comment != f"{TAG}:{self.binding.session_id}:guard"
        ):
            raise Refused("guard-unowned")
        self.run(
            ["/usr/sbin/nft", "-f", "-"],
            (
                f"insert rule inet {self.table} {self.chain_name} meta skuid {uid} "
                f'counter drop comment "{comment}"\n'
            ).encode(),
        )

    def owner_active(self, binding: Binding, manifest: Manifest) -> None:
        directory = pathlib.Path(OWNER_ROOT) / binding.session_id
        self._private_directory(directory.parent)
        self._private_directory(directory)
        owner = json.loads(self.read(directory / "owner.json", 0o600))
        expected = {
            "session_id": binding.session_id,
            "user": manifest.user,
            "uid": manifest.uid,
            "gid": manifest.gid,
            "group_attempted": True,
            "group_created": True,
            "user_attempted": True,
            "user_created": True,
        }
        if (
            owner != expected
            or manifest.user != "l3b8-" + binding.session_id[:12]
            or type(owner.get("uid")) is not int
            or type(owner.get("gid")) is not int
            or any(
                type(owner.get(k)) is not bool
                for k in (
                    "group_attempted",
                    "group_created",
                    "user_attempted",
                    "user_created",
                )
            )
        ):
            raise Refused("owner-unproven")

    def identity(self, user: str) -> tuple[int, int]:
        identity = pwd.getpwnam(user)
        return identity.pw_uid, identity.pw_gid

    def chain(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        doc = json.loads(
            self.run(
                [
                    "/usr/sbin/nft",
                    "-n",
                    "-j",
                    "list",
                    "chain",
                    "inet",
                    self.table,
                    self.chain_name,
                ]
            )
        )["nftables"]
        chains = [x["chain"] for x in doc if "chain" in x]
        if len(chains) != 1:
            raise Refused("baseline-unproven")
        rules = [x["rule"] for x in doc if "rule" in x]
        for r in rules:
            r["table"] = TABLE
            r["chain"] = CHAIN
        return chains[0], rules

    def _private_directory(self, path: pathlib.Path) -> None:
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise Refused("directory-unproven")

    def load(self) -> dict[str, Any] | None:
        try:
            self._private_directory(self.directory.parent)
            self._private_directory(self.directory)
            state = json.loads(self.read(self.directory / "state.json", 0o600))
            manifest = self.read(self.directory / "manifest.json", 0o600)
            if hashlib.sha256(
                manifest
            ).hexdigest() != self.binding.manifest_sha256 or json.loads(
                manifest
            ) != state.get("manifest"):
                raise Refused("manifest-binding")
            return state
        except FileNotFoundError:
            return None

    def save(self, state: dict[str, Any], create: bool = False) -> None:
        if create:
            self.directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.directory.mkdir(mode=0o700)
        self._private_directory(self.directory.parent)
        self._private_directory(self.directory)
        for name, value in (
            ("manifest.json", state["manifest"]),
            ("state.json", state),
        ):
            temp = self.directory / (name + ".tmp")
            fd = os.open(
                temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            try:
                data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
                written = os.write(fd, data)
                if written != len(data):
                    raise Refused("state-unproven")
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temp, self.directory / name)
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def arm(self, deadline_ns: int) -> None:
        if not 0 < deadline_ns - self.clock_ns() <= CAP_SECONDS * NS:
            raise Refused("deadline-invalid")
        self.run(
            [
                "/usr/bin/systemd-run",
                "--unit=" + self.unit,
                "--description=" + TAG + ":" + self.binding.session_id,
                "--collect",
                "--service-type=exec",
                "--property=RuntimeMaxSec=30s",
                "--property=TimeoutStartSec=30s",
                "--property=TimeoutStopSec=5s",
                "--property=KillMode=control-group",
                "--property=Restart=on-failure",
                "--property=RestartSec=1s",
                "--property=StartLimitBurst=30",
                "--property=StartLimitIntervalSec=infinity",
                "--property=RestartPreventExitStatus=2",
                "--on-boot=" + str(deadline_ns // 1000) + "us",
                "--timer-property=AccuracySec=1us",
                "/usr/bin/python3",
                "-I",
                "-B",
                str(self.helper),
                "--cleanup",
                self.binding.session_id,
            ]
        )

    def timer_verified(self, deadline_ns: int) -> bool:
        timer = dict(
            line.split("=", 1)
            for line in self.run(
                ["/usr/bin/systemctl", "show", self.unit + ".timer"]
            ).splitlines()
            if "=" in line
        )
        service = dict(
            line.split("=", 1)
            for line in self.run(
                ["/usr/bin/systemctl", "show", self.unit + ".service"]
            ).splitlines()
            if "=" in line
        )
        path = self.run(
            [
                "/usr/bin/busctl",
                "call",
                "org.freedesktop.systemd1",
                "/org/freedesktop/systemd1",
                "org.freedesktop.systemd1.Manager",
                "GetUnit",
                "s",
                self.unit + ".timer",
            ]
        )
        match = re.fullmatch(
            r'o "(/org/freedesktop/systemd1/unit/[A-Za-z0-9_]+)"', path
        )
        if not match:
            return False
        raw = self.run(
            [
                "/usr/bin/busctl",
                "get-property",
                "org.freedesktop.systemd1",
                match[1],
                "org.freedesktop.systemd1.Timer",
                "NextElapseUSecMonotonic",
            ]
        )
        nxt = re.fullmatch(r"t ([0-9]+)", raw)
        execution = service.get("ExecStart", "")
        argv = (
            "/usr/bin/python3 -I -B "
            + str(self.helper)
            + " --cleanup "
            + self.binding.session_id
        )
        expected = re.fullmatch(
            r"\{ path=/usr/bin/python3 ; argv\[\]="
            + re.escape(argv)
            + r" ; ignore_errors=no ; start_time=[^;]* ; stop_time=[^;]* ; "
            r"pid=0 ; code=[^;]* ; status=[^;]* \}",
            execution,
        )
        return bool(
            nxt
            and 0 <= deadline_ns - int(nxt[1]) * 1000 < 1000
            and timer.get("ActiveState") == "active"
            and timer.get("SubState") == "waiting"
            and timer.get("Unit") == self.unit + ".service"
            and timer.get("Transient") == "yes"
            and service.get("Transient") == "yes"
            and service.get("Description") == TAG + ":" + self.binding.session_id
            and expected
            and service.get("Type") == "exec"
            and service.get("RuntimeMaxUSec") == "30s"
            and service.get("TimeoutStartUSec") == "30s"
            and service.get("TimeoutStopUSec") == "5s"
            and service.get("KillMode") == "control-group"
            and service.get("Restart") == "on-failure"
            and service.get("RestartUSec") == "1s"
            and service.get("StartLimitBurst") == "30"
            and service.get("StartLimitIntervalUSec") == "infinity"
            and service.get("RestartPreventExitStatus") == "2"
        )

    def install(self, specs: list[dict[str, Any]], uid: int, anchor: int) -> None:
        lines = []
        for s in specs:
            proto = "ip" if ipaddress.ip_address(s["address"]).version == 4 else "ip6"
            lines.append(
                f'insert rule inet {self.table} {self.chain_name} position {anchor} '
                f'meta skuid {uid} {proto} daddr {s["address"]} tcp dport 22 '
                f'ct state new counter accept comment "{s["comment"]}"'
            )
        self.run(["/usr/sbin/nft", "-f", "-"], ("\n".join(lines) + "\n").encode())

    def delete(self, handles: list[int]) -> None:
        if any(type(h) is not int or h <= 0 for h in handles):
            raise Refused("handle-unproven")
        self.run(
            ["/usr/sbin/nft", "-f", "-"],
            (
                "\n".join(
                    f"delete rule inet {self.table} {self.chain_name} handle {h}"
                    for h in handles
                )
                + "\n"
            ).encode(),
        )

    def disarm(self) -> None:
        self.run(["/usr/bin/systemctl", "stop", self.unit + ".timer"])
        state = self.run(
            [
                "/usr/bin/systemctl",
                "show",
                self.unit + ".timer",
                "--property=ActiveState",
                "--value",
            ]
        )
        if state != "inactive":
            raise Refused("timer-stop-unproven")


def main(argv: list[str]) -> int:
    try:
        if len(argv) != 2 or argv[0] != "--cleanup" or not hex_value(argv[1], 32):
            raise Refused("usage-refused")
        raw = SystemBackend.read(
            pathlib.Path(STATE_ROOT) / argv[1] / "state.json", 0o600
        )
        state = json.loads(raw)
        binding = Binding(**state["binding"])
        if binding.session_id != argv[1]:
            raise Refused("state-unproven")
        transport = Transport(
            binding, Manifest.parse(state["manifest"]), SystemBackend(binding)
        )
        receipt = transport.cleanup()
        print(
            json.dumps(
                {
                    "verdict": "CLOSED",
                    "rule_count": receipt.rule_count,
                    "baseline_sha256": receipt.baseline_sha256,
                },
                sort_keys=True,
            )
        )
        return 0
    except Refused as error:
        exhausted = (
            "state" in locals()
            and type(state.get("timer_ns")) is int
            and time.clock_gettime_ns(time.CLOCK_MONOTONIC)
            >= state["timer_ns"] + 90 * NS
        )
        verdict = (
            "GUARDED_UNPROVEN"
            if str(error) == "guarded-unproven"
            else "CLEANUP_UNPROVEN"
        )
        print(json.dumps({"verdict": verdict}, sort_keys=True))
        return 2 if exhausted else 1
    except Exception:
        print('{"verdict":"CLEANUP_UNPROVEN"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
