"""The recorder refuses when it cannot tell where a release's migrations are.

## The defect, and why one refusal is not enough

`write_release_record.py` owns three ledgers. Its `RELEASED_TAGS` reconciliation
sat behind `if package_dir and import_name:` and then `if digests:`, and
`--package-dir` was optional. So a lineage-bearing release recorded without the
flag SKIPPED the tag oracle and reported **success**. That happened at a101 and
again at a102; both were repaired by hand, and the first repair landed inside
its own record PR, leaving the mechanism gap no separate trace.

Passing the flag is not the protection — its CORRECTNESS is. Measured against
the real a102 tag:

    correct path                        -> 28 migration files
    typo'd path (dotmac-kernal)         -> no output, exit 0
    existing dir with no migrations     -> no output, exit 0

`git ls-tree` does not error on a path that does not exist. A typo and "this
distribution has no migrations" are therefore *literally the same value* — an
empty dict from a successful call. One generic failure could not tell an
operator whether to fix the flag, fix the import name, or stop passing the flag
at all, so each way of being wrong gets its own refusal.

## Omission is not a declaration

The third clause is the one that closes the hole. Silence used to mean both "no
lineage" and "the caller forgot". `--package-dir` and `--no-lineage` are now
mutually exclusive and one is required, so the empty case cannot be expressed —
and `--no-lineage` is CHECKED against `RELEASED_TAGS` rather than believed.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "write_release_record.py"
MIGRATIONS_MODULE = ROOT / "tests" / "architecture" / "test_released_migrations.py"

#: A real published tag with a real lineage. The oracle is the artifact, not a
#: fixture: a fixture could agree with a broken reader.
KERNEL_TAG = "dotmac-kernel-v0.1.0a102"


def writer():
    spec = importlib.util.spec_from_file_location("write_release_record", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def module_text() -> str:
    return MIGRATIONS_MODULE.read_text(encoding="utf-8")


def resolve(**overrides):
    arguments = {
        "distribution": "dotmac-kernel",
        "tag": KERNEL_TAG,
        "package_dir": "packages/dotmac-kernel",
        "import_name": None,
        "no_lineage": False,
        "module_text": module_text(),
    }
    arguments.update(overrides)
    return writer().resolve_lineage_inputs(**arguments)


def test_the_anchored_set_is_read_from_the_map_that_already_records_it() -> None:
    """Clause 1 needs no second declaration to drift out of step with the first."""
    anchored = writer().anchored_distributions(module_text())
    assert "dotmac-kernel" in anchored
    assert "dotmac-ui" not in anchored, (
        "dotmac-ui is the one release lane with no migration lineage; if it "
        "gains one, --no-lineage in release-ui.yml must go"
    )
    assert len(anchored) > 50, (
        f"only {len(anchored)} anchored distributions parsed — if this collapses "
        "to a handful the refusals below stop covering the fleet and would pass "
        "by seeing nothing"
    )


def test_a_real_lineage_resolves(  # the positive control
) -> None:
    """A guard that only ever refuses proves nothing about itself."""
    package_dir, import_name, digests = resolve()
    assert (package_dir, import_name) == ("packages/dotmac-kernel", "dotmac_kernel")
    assert len(digests) == 28, digests
    assert all(len(digest) == 64 for digest in digests.values())


def test_a_genuinely_non_lineage_distribution_takes_the_explicit_path() -> None:
    assert resolve(
        distribution="dotmac-ui",
        tag="dotmac-ui-v0.1.0a8",
        package_dir=None,
        no_lineage=True,
    ) == (None, None, {})


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        pytest.param(
            {"package_dir": None},
            "MISSING lineage declaration",
            id="missing-neither-flag",
        ),
        pytest.param(
            {"package_dir": "packages/dotmac-kernel", "no_lineage": True},
            "CONTRADICTORY lineage declaration",
            id="both-flags",
        ),
        pytest.param(
            {"package_dir": "/etc"}, "INCORRECT --package-dir", id="invalid-shape"
        ),
        pytest.param(
            {"package_dir": "packages/dotmac-kernal"},
            "INCORRECT --package-dir",
            id="mismatched-name-the-a102-typo",
        ),
        pytest.param(
            {"import_name": "nope"}, "UNREADABLE lineage", id="unreadable-import-name"
        ),
        pytest.param(
            {"package_dir": None, "no_lineage": True},
            "CONTRADICTORY lineage declaration",
            id="no-lineage-claimed-for-an-anchored-distribution",
        ),
    ],
)
def test_each_way_of_being_wrong_refuses_distinctly(overrides, expected) -> None:
    module = writer()
    with pytest.raises(module.ReleaseRecordError) as refusal:
        resolve(**overrides)
    assert expected in str(refusal.value), str(refusal.value)


def test_the_import_name_override_cannot_reopen_the_skip() -> None:
    """The second door.

    `--import-name` is caller-supplied and independent of `--package-dir`. An
    earlier draft of this very change derived the import name inside the
    resolver and let a later override recompute the digests — which reinstated
    the silent skip through a path the four refusals did not cover. The
    readability check must run against the path the override actually resolves
    to.
    """
    module = writer()
    with pytest.raises(module.ReleaseRecordError, match="UNREADABLE"):
        resolve(import_name="nope")


def test_the_old_silent_skip_implementation_passes_incorrectly() -> None:
    """The sensitivity case, and the half that stops the guard being vacuous.

    Reconstructs the previous shape verbatim — `if package_dir and import_name:`
    then `if digests:` — and shows it reaching "nothing to do" on the exact typo
    that cost two releases. Without this, the new refusal could have been
    written so that it also passes over the old code, and nobody would know.
    """
    module = writer()
    package_dir, import_name = "packages/dotmac-kernal", "dotmac_kernal"

    old_digests = module.migration_digests(KERNEL_TAG, package_dir, import_name)
    assert old_digests == {}, (
        "git ls-tree started erroring on a missing path; the premise of this "
        "whole change has changed and the refusals need re-deriving"
    )
    old_reaches_the_oracle = bool(package_dir and import_name) and bool(old_digests)
    assert not old_reaches_the_oracle, (
        "the OLD implementation would have caught this typo, so the new refusal "
        "is not what closes the gap — re-derive before trusting it"
    )

    with pytest.raises(module.ReleaseRecordError, match="INCORRECT"):
        resolve(package_dir=package_dir)


def test_the_cli_cannot_express_the_empty_case() -> None:
    """argparse is where every release lane crosses, so the XOR lives there too."""
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(SCRIPT),
            "--distribution",
            "dotmac-ui",
            "--version",
            "0.1.0a8",
            "--tag",
            "dotmac-ui-v0.1.0a8",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "one of the arguments --package-dir --no-lineage is required" in (
        completed.stderr
    )


def test_every_release_lane_declares_one_or_the_other() -> None:
    """The callers, not just the callee.

    A required flag nobody passes breaks a release lane; a lane that declares
    neither is the defect this change exists to stop. Both directions are read
    off the workflows themselves.
    """
    workflows = sorted(
        path
        for path in (ROOT / ".github" / "workflows").glob("*.yml")
        if "open_release_record_pr.sh" in path.read_text(encoding="utf-8")
    )
    assert len(workflows) >= 8, f"only {len(workflows)} recorder callers found"
    undeclared = [
        path.name
        for path in workflows
        if "--package-dir" not in path.read_text(encoding="utf-8")
        and "--no-lineage" not in path.read_text(encoding="utf-8")
    ]
    assert not undeclared, (
        f"{undeclared} call the recorder without declaring a lineage; the writer "
        "now refuses that, so these lanes would fail at record time"
    )


def test_a_readable_path_carrying_another_lineage_is_contradictory() -> None:
    """The fourth kind, and the branch that needs a planted defect to be worth
    anything.

    The other refusals are reachable from the command line. This one is a claim
    about the TREE: a path that is present, readable and whose migrations are
    not this distribution's lineage. No flag combination reaches it, so it is
    proven by planting the contradiction in the map and showing a near miss —
    the real glob — still passes.
    """
    module = writer()
    real = module_text()
    planted = real.replace(
        '"dotmac-kernel": "2*_[0-9][0-9][0-9][0-9]_*.py"',
        '"dotmac-kernel": "zz_*.py"',
        1,
    )
    assert planted != real, (
        "the kernel's lineage glob moved; re-derive this plant rather than "
        "leaving it matching nothing"
    )

    with pytest.raises(module.ReleaseRecordError, match="CONTRADICTORY lineage"):
        resolve(module_text=planted)

    package_dir, _, digests = resolve(module_text=real)
    assert (
        package_dir == "packages/dotmac-kernel" and len(digests) == 28
    ), "the near miss must pass, or this guard is refusing everything"
