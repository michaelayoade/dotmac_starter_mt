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
# Every interpreter any workflow sets up must have its own lock — see
# `_python_versions_used`.
_SETUP_PY_VERSION = re.compile(r'python-version:\s*"?(?P<v>3\.\d+)"?')


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


def _python_versions_used() -> set[str]:
    """Every concrete Python minor a workflow sets up, including matrix values
    (which appear as a literal list, e.g. `["3.11", "3.12"]`)."""
    versions: set[str] = set()
    for path in _workflow_files():
        if ACTIONS in path.parents:
            continue  # action manifests do not set up interpreters
        text = path.read_text()
        versions.update(m.group("v") for m in _SETUP_PY_VERSION.finditer(text))
        for line in text.splitlines():
            if "python-version:" in line and "[" in line:
                versions.update(re.findall(r"3\.\d+", line))
    return versions


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


def test_every_python_version_ci_uses_has_its_own_lock() -> None:
    """pip resolves a DIFFERENT dependency set per interpreter — on 3.11 Poetry
    additionally needs backports.tarfile, importlib_metadata and zipp (49
    packages vs 46). Reusing one lock on another interpreter fails
    `--require-hashes` with a missing requirement, which would break the
    kernel-floors matrix that deliberately runs both."""
    used = _python_versions_used()
    assert used, "no python-version found in any workflow — the parser drifted"
    missing = [
        v
        for v in sorted(used)
        if not (BOOTSTRAP / f"poetry-requirements-py{v.replace('.', '')}.txt").exists()
    ]
    assert not missing, (
        "these interpreters are set up by a workflow but have no hash-locked "
        f"Poetry bootstrap: {missing}. Add them to PYTHON_MINORS in "
        ".github/bootstrap/regenerate.sh and regenerate — do NOT reuse another "
        "interpreter's lock."
    )


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
