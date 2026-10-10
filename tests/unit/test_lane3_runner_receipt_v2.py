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
        argv += ["--topology", _write(tmp_path, "lane3-execution.json", topology)]
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
    assert "trusted_cp_v3_plan_provider" in capsys.readouterr().err
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
