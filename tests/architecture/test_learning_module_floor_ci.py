"""The three new floor probes must execute and retain proof in required CI."""

import re
import shlex
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
NEW_ENTRIES = (
    "packages/dotmac-learning:dotmac_learning::registry",
    "packages/dotmac-fulfillment:dotmac_fulfillment::tenant-audit",
    "packages/dotmac-template-studio:dotmac_template_studio::tenant-audit",
)


def _contract(workflow, shell):
    job = workflow["jobs"]["kernel-floors"]
    assert "MODULES" not in workflow.get("env", {})
    assert "MODULES" not in job.get("env", {})
    assert not job.get("continue-on-error", False)
    assert job.get("if") is None
    assert job["strategy"]["matrix"]["python-version"] == ["3.11", "3.12"]
    steps = job["steps"]
    probes = [s for s in steps if s.get("run") == "bash scripts/module_floor_check.sh"]
    assert len(probes) == 1
    probe = probes[0]
    assert probe.get("if") is None and not probe.get("continue-on-error", False)
    assert "MODULES" not in probe["env"]  # Required CI uses the complete default.
    evidence = "${{ runner.temp }}/module-floor-evidence"
    assert probe["env"]["MODULE_FLOOR_EVIDENCE_DIR"] == evidence
    uploads = [
        s
        for s in steps
        if s.get("uses", "").startswith("actions/upload-artifact@")
        and s.get("with", {}).get("path") == evidence + "/"
    ]
    assert len(uploads) == 1 and steps.index(uploads[0]) > steps.index(probe)
    upload = uploads[0]
    assert upload.get("if") == "always()"
    assert not upload.get("continue-on-error", False)
    assert upload["with"]["if-no-files-found"] == "error"
    assert (
        upload["with"]["name"] == "module-floor-evidence-${{ matrix.python-version }}"
    )
    assert re.fullmatch(r"actions/upload-artifact@[0-9a-f]{40}", upload["uses"])
    default = re.findall(r'^MODULES="\$\{MODULES:-(.*)\}"$', shell, re.MULTILINE)
    assert len(default) == 1
    entries = shlex.split(default[0])
    assert len(entries) == len(set(entries))
    assert set(NEW_ENTRIES) <= set(entries)


def test_three_module_floor_probes_and_artifacts_are_required():
    _contract(
        yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text()),
        (ROOT / "scripts/module_floor_check.sh").read_text(),
    )


@pytest.mark.parametrize("entry", NEW_ENTRIES)
def test_omitting_a_new_module_from_required_ci_is_detected(entry):
    shell = (ROOT / "scripts/module_floor_check.sh").read_text()
    assert shell.count(entry) == 1
    with pytest.raises(AssertionError):
        _contract(
            yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text()),
            shell.replace(" " + entry, ""),
        )


def test_dropping_retained_floor_evidence_is_detected():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["kernel-floors"]["steps"]
    workflow["jobs"]["kernel-floors"]["steps"] = [
        s for s in steps if s.get("name") != "Retain module floor wheels and receipts"
    ]
    with pytest.raises(AssertionError):
        _contract(workflow, (ROOT / "scripts/module_floor_check.sh").read_text())
