"""Stage, activate, reload, read back, roll back — every one against a fake host.

Observability ADR-0010's split exists so that these properties are ordinary
unit tests. A promotion facility exercisable only against a live host is
exercised rarely, and the properties it guarantees are the ones that must not
be wrong.

The double here is a FAKE HOST rather than a mocked facility: it interprets the
actual argv the facility builds — ``test``, ``mkdir``, ``tee``, ``base64``,
``find``, ``readlink``, ``ln``, ``mv``, ``rm``, ``chmod`` — so a test that
passes is a test in which the real commands were the right ones. Mocking the
facility's own methods would prove only that the test knows what it wrote.

Every capability has a positive test AND the failure it exists to catch, because
a check that has never refused anything is a check nobody has evidence for:

* previous pointer — read off the host, and the three broken shapes refused;
* exact-byte transport — a mangled byte refuses BEFORE activation;
* atomic activation — a ``rename``, and a swap that does not read back refuses;
* reload — a ``200`` with a stale success timestamp refuses;
* read-back — complete, and an empty listing refuses rather than reading clean;
* rollback — restores the exact previous release AND returns an observation of
  the restored host, which is the fact a ``None`` return could never carry.
"""

from __future__ import annotations

import base64
import hashlib
import json
import posixpath
from collections.abc import Sequence
from typing import Any

import pytest
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.observability_promotion import (
    OBSERVATION_SCHEMA,
    ActivationNotObserved,
    CanaryPlan,
    CommandOutcome,
    Evaluator,
    HttpResponse,
    ObservabilityPromotionFacility,
    ObservationRequest,
    PreviousPointerUnreadable,
    ProbeObservation,
    ProbeSlot,
    PromotionContext,
    ReleaseAlreadyStaged,
    ReleaseLayout,
    ReloadNotObserved,
    ReloadRefused,
    RollbackTargetMissing,
    RouteProbe,
    SshTransport,
    TransportBytesMismatch,
    TreeReadBackIncomplete,
    UnknownTarget,
    tree_digest,
)

TARGET = "observer-abuja"
ROOT = "/srv/observability"

#: A tree in the RENDERER'S order, which is deliberately not alphabetical —
#: `docker-compose.yml` sorts first and renders last. That is the whole reason
#: an ordering manifest exists, so the fixture has to reproduce it.
TREE: tuple[tuple[str, str], ...] = (
    ("prometheus/prometheus.yml", "global:\n  scrape_interval: 15s\n"),
    ("alertmanager/alertmanager.yml", "route:\n  receiver: fleet\n"),
    ("docker-compose.yml", "services:\n  prometheus: {}\n"),
)

SECOND_TREE: tuple[tuple[str, str], ...] = (
    ("prometheus/prometheus.yml", "global:\n  scrape_interval: 30s\n"),
    ("alertmanager/alertmanager.yml", "route:\n  receiver: fleet\n"),
    ("docker-compose.yml", "services:\n  prometheus: {}\n"),
)


def reference_tree_digest(tree: Sequence[tuple[str, str]]) -> str:
    """``dotmac_observability.render.tree_digest``, restated independently.

    Written out rather than imported (this package must not depend on the
    control plane) and rather than calling the facility's own helper, because
    a test comparing a function with itself proves nothing. If the two ever
    disagree, a rollback's ``restored_digest`` stops matching the digest its
    release was accepted with, and the failure reads as "restored the wrong
    bytes" when nothing is wrong but the hash.
    """
    digest = hashlib.sha256()
    for path, text in tree:
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(text.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


class FakeHost:
    """An in-memory host that interprets the facility's real argv."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.links: dict[str, str] = {}
        self.dirs: set[str] = {ROOT}
        self.modes: list[tuple[str, str]] = []
        self.calls: list[list[str]] = []
        #: Paths whose bytes get one byte flipped on write — a transfer that
        #: succeeded as far as every exit code is concerned.
        self.corrupt: set[str] = set()
        #: Drop the pointer swap on the floor while reporting success.
        self.swallow_swap = False
        self.route_receiver = "fleet-oncall"

    # ── helpers ─────────────────────────────────────────────────────────
    def is_dir(self, path: str) -> bool:
        path = path.rstrip("/")
        if path in self.dirs:
            return True
        return any(name.startswith(path + "/") for name in self.files)

    def exists(self, path: str) -> bool:
        return path in self.files or path in self.links or self.is_dir(path)

    def seed_release(self, release: str, tree: Sequence[tuple[str, str]]) -> None:
        """Put a release and its manifest on the host without going through stage."""
        directory = posixpath.join(ROOT, "releases", release)
        for path, text in tree:
            self.files[posixpath.join(directory, path)] = text.encode("utf-8")
        self.files[posixpath.join(ROOT, "manifests", f"{release}.json")] = json.dumps(
            {
                "schema": "ReleaseTreeManifest.v1",
                "release": release,
                "paths": [path for path, _ in tree],
                "tree_digest": reference_tree_digest(tree),
            },
            sort_keys=True,
        ).encode("utf-8")
        self.links[posixpath.join(ROOT, "current")] = f"releases/{release}"

    # ── the transport seam ──────────────────────────────────────────────
    def run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes | None = None,
        timeout_seconds: int,
    ) -> CommandOutcome:
        self.calls.append(list(argv))
        head, rest = argv[0], list(argv[1:])
        handler = getattr(self, f"_cmd_{head}", None)
        if handler is None:
            return CommandOutcome(127, b"", f"{head}: not found")
        return handler(rest, stdin)

    def _cmd_test(self, args: list[str], _stdin: bytes | None) -> CommandOutcome:
        flag, path = args
        answers = {
            "-e": self.exists(path),
            "-L": path in self.links,
            "-d": self.is_dir(path),
            "-f": path in self.files,
        }
        return CommandOutcome(0 if answers[flag] else 1)

    def _cmd_mkdir(self, args: list[str], _stdin: bytes | None) -> CommandOutcome:
        self.dirs.add(args[-1].rstrip("/"))
        return CommandOutcome(0)

    def _cmd_tee(self, args: list[str], stdin: bytes | None) -> CommandOutcome:
        path = args[0]
        payload = stdin or b""
        if path in self.corrupt or posixpath.basename(path) in self.corrupt:
            payload = payload + b"\n"
        self.files[path] = payload
        return CommandOutcome(0, payload)

    def _cmd_base64(self, args: list[str], _stdin: bytes | None) -> CommandOutcome:
        path = args[0]
        if path not in self.files:
            return CommandOutcome(1, b"", f"{path}: No such file")
        return CommandOutcome(0, base64.b64encode(self.files[path]) + b"\n")

    def _cmd_find(self, args: list[str], _stdin: bytes | None) -> CommandOutcome:
        directory = args[0].rstrip("/")
        if not self.is_dir(directory):
            return CommandOutcome(1, b"", f"{directory}: No such file or directory")
        prefix = directory + "/"
        found = [n for n in sorted(self.files) if n.startswith(prefix)]
        return CommandOutcome(0, b"\0".join(name.encode() for name in found) + b"\0")

    def _cmd_readlink(self, args: list[str], _stdin: bytes | None) -> CommandOutcome:
        path = args[0]
        if path not in self.links:
            return CommandOutcome(1, b"", f"{path}: Invalid argument")
        return CommandOutcome(0, self.links[path].encode() + b"\n")

    def _cmd_ln(self, args: list[str], _stdin: bytes | None) -> CommandOutcome:
        target, name = args[-2], args[-1]
        self.links[name] = target
        return CommandOutcome(0)

    def _cmd_mv(self, args: list[str], _stdin: bytes | None) -> CommandOutcome:
        source, destination = args[-2], args[-1]
        if source in self.links:
            if self.swallow_swap:
                self.links.pop(source)
                return CommandOutcome(0)
            self.files.pop(destination, None)
            self.links[destination] = self.links.pop(source)
            return CommandOutcome(0)
        if not self.is_dir(source):
            return CommandOutcome(1, b"", f"{source}: No such file or directory")
        prefix = source.rstrip("/") + "/"
        for name in [n for n in self.files if n.startswith(prefix)]:
            self.files[destination.rstrip("/") + "/" + name[len(prefix) :]] = (
                self.files.pop(name)
            )
        self.dirs.discard(source.rstrip("/"))
        self.dirs.add(destination.rstrip("/"))
        return CommandOutcome(0)

    def _cmd_rm(self, args: list[str], _stdin: bytes | None) -> CommandOutcome:
        path = args[-1].rstrip("/")
        self.links.pop(path, None)
        self.files.pop(path, None)
        for name in [n for n in self.files if n.startswith(path + "/")]:
            self.files.pop(name)
        self.dirs.discard(path)
        return CommandOutcome(0)

    def _cmd_chmod(self, args: list[str], _stdin: bytes | None) -> CommandOutcome:
        self.modes.append((args[-2], args[-1]))
        return CommandOutcome(0)

    def _cmd_amtool(self, args: list[str], _stdin: bytes | None) -> CommandOutcome:
        del args
        return CommandOutcome(0, f"{self.route_receiver}\n".encode())


class FakeEvaluators:
    """Prometheus and Alertmanager, as scriptable HTTP."""

    def __init__(self, *, now: float = 1_000.0) -> None:
        self.now = now
        self.reload_status = 200
        self.metrics_status = 200
        self.reload_successful = "1"
        self.reload_timestamp = now
        self.ingestion = 41
        self.process_start_time = 900.0
        self.export_reload_metrics = True
        self.targets = [{"labels": {"job": "prometheus"}, "health": "up"}]
        self.rules = [
            {"name": "meta", "rules": [{"name": "GateStale", "health": "ok"}]}
        ]
        self.alerts: list[dict[str, Any]] = []
        self.reloaded: list[str] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        timeout_seconds: float,
    ) -> HttpResponse:
        del timeout_seconds
        if url.endswith("/-/reload"):
            self.reloaded.append("prometheus" if ":9090" in url else "alertmanager")
            return HttpResponse(self.reload_status)
        if url.endswith("/metrics"):
            return HttpResponse(self.metrics_status, self._metrics(url).encode())
        if url.endswith("/api/v1/targets"):
            return HttpResponse(
                200, json.dumps({"data": {"activeTargets": self.targets}}).encode()
            )
        if url.endswith("/api/v1/rules"):
            return HttpResponse(
                200, json.dumps({"data": {"groups": self.rules}}).encode()
            )
        if url.endswith("/api/v2/alerts"):
            if method == "POST":
                payload = json.loads((body or b"[]").decode())
                for entry in payload:
                    if "endsAt" in entry:
                        self.alerts = [
                            held
                            for held in self.alerts
                            if held["labels"] != entry["labels"]
                        ]
                    else:
                        self.alerts.append({"labels": entry["labels"]})
                return HttpResponse(200)
            return HttpResponse(200, json.dumps(self.alerts).encode())
        return HttpResponse(404)

    def _metrics(self, url: str) -> str:
        prefix = "prometheus" if ":9090" in url else "alertmanager"
        lines = [f"process_start_time_seconds {self.process_start_time}"]
        if self.export_reload_metrics:
            lines += [
                f"{prefix}_config_last_reload_successful {self.reload_successful}",
                f"{prefix}_config_last_reload_success_timestamp_seconds "
                f"{self.reload_timestamp}",
            ]
        if prefix == "prometheus":
            lines.append(f"prometheus_tsdb_out_of_order_samples_total {self.ingestion}")
        return "\n".join(lines) + "\n"


CANARY = CanaryPlan(
    alertname="PromotionCanary", receiver="fleet-oncall", settle_seconds=0
)
SLOTS = (
    ProbeSlot(surface="prometheus", family="ipv4", expectation="refused"),
    ProbeSlot(surface="prometheus", family="ipv6", expectation="refused"),
)
CONTEXT = PromotionContext(
    environment="production",
    host_target_id=TARGET,
    probe_slots=SLOTS,
    route_probes=(RouteProbe(id="fleet", labels=(("severity", "page"),)),),
    canary=CANARY,
    integrity_counters=("prometheus_tsdb_out_of_order_samples_total",),
)


def build(
    host: FakeHost,
    evaluators: FakeEvaluators,
    *,
    release_id: str = "r-new",
    context: PromotionContext = CONTEXT,
    **kwargs: Any,
) -> ObservabilityPromotionFacility:
    return ObservabilityPromotionFacility(
        transport=host,
        context=context,
        prometheus=Evaluator.prometheus("http://127.0.0.1:9090"),
        alertmanager=Evaluator.alertmanager("http://127.0.0.1:9093"),
        layout=ReleaseLayout(root=ROOT),
        http=evaluators,
        clock=lambda: evaluators.now,
        timestamp=lambda: "2026-09-02T00:00:00Z",
        release_id=lambda _digest: release_id,
        sleep=lambda _seconds: None,
        **kwargs,
    )


@pytest.fixture
def host() -> FakeHost:
    return FakeHost()


@pytest.fixture
def evaluators() -> FakeEvaluators:
    return FakeEvaluators()


# ── capability 1: immutable release-directory staging ───────────────────────


def test_the_whole_tree_lands_in_one_new_release_directory(host, evaluators):
    staged = build(host, evaluators).stage(TREE, target=TARGET)

    directory = f"{ROOT}/releases/{staged.current}"
    assert sorted(
        name[len(directory) + 1 :]
        for name in host.files
        if name.startswith(directory + "/")
    ) == sorted(path for path, _ in TREE)
    assert staged.tree_digest == reference_tree_digest(TREE)


def test_the_release_directory_is_made_unwritable(host, evaluators):
    """Immutable is a mode on the host, not an adjective in a docstring."""
    staged = build(host, evaluators).stage(TREE, target=TARGET)
    assert ("a-w", f"{ROOT}/releases/{staged.current}") in host.modes


def test_staging_over_an_existing_release_is_refused(host, evaluators):
    host.seed_release("r-new", TREE)
    with pytest.raises(ReleaseAlreadyStaged):
        build(host, evaluators).stage(SECOND_TREE, target=TARGET)


def test_an_empty_tree_is_refused(host, evaluators):
    with pytest.raises(PreconditionFailed):
        build(host, evaluators).stage((), target=TARGET)


def test_the_ordering_manifest_is_written_outside_the_release(host, evaluators):
    """A file inside the release reads back as a path the renderer never made."""
    staged = build(host, evaluators).stage(TREE, target=TARGET)
    directory = f"{ROOT}/releases/{staged.current}/"
    assert f"{ROOT}/manifests/{staged.current}.json" in host.files
    assert not any(
        name.startswith(directory) and name.endswith(".json") for name in host.files
    )


# ── capability 2: the previous pointer is READ, not supplied ────────────────


def test_the_previous_pointer_comes_from_the_host_not_the_caller(host, evaluators):
    """The property `previous_image` gets wrong today.

    The caller names no previous release anywhere in this call. What comes back
    is what the host's own pointer said, which is the only thing a rollback can
    safely restore.
    """
    host.seed_release("r-old", TREE)
    staged = build(host, evaluators).stage(SECOND_TREE, target=TARGET)
    assert staged.previous == "r-old"
    assert staged.previous_shape == "symlink"


def test_a_host_that_never_held_a_release_reports_null_once(host, evaluators):
    staged = build(host, evaluators).stage(TREE, target=TARGET)
    assert staged.previous is None
    assert staged.previous_shape == "absent"


def test_a_pointer_that_is_not_a_symlink_is_refused_not_reported_null(host, evaluators):
    """The sensitivity half: three broken shapes used to read as a fresh host."""
    host.files[f"{ROOT}/current"] = b"releases/r-old\n"
    with pytest.raises(PreviousPointerUnreadable, match="not a symlink"):
        build(host, evaluators).stage(TREE, target=TARGET)


def test_a_dangling_pointer_is_refused_not_reported_null(host, evaluators):
    host.links[f"{ROOT}/current"] = "releases/r-gone"
    with pytest.raises(PreviousPointerUnreadable, match="already gone"):
        build(host, evaluators).stage(TREE, target=TARGET)


# ── capability 3: exact-byte transport ──────────────────────────────────────


def test_every_byte_that_arrives_is_the_byte_that_was_sent(host, evaluators):
    staged = build(host, evaluators).stage(TREE, target=TARGET)
    directory = f"{ROOT}/releases/{staged.current}"
    for path, text in TREE:
        assert host.files[f"{directory}/{path}"] == text.encode("utf-8")


def test_one_flipped_byte_refuses_and_activates_nothing(host, evaluators):
    """The refusal that makes the transport exact rather than merely hopeful.

    Every command succeeded; only the bytes disagree. The release must not
    exist and the pointer must not have moved, because a corrupt release that
    was activated and then failed verification is a host that has to be rolled
    back from a state it should never have reached.
    """
    host.seed_release("r-old", TREE)
    host.corrupt.add("prometheus.yml")
    with pytest.raises(TransportBytesMismatch, match="Nothing has been activated"):
        build(host, evaluators).stage(SECOND_TREE, target=TARGET)

    assert host.links[f"{ROOT}/current"] == "releases/r-old"
    assert not host.is_dir(f"{ROOT}/releases/r-new")
    assert not host.is_dir(f"{ROOT}/.staging/r-new")


def test_a_stale_file_in_the_staging_directory_refuses(host, evaluators):
    host.files[f"{ROOT}/.staging/r-new/leftover.yml"] = b"old\n"
    facility = build(host, evaluators)
    host._cmd_rm = lambda args, stdin: CommandOutcome(0)  # the sweep silently failed
    with pytest.raises(TransportBytesMismatch, match="unexpected"):
        facility.stage(TREE, target=TARGET)


def test_the_ssh_transport_quotes_rather_than_joining_on_spaces() -> None:
    """A path with a space is otherwise two arguments on the far end."""
    recorded: list[list[str]] = []

    class Recorder:
        def run(self, argv, *, stdin=None, timeout_seconds):
            recorded.append(list(argv))
            return CommandOutcome(0)

    SshTransport(destination="deploy@observer", inner=Recorder()).run(
        ["tee", "/srv/a b/c.yml"], stdin=b"x", timeout_seconds=5
    )
    assert recorded[0][:2] == ["ssh", "-o"]
    assert recorded[0][-1] == "tee '/srv/a b/c.yml'"


# ── capability 4: atomic activation ─────────────────────────────────────────


def test_activation_is_a_rename_and_never_an_unlink_then_symlink(host, evaluators):
    """`ln -sfn` has a window with no pointer; a single-file bind mount taken
    in that window binds to the wrong inode for the life of the container."""
    staged = build(host, evaluators).stage(TREE, target=TARGET)
    pointer = f"{ROOT}/current"
    assert host.links[pointer] == f"releases/{staged.current}"
    assert not any("-sfn" in call for call in host.calls)
    swap = [call for call in host.calls if call[0] == "mv" and call[-1] == pointer]
    assert swap and swap[-1][1] == "-T"


def test_a_swap_that_does_not_read_back_is_refused(host, evaluators):
    host.swallow_swap = True
    with pytest.raises(ActivationNotObserved, match="reads back as"):
        build(host, evaluators).stage(TREE, target=TARGET)


def test_a_target_this_facility_is_not_bound_to_is_refused(host, evaluators):
    with pytest.raises(UnknownTarget):
        build(host, evaluators).stage(TREE, target="observer-lagos")


# ── capabilities 5 and 6: each evaluator reloads, and each is checked ───────


def test_prometheus_and_alertmanager_are_each_reloaded(host, evaluators):
    build(host, evaluators).reload(target=TARGET, release="r-new")
    assert evaluators.reloaded == ["prometheus", "alertmanager"]


def test_a_reload_endpoint_that_refuses_is_a_failure(host, evaluators):
    evaluators.reload_status = 405  # lifecycle not enabled
    with pytest.raises(ReloadRefused):
        build(host, evaluators).reload(target=TARGET, release="r-new")


def test_a_200_whose_last_successful_reload_predates_it_is_refused(host, evaluators):
    """THE reload property: a 200 is the request, not the configuration.

    The endpoint accepts, and the evaluator's own success timestamp is older
    than the moment we posted — so whatever it is running, it is not what we
    just asked it to take.
    """
    evaluators.reload_timestamp = evaluators.now - 60
    with pytest.raises(ReloadNotObserved, match="older than the request"):
        build(host, evaluators).reload(target=TARGET, release="r-new")


def test_a_reload_reported_unsuccessful_is_refused(host, evaluators):
    evaluators.reload_successful = "0"
    with pytest.raises(ReloadNotObserved, match="configuration did not"):
        build(host, evaluators).reload(target=TARGET, release="r-new")


def test_an_unexported_reload_metric_is_not_a_successful_reload(host, evaluators):
    evaluators.export_reload_metrics = False
    with pytest.raises(ReloadNotObserved, match="does not export"):
        build(host, evaluators).reload(target=TARGET, release="r-new")


# ── capability 7: the complete live read-back ───────────────────────────────


def _observe(
    host: FakeHost, evaluators: FakeEvaluators, release: str
) -> dict[str, Any]:
    facility = build(host, evaluators, release_id=release)
    return facility.observe(
        target=TARGET,
        request=ObservationRequest(
            release=release,
            paths=tuple(path for path, _ in TREE),
            integrity_counters=CONTEXT.integrity_counters,
            probe_slots=SLOTS,
            previous="r-old",
        ),
    )


def test_the_read_back_carries_every_block_the_contract_requires(host, evaluators):
    host.seed_release("r-live", TREE)
    document = _observe(host, evaluators, "r-live")

    assert document["schema_version"] == OBSERVATION_SCHEMA
    assert set(document) >= {
        "schema_version",
        "observed_at",
        "environment",
        "host_target_id",
        "release",
        "tree",
        "targets",
        "rules",
        "routes",
        "integrity",
        "canary",
        "probes",
    }
    assert document["release"] == {"current": "r-live", "previous": "r-old"}


def test_the_tree_block_is_complete_and_digested(host, evaluators):
    host.seed_release("r-live", TREE)
    document = _observe(host, evaluators, "r-live")

    assert [entry["path"] for entry in document["tree"]] == sorted(
        path for path, _ in TREE
    )
    for entry in document["tree"]:
        text = dict(TREE)[entry["path"]]
        assert entry["sha256"] == hashlib.sha256(text.encode()).hexdigest()


def test_an_empty_release_directory_is_refused_rather_than_read_as_clean(
    host, evaluators
):
    """A read-back that listed nothing reports exactly as a clean one does."""
    host.links[f"{ROOT}/current"] = "releases/r-live"
    host.dirs.add(f"{ROOT}/releases/r-live")
    with pytest.raises(TreeReadBackIncomplete, match="listed no files"):
        _observe(host, evaluators, "r-live")


def test_a_manifest_disagreeing_with_the_host_is_refused(host, evaluators):
    """The manifest supplies order and is never a substitute for looking."""
    host.seed_release("r-live", TREE)
    host.files[f"{ROOT}/manifests/r-live.json"] = json.dumps(
        {"paths": ["prometheus/prometheus.yml"]}
    ).encode()
    with pytest.raises(TreeReadBackIncomplete, match="omits"):
        _observe(host, evaluators, "r-live")


def test_targets_rules_and_routes_come_from_the_running_stack(host, evaluators):
    host.seed_release("r-live", TREE)
    document = _observe(host, evaluators, "r-live")

    assert document["targets"] == [{"job": "prometheus", "health": "up"}]
    assert document["rules"] == [{"group": "meta", "name": "GateStale", "health": "ok"}]
    assert document["routes"] == [{"id": "fleet", "receiver": "fleet-oncall"}]


def test_an_unfamiliar_health_word_becomes_unknown_not_itself(host, evaluators):
    """The schema's enum is closed; a stray word makes the whole receipt unfilable."""
    host.seed_release("r-live", TREE)
    evaluators.targets = [{"labels": {"job": "loki"}, "health": "flapping"}]
    document = _observe(host, evaluators, "r-live")
    assert document["targets"] == [{"job": "loki", "health": "unknown"}]


def test_the_counter_and_the_process_start_time_are_one_read(host, evaluators):
    """Only `process_start_time_seconds` separates a repair from a reset."""
    host.seed_release("r-live", TREE)
    document = _observe(host, evaluators, "r-live")
    assert document["integrity"] == {
        "counter": "prometheus_tsdb_out_of_order_samples_total",
        "value": 41,
        "process_start_time": 900.0,
    }


def test_a_read_back_with_no_named_counter_is_refused(host, evaluators):
    host.seed_release("r-live", TREE)
    facility = build(
        host,
        evaluators,
        context=PromotionContext(
            environment="production",
            host_target_id=TARGET,
            probe_slots=SLOTS,
            canary=CANARY,
        ),
    )
    with pytest.raises(PreconditionFailed, match="ingestion counter"):
        facility.observe(target=TARGET, request=ObservationRequest(release="r-live"))


def test_the_canary_is_not_delivered_without_a_witness(host, evaluators):
    """Alertmanager's own 200 is its outbound attempt, not a human reached."""
    host.seed_release("r-live", TREE)
    document = _observe(host, evaluators, "r-live")
    assert document["canary"]["fired"] is True
    assert document["canary"]["delivered"] is False
    assert "receiver_evidence_ref" not in document["canary"]


def test_a_witness_supplies_the_evidence_reference(host, evaluators):
    host.seed_release("r-live", TREE)
    facility = build(
        host,
        evaluators,
        release_id="r-live",
        receiver_witness=lambda receiver, alert: f"slack:{receiver}/{alert}",
    )
    document = facility.observe(
        target=TARGET, request=ObservationRequest(release="r-live")
    )
    assert document["canary"]["delivered"] is True
    assert document["canary"]["receiver_evidence_ref"] == (
        "slack:fleet-oncall/PromotionCanary"
    )


def test_a_read_back_with_no_canary_plan_is_refused(host, evaluators):
    host.seed_release("r-live", TREE)
    facility = build(
        host,
        evaluators,
        context=PromotionContext(
            environment="production",
            host_target_id=TARGET,
            probe_slots=SLOTS,
            integrity_counters=CONTEXT.integrity_counters,
        ),
    )
    with pytest.raises(PreconditionFailed, match="canary"):
        facility.observe(target=TARGET, request=ObservationRequest(release="r-live"))


def test_each_family_gets_its_own_slot_and_its_own_nested_control(host, evaluators):
    """An IPv6 probe structurally cannot borrow an IPv4 control."""
    host.seed_release("r-live", TREE)
    seen: list[ProbeSlot] = []

    def prober(slot: ProbeSlot) -> ProbeObservation:
        seen.append(slot)
        return ProbeObservation(
            outcome="refused",
            chain="INPUT" if slot.family == "ipv6" else "DOCKER-USER",
            control_outcome="reachable",
            control_evidence_ref=f"probe:{slot.family}",
        )

    facility = build(host, evaluators, release_id="r-live", surface_prober=prober)
    document = facility.observe(
        target=TARGET, request=ObservationRequest(release="r-live")
    )
    assert [slot.family for slot in seen] == ["ipv4", "ipv6"]
    assert [probe["family"] for probe in document["probes"]] == ["ipv4", "ipv6"]
    assert [probe["chain"] for probe in document["probes"]] == ["DOCKER-USER", "INPUT"]
    assert document["probes"][1]["control"] == {
        "outcome": "reachable",
        "evidence_ref": "probe:ipv6",
    }


def test_without_a_prober_a_probe_is_inconclusive_not_refused(host, evaluators):
    """A refusal with no working control proves the prober ran, not a shut port."""
    host.seed_release("r-live", TREE)
    document = _observe(host, evaluators, "r-live")
    assert {probe["outcome"] for probe in document["probes"]} == {"inconclusive"}
    assert {probe["control"]["outcome"] for probe in document["probes"]} == {
        "inconclusive"
    }


def test_a_probe_slot_with_no_declared_expectation_is_refused(host, evaluators):
    """A guessed expectation manufactures a pass for a surface nobody described."""
    host.seed_release("r-live", TREE)

    class BareRequest:
        release = "r-live"
        paths = ()
        integrity_counters = CONTEXT.integrity_counters
        probe_slots = (("grafana", "ipv4"),)

    with pytest.raises(PreconditionFailed, match="no expectation is declared"):
        build(host, evaluators, release_id="r-live").observe(
            target=TARGET, request=BareRequest()
        )


# ── capabilities 8 and 9: exact rollback, and the read-back that proves it ──


def test_rollback_restores_the_exact_release_the_pointer_named(host, evaluators):
    host.seed_release("r-old", TREE)
    facility = build(host, evaluators)
    staged = facility.stage(SECOND_TREE, target=TARGET)
    assert host.links[f"{ROOT}/current"] == "releases/r-new"

    facility.rollback(target=TARGET, release=staged.previous or "")
    assert host.links[f"{ROOT}/current"] == "releases/r-old"


def test_rollback_returns_a_read_back_of_the_restored_host(host, evaluators):
    """The fact a `None` return can never carry.

    Performing the rollback and observing the restored state are two facts, and
    the second one is the one an operator needs. The document must describe the
    RESTORED release — its tree, not the failed one's — and must name the
    pointer it read back rather than the argument it was handed.
    """
    host.seed_release("r-old", TREE)
    facility = build(host, evaluators)
    staged = facility.stage(SECOND_TREE, target=TARGET)

    document = facility.rollback(target=TARGET, release=staged.previous or "")

    assert document["schema_version"] == OBSERVATION_SCHEMA
    assert document["release"]["current"] == "r-old"
    assert document["rollback"] == {
        "exercised": True,
        "restored_release": "r-old",
        "restored_digest": reference_tree_digest(TREE),
        "succeeded": True,
    }
    # The tree in the document is the RESTORED release's bytes, not the staged
    # one's — the two differ by one scrape interval and nothing else, which is
    # exactly the kind of difference a pointer-only rollback would hide.
    restored = {entry["path"]: entry["sha256"] for entry in document["tree"]}
    assert (
        restored["prometheus/prometheus.yml"]
        == hashlib.sha256(dict(TREE)["prometheus/prometheus.yml"].encode()).hexdigest()
    )


def test_the_restored_digest_is_the_digest_the_release_was_accepted_with(
    host, evaluators
):
    """Order-dependent, and computed here in the renderer's order.

    `tree_digest` hashes path and contents in render order, which is not
    alphabetical; a read-back that hashed the directory listing would produce a
    digest that never equals the accepted one, and condition 6 compares them
    with `!=`.
    """
    host.seed_release("r-old", TREE)
    facility = build(host, evaluators)
    staged = facility.stage(SECOND_TREE, target=TARGET)
    document = facility.rollback(target=TARGET, release=staged.previous or "")

    assert document["rollback"]["restored_digest"] == reference_tree_digest(TREE)
    assert document["rollback"]["restored_digest"] != reference_tree_digest(SECOND_TREE)
    assert tree_digest(
        [(path, text.encode()) for path, text in TREE]
    ) == reference_tree_digest(TREE)


def test_a_rollback_whose_evaluators_never_reloaded_is_not_a_success(host, evaluators):
    """A restored pointer whose configuration nothing took is not a recovered host.

    Reported rather than raised: the read-back is the most useful thing an
    operator can be handed here, and raising would throw it away.
    """
    host.seed_release("r-old", TREE)
    facility = build(host, evaluators)
    staged = facility.stage(SECOND_TREE, target=TARGET)
    evaluators.reload_timestamp = evaluators.now - 600

    document = facility.rollback(target=TARGET, release=staged.previous or "")
    assert document["rollback"]["exercised"] is True
    assert document["rollback"]["restored_release"] == "r-old"
    assert document["rollback"]["succeeded"] is False


def test_rollback_refuses_a_release_that_is_not_on_the_host(host, evaluators):
    host.seed_release("r-old", TREE)
    with pytest.raises(RollbackTargetMissing, match="not on the host"):
        build(host, evaluators).rollback(target=TARGET, release="r-vanished")


def test_rollback_refuses_to_restore_nothing(host, evaluators):
    with pytest.raises(RollbackTargetMissing, match="restore nothing"):
        build(host, evaluators).rollback(target=TARGET, release="")


def test_a_missing_manifest_yields_an_unknown_digest_not_a_guessed_one(
    host, evaluators
):
    host.seed_release("r-old", TREE)
    host.files.pop(f"{ROOT}/manifests/r-old.json")
    facility = build(host, evaluators)
    staged = facility.stage(SECOND_TREE, target=TARGET)

    document = facility.rollback(target=TARGET, release=staged.previous or "")
    assert document["rollback"]["restored_digest"] is None
    assert document["rollback"]["succeeded"] is False


# ── acceptance: the baseline the NEXT rollback is measured against ──────────


def test_accept_records_the_digest_the_release_was_accepted_with(host, evaluators):
    facility = build(host, evaluators)
    staged = facility.stage(TREE, target=TARGET)
    record = facility.accept(target=TARGET, release=staged.current)

    assert record.tree_digest == reference_tree_digest(TREE)
    written = json.loads(host.files[f"{ROOT}/accepted.json"].decode())
    assert written["release"] == staged.current
    assert written["tree_digest"] == reference_tree_digest(TREE)


def test_accept_refuses_a_release_the_host_is_not_running(host, evaluators):
    host.seed_release("r-old", TREE)
    facility = build(host, evaluators)
    facility.stage(SECOND_TREE, target=TARGET)
    with pytest.raises(ActivationNotObserved, match="host is running"):
        facility.accept(target=TARGET, release="r-old")


# ── preservation: the pointer that was READ is the pointer that is REPORTED ──


def test_the_read_pointer_is_preserved_into_a_later_read_back(host, evaluators):
    """Reading the previous pointer is only half of the capability.

    The control plane's own `ObservationRequest` has no field for it, and a
    null `release.previous` on a promotion that is not the first is
    `RECEIPT-NO-ROLLBACK-TARGET` — the receipt is refused. So the facility
    carries forward what IT read, rather than letting the caller re-supply a
    belief; that is the same defect `previous_image` has, one layer up.
    """
    host.seed_release("r-old", TREE)
    facility = build(host, evaluators)
    facility.stage(SECOND_TREE, target=TARGET)

    class BareRequest:
        release = "r-new"
        paths = ()
        integrity_counters = CONTEXT.integrity_counters
        probe_slots = SLOTS

    document = facility.observe(target=TARGET, request=BareRequest())
    assert document["release"] == {"current": "r-new", "previous": "r-old"}


def test_a_first_promotion_preserves_a_null_because_that_is_what_it_read(
    host, evaluators
):
    facility = build(host, evaluators)
    facility.stage(TREE, target=TARGET)
    document = facility.observe(
        target=TARGET, request=ObservationRequest(release="r-new")
    )
    assert document["release"] == {"current": "r-new", "previous": None}


def test_the_rollback_read_back_names_the_release_it_rolled_away_from(host, evaluators):
    """`previous` means the pointer live immediately before THIS activation."""
    host.seed_release("r-old", TREE)
    facility = build(host, evaluators)
    staged = facility.stage(SECOND_TREE, target=TARGET)

    document = facility.rollback(target=TARGET, release=staged.previous or "")
    assert document["release"] == {"current": "r-old", "previous": "r-new"}


def test_more_counters_than_the_contract_can_hold_is_refused(host, evaluators):
    """A subset read cannot be filed as a complete one.

    The control plane derives this list from every gate's integrity predicate
    and can name several; the contract's `integrity` block holds exactly one.
    Reading the first would verify one counter and produce a document that
    reads as a complete read-back.
    """
    host.seed_release("r-live", TREE)
    facility = build(
        host,
        evaluators,
        release_id="r-live",
        context=PromotionContext(
            environment="production",
            host_target_id=TARGET,
            probe_slots=SLOTS,
            canary=CANARY,
            integrity_counters=(
                "prometheus_tsdb_out_of_order_samples_total",
                "loki_discarded_samples_total",
            ),
        ),
    )
    with pytest.raises(PreconditionFailed, match="holds exactly one"):
        facility.observe(target=TARGET, request=ObservationRequest(release="r-live"))
