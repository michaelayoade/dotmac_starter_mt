"""Every allowlisted module's record lineage declaration is valid for the tree.

For each module in `.github/release-modules.json`: resolve its release_shape
exactly as `scripts/release_module.py resolve` does (the same `cmd_resolve`),
take the lineage flag the RELEASE WORKFLOW would pass for that shape (parsed
from the workflow, not re-stated here), and assert the record writer's
preflight accepts it.

Why this exists: dotmac-config-layers 0.1.0a1 (stateless) was added while the
workflow passed `--package-dir` unconditionally. Nothing connected "this
module has no migrations" to "the workflow declares a lineage for it", so the
refusal was found only after publish and tag. With the unconditional-flag
workflow this test fails for config-layers (stateless + --package-dir is
UNREADABLE lineage).
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release-module.yml"


def _load(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RELEASE = _load("release_module")
WRITER = _load("write_release_record")
MODULES = sorted(RELEASE.load_allowlist())


def _resolved(distribution: str) -> dict[str, str]:
    """The facts `release_module.py resolve` derives, via its own functions.

    `cmd_resolve` also refuses a dev version ('+dev') and a dispatch/pyproject
    mismatch; those are not lineage questions, so this calls the allowlist gate
    (`resolve`) and the shared `release_shape` that `cmd_resolve` prints.
    """
    entry = RELEASE.resolve(distribution)
    return {
        "release_shape": RELEASE.release_shape(entry),
        "package_dir": entry["package_dir"],
        "import_name": entry["import_name"],
    }


def _workflow_lineage_flags(shape: str, package_dir: str) -> list[str]:
    """What the workflow's record step would pass for `shape`."""
    run = next(
        step["run"]
        for step in __import__("yaml").safe_load(WORKFLOW.read_text("utf-8"))["jobs"][
            "verify"
        ]["steps"]
        if "scripts/open_release_record_pr.sh" in str(step.get("run"))
    )
    case = re.search(rf"{shape}\)\s+LINEAGE=\((.*?)\)\s*;;", run)
    if case is not None:
        raw = case.group(1)
    else:  # an unconditional declaration, as the workflow had before the fix
        raw = " ".join(re.findall(r'--package-dir "\$PACKAGE_DIR"|--no-lineage', run))
    return raw.replace('"$PACKAGE_DIR"', package_dir).split()


def _versions(distribution: str, package_dir: str) -> list[Path]:
    root = ROOT / package_dir / "src"
    return sorted(
        path
        for path in root.glob("*/migrations/versions/*.py")
        if path.name != "__init__.py"
    )


@pytest.fixture(scope="module")
def head() -> str:
    return subprocess.run(  # noqa: S603
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.mark.parametrize("distribution", MODULES)
def test_workflow_lineage_declaration_is_valid_for_the_tree(
    distribution: str, head: str, tmp_path: Path
) -> None:
    facts = _resolved(distribution)
    shape, package_dir = facts["release_shape"], facts["package_dir"]
    migrations = _versions(distribution, package_dir)
    if shape == "stateless":
        assert not migrations, (
            f"{distribution} is stateless (db_schema null) but ships migrations: "
            f"{[str(m.relative_to(ROOT)) for m in migrations]}"
        )
    else:
        assert migrations, f"{distribution} is stateful but has no migration lineage"

    flags = _workflow_lineage_flags(shape, package_dir)
    expected = (
        ["--no-lineage"] if shape == "stateless" else ["--package-dir", package_dir]
    )
    assert flags == expected, (
        f"release-module.yml would declare {flags} for {distribution} "
        f"({shape}); the writer needs {expected}"
    )

    # A release not yet recorded has a ledger row naming its declared version;
    # that is the release the workflow would run next. A module with no row is
    # fully recorded, and re-using its real (frozen) version would trip the
    # legacy-history refusal, so use a version no tag can yet carry.
    row = json.loads(WRITER.LEDGER.read_text("utf-8"))["unpublished"].get(distribution)
    version = row["declared"] if row else "9.9.9"
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / f"{distribution.replace('-', '_')}-{version}-py3-none-any.whl").write_bytes(
        b"wheel"
    )
    args = (
        dict(zip(flags[::2], flags[1::2], strict=False))
        if "--package-dir" in flags
        else {}
    )
    accepted = WRITER.preflight_record(
        distribution=distribution,
        version=version,
        tag=f"{distribution}-v{version}",
        package_dir=args.get("--package-dir"),
        import_name=facts["import_name"] if args else None,
        no_lineage="--no-lineage" in flags,
        artifact_dir=str(dist),
        expected_run_id="1",
        expected_commit=head,
    )
    assert accepted
