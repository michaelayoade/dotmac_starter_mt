"""Job-side client for the Lane 3 same-job broker handoff.

The approved job reports its canonical broker origin to the root controller over
a protected Unix socket, waits, verifies the root-published grant against values
it holds locally, claims the grant exactly once and returns a
:class:`lane3_github_oidc.PinnedBrokerTarget` for the pinned OIDC transport.

Nothing here requests a token. The job never sends the request URL, path, query,
token or JWT. Every refusal is a :class:`HandoffRefused` with a fixed label (and,
for a controller refusal, a fixed category from the protocol's closed set);
exception text, paths, URLs and values are never carried.

A grant read from the file system is only trusted after a descriptor walk with
no symlinks, root (expected UID) ownership, exact mode, a regular single-link
file and a size cap. That makes a runner-created lookalike detectable; it does
not authenticate a compromised job's own report (see the design's trust
boundary).

Python standard library only; Linux only (``/proc/self/fd`` socket path).
"""

from __future__ import annotations

import dataclasses
import os
import socket
import stat
import sys
import time
from collections.abc import Callable
from typing import Any, Final
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lane3_handoff_protocol as hp
from lane3_broker_origin import OriginRefused, canonical_origin
from lane3_github_oidc import PinnedBrokerTarget

MAX_FILE: Final = hp.MAX_MESSAGE
POLL_INTERVAL_S: Final = 0.25
BOOT_ID_PATH: Final = "/proc/sys/kernel/random/boot_id"

#: Fixed local refusal labels (a controller refusal uses ``handoff.refused``).
LABELS: Final = frozenset(
    {
        "handoff.input",
        "handoff.origin",
        "handoff.files",
        "handoff.challenge",
        "handoff.transport",
        "handoff.refused",
        "handoff.timeout",
        "handoff.grant",
        "handoff.binding",
        "handoff.expired",
        "handoff.consume",
        "handoff.reused",
        "handoff.internal",
    }
)


class HandoffRefused(RuntimeError):
    """Fixed-label refusal. ``category`` is a protocol category or ``None``."""

    def __init__(self, label: str, category: str | None = None) -> None:
        if label not in LABELS:
            label = "handoff.internal"
        if category is not None and category not in hp.CATEGORIES:
            category = "internal"
        super().__init__(label)
        self.label = label
        self.category = category

    def __str__(self) -> str:
        return self.label if self.category is None else f"{self.label}:{self.category}"


@dataclasses.dataclass(frozen=True)
class LocalExpectation:
    """Values the job holds locally; the grant must match all that are given."""

    repository_id: int
    run_id: int
    run_attempt: int
    workflow_sha: str
    starter_commit: str
    job_id: int | None = None
    runner_id: int | None = None
    workflow_blob: str | None = None
    admission_digest: str | None = None
    supplier_digest: str | None = None

    def validate(self) -> None:
        for value in (self.repository_id, self.run_id, self.run_attempt):
            if type(value) is not int or value < 1:
                raise HandoffRefused("handoff.input")
        for number in (self.job_id, self.runner_id):
            if number is not None and (type(number) is not int or number < 1):
                raise HandoffRefused("handoff.input")
        try:
            for text in (self.workflow_sha, self.starter_commit):
                hp.hex40(text)
            if self.workflow_blob is not None:
                hp.hex40(self.workflow_blob)
            for value in (self.admission_digest, self.supplier_digest):
                if value is not None:
                    hp.hex64(value)
        except hp.ProtocolRefused:
            raise HandoffRefused("handoff.input") from None

    def binding_pairs(self) -> list[tuple[str, Any]]:
        pairs: list[tuple[str, Any]] = [
            ("repository_id", self.repository_id),
            ("run_id", self.run_id),
            ("run_attempt", self.run_attempt),
            ("workflow_sha", self.workflow_sha),
            ("starter_commit", self.starter_commit),
        ]
        for key in (
            "job_id",
            "runner_id",
            "workflow_blob",
            "admission_digest",
            "supplier_digest",
        ):
            value = getattr(self, key)
            if value is not None:
                pairs.append((key, value))
        return pairs


def derive_origin(request_url: str) -> str:
    """The canonical origin of the locally held request URL, or refuse.

    Userinfo or an explicit port is refused here, never stripped: the refusal
    is preserved rather than rewritten into a request that would pass.
    """
    if type(request_url) is not str or not request_url:
        raise HandoffRefused("handoff.input")
    try:
        parts = urlsplit(request_url)
        netloc = parts.netloc
        if "@" in netloc or ":" in netloc or not parts.scheme or not netloc:
            raise HandoffRefused("handoff.origin")
        return canonical_origin(parts.scheme + "://" + netloc)
    except HandoffRefused:
        raise
    except (OriginRefused, ValueError):
        raise HandoffRefused("handoff.origin") from None


# -- trusted file access ------------------------------------------------------

_DIR_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _trusted_ancestor(st: os.stat_result, uid: int) -> bool:
    if st.st_uid not in (0, uid) or not stat.S_ISDIR(st.st_mode):
        return False
    writable = st.st_mode & 0o022
    return not writable or bool(st.st_mode & stat.S_ISVTX)


def _open_lease_dir(run_root: str, lease_id: str, uid: int, gid: int | None) -> int:
    """Descriptor walk to the lease directory; no symlink at any component."""
    if not run_root.startswith("/") or "//" in run_root:
        raise HandoffRefused("handoff.files")
    names = [p for p in run_root.split("/") if p]
    if any(p in (".", "..") for p in names):
        raise HandoffRefused("handoff.files")
    fd = os.open("/", _DIR_FLAGS)
    try:
        for index, name in enumerate([*names, lease_id]):
            nxt = os.open(name, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
            st = os.fstat(fd)
            last = index == len(names)
            if last:
                ok = (
                    stat.S_ISDIR(st.st_mode)
                    and st.st_uid == uid
                    and stat.S_IMODE(st.st_mode) == hp.LEASE_DIR_MODE
                    and (gid is None or st.st_gid == gid)
                )
            elif index == len(names) - 1:
                ok = (
                    stat.S_ISDIR(st.st_mode)
                    and st.st_uid == uid
                    and not st.st_mode & 0o022
                )
            else:
                ok = _trusted_ancestor(st, uid)
            if not ok:
                raise HandoffRefused("handoff.files")
        return fd
    except HandoffRefused:
        os.close(fd)
        raise
    except OSError:
        os.close(fd)
        raise HandoffRefused("handoff.files") from None


def _read_regular(dir_fd: int, name: str, uid: int, gid: int | None) -> bytes:
    try:
        fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=dir_fd,
        )
    except OSError:
        raise HandoffRefused("handoff.files") from None
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != uid
            or (gid is not None and st.st_gid != gid)
            or stat.S_IMODE(st.st_mode) != hp.FILE_MODE
            or st.st_nlink != 1
            or not 0 < st.st_size <= MAX_FILE
        ):
            raise HandoffRefused("handoff.files")
        data = os.read(fd, MAX_FILE + 1)
        if len(data) != st.st_size:
            raise HandoffRefused("handoff.files")
        return data
    except OSError:
        raise HandoffRefused("handoff.files") from None
    finally:
        os.close(fd)


def _check_socket(dir_fd: int, uid: int, gid: int | None) -> None:
    try:
        st = os.stat(hp.SOCKET_NAME, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        raise HandoffRefused("handoff.files") from None
    if (
        not stat.S_ISSOCK(st.st_mode)
        or st.st_uid != uid
        or (gid is not None and st.st_gid != gid)
        or stat.S_IMODE(st.st_mode) != hp.SOCKET_MODE
    ):
        raise HandoffRefused("handoff.files")


def _default_boot_id() -> str:
    try:
        with open(BOOT_ID_PATH, encoding="ascii") as handle:
            return handle.read(64).strip().lower()
    except OSError:
        raise HandoffRefused("handoff.challenge") from None


class HandoffClient:
    """One lease, one report, one consume."""

    def __init__(
        self,
        *,
        lease_id: str,
        run_root: str = hp.RUN_ROOT,
        expected_uid: int = 0,
        expected_gid: int | None = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        sleep: Callable[[float], None] = time.sleep,
        boot_id_reader: Callable[[], str] = _default_boot_id,
    ) -> None:
        try:
            self._lease = hp.lease_id(lease_id)
        except hp.ProtocolRefused:
            raise HandoffRefused("handoff.input") from None
        self._run_root = run_root
        self._uid = expected_uid
        self._gid = expected_gid
        self._clock = clock_ns
        self._sleep = sleep
        self._boot_id = boot_id_reader
        self._used = False

    # -- files ---------------------------------------------------------------

    def _read_json(self, name: str) -> dict[str, Any]:
        dir_fd = _open_lease_dir(self._run_root, self._lease, self._uid, self._gid)
        try:
            raw = _read_regular(dir_fd, name, self._uid, self._gid)
        finally:
            os.close(dir_fd)
        try:
            return hp.decode_message(raw)
        except hp.ProtocolRefused:
            raise HandoffRefused("handoff.files") from None

    def read_challenge(self) -> dict[str, Any]:
        try:
            challenge = hp.validate_challenge(self._read_json(hp.CHALLENGE_NAME))
        except hp.ProtocolRefused:
            raise HandoffRefused("handoff.challenge") from None
        if challenge["lease_id"] != self._lease:
            raise HandoffRefused("handoff.challenge")
        if challenge["boot_id"] != self._boot_id():
            raise HandoffRefused("handoff.challenge")
        if self._clock() >= challenge["expires_at_monotonic_ns"]:
            raise HandoffRefused("handoff.expired")
        return challenge

    # -- transport -----------------------------------------------------------

    def _exchange(self, request: dict[str, Any]) -> dict[str, Any]:
        """One request, one connection, one strictly validated response."""
        dir_fd = _open_lease_dir(self._run_root, self._lease, self._uid, self._gid)
        try:
            _check_socket(dir_fd, self._uid, self._gid)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.settimeout(hp.IO_DEADLINE_S)
                sock.connect(f"/proc/self/fd/{dir_fd}/{hp.SOCKET_NAME}")
                hp.write_frame(sock, request)
                reply = hp.read_frame(sock, require_eof=True)
                return hp.validate_response(reply)
            finally:
                sock.close()
        except (hp.ProtocolRefused, OSError):
            raise HandoffRefused("handoff.transport") from None
        finally:
            os.close(dir_fd)

    def _call(self, request: dict[str, Any]) -> dict[str, Any]:
        reply = self._exchange(request)
        if reply["status"] == "REFUSED":
            raise HandoffRefused("handoff.refused", reply["category"])
        return reply

    # -- the handoff ---------------------------------------------------------

    def token_request_started(self) -> None:
        """Persist UNKNOWN before the transport can send credential bytes."""
        self._transport_event("token-started")

    def record_proof(self, outcome: str) -> None:
        """Record a bounded consumer outcome; issuance remains UNKNOWN."""
        self._transport_event("proof", outcome=outcome)

    def _transport_event(self, op: str, **fields: Any) -> None:
        challenge = self.read_challenge()
        request = {
            "protocol": hp.PROTOCOL,
            "op": op,
            "lease_id": self._lease,
            "nonce": challenge["nonce"],
            **fields,
        }
        hp.validate_transport_event(request)
        if self._call(request)["status"] != "CONSUMED":
            raise HandoffRefused("handoff.consume")

    def obtain_from_launch(
        self, *, request_url: str, expectation: LocalExpectation
    ) -> PinnedBrokerTarget:
        """Use root-published original launch time, never obtain-time now.

        Digest comparison here is defense in depth. Independent installation
        and admission qualification belongs to the trusted launcher/controller.
        """
        try:
            launch = hp.validate_launch(self._read_json(hp.LAUNCH_NAME))
        except hp.ProtocolRefused:
            raise HandoffRefused("handoff.challenge") from None
        if launch["lease_id"] != self._lease or launch["boot_id"] != self._boot_id():
            raise HandoffRefused("handoff.binding")
        for key in ("admission_digest", "supplier_digest"):
            if (
                getattr(expectation, key) is None
                or getattr(expectation, key) != launch[key]
            ):
                raise HandoffRefused("handoff.binding")
        if self._clock() >= launch["expires_at_monotonic_ns"]:
            raise HandoffRefused("handoff.expired")
        return self.obtain(
            request_url=request_url,
            expectation=expectation,
            launch_ns=launch["launched_at_monotonic_ns"],
        )

    def obtain(
        self,
        *,
        request_url: str,
        expectation: LocalExpectation,
        launch_ns: int,
    ) -> PinnedBrokerTarget:
        """Report, wait, verify, consume once; return the pinned target."""
        if self._used:
            raise HandoffRefused("handoff.reused")
        self._used = True
        try:
            return self._obtain(request_url, expectation, launch_ns)
        except HandoffRefused:
            raise
        except Exception:
            raise HandoffRefused("handoff.internal") from None

    def _obtain(
        self, request_url: str, expectation: LocalExpectation, launch_ns: int
    ) -> PinnedBrokerTarget:
        expectation.validate()
        if type(launch_ns) is not int:
            raise HandoffRefused("handoff.input")
        origin = derive_origin(request_url)
        challenge = self.read_challenge()
        nonce = challenge["nonce"]
        if not 0 <= self._clock() - launch_ns <= hp.METADATA_DEADLINE_NS:
            raise HandoffRefused("handoff.timeout")
        deadline = min(
            launch_ns + hp.METADATA_DEADLINE_NS, challenge["expires_at_monotonic_ns"]
        )
        reply = self._call(
            {
                "protocol": hp.PROTOCOL,
                "op": "report",
                "lease_id": self._lease,
                "nonce": nonce,
                "origin": origin,
                "flags": {"explicit_port_present": False, "userinfo_present": False},
                "expected": {
                    "run_id": expectation.run_id,
                    "run_attempt": expectation.run_attempt,
                    "workflow_sha": expectation.workflow_sha,
                    "starter_commit": expectation.starter_commit,
                },
            }
        )
        poll = {
            "protocol": hp.PROTOCOL,
            "op": "poll",
            "lease_id": self._lease,
            "nonce": nonce,
        }
        while reply["status"] != "GRANTED":
            if reply["status"] != "WAIT":
                raise HandoffRefused("handoff.grant")
            if self._clock() >= deadline:
                raise HandoffRefused("handoff.timeout")
            self._sleep(POLL_INTERVAL_S)
            if self._clock() >= deadline:
                raise HandoffRefused("handoff.timeout")
            reply = self._call(poll)
        grant = self._verified_grant(challenge, expectation, origin)
        digest = hp.grant_digest(grant)
        done = self._call(
            {
                "protocol": hp.PROTOCOL,
                "op": "consume",
                "lease_id": self._lease,
                "nonce": nonce,
                "grant_digest": digest,
            }
        )
        if done["status"] != "CONSUMED":
            raise HandoffRefused("handoff.consume")
        return self._target(grant)

    def _connection_deadline(self, grant: dict[str, Any]) -> int:
        return min(
            grant["expires_at_monotonic_ns"],
            grant["snapshot"]["expires_at_monotonic_ns"],
        )

    def _verified_grant(
        self,
        challenge: dict[str, Any],
        expectation: LocalExpectation,
        origin: str,
    ) -> dict[str, Any]:
        try:
            grant = hp.validate_grant(self._read_json(hp.GRANT_NAME))
        except hp.ProtocolRefused:
            raise HandoffRefused("handoff.grant") from None
        if (
            grant["lease_id"] != self._lease
            or grant["nonce"] != challenge["nonce"]
            or grant["boot_id"] != challenge["boot_id"]
            or grant["boot_id"] != self._boot_id()
        ):
            raise HandoffRefused("handoff.binding")
        for key, value in expectation.binding_pairs():
            if grant["binding"].get(key) != value:
                raise HandoffRefused("handoff.binding")
        if grant["origin"] != origin:
            raise HandoffRefused("handoff.binding")
        if grant["expires_at_monotonic_ns"] > challenge["expires_at_monotonic_ns"]:
            raise HandoffRefused("handoff.binding")
        if self._clock() >= self._connection_deadline(grant):
            raise HandoffRefused("handoff.expired")
        return grant

    def _target(self, grant: dict[str, Any]) -> PinnedBrokerTarget:
        deadline = self._connection_deadline(grant)
        if self._clock() >= deadline:
            raise HandoffRefused("handoff.expired")
        row = grant["snapshot"]["addresses"][0]
        family = socket.AF_INET if row["family"] == 4 else socket.AF_INET6
        return PinnedBrokerTarget(
            origin=grant["origin"],
            family=family,
            address=row["address"],
            deadline_ns=deadline,
        )
