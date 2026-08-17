"""CI must run every gate `make check` runs.

`make check` is the canonical pre-commit gate, and CI is what actually enforces
it — a contributor's local run is a courtesy, the PR job is the acceptance
owner. So the two must agree, and nothing kept them agreeing.

They drifted. `check` required six targets; the CI quality matrix named five,
omitting `ui-check`, and the formatting check was a RECIPE LINE inside `check`
rather than a target, so it could not be named in a matrix at all. The result:
committed `dotmac-ui` assets could drift from their token source, and unformatted
code could merge, on a PR whose every check was green.

That is the worst shape a gate can take — not missing, but present locally and
absent where it decides. Local `make check` passing is exactly what makes the
gap invisible.

## Why this is a test and not a comment

The drift was silent for as long as it existed because nothing compared the two
files. A comment in `ci.yml` saying "keep this in sync" is the thing that was
already implicitly there and did not work. This test reads both files and fails
on any disagreement in either direction:

* a target added to `check` and not to CI is unenforced on PRs;
* a target in CI and not in `check` is a job a contributor cannot reproduce
  locally with the documented command.

Per ADR-0018 the detector carries its own sensitivity proof: a check that reads
two lists and compares them passes just as happily when a parser silently
returns nothing from both.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"


def _check_prerequisites(makefile: str) -> list[str]:
    """The targets `make check` depends on.

    Prerequisites only — anything after `##` is the help text, and a `check`
    with its own recipe would not be visible here at all, which is precisely why
    the formatting check was moved out into a real `format-check` target.
    """
    match = re.search(r"^check:([^#\n]*)", makefile, re.MULTILINE)
    assert match, "no `check:` target in the Makefile"
    return match.group(1).split()


def _quality_matrix_targets(workflow: str) -> list[str]:
    return yaml.safe_load(workflow)["jobs"]["quality"]["strategy"]["matrix"]["target"]


def test_the_ci_quality_matrix_is_exactly_make_checks_prerequisites() -> None:
    make_targets = set(_check_prerequisites(MAKEFILE.read_text(encoding="utf-8")))
    ci_targets = set(_quality_matrix_targets(CI_WORKFLOW.read_text(encoding="utf-8")))

    unenforced = make_targets - ci_targets
    assert not unenforced, (
        f"`make check` runs {sorted(unenforced)} and CI does not — these gates "
        "pass locally and are absent from the job that decides the PR. Add them "
        "to the quality matrix in .github/workflows/ci.yml"
    )

    unreproducible = ci_targets - make_targets
    assert not unreproducible, (
        f"CI runs {sorted(unreproducible)} and `make check` does not — a "
        "contributor cannot reproduce a CI failure with the documented command. "
        "Add them to `check`'s prerequisites in the Makefile"
    )


def test_check_has_no_recipe_of_its_own() -> None:
    """A gate hidden in `check`'s recipe cannot be named in the CI matrix.

    This is how the formatting check escaped for as long as it did: it was real,
    it ran locally, and it was structurally unreferenceable from a workflow.
    Every gate must be a named target.
    """
    body = MAKEFILE.read_text(encoding="utf-8")
    match = re.search(r"^check:.*\n((?:\t.*\n)*)", body, re.MULTILINE)
    assert match, "no `check:` target in the Makefile"
    assert not match.group(1).strip(), (
        "`check` has recipe lines:\n"
        f"{match.group(1)}"
        "Move each into its own target and add it to both `check`'s "
        "prerequisites and the CI quality matrix — a recipe line is a gate no "
        "workflow can name."
    )


def test_every_matrix_target_exists_in_the_makefile() -> None:
    """A matrix naming a target that does not exist fails the job with `No rule
    to make target`, which reads as infrastructure breakage rather than as the
    configuration error it is."""
    body = MAKEFILE.read_text(encoding="utf-8")
    defined = set(re.findall(r"^([a-zA-Z][\w.-]*):", body, re.MULTILINE))
    for target in _quality_matrix_targets(CI_WORKFLOW.read_text(encoding="utf-8")):
        assert target in defined, f"CI runs `make {target}`, which is not defined"


# ── Sensitivity proof (ADR-0018) ────────────────────────────────────────────


def test_the_parsers_find_something() -> None:
    """Both sides comparing empty to empty is a passing test that proves nothing.

    The real files must yield a non-trivial list, and the two must actually
    intersect — a parser returning `[]` from each would satisfy the equality
    above while enforcing nothing at all.
    """
    make_targets = _check_prerequisites(MAKEFILE.read_text(encoding="utf-8"))
    ci_targets = _quality_matrix_targets(CI_WORKFLOW.read_text(encoding="utf-8"))

    assert len(make_targets) >= 9, make_targets
    assert len(ci_targets) >= 9, ci_targets
    assert "ui-check" in make_targets and "ui-check" in ci_targets, (
        "the specific target whose absence motivated this test — if it is gone "
        "from either side, the drift this guards against has recurred"
    )
    assert "format-check" in make_targets and "format-check" in ci_targets
    assert (
        "poetry-lock-check" in make_targets and "poetry-lock-check" in ci_targets
    ), "the exact Poetry + committed-lock gate is absent from an acceptance surface"


@pytest.mark.parametrize(
    ("makefile", "workflow", "reason"),
    [
        (
            "check: lint ui-check\n",
            "jobs: {quality: {strategy: {matrix: {target: [lint]}}}}\n",
            "a target in `check` but not in CI",
        ),
        (
            "check: lint\n",
            "jobs: {quality: {strategy: {matrix: {target: [lint, ui-check]}}}}\n",
            "a target in CI but not in `check`",
        ),
    ],
)
def test_the_comparison_detects_a_planted_drift(
    makefile: str, workflow: str, reason: str
) -> None:
    """The detector is exercised against a known-bad pair, so a passing run on
    the real files means the files agree rather than that the comparison is
    inert."""
    make_targets = set(_check_prerequisites(makefile))
    ci_targets = set(_quality_matrix_targets(workflow))
    assert make_targets != ci_targets, reason


def test_the_recipe_detector_bites() -> None:
    planted = "check: lint\n\tpoetry run ruff format --check .\n"
    match = re.search(r"^check:.*\n((?:\t.*\n)*)", planted, re.MULTILINE)
    assert match and match.group(1).strip(), (
        "the recipe detector did not see a recipe line it was handed — a "
        "hidden gate would go unnoticed"
    )
