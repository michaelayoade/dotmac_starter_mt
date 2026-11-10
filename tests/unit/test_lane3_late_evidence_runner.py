"""Hosted canaries for current-run ciphertext binding and private upload exclusion."""

from __future__ import annotations

import argparse
import ast
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests.unit.test_lane3_exposure_evidence import _reconciliation, _records
from tests.unit.test_lane3_runner_receipt_v2 import (
    DESCRIPTOR,
    DOCUMENT,
    RUNNER,
    FixedSource,
    _argv,
    lane3_runtime,  # noqa: F401 - shared pytest fixture
    runner,
    topo_record,
)

ROOT = RUNNER.parents[1]


def test_probe_capture_keeps_qualified_bytes_after_source_changes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "private.json"
    original = b'{"vantage":{},"private":"original"}\n'
    path.write_bytes(original)
    captured, document = runner._capture_probe_evidence(path)
    path.write_bytes(b'{"private":"different"}')
    assert captured == original
    assert document["private"] == "original"


@pytest.mark.parametrize("payload", [b"", b"[]", b"private-sentinel-not-json"])
def test_bad_probe_capture_does_not_echo_payload(
    tmp_path: Path, payload: bytes
) -> None:
    path = tmp_path / "private-sentinel-path"
    path.write_bytes(payload)
    with pytest.raises(runner.EvidenceUnreadable) as caught:
        runner._capture_probe_evidence(path)
    assert "private-sentinel" not in str(caught.value)


def test_real_crossing_join_precedes_encryption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = []
    monkeypatch.setattr(
        runner.evidence_finalization,
        "finalize_evidence",
        lambda **kw: called.append(kw),
    )
    ctx = runner.TerminalContext()
    with pytest.raises(runner.receipt_v2.ReceiptRefused, match="finalization refused"):
        runner._finalize_run_evidence(
            argparse.Namespace(evidence_bundle_out=str(tmp_path / "bundle.age")),
            ctx,
            probe_evidence=b"{}",
            execution_plan=SimpleNamespace(
                exposure_reconciliations=(_reconciliation(),)
            ),
            encryption=object(),
        )
    assert called == []
    assert not (tmp_path / "bundle.age").exists()


def test_finalizer_receives_frozen_actual_records_and_probe_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = runner.TerminalContext(exposure_calls=_records()[:3])
    expected = runner.serialize_exposure_calls(tuple(ctx.exposure_calls))
    context = object()
    ciphertext = b"age-encryption.org/v1\nsynthetic-transport-test"
    output = tmp_path / "bundle.age"

    def encrypt(**kwargs):
        assert kwargs == {
            "probe_evidence": b'{"private":"qualified"}',
            "exposure_calls": expected,
            "context": context,
            "output": output,
        }
        ctx.exposure_calls.clear()
        return ciphertext

    monkeypatch.setattr(runner.evidence_finalization, "finalize_evidence", encrypt)
    assert (
        runner._finalize_run_evidence(
            argparse.Namespace(evidence_bundle_out=str(output)),
            ctx,
            probe_evidence=b'{"private":"qualified"}',
            execution_plan=SimpleNamespace(
                exposure_reconciliations=(_reconciliation(),)
            ),
            encryption=context,
        )
        is ciphertext
    )


def _assembly_phase():
    tree = ast.parse(RUNNER.read_text())
    function = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run"
    )
    stages = [
        n
        for n in function.body
        if isinstance(n, ast.With)
        and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_finalize_run_evidence"
            for call in ast.walk(n)
        )
    ]
    assert len(stages) == 1
    wrapper = ast.FunctionDef(
        name="assemble",
        args=ast.arguments(
            posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]
        ),
        body=stages,
        decorator_list=[],
    )
    return compile(
        ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
        str(RUNNER),
        "exec",
    )


@pytest.mark.parametrize("refuse", [False, True])
def test_actual_assembly_phase_binds_finalizer_bytes_and_retains_live_objects(
    refuse: bool,
) -> None:
    plan, grant, outcome, encryption = object(), object(), object(), object()
    ciphertext = b"age-encryption.org/v1\nactual-finalizer-result"
    events = []
    args = SimpleNamespace(
        foundation_revision="revision",
        foundation_artifact="artifact",
        probe_vantage_ref="vantage@1",
        receipt_out="receipt.json",
    )
    ctx = runner.TerminalContext()

    def finalize(got_args, got_ctx, **kwargs):
        assert got_args is args and got_ctx is ctx
        assert kwargs["execution_plan"] is plan and kwargs["encryption"] is encryption
        assert kwargs["probe_evidence"] == b"qualified"
        events.append("encrypt")
        if refuse:
            raise runner.evidence_finalization.FinalizationRefused("encryption refused")
        return ciphertext

    def assemble(**kwargs):
        assert events == ["encrypt"]
        assert (
            kwargs["execution_plan"] is plan
            and kwargs["grant"] is grant
            and kwargs["execution_outcome"] is outcome
        )
        assert kwargs["evidence_bundle"] is ciphertext
        events.append("assemble")
        return object()

    def write(receipt, path):
        assert events == ["encrypt", "assemble"]
        events.append("write")
        return "receipt-digest"

    namespace = dict(vars(runner))
    namespace.update(
        args=args,
        ctx=ctx,
        probe_bytes=b"qualified",
        execution_plan=plan,
        grant=grant,
        execution_outcome=outcome,
        encryption=encryption,
        descriptor_digest="descriptor",
        fixture_bytes=b"fixture",
        controller_identity="controller",
        lease=SimpleNamespace(authorization_run_id="auth"),
        execution_run=object(),
        started="start",
        results=SimpleNamespace(all=lambda: ()),
        _finalize_run_evidence=finalize,
        receipt_v2=SimpleNamespace(assemble_receipt=assemble, write_receipt=write),
    )
    exec(_assembly_phase(), namespace)  # noqa: S102 - actual trusted assembly AST
    if refuse:
        with pytest.raises(runner.SpecError, match="encryption refused"):
            namespace["assemble"]()
        assert events == ["encrypt"] and ctx.receipt_digest == ""
    else:
        namespace["assemble"]()
        assert events == ["encrypt", "assemble", "write"]
        assert ctx.receipt_digest == "receipt-digest"


@pytest.mark.usefixtures("lane3_runtime")
def test_unenrolled_encryption_stops_before_probe_or_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(
        runner,
        "prepare_authorization",
        lambda **kwargs: SimpleNamespace(bindings=object()),
    )

    def forbidden(*args, **kwargs):
        pytest.fail("unenrolled encryption reached private evidence or host")

    monkeypatch.setattr(runner, "_capture_probe_evidence", forbidden)
    monkeypatch.setattr(runner, "_ssh_runner", forbidden)
    assert (
        runner.main(
            _argv(tmp_path, topology=DOCUMENT, descriptor=DESCRIPTOR),
            topology_src=FixedSource(topo_record()),
        )
        == 2
    )
    assert "unenrolled" in capsys.readouterr().err
    assert not (tmp_path / "receipt.json").exists()
    assert not (tmp_path / "release.json").exists()


def _require_ciphertext_carriers(workflow: dict) -> None:
    uploads = [
        step
        for step in workflow["jobs"]["rehearse"]["steps"]
        if "upload-artifact@" in step.get("uses", "")
    ]
    paths = [
        line.strip() for step in uploads for line in step["with"]["path"].splitlines()
    ]
    assert "evidence-bundle.age" in paths
    assert not {
        "probe-evidence.json",
        "exposure-calls.json",
        "manifest.json",
    }.intersection(paths)
    assert all("*" not in path for path in paths)


def test_only_ciphertext_is_an_upload_carrier_and_prebuilt_input_is_gone() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/exposure-rehearsal.yml").read_text()
    )
    _require_ciphertext_carriers(workflow)
    planted = copy.deepcopy(workflow)
    upload = next(
        step
        for step in planted["jobs"]["rehearse"]["steps"]
        if "upload-artifact@" in step.get("uses", "")
    )
    upload["with"]["path"] += "\nprobe-evidence.json"
    with pytest.raises(AssertionError):
        _require_ciphertext_carriers(planted)
    script = (ROOT / "scripts/lane3_rehearse.sh").read_text()
    assert "LANE3_EVIDENCE_BUNDLE" not in script
    assert "--evidence-bundle-out evidence-bundle.age" in script
    source = RUNNER.read_text()
    assert '"--evidence-bundle"' not in source
    assert '"apply_under_lock",\n            BLOCKED,' in source
    assert '"provoked_rollback",\n            BLOCKED,' in source


def test_launcher_checks_encryption_before_any_probe_collection() -> None:
    from tests.architecture.lane3_rehearse_script import units

    stages = units("rehearse")
    check = next(
        i
        for i, step in enumerate(stages)
        if "scripts/lane3_evidence_finalization.py" in step["run"]
    )
    collector = next(
        i for i, step in enumerate(stages) if "collect_probe_evidence.sh" in step["run"]
    )
    assert check < collector
    assert (
        stages[check]["run"]
        == ".lane3-foundation/bin/python -E -P scripts/lane3_evidence_finalization.py\n"
    )
    assert "umask 077" in stages[0]["run"]
    body = stages[collector]["run"]
    assert 'mktemp -d "${RUNNER_TEMP:?}/lane3-evidence.XXXXXXXX"' in body
    assert body.index("mktemp -d") < body.index("trap '")
    assert 'rmdir -- "${lane3_probe_directory}"' in body
    assert "set -o noclobber" in body
    assert '> "${lane3_probe_directory}/probe-evidence.json"' in body


@pytest.mark.parametrize("status", [0, 1, 2])
def test_fixed_preflight_cli_preserves_three_statuses(
    monkeypatch: pytest.MonkeyPatch, capsys, status: int
) -> None:
    module = runner.evidence_finalization
    monkeypatch.setattr(module.sys, "argv", ["lane3_evidence_finalization.py"])

    def prepare():
        if status == 1:
            raise module.FinalizationRefused("encryption-policy-invalid")
        if status == 2:
            raise module.FinalizationUnavailable("encryption-policy-unenrolled")
        return object()

    monkeypatch.setattr(module, "prepare_encryption", prepare)
    assert module.main() == status
    captured = capsys.readouterr()
    assert bool(captured.out) is (status == 0)


def test_cli_cannot_select_recipient_or_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    module = runner.evidence_finalization
    monkeypatch.setattr(module.sys, "argv", ["preflight", "--recipient", "arbitrary"])
    monkeypatch.setattr(
        module, "prepare_encryption", lambda: pytest.fail("selected input")
    )
    assert module.main() == 1
