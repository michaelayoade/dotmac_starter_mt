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
import tomllib
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


def _state_mismatches(
    ledger: dict[str, dict], computed: dict[str, str]
) -> dict[str, tuple]:
    """Rows whose recorded `state` disagrees with the sweep's.

    Pure, so the live check below and its sensitivity proof exercise the same
    comparison rather than two hand-written ones that can drift apart.
    """
    return {
        distribution: (entry.get("state"), computed.get(distribution))
        for distribution, entry in ledger.items()
        if entry.get("state") != computed.get(distribution)
    }


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


def test_each_ledger_state_matches_what_the_sweep_computes() -> None:
    """`state` is a FACT the sweep derives, so a hand-written one can be wrong.

    Both existing directions check MEMBERSHIP — is a distribution recorded, is
    a recorded one still unpublished. Neither reads the `state` field, so it
    could say anything at all and every gate stayed green.

    It did. `dotmac-kernel` was recorded `never-published` while ninety kernel
    versions were tagged; only `0.1.0a91` was unpublished, which is
    `declared-unpublished`. The row entered the ledger by hand during a
    conflict resolution and nothing compared it to the oracle that owns the
    answer.

    The distinction is the whole point of having two labels. `never-published`
    means nobody has ever been able to install this distribution — the state of
    a package built but withheld. `declared-unpublished` means it ships, and
    this particular version has not gone out yet. Reading the first where the
    second is true understates what consumers already depend on.
    """
    sweep, survey = _survey()
    computed = {
        distribution: finding["state"]
        for distribution, finding in sweep.unpublished(survey).items()
    }
    wrong = _state_mismatches(_ledger(), computed)
    assert not wrong, (
        "ledger state disagrees with the sweep, which owns the answer: "
        + "; ".join(
            f"{name} records {recorded!r} but is {actual!r}"
            for name, (recorded, actual) in sorted(wrong.items())
        )
        + " — regenerate with `make publication-baseline`, or correct the row"
    )


def test_no_ledger_entry_is_a_bare_label() -> None:
    """ADR-0018: an exemption states an ENFORCEABLE premise, or the region is
    unmonitored rather than exempt. "Grandfathered" is a description of history
    and is refused so it cannot become the boilerplate that ends the argument.
    "Reviewed and correct" stays distinct from "we have not got to it yet".
    `dotmac-imports` has now moved from deliberately withheld to OUTSTANDING:
    its row says to delete itself at tag time — a reason that expires is the
    healthiest shape an entry here can have."""
    for distribution, entry in _ledger().items():
        reason = entry["reason"]
        assert len(reason.split()) >= 20, f"{distribution}: a reason, not a label"
        for evasion in ("grandfathered", "tbd", "n/a", "see above"):
            assert evasion not in reason.lower(), f"{distribution}: {evasion!r}"


def test_the_integration_example_is_in_exactly_one_state() -> None:
    """The programme's live example, pinned as a BICONDITIONAL rather than as a
    state.

    This test has now been written three ways in one day, which is the evidence
    for the shape it finally has. It began as "the gap is RECORDED"; #207
    inverted it to "the gap is CLOSED and stays closed" when `0.1.0a3` was
    published and the row was correctly removed; and this change re-opens the
    gap by declaring `0.1.0a4`. Both fixed forms were true when written and
    false a few hours later, because a module cycles between the two states
    every time a version is declared and then released — that cycle is the
    normal life of the thing, not a defect either form could catch.

    So neither state is asserted. What is asserted is that the two agree:
    a ledger row exists if and only if the declared version is unpublished.
    Both failure directions are real and both are named — a silent promise to
    consumers, and an absolution that outlived its release. It still fails BY
    NAME for this distribution, which is what #207 wanted from a
    distribution-specific test that the generic ones above cannot give.
    """
    sweep, survey = _survey()
    finding = survey["distributions"]["dotmac-integration"]
    published = finding["state"] == sweep.PUBLISHED
    excused = "dotmac-integration" in _ledger()

    assert published != excused, "dotmac-integration is " + (
        "published and STILL excused by the ledger — remove the row in the "
        "same change as the release; a ledger that only ever grows stops "
        "describing anything"
        if published
        else f"declaring {finding['declared']} with no tag to prove it and "
        "no ledger row. If that is deliberate, record it with a reason — "
        "the repair is a RELEASE, never an edit of the declared number"
    )
    if excused:
        # An open gap has to carry the premise, not merely a row. Checked here
        # and not only in the generic reason-shape test, because this is the
        # example the programme cites and the sentence is the whole point of it.
        assert (
            "NOT to be repaired by editing the version"
            in _ledger()["dotmac-integration"]["reason"]
        )


def test_an_allowlisted_module_that_has_never_been_published_is_recorded() -> None:
    """An allowlist row means the workflow MAY publish; it is not proof that it
    did. Any distribution in both states at once must be in the ledger.

    NO LONGER VACUOUS, as of the ADR-0057 cohort (2026-08-20). It was, and the
    docstring said so rather than the test being deleted: the one module in both
    states, `dotmac-imports`, had its ALLOWLIST ROW removed (see the test below),
    emptying the intersection. Three modules now occupy it deliberately —
    dotmac-commercial-agreements, dotmac-licensing and dotmac-brand-profiles —
    because `dotmac_vendor_control_plane` cannot pin an unpublished module, so
    the allowlist row has to precede the release that precedes the adoption.
    (dotmac-deployment-control was a fourth until 2026-08-29, when its allowlist
    row was removed to freeze this repository as its publisher; the distribution
    moved to `michaelayoade/dotmac_deployment_control`.) That is the state this
    rule exists to police rather than forbid: each carries a ledger row saying
    when its row goes.

    Imports used to be the counterexample: it had no ready adopter and its own
    dossier said to stay unreleased, so its old allowlist row asserted something
    untrue. The premise has since changed and ERP is now its named adopter; the
    generic rule covers Imports and every other member of the intersection.

    Deliberately NOT asserting the intersection is non-empty. Releasing the
    cohort empties it again and that is the desired end state, so a
    non-vacuity assertion here would fail on success — the guard's job is to
    catch a module entering this state unrecorded, not to require that one
    always be in it.
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


def test_imports_release_lane_names_erp_without_claiming_adoption() -> None:
    """Publication is now due, while product adoption remains unclaimed.

    The 2026-08-15 removal was correct when Imports had no ready adopter. ERP's
    first-adopter slice now supplies the domain port, composed lineage, bounded
    storage adapter, scheduling/repair surface, parity check and PostgreSQL
    concurrency proof. Under the later named-adopter rule, that makes a2
    release-eligible; it does not make ERP a proven contract consumer before
    ERP can resolve the release and merge its exact lock pin.

    This assertion survives publication: before the tag the outstanding ledger
    row must authorize release; after the tag that row must disappear.
    """
    sweep, survey = _survey()
    allowlist = json.loads(
        (PROJECT_ROOT / ".github" / "release-modules.json").read_text(encoding="utf-8")
    )["modules"]
    assert "dotmac-imports" in allowlist
    package = PROJECT_ROOT / "packages" / "dotmac-imports"
    assert (package / "pyproject.toml").is_file()
    finding = survey["distributions"]["dotmac-imports"]
    assert finding["declared"] == "0.1.0a2"

    dossier = tomllib.loads((package / "EXTRACTION.toml").read_text(encoding="utf-8"))
    assert dossier["status"] == "audit-complete"
    assert dossier["contract_consumers"] == []
    assert dossier["candidate_consumers"][0] == "dotmac_erp"

    published = finding["state"] == sweep.PUBLISHED
    recorded = "dotmac-imports" in _ledger()
    assert published != recorded
    if recorded:
        reason = _ledger()["dotmac-imports"]["reason"]
        assert "OUTSTANDING" in reason
        assert "dotmac-imports-v0.1.0a2" in reason


def test_imports_allowlist_history_and_reauthorization_are_both_explained() -> None:
    """The earlier removal stays visible, followed by the changed premise."""
    allowlist = (PROJECT_ROOT / ".github" / "release-modules.json").read_text(
        encoding="utf-8"
    )
    assert "dotmac-imports was REMOVED from this file" in allowlist
    assert "dotmac-imports was RE-AUTHORIZED" in allowlist


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


def test_the_state_comparison_fires_on_a_hand_written_label() -> None:
    """SENSITIVITY for the live state check (ADR-0018: a detector carries a
    proof that it bites). The exact defect it was written for — a row calling a
    long-published distribution `never-published` — and the specificity case
    that an agreeing row is left alone."""
    sweep = _sweep()
    wrong = _state_mismatches(
        {"dotmac-thing": {"declared": "0.1.0a91", "state": sweep.NEVER_PUBLISHED}},
        {"dotmac-thing": sweep.DECLARED_UNPUBLISHED},
    )
    assert wrong == {
        "dotmac-thing": (sweep.NEVER_PUBLISHED, sweep.DECLARED_UNPUBLISHED)
    }
    assert not _state_mismatches(
        {"dotmac-thing": {"declared": "0.1.0a91", "state": sweep.DECLARED_UNPUBLISHED}},
        {"dotmac-thing": sweep.DECLARED_UNPUBLISHED},
    )


def _unpublished_survey(sweep) -> dict:
    """One unpublished distribution, so the reason rules are the only variable."""
    return _survey_of(
        {
            "dotmac-thing": {
                "declared": "0.1.0a1",
                "state": sweep.NEVER_PUBLISHED,
                "tag_prefix": "dotmac-thing-v",
                "published_versions": [],
                "release_lane": "module",
            }
        }
    )


def test_the_guard_fires_on_the_generators_unwritten_placeholder() -> None:
    """The defect this check was added for, and it is not hypothetical.

    `--write-baseline` writes `UNWRITTEN_REASON` for any distribution it has no
    existing reason to carry forward. That happens on a routine event: a rebase
    conflicting on the ledger — which every PR in a train touching released
    distributions hits — is correctly resolved by taking main's side wholesale
    rather than hand-merging JSON, which momentarily removes your own row. The
    next regeneration then emits the marker.

    Nothing rejected it. `reconcile` compared declared versions against
    published state and never read `reason` content, so a placeholder passed
    `--check` and every CI gate. It fired on #554's rebase on 2026-08-29 and was
    caught only because the lane had captured the reasons beforehand.

    Why that is worse than it sounds: `reason` is the only field distinguishing
    accepted debt from unnoticed drift. Both produce a row with the same
    `declared` and the same `state`. A placeholder silently converts "we know
    about this and accepted it" into "this drifted and nobody noticed".

    AXIS REACHABILITY (ADR-0018 amendment, 2026-08-26): a guard whose axis
    cannot be reached satisfies the letter of a sensitivity proof while being
    blind. This one is reachable by a source edit available today — delete any
    row from `docs/inventories/declared-publication-baseline.json` and run
    `make publication-baseline`; the row comes back carrying the marker and
    `make publication-check` goes red. That is the same two commands the rebase
    resolution performs by accident.
    """
    sweep = _sweep()
    problems = sweep.reconcile(
        _unpublished_survey(sweep),
        {
            "dotmac-thing": {
                "declared": "0.1.0a1",
                "state": sweep.NEVER_PUBLISHED,
                "reason": sweep.UNWRITTEN_REASON,
            }
        },
    )
    # NAMED, not merely detected: a failure that says only "something is wrong"
    # tells a reader nothing about which row to repair.
    assert any("dotmac-thing" in problem for problem in problems)
    assert any("placeholder" in problem for problem in problems)


def test_the_guard_fires_on_a_placeholder_that_was_half_edited() -> None:
    """Containment, not equality, is why this case is caught.

    Appending a few words to the marker is the row that looks reviewed and is
    not — the reader sees prose and moves on. Equality against the constant
    would pass it.
    """
    sweep = _sweep()
    problems = sweep.reconcile(
        _unpublished_survey(sweep),
        {
            "dotmac-thing": {
                "declared": "0.1.0a1",
                "state": sweep.NEVER_PUBLISHED,
                "reason": sweep.UNWRITTEN_REASON + " \u2014 will fix after the release",
            }
        },
    )
    assert any("placeholder" in problem for problem in problems)


@pytest.mark.parametrize("reason", ["", "   ", "\n\t ", None])
def test_the_guard_fires_on_a_reason_that_says_nothing(reason) -> None:
    """The half a `"TODO" in reason` substring check would miss entirely.

    An empty string, a row of spaces and an absent key are the same defect as
    the placeholder — nobody has said why — with no name to match on. They are
    checked as a PROPERTY (is there text here) rather than as a word, which is
    the whole reason this rejection is not written as a `TODO` scan.
    """
    sweep = _sweep()
    entry = {"declared": "0.1.0a1", "state": sweep.NEVER_PUBLISHED}
    if reason is not None:
        entry["reason"] = reason
    problems = sweep.reconcile(_unpublished_survey(sweep), {"dotmac-thing": entry})
    assert any("states no reason" in problem for problem in problems)
    assert any("dotmac-thing" in problem for problem in problems)


def test_a_real_reason_mentioning_a_todo_elsewhere_is_left_alone() -> None:
    """SPECIFICITY, and the reason the tempting form was rejected.

    A substring match on `TODO` is what this check obviously wants to be, and it
    is precisely the "guard checks a NAME instead of the property it is named
    for" shape ADR-0018's 2026-08-26 amendment ratified. The ledger's reasons
    are paragraphs and several already name follow-up work; a word scan would
    fail one of them for saying so out loud, which is how a guard gets switched
    off. Matching the generator's whole marker sentence cannot make that
    mistake.
    """
    sweep = _sweep()
    assert not sweep.reconcile(
        _unpublished_survey(sweep),
        {
            "dotmac-thing": {
                "declared": "0.1.0a1",
                "state": sweep.NEVER_PUBLISHED,
                "reason": (
                    "INTENDED UNTIL THE PILOT LANDS. The distribution is built "
                    "and withheld on purpose; the remaining TODO is the "
                    "adopter's lock pin, tracked on the release lane, and the "
                    "row deletes itself at tag time."
                ),
            }
        },
    )


def test_the_live_ledger_carries_a_written_reason_on_every_row() -> None:
    """The case that matters as much as the firing ones: a check that rejects
    the REAL ledger is worse than no check at all.

    Eighteen unpublished rows as of 2026-08-31, zero placeholders. Asserted
    through the same function `--check` calls, so this cannot pass while the
    gate would fail.
    """
    sweep = _sweep()
    ledger = _ledger()
    assert ledger, "an empty ledger would make this pass for the wrong reason"
    for distribution, entry in sorted(ledger.items()):
        assert not sweep.unwritten_reason(distribution, entry), distribution


def test_the_placeholder_literal_has_exactly_one_home() -> None:
    """Two copies of the marker is the second-authority defect in miniature.

    The generator emits it and the guard refuses it. If those were two literals,
    editing the emitted one would leave the guard recognising a string nothing
    writes any more — a check that still runs, still passes, and no longer
    checks anything. Both go through `UNWRITTEN_REASON`, and this test is what
    keeps that true; note that it refers to the constant rather than repeating
    the text, for the same reason.
    """
    source = (PROJECT_ROOT / "scripts" / "declared_publication_sweep.py").read_text(
        encoding="utf-8"
    )
    sweep = _sweep()
    assert source.count(sweep.UNWRITTEN_REASON) == 1, (
        "the placeholder text appears more than once in the sweep — it must "
        "exist only at `UNWRITTEN_REASON`, with every other use referring to "
        "that name"
    )


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
