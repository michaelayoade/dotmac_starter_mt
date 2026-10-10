"""Root controller for the Lane 3 same-job broker handoff (host side).

The coordinator is the sole admission and mutation owner; this controller is
the pinned root enforcer of its decisions on one host. It owns, for exactly
one lease at a time:

* a private journal (``/var/lib/dotmac-lane3-handoff``: directories ``0700``,
  files ``0600``) bound to the host boot id, written BEFORE every host
  mutation so that rollback has enough information at every crash point;
* a temporary runner identity (unique system UID and group), its tmpfs
  workspace and its systemd unit / cgroup;
* the per-lease directory ``/run/dotmac-lane3-handoff/<lease_id>/`` (root,
  task group, ``0750``) holding ``control.sock`` (``0660``) and the atomic
  ``challenge.json`` / ``grant.json`` (``0640``). The runner cannot create,
  unlink or replace anything there;
* every nftables rule tagged ``dotmac-lane3-handoff:<lease_id>:...``: exact
  UID + exact address + TCP 443 NEW accepts placed before the baseline's
  anchor, and a UID deny guard placed at the head of the chain during cleanup.
  Rule changes are one ``nft -f`` transaction each, followed by an exact
  readback of the owned rules and an unchanged non-owned baseline;
* one independent rollback timer, armed before the first rule change, that
  runs the originally pinned controller bytes at ``T0 + 300 s``.

There is one deadline. Metadata receipt, DNS, grant issuance, supplier start,
restarts and human conversation never extend it; monotonic time plus the boot
id are used and a journal from another boot can only be cleaned up.

Cleanup revokes the grant, installs the UID guard ahead of any baseline
ESTABLISHED accept, kills the exact runner cgroup, proves that the UID has no
process and no socket, removes ONLY rules that exactly match what this lease
recorded, checks the baseline and default DROP, then removes the lease
directory, workspace and account and finally stops the timer. Global
conntrack is never flushed. On any drift the guard and the UID are kept and
the refusal asks for existing management recovery; unrelated rules are never
erased to manufacture a clean baseline.

Every refusal is a fixed label. Exception text, paths, addresses, tokens, the
JIT configuration and bodies are never printed.

Python standard library only; root only; Linux with cgroup v2 and nftables.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import fcntl
import hashlib
import hmac
import io
import ipaddress
import json
import os
import pwd
import re
import secrets
import signal
import socket
import stat
import struct
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lane3_handoff_protocol as hp
import lane3_handoff_resolver as hr
from lane3_broker_origin import OriginPolicy

TAG: Final = "dotmac-lane3-handoff"
HOST_SCHEMA: Final = "dotmac.lane3.handoff-host.v1"
POLICY_SCHEMA: Final = "dotmac.lane3.handoff-policy.v1"
BOOTSTRAP_SCHEMA: Final = "dotmac.lane3.handoff-bootstrap.v1"
EFFECTIVE_SCHEMA: Final = "dotmac.lane3.handoff-effective.v1"
APPROVAL_SCHEMA: Final = "dotmac.lane3.handoff-approval.v1"
JOURNAL_SCHEMA: Final = "dotmac.lane3.handoff-journal.v1"
EVIDENCE_SCHEMA: Final = "dotmac.lane3.handoff-evidence.v1"

#: The bytes the rollback timer and the socket server run, copied at prepare.
PINNED_MODULES: Final = (
    "lane3_broker_origin.py",
    "lane3_handoff_controller.py",
    "lane3_handoff_protocol.py",
    "lane3_handoff_resolver.py",
)
USER_PREFIX: Final = "l3h-"
TRANSIT_ALLOWANCE_NS: Final = 2 * 1_000_000_000
APPROVAL_MAX: Final = datetime.timedelta(minutes=30)
LOCK_WAIT_S: Final = 4.0
CLEANUP_WAIT_S: Final = 5.0
MAX_CONFIG: Final = 65536
MAX_MGMT_INPUT: Final = 1_048_576
MAX_JIT: Final = 1_000_000

TERMINAL: Final = frozenset({"REFUSED", "EXPIRED", "CLOSED"})
_NAME: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")
_JIT: Final = re.compile(r"[A-Za-z0-9+/=_-]+")
_ABS: Final = re.compile(r"/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+")


class ControllerRefused(RuntimeError):
    """A fixed-label refusal; ``label`` is never input or exception text."""

    def __init__(self, label: str) -> None:
        super().__init__(label)
        self.label = label

    def __str__(self) -> str:
        return self.label


def refused(label: str) -> ControllerRefused:
    return ControllerRefused(label)


def category_of(exc: BaseException) -> str:
    label = getattr(exc, "label", "internal")
    return label if label in hp.CATEGORIES else "internal"


# ── strict JSON for root-owned configuration and the journal ───────────────


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise refused("config.invalid")
        out[key] = value
    return out


def _constant(_: str) -> Any:
    raise refused("config.invalid")


def strict_json(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except ControllerRefused:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise refused("config.invalid") from None
    if not isinstance(value, dict):
        raise refused("config.invalid")
    return value


# ── descriptor-walk file access (no symlinks, root-owned parents) ──────────

_DIR_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _check_dir(fd: int, forbid: int, gid: int | None = None) -> None:
    info = os.fstat(fd)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or info.st_mode & forbid
        or (gid is not None and info.st_gid != gid)
    ):
        raise refused("path.unsafe")


def open_dir(path: Path, *, forbid: int = 0o022, gid: int | None = None) -> int:
    """Walk ``path`` from ``/`` without following symlinks.

    Every component is root-owned and not group/other writable; the final one
    must additionally satisfy ``forbid`` (and ``gid`` when given).
    """
    if not path.is_absolute() or any(p in ("", ".", "..") for p in path.parts[1:]):
        raise refused("path.unsafe")
    fd = os.open("/", _DIR_FLAGS)
    try:
        parts = path.parts[1:]
        _check_dir(fd, 0o022 if parts else forbid)
        for index, part in enumerate(parts):
            child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
            last = index == len(parts) - 1
            _check_dir(fd, forbid if last else 0o022, gid if last else None)
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_protected(
    path: Path,
    *,
    forbid: int = 0o022,
    exact_mode: int | None = None,
    gid: int | None = None,
    limit: int = MAX_CONFIG,
) -> bytes:
    """A root-owned regular file under root-owned parents, size-capped."""
    try:
        dfd = open_dir(path.parent)
        try:
            fd = os.open(
                path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=dfd,
            )
        finally:
            os.close(dfd)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or info.st_nlink != 1
                or info.st_mode & forbid
                or (exact_mode is not None and stat.S_IMODE(info.st_mode) != exact_mode)
                or (gid is not None and info.st_gid != gid)
            ):
                raise refused("path.unsafe")
            raw = stream.read(limit + 1)
    except ControllerRefused:
        raise
    except OSError:
        raise refused("path.unsafe") from None
    if len(raw) > limit:
        raise refused("path.unsafe")
    return raw


def write_atomic(dir_fd: int, name: str, data: bytes, *, mode: int, gid: int) -> None:
    """Create a fresh file, fsync it, rename over ``name`` and fsync the dir."""
    temporary = "." + name + "." + secrets.token_hex(8) + ".tmp"
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=dir_fd,
    )
    try:
        try:
            os.fchown(fd, 0, gid)
            os.fchmod(fd, mode)
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary, dir_fd=dir_fd)
        raise
    os.fsync(dir_fd)


# ── /proc and cgroup facts about the peer and the runner UID ───────────────


def proc_start_time(pid: int) -> int:
    """Field 22 of ``/proc/<pid>/stat`` (start time in clock ticks)."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_bytes()
    except OSError:
        raise refused("peer.refused") from None
    tail = raw[raw.rfind(b")") + 2 :].split()
    if len(tail) < 20:
        raise refused("peer.refused")
    return int(tail[19])


def proc_cgroup(pid: int) -> str:
    """The single cgroup v2 path of ``pid``; hybrid/v1 layouts refuse."""
    try:
        text = Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        raise refused("peer.refused") from None
    lines = text.splitlines()
    if len(lines) != 1 or not lines[0].startswith("0::/"):
        raise refused("peer.refused")
    return lines[0][3:]


def uid_processes(uid: int) -> list[int]:
    """Every live PID with ``uid`` as real, effective, saved or fs UID."""
    found: list[int] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            lines = Path(f"/proc/{entry}/status").read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            if line.startswith("State:") and "Z" in line.split()[1:2]:
                break
            if line.startswith("Uid:"):
                if str(uid) in line.split()[1:]:
                    found.append(int(entry))
                break
    return found


def uid_sockets(uid: int) -> int:
    """Count TCP/UDP sockets (both families) owned by ``uid`` in this netns."""
    count = 0
    for name in ("tcp", "tcp6", "udp", "udp6"):
        try:
            lines = Path(f"/proc/net/{name}").read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) > 7 and fields[7] == str(uid):
                count += 1
    return count


# ── configuration and manifests ────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class Firewall:
    family: str
    table: str
    chain: str
    anchor: str


@dataclasses.dataclass(frozen=True)
class HostConfig:
    firewall: Firewall
    policy_digest: str
    controller_digest: str
    archive: Path
    archive_sha256: str
    workspace_mb: int
    memory_mb: int


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise refused(label)
    return value


def parse_host_config(value: Any) -> HostConfig:
    doc = _exact(
        value,
        {"schema", "firewall", "policy_digest", "controller_digest", "runner"},
        "config.invalid",
    )
    if doc["schema"] != HOST_SCHEMA:
        raise refused("config.invalid")
    fw = _exact(
        doc["firewall"],
        {"family", "table", "chain", "anchor_comment"},
        "config.invalid",
    )
    if fw["family"] != "inet" or not all(
        type(fw[k]) is str and _NAME.fullmatch(fw[k])
        for k in ("table", "chain", "anchor_comment")
    ):
        raise refused("config.invalid")
    runner = _exact(
        doc["runner"],
        {"archive", "archive_sha256", "workspace_mb", "memory_mb"},
        "config.invalid",
    )
    if type(runner["archive"]) is not str or _ABS.fullmatch(runner["archive"]) is None:
        raise refused("config.invalid")
    for key, low, high in (("workspace_mb", 16, 4096), ("memory_mb", 64, 8192)):
        if type(runner[key]) is not int or not low <= runner[key] <= high:
            raise refused("config.invalid")
    try:
        hp.hex64(doc["policy_digest"])
        hp.hex64(doc["controller_digest"])
        hp.hex64(runner["archive_sha256"])
    except hp.ProtocolRefused:
        raise refused("config.invalid") from None
    return HostConfig(
        firewall=Firewall(fw["family"], fw["table"], fw["chain"], fw["anchor_comment"]),
        policy_digest=doc["policy_digest"],
        controller_digest=doc["controller_digest"],
        archive=Path(runner["archive"]),
        archive_sha256=runner["archive_sha256"],
        workspace_mb=runner["workspace_mb"],
        memory_mb=runner["memory_mb"],
    )


@dataclasses.dataclass(frozen=True)
class Policy:
    digest: str
    origins: Any
    aliases: hr.AliasPolicy


def parse_policy(value: Any) -> Policy:
    """The versioned endpoint policy: a FINITE exact origin set plus aliases."""
    doc = _exact(value, {"schema", "version", "origins", "aliases"}, "policy.invalid")
    if (
        doc["schema"] != POLICY_SCHEMA
        or type(doc["version"]) is not int
        or doc["version"] < 1
    ):
        raise refused("policy.invalid")
    try:
        origins = OriginPolicy.from_mapping(doc["origins"])
        aliases = hr.AliasPolicy.from_mapping(doc["aliases"])
    except Exception:
        raise refused("policy.invalid") from None
    return Policy(hp.digest(doc), origins, aliases)


def validate_destinations(
    rows: Any, routable: Callable[[Any], bool], purposes: frozenset[str]
) -> list[dict[str, Any]]:
    if type(rows) is not list or not 1 <= len(rows) <= hp.MAX_ADDRESSES:
        raise refused("manifest.invalid")
    seen: set[tuple[str, str]] = set()
    for row in rows:
        row = _exact(
            row, {"purpose", "origin", "family", "address", "port"}, "manifest.invalid"
        )
        if (
            row["purpose"] not in purposes
            or type(row["port"]) is not int
            or row["port"] != 443
        ):
            raise refused("manifest.invalid")
        try:
            hp.origin(row["origin"])
            hp.address_row({"family": row["family"], "address": row["address"]})
        except hp.ProtocolRefused:
            raise refused("manifest.invalid") from None
        key = (row["purpose"], row["address"])
        if not routable(ipaddress.ip_address(row["address"])) or key in seen:
            raise refused("manifest.invalid")
        seen.add(key)
    if len({r["address"] for r in rows}) > hp.MAX_ADDRESSES:
        raise refused("manifest.invalid")
    return rows


def validate_bootstrap(value: Any, routable: Callable[[Any], bool]) -> dict[str, Any]:
    doc = _exact(value, {"schema", "destinations"}, "manifest.invalid")
    if doc["schema"] != BOOTSTRAP_SCHEMA:
        raise refused("manifest.invalid")
    rows = validate_destinations(
        doc["destinations"], routable, frozenset({"runner-https", "module-https"})
    )
    if len({r["address"] for r in rows}) != len(rows):
        # One exact rule per bootstrap address keeps ownership unambiguous.
        raise refused("manifest.invalid")
    return doc


def effective_manifest(
    bootstrap: Mapping[str, Any], origin: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    broker = [
        {
            "purpose": "oidc-https",
            "origin": origin,
            "family": r["family"],
            "address": r["address"],
            "port": hp.BROKER_PORT,
        }
        for r in rows
    ]
    return {
        "schema": EFFECTIVE_SCHEMA,
        "destinations": [*bootstrap["destinations"], *broker],
    }


# ── nftables rule rendering and exact readback ─────────────────────────────


def comment_for(lease: str, kind: str, index: int | None = None) -> str:
    return f"{TAG}:{lease}:{kind}" + ("" if index is None else f":{index}")


def rule_specs(
    lease: str, destinations: list[dict[str, Any]], start: int = 0
) -> list[dict[str, Any]]:
    return [
        {
            "kind": "a",
            "index": start + i,
            "family": d["family"],
            "address": d["address"],
            "port": d["port"],
        }
        for i, d in enumerate(destinations)
    ]


def _safe_tokens(fw: Firewall, uid: int) -> None:
    if (
        type(uid) is not int
        or uid <= 0
        or not all(_NAME.fullmatch(v) for v in (fw.table, fw.chain, fw.anchor))
    ):
        raise refused("firewall.invalid")


def accept_line(
    fw: Firewall, lease: str, uid: int, spec: Mapping[str, Any], anchor: int
) -> str:
    _safe_tokens(fw, uid)
    hp.lease_id(lease)
    hp.address_row({"family": spec["family"], "address": spec["address"]})
    if (
        type(anchor) is not int
        or anchor <= 0
        or spec["port"] != 443
        or type(spec["index"]) is not int
    ):
        raise refused("firewall.invalid")
    proto = "ip" if spec["family"] == 4 else "ip6"
    comment = comment_for(lease, "a", spec["index"])
    return (
        f"insert rule {fw.family} {fw.table} {fw.chain} position {anchor} "
        f"meta skuid {uid} {proto} daddr {spec['address']} tcp dport {spec['port']} "
        f'ct state new counter accept comment "{comment}"'
    )


def guard_line(fw: Firewall, lease: str, uid: int) -> str:
    _safe_tokens(fw, uid)
    hp.lease_id(lease)
    # No position: inserted at the head, ahead of any baseline ESTABLISHED accept.
    return (
        f"insert rule {fw.family} {fw.table} {fw.chain} "
        f'meta skuid {uid} counter drop comment "{comment_for(lease, "x")}"'
    )


def delete_line(fw: Firewall, handle: int) -> str:
    if type(handle) is not int or handle <= 0:
        raise refused("firewall.invalid")
    return f"delete rule {fw.family} {fw.table} {fw.chain} handle {handle}"


def owned(rule: Mapping[str, Any]) -> bool:
    comment = rule.get("comment")
    return isinstance(comment, str) and comment.startswith(TAG + ":")


def stable_digest(rules: list[Mapping[str, Any]]) -> str:
    """Digest of rules minus handles and counter values (they change)."""
    clean = []
    for rule in rules:
        item = {k: v for k, v in rule.items() if k != "handle"}
        item["expr"] = [
            e
            for e in rule.get("expr", [])
            if not (isinstance(e, dict) and "counter" in e)
        ]
        clean.append(item)
    return hp.digest(clean)


def _matches(rule: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        e["match"] for e in rule.get("expr", []) if isinstance(e, dict) and "match" in e
    ]


def _uid_ok(right: Any, uid: int, user: str) -> bool:
    return (type(right) is int and right == uid) or right == user


def _shape_ok(
    rule: Mapping[str, Any], fw: Firewall, verdict: str, n_matches: int
) -> bool:
    expr = rule.get("expr")
    if (
        rule.get("family") != fw.family
        or rule.get("table") != fw.table
        or rule.get("chain") != fw.chain
        or type(expr) is not list
    ):
        return False
    kinds = []
    for item in expr:
        if not isinstance(item, dict) or len(item) != 1:
            return False
        kinds.append(next(iter(item)))
    return (
        kinds.count("match") == n_matches
        and kinds.count("counter") == 1
        and kinds.count(verdict) == 1
        and kinds[-1] == verdict
        and len(kinds) == n_matches + 2
    )


def is_exact_accept(
    rule: Mapping[str, Any],
    fw: Firewall,
    lease: str,
    uid: int,
    user: str,
    spec: Mapping[str, Any],
) -> bool:
    if rule.get("comment") != comment_for(lease, "a", spec["index"]) or not _shape_ok(
        rule, fw, "accept", 4
    ):
        return False
    proto = "ip" if spec["family"] == 4 else "ip6"
    found = {"uid": 0, "daddr": 0, "dport": 0, "state": 0}
    for m in _matches(rule):
        left, right, op = m.get("left"), m.get("right"), m.get("op")
        if (
            left == {"meta": {"key": "skuid"}}
            and op == "=="
            and _uid_ok(right, uid, user)
        ):
            found["uid"] += 1
        elif (
            left == {"payload": {"protocol": proto, "field": "daddr"}}
            and op == "=="
            and right == spec["address"]
        ):
            found["daddr"] += 1
        elif (
            left == {"payload": {"protocol": "tcp", "field": "dport"}}
            and op == "=="
            and right == spec["port"]
        ):
            found["dport"] += 1
        elif (
            left == {"ct": {"key": "state"}}
            and op in ("==", "in")
            and right in ("new", ["new"], {"set": ["new"]})
        ):
            found["state"] += 1
        else:
            return False
    return all(v == 1 for v in found.values())


def is_exact_guard(
    rule: Mapping[str, Any], fw: Firewall, lease: str, uid: int, user: str
) -> bool:
    if rule.get("comment") != comment_for(lease, "x") or not _shape_ok(
        rule, fw, "drop", 1
    ):
        return False
    (m,) = _matches(rule)
    return (
        m.get("left") == {"meta": {"key": "skuid"}}
        and m.get("op") == "=="
        and _uid_ok(m.get("right"), uid, user)
    )


def classify_owned(
    rules: list[Mapping[str, Any]],
    fw: Firewall,
    lease: str,
    uid: int,
    user: str,
    specs: list[Mapping[str, Any]],
    guard_expected: bool,
) -> tuple[list[int], bool]:
    """(handles of exact owned rules, guard present). Drift refuses.

    Each owned rule must match exactly one recorded spec (or the guard), each
    spec at most once. A rule carrying the tag of another lease, or a tagged
    rule this lease did not record exactly, is drift: nothing is deleted.
    """
    handles: list[int] = []
    used: set[int] = set()
    guard = False
    for rule in rules:
        if not owned(rule):
            continue
        handle = rule.get("handle")
        if type(handle) is not int:
            raise refused("cleanup.drift")
        if guard_expected and is_exact_guard(rule, fw, lease, uid, user):
            if guard:
                raise refused("cleanup.drift")
            guard = True
            handles.append(handle)
            continue
        hits = [
            i
            for i, s in enumerate(specs)
            if is_exact_accept(rule, fw, lease, uid, user, s)
        ]
        if len(hits) != 1 or hits[0] in used:
            raise refused("cleanup.drift")
        used.add(hits[0])
        handles.append(handle)
    return handles, guard


# ── host effectors (real implementation) ───────────────────────────────────

_ENV: Final = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}

_RUNNER_CHILD: Final = (
    "import json,os,sys;p=json.loads(sys.stdin.buffer.read(1048577));"
    "e={'HOME':os.getcwd(),'PATH':'/usr/bin:/bin','LANG':'C.UTF-8',"
    "'ACTIONS_RUNNER_INPUT_JITCONFIG':p['jit']};"
    "os.execve('./run.sh',['./run.sh'],e)"
)


class SystemHost:
    """Fixed-argv effectors. Nothing here accepts a caller-chosen command."""

    cgroup_root = Path("/sys/fs/cgroup")

    def _run(
        self,
        argv: list[str],
        *,
        data: bytes | None = None,
        timeout: float = 15,
        check: bool = True,
    ) -> bytes:
        try:
            result = subprocess.run(
                argv,
                input=data,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=_ENV,
            )
        except (OSError, subprocess.SubprocessError):
            raise refused("host.command") from None
        if check and result.returncode:
            raise refused("host.command")
        return result.stdout

    def boot_id(self) -> str:
        try:
            return hp.boot_id(
                Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            )
        except (OSError, hp.ProtocolRefused):
            raise refused("boot.mismatch") from None

    def nft_list(self, fw: Firewall) -> list[Any]:
        raw = self._run(["nft", "-j", "list", "chain", fw.family, fw.table, fw.chain])
        try:
            doc = json.loads(raw)
            return list(doc["nftables"])
        except (ValueError, KeyError, TypeError):
            raise refused("firewall.readback") from None

    def nft_apply(self, script: str) -> None:
        self._run(["nft", "-f", "-"], data=script.encode("ascii"))

    def identity(self, user: str) -> tuple[int, int] | None:
        try:
            entry = pwd.getpwnam(user)
        except KeyError:
            return None
        return entry.pw_uid, entry.pw_gid

    def users_with_prefix(self, prefix: str) -> list[str]:
        return [e.pw_name for e in pwd.getpwall() if e.pw_name.startswith(prefix)]

    def create_identity(self, user: str, home: Path) -> tuple[int, int]:
        self._run(
            [
                "useradd",
                "--system",
                "--user-group",
                "--no-create-home",
                "--home-dir",
                str(home),
                "--shell",
                "/usr/sbin/nologin",
                user,
            ]
        )
        found = self.identity(user)
        if found is None:
            raise refused("identity.create")
        return found

    def remove_identity(self, user: str) -> None:
        self._run(["userdel", user])

    def arm_timer(self, unit: str, seconds: int, argv: list[str]) -> None:
        self._run(
            [
                "systemd-run",
                "--quiet",
                "--collect",
                "--unit",
                unit,
                f"--on-active={seconds}s",
                "--timer-property=AccuracySec=1s",
                "--property=Type=oneshot",
                *argv,
            ]
        )

    def disarm_timer(self, unit: str) -> None:
        self._run(["systemctl", "stop", unit + ".timer"], check=False)

    def start_service(self, unit: str, argv: list[str], properties: list[str]) -> None:
        self._run(
            [
                "systemd-run",
                "--quiet",
                "--collect",
                "--unit",
                unit,
                *[f"--property={p}" for p in properties],
                *argv,
            ]
        )

    def launch_runner(
        self, unit: str, properties: list[str], python: str, payload: bytes
    ) -> None:
        argv = [
            "systemd-run",
            "--quiet",
            "--collect",
            "--pipe",
            "--unit",
            unit,
            *[f"--property={p}" for p in properties],
            python,
            "-I",
            "-c",
            _RUNNER_CHILD,
        ]
        try:
            child = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=_ENV,
                start_new_session=True,
            )
            assert child.stdin is not None
            child.stdin.write(payload)
            child.stdin.close()
        except (OSError, subprocess.SubprocessError):
            raise refused("host.command") from None

    def stop_unit(self, unit: str) -> None:
        self._run(["systemctl", "stop", "--no-block", unit + ".service"], check=False)

    def kill_unit(self, unit: str) -> None:
        self._run(
            ["systemctl", "kill", "--signal=SIGKILL", unit + ".service"], check=False
        )

    def unit_cgroup(self, unit: str) -> str:
        return f"/system.slice/{unit}.service"

    def kill_cgroup(self, cgroup: str) -> None:
        target = self.cgroup_root / cgroup.lstrip("/") / "cgroup.kill"
        with contextlib.suppress(FileNotFoundError):
            target.write_text("1")

    def cgroup_pids(self, cgroup: str) -> list[int]:
        try:
            text = (self.cgroup_root / cgroup.lstrip("/") / "cgroup.procs").read_text()
        except FileNotFoundError:
            return []
        return [int(x) for x in text.split()]

    def mount_workspace(self, path: Path, size_mb: int) -> None:
        self._run(
            [
                "mount",
                "-t",
                "tmpfs",
                "-o",
                f"size={size_mb}m,nodev,nosuid,mode=0700",
                "tmpfs",
                str(path),
            ]
        )

    def unmount_workspace(self, path: Path) -> None:
        if os.path.ismount(path):
            self._run(["umount", str(path)])

    def now_ns(self) -> int:
        return time.monotonic_ns()


# ── paths ──────────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class Paths:
    run_root: Path = Path(hp.RUN_ROOT)
    state_root: Path = Path("/var/lib/dotmac-lane3-handoff")
    config_root: Path = Path("/etc/dotmac-lane3-handoff")
    work_root: Path = Path("/run/dotmac-lane3-handoff-work")
    python: str = "/usr/bin/python3"

    @property
    def journal(self) -> Path:
        return self.state_root / "journal.json"

    @property
    def bin(self) -> Path:
        return self.state_root / "bin"


def controller_digest(directory: Path) -> str:
    """Digest of the exact module bytes the controller runs (and pins)."""
    parts = {}
    for name in PINNED_MODULES:
        try:
            parts[name] = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        except OSError:
            raise refused("controller.digest") from None
    return hp.digest(parts)


# ── the job-socket state machine (pure; unit-testable) ─────────────────────


def _history(j: dict[str, Any], state: str, now: int) -> None:
    j["state"] = state
    j["history"].append({"state": state, "at_monotonic_ns": now})


def _end(j: dict[str, Any], label: str, now: int) -> tuple[dict[str, Any], bool]:
    state = "EXPIRED" if label == "lease.expired" else "REFUSED"
    if j["state"] not in TERMINAL:
        j["category"] = label
        _history(j, state, now)
    return hp.response("REFUSED", label), True


def transition(
    j: dict[str, Any], request: Mapping[str, Any], peer: tuple[int, int], now: int
) -> tuple[dict[str, Any], bool]:
    """Apply one validated job request to journal ``j`` (mutated in place).

    Returns ``(response, invalidate)``. Any refusal to an authenticated peer
    ends the lease (``invalidate``); crash/restart resumes cleanup, never new
    authority. A duplicate exact report returns its recorded status.
    """
    if j["state"] in TERMINAL:
        return hp.response("REFUSED", j.get("category") or "state.invalid"), False
    if request["lease_id"] != j["lease_id"]:
        return _end(j, "lease.unknown", now)
    if not hmac.compare_digest(request["nonce"], j["nonce"]):
        return _end(j, "nonce.mismatch", now)
    if j.get("expires_at_monotonic_ns") is None or now >= j["expires_at_monotonic_ns"]:
        return _end(j, "lease.expired", now)
    op = request["op"]
    if op == "report":
        value = hp.digest(dict(request))
        if j["report"] is not None:
            if value == j["report_digest"] and list(peer) == j["peer"]:
                return _status(j), False
            return _end(j, "report.changed", now)
        if j["state"] != "BOOTSTRAP":
            return _end(j, "state.invalid", now)
        if now > j["launched_at_monotonic_ns"] + hp.METADATA_DEADLINE_NS:
            return _end(j, "deadline.insufficient", now)
        if any(request["flags"].values()):
            return _end(j, "origin.flags", now)
        j["report"] = dict(request)
        j["report_digest"] = value
        j["peer"] = list(peer)
        _history(j, "REPORTED", now)
        return hp.response("WAIT"), False
    if j["peer"] is None or list(peer) != j["peer"]:
        return _end(j, "peer.refused", now)
    if op == "poll":
        return _status(j), False
    if op == "consume":
        if j["state"] == "CONSUMED":
            return _end(j, "grant.consumed", now)
        if j["state"] != "GRANTED":
            return _end(j, "state.invalid", now)
        if not hmac.compare_digest(request["grant_digest"], j["grant_digest"]):
            return _end(j, "grant.mismatch", now)
        if now >= j["grant"]["expires_at_monotonic_ns"]:
            return _end(j, "lease.expired", now)
        _history(j, "CONSUMED", now)
        return hp.response("CONSUMED"), False
    return _end(j, "schema.invalid", now)


def _status(j: Mapping[str, Any]) -> dict[str, Any]:
    state = j["state"]
    if state in ("REPORTED", "VALIDATED"):
        return hp.response("WAIT")
    if state in ("GRANTED", "CONSUMED"):
        return hp.response(state)
    return hp.response("REFUSED", "state.invalid")


# ── management-channel operation schemas (fixed; data only) ────────────────

MGMT_REQUESTS: Final[dict[str, set[str]]] = {
    "prepare": {"protocol", "op", "bootstrap_manifest", "policy_digest"},
    "bootstrap": {"protocol", "op", "lease_id", "jit_config"},
    "status": {"protocol", "op", "lease_id"},
    "grant": {
        "protocol",
        "op",
        "lease_id",
        "report_digest",
        "binding",
        "origin",
        "snapshot",
        "policy_digest",
    },
    "refuse": {"protocol", "op", "lease_id", "category"},
    "cleanup": {"protocol", "op", "lease_id"},
}
MGMT_STDIN_OPS: Final = frozenset(MGMT_REQUESTS)
MGMT_FIXED_OPS: Final = frozenset({"serve", "expire", "reconcile"})


def validate_mgmt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("protocol") != hp.PROTOCOL:
        raise refused("schema.invalid")
    op = value.get("op")
    if (
        type(op) is not str
        or op not in MGMT_REQUESTS
        or set(value) != MGMT_REQUESTS[op]
    ):
        raise refused("schema.invalid")
    try:
        if "lease_id" in value:
            hp.lease_id(value["lease_id"])
        if "policy_digest" in value:
            hp.hex64(value["policy_digest"])
        if "report_digest" in value:
            hp.hex64(value["report_digest"])
        if op == "grant":
            hp.validate_binding(value["binding"])
            hp.origin(value["origin"])
            snap = _exact(
                value["snapshot"],
                {"digest", "ttl_remaining_ms", "addresses"},
                "schema.invalid",
            )
            hp.hex64(snap["digest"])
            if (
                type(snap["ttl_remaining_ms"]) is not int
                or not 1
                <= snap["ttl_remaining_ms"]
                <= hp.SNAPSHOT_MAX_AGE_NS // 1_000_000
            ):
                raise refused("schema.invalid")
    except hp.ProtocolRefused as exc:
        raise refused(exc.label) from None
    if op == "bootstrap":
        jit = value["jit_config"]
        if (
            type(jit) is not str
            or not 1 <= len(jit) <= MAX_JIT
            or _JIT.fullmatch(jit) is None
        ):
            raise refused("schema.invalid")
    if op == "refuse" and value["category"] not in hp.CATEGORIES:
        raise refused("schema.invalid")
    return value


# ── the controller ──────────────────────────────────────────────────────────


class Controller:
    """One lease at a time, under one exclusive host lock."""

    def __init__(
        self,
        *,
        paths: Paths | None = None,
        host: Any = None,
        routable: Callable[[Any], bool] = hr.globally_routable,
        module_dir: Path | None = None,
        wall_clock: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(
            datetime.UTC
        ),
    ) -> None:
        self.paths = paths or Paths()
        self.host = host if host is not None else SystemHost()
        self.routable = routable
        self.module_dir = module_dir or Path(__file__).resolve().parent
        self.wall_clock = wall_clock
        self.self_unit: str | None = None
        self._lock_fd: int | None = None

    def _checkpoint(self, name: str) -> None:
        """Crash-point seam for hosted integration tests; no-op otherwise."""
        hook = getattr(self.host, "checkpoint", None)
        if hook is not None:
            hook(name)

    # journal ---------------------------------------------------------------

    def _state_dir(self, create: bool = False) -> int:
        root = self.paths.state_root
        if create:
            try:
                os.mkdir(root, 0o700)
            except FileExistsError:
                pass
        return open_dir(root, forbid=0o077)

    @contextlib.contextmanager
    def locked(self, create: bool = False) -> Iterator[None]:
        if self._lock_fd is not None:
            yield
            return
        dfd = self._state_dir(create)
        try:
            fd = os.open(
                "lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=dfd,
            )
        finally:
            os.close(dfd)
        try:
            deadline = time.monotonic() + LOCK_WAIT_S
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise refused("lock.busy") from None
                    time.sleep(0.05)
            self._lock_fd = fd
            yield
        finally:
            self._lock_fd = None
            os.close(fd)

    def load(self) -> dict[str, Any] | None:
        try:
            raw = read_protected(
                self.paths.journal, forbid=0o077, exact_mode=0o600, limit=MAX_MGMT_INPUT
            )
        except ControllerRefused:
            try:
                self.paths.journal.lstat()
            except FileNotFoundError:
                return None
            raise refused("journal.unsafe") from None
        j = strict_json(raw)
        if j.get("schema") != JOURNAL_SCHEMA:
            raise refused("journal.unsafe")
        return j

    def save(self, j: Mapping[str, Any]) -> None:
        if self._lock_fd is None:
            raise refused("lock.required")
        dfd = self._state_dir()
        try:
            write_atomic(
                dfd, "journal.json", hp.canonical_json(dict(j)), mode=0o600, gid=0
            )
        finally:
            os.close(dfd)

    def _archive(self, j: Mapping[str, Any]) -> None:
        dfd = self._state_dir()
        try:
            with contextlib.suppress(FileExistsError):
                os.mkdir("closed", 0o700, dir_fd=dfd)
            cfd = os.open("closed", _DIR_FLAGS, dir_fd=dfd)
            try:
                _check_dir(cfd, 0o077)
                write_atomic(
                    cfd,
                    j["lease_id"] + ".json",
                    hp.canonical_json(dict(j)),
                    mode=0o600,
                    gid=0,
                )
            finally:
                os.close(cfd)
            os.unlink("journal.json", dir_fd=dfd)
            os.fsync(dfd)
        finally:
            os.close(dfd)

    def _active(self, lease: str) -> dict[str, Any]:
        j = self.load()
        if j is None or j["lease_id"] != lease or j["state"] == "CLOSED":
            raise refused("lease.unknown")
        if j["boot_id"] != self.host.boot_id():
            raise refused("boot.mismatch")
        return j

    # configuration ---------------------------------------------------------

    def host_config(self) -> HostConfig:
        return parse_host_config(
            strict_json(read_protected(self.paths.config_root / "host.json"))
        )

    def policy(self) -> Policy:
        return parse_policy(
            strict_json(read_protected(self.paths.config_root / "policy.json"))
        )

    def _approval(self, manifest_digest: str, policy_digest: str, cdigest: str) -> None:
        doc = strict_json(read_protected(self.paths.config_root / "approval.json"))
        doc = _exact(
            doc,
            {
                "schema",
                "approved",
                "manifest_digest",
                "policy_digest",
                "controller_digest",
                "expires_at",
            },
            "approval.missing",
        )
        try:
            expires = datetime.datetime.fromisoformat(doc["expires_at"])
        except (TypeError, ValueError):
            raise refused("approval.missing") from None
        now = self.wall_clock()
        if (
            doc["schema"] != APPROVAL_SCHEMA
            or doc["approved"] is not True
            or doc["manifest_digest"] != manifest_digest
            or doc["policy_digest"] != policy_digest
            or doc["controller_digest"] != cdigest
            or expires.tzinfo is None
            or not now < expires <= now + APPROVAL_MAX
        ):
            raise refused("approval.missing")

    # firewall --------------------------------------------------------------

    def chain(self, fw: Firewall) -> tuple[list[dict[str, Any]], int]:
        """(rules, anchor handle); default DROP output filter and one anchor."""
        doc = self.host.nft_list(fw)
        chains = [x["chain"] for x in doc if isinstance(x, dict) and "chain" in x]
        if (
            len(chains) != 1
            or chains[0].get("policy") != "drop"
            or chains[0].get("hook") != "output"
        ):
            raise refused("firewall.baseline")
        rules = [x["rule"] for x in doc if isinstance(x, dict) and "rule" in x]
        anchors = [r for r in rules if r.get("comment") == fw.anchor and not owned(r)]
        if len(anchors) != 1 or type(anchors[0].get("handle")) is not int:
            raise refused("firewall.baseline")
        return rules, anchors[0]["handle"]

    def _install(
        self, j: dict[str, Any], fw: Firewall, new: list[dict[str, Any]]
    ) -> None:
        """One ``nft -f`` transaction, then exact readback of all owned rules."""
        rules, anchor = self.chain(fw)
        if stable_digest([r for r in rules if not owned(r)]) != j["baseline_digest"]:
            raise refused("install.failed")
        script = "".join(
            accept_line(fw, j["lease_id"], j["uid"], s, anchor) + "\n" for s in new
        )
        self.host.nft_apply(script)
        self._checkpoint("install.applied")
        after, _ = self.chain(fw)
        expected = [*j["rules"], *new]
        handles, guard = classify_owned(
            after, fw, j["lease_id"], j["uid"], j["user"], expected, False
        )
        if (
            guard
            or len(handles) != len(expected)
            or stable_digest([r for r in after if not owned(r)]) != j["baseline_digest"]
        ):
            raise refused("install.failed")

    # lease directory -------------------------------------------------------

    def _lease_dir(self, j: Mapping[str, Any]) -> Path:
        return self.paths.run_root / j["lease_id"]

    def _write_lease_file(
        self, j: Mapping[str, Any], name: str, value: Mapping[str, Any]
    ) -> None:
        dfd = open_dir(self._lease_dir(j), forbid=0o027, gid=j["gid"])
        try:
            write_atomic(
                dfd,
                name,
                hp.canonical_json(dict(value)),
                mode=hp.FILE_MODE,
                gid=j["gid"],
            )
        finally:
            os.close(dfd)

    # operations ------------------------------------------------------------

    def prepare(self, request: Mapping[str, Any]) -> dict[str, Any]:
        req = validate_mgmt(request)
        if req["op"] != "prepare":
            raise refused("schema.invalid")
        cfg = self.host_config()
        policy = self.policy()
        if not req["policy_digest"] == policy.digest == cfg.policy_digest:
            raise refused("policy.mismatch")
        manifest = validate_bootstrap(req["bootstrap_manifest"], self.routable)
        mdigest = hp.digest(manifest)
        cdigest = controller_digest(self.module_dir)
        if cdigest != cfg.controller_digest:
            raise refused("controller.digest")
        self._approval(mdigest, policy.digest, cdigest)
        archive = read_protected(cfg.archive, limit=1 << 30)
        if hashlib.sha256(archive).hexdigest() != cfg.archive_sha256:
            raise refused("runner.archive")
        with self.locked(create=True):
            if self.load() is not None:
                raise refused("lease.active")
            rules, _ = self.chain(cfg.firewall)
            if any(owned(r) for r in rules):
                raise refused("firewall.drift")
            if self.host.users_with_prefix(USER_PREFIX):
                raise refused("identity.drift")
            lease = secrets.token_hex(16)
            boot = self.host.boot_id()
            now = self.host.now_ns()
            stem = f"{TAG}-{lease[:12]}"
            j: dict[str, Any] = {
                "schema": JOURNAL_SCHEMA,
                "protocol": hp.PROTOCOL,
                "lease_id": lease,
                "nonce": secrets.token_hex(32),
                "boot_id": boot,
                "state": "PREPARED",
                "history": [{"state": "PREPARED", "at_monotonic_ns": now}],
                "category": None,
                "outcome": None,
                "closing": False,
                "cleanup_blocked": None,
                "user": USER_PREFIX + lease[:12],
                "uid": None,
                "gid": None,
                "units": {
                    "runner": stem + "-runner",
                    "serve": stem + "-serve",
                    "expire": stem + "-expire",
                },
                "cgroup": None,
                "firewall": dataclasses.asdict(cfg.firewall),
                "policy_digest": policy.digest,
                "controller_digest": cdigest,
                "bootstrap_manifest": manifest,
                "bootstrap_manifest_digest": mdigest,
                "manifest_digest": None,
                "workspace": str(self.paths.work_root / lease),
                "baseline_digest": None,
                "t0_monotonic_ns": None,
                "expires_at_monotonic_ns": None,
                "launched_at_monotonic_ns": None,
                "timer_armed": False,
                "rules": [],
                "planned": [],
                "guard_planned": False,
                "report": None,
                "report_digest": None,
                "peer": None,
                "grant": None,
                "grant_digest": None,
                "cleanup": None,
            }
            self.save(j)
            try:
                self._checkpoint("prepare.journal")
                self._pin_modules(cdigest)
                uid, gid = self.host.create_identity(j["user"], Path(j["workspace"]))
                j["uid"], j["gid"] = uid, gid
                j["cgroup"] = self.host.unit_cgroup(j["units"]["runner"])
                self.save(j)
                self._checkpoint("prepare.identity")
                self._make_lease_dir(j)
                self._checkpoint("prepare.lease_dir")
                self._make_workspace(j, cfg, archive)
                self._checkpoint("prepare.workspace")
                archive = b""
            except BaseException as exc:
                self._cleanup_locked(j, "REFUSED", category_of(exc))
                raise
            return {
                "protocol": hp.PROTOCOL,
                "op": "prepare",
                "lease_id": lease,
                "boot_id": boot,
                "state": "PREPARED",
                "manifest_digest": mdigest,
                "controller_digest": cdigest,
            }

    def _pin_modules(self, cdigest: str) -> None:
        dfd = self._state_dir()
        try:
            with contextlib.suppress(FileExistsError):
                os.mkdir("bin", 0o700, dir_fd=dfd)
            bfd = os.open("bin", _DIR_FLAGS, dir_fd=dfd)
        finally:
            os.close(dfd)
        try:
            _check_dir(bfd, 0o077)
            for name in PINNED_MODULES:
                write_atomic(
                    bfd, name, (self.module_dir / name).read_bytes(), mode=0o600, gid=0
                )
        finally:
            os.close(bfd)
        if controller_digest(self.paths.bin) != cdigest:
            raise refused("controller.digest")

    def _make_lease_dir(self, j: Mapping[str, Any]) -> None:
        root = self.paths.run_root
        with contextlib.suppress(FileExistsError):
            os.mkdir(root, 0o755)
        rfd = open_dir(root)
        try:
            os.mkdir(j["lease_id"], 0o700, dir_fd=rfd)
            lfd = os.open(j["lease_id"], _DIR_FLAGS, dir_fd=rfd)
            try:
                os.fchown(lfd, 0, j["gid"])
                os.fchmod(lfd, hp.LEASE_DIR_MODE)
                _check_dir(lfd, 0o027, j["gid"])
            finally:
                os.close(lfd)
        finally:
            os.close(rfd)

    def _make_workspace(
        self, j: Mapping[str, Any], cfg: HostConfig, archive: bytes
    ) -> None:
        root = self.paths.work_root
        with contextlib.suppress(FileExistsError):
            os.mkdir(root, 0o755)
        open_dir_fd = open_dir(root)
        os.close(open_dir_fd)
        workspace = Path(j["workspace"])
        os.mkdir(workspace, 0o700)
        self.host.mount_workspace(workspace, cfg.workspace_mb)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as bundle:
            bundle.extractall(workspace, filter="data")
        for base, dirs, files in os.walk(workspace):
            for name in [*dirs, *files]:
                os.lchown(os.path.join(base, name), j["uid"], j["gid"])
        os.chown(workspace, j["uid"], j["gid"])

    def bootstrap(self, request: Mapping[str, Any]) -> dict[str, Any]:
        req = validate_mgmt(request)
        if req["op"] != "bootstrap":
            raise refused("schema.invalid")
        payload = json.dumps({"jit": req["jit_config"]}).encode("ascii")
        with self.locked():
            j = self._active(req["lease_id"])
            if j["state"] != "PREPARED":
                raise refused("state.invalid")
            try:
                cfg = self.host_config()
                fw = Firewall(**j["firewall"])
                if dataclasses.asdict(cfg.firewall) != j["firewall"]:
                    raise refused("config.drift")
                rules, _ = self.chain(fw)
                if any(owned(r) for r in rules):
                    raise refused("firewall.drift")
                specs = rule_specs(
                    j["lease_id"], j["bootstrap_manifest"]["destinations"]
                )
                # T0: immediately before the first task egress exception.
                t0 = self.host.now_ns()
                j["t0_monotonic_ns"] = t0
                j["expires_at_monotonic_ns"] = t0 + hp.WINDOW_NS
                j["baseline_digest"] = stable_digest([r for r in rules if not owned(r)])
                j["planned"] = specs
                self.save(j)
                self._checkpoint("bootstrap.planned")
                # Arm the independent rollback BEFORE any rule change.
                seconds = max(
                    1,
                    (j["expires_at_monotonic_ns"] - self.host.now_ns())
                    // 1_000_000_000,
                )
                j["timer_armed"] = True  # recorded first: disarm tolerates absence
                self.save(j)
                self.host.arm_timer(
                    j["units"]["expire"],
                    int(seconds),
                    self._pinned_argv("expire", j["lease_id"]),
                )
                self._checkpoint("bootstrap.armed")
                self._install(j, fw, specs)
                j["rules"], j["planned"] = specs, []
                self.save(j)
                self._checkpoint("bootstrap.recorded")
                self._start_server(j)
                self._checkpoint("bootstrap.served")
                challenge = {
                    "protocol": hp.PROTOCOL,
                    "lease_id": j["lease_id"],
                    "nonce": j["nonce"],
                    "boot_id": j["boot_id"],
                    "expires_at_monotonic_ns": j["expires_at_monotonic_ns"],
                }
                self._write_lease_file(
                    j, hp.CHALLENGE_NAME, hp.validate_challenge(challenge)
                )
                self._checkpoint("bootstrap.challenge")
                now = self.host.now_ns()
                remaining = j["expires_at_monotonic_ns"] - now
                if remaining <= hp.GRANT_MIN_REMAINING_NS:
                    raise refused("deadline.insufficient")
                j["launched_at_monotonic_ns"] = now
                _history(j, "BOOTSTRAP", now)
                self.save(j)
                self.host.launch_runner(
                    j["units"]["runner"],
                    self._runner_properties(j, cfg, remaining),
                    self.paths.python,
                    payload,
                )
                payload = b""
                self._checkpoint("bootstrap.launched")
            except BaseException as exc:
                payload = b""
                self._cleanup_locked(j, "REFUSED", category_of(exc))
                raise
            return {
                "protocol": hp.PROTOCOL,
                "op": "bootstrap",
                "lease_id": j["lease_id"],
                "state": "BOOTSTRAP",
                "remaining_ms": remaining // 1_000_000,
            }

    def _pinned_argv(self, op: str, lease: str) -> list[str]:
        hp.lease_id(lease)
        return [
            self.paths.python,
            "-I",
            str(self.paths.bin / "lane3_handoff_controller.py"),
            op,
            lease,
        ]

    def _runner_properties(
        self, j: Mapping[str, Any], cfg: HostConfig, remaining_ns: int
    ) -> list[str]:
        workspace = j["workspace"]
        return [
            f"User={j['user']}",
            f"Group={j['user']}",
            f"WorkingDirectory={workspace}",
            f"RuntimeMaxSec={max(1, remaining_ns // 1_000_000_000)}",
            "KillMode=control-group",
            "TimeoutStopSec=5",
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
            "ProtectHome=yes",
            "PrivateTmp=yes",
            "RestrictSUIDSGID=yes",
            f"ReadWritePaths={workspace}",
            f"InaccessiblePaths={self.paths.state_root}",
            f"MemoryMax={cfg.memory_mb}M",
        ]

    def _start_server(self, j: Mapping[str, Any]) -> None:
        remaining = (j["expires_at_monotonic_ns"] - self.host.now_ns()) // 1_000_000_000
        self.host.start_service(
            j["units"]["serve"],
            self._pinned_argv("serve", j["lease_id"]),
            [
                f"RuntimeMaxSec={max(1, remaining)}",
                "KillMode=control-group",
                "NoNewPrivileges=yes",
            ],
        )
        deadline = time.monotonic() + hp.IO_DEADLINE_S
        path = self._lease_dir(j) / hp.SOCKET_NAME
        while time.monotonic() < deadline:
            try:
                info = path.lstat()
            except FileNotFoundError:
                time.sleep(0.05)
                continue
            if (
                stat.S_ISSOCK(info.st_mode)
                and info.st_uid == 0
                and info.st_gid == j["gid"]
                and stat.S_IMODE(info.st_mode) == hp.SOCKET_MODE
            ):
                return
            break
        raise refused("socket.unready")

    def status(self, request: Mapping[str, Any]) -> dict[str, Any]:
        req = validate_mgmt(request)
        if req["op"] != "status":
            raise refused("schema.invalid")
        with self.locked():
            j = self._active(req["lease_id"])
            now = self.host.now_ns()
            if (
                j["expires_at_monotonic_ns"] is not None
                and now >= j["expires_at_monotonic_ns"]
                and j["state"] not in TERMINAL
            ):
                self._cleanup_locked(j, "EXPIRED", "lease.expired")
            remaining = (
                None
                if j["expires_at_monotonic_ns"] is None
                else max(0, j["expires_at_monotonic_ns"] - now) // 1_000_000
            )
            return {
                "protocol": hp.PROTOCOL,
                "op": "status",
                "lease_id": j["lease_id"],
                "state": j["state"],
                "category": j["category"],
                "report": j["report"],
                "report_digest": j["report_digest"],
                "remaining_ms": remaining,
            }

    def grant(self, request: Mapping[str, Any]) -> dict[str, Any]:
        req = validate_mgmt(request)
        if req["op"] != "grant":
            raise refused("schema.invalid")
        with self.locked():
            j = self._active(req["lease_id"])
            try:
                result = self._grant_locked(j, req)
            except BaseException as exc:
                self._cleanup_locked(j, "REFUSED", category_of(exc))
                raise
            return result

    def _grant_locked(
        self, j: dict[str, Any], req: Mapping[str, Any]
    ) -> dict[str, Any]:
        now = self.host.now_ns()
        if j["state"] != "REPORTED":
            raise refused("state.invalid")
        if now >= j["expires_at_monotonic_ns"]:
            raise refused("lease.expired")
        if not hmac.compare_digest(req["report_digest"], j["report_digest"]):
            raise refused("binding.mismatch")
        binding = req["binding"]
        expected = j["report"]["expected"]
        if (
            binding["run_id"] != expected["run_id"]
            or binding["run_attempt"] != expected["run_attempt"]
            or binding["workflow_sha"] != expected["workflow_sha"]
            or binding["starter_commit"] != expected["starter_commit"]
        ):
            raise refused("binding.mismatch")
        origin = req["origin"]
        if origin != j["report"]["origin"]:
            raise refused("origin.invalid")
        policy = self.policy()
        if not req["policy_digest"] == policy.digest == j["policy_digest"]:
            raise refused("binding.mismatch")
        if not policy.origins.admits(origin):
            raise refused("origin.not_admitted")
        snapshot = req["snapshot"]
        try:
            rows = hr.validate_snapshot_addresses(snapshot["addresses"], self.routable)
        except hr.SnapshotRefused:
            raise refused("snapshot.refused") from None
        bootstrap_rows = j["bootstrap_manifest"]["destinations"]
        bootstrap_addresses = {r["address"] for r in bootstrap_rows}
        if len(bootstrap_addresses | {r["address"] for r in rows}) > hp.MAX_ADDRESSES:
            # The controller cap is real; an answer is never truncated to fit.
            raise refused("snapshot.refused")
        snapshot_expires = (
            now - TRANSIT_ALLOWANCE_NS + snapshot["ttl_remaining_ms"] * 1_000_000
        )
        deadline = min(j["expires_at_monotonic_ns"], snapshot_expires)
        if deadline - now < hp.GRANT_MIN_REMAINING_NS:
            raise refused("deadline.insufficient")
        effective = effective_manifest(j["bootstrap_manifest"], origin, rows)
        validate_destinations(
            effective["destinations"],
            self.routable,
            frozenset({"runner-https", "module-https", "oidc-https"}),
        )
        manifest_digest = hp.digest(effective)
        grant = hp.validate_grant(
            {
                "protocol": hp.PROTOCOL,
                "state": "GRANTED",
                "lease_id": j["lease_id"],
                "nonce": j["nonce"],
                "boot_id": j["boot_id"],
                "sequence": 1,
                "expires_at_monotonic_ns": deadline,
                "binding": dict(binding),
                "origin": origin,
                "snapshot": {
                    "digest": snapshot["digest"],
                    "expires_at_monotonic_ns": snapshot_expires,
                    "port": hp.BROKER_PORT,
                    "addresses": rows,
                },
                "policy_digest": policy.digest,
                "manifest_digest": manifest_digest,
                "controller_digest": j["controller_digest"],
            }
        )
        # A broker address already opened for bootstrap needs no second rule.
        # Overlap is allowed by the design and never proves token contact was
        # impossible; the no-token-before-grant rule is the workflow's.
        extra = [
            r
            for r in effective["destinations"][len(bootstrap_rows) :]
            if r["address"] not in bootstrap_addresses
        ]
        new = rule_specs(j["lease_id"], extra, start=len(bootstrap_rows))
        j["planned"] = new
        j["manifest_digest"] = manifest_digest
        _history(j, "VALIDATED", now)
        self.save(j)  # PREPARED transaction: rollback knows the planned rules
        self._checkpoint("grant.validated")
        fw = Firewall(**j["firewall"])
        self._install(j, fw, new)
        j["rules"], j["planned"] = [*j["rules"], *new], []
        self.save(j)
        self._checkpoint("grant.recorded")
        after = self.host.now_ns()
        if deadline - after < hp.GRANT_MIN_REMAINING_NS or j["state"] != "VALIDATED":
            raise refused("deadline.insufficient")
        # Publish only after exact readback and another expiry check.
        self._write_lease_file(j, hp.GRANT_NAME, grant)
        self._checkpoint("grant.published")
        j["grant"] = grant
        j["grant_digest"] = hp.grant_digest(grant)
        _history(j, "GRANTED", after)
        self.save(j)
        self._checkpoint("grant.saved")
        return {
            "protocol": hp.PROTOCOL,
            "op": "grant",
            "lease_id": j["lease_id"],
            "state": "GRANTED",
            "grant_digest": j["grant_digest"],
            "manifest_digest": manifest_digest,
        }

    def refuse(self, request: Mapping[str, Any]) -> dict[str, Any]:
        req = validate_mgmt(request)
        if req["op"] != "refuse":
            raise refused("schema.invalid")
        with self.locked():
            j = self._active_or_other_boot(req["lease_id"])
            self._cleanup_locked(j, "REFUSED", req["category"])
            return self._closed(j)

    def cleanup(self, request: Mapping[str, Any]) -> dict[str, Any]:
        req = validate_mgmt(request)
        if req["op"] != "cleanup":
            raise refused("schema.invalid")
        with self.locked():
            j = self._active_or_other_boot(req["lease_id"])
            final = "CLOSED" if j["state"] == "CONSUMED" else "REFUSED"
            self._cleanup_locked(j, final, "state.invalid")
            return self._closed(j)

    def expire(self, lease: str) -> dict[str, Any]:
        """The rollback timer: ends exactly its own lease, never another one."""
        with self.locked():
            j = self.load()
            if j is None or j["lease_id"] != lease:
                return {"protocol": hp.PROTOCOL, "op": "expire", "state": "ABSENT"}
            self.self_unit = j["units"]["expire"]
            self._cleanup_locked(j, "EXPIRED", "lease.expired")
            return self._closed(j)

    def reconcile(self) -> dict[str, Any]:
        """Boot-time: never launch over orphaned rules or UIDs."""
        with self.locked(create=True):
            j = self.load()
            if j is not None:
                self._cleanup_locked(j, "EXPIRED", "boot.mismatch")
                return self._closed(j)
            cfg = self.host_config()
            rules, _ = self.chain(cfg.firewall)
            if any(owned(r) for r in rules) or self.host.users_with_prefix(USER_PREFIX):
                raise refused("cleanup.drift")
            return {"protocol": hp.PROTOCOL, "op": "reconcile", "state": "ABSENT"}

    def _active_or_other_boot(self, lease: str) -> dict[str, Any]:
        j = self.load()
        if j is None or j["lease_id"] != lease:
            raise refused("lease.unknown")
        return j

    def _closed(self, j: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "protocol": hp.PROTOCOL,
            "op": "cleanup",
            "lease_id": j["lease_id"],
            "state": j["state"],
            "evidence": evidence(j),
        }

    # cleanup ---------------------------------------------------------------

    def _cleanup_locked(self, j: dict[str, Any], final: str, label: str) -> None:
        """Idempotent, resumable cleanup from ANY recorded state."""
        if j["state"] == "CLOSED":
            return
        now = self.host.now_ns()
        if j["outcome"] is None:
            j["outcome"] = {"CLOSED": "COMPLETED"}.get(final, final)
        if j["state"] not in TERMINAL:
            if final != "CLOSED":
                j["category"] = j["category"] or (
                    label if label in hp.CATEGORIES else "internal"
                )
                _history(j, final, now)
        j["closing"] = True
        self.save(j)
        self._checkpoint("cleanup.closing")
        results = {
            "grant_revoked": False,
            "guard_installed": False,
            "processes_absent": False,
            "sockets_absent": False,
            "owned_rules_absent": False,
            "baseline_preserved": False,
            "default_drop_preserved": False,
            "lease_files_absent": False,
            "workspace_absent": False,
            "identity_absent": False,
            "timer_stopped": False,
            "global_conntrack_flushed": False,
        }
        try:
            # The terminal journal state already refuses every consume; the
            # grant file is never authority. Its removal must not delay
            # containment, so a failure here is recorded and checked below.
            try:
                self._remove_lease_dir(j, keep_dir=True)
                results["grant_revoked"] = True
            except (ControllerRefused, OSError):
                results["grant_revoked"] = False
            if self.self_unit != j["units"]["serve"]:
                self.host.stop_unit(j["units"]["serve"])
            fw = Firewall(**j["firewall"])
            identity = self.host.identity(j["user"]) if j["uid"] is not None else None
            if identity is not None and identity != (j["uid"], j["gid"]):
                raise refused("cleanup.identity")
            specs = [*j["rules"], *j["planned"]]
            if identity is not None:
                results["guard_installed"] = self._ensure_guard(j, fw, specs)
                self._checkpoint("cleanup.guard")
                self.host.kill_unit(j["units"]["runner"])
                if j["cgroup"]:
                    self.host.kill_cgroup(j["cgroup"])
                self._await_quiet(j)
                self._checkpoint("cleanup.killed")
            results["processes_absent"] = results["sockets_absent"] = True
            rules, _ = self.chain(fw)
            handles, _guard = classify_owned(
                rules, fw, j["lease_id"], j["uid"] or 0, j["user"], specs, True
            )
            if handles:
                self.host.nft_apply("".join(delete_line(fw, h) + "\n" for h in handles))
            self._checkpoint("cleanup.rules")
            after, _ = self.chain(fw)
            if any(owned(r) for r in after):
                raise refused("cleanup.drift")
            results["owned_rules_absent"] = True
            results["default_drop_preserved"] = True
            if (
                j["baseline_digest"] is not None
                and stable_digest(after) != j["baseline_digest"]
            ):
                raise refused("cleanup.baseline")
            results["baseline_preserved"] = True
            self._remove_lease_dir(j, keep_dir=False)
            results["grant_revoked"] = True
            results["lease_files_absent"] = True
            workspace = Path(j["workspace"])
            self.host.unmount_workspace(workspace)
            with contextlib.suppress(FileNotFoundError):
                os.rmdir(workspace)
            results["workspace_absent"] = not workspace.exists()
            if not results["workspace_absent"]:
                raise refused("cleanup.workspace")
            # The UID stays reserved until process and network cleanup is proven.
            if identity is not None:
                self.host.remove_identity(j["user"])
            results["identity_absent"] = self.host.identity(j["user"]) is None
            self._checkpoint("cleanup.identity")
            if not results["identity_absent"]:
                raise refused("cleanup.identity")
            # Stop the exact timer only after verified closure.
            if j["timer_armed"]:
                self.host.disarm_timer(j["units"]["expire"])
            results["timer_stopped"] = True
        except BaseException as exc:
            j["cleanup"] = results
            j["cleanup_blocked"] = (
                exc.label if isinstance(exc, ControllerRefused) else "cleanup.failed"
            )
            self.save(j)
            raise refused("cleanup.blocked") from None
        j["cleanup"] = results
        j["cleanup_blocked"] = None
        _history(j, "CLOSED", self.host.now_ns())
        self.save(j)
        self._archive(j)

    def _ensure_guard(
        self, j: dict[str, Any], fw: Firewall, specs: list[Mapping[str, Any]]
    ) -> bool:
        try:
            rules, _ = self.chain(fw)
            _, present = classify_owned(
                rules, fw, j["lease_id"], j["uid"], j["user"], specs, True
            )
        except ControllerRefused:
            present = False
        if present:
            return True
        j["guard_planned"] = True
        self.save(j)
        try:
            self.host.nft_apply(guard_line(fw, j["lease_id"], j["uid"]) + "\n")
            rules, _ = self.chain(fw)
            # The guard must be the chain's first rule: ahead of ESTABLISHED.
            return bool(rules) and is_exact_guard(
                rules[0], fw, j["lease_id"], j["uid"], j["user"]
            )
        except ControllerRefused:
            # Containment continues through the cgroup kill; the rules stay.
            return False

    def _await_quiet(self, j: Mapping[str, Any]) -> None:
        deadline = time.monotonic() + CLEANUP_WAIT_S
        while True:
            busy = (
                (j["cgroup"] and self.host.cgroup_pids(j["cgroup"]))
                or uid_processes(j["uid"])
                or uid_sockets(j["uid"])
            )
            if not busy:
                return
            if time.monotonic() >= deadline:
                raise refused("cleanup.processes")
            if j["cgroup"]:
                self.host.kill_cgroup(j["cgroup"])
            # The UID is unique to this lease: any process holding it (even
            # one that left the cgroup) is owned and is killed too.
            for pid in uid_processes(j["uid"]):
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
            time.sleep(0.1)

    def _remove_lease_dir(self, j: Mapping[str, Any], *, keep_dir: bool) -> None:
        try:
            rfd = open_dir(self.paths.run_root)
        except (ControllerRefused, FileNotFoundError):
            return
        try:
            try:
                lfd = os.open(j["lease_id"], _DIR_FLAGS, dir_fd=rfd)
            except FileNotFoundError:
                return
            try:
                _check_dir(lfd, 0o027)
                names = [hp.GRANT_NAME] if keep_dir else os.listdir(lfd)
                for name in names:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(name, dir_fd=lfd)
                os.fsync(lfd)
            finally:
                os.close(lfd)
            if not keep_dir:
                os.rmdir(j["lease_id"], dir_fd=rfd)
        finally:
            os.close(rfd)

    # job socket ------------------------------------------------------------

    def serve(self, lease: str) -> dict[str, Any]:
        """Accept one request per connection until the lease ends or expires."""
        j = self.load()
        if (
            j is None
            or j["lease_id"] != lease
            or j["state"] in TERMINAL
            or j["boot_id"] != self.host.boot_id()
        ):
            raise refused("lease.unknown")
        self.self_unit = j["units"]["serve"]
        directory = self._lease_dir(j)
        os.close(open_dir(directory, forbid=0o027, gid=j["gid"]))
        path = directory / hp.SOCKET_NAME
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            server.bind(str(path))
        finally:
            os.umask(old)
        try:
            os.chown(path, 0, j["gid"], follow_symlinks=False)
            os.chmod(path, hp.SOCKET_MODE)
            server.listen(8)
            server.settimeout(0.5)
            while True:
                current = self.load()
                if (
                    current is None
                    or current["lease_id"] != lease
                    or current["state"] in TERMINAL
                ):
                    break
                if self.host.now_ns() >= current["expires_at_monotonic_ns"]:
                    with self.locked():
                        latest = self.load()
                        if latest is not None and latest["state"] not in TERMINAL:
                            self._cleanup_locked(latest, "EXPIRED", "lease.expired")
                    break
                try:
                    conn, _ = server.accept()
                except TimeoutError:
                    continue
                with conn:
                    self.handle_connection(conn)
        finally:
            server.close()
            with contextlib.suppress(OSError):
                os.unlink(path)
        return {"protocol": hp.PROTOCOL, "op": "serve", "state": "ENDED"}

    def handle_connection(self, conn: socket.socket) -> None:
        reply: dict[str, Any]
        invalidate = False
        try:
            raw = conn.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
            )
            pid, uid, _gid = struct.unpack("3i", raw)
            reply, invalidate = self.handle_request(conn, pid, uid)
        except Exception as exc:
            reply = hp.response("REFUSED", category_of(exc))
            invalidate = True
        with contextlib.suppress(Exception):
            hp.write_frame(conn, reply)
        # Close before any cleanup so the peer sees its response promptly.
        with contextlib.suppress(OSError):
            conn.shutdown(socket.SHUT_RDWR)
        conn.close()
        if invalidate:
            with contextlib.suppress(Exception), self.locked():
                j = self.load()
                if j is not None and j["state"] != "CLOSED":
                    if j["state"] not in TERMINAL:
                        j["category"] = reply.get("category") or "internal"
                        _history(j, "REFUSED", self.host.now_ns())
                    self._cleanup_locked(j, "REFUSED", j["category"])

    def handle_request(
        self, conn: socket.socket, pid: int, uid: int
    ) -> tuple[dict[str, Any], bool]:
        j = self.load()
        if j is None or j["state"] in TERMINAL:
            return hp.response("REFUSED", "lease.unknown"), False
        try:
            peer_ok = uid == j["uid"] and proc_cgroup(pid) == j["cgroup"]
            started = proc_start_time(pid)
        except ControllerRefused:
            peer_ok, started = False, -1
        # Read the bounded request before answering even a refused peer, so
        # the fixed refusal is delivered rather than lost to a reset.
        try:
            request = hp.validate_request(hp.read_frame(conn))
        except hp.ProtocolRefused as exc:
            raise refused("peer.refused" if not peer_ok else exc.label) from None
        if not peer_ok:
            raise refused("peer.refused")
        try:
            lock = self.locked()
            lock.__enter__()
        except ControllerRefused as exc:
            if exc.label == "lock.busy" and request["op"] == "poll":
                # A management operation holds the lock: polling changes
                # nothing, so it may safely wait. Report/consume refuse.
                return hp.response("WAIT"), False
            raise
        try:
            j = self.load()
            if j is None or j["boot_id"] != self.host.boot_id():
                raise refused("boot.mismatch")
            reply, invalidate = transition(
                j, request, (pid, started), self.host.now_ns()
            )
            # The PID must still be the same live process before we commit.
            if proc_start_time(pid) != started or proc_cgroup(pid) != j["cgroup"]:
                raise refused("peer.refused")
            self.save(j)
        finally:
            lock.__exit__(None, None, None)
        return reply, invalidate


def evidence(j: Mapping[str, Any]) -> dict[str, Any]:
    """The reviewed public projection: allowlisted fields only."""
    report = j.get("report") or {}
    grant = j.get("grant") or {}
    binding = grant.get("binding")
    t0 = j.get("t0_monotonic_ns")
    transitions = [
        {
            "state": h["state"],
            "offset_ms": None
            if t0 is None
            else (h["at_monotonic_ns"] - t0) // 1_000_000,
        }
        for h in j.get("history", [])
    ]
    return {
        "schema": EVIDENCE_SCHEMA,
        "protocol": hp.PROTOCOL,
        "lease_id": j["lease_id"],
        "outcome": j.get("outcome"),
        "category": j.get("category"),
        "origin": report.get("origin"),
        "flags": report.get("flags"),
        "binding": dict(binding) if binding else None,
        "policy_digest": j.get("policy_digest"),
        "controller_digest": j.get("controller_digest"),
        "bootstrap_manifest_digest": j.get("bootstrap_manifest_digest"),
        "manifest_digest": j.get("manifest_digest"),
        "snapshot_digest": (grant.get("snapshot") or {}).get("digest"),
        "report_digest": j.get("report_digest"),
        "grant_digest": j.get("grant_digest"),
        "transitions": transitions,
        "cleanup": j.get("cleanup"),
        "cleanup_blocked": j.get("cleanup_blocked"),
        "gate0_accepted": False,
    }


def main(argv: list[str]) -> int:
    os.umask(0o077)
    lease_ops = {"serve", "expire"}
    shape_ok = (len(argv) == 2 and argv[1] in MGMT_STDIN_OPS | {"reconcile"}) or (
        len(argv) == 3
        and argv[1] in lease_ops
        and re.fullmatch(r"[0-9a-f]{32}", argv[2]) is not None
    )
    if os.geteuid() != 0 or not shape_ok:
        print(
            json.dumps(
                {
                    "protocol": hp.PROTOCOL,
                    "status": "REFUSED",
                    "category": "operation.invalid",
                }
            )
        )
        return 2
    op = argv[1]
    controller = Controller()
    try:
        if op in MGMT_STDIN_OPS:
            raw = sys.stdin.buffer.read(MAX_MGMT_INPUT + 1)
            if len(raw) > MAX_MGMT_INPUT:
                raise refused("schema.invalid")
            request = strict_json(raw)
            raw = b""
            if request.get("op") != op:
                raise refused("schema.invalid")
            result = getattr(controller, op)(request)
        elif op in lease_ops:
            result = getattr(controller, op)(argv[2])
        else:
            result = controller.reconcile()
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        label = (
            exc.label
            if isinstance(exc, ControllerRefused | hp.ProtocolRefused)
            else "internal"
        )
        print(
            json.dumps(
                {"protocol": hp.PROTOCOL, "status": "REFUSED", "category": label}
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
