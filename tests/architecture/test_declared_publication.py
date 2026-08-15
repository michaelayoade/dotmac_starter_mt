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
    """The sweep, or a FAILURE — never a skip.

    This used to `pytest.skip` when the sweep refused, which reads as caution
    and behaved as a hole. `actions/checkout` fetches no tags by default, so on
    CI the refusal fired on every run and every test in this module skipped
    silently: the gate reported green while checking nothing.

    It was not theoretical. `dotmac-numbering` was merged (#193) declaring
    `0.1.0a1`, allowlisted for release, with no ledger row — exactly what this
    module exists to catch — and CI passed. The stale `dotmac-integration` row
    left by publishing a2 would have gone the same way.

    An unavailable oracle is not a pass. The refusal message names the fix
    (`fetch-depth: 0`), so failing here costs one workflow line and buys a gate
    that is actually running.
    """
    sweep = _sweep()
    try:
        return sweep, sweep.survey(PROJECT_ROOT)
    except SystemExit as refusal:
        pytest.fail(
            "the publication oracle is incomplete, so this gate cannot answer "
            f"and must not pass: {refusal}"
        )


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
    "Reviewed and correct" stays distinct from "we have not got to it yet",
    which is what `dotmac-imports` actually is. `dotmac-auth-oidc` was the
    first kind and has since moved on: its pilot ran, it is allowlisted, and its
    row now says to delete itself at tag time — a reason that expires is the
    healthiest shape an entry here can have."""
    for distribution, entry in _ledger().items():
        reason = entry["reason"]
        assert len(reason.split()) >= 20, f"{distribution}: a reason, not a label"
        for evasion in ("grandfathered", "tbd", "n/a", "see above"):
            assert evasion not in reason.lower(), f"{distribution}: {evasion!r}"


def test_the_integration_gap_is_closed_and_stays_closed() -> None:
    """The programme's live example, now CLOSED — and pinned in that state.

    This test spent its life asserting a gap: `dotmac-integration` declared a
    version no tag proved, and the ledger had to say so. `0.1.0a3` was
    published and tagged on 2026-08-15, so the row was removed in the same
    change as the release, which is the rule the ledger exists to enforce.

    Inverted rather than deleted, for the same reason the receipt-delivery
    ratchet was inverted rather than deleted when its columns landed: the
    assertion that a gap is RECORDED and the assertion that it is CLOSED are
    the two halves of one ratchet, and dropping the second leaves the example
    unguarded exactly when it starts looking finished. A re-declared,
    unpublished version now fails here as well as in the generic test above,
    and it fails by name.
    """
    sweep, survey = _survey()
    finding = survey["distributions"]["dotmac-integration"]
    assert finding["state"] == sweep.PUBLISHED, (
        f"dotmac-integration declares {finding['declared']} with no tag to "
        "prove it. If that is deliberate, record it in the ledger with a "
        "reason — the repair is a RELEASE, never an edit of the declared number"
    )
    assert "dotmac-integration" not in _ledger(), (
        "the gap is closed, so the ledger must not still excuse it — a ledger "
        "that only ever grows stops describing anything"
    )


def test_an_allowlisted_module_that_has_never_been_published_is_recorded() -> None:
    """An allowlist row means the workflow MAY publish; it is not proof that it
    did. Any distribution in both states at once must be in the ledger.

    Currently vacuous — deliberately, and it says so rather than being deleted.
    The one module that was in both states, `dotmac-imports`, had its ALLOWLIST
    ROW removed (see the test below), so the intersection is now empty. Keeping
    the rule is what stops the next module from quietly entering that state; the
    non-vacuity of the guard as a whole is carried by
    `test_every_unpublished_declaration_is_recorded_with_a_reason` and the
    planted-violation proofs below.
    """
    sweep, survey = _survey()
    allowlist = json.loads(
        (PROJECT_ROOT / ".github" / "release-modules.json").read_text(encoding="utf-8")
    )["modules"]
    never = {
        distribution
        for distribution, finding in survey["distributions"].items()
        if finding["state"] == sweep.NEVER_PUBLISHED
    }
    assert never, "the sweep found no never-published distribution at all"
    for distribution in never & set(allowlist):
        assert distribution in _ledger(), distribution


def test_imports_is_unreleased_by_decision_not_by_omission() -> None:
    """The 2026-08-15 ruling, pinned so neither half can drift back.

    `dotmac-imports` was reported as an anomaly because it was
    release-allowlisted with no tag in any version. The anomaly was the
    ALLOWLIST ROW: its own `EXTRACTION.toml` says to keep the module
    audit-complete and unreleased until a real first adopter is ready, and to
    publish just in time for that cutover. So the row was removed and the module
    was NOT published — the opposite repair from the one the report suggested.

    Three things are asserted together because each alone can be undone by
    somebody acting in good faith: the row stays out, the package stays intact,
    and the ledger says which of the two is the decision.
    """
    sweep, survey = _survey()
    allowlist = json.loads(
        (PROJECT_ROOT / ".github" / "release-modules.json").read_text(encoding="utf-8")
    )["modules"]
    assert "dotmac-imports" not in allowlist, (
        "dotmac-imports is allowlisted again. Its dossier gates release on ERP's "
        "first-adopter cutover; re-add the row WITH that proof, not before it"
    )
    # The package is untouched: removal from the lane is not retirement.
    package = PROJECT_ROOT / "packages" / "dotmac-imports"
    assert (package / "pyproject.toml").is_file()
    assert survey["distributions"]["dotmac-imports"]["declared"] == "0.1.0a2"
    assert survey["distributions"]["dotmac-imports"]["state"] == sweep.NEVER_PUBLISHED
    # …and the dossier still says why, so the ledger reason is not the only copy.
    dossier = (package / "EXTRACTION.toml").read_text(encoding="utf-8")
    assert "audit-complete and unreleased until a real first adopter" in dossier

    reason = _ledger()["dotmac-imports"]["reason"]
    assert "DO NOT" in reason and "dispatching a release" in reason


def test_a_removed_allowlist_row_is_explained_where_it_was_removed_from() -> None:
    """A deletion is the least self-explanatory diff there is: the next reader
    sees a module with a package, a catalogue entry and no lane, and the
    cheapest explanation is 'someone forgot'. The reason has to live in the file
    the row left, not only in a ledger they may never open."""
    allowlist = (PROJECT_ROOT / ".github" / "release-modules.json").read_text(
        encoding="utf-8"
    )
    assert "dotmac-imports was REMOVED from this file" in allowlist
    assert "re-added with ERP's adoption proof" in allowlist


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
    as unpublished — and the shallow case is now probed FIRST, so a non-git
    directory refuses on `git rev-parse` rather than on `git tag`. All three
    reasons are accepted here because the assertion is about refusing with a
    named cause, not about which probe happened to run first.

    An unpublished-looking sweep is a property of the clone, not of the
    releases, and a guard that cries wolf on a fresh CI checkout is one
    somebody disables. An
    unavailable oracle is never a pass — both the "not a repository" and the
    "no such directory" cases refuse, rather than one refusing and the other
    raising something the caller has to interpret."""
    sweep = _sweep()
    for root in (tmp_path, tmp_path / "nonexistent"):
        with pytest.raises(SystemExit) as refusal:
            sweep.survey(root)
        message = str(refusal.value)
        assert any(
            reason in message
            for reason in ("git tag", "no tags", "git rev-parse", "SHALLOW")
        ), f"a refusal must name the oracle problem it hit, got: {message}"


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


def test_the_gate_fails_closed_rather_than_skipping_on_a_bad_oracle() -> None:
    """The regression that matters most here, pinned at the source level.

    A future edit restoring `pytest.skip` on the refusal path would take this
    whole module back to silently green, and every behavioural test above would
    keep passing while checking nothing — the failure mode is invisible by
    construction, so it is asserted structurally instead.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    survey_source = source[
        source.index("def _survey()") : source.index("# ── The live state")
    ]
    assert "pytest.fail(" in survey_source
    # The CALL, not the word: the docstring above legitimately explains why
    # `pytest.skip` was wrong, and a guard that cannot tell prose from code is
    # the same class of defect as a word-boundary scanner blind to identifiers.
    assert "pytest.skip(" not in survey_source, (
        "a refused oracle must FAIL. Skipping is how this gate spent its life "
        "green on CI without ever running"
    )


def test_a_shallow_checkout_is_refused() -> None:
    """Sensitivity proof for the new half of the refusal.

    `git tag` returning nothing was already refused; a shallow checkout that
    happens to carry SOME tags was not, and a partial tag set is the worse
    case — it produces confident, wrong answers rather than none.
    """
    sweep = _sweep()
    assert hasattr(sweep, "is_shallow"), "the shallow check was removed"
    assert (
        sweep.is_shallow(PROJECT_ROOT) is False
    ), "this checkout is shallow, so the suite itself cannot trust its tags"
