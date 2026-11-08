"""D-S2c C2: the Lane 3 runner produces only `RehearsalReceipt.v2`.

`exposure_rehearsal_runner.py` used to build a v1 receipt with
`build_receipt`. It now takes the plan, grant and outcome through the D4
producer (`lane3_receipt_v2`), binds the execution run before the host is
touched, and assembles with `assemble_receipt`. None of those inputs exists
yet, so every run refuses; these tests pin WHERE and WITH WHAT STATUS it
refuses, because both are what a terminal record and an operator act on.

A run that refuses before host contact is given a descriptor path that does
not exist. Reaching the descriptor would raise `FileNotFoundError`, which the
runner does not catch, so a clean refusal status proves the refusal came first.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import sys
from typing import Any

import pytest

from tests.unit.lane3_evidence_support import write_topology
from tests.unit.test_lane3_receipt_v2 import API_RUN, DOCUMENT, ENVIRON
from tests.unit.test_lane3_topology_source import HOST as TOPO_HOST
from tests.unit.test_lane3_topology_source import VALUES as TOPO_VALUES
from tests.unit.test_lane3_topology_source import record as topo_record

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "exposure_rehearsal_runner.py"
DESCRIPTOR = ROOT / "scripts" / "exposure-rehearsal" / "product.toml"
FINGERPRINT_TEXT = "SHA256:T1kdK/6QTzzwU1EienO6nUgk8wu9UpjqB8BatKbndSE"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("_lane3_runner_v2", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["_lane3_runner_v2"] = module
    spec.loader.exec_module(module)
    return module


runner = _load()
producer = sys.modules["lane3_receipt_v2"]


def _write_topology(tmp_path: pathlib.Path, document: Any) -> str:
    return write_topology(tmp_path, document)


def _write(tmp_path: pathlib.Path, name: str, document: Any) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


def _argv(
    tmp_path: pathlib.Path,
    *,
    topology: Any = None,
    api: Any = None,
    descriptor: pathlib.Path | None = None,
) -> list[str]:
    argv = [
        "--foundation-revision",
        "2288b4d68f6b93d3e391d0dafa04987fb3f750f7",
        "--candidate-source-revision",
        "27bee8fc43919a5ed7f4853ccdedc2f996ad8d86",
        "--foundation-artifact",
        "sha256:" + "17" * 32,
        "--authorization-run",
        "authz-77",
        "--controller-identity",
        FINGERPRINT_TEXT,
        "--controller-key",
        str(tmp_path / "controller-key"),
        "--host-id",
        "lane3-rehearsal-target",
        "--candidate-version",
        "0.4.0a2",
        "--inside-jump-key",
        str(tmp_path / "jump-key"),
        "--observer-key",
        str(tmp_path / "observer-key"),
        "--probe-evidence",
        str(tmp_path / "probe-evidence.json"),
        "--descriptor",
        str(descriptor or tmp_path / "never-opened.toml"),
        "--receipt-out",
        str(tmp_path / "receipt.json"),
        "--lease-dir",
        str(tmp_path / "leases"),
        "--release-out",
        str(tmp_path / "release.json"),
        "--terminal-evidence-out",
        str(tmp_path / "evidence.json"),
        "--api-run",
        _write(tmp_path, "api-run.json", API_RUN if api is None else api),
    ]
    if topology is not None:
        argv += ["--topology", _write_topology(tmp_path, topology)]
    return argv


@pytest.fixture()
def lane3_runtime(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    for name, value in ENVIRON.items():
        monkeypatch.setenv(name, value)
    return dict(ENVIRON)


def _no_receipt_and_no_release(tmp_path: pathlib.Path) -> None:
    assert not (tmp_path / "receipt.json").exists()
    assert not (tmp_path / "release.json").exists()


# ── before the host: the run binding ────────────────────────────────────────


def test_the_checked_in_topology_is_unanswerable_before_the_descriptor(
    tmp_path: pathlib.Path,
    lane3_runtime: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The real `.github/lane3-execution.json`: admission_evidence is null.
    assert runner.main(_argv(tmp_path)) == 2
    assert "unanswerable" in capsys.readouterr().err
    _no_receipt_and_no_release(tmp_path)


def test_a_run_outside_the_pinned_surface_is_refused_before_the_descriptor(
    tmp_path: pathlib.Path,
    lane3_runtime: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "1397614141")
    assert runner.main(_argv(tmp_path, topology=DOCUMENT)) == 1
    _no_receipt_and_no_release(tmp_path)


def test_an_api_run_that_disagrees_is_refused_before_the_descriptor(
    tmp_path: pathlib.Path, lane3_runtime: dict[str, str]
) -> None:
    api = {**API_RUN, "run_attempt": 2}
    assert runner.main(_argv(tmp_path, topology=DOCUMENT, api=api)) == 1
    _no_receipt_and_no_release(tmp_path)


def test_a_missing_api_run_is_refused_not_ignored(
    tmp_path: pathlib.Path, lane3_runtime: dict[str, str]
) -> None:
    argv = _argv(tmp_path, topology=DOCUMENT)
    argv[argv.index("--api-run") + 1] = ""
    assert runner.main(argv) == 1
    _no_receipt_and_no_release(tmp_path)


# ── the plan: one source, and it refuses ────────────────────────────────────


def test_an_admitted_run_is_unanswerable_for_want_of_a_trusted_plan(
    tmp_path: pathlib.Path,
    lane3_runtime: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Past the run binding, the vantage topology and the descriptor, and
    # stopped at the plan: before the lease, so no release is written and the
    # host keeps its standing.
    argv = _argv(tmp_path, topology=DOCUMENT, descriptor=DESCRIPTOR)
    assert runner.main(argv, topology_src=FixedSource(topo_record())) == 2
    assert "no distribution declares" in capsys.readouterr().err
    _no_receipt_and_no_release(tmp_path)


# ── the vantage topology: one injected seam, before any target contact ─────


class FixedSource:
    """In-memory stand-in for the B7 KV reader (documentation-range values)."""

    def __init__(self, document: Any, version: int = 7) -> None:
        self.document, self.version = document, version

    def read(self) -> Any:
        return runner.topology_source.TopologyReading(
            record=copy.deepcopy(self.document), kv_version=self.version
        )


def _evidence(tmp_path: pathlib.Path) -> dict[str, Any]:
    return json.loads((tmp_path / "evidence.json").read_text(encoding="utf-8"))


def test_without_a_topology_reader_the_run_is_unanswerable_before_the_descriptor(
    tmp_path: pathlib.Path,
    lane3_runtime: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The production default: no B7 reader exists. The descriptor path does not
    # exist, so a clean exit 2 proves the refusal came before it was opened.
    assert runner.main(_argv(tmp_path, topology=DOCUMENT)) == 2
    assert "topology record unavailable" in capsys.readouterr().err
    _no_receipt_and_no_release(tmp_path)
    assert _evidence(tmp_path)["vantage_topology"] is None


def _far_end_elsewhere() -> dict[str, Any]:
    doc = topo_record()
    doc["targets"][TOPO_HOST]["far_end"] = "192.0.2.99"
    return doc


@pytest.mark.parametrize(
    ("document", "extra"),
    [
        (_far_end_elsewhere(), []),
        (topo_record(targets={}), []),
        (topo_record(), ["--host-id", "unknown-host"]),
        (topo_record(), ["--topology-version", "6"]),
    ],
    ids=["far-end-not-target", "no-targets", "unknown-host", "version-moved"],
)
def test_an_unbindable_topology_refuses_before_the_descriptor_naming_no_value(
    tmp_path: pathlib.Path,
    lane3_runtime: dict[str, str],
    capsys: pytest.CaptureFixture[str],
    document: Any,
    extra: list[str],
) -> None:
    argv = _argv(tmp_path, topology=DOCUMENT) + extra
    assert runner.main(argv, topology_src=FixedSource(document)) == 1
    err = capsys.readouterr().err
    for value in TOPO_VALUES:
        assert value not in err
    _no_receipt_and_no_release(tmp_path)
    assert _evidence(tmp_path)["vantage_topology"] is None


def test_a_bound_topology_is_recorded_without_values(
    tmp_path: pathlib.Path,
    lane3_runtime: dict[str, str],
) -> None:
    argv = _argv(tmp_path, topology=DOCUMENT, descriptor=DESCRIPTOR)
    argv += ["--topology-version", "7"]
    assert runner.main(argv, topology_src=FixedSource(topo_record())) == 2
    evidence = _evidence(tmp_path)
    vantage = evidence["vantage_topology"]
    assert vantage["kv_version"] == 7
    assert vantage["probe_vantage_ref"] == "probe-vantage-1@7"
    assert vantage["host_id"] == TOPO_HOST == evidence["target"]
    text = json.dumps({k: v for k, v in evidence.items() if k != "vantage_topology"})
    text += json.dumps({k: v for k, v in vantage.items() if k != "probe_vantage_ref"})
    for value in TOPO_VALUES:
        assert value not in text


def test_the_plan_source_refuses_naming_only_the_plan_precondition() -> None:
    with pytest.raises(producer.AuthorityUnavailable) as caught:
        producer.acquire_execution_plan()
    assert caught.value.missing
    assert all(m.startswith("trusted_cp") for m in caught.value.missing)


# ── the translation into this lane's two families ───────────────────────────


@pytest.mark.parametrize(
    "raised",
    [
        producer.AuthorityUnavailable(["trusted_cp_v3_plan_provider: x"]),
        sys.modules["lane3_execution"].TopologyRefused("not admitted"),
    ],
)
def test_cannot_answer_keeps_the_unanswerable_status(raised: Exception) -> None:
    with pytest.raises(runner.AuthorizationUnverifiable) as caught:
        with runner.receipt_v2_refusals(refused_as=runner.PreconditionUnfit):
            raise raised
    assert caught.value.exit_status == 2


@pytest.mark.parametrize("site", ["PreconditionUnfit", "SpecError"])
def test_a_contradiction_refuses_as_the_call_site_says(site: str) -> None:
    kind = getattr(runner, site)
    with pytest.raises(kind):
        with runner.receipt_v2_refusals(refused_as=kind):
            raise producer.ReceiptRefused("the API run disagrees")


def test_an_assembly_contradiction_under_the_lease_is_uncertified_not_unfit() -> None:
    # `SpecError` at assembly is what the terminal record must see: the host
    # was owned and observed, so "nothing attempted" would be false.
    exc = runner.SpecError("the evidence bundle is not age-encrypted")
    member = runner.classify_refusal(exc, lease_in_hand=True, host_mutated=False)
    assert member is runner.TerminalRefusal.HOST_STATE_UNCERTIFIED


def test_prepared_authorization_reads_pair_and_discovers_bindings_once(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    import lane3_authorization as authorization

    events: list[object] = []
    bindings = SimpleNamespace(
        authorization_v3_provider=object(), build_effects=lambda *a: object()
    )
    plan = object()
    grant = object()

    def discover(**kwargs: object) -> object:
        events.append("discover")
        return bindings

    def authorize(**kwargs: object) -> object:
        events.append(kwargs)
        return grant

    monkeypatch.setattr(
        authorization, "_verifying_symbols", lambda: (authorize, discover)
    )
    path = pathlib.Path(
        _write(
            tmp_path,
            "pair.json",
            {"authorization_material": {}, "dispatch_material": {}},
        )
    )
    prepared = authorization.prepare_authorization(
        authorization_document=path, require_build_effects=True
    )
    path.unlink()
    assert prepared.bindings is bindings
    assert (
        authorization.establish_authorization(
            descriptor_digest="digest",
            target="host",
            authorization_document=None,
            execution_plan=plan,
            prepared=prepared,
        )
        is grant
    )
    assert events[0] == "discover" and len(events) == 2
    assert events[1]["bindings"] is bindings
    assert events[1]["plan"] is plan


def test_remote_bootstrap_refuses_local_effects_fallback_before_pair_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import lane3_authorization as authorization

    bindings = SimpleNamespace(authorization_v3_provider=object())
    monkeypatch.setattr(
        authorization, "_verifying_symbols", lambda: (object(), lambda **k: bindings)
    )
    monkeypatch.setattr(
        authorization, "_read_document", lambda *a: pytest.fail("read pair")
    )
    with pytest.raises(authorization.AuthorizationUnverifiable) as caught:
        authorization.prepare_authorization(
            authorization_document="missing", require_build_effects=True
        )
    assert caught.value.standing is authorization.Standing.UNANSWERABLE


@pytest.mark.parametrize(
    "failure",
    [
        "bootstrap",
        "pair",
        "observe",
        "authority_missing",
        "lease",
        "controller",
        "lock",
        "render",
        "host",
        "grant",
        "plan_controller",
        "exposure",
        "lease_recheck",
        "executor",
    ],
)
def test_runner_phase_failures_and_exact_object_handoff(
    tmp_path: pathlib.Path,
    lane3_runtime: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Canaries: each refused phase must prevent every later effect boundary."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    events: list[object] = []
    captured: dict[str, Any] = {}
    deployment_plan = SimpleNamespace(image_digest="sha256:image")
    base = object()
    plan = SimpleNamespace(
        controller_ssh_fingerprint="other"
        if failure == "plan_controller"
        else FINGERPRINT_TEXT,
        host_id="other" if failure == "host" else TOPO_HOST,
        exposure_reconciliations=() if failure == "exposure" else (object(),),
    )
    grant = object()
    held = SimpleNamespace(require_held=lambda **k: events.append("held"))

    def refuse(where: str) -> None:
        if failure == where:
            raise runner.SpecError(where)

    class Lease:
        controller_identity_fingerprint = runner.ControllerSshFingerprintV1.parse(
            "SHA256:" + "A" * 43 if failure == "controller" else FINGERPRINT_TEXT,
            field="controller",
        )

        def covers(self, **kwargs: object) -> None:
            events.append("covers")
            refuse("lease" if events.count("covers") == 1 else "lease_recheck")

    lease = Lease()
    ctx = runner.TerminalContext()

    class Effects:
        def observe_roles(self) -> tuple[object, ...]:
            assert ctx.lease is None and "load_lease" not in events
            assert events[-1] == "build_effects"
            events.append("observe")
            refuse("observe")
            return ()

    effects = Effects()

    def build_effects(*args: object) -> Effects:
        events.append("build_effects")
        return effects

    bindings = SimpleNamespace(build_effects=build_effects)
    prepared = SimpleNamespace(bindings=bindings)

    def prepare(**kwargs: object) -> object:
        events.append("bootstrap")
        assert kwargs["require_build_effects"] is True
        if failure in {"bootstrap", "pair"}:
            raise runner.AuthorizationUnverifiable(
                runner.Standing.UNANSWERABLE
                if failure == "bootstrap"
                else runner.Standing.UNATTESTABLE,
                "missing provider" if failure == "bootstrap" else "malformed pair",
            )
        return prepared

    @contextmanager
    def lock(*args: object, **kwargs: object) -> Any:
        assert kwargs["directory"] == "/var/lock/dotmac"
        events.append("lock")
        refuse("lock")
        yield held
        events.append("unlock")

    def build_base(spec: Any, **kwargs: Any) -> tuple[object, object, object]:
        assert kwargs["bindings"] is bindings
        assert kwargs["effects"] is effects and "deployment_plan" not in kwargs
        captured["spec"] = spec
        effects.observe_roles()
        return base, deployment_plan, effects

    def render(got_base: object, **kwargs: object) -> object:
        assert got_base is base and kwargs["bindings"] is bindings
        events.append("render")
        if failure == "render":
            raise producer.ReceiptRefused("prelease renderer contradiction")
        if failure == "authority_missing":
            raise producer.AuthorityUnavailable(
                ["trusted_cp_v3_plan_provider: missing"]
            )
        return plan

    def authorize(**kwargs: object) -> object:
        assert kwargs["prepared"] is prepared and kwargs["execution_plan"] is plan
        assert ctx.lease is None and "load_lease" not in events
        events.append("grant")
        if failure == "grant":
            raise runner.AuthorizationUnverifiable(
                runner.Standing.UNATTESTABLE, "prelease pair refusal"
            )
        return grant

    def bind(got_effects: object, got_plan: object) -> None:
        assert got_effects is effects and got_plan is plan and "grant" in events
        events.append("bind")

    def observe_exposure() -> object:
        assert ctx.lease is lease and "grant" in events and "lock" in events
        events.append("exposure_snapshot")
        return SimpleNamespace(sockets=(), chains=(), proxies=())

    exposure = SimpleNamespace(observe=observe_exposure)

    def load_lease(*args: object, **kwargs: object) -> object:
        assert "grant" in events and events.index("observe") < events.index("grant")
        events.append("load_lease")
        return lease

    def execute(got_plan: object, got_grant: object, **kwargs: Any) -> object:
        assert got_plan is plan and got_grant is grant
        assert kwargs["spec"] is captured["spec"]
        assert kwargs["deployment_plan"] is deployment_plan
        assert kwargs["effects"] is effects and kwargs["exposure_effects"] is exposure
        assert kwargs["bindings"] is bindings and kwargs["lock"] is held
        assert ctx.host_mutated is True
        events.append("executor")
        raise runner.SpecError("executor refused, including drift")

    classify = runner.classify_refusal

    def classify_actual(exc: BaseException, **kwargs: Any) -> Any:
        captured["refusal"] = exc
        return classify(exc, **kwargs)

    monkeypatch.setattr(runner, "classify_refusal", classify_actual)
    monkeypatch.setattr(runner, "TerminalContext", lambda: ctx)
    monkeypatch.setattr(
        runner,
        "record_terminal",
        lambda context, args, outcome: captured.update(terminal=outcome),
    )
    monkeypatch.setattr(runner, "prepare_authorization", prepare)
    monkeypatch.setattr(runner, "load_lease", load_lease)
    monkeypatch.setattr(runner, "deployment_lock", lock)
    monkeypatch.setattr(runner, "_load_probe_evidence", lambda *a: {})
    monkeypatch.setattr(runner, "_qualify", lambda *a: None)
    monkeypatch.setattr(runner, "require_inside_probe_harness", lambda *a, **k: None)
    monkeypatch.setattr(producer, "build_base_plan", build_base)
    monkeypatch.setattr(producer, "acquire_execution_plan", render)
    monkeypatch.setattr(runner, "establish_authorization", authorize)
    monkeypatch.setattr(runner, "bind_authorized_effects", bind)
    monkeypatch.setattr(runner, "_ssh_runner", lambda *a: object())
    monkeypatch.setattr(runner, "ComposeHostExposureEffects", lambda *a, **k: exposure)
    monkeypatch.setattr(runner, "read_installation_id", lambda *a: "installation")
    monkeypatch.setattr(producer, "execute_authorized_plan", execute)
    assert runner.main(
        _argv(tmp_path, topology=DOCUMENT, descriptor=DESCRIPTOR),
        topology_src=FixedSource(topo_record()),
    ) == (2 if failure in {"bootstrap", "authority_missing"} else 1)
    before_lease = {
        "bootstrap",
        "pair",
        "observe",
        "authority_missing",
        "render",
        "host",
        "grant",
        "plan_controller",
        "exposure",
    }
    if failure in {"bootstrap", "pair"}:
        assert "observe" not in events
    if failure in before_lease:
        assert "load_lease" not in events and "lock" not in events
        assert ctx.lease is None
    else:
        assert (
            events.index("observe")
            < events.index("render")
            < events.index("grant")
            < events.index("load_lease")
        )
    if failure != "executor":
        assert "executor" not in events
    if failure in before_lease | {"lease", "controller", "lock"}:
        assert "exposure_snapshot" not in events
    terminal = captured["terminal"]
    if failure in before_lease | {"lease", "controller"}:
        assert terminal is None
    else:
        assert terminal.refusal is runner.TerminalRefusal.HOST_STATE_UNCERTIFIED
    if failure in {
        "pair",
        "observe",
        "render",
        "host",
        "grant",
        "plan_controller",
        "exposure",
    }:
        assert isinstance(captured["refusal"], runner.PreconditionUnfit)
    if failure in {"bootstrap", "authority_missing"}:
        assert isinstance(captured["refusal"], runner.AuthorizationUnverifiable)
    assert ctx.host_mutated is (failure == "executor")
    assert events.count("bootstrap") == 1
    assert events.count("lock") <= 1
    assert events.count("observe") <= 1
    _no_receipt_and_no_release(tmp_path)


def test_preparation_cannot_supply_the_grant_issuer(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    import lane3_authorization as authorization

    bindings = SimpleNamespace(authorization_v3_provider=object())
    monkeypatch.setattr(
        authorization,
        "_verifying_symbols",
        lambda: (
            lambda **k: pytest.fail("startup callable retained"),
            lambda **k: bindings,
        ),
    )
    path = _write(
        tmp_path, "pair.json", {"authorization_material": {}, "dispatch_material": {}}
    )
    prepared = authorization.prepare_authorization(authorization_document=path)

    def refuse(**kwargs: object) -> None:
        raise ValueError("installed issuer refuses")

    monkeypatch.setattr(
        authorization,
        "_verifying_symbols",
        lambda: (refuse, lambda **k: pytest.fail("rediscovered")),
    )
    with pytest.raises(
        authorization.AuthorizationUnverifiable, match="installed issuer refuses"
    ):
        authorization.establish_authorization(
            descriptor_digest="digest",
            target="host",
            authorization_document=None,
            execution_plan=object(),
            prepared=prepared,
        )
