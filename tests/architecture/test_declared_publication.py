"""A declared version nobody can install is detected, not discovered later.

`test_module_version_sync.py` proves a module's three version surfaces AGREE.
It cannot prove the agreed version EXISTS — three surfaces reading `0.1.0a2` in
unison say nothing about whether `0.1.0a2` was ever built, uploaded and
verified. Internal consistency and publication are different questions, and
only the first one had a guard.

The live example is why this file exists. `dotmac-integration` declares
`0.1.0a2` everywhere and the newest tag is `dotmac-integration-v0.1.0a1`, so a
consumer reading the repository, the changelog or `docs/MODULE_CATALOG.md` and
pinning `==0.1.0a2` gets a resolver error. `dotmac-imports` is sharper still: it
is release-allowlisted, declares `0.1.0a2`, and has NO tag in any version.

**This is a detector, not a fixer.** The repair is a release run or a recorded
decision to leave the version unreleased — never a quiet edit of the declared
number, which would make the repository agree with the index by discarding the
work the number describes.

Measurement: `scripts/declared_publication_sweep.py`. Ledger:
`docs/inventories/declared-publication-baseline.json`.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "declared_publication_sweep.py"
LEDGER = PROJECT_ROOT / "docs" / "inventories" / "declared-publication-baseline.json"


def _sweep():
    spec = importlib.util.spec_from_file_location("declared_publication_sweep", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ledger() -> dict[str, dict]:
    return json.loads(LEDGER.read_text(encoding="utf-8"))["unpublished"]


def _survey():
    sweep = _sweep()
    try:
        return sweep, sweep.survey(PROJECT_ROOT)
    except SystemExit as refusal:  # a tagless or shallow clone
        pytest.skip(str(refusal))


# ── The live state ──────────────────────────────────────────────────────────


def test_every_unpublished_declaration_is_recorded_with_a_reason() -> None:
    """The forward direction. A distribution entering this state without a
    ledger entry is a version silently promised to consumers."""
    sweep, survey = _survey()
    problems = sweep.reconcile(survey, _ledger())
    assert not problems, "declared-publication:\n" + "\n".join(problems)


def test_the_ledger_holds_no_stale_absolution() -> None:
    """The other direction, and the half a one-directional guard misses. A row
    whose distribution has since been published must be removed in the SAME
    change as the release; a ledger that only ever grows stops describing
    anything (ADR-0018)."""
    sweep, survey = _survey()
    published = {
        distribution
        for distribution, finding in survey["distributions"].items()
        if finding["state"] == sweep.PUBLISHED
    }
    assert not published & set(_ledger()), "a published distribution is still excused"


def test_no_ledger_entry_is_a_bare_label() -> None:
    """ADR-0018: an exemption states an ENFORCEABLE premise, or the region is
    unmonitored rather than exempt. "Grandfathered" is a description of history
    and is refused so it cannot become the boilerplate that ends the argument.
    "Reviewed and correct" — `dotmac-auth-oidc`, deliberately unpublished until
    the Workspace pilot runs — stays distinct from "we have not got to it yet",
    which is what `dotmac-imports` actually is."""
    for distribution, entry in _ledger().items():
        reason = entry["reason"]
        assert len(reason.split()) >= 20, f"{distribution}: a reason, not a label"
        for evasion in ("grandfathered", "tbd", "n/a", "see above"):
            assert evasion not in reason.lower(), f"{distribution}: {evasion!r}"


def test_the_integration_gap_is_recorded_as_evidence() -> None:
    """The programme's live example, pinned so it cannot be closed by editing a
    version. `0.1.0a2` is the 2026-08-14 strictness fix; the only INSTALLABLE
    release is `0.1.0a1`, which ships a `run_effect_once` that raises on its
    first call. Recording that is the point — the release decision belongs to
    the release step, and a guard that "fixed" it by bumping would delete the
    evidence the decision needs."""
    sweep, survey = _survey()
    finding = survey["distributions"]["dotmac-integration"]
    assert finding["state"] == sweep.DECLARED_UNPUBLISHED
    assert finding["published_versions"], "a1 is published; only a2 is not"
    entry = _ledger()["dotmac-integration"]
    assert entry["declared"] == finding["declared"]
    assert "NOT to be repaired by editing the version" in entry["reason"]


def test_an_allowlisted_module_that_has_never_been_published_is_visible() -> None:
    """`dotmac-imports` is the worst state in the file and the least visible:
    release-allowlisted, so the allowlist reads as a catalogue of available
    modules, while no `dotmac-imports-v` tag has ever been written. An allowlist
    row means the workflow MAY publish — it is not proof that it did."""
    sweep, survey = _survey()
    allowlist = json.loads(
        (PROJECT_ROOT / ".github" / "release-modules.json").read_text(encoding="utf-8")
    )["modules"]
    never = {
        distribution
        for distribution, finding in survey["distributions"].items()
        if finding["state"] == sweep.NEVER_PUBLISHED
    }
    assert "dotmac-imports" in never
    assert "dotmac-imports" in allowlist
    for distribution in never & set(allowlist):
        assert distribution in _ledger(), distribution


# ── Sensitivity proofs (ADR-0018) ───────────────────────────────────────────


def _survey_of(distributions: dict) -> dict:
    return {"distributions": distributions}


def test_the_guard_fires_on_an_unrecorded_unpublished_declaration() -> None:
    """Without this, "every unpublished version is recorded" is an assertion
    about a file nobody proved is compared."""
    sweep = _sweep()
    problems = sweep.reconcile(
        _survey_of(
            {
                "dotmac-newthing": {
                    "declared": "0.1.0a1",
                    "state": sweep.NEVER_PUBLISHED,
                    "tag_prefix": "dotmac-newthing-v",
                    "published_versions": [],
                    "release_lane": "module",
                }
            }
        ),
        {},
    )
    assert any("dotmac-newthing" in problem for problem in problems)
    assert any("NEVER repaired by editing" in problem for problem in problems)


def test_the_guard_fires_when_a_recorded_entry_has_been_published() -> None:
    """The falling direction. A ledger nobody prunes is a ledger that stops
    being read."""
    sweep = _sweep()
    problems = sweep.reconcile(
        _survey_of(
            {
                "dotmac-oldthing": {
                    "declared": "0.1.0a1",
                    "state": sweep.PUBLISHED,
                    "tag_prefix": "dotmac-oldthing-v",
                    "published_versions": ["0.1.0a1"],
                    "release_lane": "module",
                }
            }
        ),
        {"dotmac-oldthing": {"declared": "0.1.0a1", "reason": "x" * 200}},
    )
    assert any("Remove the entry in the SAME change" in problem for problem in problems)


def test_the_guard_fires_when_a_bump_outruns_its_ledger_entry() -> None:
    """A recorded excuse is about a specific version. Bumping past it without
    touching the ledger would leave the new version excused by a reason written
    for the old one — the drift that makes an exemption meaningless while it
    still looks reviewed."""
    sweep = _sweep()
    problems = sweep.reconcile(
        _survey_of(
            {
                "dotmac-thing": {
                    "declared": "0.1.0a3",
                    "state": sweep.DECLARED_UNPUBLISHED,
                    "tag_prefix": "dotmac-thing-v",
                    "published_versions": ["0.1.0a1"],
                    "release_lane": "module",
                }
            }
        ),
        {"dotmac-thing": {"declared": "0.1.0a2", "reason": "x" * 200}},
    )
    assert any("the ledger records" in problem for problem in problems)


def test_a_consistent_state_produces_no_findings() -> None:
    """SPECIFICITY for all three proofs above: the reconciler must object
    because something is wrong, not because it objects to everything."""
    sweep = _sweep()
    assert not sweep.reconcile(
        _survey_of(
            {
                "dotmac-thing": {
                    "declared": "0.1.0a1",
                    "state": sweep.PUBLISHED,
                    "tag_prefix": "dotmac-thing-v",
                    "published_versions": ["0.1.0a1"],
                    "release_lane": "module",
                }
            }
        ),
        {},
    )


def test_an_unusable_oracle_refuses_rather_than_reporting_nine_defects(
    tmp_path: Path,
) -> None:
    """A shallow, tagless or non-git checkout would make every distribution read
    as unpublished. That is a property of the clone, not of the releases, and a
    guard that cries wolf on a fresh CI checkout is one somebody disables. An
    unavailable oracle is never a pass — both the "not a repository" and the
    "no such directory" cases refuse, rather than one refusing and the other
    raising something the caller has to interpret."""
    sweep = _sweep()
    for root in (tmp_path, tmp_path / "nonexistent"):
        with pytest.raises(SystemExit) as refusal:
            sweep.survey(root)
        assert "git tag" in str(refusal.value) or "no tags" in str(refusal.value)


def test_versions_are_ordered_naturally_not_lexically() -> None:
    """`0.1.0a9` outranking `0.1.0a64` would make the newest kernel look older
    than one published fifty-five releases earlier — wrong in the direction that
    misleads a reader trying to decide whether they are behind."""
    sweep = _sweep()
    ordered = sorted(
        ["0.1.0a9", "0.1.0a64", "0.1.0a12", "0.1.0a2"], key=sweep.version_key
    )
    assert ordered == ["0.1.0a2", "0.1.0a9", "0.1.0a12", "0.1.0a64"]


def test_the_ledger_covers_every_package_and_nothing_else() -> None:
    """Discovery, not enumeration: the survey reads `packages/*/pyproject.toml`,
    so a new distribution is enrolled by existing rather than by being added to
    a list here — which would go stale exactly when it matters."""
    sweep, survey = _survey()
    packages = {
        path.parent.name
        for path in (PROJECT_ROOT / "packages").glob("*/pyproject.toml")
    }
    assert len(survey["distributions"]) == len(packages)
    assert set(_ledger()) <= set(survey["distributions"])
