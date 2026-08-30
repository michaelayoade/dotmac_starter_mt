"""Direct password-primitive calls are frozen and may only shrink deliberately.

`dotmac_kernel.credential_lifecycle` is the single owner of human credential
decisions. Every other call to `hash_password`, `verify_password` or
`password_needs_rehash` is a second owner deciding for itself what active,
locked and reset-required mean — which is not hypothetical: Sub has FOUR
verification owners across ten grep-counted sites, and each returns a bare
boolean that cannot carry the answer.

Two-directional, per ADR-0018. A RISE means a new second owner landed. A FALL
without the baseline moving in the same change means the detector stopped
seeing something, which reads exactly like progress — and only this direction
tells the two apart.

**Scope: every Python entry-point family, not one directory.** Hard rule 25 is
concrete here rather than abstract: two of Sub's eleven `hash_password` call
sites are in `scripts/seed/`, so a sweep scoped to `app/` reports nine and
calls the other two absent.

**This repository is enforced; siblings are evidence.** The Starter tree is
measured live and always compared. A sibling is measured from IMMUTABLE GIT
OBJECTS at the commit its baseline row names, so a colleague's in-progress
branch cannot move the number — and when that commit is not in the local clone
the gate abstains rather than scoring it. CI has no fleet beside the checkout,
so in CI the sibling rows abstain by design and the Starter row is the gate.

The measurement is `scripts/credential_lifecycle_sweep.py`; the frozen numbers
are `docs/inventories/credential-lifecycle-baseline.json`; the prose, the
census it reconciles against and the known bounds are
`docs/inventories/credential-lifecycle-sources.md`.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "credential_lifecycle_sweep.py"
BASELINE = PROJECT_ROOT / "docs" / "inventories" / "credential-lifecycle-baseline.json"
INVENTORY = PROJECT_ROOT / "docs" / "inventories" / "credential-lifecycle-sources.md"
FLEET_ROOT = PROJECT_ROOT.parent


def _sweep():
    spec = importlib.util.spec_from_file_location("credential_lifecycle_sweep", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


# ── The artifacts exist and agree ───────────────────────────────────────────


def test_the_baseline_and_its_inventory_exist_and_reference_each_other() -> None:
    assert BASELINE.is_file()
    assert INVENTORY.is_file()
    assert "credential-lifecycle-baseline.json" in INVENTORY.read_text(encoding="utf-8")


def test_the_baseline_records_every_symbol_the_owner_wraps() -> None:
    """A symbol dropped from the sweep would stop being ratcheted and nothing
    else in the repository would notice."""
    sweep = _sweep()
    assert set(sweep.SYMBOLS) == {
        "hash_password",
        "password_needs_rehash",
        "verify_password",
    }
    assert _baseline()["symbols"] == list(sweep.SYMBOLS)


def test_the_symbols_are_exactly_the_kernels_published_password_surface() -> None:
    """Bound to the kernel's own `__all__`, so a fourth primitive cannot be
    published into an unmonitored region."""
    import dotmac_kernel.security as security

    published = {
        name for name in security.__all__ if "password" in name or "hash" in name
    }
    assert published - {"hash_token"} == set(_sweep().SYMBOLS)


def test_every_entry_point_family_is_named_not_a_single_directory() -> None:
    sweep = _sweep()
    families = set(sweep.ENTRY_POINT_FAMILIES)
    assert {
        "application",
        "installable_packages",
        "scripts",
        "migrations",
        "tasks",
        "workers",
        "cli",
        "cron",
    } <= families
    recorded = _baseline()["entry_point_families"]
    assert set(recorded) == families


def test_the_baseline_names_which_families_each_repository_lacks() -> None:
    """ "Absent" and "zero" are different claims. A repository row that listed
    neither would be indistinguishable from one nobody scanned."""
    for repository, row in _baseline()["repositories"].items():
        assert row["families_present"], f"{repository} scanned no family at all"
        assert row["scanned_files"] > 0, repository
        overlap = set(row["families_present"]) & set(row["families_absent"])
        assert not overlap, f"{repository}: {overlap} is both present and absent"


def test_every_recorded_revision_is_a_full_immutable_commit() -> None:
    """Hard rule 30: a branch name is not a coordinate. A row that named one
    would point at a different tree tomorrow."""
    for repository, row in _baseline()["repositories"].items():
        revision = row["revision"]
        assert isinstance(revision, str) and len(revision) == 40, repository
        assert all(
            character in "0123456789abcdef" for character in revision
        ), repository


# ── The owner exemption states an enforceable premise ───────────────────────


def test_each_exempt_file_exists_and_actually_calls_a_primitive() -> None:
    """An exemption for a file that no longer uses the thing it is exempt from
    is a stale exemption, not a safe one — and the stale one is the shape that
    survives a refactor and quietly widens.

    `security.py` is exempt because it DEFINES the primitives, so it is checked
    for definitions rather than calls; `credential_lifecycle.py` is exempt
    because it is the owner, so it is checked for calls.
    """
    sweep = _sweep()
    owner = "packages/dotmac-kernel/src/dotmac_kernel/credential_lifecycle.py"
    definer = "packages/dotmac-kernel/src/dotmac_kernel/security.py"
    assert set(sweep.OWNER_PATHS) == {owner, definer}

    owner_source = (PROJECT_ROOT / owner).read_text(encoding="utf-8")
    calls = sweep.count_calls(owner_source)
    assert set(calls) == set(sweep.SYMBOLS), (
        "the lifecycle owner must call every primitive it owns; if it stopped "
        f"calling one, its exemption for that symbol is stale: {calls}"
    )

    defined = {
        node.name
        for node in ast.walk(ast.parse((PROJECT_ROOT / definer).read_text("utf-8")))
        if isinstance(node, ast.FunctionDef)
    }
    assert set(sweep.SYMBOLS) <= defined


def test_the_test_exclusion_states_its_premise() -> None:
    sweep = _sweep()
    assert "verif" in sweep.TEST_PREMISE
    assert "grandfather" not in sweep.TEST_PREMISE.lower()


def test_reviewed_stays_lexically_distinct_from_grandfathered() -> None:
    """ADR-0018. A baseline entry is FROZEN DEBT, never a review verdict; if the
    two words could be used interchangeably the ratchet would start reading as
    approval."""
    text = (SCRIPT.read_text("utf-8") + BASELINE.read_text("utf-8")).lower()
    assert "grandfathered" not in text
    assert "reviewed and correct" not in text


# ── The ratchet ─────────────────────────────────────────────────────────────


def test_no_repository_grew_a_new_direct_primitive_call() -> None:
    """Never skipped. Sibling rows abstain on their own when unmeasurable, but
    this repository is always compared — a gate that skips wholesale when the
    fleet is missing would be inert in CI, which is the only place it runs."""
    sweep = _sweep()
    baseline = _baseline()
    measured, _absent, _unverified = sweep.measure(FLEET_ROOT, baseline)
    assert (
        sweep.SELF_REPOSITORY in measured
    ), "the repository under test must always be measured"
    failures, _abstentions = sweep.ratchet(measured, baseline)
    assert not failures, "credential-lifecycle ratchet:\n" + "\n".join(failures)


def test_this_repositorys_own_debt_matches_the_baseline_exactly() -> None:
    """The half that actually bites in CI, asserted on its own so a green run
    cannot be explained by everything having abstained."""
    sweep = _sweep()
    baseline = _baseline()
    live = sweep.measure_repository(sweep.SELF_REPOSITORY, PROJECT_ROOT)
    recorded = baseline["repositories"][sweep.SELF_REPOSITORY]["files"]
    assert live.files == recorded, sweep._drift(live.files, recorded, "starter")


# ── Sensitivity proofs (ADR-0018) ───────────────────────────────────────────


def test_the_detector_counts_a_bare_call() -> None:
    assert _sweep().count_calls("hash_password('x')\n") == {"hash_password": 1}


def test_the_detector_counts_an_attribute_call() -> None:
    """`security.hash_password(x)` is the same second owner as the bare name,
    and a Name-only detector would miss every module that imports the module."""
    sweep = _sweep()
    assert sweep.count_calls("security.verify_password(a, b)\n") == {
        "verify_password": 1
    }


def test_the_detector_does_not_count_an_import_or_a_re_export() -> None:
    """SPECIFICITY. Counting mentions makes the number un-actionable: retire the
    caller and the import stays behind, so the ratchet reports no progress and
    the next person stops believing it."""
    sweep = _sweep()
    source = (
        "from dotmac_kernel.security import hash_password, verify_password\n"
        '__all__ = ["hash_password"]\n'
        "# hash_password is called elsewhere\n"
        '"""verify_password appears in this docstring"""\n'
    )
    assert sweep.count_calls(source) == {}


def test_the_detector_does_not_count_a_definition() -> None:
    sweep = _sweep()
    assert sweep.count_calls("def hash_password(p):\n    return p\n") == {}


def test_the_ratchet_fires_when_a_count_rises() -> None:
    sweep = _sweep()
    baseline = _baseline()
    grown = json.loads(json.dumps(baseline))
    row = grown["repositories"][sweep.SELF_REPOSITORY]["files"]
    path = next(iter(row))
    row[path]["hash_password"] = row[path].get("hash_password", 0) + 1
    problems = sweep._drift(
        row, baseline["repositories"][sweep.SELF_REPOSITORY]["files"], "probe"
    )
    assert any("a new direct" in problem for problem in problems), problems


def test_the_ratchet_fires_when_a_count_falls_without_a_rebaseline() -> None:
    """The half a one-directional ratchet misses."""
    sweep = _sweep()
    recorded = _baseline()["repositories"][sweep.SELF_REPOSITORY]["files"]
    shrunk = json.loads(json.dumps(recorded))
    path = next(iter(shrunk))
    symbol = next(iter(shrunk[path]))
    shrunk[path][symbol] -= 1
    problems = sweep._drift(shrunk, recorded, "probe")
    assert any("lower the baseline" in problem for problem in problems), problems


def test_a_brand_new_caller_file_is_detected() -> None:
    """A PLANTED MUTATION rather than an arithmetic edit: the tree is otherwise
    clean, so every assertion above would pass over a near-empty set if the
    file-walking half of the detector silently broke."""
    sweep = _sweep()
    planted = dict(_baseline()["repositories"][sweep.SELF_REPOSITORY]["files"])
    planted["scripts/_planted_second_owner.py"] = {"verify_password": 1}
    problems = sweep._drift(
        planted,
        _baseline()["repositories"][sweep.SELF_REPOSITORY]["files"],
        "probe",
    )
    assert any("_planted_second_owner" in problem for problem in problems), problems


def test_a_planted_caller_in_every_family_is_actually_walked(tmp_path) -> None:
    """The strongest available proof that the sweep reaches each family: build a
    synthetic repository with one offending module per entry-point root and
    assert every one is found. An enumeration nobody exercised is a list, not a
    guard.
    """
    sweep = _sweep()
    expected: set[str] = set()
    for family, roots in sweep.ENTRY_POINT_FAMILIES.items():
        root = roots[0]
        directory = tmp_path / root
        directory.mkdir(parents=True, exist_ok=True)
        module = directory / f"{family}_entry.py"
        module.write_text("hash_password('x')\n", encoding="utf-8")
        expected.add(f"{root}/{family}_entry.py")
    (tmp_path / "top_level_cli.py").write_text(
        "verify_password('a', 'b')\n", encoding="utf-8"
    )
    expected.add("top_level_cli.py")

    measured = sweep.measure_repository("probe", tmp_path)
    assert set(measured.files) == expected, sorted(
        expected.symmetric_difference(measured.files)
    )


def test_a_planted_caller_under_tests_is_deliberately_not_counted(tmp_path) -> None:
    """The other direction: the exclusion must actually exclude, or the premise
    printed beside it is decorative."""
    sweep = _sweep()
    (tmp_path / "app" / "tests").mkdir(parents=True)
    (tmp_path / "app" / "tests" / "helpers.py").write_text(
        "hash_password('x')\n", encoding="utf-8"
    )
    (tmp_path / "app" / "test_login.py").write_text(
        "verify_password('a', 'b')\n", encoding="utf-8"
    )
    assert sweep.measure_repository("probe", tmp_path).files == {}


def test_an_unparseable_file_inside_a_measured_repository_is_named_not_dropped(
    tmp_path,
) -> None:
    """A dropped file silently LOWERS the number the ratchet defends, which is
    indistinguishable from retirement."""
    sweep = _sweep()
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "broken.py").write_text("def (\n", encoding="utf-8")
    measured = sweep.measure_repository("probe", tmp_path)
    assert measured.unreadable, "an unparseable module must be reported"
    failures, _ = sweep.ratchet(
        {"probe": measured},
        {"repositories": {"probe": {"revision": "0" * 40, "files": {}}}},
    )
    assert any("unparseable" in failure for failure in failures), failures


def test_an_unmeasurable_sibling_abstains_and_is_never_scored_zero() -> None:
    """A repository nobody could read must not read as retired debt."""
    sweep = _sweep()
    baseline = {
        "repositories": {
            "dotmac_nowhere": {"revision": "0" * 40, "files": {"a.py": {"x": 1}}}
        }
    }
    measured, absent, unverified = sweep.measure(FLEET_ROOT, baseline)
    assert "dotmac_nowhere" not in measured
    assert absent or unverified
    failures, abstentions = sweep.ratchet(measured, baseline)
    assert any("dotmac_nowhere" in line for line in abstentions)
    assert not any("dotmac_nowhere" in failure for failure in failures)


def test_a_measured_repository_absent_from_the_baseline_fails() -> None:
    """Unmonitored is not clean. Without this, adding a repository to the sweep
    and forgetting the baseline row would look like success."""
    sweep = _sweep()
    measured = {
        "dotmac_surprise": sweep.RepoMeasurement(
            repository="dotmac_surprise", revision="0" * 40
        )
    }
    failures, _ = sweep.ratchet(measured, {"repositories": {}})
    assert any("absent from the baseline" in failure for failure in failures)
