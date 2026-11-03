"""F1/F2 end to end: rules-driven drafting, the adapter and the scale contract."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import resource
import subprocess
import sys
import time
from pathlib import Path

import pytest
from dotmac_billing import opening_receivable_validation as validation
from dotmac_billing.opening_receivable_validation import (
    opening_account_import_blockers_v2,
    preview_opening_account_import_batch_v2,
    preview_opening_account_import_v2,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

import billing_opening_scale_fixture as fixture  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


drafter = _load("billing_opening_assembly_drafter")
adapter = _load("billing_opening_rehearsal_adapter")


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return path


def _files(tmp_path: Path, partitions: int, invoices: int) -> dict[str, Path]:
    data = fixture.build_scale_inputs(partitions, invoices)
    return {
        "census": _write(tmp_path / "census.json", data["census"]),
        "a10": _write(tmp_path / "a10.json", data["a10"]),
        "rules": _write(tmp_path / "rules.json", data["rules"]),
        "target": _write(tmp_path / "target.json", data["target_state"]),
    }


def _draft(files: dict[str, Path], out: Path, *, rules: bool = True) -> int:
    out.mkdir()
    args = [
        "--census", str(files["census"]),
        "--target-state", str(files["target"]),
        "--out-dir", str(out),
    ]  # fmt: skip
    if rules:
        args += ["--rules", str(files["rules"]), "--a10", str(files["a10"])]
    return drafter.main(args)


def _adapt(files, assembly: Path, out: Path, decisions: Path | None = None) -> dict:
    args = [
        "--census", str(files["census"]),
        "--assembly", str(assembly),
        "--out", str(out),
        "--rules", str(files["rules"]),
        "--a10", str(files["a10"]),
    ]  # fmt: skip
    if decisions:
        args += ["--decisions-out", str(decisions)]
    adapter.main(args)
    return json.loads(out.read_text(encoding="utf-8"))


def test_the_census_rule_admits_nothing_but_the_rules_admit_the_account_set(tmp_path):
    files = _files(tmp_path, 400, 3156)
    expected = fixture.build_scale_inputs(400, 3156)["expected"]
    # Without rules the drafter copies the census's own (empty) eligible list.
    assert _draft(files, tmp_path / "plain", rules=False) == 0
    plain = json.loads((tmp_path / "plain" / "assembly.json").read_text())
    assert plain["invoice_mappings"] == []
    # With rules the eligible set is the admitted accounts' status-open invoices.
    assert _draft(files, tmp_path / "ruled") == 0
    ruled = json.loads((tmp_path / "ruled" / "assembly.json").read_text())
    assert len(ruled["invoice_mappings"]) == expected["eligible_invoices"] > 0
    decisions = json.loads((tmp_path / "ruled" / "admissibility.json").read_text())
    counts = decisions["counts"]
    assert counts["eligible_invoices"] == expected["eligible_invoices"]
    assert counts["admitted"] + counts["held"] == counts["partitions"] == 400
    # exactly one reason per held partition, none for an admitted one
    for row in decisions["decisions"]:
        assert (row["reason"] is None) == row["admitted"]
    assert decisions["archived_drafts"] and counts["archived_drafts"] > 0
    summary = (tmp_path / "ruled" / "summary.csv").read_text()
    assert "admissibility_digest,rules," in summary and "\nhold," in summary


def test_drafter_and_adapter_are_byte_identical_across_runs(tmp_path):
    files = _files(tmp_path, 300, 2367)
    for name in ("one", "two"):
        _draft(files, tmp_path / name)
    for artifact in ("assembly.json", "summary.csv", "admissibility.json"):
        assert (tmp_path / "one" / artifact).read_bytes() == (
            tmp_path / "two" / artifact
        ).read_bytes()
    asm = tmp_path / "one" / "assembly.json"
    first = _adapt(files, asm, tmp_path / "a1.json", tmp_path / "d1.json")
    second = _adapt(files, asm, tmp_path / "a2.json", tmp_path / "d2.json")
    assert (tmp_path / "a1.json").read_bytes() == (tmp_path / "a2.json").read_bytes()
    assert (tmp_path / "d1.json").read_bytes() == (tmp_path / "d2.json").read_bytes()
    # the adapter and the drafter reach the same admissibility decisions
    assert (tmp_path / "d1.json").read_text().replace(
        '"originals_evaluated": true', '"originals_evaluated": false'
    ) == (tmp_path / "one" / "admissibility.json").read_text()
    assert first == second
    receivable = [p for p in first["partitions"] if p["receivable_residual_minor"]]
    assert receivable, "admitted accounts with status-open documents are imported"
    held = json.loads((tmp_path / "one" / "admissibility.json").read_text())
    counts = held["counts"]
    # admitted plus held-but-in-the-frozen-manifest (post-seal route)
    assert (
        len(first["partitions"])
        == counts["admitted"] + counts["route:post_seal_admission"]
    )


def test_manifest_wide_findings_are_not_replayed_per_partition(tmp_path):
    files = _files(tmp_path, 300, 2367)
    _draft(files, tmp_path / "d")
    artifact = _adapt(files, tmp_path / "d" / "assembly.json", tmp_path / "a.json")
    per_partition = {
        b["code"]: b["count"] for p in artifact["partitions"] for b in p["blockers"]
    }
    # every partition cannot carry every manifest-sized reference list
    assert max(per_partition.values()) < 300 * 20
    size = (tmp_path / "a.json").stat().st_size
    assert size < 40_000 * 300, size


def test_batch_validation_equals_the_one_command_at_a_time_path(tmp_path):
    files = _files(tmp_path, 60, 473)
    _draft(files, tmp_path / "d")
    assembly = adapter.load_assembly(
        json.loads((tmp_path / "d/assembly.json").read_text())
    )
    census = json.loads(files["census"].read_text())
    from dotmac_billing.opening_admissibility import (
        parse_a10_classes,
        parse_admissibility_rules,
        project_census,
    )

    projection = project_census(
        census,
        rules=parse_admissibility_rules(json.loads(files["rules"].read_text())),
        a10=parse_a10_classes(json.loads(files["a10"].read_text())),
        original_invoice_ids=None,
    )
    obs, sidecar = adapter.load_sub_observation(projection.census)
    run = adapter.assemble_rehearsal_run(obs, assembly=assembly, sidecar=sidecar)
    commands = list(run.commands)
    assert len(commands) > 5
    batch = preview_opening_account_import_batch_v2(commands)
    for command, item in zip(commands, batch.items, strict=True):
        assert item.preview == preview_opening_account_import_v2(command)
        full = opening_account_import_blockers_v2(command)
        previewed = item.preview.blockers[: len(full)]
        assert previewed == full
        assert item.preview.blockers[len(full) :] == item.verification_blockers
        # every partition-scoped or manifest-wide finding is one of the full ones
        full_refs = {(b.code, r) for b in full for r in b.references}
        scoped = {(b.code, r) for b in item.partition_blockers for r in b.references}
        manifest = {(b.code, r) for b in batch.manifest_blockers for r in b.references}
        assert scoped <= full_refs and manifest <= full_refs


def test_the_manifest_is_validated_once_per_run_not_once_per_partition(
    tmp_path, monkeypatch
):
    files = _files(tmp_path, 200, 1578)
    _draft(files, tmp_path / "d")
    analyses = []
    original = validation._ReceivableManifestAnalysis.__init__

    def counting(self, manifest):
        analyses.append(len(manifest.dispositions))
        original(self, manifest)

    monkeypatch.setattr(validation._ReceivableManifestAnalysis, "__init__", counting)
    fingerprints = []
    original_fp = validation.opening_manifest_fingerprint_v2
    monkeypatch.setattr(
        validation,
        "opening_manifest_fingerprint_v2",
        lambda m: fingerprints.append(1) or original_fp(m),
    )
    artifact = _adapt(files, tmp_path / "d" / "assembly.json", tmp_path / "a.json")
    assert len(artifact["partitions"]) > 100
    assert len(analyses) == 1
    assert len(fingerprints) == 1


def test_mixed_manifest_batches_are_refused():
    from dotmac_billing.opening_contracts import OpeningEvidenceError

    with pytest.raises(OpeningEvidenceError):
        # two distinct manifest objects cannot share one validation
        class _C:  # pragma: no cover - rejected before use
            manifest = object()

        validation.preview_opening_account_import_batch_v2([_C(), _C()])  # type: ignore[list-item]


def _cmd(script: str, **flags: Path) -> list[str]:
    command = [sys.executable, str(ROOT / "scripts" / f"{script}.py")]
    for name, value in flags.items():
        command += [f"--{name.replace('_', '-')}", str(value)]
    return command


# -- full-scale contract (Phase B entry numbers) --------------------------------

PHASE_B_SECONDS = 1_800
PHASE_B_RSS_BYTES = 6 * 1024**3


@pytest.mark.slow
def test_full_scale_fixture_meets_the_phase_b_entry_budget(tmp_path):
    """15,293 partitions / 120,670 invoices: <= 1,800 s, <= 6 GiB, identical x2."""
    data = fixture.build_scale_inputs()
    assert data["expected"]["partitions"] == fixture.FULL_PARTITIONS
    assert data["expected"]["invoices"] == fixture.FULL_INVOICES
    files = {
        "census": _write(tmp_path / "census.json", data["census"]),
        "a10": _write(tmp_path / "a10.json", data["a10"]),
        "rules": _write(tmp_path / "rules.json", data["rules"]),
        "target": _write(tmp_path / "target.json", data["target_state"]),
    }
    del data
    digests = []
    for run in ("r1", "r2"):
        out = tmp_path / run
        out.mkdir()
        started = time.monotonic()
        before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        steps = [
            _cmd(
                "billing_opening_assembly_drafter",
                census=files["census"],
                target_state=files["target"],
                out_dir=out,
                rules=files["rules"],
                a10=files["a10"],
            ),
            _cmd(
                "billing_opening_rehearsal_adapter",
                census=files["census"],
                assembly=out / "assembly.json",
                out=out / "census-artifact.json",
                rules=files["rules"],
                a10=files["a10"],
                decisions_out=out / "decisions.json",
            ),
        ]
        for step in steps:
            done = subprocess.run(  # noqa: S603
                step,
                capture_output=True,
                text=True,
                timeout=PHASE_B_SECONDS,
                check=False,
            )
            assert done.returncode in (0, 1), done.stdout[-400:] + done.stderr[-400:]
        elapsed = time.monotonic() - started
        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        # ru_maxrss is bytes on macOS and KiB on Linux
        peak_bytes = peak if sys.platform == "darwin" else peak * 1024
        assert before <= peak
        assert elapsed <= PHASE_B_SECONDS, elapsed
        assert peak_bytes <= PHASE_B_RSS_BYTES, peak_bytes
        digests.append(
            tuple(
                hashlib.sha256((out / name).read_bytes()).hexdigest()
                for name in (
                    "assembly.json",
                    "summary.csv",
                    "admissibility.json",
                    "census-artifact.json",
                    "decisions.json",
                )
            )
        )
        print(f"scale run {run}: {elapsed:.1f}s peak {peak_bytes / 1024**3:.2f}GiB")
    assert digests[0] == digests[1]


def _run_for(partitions: int, invoices: int, tmp_path: Path):
    from dotmac_billing.opening_admissibility import (
        parse_a10_classes,
        parse_admissibility_rules,
        project_census,
    )

    data = fixture.build_scale_inputs(partitions, invoices)
    projection = project_census(
        data["census"],
        rules=parse_admissibility_rules(data["rules"]),
        a10=parse_a10_classes(data["a10"]),
        original_invoice_ids=None,
    )
    draft = drafter.draft_assembly(
        data["census"], data["target_state"], admissibility=projection
    )
    obs, sidecar = adapter.load_sub_observation(projection.census)
    run = adapter.assemble_rehearsal_run(
        obs,
        assembly=adapter.load_assembly(draft.assembly),
        sidecar=sidecar,
        held_reasons=projection.held_invoice_reasons,
    )
    return projection, run


def test_held_partitions_stay_in_the_manifest_without_blocking_the_others(tmp_path):
    projection, run = _run_for(200, 1578, tmp_path)
    held = {
        (d.account_id) for d in projection.decisions if d.route == "post_seal_admission"
    }
    assert held, "the fixture must hold some non-structural partitions"
    batch = preview_opening_account_import_batch_v2(list(run.commands))
    codes = {b.code for b in batch.manifest_blockers}
    assert "source_blocking_disposition" not in codes
    refused = {
        item.preview.partition.source_account_key
        for item in batch.items
        if any(b.code == "partition_held" for b in item.partition_blockers)
    }
    # a held partition with open residual is marked; one with nothing to
    # admit later has no marker and simply recognizes no backing
    expected = {
        d.partition.source_account_key
        for d in run.manifest.dispositions
        if d.disposition == "blocking" and d.reason.startswith("held:")
    }
    assert expected and refused == expected
    # held open invoices carry the digest-bound marker as their disposition
    marked = {
        d.reason
        for d in run.manifest.dispositions
        if d.disposition == "blocking" and d.reason.startswith("held:")
    }
    assert marked and all(
        r in {f"held:{x.reason}" for x in projection.decisions} for r in marked
    )


def test_import_cost_does_not_depend_on_manifest_size_per_command(
    tmp_path, monkeypatch
):
    from dotmac_billing.opening_receivable_validation import (
        opening_account_command_digest_v2,
    )

    _projection, run = _run_for(200, 1578, tmp_path)
    builds = []
    original = validation._ReceivableManifestAnalysis.__init__

    def counting(self, manifest):
        builds.append(1)
        original(self, manifest)

    monkeypatch.setattr(validation._ReceivableManifestAnalysis, "__init__", counting)
    digests = set()
    for command in run.commands:
        opening_account_import_blockers_v2(command)  # as service.import does
        digests.add(opening_account_command_digest_v2(command))
    assert len(builds) <= 1  # one analysis for the shared manifest object
    assert len(digests) == len(run.commands)  # still partition-specific


@pytest.mark.slow
def test_full_scale_import_validation_pass_is_linear(tmp_path):
    """A 15,293-command import pass: the per-command validation + digest cost."""
    from dotmac_billing.opening_receivable_validation import (
        opening_account_command_digest_v2,
    )

    _projection, run = _run_for(
        fixture.FULL_PARTITIONS, fixture.FULL_INVOICES, tmp_path
    )
    commands = list(run.commands)
    assert len(commands) > 13_000
    started = time.monotonic()
    digests = set()
    for command in commands:
        opening_account_import_blockers_v2(command)
        digests.add(opening_account_command_digest_v2(command))
    elapsed = time.monotonic() - started
    print(f"import pass: {len(commands)} commands in {elapsed:.1f}s")
    assert len(digests) == len(commands)
    assert elapsed <= 300, elapsed  # the quadratic path needs hours
