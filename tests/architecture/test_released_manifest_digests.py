"""A published version's manifest is its contract, and a contract does not move.

## The hole

An installation adopts a connector by MANIFEST DIGEST. `mod_intg` stores the
digest a binding was enabled against and `dotmac_integration.spi
.accepts_manifest_digest` decides adoptability by asking whether the plugin
still carries a manifest with that digest.

Nothing stopped a change from editing the manifest of an ALREADY-PUBLISHED
version. Do that and one version name means two contracts: the wheel published
digest A, the repository publishes digest B under the same version string, and
every installation adopted against A becomes unidentifiable — a re-approval
demanded for a contract that never actually changed.

It was live. Five connectors reached this file's first commit declaring their
published version while carrying a different manifest, and TWO of them —
flutterwave and remita — carried two manifests both claiming the same version
string, the published one preserved as historical and a superseding one as
`manifest`. That case is worse than an in-place edit, because
`accepts_manifest_digest` accepted both and nothing could see the collision.

Every existing gate was green. `test_module_version_sync.py` compares three
version SURFACES; `test_declared_publication.py` compares a version to a TAG;
neither reads a manifest.

## Two halves, because either alone is defeatable

The **checked-in ledger** (`docs/inventories/released-manifest-digests.json`) is
what a reviewer reads, and it is what `make manifest-digest-check` compares the
tree against — offline, with no tags and no git at all, which is why it can sit
in the cheap `quality` matrix where `actions/checkout` fetches nothing. On its
own it is defeatable in one commit: edit the manifest, edit the digest beside
it, and the comparison agrees with itself.

So the ledger is also **cross-checked against the tags** here
(`test_the_ledger_agrees_with_every_tag`), in the `unit` job that already has
`fetch-depth: 0`. Doctoring the ledger then requires moving a tag on `origin`,
which is a different and far more visible act. This is exactly the shape
`test_released_migrations.py` uses for released migration BYTES, applied to the
published CONTRACT.

The tag half follows the same fail-closed oracle discipline: a shallow or
tagless checkout is a FAILURE, never a skip. "The oracle was unavailable" is not
evidence that nothing is wrong, and a gate that skips on CI is a gate that spent
its life green while checking nothing.

## What this does NOT claim

It does not claim to reproduce the hash function that ran in August 2026. Both
sides of every comparison are computed by `ConnectorManifest.digest`, the ONE
owner of contract identity — so a difference is a difference in the CONTRACT
rather than in the arithmetic. A change to the digest formula itself moves every
row at once, which is the correct signal: it moves every installation's pin at
once.

It does not stop a published manifest from being WRONG. The repair for that is a
new version, which is what this file insists on.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "released_manifest_sweep.py"
LEDGER = PROJECT_ROOT / "docs" / "inventories" / "released-manifest-digests.json"
LANE = PROJECT_ROOT / ".github" / "release-connectors.json"

_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _sweep():
    spec = importlib.util.spec_from_file_location("released_manifest_sweep", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _survey():
    """The sweep, or a FAILURE — never a skip.

    Same rule as `test_declared_publication.py::_survey`, and for the same
    reason: that module skipped on a refused oracle and reported green on every
    CI run for weeks while checking nothing.
    """
    sweep = _sweep()
    try:
        return sweep, sweep.survey(PROJECT_ROOT), sweep.ledger(PROJECT_ROOT)
    except SystemExit as refusal:
        pytest.fail(
            "the released-manifest sweep could not measure this tree, so this "
            f"gate cannot answer and must not pass: {refusal}"
        )


# ── The live state ───────────────────────────────────────────────────────────


def test_no_published_manifest_digest_has_moved() -> None:
    """The gate. Every version with a tag must still hash to what it published,
    and its manifest must still be carried."""
    sweep, survey, ledger = _survey()
    problems = sweep.reconcile(survey, ledger)
    assert not problems, "released-manifest:\n" + "\n\n".join(problems)


def test_the_ledger_agrees_with_every_tag() -> None:
    """The cross-check, and the half that makes the ledger unforgeable alone.

    Every recorded row must name a tag that exists, peel to the recorded commit,
    and re-derive the recorded digest from the source THAT TAG published; and
    every published tag must have a row. Both directions, per ADR-0018.
    """
    sweep = _sweep()
    try:
        problems = sweep.verify_tags(PROJECT_ROOT)
    except SystemExit as refusal:
        pytest.fail(
            "the publication oracle is incomplete, so this gate cannot answer "
            f"and must not pass: {refusal}"
        )
    assert not problems, "released-manifest tags:\n" + "\n".join(problems)


def test_every_row_carries_immutable_coordinates() -> None:
    """AGENTS.md rule 30. A row here is a PUBLICATION claim, so it needs
    coordinates rather than a version string: the PEELED commit (never the
    annotated tag object), a sha256 digest, and a tag built from the lane's own
    prefix. `release_run` may be empty — an absent second coordinate is honest;
    a guessed one is not — but it is never a branch name or a bare 'latest'."""
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))["released"]
    lane = json.loads(LANE.read_text(encoding="utf-8"))["connectors"]
    assert ledger, "the ledger is empty, so every assertion below is vacuous"
    for distribution, versions in ledger.items():
        prefix = lane[distribution]["tag_prefix"]
        assert versions, f"{distribution}: an empty row says nothing"
        for version, row in versions.items():
            assert row["tag"] == f"{prefix}{version}", f"{distribution} {version}"
            assert _SHA1.fullmatch(row["peeled_commit"]), f"{distribution} {version}"
            assert _SHA256.fullmatch(
                row["manifest_digest"]
            ), f"{distribution} {version}"
            assert isinstance(row["release_run"], str)
            assert row["release_run"] == "" or row["release_run"].isdigit(), (
                f"{distribution} {version}: release_run must be a run ID or empty "
                "— a branch name or 'latest' is not a coordinate"
            )
            assert row["capabilities"] == sorted(row["capabilities"])


def test_the_unmonitored_distributions_are_named() -> None:
    """ADR-0018: an unmonitored region is honest, an implied guard is not.

    This sweep covers CONNECTORS. Installable modules are outside it because
    `ModuleManifest` exposes no digest and no installation adopts a module by
    one — their published bytes are held by `test_released_migrations.py`
    instead. Naming them here stops the scope being read as 'every package'.
    """
    sweep = _sweep()
    outside = sweep.unmonitored(PROJECT_ROOT)
    assert outside, "everything is monitored, which this sweep does not claim"
    assert "dotmac-kernel" in outside
    assert not any(name.startswith("dotmac-connector-") for name in outside), (
        "a connector distribution is missing from .github/release-connectors.json, "
        "so it is silently outside the one guard written for its shape"
    )


def test_every_lane_connector_is_surveyed() -> None:
    """Specificity for the gate above: a survey over an empty set passes for the
    wrong reason, and the lane is the discovery source rather than a list kept
    here."""
    sweep, survey, ledger = _survey()
    lane = json.loads(LANE.read_text(encoding="utf-8"))["connectors"]
    assert set(survey) == {name for name in lane if not name.startswith("$")}
    assert len(survey) >= 7, survey
    assert set(ledger) <= set(survey)
    assert sum(len(rows) for rows in ledger.values()) >= 11


# ── Sensitivity proofs: PLANTED violations on the REAL tree (ADR-0018) ───────
#
# The proofs below perturb the actual survey rather than a synthetic one, so
# they exercise the same wiring the live gate uses — measurement, ledger read
# and reconciliation together. A proof that only drives the pure reconciler
# with hand-made dictionaries shows the function works; it cannot show the
# function is reached.


def _first_published(ledger: dict) -> tuple[str, str]:
    distribution = sorted(ledger)[0]
    return distribution, sorted(ledger[distribution])[0]


def test_the_guard_bites_when_a_published_manifest_is_edited_in_place() -> None:
    """PLANT: change the digest the tree computes for a version that is
    published. This is the whole hole, reproduced against the live tree."""
    sweep, survey, ledger = _survey()
    distribution, version = _first_published(ledger)
    tampered = copy.deepcopy(survey)
    for manifest in tampered[distribution]["manifests"]:
        if manifest["version"] == version:
            manifest["digest"] = "0" * 64
    problems = sweep.reconcile(tampered, ledger)
    assert any(
        distribution in problem and "PUBLISHED with manifest digest" in problem
        for problem in problems
    ), problems
    # And it must refuse the repair that would make everything agree again.
    assert any("Never edit the digest here to match" in p for p in problems)


def test_the_guard_bites_when_a_published_manifest_is_dropped() -> None:
    """PLANT: remove a published version's manifest from the carried set — the
    `historical_manifests` requirement, which is what keeps an adopted digest
    resolvable at all."""
    sweep, survey, ledger = _survey()
    distribution, version = _first_published(ledger)
    tampered = copy.deepcopy(survey)
    tampered[distribution]["manifests"] = [
        manifest
        for manifest in tampered[distribution]["manifests"]
        if manifest["version"] != version
    ]
    problems = sweep.reconcile(tampered, ledger)
    assert any(
        distribution in problem and "no longer carries its manifest" in problem
        for problem in problems
    ), problems


def test_the_guard_bites_when_one_version_names_two_contracts() -> None:
    """PLANT: the exact defect flutterwave and remita shipped — two manifests,
    one version string. `accepts_manifest_digest` accepts both, so nothing else
    in the system can see it."""
    sweep, survey, ledger = _survey()
    distribution, version = _first_published(ledger)
    tampered = copy.deepcopy(survey)
    twin = dict(tampered[distribution]["manifests"][0])
    twin["version"] = version
    twin["digest"] = "1" * 64
    tampered[distribution]["manifests"].append(twin)
    problems = sweep.reconcile(tampered, ledger)
    assert any("TWO manifests both claiming version" in p for p in problems), problems


def test_the_guard_bites_on_a_manifest_for_a_version_that_never_shipped() -> None:
    """PLANT: a historical manifest invented for a version with no tag. An
    adoption window onto a contract nobody could ever have adopted is a claim
    about history, and it is false."""
    sweep, survey, ledger = _survey()
    distribution, _ = _first_published(ledger)
    tampered = copy.deepcopy(survey)
    invented = dict(tampered[distribution]["manifests"][0])
    invented["version"] = "9.9.9a9"
    invented["digest"] = "2" * 64
    tampered[distribution]["manifests"].append(invented)
    problems = sweep.reconcile(tampered, ledger)
    assert any("neither a recorded publication nor" in p for p in problems), problems


def test_the_guard_bites_when_the_readable_half_drifts() -> None:
    """PLANT: leave the digest alone and change the capability list beside it.

    The digest is the enforceable fact; the capability list is what makes a
    review possible. If they may disagree, the reviewable half stops describing
    the enforced one and the file degrades into an opaque hash table.
    """
    sweep, survey, ledger = _survey()
    distribution, version = _first_published(ledger)
    tampered = copy.deepcopy(survey)
    for manifest in tampered[distribution]["manifests"]:
        if manifest["version"] == version:
            manifest["capabilities"] = ["invented.capability.v1"]
    problems = sweep.reconcile(tampered, ledger)
    assert any("readable half drifting" in p for p in problems), problems


def test_the_guard_bites_when_a_recorded_connector_leaves_the_lane() -> None:
    """PLANT: rows for a distribution the lane no longer names. Deleting a lane
    entry must not silently retire the contracts that distribution published."""
    sweep, survey, ledger = _survey()
    problems = sweep.reconcile(
        survey, {**ledger, "dotmac-connector-gone": {"0.1.0a1": {}}}
    )
    assert any("not in .github/release-connectors.json" in p for p in problems)


def test_the_real_tree_produces_no_findings() -> None:
    """SPECIFICITY for all six proofs above: the reconciler must object because
    something is wrong, not because it objects to everything. Without this, a
    detector that always fires would pass every sensitivity proof."""
    sweep, survey, ledger = _survey()
    assert not sweep.reconcile(survey, ledger)


# ── Oracle discipline ───────────────────────────────────────────────────────


def test_an_unusable_oracle_refuses_rather_than_reporting_every_tag_missing(
    tmp_path: Path,
) -> None:
    """A shallow, tagless or non-git checkout would make every publication read
    as absent. That is a property of the clone, not of the releases, and a guard
    that cries wolf on a fresh checkout is one somebody switches off."""
    sweep = _sweep()
    for root in (tmp_path, tmp_path / "nonexistent"):
        with pytest.raises(SystemExit) as refusal:
            sweep.refuse_unusable_oracle(root)
        message = str(refusal.value)
        assert any(
            reason in message
            for reason in ("git tag", "no tags", "git rev-parse", "SHALLOW")
        ), f"a refusal must name the oracle problem it hit, got: {message}"


def test_this_checkout_is_not_shallow() -> None:
    """Sensitivity for the refusal above: if this very checkout were shallow,
    `test_the_ledger_agrees_with_every_tag` would be failing for a reason that
    has nothing to do with any manifest."""
    sweep = _sweep()
    assert sweep.refuse_unusable_oracle(PROJECT_ROOT)


def test_the_gate_fails_closed_rather_than_skipping() -> None:
    """The regression that matters most, pinned at the source level.

    An edit restoring `pytest.skip` on either refusal path would take this whole
    module back to silently green while every behavioural test above kept
    passing. The failure mode is invisible by construction, so it is asserted
    structurally instead.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    body = source[source.index("def _survey()") : source.index("# ── Sensitivity")]
    assert body.count("pytest.fail(") == 2
    # The CALL, not the word: the prose above legitimately explains why skipping
    # was wrong, and a guard that cannot tell prose from code is the same class
    # of defect it exists to catch.
    assert "pytest.skip(" not in body, (
        "a refused oracle must FAIL. Skipping is how a sibling gate spent its "
        "life green on CI without ever running"
    )


def test_the_check_is_wired_into_make_check_and_the_ci_matrix() -> None:
    """A gate nobody runs is not a gate. `test_ci_runs_canonical_check.py`
    proves the matrix equals `make check`'s prerequisites; this proves the
    prerequisite exists at all, so a rename cannot quietly drop it from BOTH
    sides at once and stay green."""
    makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")
    check_line = re.search(r"^check:([^#\n]*)", makefile, re.MULTILINE)
    assert check_line and "manifest-digest-check" in check_line.group(1).split()
    assert "released_manifest_sweep.py --check" in makefile
