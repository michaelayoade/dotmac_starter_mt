"""Every third-party GitHub Action is pinned to an immutable commit SHA, and
Poetry comes only from the hash-locked bootstrap.

A `uses:` reference like `snok/install-poetry@v1` resolves a MUTABLE tag: a tag
is a pointer, and only a full commit SHA is immutable. Whoever controls the tag
controls code running in CI — and in the sibling vendor repo, on a self-hosted
runner deliberately kept out of the docker group so workflow code cannot reach
root-equivalent host control.

This repo's release workflow already pinned every action by SHA and said so in
its header. CI never did: five `snok/install-poetry@v1` call sites ran unpinned
until 2026-08-04, and were additionally installing an UNPINNED Poetry, floating
to whatever was latest.

Local composite actions (`./.github/actions/...`) are exempt — they are this
repository's own code at this commit, so there is no external reference that can
move under them.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
ACTIONS = REPO / ".github" / "actions"
BOOTSTRAP = REPO / ".github" / "bootstrap"

_SHA = re.compile(r"^[0-9a-f]{40}$")
# Deliberately textual rather than a YAML parse: the check is about a line
# shape GitHub Actions fixes anyway, and the trailing `# v7.0.0` comment is
# stripped rather than matched.
_USES = re.compile(r"^\s*-?\s*uses:\s*(?P<ref>[^\s#]+)")
# Every interpreter a workflow sets up AND bootstraps Poetry on must have its
# own lock — see `_python_versions_needing_a_lock`.
_SETUP_PY_VERSION = re.compile(r'python-version:\s*"?(?P<v>3\.\d+)"?')
# `uses: ./.github/actions/setup-poetry` is the sanctioned bootstrap; a bare
# `poetry` in a `run:` step is the unsanctioned one that
# `test_poetry_toolchain_contract.py` separately refuses. Either is Poetry use,
# and this module needs the union rather than the approved half: a workflow
# reaching for an ambient Poetry still needs the lock this guard is about.
_SETUP_POETRY = "./.github/actions/setup-poetry"


def _workflow_files() -> list[Path]:
    """Every file GitHub would treat as a workflow or a local action manifest.

    BOTH extensions, RECURSIVELY. GitHub accepts `.yml` and `.yaml` for
    workflows, and `action.yml` or `action.yaml` for action metadata — so a
    checker scanning only `*.yml` one directory deep can be bypassed by a
    perfectly valid `.yaml` file, which would then escape every assertion in
    this module. That is not hypothetical laxity: it is a silent hole in a
    supply-chain guard.
    """
    files: list[Path] = []
    for ext in ("yml", "yaml"):
        files += WORKFLOWS.rglob(f"*.{ext}")
        files += ACTIONS.rglob(f"action.{ext}")
    return sorted(set(files))


def _iter_uses() -> list[tuple[str, str]]:
    """(source file, `uses:` value) for every step in every workflow and every
    local composite action."""
    found: list[tuple[str, str]] = []
    for path in _workflow_files():
        rel = str(path.relative_to(REPO))
        for line in path.read_text().splitlines():
            match = _USES.match(line)
            if match:
                found.append((rel, match.group("ref").strip("'\"")))
    return found


def _versions_set_up_by(text: str) -> set[str]:
    """Every concrete Python minor one workflow sets up, including matrix values
    (which appear as a literal list, e.g. `["3.11", "3.12"]`)."""
    versions = {m.group("v") for m in _SETUP_PY_VERSION.finditer(text)}
    for line in text.splitlines():
        if "python-version:" in line and "[" in line:
            versions.update(re.findall(r"3\.\d+", line))
    return versions


def _uses_poetry(text: str) -> bool:
    """Whether this workflow puts Poetry on an interpreter, by either route.

    Textual for the same reason `_USES` is: the question is whether the bytes
    reach for Poetry at all, and a YAML parse would answer a narrower question
    about where.
    """
    if _SETUP_POETRY in text:
        return True
    return any(
        ("run:" in line and "poetry" in line.lower())
        or line.lstrip().startswith("poetry ")
        for line in text.splitlines()
    )


def _python_versions_needing_a_lock() -> set[str]:
    """Interpreters that a workflow sets up AND bootstraps Poetry on.

    NARROWED on 2026-09-06, and the narrowing is the point rather than a
    convenience. This used to be every interpreter any workflow set up, which
    is broader than the reason the lock exists — stated in this module, in
    `regenerate.sh`, and in the assertion's own docstring: *pip resolves a
    different dependency SET per interpreter, so reusing one interpreter's
    Poetry lock on another fails `--require-hashes`*. A workflow that installs
    nothing has no lock to reuse and no `--require-hashes` install to fail.

    `deployment-render-check.yml` is the case that surfaced it. It sets up 3.11
    and 3.13 to run the deployment renderer straight off the source tree —
    `dotmac-deployment-foundation` has zero runtime dependencies, so there is
    no install of any kind, Poetry or otherwise. The old match demanded a
    hash-locked 46-package bootstrap for an interpreter that never sees pip,
    and the only ways to satisfy it were to generate a lock nothing installs or
    to reuse 3.12's — which `regenerate.sh` names, in capitals, as the thing
    not to do.

    The premise is ENFORCEABLE rather than stated: it is decided per workflow
    from that workflow's own bytes, and it re-arms the moment one of these
    files grows a `setup-poetry` step or a `poetry` command.
    `test_an_interpreter_that_gains_poetry_needs_a_lock_again` plants exactly
    that.

    WHAT IS NOW UNMONITORED, said plainly rather than left to be inferred: an
    interpreter set up only by non-Poetry workflows has no hash-locked
    bootstrap and this guard does not ask for one. That is correct — there is
    nothing to lock — but it means adding a `pip install` to such a workflow
    would introduce an unpinned install this module would not see. The guard
    that would see it is `test_poetry_comes_only_from_the_hash_locked_bootstrap`
    for Poetry specifically; a general unpinned-pip guard does not exist in this
    repository and is not created here.
    """
    needed: set[str] = set()
    for path in _workflow_files():
        if ACTIONS in path.parents:
            continue  # action manifests do not set up interpreters
        text = path.read_text()
        if not _uses_poetry(text):
            continue
        needed.update(_versions_set_up_by(text))
    return needed


def _all_python_versions_set_up() -> set[str]:
    """Every interpreter any workflow sets up, Poetry or not.

    Kept so the non-vacuity assertion below still measures the whole corpus. A
    parser that silently stopped finding `python-version:` would otherwise make
    the narrowed check pass over an empty set, which is the failure mode the
    narrowing itself could introduce.
    """
    return {
        version
        for path in _workflow_files()
        if ACTIONS not in path.parents
        for version in _versions_set_up_by(path.read_text())
    }


def test_every_third_party_action_is_pinned_to_a_full_sha() -> None:
    violations: list[str] = []
    for rel, uses in _iter_uses():
        if uses.startswith("./"):
            continue  # this repo's own code at this commit
        if "@" not in uses:
            violations.append(f"{rel}: `{uses}` has no ref at all")
            continue
        ref = uses.rsplit("@", 1)[1]
        if not _SHA.match(ref):
            violations.append(
                f"{rel}: `{uses}` is pinned to `{ref}`, which is a MUTABLE tag "
                "or branch, not an immutable commit SHA"
            )
    assert not violations, (
        "Third-party actions must be pinned to a full 40-character commit SHA "
        "(add the human-readable version as a trailing comment):\n  "
        + "\n  ".join(violations)
    )


def test_the_checker_would_catch_a_mutable_tag() -> None:
    """Sensitivity proof — the assertion above only means something if the SHA
    pattern rejects what it is supposed to reject."""
    assert not _SHA.match("v1")
    assert not _SHA.match("main")
    assert not _SHA.match("v7.0.0")
    assert not _SHA.match("3d3c42e5aac5ba805825da76410c181273ba90b1x")  # 41 chars
    assert not _SHA.match("3D3C42E5AAC5BA805825DA76410C181273BA90B1")  # uppercase
    assert _SHA.match("3d3c42e5aac5ba805825da76410c181273ba90b1")


def test_poetry_comes_only_from_the_hash_locked_bootstrap() -> None:
    """No workflow may reinstate a network Poetry installer alongside the
    pinned one — including the release workflow, which shares the same
    bootstrap so the release path and CI cannot drift apart on the installer."""
    offenders = [
        f"{rel}: {uses}"
        for rel, uses in _iter_uses()
        # `./.github/actions/setup-poetry` IS the sanctioned path.
        if not uses.startswith("./") and "poetry" in uses.lower().split("@")[0]
    ]
    assert not offenders, (
        "Poetry must come from the hash-locked bootstrap via "
        "./.github/actions/setup-poetry, not a third-party installer action:\n  "
        + "\n  ".join(offenders)
    )


def _missing_locks(versions: set[str]) -> list[str]:
    return [
        v
        for v in sorted(versions)
        if not (BOOTSTRAP / f"poetry-requirements-py{v.replace('.', '')}.txt").exists()
    ]


def test_every_python_version_that_bootstraps_poetry_has_its_own_lock() -> None:
    """pip resolves a DIFFERENT dependency set per interpreter — on 3.11 Poetry
    additionally needs backports.tarfile, importlib_metadata and zipp (49
    packages vs 46). Reusing one lock on another interpreter fails
    `--require-hashes` with a missing requirement, which would break the
    kernel-floors matrix that deliberately runs both.

    Scoped to interpreters that actually bootstrap Poetry — see
    `_python_versions_needing_a_lock` for why that is the guard's real premise
    and what it leaves unmonitored.
    """
    assert (
        _all_python_versions_set_up()
    ), "no python-version found in any workflow — the parser drifted"
    needed = _python_versions_needing_a_lock()
    assert needed, (
        "no workflow both sets up an interpreter and bootstraps Poetry. That is "
        "not a pass — it means `_uses_poetry` stopped matching, and this check "
        "is now asking nothing of anybody"
    )
    missing = _missing_locks(needed)
    assert not missing, (
        "these interpreters bootstrap Poetry in a workflow and have no "
        f"hash-locked bootstrap: {missing}. Add them to PYTHON_MINORS in "
        ".github/bootstrap/regenerate.sh and regenerate — do NOT reuse another "
        "interpreter's lock."
    )


def test_an_interpreter_that_gains_poetry_needs_a_lock_again() -> None:
    """Sensitivity. The narrowing above is only honest if it re-arms.

    A workflow setting up an unlocked interpreter is silent today. The same
    workflow with a `setup-poetry` step must be named — otherwise the exemption
    is not a premise, it is a hole with a docstring.
    """
    unlocked = sorted(_all_python_versions_set_up() - _python_versions_needing_a_lock())
    assert unlocked, (
        "no interpreter is currently exempt, so this test would pass without "
        "exercising the narrowing at all"
    )
    victim = unlocked[0]
    assert _missing_locks({victim}) == [victim], (
        f"{victim} is exempt from the lock requirement AND has a lock. Pick a "
        "genuinely unlocked interpreter, or the planted defect below proves "
        "nothing"
    )

    inert = (
        "    steps:\n"
        f"      - uses: actions/setup-python@{'0' * 40}\n"
        "        with:\n"
        f'          python-version: "{victim}"\n'
    )
    assert not _uses_poetry(inert)
    assert _versions_set_up_by(inert) == {victim}

    planted = inert + f"      - uses: {_SETUP_POETRY}\n"
    assert _uses_poetry(planted), (
        "a `setup-poetry` step is not recognised as Poetry use, so the "
        "exemption would survive the thing it is supposed to re-arm on"
    )
    ambient = inert + "      - run: poetry install\n"
    assert _uses_poetry(ambient), (
        "an ambient `poetry` command is not recognised as Poetry use. That is "
        "the route `test_poetry_comes_only_from_the_hash_locked_bootstrap` "
        "refuses, and it still needs the lock this guard is about"
    )


def test_the_exemption_is_refused_over_a_workflow_that_bootstraps_poetry() -> None:
    """The near-miss, from the other side: a locked interpreter that DOES use
    Poetry must stay inside the requirement, or the narrowing quietly excused
    the whole corpus."""
    needed = _python_versions_needing_a_lock()
    assert "3.12" in needed, (
        "3.12 bootstraps Poetry in `ci.yml` and must still be required to have "
        "a lock; if it is not, `_uses_poetry` is under-matching"
    )
    assert _missing_locks({"3.12"}) == []


def test_the_bootstrap_installs_into_a_fresh_venv_not_the_interpreter() -> None:
    """pip leaves an already-satisfied requirement untouched, and hashes cover
    archives being INSTALLED — not packages already on disk. Installing into
    the interpreter's own site-packages therefore verifies nothing on a
    persistent runner, which is what happened on the self-hosted runner on
    2026-08-04: every package "already satisfied", nothing downloaded, no hash
    checked. Only a freshly created venv makes skipping impossible."""
    action = (ACTIONS / "setup-poetry" / "action.yml").read_text()
    assert 'rm -rf "$venv"' in action, "the venv is not recreated, so pip may skip"
    assert "python -m venv" in action, "no venv is created"
    assert (
        '"$venv/bin/python" -m pip install' in action
    ), "pip must install into the venv's interpreter, not the job's"
    assert "--require-hashes" in action
    assert (
        'echo "${venv}/bin" >> "$GITHUB_PATH"' in action
    ), "the verified venv is never published on PATH"
    assert (
        "command -v poetry" in action
    ), "nothing asserts that the poetry on PATH is the verified one"


def test_a_dot_yaml_workflow_cannot_bypass_the_checks() -> None:
    """Sensitivity proof for the extension coverage.

    GitHub runs `.yaml` workflows exactly as it runs `.yml`. A checker that
    scanned only `*.yml` would let a valid `.yaml` file carry a mutable action
    ref past every assertion here — so prove the discovery actually picks one
    up, rather than trusting the glob by inspection.
    """
    probe = WORKFLOWS / "_pinning_probe_.yaml"
    probe.write_text(
        "name: probe\njobs:\n  p:\n    steps:\n"
        "      - uses: snok/install-poetry@v1\n"
    )
    try:
        assert probe in _workflow_files(), ".yaml workflows are not discovered"
        refs = [uses for rel, uses in _iter_uses() if "_pinning_probe_" in rel]
        assert refs == [
            "snok/install-poetry@v1"
        ], "a .yaml workflow's `uses:` was not read"
    finally:
        probe.unlink(missing_ok=True)


def test_a_dot_yaml_action_manifest_is_also_scanned() -> None:
    """Same hole one level down: action metadata may be `action.yaml`."""
    probe_dir = ACTIONS / "_pinning_probe_"
    probe = probe_dir / "action.yaml"
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe.write_text(
        "name: probe\nruns:\n  using: composite\n  steps:\n"
        "      - uses: some/action@main\n"
    )
    try:
        assert probe in _workflow_files(), "action.yaml manifests are not scanned"
    finally:
        probe.unlink(missing_ok=True)
        probe_dir.rmdir()


def test_every_bootstrap_lock_is_fully_hash_pinned() -> None:
    """Every requirement carries `==` and at least one sha256. A single
    unpinned line disables `--require-hashes` for the whole file."""
    locks = sorted(BOOTSTRAP.glob("poetry-requirements-py*.txt"))
    assert locks, "no bootstrap lock files found"
    for lock in locks:
        logical = [
            line.strip()
            for line in lock.read_text().replace("\\\n", " ").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        assert logical, f"{lock.name} is empty"
        for line in logical:
            name = line.split()[0]
            assert "==" in name, f"{lock.name}: {name!r} is not pinned exactly"
            assert "--hash=sha256:" in line, f"{lock.name}: {name!r} has no hash"
