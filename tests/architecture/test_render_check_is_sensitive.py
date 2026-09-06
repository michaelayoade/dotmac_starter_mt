"""The render comparison is proven to bite, and proven to stay silent.

ADR 0014 names two artefacts: the DECLARATION (`deploy/product.toml`, which
must carry no environment fact) and the RENDER (that declaration plus one
environment, committed under `deploy/rendered/` and compared byte-for-byte by a
named workflow). "A render nobody compares is a deployment nobody approved."

The comparison itself is `dotmac-deploy render --check`, and it already ran in
`ci.yml`'s `quality` matrix by way of `make deployment-check`. What did NOT
exist was evidence that the comparison can fail. A check that passes over a
clean tree proves nothing about itself: `deploy/rendered/` has been in
agreement with `deploy/product.toml` for as long as both have existed, so every
green run to date is equally consistent with a check that compares nothing.

So each test below plants a specific defect and requires it to be NAMED, and
two of them plant a near-miss and require SILENCE. The near-misses are the half
that catches an over-broad check: a comparison that fires on a touched mtime,
or on any new file anywhere under `deploy/`, would satisfy every positive test
here and be useless in review.

THE EMPTY-SET CASE IS ITS OWN TEST, AND IT IS THE ONE THAT MATTERS MOST.
A comparison whose expected set is drawn from the directory it is checking
passes vacuously when that directory is empty — declare nothing, render
nothing, go green. `cmd_render`'s check arm draws the set from the DESCRIPTOR
instead, so an emptied or absent `deploy/rendered/` is three `missing` findings
rather than a pass. That is the property, and it is proven here rather than
read out of the source.

NOTHING HERE INSTALLS ANYTHING. The facility has zero runtime dependencies, so
every subprocess below is `sys.executable -m dotmac_deployment_foundation.cli`
against the source tree. That is also why the workflow this file guards needs
no published wheel — see `.github/workflows/deployment-render-check.yml`.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
FOUNDATION_SRC = REPO / "packages" / "dotmac-deployment-foundation" / "src"
DESCRIPTOR = REPO / "deploy" / "product.toml"
THRESHOLDS = REPO / "deploy" / "alerts" / "thresholds.json"
RENDERED = REPO / "deploy" / "rendered"
PROFILE = REPO / ".dotmac" / "standards-profile.json"
MAKEFILE = REPO / "Makefile"

EXIT_OK = 0
EXIT_REFUSED = 1


def _run(*args: str, hash_seed: str = "0") -> subprocess.CompletedProcess[str]:
    """Drive the real CLI in a fresh process.

    A fresh process rather than an in-process call, because same-process
    determinism is exactly the property that cannot see a hash-derived
    ordering: `PYTHONHASHSEED` is fixed at interpreter start and a second call
    inside one process reuses the first one's seed.
    """
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "dotmac_deployment_foundation.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPO,
        # The ambient environment with two overrides, rather than a wiped
        # one. `PYTHONHASHSEED` is the variable under test and `PYTHONPATH`
        # is how the facility is reached without installing it; clearing
        # everything else would make this test depend on the runner's
        # default locale and home directory instead of on the renderer.
        env={
            **os.environ,
            "PYTHONPATH": str(FOUNDATION_SRC),
            "PYTHONHASHSEED": hash_seed,
        },
    )


def _check(output_dir: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return _run(
        "-f",
        str(DESCRIPTOR),
        "render",
        "--check",
        "-o",
        str(output_dir),
        "--thresholds",
        str(THRESHOLDS),
    )


def _render(output_dir: pathlib.Path, *, hash_seed: str = "0") -> None:
    result = _run(
        "-f",
        str(DESCRIPTOR),
        "render",
        "-o",
        str(output_dir),
        "--thresholds",
        str(THRESHOLDS),
        hash_seed=hash_seed,
    )
    assert result.returncode == EXIT_OK, result.stderr


@pytest.fixture
def rendered_copy(tmp_path: pathlib.Path) -> pathlib.Path:
    """A throwaway copy of the committed assets, for planting defects in.

    The repository's own `deploy/rendered/` is never mutated. A test that
    edited it and restored it would leave the tree wrong whenever it failed
    part-way, which is precisely when somebody is reading the tree.
    """
    target = tmp_path / "rendered"
    shutil.copytree(RENDERED, target)
    return target


# ── the control ─────────────────────────────────────────────────────────────


def test_the_committed_assets_are_what_the_declaration_renders() -> None:
    """The control. On its own it proves nothing; every test below needs it."""
    result = _check(RENDERED)
    assert result.returncode == EXIT_OK, result.stdout + result.stderr


# ── planted defects, each of which must be NAMED ────────────────────────────


def test_a_changed_rendered_file_is_named(rendered_copy: pathlib.Path) -> None:
    victim = rendered_copy / "docker-compose.yml"
    victim.write_text(victim.read_text(encoding="utf-8") + "\n# hand edit\n")

    result = _check(rendered_copy)

    assert result.returncode == EXIT_REFUSED, result.stdout
    assert "docker-compose.yml" in result.stderr
    assert "differs" in result.stderr


def test_a_committed_file_nothing_renders_is_named(
    rendered_copy: pathlib.Path,
) -> None:
    """The second direction, and the one a naive check omits.

    A comparison that walks the descriptor's assets and stops there reports
    every changed file and never reports the file somebody added by hand. That
    file carries configuration nothing approves, and it is the shape a
    hand-patched host produces.
    """
    (rendered_copy / "unapproved.yml").write_text("services: {}\n")

    result = _check(rendered_copy)

    assert result.returncode == EXIT_REFUSED, result.stdout
    assert "unapproved.yml" in result.stderr
    assert "not rendered by this descriptor" in result.stderr


def test_a_deleted_rendered_file_is_named(rendered_copy: pathlib.Path) -> None:
    (rendered_copy / "alerts.rules.yml").unlink()

    result = _check(rendered_copy)

    assert result.returncode == EXIT_REFUSED, result.stdout
    assert "alerts.rules.yml" in result.stderr
    assert "missing" in result.stderr


def test_an_emptied_rendered_directory_refuses(rendered_copy: pathlib.Path) -> None:
    """The vacuous-pass case, stated as its own test.

    Nothing is left to compare, and a comparison whose expected set came from
    this directory would report zero differences over zero files and exit 0.
    """
    for member in sorted(rendered_copy.rglob("*")):
        if member.is_file():
            member.unlink()

    result = _check(rendered_copy)

    assert result.returncode == EXIT_REFUSED, (
        "an empty rendered set must REFUSE. It passed, which means the expected "
        "set is drawn from the directory being checked rather than from the "
        "descriptor, and a product could go green by rendering nothing"
    )
    assert "missing" in result.stderr


def test_an_absent_rendered_directory_refuses(tmp_path: pathlib.Path) -> None:
    """Absent is not the same code path as empty, so it gets its own test."""
    result = _check(tmp_path / "never-created")

    assert result.returncode == EXIT_REFUSED, (
        "a rendered directory that does not exist must REFUSE, not pass for "
        "want of anything to disagree with"
    )
    assert "missing" in result.stderr


# ── near-misses, each of which must be met with SILENCE ─────────────────────


def test_a_touched_but_unchanged_file_is_not_drift(
    rendered_copy: pathlib.Path,
) -> None:
    """Content, not metadata.

    A check comparing mtimes or a manifest of stat results would fire here, and
    would then fire on every fresh checkout — which is how a real gate gets
    disabled.
    """
    victim = rendered_copy / "docker-compose.yml"
    victim.write_text(victim.read_text(encoding="utf-8"), encoding="utf-8")

    result = _check(rendered_copy)

    assert result.returncode == EXIT_OK, result.stdout + result.stderr


def test_a_file_outside_the_rendered_directory_is_not_drift(
    tmp_path: pathlib.Path,
) -> None:
    """The stray detector is SCOPED, and that scoping is the near-miss.

    `deploy/` holds an alert-threshold file, a Forgejo compose for a different
    host, and rehearsal fixtures — none of them rendered by this descriptor and
    none of them a finding. A stray check that walked `deploy/` instead of
    `deploy/rendered/` would refuse the repository as it stands.
    """
    enclosing = tmp_path / "deploy"
    shutil.copytree(RENDERED, enclosing / "rendered")
    (enclosing / "sibling.yml").write_text("not a rendered asset\n")

    result = _check(enclosing / "rendered")

    assert result.returncode == EXIT_OK, result.stdout + result.stderr


# ── reproducibility across processes, not merely within one ─────────────────


def test_two_processes_with_different_hash_seeds_render_the_same_bytes(
    tmp_path: pathlib.Path,
) -> None:
    """`render_compose(spec) == render_compose(spec)` cannot see this.

    A set iterated without `sorted()`, or a dict keyed by an object whose hash
    varies, is stable inside one interpreter and varies between two. The
    facility's own tests compare two calls in one process, so they agree by
    construction on exactly the orderings this test can disagree on.
    """
    first = tmp_path / "seed-zero"
    second = tmp_path / "seed-other"
    _render(first, hash_seed="0")
    _render(second, hash_seed="524287")

    left = {
        path.relative_to(first).as_posix(): path.read_bytes()
        for path in sorted(first.rglob("*"))
        if path.is_file()
    }
    right = {
        path.relative_to(second).as_posix(): path.read_bytes()
        for path in sorted(second.rglob("*"))
        if path.is_file()
    }

    assert left.keys() == right.keys()
    for name in sorted(left):
        assert left[name] == right[name], (
            f"{name} differs between two processes whose only difference is "
            "PYTHONHASHSEED. Some ordering in the renderer is hash-derived"
        )


def test_a_fresh_render_reproduces_the_committed_bytes(
    tmp_path: pathlib.Path,
) -> None:
    """Belt to `test_the_committed_assets_are_what_the_declaration_renders`'s
    braces, and not a duplicate of it: that test asks the tool whether it
    agrees, this one compares the bytes without the tool's help. If the check
    arm ever stops reading files, this one still fails.
    """
    fresh = tmp_path / "fresh"
    _render(fresh, hash_seed="524287")

    committed = {
        path.relative_to(RENDERED).as_posix(): path.read_bytes()
        for path in sorted(RENDERED.rglob("*"))
        if path.is_file()
    }
    produced = {
        path.relative_to(fresh).as_posix(): path.read_bytes()
        for path in sorted(fresh.rglob("*"))
        if path.is_file()
    }

    assert produced == committed


# ── the declaration points at a workflow that actually runs ─────────────────


def _surface() -> dict[str, object]:
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    surfaces = profile["deployment_artefact_surfaces"]
    assert len(surfaces) == 1, (
        "this file assumes the Starter's single deployment surface; a second "
        "one needs its own render-check workflow, not a shared assertion"
    )
    surface: dict[str, object] = surfaces[0]
    return surface


def test_the_declared_render_check_workflow_runs_on_a_pull_request() -> None:
    """Governance's `deployment.render-check.absent` is a TEXTUAL check.

    It reads the declared workflow and looks for the declared command. It
    cannot tell whether that workflow ever executes — and for the whole life of
    this declaration it did not: the pointer named
    `deployment-conformance.yml`, which is `workflow_call`-only, whose single
    caller `deployment-adopter.yml` is `workflow_dispatch`-only and pinned to a
    foundation version nothing has ever published. Green, over a comparison
    that ran on nobody's merge.

    This is the assertion Governance structurally cannot make.
    """
    surface = _surface()
    workflow_path = REPO / str(surface["render_check_workflow"])
    assert workflow_path.is_file(), workflow_path

    document = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    # `on` is YAML 1.1's boolean true when unquoted, which is why this reads
    # both spellings rather than assuming the one PyYAML happens to produce.
    triggers = document.get("on", document.get(True))
    assert isinstance(triggers, dict), triggers
    assert "pull_request" in triggers, (
        f"{workflow_path.name} is the declared render-check workflow and does "
        "not run on a pull request. A comparison nobody's merge waits on is "
        "the same as no comparison"
    )
    assert "workflow_call" not in triggers, (
        f"{workflow_path.name} is `workflow_call`, so whether it runs depends "
        "on a caller this assertion cannot see"
    )


def test_the_declared_workflow_runs_the_declared_command_for_real() -> None:
    """Not merely that the string appears — that a `run:` step executes it.

    Governance's check reads the whole file, so a command sitting in a comment
    satisfies it. That is the one way to make this declaration green without
    comparing anything, and it is closed here rather than left to review.
    """
    surface = _surface()
    command = str(surface["render_check_command"])
    workflow_path = REPO / str(surface["render_check_workflow"])
    document = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    scripts = [
        str(step.get("run", ""))
        for job in document["jobs"].values()
        for step in job.get("steps", [])
    ]
    assert any(command in script for script in scripts), (
        f"no `run:` step in {workflow_path.name} executes {command!r}. The "
        "string may still be present in a comment, which is what Governance "
        "would accept and what this assertion refuses"
    )


def test_the_workflow_and_the_makefile_check_the_same_three_paths() -> None:
    """Two runners of one comparison must not drift into two comparisons.

    `make deployment-check` is what a laptop and `ci.yml`'s `quality` matrix
    run; the declared workflow is what Governance points at. If one pointed at
    a different descriptor, a different output directory or a different
    thresholds file, the repository would hold two answers to one question and
    a reviewer would have no way to know which one the pin meant.
    """
    surface = _surface()
    workflow = (REPO / str(surface["render_check_workflow"])).read_text(
        encoding="utf-8"
    )
    makefile = MAKEFILE.read_text(encoding="utf-8")

    recipe = next(
        line
        for line in makefile.splitlines()
        if "render --check" in line and line.startswith("\t")
    )
    assert "$(DEPLOY_DESCRIPTOR)" in recipe and "$(DEPLOY_RENDERED)" in recipe

    for literal in ("deploy/product.toml", "deploy/rendered", THRESHOLDS.name):
        assert literal in workflow, (
            f"the declared workflow does not name {literal!r}, which "
            "`make deployment-check` resolves its variables to"
        )
    for variable, value in (
        ("DEPLOY_DESCRIPTOR", "deploy/product.toml"),
        ("DEPLOY_RENDERED", "deploy/rendered"),
        ("DEPLOY_THRESHOLDS", f"deploy/alerts/{THRESHOLDS.name}"),
    ):
        assert f"{variable} ?= {value}" in makefile, (
            f"{variable} no longer defaults to {value!r}, so the workflow and "
            "the Makefile now check different paths"
        )


def test_every_rendered_asset_is_declared_to_governance(
    tmp_path: pathlib.Path,
) -> None:
    """A renderer added later must not escape the profile's declaration.

    `rendered_paths` is what Governance scans for an unpinned image. A fourth
    asset appearing under `deploy/rendered/` without a matching row would be
    committed configuration nothing in the pin covers — and the pin would stay
    green, because a list that names three files reports nothing about a
    fourth.
    """
    fresh = tmp_path / "fresh"
    _render(fresh)
    produced = {
        f"deploy/rendered/{path.relative_to(fresh).as_posix()}"
        for path in fresh.rglob("*")
        if path.is_file()
    }

    declared = set(_surface()["rendered_paths"])  # type: ignore[call-overload]
    assert produced == declared, (
        "the descriptor renders a different set than the profile declares. "
        f"rendered-not-declared: {sorted(produced - declared)}; "
        f"declared-not-rendered: {sorted(declared - produced)}"
    )
