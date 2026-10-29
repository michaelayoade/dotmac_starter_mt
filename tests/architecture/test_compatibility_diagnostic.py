"""Candidate measurement always remains diagnostic, including a green result."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "_compatibility_diagnostic", ROOT / "scripts/compatibility_diagnostic.py"
)
assert SPEC is not None and SPEC.loader is not None
wrapper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wrapper)


@dataclass
class _Subject:
    distribution: str = "dotmac-kernel"
    version: str = "0.1.0a107"


class _Result:
    def __init__(self, satisfied: bool) -> None:
        self.compatibility_satisfied = satisfied
        self.evidence_verified = True
        self.adoption_status = "not_evaluated"
        self.kernel_subject = _Subject()
        self.evaluations = ()

    def explain(self) -> str:
        return "measured result"


@pytest.mark.parametrize("satisfied", [True, False])
def test_both_green_and_refusing_measurements_remain_untrusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, satisfied: bool
) -> None:
    monkeypatch.setattr(wrapper, "_evaluate", lambda path: (_Result(satisfied), {}))
    output = tmp_path / "diagnostic.json"
    status = wrapper.main(["--workspace", str(tmp_path), "--output", str(output)])
    document = json.loads(output.read_bytes())
    assert status == (0 if satisfied else 1)
    assert document["compatibility_satisfied"] is satisfied
    assert document["diagnostic_only"] is True
    assert document["trusted_acceptance"] is False
    assert document["adoption_status"] == "not_evaluated"


def test_acquisition_error_still_writes_untrusted_artifact_and_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(path):
        raise RuntimeError("public source unavailable")

    monkeypatch.setattr(wrapper, "_evaluate", unavailable)
    output = tmp_path / "diagnostic.json"
    assert wrapper.main(["--workspace", str(tmp_path), "--output", str(output)]) == 2
    document = json.loads(output.read_bytes())
    assert document["execution_status"] == "error"
    assert document["diagnostic_only"] is True
    assert document["trusted_acceptance"] is False


def test_mutable_helpers_and_package_initializers_cannot_replace_captured_helpers(
    tmp_path: Path,
) -> None:
    contract = tmp_path / "tools/composition_contract"
    contract.mkdir(parents=True)
    (tmp_path / "tools/__init__.py").write_text(
        "raise AssertionError('candidate init')"
    )
    (contract / "__init__.py").write_text("raise AssertionError('candidate init')")
    for name in wrapper.HELPERS:
        (contract / f"{name}.py").write_text("raise AssertionError('mutable helper')")
    helpers = {name: b"SOURCE = 'captured pinned helper'\n" for name in wrapper.HELPERS}
    source = b"from tools.composition_contract.composition_schema import SOURCE\n"
    with patch.dict(sys.modules):
        loaded = wrapper._load_evaluator(tmp_path, helpers, source)
        assert loaded.SOURCE == "captured pinned helper"
        assert all(
            (contract / f"{name}.py").read_bytes() == helpers[name] for name in helpers
        )


def test_foreign_child_can_reopen_the_exact_captured_gate(tmp_path: Path) -> None:
    helpers = {name: b"SOURCE = 'captured pinned helper'\n" for name in wrapper.HELPERS}
    source = (
        b"import json\nimport sys\nfrom pathlib import Path\n"
        b"if __name__ == '__main__':\n"
        b"    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))\n"
        b"from tools.composition_contract.composition_schema import SOURCE\n"
        b"if __name__ == '__main__':\n"
        b"    print(json.dumps({'path': str(Path(__file__).resolve()), "
        b"'helper': SOURCE, 'argument': sys.argv[1]}))\n"
    )
    with patch.dict(sys.modules):
        loaded = wrapper._load_evaluator(tmp_path, helpers, source)
    gate_path = tmp_path / wrapper.GATE_PATH
    assert gate_path.is_file() and not gate_path.is_symlink()
    assert gate_path.read_bytes() == source
    assert Path(loaded.__file__) == gate_path
    # The production gate's foreign parser launches this exact __file__ with -I.
    child = subprocess.run(  # noqa: S603 - fixed hosted-canary source and interpreter
        [
            sys.executable,
            "-I",
            str(Path(loaded.__file__).resolve()),
            "--imports-worker",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == {
        "path": str(gate_path.resolve()),
        "helper": "captured pinned helper",
        "argument": "--imports-worker",
    }


def test_checkout_head_mismatch_cannot_measure_a_different_product(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wrapper, "_git", lambda *a: b"a" * 40 + b"\n")
    with pytest.raises(ValueError, match="differs from binding"):
        wrapper._require_clone_revision(tmp_path, "b" * 40)


def _workflow_findings(document: dict) -> list[str]:
    findings = []
    if document.get("permissions") != {"contents": "read"}:
        findings.append("permissions")
    if "workflow_call" in document.get(True, {}):
        findings.append("reusable acceptance")
    job = document["jobs"]["diagnostic"]
    steps = job["steps"]
    if "secrets" in json.dumps(document) or "env" in document or "env" in job:
        findings.append("inherited or secret environment")
    for step in steps:
        if "env" in step or "continue-on-error" in step:
            findings.append("step environment or suppressed failure")
    checkouts = [
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    if len(checkouts) != 4:
        findings.append("checkout population")
    for step in checkouts:
        if step["uses"] != "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1":
            findings.append("checkout pin")
        inputs = step.get("with", {})
        if (
            inputs.get("persist-credentials") is not False
            or inputs.get("fetch-depth") != 0
            or inputs.get("fetch-tags") is not True
        ):
            findings.append("checkout credentials/history")
    bindings = json.loads((ROOT / wrapper.BINDINGS_PATH).read_bytes())["bindings"]
    expected = {
        f"michaelayoade/{directory}": bindings[product]["revision"]
        for product, directory in wrapper.PRODUCT_DIRECTORIES.items()
    }
    if {
        step["with"]["repository"]: step["with"]["ref"] for step in checkouts[1:]
    } != expected:
        findings.append("product coordinate mismatch")
    runs = [step["run"] for step in steps if "run" in step]
    if (
        len(runs) != 1
        or "env -i PATH=" not in runs[0]
        or "python3.11 -I scripts/compatibility_diagnostic.py" not in runs[0]
    ):
        findings.append("isolated stdlib execution")
    setup = [
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/setup-python@")
    ]
    if (
        len(setup) != 1
        or setup[0].get("uses")
        != "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
        or setup[0].get("with") != {"python-version": "3.12\n3.11"}
    ):
        findings.append("interpreter setup")
    artifact = steps[-1]
    if (
        artifact.get("uses")
        != "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
    ):
        findings.append("artifact pin")
    if (
        artifact.get("if") != "always()"
        or artifact.get("with", {}).get("if-no-files-found") != "error"
    ):
        findings.append("artifact preservation")
    return findings


def test_workflow_is_secret_free_pinned_and_preserves_failure_artifacts() -> None:
    document = yaml.safe_load(
        (
            ROOT / ".github/workflows/composition-compatibility-diagnostic.yml"
        ).read_text()
    )
    assert _workflow_findings(document) == []
    for plant in (
        "credentials",
        "head",
        "failure",
        "environment",
        "interpreter",
        "artifact",
    ):
        planted = copy.deepcopy(document)
        steps = planted["jobs"]["diagnostic"]["steps"]
        if plant == "credentials":
            steps[0]["with"]["persist-credentials"] = True
        elif plant == "head":
            steps[1]["with"]["ref"] = "main"
        elif plant == "failure":
            steps[-2]["continue-on-error"] = True
        elif plant == "environment":
            planted["env"] = {"TOKEN": "inherited"}
        elif plant == "interpreter":
            steps[4]["with"]["python-version"] = "3.11"
        else:
            steps[-1]["uses"] = "actions/upload-artifact@main"
        assert _workflow_findings(planted), plant
