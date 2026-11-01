"""The record writer's preflight: refusals surface BEFORE anything is published.

dotmac-config-layers 0.1.0a1 (stateless) built, published and tagged, and only
then did the record step refuse (--package-dir for a module with no lineage).
A tag without a record turns main red. `--preflight` runs the tag-independent
half of the writer against the just-built dist so the same refusal happens in
the build job, before publication.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "write_release_record.py"
WORKFLOWS = ROOT / ".github" / "workflows"


def _writer():
    spec = importlib.util.spec_from_file_location("write_release_record", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _head() -> str:
    return subprocess.run(  # noqa: S603
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _stateless(writer) -> str:
    """A governed module that carries no lineage, read from the real tree."""
    anchored = writer.anchored_distributions(
        writer.RELEASED_TAGS_MODULE.read_text(encoding="utf-8")
    )
    return next(
        name for name in sorted(writer.module_release_targets()) if name not in anchored
    )


def _inputs(writer, tmp_path: Path, distribution: str):
    row = json.loads(writer.LEDGER.read_text("utf-8"))["unpublished"].get(distribution)
    version = row["declared"] if row else "9.9.9"
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / f"{distribution.replace('-', '_')}-{version}-py3-none-any.whl").write_bytes(
        b"wheel"
    )
    return {
        "distribution": distribution,
        "version": version,
        "tag": f"{distribution}-v{version}",
        "package_dir": None,
        "import_name": None,
        "no_lineage": False,
        "artifact_dir": str(dist),
        "expected_run_id": "1",
        "expected_commit": _head(),
    }


def test_preflight_accepts_a_stateless_module_declared_no_lineage(tmp_path) -> None:
    writer = _writer()
    kwargs = _inputs(writer, tmp_path, _stateless(writer))
    kwargs["no_lineage"] = True
    passed = writer.preflight_record(**kwargs)
    assert any("lineage" in line for line in passed)


def test_preflight_refuses_package_dir_for_a_stateless_module(tmp_path) -> None:
    """The config-layers incident, refused before publication."""
    writer = _writer()
    distribution = _stateless(writer)
    kwargs = _inputs(writer, tmp_path, distribution)
    kwargs["package_dir"] = f"packages/{distribution}"
    kwargs["import_name"] = distribution.replace("-", "_")
    with pytest.raises(writer.ReleaseRecordError, match="UNREADABLE lineage"):
        writer.preflight_record(**kwargs)


def test_preflight_refuses_a_missing_lineage_declaration(tmp_path) -> None:
    writer = _writer()
    kwargs = _inputs(writer, tmp_path, _stateless(writer))
    with pytest.raises(writer.ReleaseRecordError, match="MISSING lineage"):
        writer.preflight_record(**kwargs)


def test_preflight_refuses_no_lineage_for_an_anchored_module(tmp_path) -> None:
    writer = _writer()
    anchored = sorted(
        writer.anchored_distributions(
            writer.RELEASED_TAGS_MODULE.read_text(encoding="utf-8")
        )
        & writer.module_release_targets()
    )
    assert anchored, "no governed module carries a lineage"
    kwargs = _inputs(writer, tmp_path, anchored[0])
    kwargs["no_lineage"] = True
    with pytest.raises(writer.ReleaseRecordError, match="CONTRADICTORY"):
        writer.preflight_record(**kwargs)


def test_preflight_refuses_a_missing_or_wrong_wheel(tmp_path) -> None:
    writer = _writer()
    kwargs = _inputs(writer, tmp_path, _stateless(writer))
    kwargs["no_lineage"] = True
    for wheel in Path(kwargs["artifact_dir"]).glob("*.whl"):
        wheel.rename(wheel.with_name("other-1.0-py3-none-any.whl"))
    with pytest.raises(writer.ReleaseRecordError, match="does not bind"):
        writer.preflight_record(**kwargs)


def test_preflight_refuses_a_tag_version_mismatch_and_a_missing_commit(
    tmp_path,
) -> None:
    writer = _writer()
    kwargs = _inputs(writer, tmp_path, _stateless(writer))
    kwargs["no_lineage"] = True
    with pytest.raises(writer.ReleaseRecordError, match="identity mismatch"):
        writer.preflight_record(**{**kwargs, "tag": "x-v1"})
    with pytest.raises(writer.ReleaseRecordError, match="--expected-commit"):
        writer.preflight_record(**{**kwargs, "expected_commit": None})


def test_preflight_writes_nothing_and_needs_no_tag(tmp_path, monkeypatch) -> None:
    writer = _writer()
    kwargs = _inputs(writer, tmp_path, _stateless(writer))
    kwargs["no_lineage"] = True

    def forbidden(*_a, **_k):
        raise AssertionError("preflight must not consult the tag")

    for name in ("tag_commit", "annotated_tag_object", "annotated_tag_message"):
        monkeypatch.setattr(writer, name, forbidden)
    monkeypatch.setattr(writer, "published_release_history", forbidden)
    before = {
        path: path.read_bytes()
        for path in (
            writer.LEDGER,
            writer.RELEASED_TAGS_MODULE,
            writer.SOURCE_DRIFT_BASELINE,
            writer.MODULE_RELEASE_VERIFICATIONS,
        )
    }
    writer.preflight_record(**kwargs)
    assert {path: path.read_bytes() for path in before} == before


def test_cli_preflight_states_what_it_skipped(tmp_path, capsys) -> None:
    writer = _writer()
    kwargs = _inputs(writer, tmp_path, _stateless(writer))
    code = writer.main(
        [
            "--preflight",
            "--distribution", kwargs["distribution"],
            "--version", kwargs["version"],
            "--tag", kwargs["tag"],
            "--no-lineage",
            "--artifact-dir", kwargs["artifact_dir"],
            "--expected-run-id", "1",
            "--expected-commit", kwargs["expected_commit"],
        ]
    )  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0
    assert "SKIPPED (needs the tag)" in out and "ModuleReleaseTagEvidence" in out
    assert "nothing written" in out


def test_cli_preflight_refusal_exits_nonzero(tmp_path, capsys) -> None:
    writer = _writer()
    distribution = _stateless(writer)
    kwargs = _inputs(writer, tmp_path, distribution)
    code = writer.main(
        [
            "--preflight",
            "--distribution", distribution,
            "--version", kwargs["version"],
            "--tag", kwargs["tag"],
            "--package-dir", f"packages/{distribution}",
            "--artifact-dir", kwargs["artifact_dir"],
            "--expected-run-id", "1",
            "--expected-commit", kwargs["expected_commit"],
        ]
    )  # fmt: skip
    assert code == 1
    assert "preflight REFUSED" in capsys.readouterr().err


def _steps(name: str) -> list[dict]:
    wf = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return [s for job in wf["jobs"].values() for s in job.get("steps", [])]


def test_release_module_preflights_in_build_before_any_publish() -> None:
    wf = yaml.safe_load((WORKFLOWS / "release-module.yml").read_text("utf-8"))
    names = [s.get("name") for s in wf["jobs"]["build"]["steps"]]
    assert "Preflight the release record" in names
    assert names.index("Inspect artifacts") < names.index(
        "Preflight the release record"
    )
    step = next(
        s
        for s in wf["jobs"]["build"]["steps"]
        if s.get("name", "").startswith("Preflight")
    )
    assert "if" not in step, "preflight must not be skippable"
    assert "--preflight" in step["run"]
    assert "FORGEJO" not in str(step), "the build job holds no registry credential"


def test_recovery_preflights_before_it_tags() -> None:
    names = [s.get("name") for s in _steps("recover-module-release.yml")]
    assert names.index("Preflight the release record") < names.index(
        "Tag the recovered release"
    )


def test_preflight_and_record_steps_declare_the_same_lineage() -> None:
    for name in ("release-module.yml", "recover-module-release.yml"):
        steps = _steps(name)
        pre = next(s for s in steps if s.get("name") == "Preflight the release record")
        rec = next(
            s for s in steps if "scripts/open_release_record_pr.sh" in str(s.get("run"))
        )

        def case(run: str) -> str:
            start = run.index('case "$RELEASE_SHAPE" in')
            return run[start : run.index("esac", start)]

        assert case(pre["run"]) == case(rec["run"]).replace(
            "release record REFUSED", "release record preflight REFUSED"
        ), name
