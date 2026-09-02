"""The host half of an Observability promotion: stage, activate, reload, look.

Observability ADR-0010 splits a promotion in two. That repository owns the
DECISION — the state machine, the refusals, the rollback decision, the six
conditions and the receipt — and owns no host effect. Every host effect is a
method on ``promote.PromotionFacility``, a Protocol it declares and does not
implement. This module is the implementation.

The shipped executor could not do any of it. Its "switch" is ``docker compose
up -d --force-recreate`` against a re-rendered Compose file
(``providers/compose_host.py``): nothing stages a directory, nothing swaps a
pointer, nothing reads a pointer back, there is no transport, the only reload
primitive is ``nginx -s reload`` and nothing anywhere queries Prometheus or
Alertmanager. A promotion driven by it could not roll back to anything it had
verified itself.

## The three properties this module exists to have

**A previous pointer is READ, never supplied.** ``engine/plan.py`` takes
``previous_image`` from its caller, so a rollback restores what the caller
BELIEVED was running. :meth:`ObservabilityPromotionFacility.read_previous_pointer`
reads the symlink off the host, and every shape that is not "a symlink
resolving to a release directory" is a refusal rather than a ``None``: a
regular file where the pointer belongs means activation was never atomic here,
and a dangling symlink means the rollback target is already gone. ``None`` is
returned for exactly one shape — no pointer at all — which is a host that has
never held a release.

**Activation and restoration are OBSERVED.** Both swap the pointer and then
re-read it, and both refuse when what came back is not what was written. A
command that returned is not a host that changed.

**A reload is not an activation.** Prometheus and Alertmanager are reloaded
separately, over ``--web.enable-lifecycle`` rather than by recreating the
container (which would throw the scrape window away), and each reload is then
CHECKED against the evaluator's own
``*_config_last_reload_success_timestamp_seconds``. A ``200`` from ``/-/reload``
says the request was accepted; only a success timestamp later than the instant
we posted says the process took the bytes. When the two disagree this module
raises :class:`ReloadNotObserved` — the promotion fails at ``RELOADED`` and the
control plane rolls back, which is the correct outcome for a host running a
configuration nobody can name.

## What this module deliberately does not decide

Nothing here judges whether a promotion succeeded. Health thresholds, target
expectations, the verdict and the six conditions are the control plane's,
because they are statements about THAT control plane. This module performs what
it was told and reports what it observed, including reporting that it observed
nothing.

That is why every fact it cannot obtain itself arrives through a seam and
defaults to an honest absence rather than a convenient value. A canary with no
:data:`ReceiverWitness` reports ``delivered: false`` with no evidence
reference, which the verifier refuses — rather than reporting a delivery on the
strength of Alertmanager's outbound ``200``. A probe with no
:data:`SurfaceProber` reports ``inconclusive``, not ``refused``, because an
unplugged cable and a shut port look identical from here.

## Why this returns a document rather than a ``LiveState``

``PromotionFacility.observe`` is annotated ``-> LiveState``, a dataclass in
``dotmac_observability``. This facility cannot import the control plane it
serves — a universal facility that imports one product is not universal, and
ADR-0070 is explicit that the Foundation carries zero runtime dependencies. So
:meth:`observe` and :meth:`rollback` return the
``observability-live-observation.v1`` DOCUMENT, which is the actual contract
(``contracts/live-observation.schema.json``), and the control plane's own
``live_verify.live_state`` types it. The adapter is one call on the side that
owns the type. This is recorded as a contradiction against the ADR's stated
signature in this package's CHANGELOG.

## The ordering problem, and why a manifest is not a claim

``render.tree_digest`` hashes ``path\\0contents\\0`` in the RENDERER'S order,
which is not alphabetical. A directory read-back recovers the paths and the
bytes and cannot recover that order, so the digest a read-back computes could
never equal the digest a release was accepted with — and
``restored_digest``/``previous_digest`` are compared with ``!=``.

:meth:`stage` therefore writes a ``ReleaseTreeManifest.v1`` recording the
render order, and it is stored OUTSIDE the release directory (a file inside it
would read back as a path the renderer does not produce, which the verifier
compares as unexpected). The manifest supplies ORDER and nothing else: every
path and every byte is still read back off the host, the manifest's path set is
compared with the directory walk and a disagreement in either direction is a
refusal. A missing manifest yields a ``None`` digest, never a guessed one.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import posixpath
import shlex
import subprocess  # nosec B404 -- argv list, shell=False; see LocalTransport
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final, Protocol

from .errors import DeploymentError, PreconditionFailed

__all__ = [
    "ACCEPTED_RELEASE_SCHEMA",
    "ACTIVATION_POINTER_SHAPES",
    "OBSERVATION_SCHEMA",
    "RELEASE_MANIFEST_SCHEMA",
    "ActivationNotObserved",
    "AcceptedRelease",
    "CanaryPlan",
    "CommandOutcome",
    "Evaluator",
    "HostTools",
    "HostTransport",
    "HttpClient",
    "HttpResponse",
    "LocalTransport",
    "ObservabilityPromotionFacility",
    "ObservationRequest",
    "PreviousPointerUnreadable",
    "ProbeObservation",
    "ProbeSlot",
    "PromotionContext",
    "ReceiverWitness",
    "ReleaseAlreadyStaged",
    "ReleaseLayout",
    "ReloadNotObserved",
    "ReloadRefused",
    "RollbackTargetMissing",
    "RouteProbe",
    "SshTransport",
    "StagedRelease",
    "SurfaceProber",
    "TransportBytesMismatch",
    "TreeReadBackIncomplete",
    "UnknownTarget",
    "UrllibHttpClient",
    "file_digest",
    "parse_metric",
    "tree_digest",
]

#: The document contract this facility produces. Owned by
#: ``dotmac_observability/contracts/live-observation.schema.json``; named here
#: so a schema rename is a mismatch a reader can see rather than a silently
#: unvalidated document.
OBSERVATION_SCHEMA: Final = "observability-live-observation.v1"

#: The ordering record written beside — never inside — a release directory.
RELEASE_MANIFEST_SCHEMA: Final = "ReleaseTreeManifest.v1"

#: What the accepted-release record is called. Written by :meth:`accept` so the
#: NEXT promotion has a ``previous_digest`` to compare a restored tree against.
ACCEPTED_RELEASE_SCHEMA: Final = "AcceptedRelease.v1"

#: The four shapes the release pointer can be found in, and the one that means
#: "no previous release". Written out because three of them used to be
#: indistinguishable from the fourth: a caller-supplied ``previous_image``
#: reported ``None`` for a broken pointer exactly as it did for a fresh host.
ACTIVATION_POINTER_SHAPES: Final = (
    "absent",  # no previous release — the only legitimate null
    "symlink",  # the expected shape
    "not-a-symlink",  # activation here was never atomic
    "dangling",  # the rollback target is already gone
)


# ── refusals ────────────────────────────────────────────────────────────────


class PreviousPointerUnreadable(DeploymentError):
    """The release pointer exists in a shape a rollback could not use.

    Deliberately not a ``None``. A pointer that is a regular file, or a symlink
    into nothing, is a host whose rollback target cannot be established, and
    reporting that as "no previous release" is how a wrong belief becomes a
    wrong rollback without anybody being told.
    """


class ReleaseAlreadyStaged(PreconditionFailed):
    """The release directory already exists.

    Nothing has been written, so the caller may pick another release id and
    re-run. A release directory is immutable; writing into an existing one
    would make the release name cover two sets of bytes.
    """


class TransportBytesMismatch(DeploymentError):
    """What the host holds is not what was sent, so nothing was activated."""


class TreeReadBackIncomplete(DeploymentError):
    """The read-back could not enumerate the active release completely.

    Raised rather than returning a short ``tree``: an empty or partial array
    reports exactly as a clean one does, and the verifier would compare the
    missing paths as removed files.
    """


class ActivationNotObserved(DeploymentError):
    """The pointer was swapped and does not read back as the new release."""


class ReloadRefused(DeploymentError):
    """The evaluator refused the reload request outright."""


class ReloadNotObserved(DeploymentError):
    """The reload returned success and the evaluator did not take the bytes.

    The distinction this class exists for: ``POST /-/reload`` answering ``200``
    is the request being accepted. The evaluator's own
    ``*_config_last_reload_successful`` and
    ``*_config_last_reload_success_timestamp_seconds`` are the process saying
    it parsed and adopted a configuration, and only a success timestamp AFTER
    the moment we posted says it adopted THIS one. Treating the ``200`` as the
    answer is how a host runs a configuration nobody can name.
    """


class RollbackTargetMissing(DeploymentError):
    """There is no release to roll back to, or the one named is not on the host."""


class UnknownTarget(PreconditionFailed):
    """The named target is not the host this facility can reach.

    Rule 17 makes the target a human's declaration, and a facility that ignored
    it would make the name decorative — a promotion authorized for one host and
    executed against whichever host the transport happened to point at.
    """


# ── the transport seam ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """One command's result. ``stdout`` is bytes because file content is."""

    exit_code: int
    stdout: bytes = b""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")


class HostTransport(Protocol):
    """Run one argv on the target host, optionally feeding it bytes.

    ADR-0010 requirement 3 names the ``runner:`` seam as the insertion point
    for a remote transport, and this is that seam with the one addition a
    file transport needs: ``stdin``. ``providers/compose_host.Runner`` cannot
    carry bytes to a command, which is why this is a second Protocol rather
    than a widening of the shipped one — widening it would change ``0.3.0a3``.
    """

    def run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes | None = None,
        timeout_seconds: int,
    ) -> CommandOutcome: ...


@dataclass(frozen=True, slots=True)
class LocalTransport:
    """``subprocess``, argv list, ``shell=False``. The host is this machine."""

    def run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes | None = None,
        timeout_seconds: int,
    ) -> CommandOutcome:
        try:
            # argv LIST, shell=False -- the two properties B603/S603 check.
            completed = subprocess.run(  # nosec B603  # noqa: S603
                list(argv),
                shell=False,
                input=stdin,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return CommandOutcome(124, b"", f"timed out after {timeout_seconds}s")
        except OSError as exc:
            return CommandOutcome(127, b"", str(exc))
        stderr = completed.stderr or b""
        return CommandOutcome(
            completed.returncode,
            completed.stdout or b"",
            stderr.decode("utf-8", errors="replace"),
        )


@dataclass(frozen=True, slots=True)
class SshTransport:
    """Wrap another transport, running each argv on a remote host over SSH.

    ``ssh`` hands the remote side a STRING and the remote side runs a shell over
    it, so quoting is this class's job and is done with :func:`shlex.join`
    rather than by joining on spaces. A path containing a space is otherwise two
    arguments on the far end, and the file it would have written lands
    somewhere nobody looks.

    Every part of the invocation is configuration: the binary, the options and
    the destination. Nothing here hardcodes a host, a user, a port or a key —
    ``dotmac-deployment-control`` owns fleet intent and this owns one release on
    one host.
    """

    destination: str
    inner: HostTransport = field(default_factory=LocalTransport)
    ssh_bin: str = "ssh"
    ssh_options: tuple[str, ...] = ("-o", "BatchMode=yes")

    def run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes | None = None,
        timeout_seconds: int,
    ) -> CommandOutcome:
        remote = shlex.join(list(argv))
        wrapped = [self.ssh_bin, *self.ssh_options, self.destination, remote]
        return self.inner.run(wrapped, stdin=stdin, timeout_seconds=timeout_seconds)


# ── the HTTP seam ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes = b""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class HttpClient(Protocol):
    """The evaluator API seam. Stdlib by default; no ``requests``/``httpx``."""

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        timeout_seconds: float,
    ) -> HttpResponse: ...


@dataclass(frozen=True, slots=True)
class UrllibHttpClient:
    """``urllib``, and a non-2xx is a RESPONSE rather than an exception.

    A transport-level failure is reported as status ``0`` for the same reason:
    the caller decides what an unreachable evaluator means, and a caller that
    has to catch three exception types to learn "it did not answer" eventually
    catches two.
    """

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        timeout_seconds: float,
    ) -> HttpResponse:
        # The URL is built by this module from a configured evaluator base and
        # a constant path; no caller-supplied scheme reaches it, which is what
        # B310/S310 exist to catch.
        request = urllib.request.Request(  # nosec B310  # noqa: S310
            url, data=body, method=method
        )
        try:
            with urllib.request.urlopen(  # nosec B310  # noqa: S310
                request, timeout=timeout_seconds
            ) as response:
                status = int(getattr(response, "status", 200))
                return HttpResponse(status, response.read())
        except urllib.error.HTTPError as exc:  # an answer, just not a 2xx
            return HttpResponse(int(exc.code), exc.read())
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            return HttpResponse(0, str(exc).encode("utf-8"))


# ── configuration ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ReleaseLayout:
    """Where releases, their ordering manifests and the pointer live.

    The manifest directory is a SIBLING of the releases directory, not a file
    inside a release. A release directory holds exactly the rendered tree,
    because the verifier compares a path it finds there that the renderer does
    not produce as unexpected — and it would be right to.
    """

    root: str = "/srv/observability"
    releases: str = "releases"
    manifests: str = "manifests"
    staging: str = ".staging"
    pointer: str = "current"
    accepted: str = "accepted.json"

    def releases_dir(self) -> str:
        return posixpath.join(self.root, self.releases)

    def release_dir(self, release: str) -> str:
        return posixpath.join(self.releases_dir(), release)

    def staging_dir(self, release: str) -> str:
        return posixpath.join(self.root, self.staging, release)

    def manifest_path(self, release: str) -> str:
        return posixpath.join(self.root, self.manifests, f"{release}.json")

    def pointer_path(self) -> str:
        return posixpath.join(self.root, self.pointer)

    def accepted_path(self) -> str:
        return posixpath.join(self.root, self.accepted)

    def pointer_target(self, release: str) -> str:
        """The pointer's value: RELATIVE to the root.

        A relative target keeps the release tree movable and keeps the pointer
        meaningful inside a bind mount whose path differs from the host's.
        """
        return posixpath.join(self.releases, release)


@dataclass(frozen=True, slots=True)
class HostTools:
    """Every binary this facility invokes, as configuration.

    Named rather than embedded so a host with a different toolchain is a
    constructor argument instead of a fork. ``pointer_swap_flags`` is here for
    a specific reason: ``ln -sfn`` over an existing symlink-to-directory is an
    unlink followed by a symlink, so there is an instant with no pointer at
    all. ``mv -T`` is a ``rename(2)``, which is atomic. ``-T`` is GNU; a host
    whose ``mv`` spells it differently supplies different flags rather than
    losing the atomicity.
    """

    mkdir: str = "mkdir"
    rm: str = "rm"
    mv: str = "mv"
    ln: str = "ln"
    find: str = "find"
    readlink: str = "readlink"
    base64: str = "base64"
    tee: str = "tee"
    chmod: str = "chmod"
    test: str = "test"
    amtool: str = "amtool"
    pointer_swap_flags: tuple[str, ...] = ("-T",)
    immutable_mode: str = "a-w"


@dataclass(frozen=True, slots=True)
class Evaluator:
    """One reloadable evaluator, and the metrics that prove it reloaded.

    The metric names are configuration because they are the evaluator's
    vocabulary, not this facility's, and a version that renames one must be a
    constructor argument rather than a patch here.
    """

    name: str
    base_url: str
    reload_path: str = "/-/reload"
    metrics_path: str = "/metrics"
    reload_success_metric: str = ""
    reload_timestamp_metric: str = ""

    @classmethod
    def prometheus(cls, base_url: str) -> Evaluator:
        return cls(
            name="prometheus",
            base_url=base_url,
            reload_success_metric="prometheus_config_last_reload_successful",
            reload_timestamp_metric=(
                "prometheus_config_last_reload_success_timestamp_seconds"
            ),
        )

    @classmethod
    def alertmanager(cls, base_url: str) -> Evaluator:
        return cls(
            name="alertmanager",
            base_url=base_url,
            reload_success_metric="alertmanager_config_last_reload_successful",
            reload_timestamp_metric=(
                "alertmanager_config_last_reload_success_timestamp_seconds"
            ),
        )

    def url(self, path: str) -> str:
        return urllib.parse.urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/"))


# ── what the control plane asks to have read back ───────────────────────────


@dataclass(frozen=True, slots=True)
class ProbeSlot:
    """One surface, one address family, and what the desired state expects.

    ``expectation`` is carried rather than derived because it is a statement
    about the desired state, which this facility does not hold. It is also the
    field the control plane's own ``ObservationRequest`` does not yet carry —
    see this package's CHANGELOG.
    """

    surface: str
    family: str
    expectation: str


@dataclass(frozen=True, slots=True)
class ProbeObservation:
    """What a prober found, including that it found nothing conclusive."""

    outcome: str = "inconclusive"
    chain: str = "unobserved"
    control_outcome: str = "inconclusive"
    control_evidence_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RouteProbe:
    """A declared route id and the labels that should land on its receiver."""

    id: str
    labels: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class CanaryPlan:
    """The alert to fire, and the receiver a witness must find it at."""

    alertname: str
    receiver: str
    labels: tuple[tuple[str, str], ...] = ()
    settle_seconds: float = 2.0


#: ``(receiver, alertname) -> evidence reference``, or ``None`` when the
#: delivery was not seen. Supplied by the product, never by this facility: what
#: "seen at the receiver" means is a Slack thread on one fleet and a mail log on
#: another, and a facility that guessed would be reporting Alertmanager's
#: outbound ``200`` as a human having been reached.
ReceiverWitness = Callable[[str, str], str | None]

#: ``ProbeSlot -> ProbeObservation``. Supplied by the product because a probe's
#: answer depends on the VANTAGE it ran from, and a probe from inside an
#: allowlist proves nothing (``exposure.accept_public_exposure_evidence``).
SurfaceProber = Callable[[ProbeSlot], ProbeObservation]


@dataclass(frozen=True, slots=True)
class ObservationRequest:
    """Everything one read-back needs.

    Structurally a superset of the control plane's request of the same name:
    ``release``, ``paths``, ``integrity_counters`` and ``probe_slots`` are its
    fields, and the rest are what the schema requires and that request does not
    carry. :meth:`ObservabilityPromotionFacility.observe` accepts either — it
    reads the four shared fields off whatever it is handed and fills the rest
    from the facility's standing :class:`PromotionContext`.
    """

    release: str
    paths: tuple[str, ...] = ()
    integrity_counters: tuple[str, ...] = ()
    probe_slots: tuple[ProbeSlot, ...] = ()
    route_probes: tuple[RouteProbe, ...] = ()
    canary: CanaryPlan | None = None
    previous: str | None = None


@dataclass(frozen=True, slots=True)
class PromotionContext:
    """The facts one promotion is bound to, fixed for the facility's lifetime.

    ``host_target_id`` is the name a human wrote in the authorizing request.
    Every method that takes a ``target`` compares against it and refuses a
    disagreement, so an authorization for one host cannot execute against
    another because the transport happened to point elsewhere.
    """

    environment: str
    host_target_id: str
    probe_slots: tuple[ProbeSlot, ...] = ()
    route_probes: tuple[RouteProbe, ...] = ()
    canary: CanaryPlan | None = None
    integrity_counters: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StagedRelease:
    """The release created, what preceded it, and the digest of what was sent.

    ``previous`` is READ off the host before activation. It is the only
    rollback target this facility will restore, and ``None`` means one thing
    only: the pointer was absent, so this host has never held a release.
    """

    current: str
    previous: str | None
    tree_digest: str
    previous_shape: str = "absent"


@dataclass(frozen=True, slots=True)
class AcceptedRelease:
    """What :meth:`accept` records, so the next promotion has a baseline."""

    release: str
    tree_digest: str
    accepted_at: str


# ── helpers ─────────────────────────────────────────────────────────────────


def tree_digest(entries: Sequence[tuple[str, bytes]]) -> str:
    """``sha256`` over ``path\\0contents\\0``, in the order given.

    Byte-identical to ``dotmac_observability.render.tree_digest`` for the same
    tree in the same order, and that identity is the point: a digest computed
    differently here would disagree with the one a release was accepted with,
    and the disagreement would surface as a rollback that "restored the wrong
    bytes" when nothing was wrong but the hash function.
    """
    digest = hashlib.sha256()
    for path, contents in entries:
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(contents)
        digest.update(b"\0")
    return digest.hexdigest()


def file_digest(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _canonical_json(document: Mapping[str, Any]) -> bytes:
    """Sorted keys, no spurious whitespace — a document that re-derives."""
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def parse_metric(text: str, name: str) -> float | None:
    """One unlabelled Prometheus-exposition sample, or ``None`` if absent.

    Absent returns ``None`` rather than ``0.0``. ``0.0`` is a real value for
    ``*_config_last_reload_successful`` and means the reload FAILED, so
    defaulting a missing sample to it would report a failure that was really a
    metric this build does not export — and defaulting it to ``1.0`` would
    report a success nobody observed.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        head, _, value = stripped.rpartition(" ")
        if head.strip() != name:
            continue
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _require_release_name(release: str) -> str:
    """A release id is a single path segment, and nothing else.

    Checked because the id reaches a path join and an argv. A ``..`` segment
    would let a caller name a directory outside the releases root, and a slash
    would let one name a subdirectory of an existing immutable release.
    """
    unusable = (
        not release
        or "/" in release
        or release in {".", ".."}
        or release.startswith("-")
    )
    if unusable:
        raise PreconditionFailed(
            f"{release!r} is not a usable release id: one path segment, "
            "not '.' or '..', and not starting with '-'.",
        )
    return release


class ObservabilityPromotionFacility:
    """Stage, activate, reload, read back, roll back — and observe each one.

    Constructed per promotion, because it is bound to one named target and one
    set of evaluator endpoints. Everything it invokes and every address it
    reaches is a constructor argument with a documented default; nothing here
    hardcodes a host, a port, a path or a binary.
    """

    def __init__(
        self,
        *,
        transport: HostTransport,
        context: PromotionContext,
        prometheus: Evaluator,
        alertmanager: Evaluator,
        layout: ReleaseLayout | None = None,
        tools: HostTools | None = None,
        http: HttpClient | None = None,
        clock: Callable[[], float] = time.time,
        timestamp: Callable[[], str] | None = None,
        release_id: Callable[[str], str] | None = None,
        receiver_witness: ReceiverWitness | None = None,
        surface_prober: SurfaceProber | None = None,
        sleep: Callable[[float], None] = time.sleep,
        command_timeout_seconds: int = 60,
        transfer_timeout_seconds: int = 300,
        http_timeout_seconds: float = 15.0,
    ) -> None:
        self._transport = transport
        self._context = context
        self._prometheus = prometheus
        self._alertmanager = alertmanager
        self._layout = layout or ReleaseLayout()
        self._tools = tools or HostTools()
        self._http = http or UrllibHttpClient()
        self._clock = clock
        self._timestamp = timestamp or _utc_now
        self._release_id = release_id or _default_release_id
        self._witness = receiver_witness
        self._prober = surface_prober
        self._sleep = sleep
        self._command_timeout = command_timeout_seconds
        self._transfer_timeout = transfer_timeout_seconds
        self._http_timeout = http_timeout_seconds
        #: What :meth:`stage` read off the host, PRESERVED for every later
        #: read-back on this facility. Reading the previous pointer is only
        #: half of requirement 2; carrying it forward is the other half, and
        #: nothing else can. The control plane's own ``ObservationRequest``
        #: has no field for it, and a null ``release.previous`` on a promotion
        #: that is not the first is `RECEIPT-NO-ROLLBACK-TARGET` — the receipt
        #: is refused. So the pointer this facility READ is the pointer this
        #: facility reports, rather than one the caller re-supplies.
        self._staged: StagedRelease | None = None

    # ── target binding ──────────────────────────────────────────────────

    def _require_target(self, target: str) -> str:
        if target != self._context.host_target_id:
            raise UnknownTarget(
                f"this facility is bound to {self._context.host_target_id!r} and was "
                f"asked to act on {target!r}. The target is named by a human in the "
                "authorizing request and is not something a transport may reinterpret.",
            )
        return target

    # ── raw host access ─────────────────────────────────────────────────

    def _run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes | None = None,
        timeout_seconds: int | None = None,
    ) -> CommandOutcome:
        return self._transport.run(
            list(argv),
            stdin=stdin,
            timeout_seconds=timeout_seconds or self._command_timeout,
        )

    def _exists(self, path: str, flag: str = "-e") -> bool:
        return self._run([self._tools.test, flag, path]).ok

    def _read_bytes(self, path: str) -> bytes:
        """Fetch one file's bytes, base64 over the wire.

        Base64 rather than raw because the remote side of an SSH channel is a
        shell and a raw byte stream through it is at the mercy of whatever the
        far end decides is a line ending. A decode failure is a transport
        failure and is raised as one, not silently returned as empty bytes.
        """
        result = self._run(
            [self._tools.base64, path], timeout_seconds=self._transfer_timeout
        )
        if not result.ok:
            raise TransportBytesMismatch(
                f"could not read {path} back from the host: "
                f"{result.stderr.strip() or f'exit {result.exit_code}'}"
            )
        try:
            return base64.b64decode(b"".join(result.stdout.split()), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise TransportBytesMismatch(
                f"the host's encoding of {path} did not decode: {exc}"
            ) from exc

    def _write_bytes(self, path: str, contents: bytes) -> None:
        parent = posixpath.dirname(path)
        made = self._run([self._tools.mkdir, "-p", parent])
        if not made.ok:
            raise TransportBytesMismatch(
                f"could not create {parent}: "
                f"{made.stderr.strip() or f'exit {made.exit_code}'}"
            )
        written = self._run(
            [self._tools.tee, path],
            stdin=contents,
            timeout_seconds=self._transfer_timeout,
        )
        if not written.ok:
            raise TransportBytesMismatch(
                f"could not write {path}: "
                f"{written.stderr.strip() or f'exit {written.exit_code}'}"
            )

    def _list_files(self, directory: str) -> tuple[str, ...]:
        """Every regular file under ``directory``, relative, sorted.

        ``-print0`` rather than ``-print``: a path containing a newline would
        otherwise split into two entries, and the two halves would compare as
        an unexpected path and a missing one.
        """
        result = self._run(
            [self._tools.find, directory, "-type", "f", "-print0"],
            timeout_seconds=self._transfer_timeout,
        )
        if not result.ok:
            raise TreeReadBackIncomplete(
                f"could not enumerate {directory}: "
                f"{result.stderr.strip() or f'exit {result.exit_code}'}"
            )
        prefix = directory.rstrip("/") + "/"
        found: list[str] = []
        for raw in result.stdout.split(b"\0"):
            if not raw:
                continue
            absolute = raw.decode("utf-8")
            if not absolute.startswith(prefix):
                raise TreeReadBackIncomplete(
                    f"{absolute} was listed under {directory} and is not inside it"
                )
            found.append(absolute[len(prefix) :])
        return tuple(sorted(found))

    # ── requirement 2: the previous-pointer READER ──────────────────────

    def read_previous_pointer(self, target: str) -> tuple[str | None, str]:
        """What the host says is running, and the SHAPE that answer came in.

        Returns ``(release, shape)`` where ``shape`` is one of
        :data:`ACTIVATION_POINTER_SHAPES`. Only ``"absent"`` yields a ``None``
        release; ``"not-a-symlink"`` and ``"dangling"`` raise, because both are
        hosts whose rollback target cannot be established and reporting either
        as "no previous release" is exactly the silent wrong answer a
        caller-supplied ``previous_image`` gives today.
        """
        self._require_target(target)
        pointer = self._layout.pointer_path()
        if not self._exists(pointer, "-L"):
            if self._exists(pointer, "-e"):
                raise PreviousPointerUnreadable(
                    f"{pointer} exists and is not a symlink. Activation on this host "
                    "was not a pointer swap, so there is no previous release to read "
                    "and no atomic way to restore one."
                )
            return None, "absent"
        link = self._run([self._tools.readlink, pointer])
        value = link.text().strip()
        if not link.ok or not value:
            raise PreviousPointerUnreadable(
                f"{pointer} is a symlink whose target could not be read: "
                f"{link.stderr.strip() or f'exit {link.exit_code}'}"
            )
        release = posixpath.basename(value.rstrip("/"))
        if not self._exists(self._layout.release_dir(release), "-d"):
            raise PreviousPointerUnreadable(
                f"{pointer} points at {value!r}, and "
                f"{self._layout.release_dir(release)} is not a directory. The rollback "
                "target this host would restore is already gone."
            )
        return release, "symlink"

    # ── requirement 1 + 3: immutable staging over an exact-byte transport ──

    def stage(self, tree: Sequence[tuple[str, str]], *, target: str) -> StagedRelease:
        """Write the whole tree to a NEW release directory and activate it.

        The order is the guarantee. The previous pointer is read first, so the
        rollback target is established before anything on the host changes.
        The tree is written to a staging directory that is not the release
        directory, and every file is then READ BACK and compared byte for byte
        against what was sent — a mismatch refuses here, with the staging
        directory removed and the pointer untouched, so a corrupted transfer
        can never be activated. Only then does the tree become the immutable
        release directory, and only then is the pointer swapped.
        """
        self._require_target(target)
        entries = tuple((path, text.encode("utf-8")) for path, text in tree)
        if not entries:
            raise PreconditionFailed(
                "an empty tree was handed to stage(). A release directory holding "
                "nothing is not a release, and activating one would unmount every "
                "configuration file the evaluators read.",
            )
        digest = tree_digest(entries)
        release = _require_release_name(self._release_id(digest))
        previous, shape = self.read_previous_pointer(target)

        release_dir = self._layout.release_dir(release)
        if self._exists(release_dir):
            raise ReleaseAlreadyStaged(
                f"{release_dir} already exists. A release directory is immutable, so "
                "writing into this one would make the name cover two sets of bytes.",
            )

        staging = self._layout.staging_dir(release)
        self._run([self._tools.rm, "-rf", staging])
        try:
            for path, contents in entries:
                self._write_bytes(posixpath.join(staging, path), contents)
            self._verify_transport(staging, entries)
            self._write_bytes(
                self._layout.manifest_path(release),
                _canonical_json(
                    {
                        "schema": RELEASE_MANIFEST_SCHEMA,
                        "release": release,
                        "paths": [path for path, _ in entries],
                        "entries": {
                            path: file_digest(contents) for path, contents in entries
                        },
                        "tree_digest": digest,
                    }
                ),
            )
            self._run(
                [self._tools.mkdir, "-p", posixpath.dirname(release_dir.rstrip("/"))]
            )
            moved = self._run([self._tools.mv, staging, release_dir])
            if not moved.ok:
                raise TransportBytesMismatch(
                    f"could not move the verified tree into {release_dir}: "
                    f"{moved.stderr.strip() or f'exit {moved.exit_code}'}"
                )
        except Exception:
            self._run([self._tools.rm, "-rf", staging])
            raise
        self._run([self._tools.chmod, "-R", self._tools.immutable_mode, release_dir])
        self._activate(release)
        self._staged = StagedRelease(
            current=release,
            previous=previous,
            tree_digest=digest,
            previous_shape=shape,
        )
        return self._staged

    def _verify_transport(
        self, directory: str, entries: Sequence[tuple[str, bytes]]
    ) -> None:
        """Read every staged file back and compare it with what was sent.

        The comparison is over BYTES fetched from the host and hashed here, not
        over a digest the host computed. A remote ``sha256sum`` would make the
        host the authority on whether the host received the right file, which
        is the one question it cannot be asked.

        The path set is compared in both directions, so a stale file left in
        the staging directory is a refusal rather than an extra mount.
        """
        expected = dict(entries)
        found = set(self._list_files(directory))
        missing = sorted(set(expected) - found)
        unexpected = sorted(found - set(expected))
        if missing or unexpected:
            raise TransportBytesMismatch(
                "the staged tree is not the tree that was sent: "
                f"missing {missing or 'none'}, unexpected {unexpected or 'none'}. "
                "Nothing has been activated."
            )
        for path, contents in entries:
            landed = self._read_bytes(posixpath.join(directory, path))
            if landed != contents:
                raise TransportBytesMismatch(
                    f"{path} read back as {file_digest(landed)[:12]} and was sent as "
                    f"{file_digest(contents)[:12]}. Nothing has been activated."
                )

    # ── requirement 1: atomic activation, then observed ─────────────────

    def _activate(self, release: str) -> None:
        """Swap the pointer with a ``rename(2)``, then read it back.

        ``ln -s`` into a temporary name followed by ``mv -T`` over the pointer
        is a rename, which is atomic: a reader either resolves the old release
        or the new one and never resolves nothing. ``ln -sfn`` is an unlink
        followed by a symlink, which has a window where the mount source does
        not exist — and a single-file bind mount taken during that window binds
        to the wrong inode for the life of the container.
        """
        pointer = self._layout.pointer_path()
        temporary = f"{pointer}.staging.{release}"
        self._run([self._tools.rm, "-f", temporary])
        linked = self._run(
            [self._tools.ln, "-s", self._layout.pointer_target(release), temporary]
        )
        if not linked.ok:
            raise ActivationNotObserved(
                f"could not create the replacement pointer {temporary}: "
                f"{linked.stderr.strip() or f'exit {linked.exit_code}'}"
            )
        swapped = self._run(
            [self._tools.mv, *self._tools.pointer_swap_flags, temporary, pointer]
        )
        if not swapped.ok:
            self._run([self._tools.rm, "-f", temporary])
            raise ActivationNotObserved(
                f"could not swap {pointer}: "
                f"{swapped.stderr.strip() or f'exit {swapped.exit_code}'}"
            )
        active, _ = self.read_previous_pointer(self._context.host_target_id)
        if active != release:
            raise ActivationNotObserved(
                f"{pointer} was swapped to {release} and reads back as "
                f"{active or 'nothing'}. A command that returned is not a host that "
                "changed."
            )

    # ── requirements 4: reload, and prove the process took the bytes ────

    def reload(self, *, target: str, release: str) -> None:
        """Reload Prometheus and Alertmanager, and check each one took it.

        Each evaluator is reloaded over its lifecycle endpoint rather than by
        recreating its container, which would discard the scrape window the
        verification is about to read. Both are reloaded even if the first
        fails, so an operator learns about both faults from one run — but the
        first failure is what is raised.
        """
        self._require_target(target)
        _require_release_name(release)
        failures: list[DeploymentError] = []
        for evaluator in (self._prometheus, self._alertmanager):
            try:
                self._reload_one(evaluator)
            except DeploymentError as error:
                failures.append(error)
        if failures:
            raise failures[0]

    def _reload_one(self, evaluator: Evaluator) -> None:
        posted_at = self._clock()
        response = self._http.request(
            "POST",
            evaluator.url(evaluator.reload_path),
            timeout_seconds=self._http_timeout,
        )
        if not response.ok:
            raise ReloadRefused(
                f"{evaluator.name} refused the reload: HTTP {response.status}. "
                "A lifecycle endpoint that answers non-2xx has not reloaded, and a "
                "reload endpoint that is not enabled answers 405."
            )
        metrics = self._http.request(
            "GET",
            evaluator.url(evaluator.metrics_path),
            timeout_seconds=self._http_timeout,
        )
        if not metrics.ok:
            raise ReloadNotObserved(
                f"{evaluator.name} accepted the reload and its metrics endpoint "
                f"answered HTTP {metrics.status}, so nothing observed whether the "
                "process took the new configuration."
            )
        text = metrics.body.decode("utf-8", errors="replace")
        successful = parse_metric(text, evaluator.reload_success_metric)
        timestamp = parse_metric(text, evaluator.reload_timestamp_metric)
        if successful is None or timestamp is None:
            raise ReloadNotObserved(
                f"{evaluator.name} accepted the reload and does not export "
                f"{evaluator.reload_success_metric} / "
                f"{evaluator.reload_timestamp_metric}, so the reload cannot be "
                "confirmed. An unexported metric is not a successful reload."
            )
        if successful != 1.0:
            raise ReloadNotObserved(
                f"{evaluator.name} accepted the reload and reports "
                f"{evaluator.reload_success_metric}={successful:g}. The request "
                "succeeded and the configuration did not."
            )
        if timestamp < posted_at:
            raise ReloadNotObserved(
                f"{evaluator.name} accepted the reload and its last SUCCESSFUL "
                f"reload is older than the request ({timestamp:.0f} < "
                f"{posted_at:.0f}). The 200 is the request being accepted; this "
                "timestamp is the process adopting a configuration, and it adopted "
                "an earlier one."
            )

    # ── requirement 5: the complete read-back ───────────────────────────

    def observe(self, *, target: str, request: Any) -> dict[str, Any]:
        """Read the running control plane back as one contract document."""
        self._require_target(target)
        return self._observe(self._merge(request), rollback=None)

    def _merge(self, request: Any) -> ObservationRequest:
        """Accept either request shape, and fill what the caller cannot carry.

        The control plane's ``ObservationRequest`` carries ``release``,
        ``paths``, ``integrity_counters`` and ``probe_slots``, and the schema
        needs an expectation per probe, a route list, a canary plan, an
        environment and a host id besides. Those come from the standing
        :class:`PromotionContext`. A probe slot the context has no expectation
        for is a refusal rather than a guessed ``refused``, because a guessed
        expectation manufactures a pass.
        """
        if isinstance(request, ObservationRequest):
            merged = request
        else:
            merged = ObservationRequest(
                release=str(request.release),
                paths=tuple(getattr(request, "paths", ())),
                integrity_counters=tuple(getattr(request, "integrity_counters", ())),
                probe_slots=self._expect(getattr(request, "probe_slots", ())),
            )
        preserved = self._staged.previous if self._staged is not None else None
        return replace(
            merged,
            previous=merged.previous if merged.previous is not None else preserved,
            probe_slots=merged.probe_slots or self._context.probe_slots,
            route_probes=merged.route_probes or self._context.route_probes,
            canary=merged.canary or self._context.canary,
            integrity_counters=(
                merged.integrity_counters or self._context.integrity_counters
            ),
        )

    def _expect(self, slots: Sequence[Any]) -> tuple[ProbeSlot, ...]:
        declared = {
            (slot.surface, slot.family): slot for slot in self._context.probe_slots
        }
        resolved: list[ProbeSlot] = []
        for slot in slots:
            if isinstance(slot, ProbeSlot):
                resolved.append(slot)
                continue
            surface, family = slot
            known = declared.get((surface, family))
            if known is None:
                raise PreconditionFailed(
                    f"no expectation is declared for surface {surface!r} on "
                    f"{family}. The expectation is derived from the desired state and "
                    "this facility does not hold it; guessing one would manufacture a "
                    "pass for a surface nobody described.",
                )
            resolved.append(known)
        return tuple(resolved)

    def _observe(
        self, request: ObservationRequest, *, rollback: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        current, _ = self.read_previous_pointer(self._context.host_target_id)
        if current is None:
            raise TreeReadBackIncomplete(
                "there is no active release pointer, so nothing could be read back."
            )
        entries, _order = self._read_active_tree(current)
        document: dict[str, Any] = {
            "schema_version": OBSERVATION_SCHEMA,
            "observed_at": self._timestamp(),
            "environment": self._context.environment,
            "host_target_id": self._context.host_target_id,
            "release": {"current": current, "previous": request.previous},
            "tree": [
                {"path": path, "sha256": file_digest(contents)}
                for path, contents in sorted(entries.items())
            ],
            "targets": self._read_targets(),
            "rules": self._read_rules(),
            "routes": self._read_routes(request.route_probes, current),
            "integrity": self._read_integrity(request.integrity_counters),
            "canary": self._exercise_canary(request.canary),
            "probes": self._read_probes(request.probe_slots),
        }
        if rollback is not None:
            document["rollback"] = dict(rollback)
        return document

    def _read_active_tree(
        self, release: str
    ) -> tuple[dict[str, bytes], tuple[str, ...] | None]:
        """Every file under the active release, plus the render order if known.

        Complete by construction: the directory is walked, every file listed is
        fetched, and a fetch that fails raises. The manifest supplies ORDER
        only, and its path list is compared with the walk in both directions —
        so a manifest that disagrees with the host is a refusal, never a
        substitute for looking.
        """
        directory = self._layout.release_dir(release)
        paths = self._list_files(directory)
        if not paths:
            raise TreeReadBackIncomplete(
                f"{directory} listed no files. An empty tree reports exactly as a "
                "clean one does, so it is refused rather than returned."
            )
        entries = {
            path: self._read_bytes(posixpath.join(directory, path)) for path in paths
        }
        order = self._read_manifest_order(release, set(paths))
        return entries, order

    def _read_manifest_order(
        self, release: str, paths: set[str]
    ) -> tuple[str, ...] | None:
        manifest_path = self._layout.manifest_path(release)
        if not self._exists(manifest_path, "-f"):
            return None
        try:
            document = json.loads(self._read_bytes(manifest_path).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        order = tuple(str(path) for path in document.get("paths", ()))
        if set(order) != paths:
            raise TreeReadBackIncomplete(
                f"the ordering manifest for {release} lists "
                f"{sorted(set(order) - paths) or 'nothing'} the host does not have and "
                f"omits {sorted(paths - set(order)) or 'nothing'} it does. A "
                "manifest records order; it never substitutes for the directory."
            )
        return order

    def release_tree_digest(self, release: str) -> str | None:
        """The digest of what is on the host, in the render order, or ``None``.

        ``None`` when no ordering manifest survives, because the digest a
        release was accepted with is order-dependent and a digest computed in
        a different order would compare as a rollback that restored the wrong
        bytes. An unknown digest is reported as unknown.
        """
        entries, order = self._read_active_tree(release)
        if order is None:
            return None
        return tree_digest([(path, entries[path]) for path in order])

    def _read_targets(self) -> list[dict[str, str]]:
        response = self._http.request(
            "GET",
            self._prometheus.url("/api/v1/targets"),
            timeout_seconds=self._http_timeout,
        )
        if not response.ok:
            return []
        payload = response.json()
        active = payload.get("data", {}).get("activeTargets", [])
        return [
            {
                "job": str(entry.get("labels", {}).get("job", "")),
                "health": _health(str(entry.get("health", "unknown")), ("up", "down")),
            }
            for entry in active
        ]

    def _read_rules(self) -> list[dict[str, str]]:
        response = self._http.request(
            "GET",
            self._prometheus.url("/api/v1/rules"),
            timeout_seconds=self._http_timeout,
        )
        if not response.ok:
            return []
        payload = response.json()
        rows: list[dict[str, str]] = []
        for group in payload.get("data", {}).get("groups", []):
            for rule in group.get("rules", []):
                rows.append(
                    {
                        "group": str(group.get("name", "")),
                        "name": str(rule.get("name", "")),
                        "health": _health(
                            str(rule.get("health", "unknown")), ("ok", "err")
                        ),
                    }
                )
        return rows

    def _read_routes(
        self, probes: Sequence[RouteProbe], release: str
    ) -> list[dict[str, str]]:
        """Resolve each declared route against the ACTIVE alertmanager.yml.

        ``amtool config routes test`` is used rather than an HTTP call because
        Alertmanager exposes no routing-resolution endpoint, and it is pointed
        at the file under the active release rather than at a local copy: the
        question is which receiver the RUNNING configuration lands these labels
        on, and a local file cannot answer it.
        """
        rows: list[dict[str, str]] = []
        config = posixpath.join(
            self._layout.release_dir(release), "alertmanager/alertmanager.yml"
        )
        for probe in probes:
            argv = [
                self._tools.amtool,
                "config",
                "routes",
                "test",
                f"--config.file={config}",
                *[f"{key}={value}" for key, value in probe.labels],
            ]
            result = self._run(argv)
            receiver = result.text().strip().splitlines()
            rows.append(
                {
                    "id": probe.id,
                    "receiver": receiver[-1].strip() if result.ok and receiver else "",
                }
            )
        return [row for row in rows if row["receiver"]]

    def _read_integrity(self, counters: Sequence[str]) -> dict[str, Any]:
        """The named counter and ``process_start_time_seconds``, in ONE read.

        One read, because the pair is what makes a reset detectable: a counter
        read now and a start time read a minute later cannot rule out a restart
        between them, which is the exact shape
        ``INTEGRITY-PROCESS-RESTARTED`` exists to catch.
        """
        if not counters or not counters[0]:
            raise PreconditionFailed(
                "no ingestion counter was named for this read-back. The contract's "
                "`integrity` block is required and its `counter` field names the "
                "series a baseline is compared against; emitting an unnamed counter "
                "would let a comparison against a DIFFERENT counter pass silently."
            )
        if len(counters) > 1:
            # The control plane derives this list from every gate's integrity
            # predicate, so it can legitimately name several counters — and the
            # contract's `integrity` block holds exactly ONE. Reading the first
            # and filing the document would verify one counter and report a
            # complete read-back, which is the subset-reported-as-the-whole
            # failure. Refuse loudly instead: the fix is a contract that carries
            # a list, and it is not this facility's to make.
            raise PreconditionFailed(
                f"{len(counters)} ingestion counters were named "
                f"({', '.join(counters)}) and `observability-live-observation.v1`"
                "'s `integrity` block holds exactly one. Reading the first would "
                "leave the rest unverified while the document read as complete."
            )
        name = counters[0]
        metrics = self._http.request(
            "GET",
            self._prometheus.url(self._prometheus.metrics_path),
            timeout_seconds=self._http_timeout,
        )
        text = metrics.body.decode("utf-8", errors="replace") if metrics.ok else ""
        value = parse_metric(text, name) if name else None
        started = parse_metric(text, "process_start_time_seconds")
        return {
            "counter": name,
            "value": int(value) if value is not None else 0,
            "process_start_time": float(started) if started is not None else 0.0,
        }

    def _exercise_canary(self, plan: CanaryPlan | None) -> dict[str, Any]:
        """Fire, look for it, ask the witness, resolve, look again.

        ``delivered`` is answered by the injected :data:`ReceiverWitness` and by
        nothing else. Alertmanager returning ``200`` from ``/api/v2/alerts`` is
        this facility's own POST being accepted, and even a successful outbound
        notification is Alertmanager's attempt succeeding — neither is evidence
        a human can be reached. With no witness the answer is ``false`` with no
        evidence reference, which the verifier refuses, and that refusal is
        correct.
        """
        if plan is None:
            raise PreconditionFailed(
                "no canary was planned for this read-back. The contract's `canary` "
                "block is required and names the receiver the delivery must be "
                "witnessed at; a block with an empty receiver is not a document that "
                "validates, and one with a fabricated receiver is worse."
            )
        labels = {"alertname": plan.alertname, **dict(plan.labels)}
        url = self._alertmanager.url("/api/v2/alerts")
        fired = self._http.request(
            "POST",
            url,
            body=json.dumps([{"labels": labels}]).encode("utf-8"),
            timeout_seconds=self._http_timeout,
        ).ok
        self._sleep(plan.settle_seconds)
        seen = fired and self._alert_present(plan.alertname)
        evidence = (
            self._witness(plan.receiver, plan.alertname) if self._witness else None
        )
        self._http.request(
            "POST",
            url,
            body=json.dumps([{"labels": labels, "endsAt": self._timestamp()}]).encode(
                "utf-8"
            ),
            timeout_seconds=self._http_timeout,
        )
        self._sleep(plan.settle_seconds)
        recovered = seen and not self._alert_present(plan.alertname)
        document: dict[str, Any] = {
            "fired": bool(seen),
            "delivered": bool(evidence),
            "recovered": bool(recovered),
            "receiver": plan.receiver,
        }
        if evidence:
            document["receiver_evidence_ref"] = evidence
        return document

    def _alert_present(self, alertname: str) -> bool:
        response = self._http.request(
            "GET",
            self._alertmanager.url("/api/v2/alerts"),
            timeout_seconds=self._http_timeout,
        )
        if not response.ok:
            return False
        return any(
            entry.get("labels", {}).get("alertname") == alertname
            for entry in response.json()
        )

    def _read_probes(self, slots: Sequence[ProbeSlot]) -> list[dict[str, Any]]:
        """One entry per declared surface PER FAMILY, control nested inside it.

        The slot list is what the control plane derived from the exposure
        policy, so a ``dual_stack`` surface arrives as two slots and this
        produces two entries. Nothing here shortens the list, and nothing here
        invents an outcome: without a :data:`SurfaceProber` every slot reports
        ``inconclusive`` with an ``inconclusive`` control, because a refusal
        with no working control proves the prober ran and not that access is
        shut.
        """
        rows: list[dict[str, Any]] = []
        for slot in slots:
            found = self._prober(slot) if self._prober else ProbeObservation()
            control: dict[str, Any] = {"outcome": found.control_outcome}
            if found.control_evidence_ref:
                control["evidence_ref"] = found.control_evidence_ref
            rows.append(
                {
                    "surface": slot.surface,
                    "family": slot.family,
                    "chain": found.chain,
                    "expectation": slot.expectation,
                    "outcome": found.outcome,
                    "control": control,
                }
            )
        return rows

    # ── requirements 6: exact rollback, and the read-back that proves it ──

    def rollback(self, *, target: str, release: str) -> dict[str, Any]:
        """Restore ``release``, then read the host back and report what it found.

        The return value is the whole point. A ``rollback`` that returned
        ``None`` would let "the command did not raise" stand in for "the host
        recovered", and the control plane records exactly that as
        ``ROLLBACK-UNOBSERVED``.

        ``restored_release`` is RE-READ from the pointer rather than echoed
        from the argument, so a swap that silently did not take reports the
        release the host is actually running. ``restored_digest`` is computed
        from the bytes fetched back off the host, so a pointer restored without
        the bytes behind it — the failure the field exists to catch — shows as
        a digest that does not match what the previous release was accepted
        with.
        """
        self._require_target(target)
        if not release:
            raise RollbackTargetMissing(
                "rollback was asked to restore nothing. There is no previous release "
                "pointer to return to, which is the state the staging refusal exists "
                "to prevent reaching."
            )
        _require_release_name(release)
        if not self._exists(self._layout.release_dir(release), "-d"):
            raise RollbackTargetMissing(
                f"{self._layout.release_dir(release)} is not on the host, so the "
                "release this rollback names cannot be restored."
            )
        self._activate(release)
        restored, _ = self.read_previous_pointer(target)
        reload_ok = True
        try:
            self.reload(target=target, release=release)
        except DeploymentError:
            # A restored pointer whose configuration the evaluators never took
            # is not a recovered host. Recorded rather than raised, because the
            # read-back below is the most useful thing an operator can be
            # handed at this point and raising would throw it away.
            reload_ok = False
        digest = self.release_tree_digest(restored) if restored else None
        succeeded = bool(reload_ok and restored == release and digest is not None)
        # `previous` for the RESTORED host is the release that was just
        # rolled away from — the pointer that was live immediately before this
        # activation, which is exactly what the field means. Leaving it null
        # would file a rolled-back receipt carrying `RECEIPT-NO-ROLLBACK-TARGET`
        # for a promotion whose rollback target was never in doubt.
        request = replace(
            self._standing_request(release),
            release=release,
            previous=self._staged.current if self._staged is not None else None,
        )
        return self._observe(
            request,
            rollback={
                "exercised": True,
                "restored_release": restored,
                "restored_digest": digest,
                "succeeded": succeeded,
            },
        )

    def _standing_request(self, release: str) -> ObservationRequest:
        return ObservationRequest(
            release=release,
            probe_slots=self._context.probe_slots,
            route_probes=self._context.route_probes,
            canary=self._context.canary,
            integrity_counters=self._context.integrity_counters,
        )

    # ── acceptance ──────────────────────────────────────────────────────

    def accept(self, *, target: str, release: str) -> AcceptedRelease:
        """Record this release as the baseline the next promotion compares to.

        Refuses when the pointer does not read back as ``release``: accepting a
        release the host is not running would write a baseline digest for bytes
        nobody is serving, and every later rollback would be measured against
        it.

        The record carries the tree digest because the verifier's condition 6
        compares a restored tree against "the digest the previous release was
        accepted with", and until this is written down that value exists only
        in the memory of a process that has exited.
        """
        self._require_target(target)
        _require_release_name(release)
        active, _ = self.read_previous_pointer(target)
        if active != release:
            raise ActivationNotObserved(
                f"accept was asked to record {release} and the host is running "
                f"{active or 'nothing'}."
            )
        digest = self.release_tree_digest(release)
        if digest is None:
            raise TreeReadBackIncomplete(
                f"no ordering manifest survives for {release}, so the digest this "
                "release is accepted with cannot be computed, and the next rollback "
                "would have nothing to be compared against."
            )
        record = AcceptedRelease(
            release=release, tree_digest=digest, accepted_at=self._timestamp()
        )
        self._write_bytes(
            self._layout.accepted_path(),
            _canonical_json(
                {
                    "schema": ACCEPTED_RELEASE_SCHEMA,
                    "release": record.release,
                    "tree_digest": record.tree_digest,
                    "accepted_at": record.accepted_at,
                }
            ),
        )
        return record


def _health(value: str, known: tuple[str, ...]) -> str:
    """Map an evaluator's health word onto the contract's enum.

    Anything unrecognised becomes ``unknown`` rather than being passed through:
    the schema's enum is closed, and a document carrying a word outside it
    fails validation at the consumer, which turns a target that reported an
    unfamiliar state into a promotion that cannot file a receipt at all.
    """
    return value if value in known else "unknown"


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_release_id(digest: str) -> str:
    """``<utc timestamp>-<tree digest prefix>``.

    Time first so the directory listing sorts chronologically, digest second so
    the name says what the release holds. Time is part of it deliberately:
    re-promoting an identical tree is a legitimate operation and a purely
    content-derived name would collide with the immutability refusal.
    """
    return f"{_utc_now().replace(':', '').replace('-', '')}-{digest[:12]}"
