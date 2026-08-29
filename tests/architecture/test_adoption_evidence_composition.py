"""`composed_at`: composition proven by parsing, and proven able to fail.

The gap this closes
-------------------

`AdoptionEvidenceV1` can address a TOML field and nothing else — `path` must
carry a structured extension, because "a checker would have to grep it, and a
grep result is not a field".  Real composition is not in a TOML file.  Vendor
Control Plane composes `dotmac-deployment-control` by naming it in a module
roster in `src/vendor_cp/assembly.py` and by calling its `versions_dir()` inside
`composed_version_locations()` in `src/vendor_cp/migrations.py`.  Neither fact
is expressible as a key path, so the strongest row the vocabulary could carry
was `pinned_at` — and "a pin is installation, not adoption".

That is why nine of the ten dossiers migrated in #496 came out pin-only, and
why the same gap has now been hit on `dotmac-auth-oidc`, both `dotmac-ui`
slices, and `dotmac-deployment-control`.  Four occurrences of one missing
vocabulary item is a missing vocabulary item.

Why these fixtures are the real thing
-------------------------------------

An assertion type validated against a fixture somebody wrote to satisfy it
proves nothing.  `fixtures/vendor_cp_69a877d6/` holds the **verbatim bytes** of
three files from `dotmac_vendor_control_plane` at commit
`69a877d6f0c6886e300f5433020f7f25421e111c` — "Compose Deployment Control, and
cut target authority over to it (#71)".

`test_the_fixtures_are_the_bytes_they_claim_to_be` re-derives each file's **git
blob id** and compares it with `MANIFEST.json`.  A git blob id is
`sha1("blob <len>\\0" + content)` — a pure function of the bytes, so the check
needs no network, no clone and no git binary, yet anyone holding the Vendor
repository can confirm the same id is what that commit stores.  Edit a fixture
to make a test pass and that check goes red.

The mutations are of those real bytes
-------------------------------------

Each refusal below starts from the genuine file and removes exactly one thing.
A synthetic "bad" fixture would only prove the checker rejects something; these
prove it rejects the *specific* ways this claim goes wrong, on the *specific*
shape it will be used against.

`test_the_real_shape_is_accepted` is the positive control.  Without it three
passing refusals would be equally consistent with a checker that rejects
everything.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from tests.architecture import adoption_evidence as evidence

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vendor_cp_69a877d6"
MANIFEST = json.loads((FIXTURES / "MANIFEST.json").read_text(encoding="utf-8"))

VENDOR = "dotmac_vendor_control_plane"
COMMIT = "69a877d6f0c6886e300f5433020f7f25421e111c"
DISTRIBUTION = "dotmac-deployment-control"
PIN = "0.1.0a2"

ASSEMBLY = "src/vendor_cp/assembly.py"
MIGRATIONS = "src/vendor_cp/migrations.py"


def _fixture(repo_path: str) -> str:
    for entry in MANIFEST["files"]:
        if entry["path"] == repo_path:
            return (FIXTURES / entry["fixture"]).read_text(encoding="utf-8")
    raise AssertionError(f"no fixture captured for {repo_path}")


def _sources() -> dict[str, str]:
    return {ASSEMBLY: _fixture(ASSEMBLY), MIGRATIONS: _fixture(MIGRATIONS)}


def _rows() -> list[dict[str, object]]:
    """The two `composed_at` rows the corrected dossier will carry."""
    return [
        {
            "kind": "composed_at",
            "repository": VENDOR,
            "commit": COMMIT,
            "path": ASSEMBLY,
            "module": "dotmac_deployment_control",
            "symbol": "module",
            "construct": "collection_member",
            "within": "STATEFUL_MODULES",
            "proves": "module_registration",
            "expected": (
                "deployment_control_module is a member of STATEFUL_MODULES, the "
                "roster build_spec composes"
            ),
        },
        {
            "kind": "composed_at",
            "repository": VENDOR,
            "commit": COMMIT,
            "path": MIGRATIONS,
            "module": "dotmac_deployment_control",
            "symbol": "versions_dir",
            "construct": "call",
            "within": "composed_version_locations",
            "proves": "migration_lineage",
            "expected": (
                "deployment_control_versions_dir() is called inside "
                "composed_version_locations, so the dc lineage is composed"
            ),
        },
    ]


def _pinned_row() -> dict[str, object]:
    return {
        "kind": "pinned_at",
        "repository": VENDOR,
        "commit": COMMIT,
        "path": "pyproject.toml",
        "field": "tool.poetry.dependencies.dotmac-deployment-control.version",
        "expected": PIN,
    }


# ── the fixtures are what they claim ────────────────────────────────────────


def test_the_fixtures_are_the_bytes_they_claim_to_be() -> None:
    for entry in MANIFEST["files"]:
        data = (FIXTURES / entry["fixture"]).read_bytes()
        blob = hashlib.sha1(
            b"blob %d\0" % len(data) + data, usedforsecurity=False
        ).hexdigest()
        assert blob == entry["blob_sha1"], (
            f"{entry['fixture']} no longer hashes to the git blob id recorded for "
            f"{VENDOR}@{COMMIT[:12]}:{entry['path']}. Either the fixture was "
            "edited — which would make every result below a statement about a "
            "file that does not exist — or the manifest is wrong."
        )
        assert entry["bytes"] == len(data)


def test_the_manifest_pins_an_immutable_commit() -> None:
    assert evidence.IMMUTABLE_COMMIT.fullmatch(MANIFEST["commit"])
    assert MANIFEST["commit"] == COMMIT


# ── positive control ────────────────────────────────────────────────────────


def test_the_real_shape_is_accepted() -> None:
    """Without this, three passing refusals prove only that the walk says no."""
    assert (
        evidence.composition_claim_problems(
            sources=_sources(),
            rows=_rows(),
            pin_source=_fixture("pyproject.toml"),
            distribution=DISTRIBUTION,
            expected_pin=PIN,
        )
        == []
    )


def test_the_rows_are_well_formed_under_the_schema() -> None:
    problems = evidence.evidence_problems(
        rows=[*_rows(), _pinned_row()],
        pointers=[
            {
                "subject": "current_pin",
                "repository": VENDOR,
                "paths": ["pyproject.toml"],
                "field": "tool.poetry.dependencies.dotmac-deployment-control",
            }
        ],
        schema_marker=evidence.SCHEMA_VALUE,
        distribution=DISTRIBUTION,
    )
    assert problems == []


# ── the three refusals, each a single removal from the real bytes ───────────


def test_a_removed_registration_is_refused() -> None:
    """Delete the roster entry. The import, the comment above it and every other
    module stay exactly as they are."""
    mutated = _fixture(ASSEMBLY).replace("    deployment_control_module,\n", "", 1)
    assert mutated != _fixture(ASSEMBLY), "the mutation did not apply"

    problems = evidence.python_composition_problems(
        mutated,
        module="dotmac_deployment_control",
        symbol="module",
        construct="collection_member",
        within="STATEFUL_MODULES",
    )
    assert problems, "a deleted registration was accepted as composition"
    assert "NOT a member of 'STATEFUL_MODULES'" in problems[0]


def test_an_import_only_mutant_is_refused() -> None:
    """The sharpest one. The import stays, so the module name is still all over
    the file — a grep for `deployment_control` still matches nine times, and the
    nine-line explanatory comment naming `deployment_control_module` is still
    there verbatim. Only the roster entry is gone."""
    source = _fixture(ASSEMBLY)
    mutated = source.replace("    deployment_control_module,\n", "", 1)

    assert "from dotmac_deployment_control import module" in mutated
    assert mutated.count("deployment_control") >= 2, (
        "the mutation must leave the name present, or it is not testing the "
        "difference between mentioning and composing"
    )

    assert evidence.python_composition_problems(
        mutated,
        module="dotmac_deployment_control",
        symbol="module",
        construct="collection_member",
        within="STATEFUL_MODULES",
    )


def test_a_wrong_package_version_is_refused() -> None:
    """The pin cross-check. Composition is untouched and still true; only the
    version in the same tree moves."""
    mutated = _fixture("pyproject.toml").replace(
        f'version = "{PIN}"', 'version = "0.1.0a3"'
    )
    assert mutated != _fixture("pyproject.toml")

    problems = evidence.composition_claim_problems(
        sources=_sources(),
        rows=_rows(),
        pin_source=mutated,
        distribution=DISTRIBUTION,
        expected_pin=PIN,
    )
    assert problems, "a pin that disagrees with the dossier was accepted"
    assert any("0.1.0a3" in p and PIN in p for p in problems)


# ── the walk sees syntax, not text ──────────────────────────────────────────


def test_a_comment_mentioning_the_module_is_not_composition() -> None:
    """Vendor's real file carries a nine-line comment naming
    `deployment_control_module` directly above the roster entry. Remove the
    entry and the comment alone must not satisfy the claim — this is the
    property a grep-based checker cannot have."""
    source = _fixture(ASSEMBLY)
    assert (
        "# Composed as a GREENFIELD owner" in source
    ), "the real file no longer carries the comment this test is about"
    mutated = source.replace("    deployment_control_module,\n", "", 1)
    assert "deployment_control_module" in mutated, "the comment should survive"
    assert evidence.python_composition_problems(
        mutated,
        module="dotmac_deployment_control",
        symbol="module",
        construct="collection_member",
        within="STATEFUL_MODULES",
    )


def test_an_uncalled_lineage_import_is_refused() -> None:
    """The migrations half: the import stays, the f-string call is removed."""
    source = _fixture(MIGRATIONS)
    mutated = source.replace('        f"{deployment_control_versions_dir()} "\n', "", 1)
    assert mutated != source, "the mutation did not apply"
    assert "deployment_control_versions_dir" in mutated

    problems = evidence.python_composition_problems(
        mutated,
        module="dotmac_deployment_control",
        symbol="versions_dir",
        construct="call",
        within="composed_version_locations",
    )
    assert problems
    assert "never called inside 'composed_version_locations'" in problems[0]


def test_the_checker_never_imports_the_file_it_reads() -> None:
    """`ast.parse` builds a tree from text. Nothing here imports, compiles for
    execution, or resolves the consumer's dependencies — which this repository
    does not have and must not acquire."""
    source = Path(evidence.__file__).read_text(encoding="utf-8")
    for forbidden in ("exec(", "eval(", "importlib", "__import__", "subprocess"):
        assert forbidden not in source, (
            f"{forbidden!r} appears in the evidence module; the composition "
            "checker must be a pure parse of bytes"
        )


# ── shape refusals ──────────────────────────────────────────────────────────


def test_composed_at_is_adoption_proving_and_pinned_at_is_not() -> None:
    assert "composed_at" in evidence.ADOPTION_PROVING_KINDS
    assert "pinned_at" in evidence.INSTALLATION_KINDS
    assert not (evidence.INSTALLATION_KINDS & evidence.ADOPTION_PROVING_KINDS)


def test_composed_at_satisfies_the_adoption_status_coupling() -> None:
    assert (
        evidence.adoption_state_problems(
            status="adopted",
            rows=[*_rows(), _pinned_row()],
            adoption_states=frozenset({"adopted", "reuse-proven"}),
        )
        == []
    )


def test_a_pin_alone_still_cannot_claim_adoption() -> None:
    """The ruling this kind exists to serve, restated as a test."""
    assert evidence.adoption_state_problems(
        status="adopted",
        rows=[_pinned_row()],
        adoption_states=frozenset({"adopted", "reuse-proven"}),
    )


def test_composition_without_a_pin_for_the_same_tree_is_refused() -> None:
    problems = evidence.evidence_problems(
        rows=_rows(),
        pointers=None,
        schema_marker=evidence.SCHEMA_VALUE,
        distribution=DISTRIBUTION,
    )
    assert any("no `pinned_at` row for that same tree" in p for p in problems)


def test_a_pin_from_a_different_commit_does_not_satisfy_the_coherence_rule() -> None:
    other = dict(_pinned_row())
    other["commit"] = "0" * 40
    problems = evidence.evidence_problems(
        rows=[*_rows(), other],
        pointers=[
            {
                "subject": "current_pin",
                "repository": VENDOR,
                "paths": ["pyproject.toml"],
                "field": "tool.poetry.dependencies.dotmac-deployment-control",
            }
        ],
        schema_marker=evidence.SCHEMA_VALUE,
        distribution=DISTRIBUTION,
    )
    assert any("no `pinned_at` row for that same tree" in p for p in problems)


def test_only_one_half_of_the_composition_is_refused() -> None:
    for keep in (0, 1):
        problems = evidence.composition_claim_problems(
            sources=_sources(),
            rows=[_rows()[keep]],
            pin_source=_fixture("pyproject.toml"),
            distribution=DISTRIBUTION,
            expected_pin=PIN,
        )
        assert any(
            "missing" in p for p in problems
        ), "one half of a composition claim was accepted as the whole"


@pytest.mark.parametrize(
    ("mutation", "fragment"),
    [
        ({"path": "pyproject.toml"}, "not a Python source file"),
        ({"construct": "mentioned"}, "construct 'mentioned' is unknown"),
        ({"proves": "vibes"}, "proves 'vibes' is unknown"),
        ({"symbol": "not an identifier"}, "must be a Python identifier"),
        ({"module": "dotmac deployment control"}, "dotted Python path"),
        ({"field": "tool.poetry"}, "must not carry 'field'"),
        ({"expected": ""}, "must record in `expected`"),
    ],
)
def test_malformed_composed_at_rows_are_refused(
    mutation: dict[str, object], fragment: str
) -> None:
    row = {**_rows()[0], **mutation}
    problems = evidence.evidence_problems(
        rows=[row, _pinned_row()],
        pointers=[
            {
                "subject": "current_pin",
                "repository": VENDOR,
                "paths": ["pyproject.toml"],
                "field": "tool.poetry.dependencies.dotmac-deployment-control",
            }
        ],
        schema_marker=evidence.SCHEMA_VALUE,
        distribution=DISTRIBUTION,
    )
    assert any(fragment in p for p in problems), (problems, fragment)


def test_a_moving_ref_is_refused_here_too() -> None:
    row = {**_rows()[0], "commit": "main"}
    problems = evidence.evidence_problems(
        rows=[row],
        pointers=None,
        schema_marker=evidence.SCHEMA_VALUE,
        distribution=DISTRIBUTION,
    )
    assert any("moving ref" in p for p in problems)


def test_the_starter_cannot_assert_its_own_composition() -> None:
    row = {**_rows()[0], "repository": evidence.SELF_REPOSITORY}
    problems = evidence.evidence_problems(
        rows=[row],
        pointers=None,
        schema_marker=evidence.SCHEMA_VALUE,
        distribution=DISTRIBUTION,
    )
    assert any("cannot assert its own adoption" in p for p in problems)


def test_an_unresolved_source_is_reported_rather_than_skipped() -> None:
    problems = evidence.composition_claim_problems(
        sources={ASSEMBLY: _fixture(ASSEMBLY)},
        rows=_rows(),
        pin_source=_fixture("pyproject.toml"),
        distribution=DISTRIBUTION,
        expected_pin=PIN,
    )
    assert any("unresolved" in p for p in problems)


def test_both_poetry_pin_spellings_are_understood() -> None:
    inline = _fixture("pyproject.toml")
    assert evidence.declared_dependency_version(inline, DISTRIBUTION) == PIN
    bare = '[tool.poetry.dependencies]\n"dotmac-deployment-control" = "0.1.0a9"\n'
    assert evidence.declared_dependency_version(bare, DISTRIBUTION) == "0.1.0a9"
    assert evidence.declared_dependency_version(bare, "absent") is None


# ── composition is not production, and that is structural ───────────────────
#
# The first version of this section shipped a predicate that contradicted its
# own rationale: it tested `kinds & ATTESTATION_KINDS`, so a `workflow_run` or
# an `image_digest` row flipped it to "runtime evidence exists" while the file
# beside it argued neither proves a system ran. It passed because the only
# mutation planted was `live_observation`.
#
# The mutation set below covers the vocabulary instead of one member of it.


def _deploy(**over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "kind": "deploy_run",
        "repository": VENDOR,
        "commit": COMMIT,
        "run_id": "40000000001",
        "environment": "production",
        "outcome": "succeeded",
        "observed": "the dc lineage applied by the production deploy",
        "observed_at": "2026-08-20",
        "observed_by": "michaelayoade",
    }
    row.update(over)
    return row


def _observation(**over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "kind": "live_observation",
        "repository": VENDOR,
        "commit": COMMIT,
        "subject": "mod_deploy",
        "deployment_run_id": "40000000001",
        "observed": "mod_deploy present and serving",
        "observed_at": "2026-08-25",
        "observed_by": "michaelayoade",
    }
    row.update(over)
    return row


TODAY = date(2026, 8, 29)


def test_composed_at_can_never_be_runtime_evidence() -> None:
    assert "composed_at" not in evidence.RUNTIME_CLAIM_KINDS
    assert "adopted" not in evidence.RUNTIME_CLAIM_KINDS
    assert evidence.RUNTIME_CLAIM_KINDS <= evidence.ATTESTATION_KINDS, (
        "a runtime claim cannot be a fact about a file: no file in any tree can "
        "state that a system is running"
    )
    assert "workflow_run" not in evidence.RUNTIME_CLAIM_KINDS
    assert "image_digest" not in evidence.RUNTIME_CLAIM_KINDS


def test_the_two_axes_are_orthogonal_and_that_is_the_point() -> None:
    proving = evidence.ADOPTION_PROVING_KINDS
    runtime = evidence.RUNTIME_CLAIM_KINDS
    assert proving & runtime, "live_observation should be in both"
    assert proving - runtime, "composed_at and adopted are source-level"
    assert runtime - proving, "deploy_run bears on running, not on composition"


@pytest.mark.parametrize("kind", sorted(evidence.EVIDENCE_KINDS))
def test_every_kind_is_on_exactly_one_side_of_the_runtime_axis(kind: str) -> None:
    """COVERAGE over the vocabulary, which is what the first version lacked.

    A kind added later without a decision would silently count as non-runtime
    and weaken `has_no_runtime_evidence` by omission rather than by choice.
    """
    rows = [{"kind": kind}]
    assert evidence.has_no_runtime_evidence(rows) is (
        kind not in evidence.RUNTIME_CLAIM_KINDS
    )


@pytest.mark.parametrize("kind", ["workflow_run", "image_digest"])
def test_ci_and_build_evidence_leave_a_dossier_runtime_free(kind: str) -> None:
    """THE REGRESSION. These two are exactly what the previous predicate got
    wrong: it read them as runtime evidence while arguing they were not."""
    rows = [*_rows(), _pinned_row(), {"kind": kind}]
    assert evidence.has_no_runtime_evidence(rows), (
        f"{kind} was read as evidence that something ran; CI exercising a tree "
        "is not that tree running anything for anyone, and a built image is not "
        "a deployed one"
    )
    # ...and the literal source predicate correctly says otherwise, which is
    # why there are two of them.
    assert not evidence.rests_on_source_alone(rows)


def test_this_package_makes_no_runtime_claim() -> None:
    rows = [*_rows(), _pinned_row()]
    assert evidence.has_no_runtime_evidence(rows)
    assert evidence.rests_on_source_alone(rows)
    assert evidence.proves_source_composition(rows)
    assert not evidence.proves_production_deployment(rows)
    assert not evidence.proves_currently_running(rows, as_of=TODAY)

    # SENSITIVITY: both predicates must stop firing, or they are constants.
    with_runtime = [*rows, _observation()]
    assert not evidence.has_no_runtime_evidence(with_runtime)
    assert not evidence.rests_on_source_alone(with_runtime)
    assert not evidence.has_no_runtime_evidence([_deploy()])

    # The two predicates answer differently on an empty list, and the asymmetry
    # is deliberate rather than an oversight: an empty dossier genuinely makes
    # no runtime claim, but it does not rest on source either, because it rests
    # on nothing. A caller reading "no runtime evidence" as "safe" must also
    # have checked that some evidence exists.
    assert evidence.has_no_runtime_evidence([])
    assert not evidence.rests_on_source_alone([])


# ── claim 2: a deploy receipt that is not a production deployment ───────────


def test_a_production_deploy_receipt_is_accepted() -> None:
    """Positive control. Without it every refusal below is consistent with a
    predicate that refuses everything."""
    assert evidence.proves_production_deployment([_deploy()])


@pytest.mark.parametrize(
    "environment", ["staging", "rehearsal", "disposable", "test", "preview", "ci"]
)
def test_a_non_production_deploy_is_not_a_production_deployment(
    environment: str,
) -> None:
    """A rehearsal on a disposable host is a real deploy of the wrong thing."""
    assert not evidence.proves_production_deployment([_deploy(environment=environment)])


def test_a_deploy_with_no_environment_cannot_be_production() -> None:
    """Fail closed on absence. An unnamed environment is not 'probably
    production' — and the five deploy_run rows already in this tree carry no
    environment at all, so this is the live case rather than a hypothetical."""
    row = _deploy()
    del row["environment"]
    assert not evidence.proves_production_deployment([row])


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "timed_out", "in_progress"])
def test_a_deploy_that_did_not_succeed_is_not_a_deployment(outcome: str) -> None:
    assert not evidence.proves_production_deployment([_deploy(outcome=outcome)])


def test_a_deploy_with_no_outcome_cannot_be_production() -> None:
    row = _deploy()
    del row["outcome"]
    assert not evidence.proves_production_deployment([row])


def test_a_deploy_with_no_immutable_commit_is_not_bound_to_what_was_deployed() -> None:
    assert not evidence.proves_production_deployment([_deploy(commit="main")])


# ── claim 3: deployed once is not running now ───────────────────────────────


def test_a_fresh_bound_observation_proves_currently_running() -> None:
    assert evidence.proves_currently_running([_deploy(), _observation()], as_of=TODAY)


def test_a_historical_production_deploy_alone_is_not_currently_running() -> None:
    """THE SEPARATION. Claim 2 satisfied, claim 3 not — different claims,
    different failure modes."""
    rows = [_deploy()]
    assert evidence.proves_production_deployment(rows)
    assert not evidence.proves_currently_running(rows, as_of=TODAY)


def test_a_stale_observation_is_not_currently_running() -> None:
    """An observation is a statement about a moment. Rendering a year-old one as
    present tense is the defect."""
    rows = [_deploy(), _observation(observed_at="2026-01-01")]
    assert not evidence.proves_currently_running(rows, as_of=TODAY)
    # ...and it becomes current again only by widening the window ON PURPOSE.
    assert evidence.proves_currently_running(rows, as_of=TODAY, max_age_days=400)


def test_an_unbound_observation_cannot_say_what_is_running() -> None:
    """Without the receipt binding, the observation cannot say the running thing
    is the thing that was authorised."""
    row = _observation()
    del row["deployment_run_id"]
    assert not evidence.proves_currently_running([_deploy(), row], as_of=TODAY)


def test_an_observation_bound_to_a_staging_deploy_is_not_production() -> None:
    """The two refusals compose: a real, fresh, correctly bound observation of a
    STAGING deploy must not read as production."""
    rows = [_deploy(environment="staging"), _observation()]
    assert not evidence.proves_currently_running(rows, as_of=TODAY)


def test_an_observation_dated_in_the_future_is_refused() -> None:
    rows = [_deploy(), _observation(observed_at="2027-01-01")]
    assert not evidence.proves_currently_running(rows, as_of=TODAY)


def test_the_claim_predicates_are_independent() -> None:
    """Each of the three can hold without the others, or the separation is
    decorative."""
    source = [*_rows(), _pinned_row()]
    deployed = [_deploy()]
    running = [_deploy(), _observation()]

    assert evidence.proves_source_composition(source)
    assert not evidence.proves_production_deployment(source)

    assert not evidence.proves_source_composition(deployed)
    assert evidence.proves_production_deployment(deployed)
    assert not evidence.proves_currently_running(deployed, as_of=TODAY)

    assert evidence.proves_currently_running(running, as_of=TODAY)


# ── shape refusals for the new fields ───────────────────────────────────────


@pytest.mark.parametrize(
    ("row", "fragment"),
    [
        (
            {"kind": "workflow_run", "environment": "production"},
            "must not carry 'environment'",
        ),
        ({"kind": "image_digest", "outcome": "succeeded"}, "must not carry 'outcome'"),
        (
            {"kind": "deploy_run", "deployment_run_id": "1"},
            "must not carry 'deployment_run_id'",
        ),
        (
            {"kind": "deploy_run", "outcome": "probably-fine"},
            "outcome 'probably-fine' is unknown",
        ),
        (
            {"kind": "deploy_run", "environment": ""},
            "environment must be a non-empty string",
        ),
    ],
)
def test_a_claim_field_on_the_wrong_kind_is_refused(
    row: dict[str, object], fragment: str
) -> None:
    base = {
        "repository": VENDOR,
        "commit": COMMIT,
        "observed": "x",
        "observed_at": "2026-08-20",
        "observed_by": "michaelayoade",
        "run_id": "1",
        "digest": "sha256:" + "0" * 64,
    }
    identity = evidence.ATTESTATION_IDENTITY[str(row["kind"])]
    candidate = {
        **{
            k: v
            for k, v in base.items()
            if k
            in (
                identity,
                "repository",
                "commit",
                "observed",
                "observed_at",
                "observed_by",
            )
        },
        **row,
    }
    problems = evidence.evidence_problems(
        rows=[candidate, *_rows(), _pinned_row()],
        pointers=[
            {
                "subject": "current_pin",
                "repository": VENDOR,
                "paths": ["pyproject.toml"],
                "field": "tool.poetry.dependencies.dotmac-deployment-control",
            }
        ],
        schema_marker=evidence.SCHEMA_VALUE,
        distribution=DISTRIBUTION,
    )
    assert any(fragment in p for p in problems), (fragment, problems)
