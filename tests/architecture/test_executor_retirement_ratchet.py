"""A legacy deployment executor is frozen, proven displaced, and only then gone.

The programme scoreboard reads ZERO retired legacy executors. This is the gate
over the machinery that lets it move — and over the two ways it must not move:

> Deleting scripts before this would remove rollback capability; leaving them
> active afterward creates two executors.

`scripts/executor_retirement.py` is the measurement,
`docs/inventories/executor-retirement/<product>.toml` is each product's typed
census, `docs/inventories/executor-retirement-baseline.json` is what the
ratchet freezes, and `docs/inventories/executor-retirement.md` is the prose and
the receipt schema.

## Two-directional, per family, per ADR-0018

A RISE is a new entrypoint or a new caller. A FALL without the baseline moving
in the same change means the detector stopped seeing something — which reads
exactly like a retirement, and only this direction tells the two apart.

## One planted violation PER FAMILY, and the detector differs by family

The tree is near-clean, so every assertion here would pass over an empty set if
the walking half of the detector silently broke. That is failure mode 1 of the
fleet guard standard. So each of the seven families gets its own planted case
— and the case is shaped by what CAN be detected for that family:

| family           | planted violation                    | detector    |
| ---------------- | ------------------------------------ | ----------- |
| `workflow`       | undeclared `.yml` in `.github/`      | walk        |
| `script`         | undeclared `.sh` in `scripts/`       | walk        |
| `cron`           | undeclared file in `deploy/cron.d/`  | walk        |
| `systemd_unit`   | undeclared `.service` in `deploy/`   | walk        |
| `manual_runbook` | undeclared `.md` in `docs/runbooks/` | walk        |
| `ssh_credential` | absent, with no observer record      | parser      |
| `webhook`        | absent, with no observer record      | parser      |

The last two are the honest ones. No repository walk can enumerate a deploy key
or a third party's webhook registration, so for those families the ONLY thing a
guard can hold is whether somebody claimed the absence and said how. Pretending
a walk covers them would be the unmonitored region wearing a guard's costume.

## An in-situ proof, not only fixtures

`dotmac_governance`'s ADR-0018 conformance backlog records 25 starter guard
files whose firing proof is fixture-only. `test_a_real_planted_script_is_named`
below drives the REAL discovery over the REAL tree against the REAL checked-in
inventory, shows it naming a real path, and restores the tree.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "executor_retirement.py"
BASELINE = PROJECT_ROOT / "docs" / "inventories" / "executor-retirement-baseline.json"
INVENTORY_DIR = PROJECT_ROOT / "docs" / "inventories" / "executor-retirement"
PROSE = PROJECT_ROOT / "docs" / "inventories" / "executor-retirement.md"
_MODULE_NAME = "executor_retirement"


def _sweep():
    """Load the sweep by path, REGISTERED in `sys.modules` before execution.

    The registration is not optional: `@dataclass` resolves a string annotation
    through `sys.modules[cls.__module__]`, so a module executed unregistered
    raises `AttributeError` the moment it defines a dataclass under
    `from __future__ import annotations`.
    """
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[_MODULE_NAME]
        raise
    return module


def _baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def _self_inventory():
    sweep = _sweep()
    path = INVENTORY_DIR / f"{sweep.SELF_REPOSITORY}.toml"
    return sweep.parse_inventory(path.read_text(encoding="utf-8"), source=path.name)


# ── The artifacts exist and agree ───────────────────────────────────────────


def test_the_baseline_inventory_and_prose_exist_and_reference_each_other() -> None:
    sweep = _sweep()
    assert BASELINE.is_file()
    assert PROSE.is_file()
    assert (INVENTORY_DIR / f"{sweep.SELF_REPOSITORY}.toml").is_file()
    prose = PROSE.read_text(encoding="utf-8")
    assert "executor-retirement-baseline.json" in prose
    assert "scripts/executor_retirement.py" in prose


def test_this_repository_adopts_the_contract_it_publishes() -> None:
    """A contract its own repository does not satisfy is a contract whose first
    real exercise happens in somebody else's pull request."""
    sweep = _sweep()
    assert sweep.SELF_REPOSITORY in sweep.ADOPTION_TARGETS
    assert (INVENTORY_DIR / f"{sweep.SELF_REPOSITORY}.toml").is_file()


def test_the_named_first_adopter_is_recorded_as_owing_an_inventory() -> None:
    """ERP is the named first adopter and Sub follows. Naming them is what
    makes UNADOPTED a measurement instead of silence: a product cannot leave
    the expectation by being forgotten."""
    sweep = _sweep()
    assert {"dotmac_erp", "dotmac_sub"} <= set(sweep.ADOPTION_TARGETS)


# ── Entry-point families ────────────────────────────────────────────────────


def test_every_entry_point_family_michael_enumerated_is_present() -> None:
    """Workflow, script, cron, systemd unit, SSH credential, webhook, manual
    runbook. A census covering `scripts/` alone is the classic miss."""
    sweep = _sweep()
    assert set(sweep.FAMILY_NAMES) == {
        "workflow",
        "script",
        "cron",
        "systemd_unit",
        "ssh_credential",
        "webhook",
        "manual_runbook",
    }
    assert set(_baseline()["families"]) == set(sweep.FAMILY_NAMES)


def test_a_family_a_tree_cannot_enumerate_states_why() -> None:
    """An exclusion whose premise is unstated is an unmonitored region, not an
    exemption (ADR-0018 §2). Four of these seven families cannot be walked, and
    each says so in its own words."""
    for family in _sweep().FAMILIES:
        if family.tree_complete:
            assert not family.incompleteness_premise, family.name
        else:
            assert len(family.incompleteness_premise) > 40, family.name


def test_the_baseline_discloses_which_absences_are_only_a_tree_walk() -> None:
    """The ERP lesson, made structural. `dotmac-books.service` was installed
    and DISABLED on a host and appears in no tree; a walk that finds nothing
    has established nothing about any host, and the baseline must say so rather
    than let a clean walk read as a clean estate."""
    sweep = _sweep()
    row = _baseline()["products"][sweep.SELF_REPOSITORY]
    assert "absences_established_by_tree_walk_only" in row
    assert set(row["absences_established_by_tree_walk_only"]) <= set(
        row["families_absent"]
    )


# ── The disposition vocabulary ──────────────────────────────────────────────


def test_reviewed_stays_lexically_distinct_from_grandfathered() -> None:
    """ADR-0018 §4. A baseline entry is FROZEN DEBT, never a review verdict; if
    the two could be spelled interchangeably the ratchet would start reading as
    approval."""
    sweep = _sweep()
    text = (
        SCRIPT.read_text("utf-8")
        + BASELINE.read_text("utf-8")
        + (INVENTORY_DIR / f"{sweep.SELF_REPOSITORY}.toml").read_text("utf-8")
    ).lower()
    assert "grandfathered" not in text
    assert "reviewed and correct" not in text


def test_the_three_disposition_sets_are_disjoint() -> None:
    """Debt, a reviewed verdict and a terminal state are three different
    claims, and a vocabulary that let one term mean two of them would let a
    review verdict pay off debt."""
    sweep = _sweep()
    backlog = set(sweep.BACKLOG_DISPOSITIONS)
    reviewed = set(sweep.REVIEWED_DISPOSITIONS)
    terminal = set(sweep.TERMINAL_DISPOSITIONS)
    assert not backlog & reviewed
    assert not backlog & terminal
    assert not reviewed & terminal
    assert backlog | reviewed | terminal == set(sweep.DISPOSITIONS)


def test_an_active_executor_may_not_be_retired_without_passing_through_frozen() -> None:
    """THE decisive rule: a replacement is not adopted while the displaced
    executor can still act normally. The jump this refuses is exactly the one
    that takes the rollback path away with the script."""
    sweep = _sweep()
    assert "retired" not in sweep.PERMITTED_TRANSITIONS["active_executor"]
    assert "displaced" not in sweep.PERMITTED_TRANSITIONS["active_executor"]
    assert sweep.PERMITTED_TRANSITIONS["frozen"] == ("active_executor", "displaced")
    assert "retired" in sweep.PERMITTED_TRANSITIONS["displaced"]


def test_every_disposition_has_a_transition_row_and_a_stated_meaning() -> None:
    sweep = _sweep()
    assert set(sweep.PERMITTED_TRANSITIONS) == set(sweep.DISPOSITIONS)
    for disposition, meaning in sweep.DISPOSITIONS.items():
        assert len(meaning) > 30, disposition
    for source, targets in sweep.PERMITTED_TRANSITIONS.items():
        assert set(targets) <= set(sweep.DISPOSITIONS), source


# ── The ratchet, as it actually runs ────────────────────────────────────────


def test_no_family_grew_or_lost_an_entrypoint() -> None:
    """Never skipped. The Starter row is always compared, because a gate that
    skips wholesale when a sibling is missing is inert in CI, which is the only
    place it runs."""
    sweep = _sweep()
    baseline = _baseline()
    measured, unadopted, _unverified = sweep.measure(baseline, INVENTORY_DIR)
    assert sweep.SELF_REPOSITORY in measured
    failures, _ = sweep.ratchet(measured, baseline)
    failures.extend(sweep.ratchet_adoption(measured, unadopted, baseline))
    assert not failures, "executor-retirement ratchet:\n" + "\n".join(failures)


def test_this_repositorys_own_census_reconciles_with_its_own_tree() -> None:
    """The half that bites, asserted alone so a green run cannot be explained
    by everything having abstained."""
    sweep = _sweep()
    problems = sweep.reconcile(_self_inventory(), PROJECT_ROOT)
    assert not problems, "\n".join(problems)


def test_the_scoreboard_is_recorded_and_is_still_zero() -> None:
    """Stated as a fact rather than assumed. When the first retirement lands,
    this number and this assertion move together, in the same change, with a
    receipt — which is the whole point of recording it here."""
    baseline = _baseline()
    assert baseline["retired_total"] == 0
    assert sorted(baseline["unadopted"]) == ["dotmac_erp", "dotmac_sub"]


# ── Sensitivity proofs (ADR-0018 §5) ────────────────────────────────────────


def test_the_verb_detector_names_a_real_deployment_command() -> None:
    sweep = _sweep()
    assert "docker compose" in sweep.deployment_verbs("docker compose up -d\n")
    assert "systemctl" in sweep.deployment_verbs("  systemctl restart books\n")
    assert "rsync" in sweep.deployment_verbs("rsync -a ./static/ /var/www/\n")


def test_the_verb_detector_does_not_fire_on_ordinary_ci() -> None:
    """SPECIFICITY. `git checkout` was in this list once and made every release
    workflow inherit a "host source mutation" it never performs. A verb that
    fires on ordinary CI teaches reviewers to override the finding."""
    sweep = _sweep()
    assert sweep.deployment_verbs("git checkout -b release\n") == {}
    assert sweep.deployment_verbs("uses: actions/checkout@v4\n") == {}
    assert sweep.deployment_verbs("# deploy the thing, eventually\n") == {}


def test_a_caller_inherits_its_callees_verbs(tmp_path) -> None:
    """`deployment-adopter.yml` runs no container; it dispatches one that does.
    Judged on its own bytes it reads as inert — the unchecked-caller hole of
    hard rule 37, seen from inside a guard rather than across a wire."""
    sweep = _sweep()
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "scripts" / "inner.sh").write_text(
        "docker compose up -d\n", encoding="utf-8"
    )
    caller = tmp_path / ".github" / "workflows" / "outer.yml"
    caller.write_text("jobs:\n  a:\n    run: scripts/inner.sh\n", encoding="utf-8")
    known = sweep.known_paths(tmp_path)
    direct = sweep.deployment_verbs(caller.read_text(encoding="utf-8"))
    assert direct == {}, "the caller must look inert on its own bytes"
    inherited = sweep.resolve_verbs(tmp_path, ".github/workflows/outer.yml", known)
    assert "docker compose" in inherited
    assert "via scripts/inner.sh" in inherited["docker compose"]


def test_transitive_resolution_terminates_on_a_reference_cycle(tmp_path) -> None:
    sweep = _sweep()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "a.sh").write_text(
        "scripts/b.sh\ndocker compose up\n", encoding="utf-8"
    )
    (tmp_path / "scripts" / "b.sh").write_text("scripts/a.sh\n", encoding="utf-8")
    known = sweep.known_paths(tmp_path)
    assert "docker compose" in sweep.resolve_verbs(tmp_path, "scripts/a.sh", known)


def test_the_ratchet_fires_when_a_family_count_rises() -> None:
    sweep = _sweep()
    recorded = _baseline()["products"][sweep.SELF_REPOSITORY]["families"]
    grown = json.loads(json.dumps(recorded))
    grown["script"]["active_executor"] = grown["script"].get("active_executor", 0) + 1
    problems = sweep.ratchet_family(grown, recorded, "probe")
    assert any("rose" in problem for problem in problems), problems


def test_the_ratchet_fires_when_a_family_count_falls_without_a_rebaseline() -> None:
    """The half a one-directional ratchet misses, and the half this programme
    needs most: a retirement and a broken detector look identical from above."""
    sweep = _sweep()
    recorded = _baseline()["products"][sweep.SELF_REPOSITORY]["families"]
    shrunk = json.loads(json.dumps(recorded))
    shrunk["script"]["active_executor"] -= 1
    problems = sweep.ratchet_family(shrunk, recorded, "probe")
    assert any("lower the baseline" in problem for problem in problems), problems


def test_the_ratchet_sees_a_disposition_move_that_leaves_the_total_still() -> None:
    """Per DISPOSITION, not per family total. An `active_executor` becoming
    `frozen` keeps the count identical and changes everything about what may
    happen next."""
    sweep = _sweep()
    recorded = {"script": {"active_executor": 1}}
    moved = {"script": {"frozen": 1}}
    problems = sweep.ratchet_family(moved, recorded, "probe")
    assert any("active_executor" in problem for problem in problems), problems
    assert any("frozen" in problem for problem in problems), problems


def test_the_adoption_ratchet_fires_in_both_directions() -> None:
    """The scoreboard itself. It rises only with a receipt, and it may not fall
    at all — a `retired` row that vanished is a record being deleted."""
    sweep = _sweep()
    baseline = _baseline()
    measured, unadopted, _ = sweep.measure(baseline, INVENTORY_DIR)

    grown = json.loads(json.dumps(baseline))
    grown["retired_total"] = -1
    assert any(
        "rose" in problem
        for problem in sweep.ratchet_adoption(measured, unadopted, grown)
    )

    shrunk = json.loads(json.dumps(baseline))
    shrunk["retired_total"] = 3
    assert any(
        "does not go down" in problem
        for problem in sweep.ratchet_adoption(measured, unadopted, shrunk)
    )

    forgotten = json.loads(json.dumps(baseline))
    forgotten["unadopted"] = []
    assert any(
        "unadopted set drifted" in problem
        for problem in sweep.ratchet_adoption(measured, unadopted, forgotten)
    )


# ── One planted violation per family ────────────────────────────────────────

#: Families a repository walk can enumerate at all. Each gets a real planted
#: artifact under a real root. The two that are absent from this list —
#: `ssh_credential` and `webhook` — are covered by their own proof below,
#: because no walk can reach a deploy key or a third party's registration and
#: pretending otherwise would be the unmonitored region wearing a costume.
WALKABLE_PLANTS: dict[str, tuple[str, str]] = {
    "workflow": (".github/workflows/_planted_deployer.yml", "run: docker compose up\n"),
    "script": ("scripts/_planted_deployer.sh", "docker compose up -d\n"),
    "cron": ("deploy/cron.d/_planted_deployer", "0 * * * * root /opt/deploy.sh\n"),
    "systemd_unit": ("deploy/systemd/_planted.service", "ExecStart=/opt/deploy.sh\n"),
    "manual_runbook": ("docs/runbooks/_planted.md", "Then run `docker compose up`.\n"),
}


def test_every_walkable_family_has_exactly_one_planted_case() -> None:
    """An enumeration nobody exercised is a list, not a guard."""
    sweep = _sweep()
    walkable = {family.name for family in sweep.FAMILIES if family.roots}
    assert set(WALKABLE_PLANTS) == walkable


@pytest.mark.parametrize("family", sorted(WALKABLE_PLANTS))
def test_an_undeclared_entrypoint_in_each_family_is_caught(family, tmp_path) -> None:
    """Absence is never a disposition. Planted per family and asserted per
    family, so a detector that silently stopped walking one root cannot hide
    behind six that still work."""
    sweep = _sweep()
    relative, body = WALKABLE_PLANTS[family]
    planted = tmp_path / relative
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(body, encoding="utf-8")

    assert (
        relative in sweep.discover(tmp_path)[family]
    ), f"{family}: discovery did not reach {relative}"

    inventory = sweep.Inventory(
        product="probe",
        revision="0" * 40,
        production_targets=(),
        families_present=(family,),
        families_absent=tuple(n for n in sweep.FAMILY_NAMES if n != family),
        absences=(),
        entrypoints=(
            sweep.Entrypoint(
                name="decoy",
                family=family,
                trigger="manual",
                credential="none",
                disposition="active_executor",
                path=relative + ".other",
                targets=("probe-host",),
            ),
        ),
    )
    problems = sweep.reconcile(inventory, tmp_path)
    assert any(
        relative in problem and "not in the inventory" in problem
        for problem in problems
    ), problems


@pytest.mark.parametrize("family", ["ssh_credential", "webhook"])
def test_an_unwalkable_family_declared_absent_without_an_observer_is_refused(
    family,
) -> None:
    """The planted case for the two families NO walk can reach. Their only
    available detector is whether a person claimed the absence and said how —
    which is precisely the record whose absence let a disabled-but-installed
    unit sit unnoticed on a production host."""
    sweep = _sweep()
    text = "\n".join(
        [
            'schema = "ExecutorInventory.v1"',
            'product = "probe"',
            f'revision = "{"0" * 40}"',
            "families_present = []",
            f"families_absent = {json.dumps(list(sweep.FAMILY_NAMES))}",
        ]
        + [
            f'[[family_absence]]\nfamily = "{name}"\nscope = "repository_tree"\n'
            f'observed_at = "2026-08-30"\nobserved_by = "probe"\nmethod = "walked"'
            for name in sweep.FAMILY_NAMES
            if name != family and not sweep.FAMILY_BY_NAME[name].tree_complete
        ]
    )
    with pytest.raises(sweep.InventoryError) as caught:
        sweep.parse_inventory(text, source="probe.toml")
    assert family in str(caught.value)
    assert "unexamined, not empty" in str(caught.value)


def test_a_host_observed_absence_must_name_the_host() -> None:
    """An unnamed host is not an observation, and `host_observed` is the only
    scope that can say anything about a host at all."""
    sweep = _sweep()
    text = "\n".join(
        [
            'schema = "ExecutorInventory.v1"',
            'product = "probe"',
            f'revision = "{"0" * 40}"',
            "families_present = []",
            f"families_absent = {json.dumps(list(sweep.FAMILY_NAMES))}",
        ]
        + [
            f'[[family_absence]]\nfamily = "{name}"\nscope = "host_observed"\n'
            f'observed_at = "2026-08-30"\nobserved_by = "probe"\nmethod = "looked"'
            for name in sweep.FAMILY_NAMES
            if not sweep.FAMILY_BY_NAME[name].tree_complete
        ]
    )
    with pytest.raises(sweep.InventoryError) as caught:
        sweep.parse_inventory(text, source="probe.toml")
    assert "names the host" in str(caught.value)


# ── The reviewed premises are checked, not asserted ─────────────────────────


def test_not_an_executor_is_refused_over_a_file_that_deploys(tmp_path) -> None:
    """The premise is machine-checked, so the verdict cannot be bought by
    copying a comment onto a file that runs `docker compose`."""
    sweep = _sweep()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "quiet.sh").write_text(
        "systemctl restart app\n", encoding="utf-8"
    )
    inventory = sweep.Inventory(
        product="probe",
        revision="0" * 40,
        production_targets=(),
        families_present=("script",),
        families_absent=tuple(n for n in sweep.FAMILY_NAMES if n != "script"),
        absences=(),
        entrypoints=(
            sweep.Entrypoint(
                name="quiet",
                family="script",
                trigger="manual",
                credential="none",
                disposition="not_an_executor",
                path="scripts/quiet.sh",
                premise="looks harmless",
            ),
        ),
    )
    problems = sweep.reconcile(inventory, tmp_path)
    assert any("verdict is refused" in problem for problem in problems), problems


def test_non_production_executor_is_refused_over_a_file_naming_production(
    tmp_path,
) -> None:
    sweep = _sweep()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "rehearse.sh").write_text(
        "ssh root@erp.dotmac.io docker compose up -d\n", encoding="utf-8"
    )
    inventory = sweep.Inventory(
        product="probe",
        revision="0" * 40,
        production_targets=("erp.dotmac.io",),
        families_present=("script",),
        families_absent=tuple(n for n in sweep.FAMILY_NAMES if n != "script"),
        absences=(),
        entrypoints=(
            sweep.Entrypoint(
                name="rehearse",
                family="script",
                trigger="manual",
                credential="none",
                disposition="non_production_executor",
                path="scripts/rehearse.sh",
                premise="disposable host only",
                targets=("disposable",),
            ),
        ),
    )
    problems = sweep.reconcile(inventory, tmp_path)
    assert any("erp.dotmac.io" in problem for problem in problems), problems


def test_a_reviewed_verdict_with_no_premise_is_refused(tmp_path) -> None:
    sweep = _sweep()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "quiet.sh").write_text("echo hello\n", encoding="utf-8")
    inventory = sweep.Inventory(
        product="probe",
        revision="0" * 40,
        production_targets=(),
        families_present=("script",),
        families_absent=tuple(n for n in sweep.FAMILY_NAMES if n != "script"),
        absences=(),
        entrypoints=(
            sweep.Entrypoint(
                name="quiet",
                family="script",
                trigger="manual",
                credential="none",
                disposition="not_an_executor",
                path="scripts/quiet.sh",
            ),
        ),
    )
    problems = sweep.reconcile(inventory, tmp_path)
    assert any("with no premise" in problem for problem in problems), problems


def test_a_declared_artifact_that_vanished_is_named_not_dropped(tmp_path) -> None:
    """A row pointing at nothing cannot be re-checked next time, and a silently
    dropped row LOWERS the number the ratchet defends."""
    sweep = _sweep()
    (tmp_path / "scripts").mkdir()
    inventory = sweep.Inventory(
        product="probe",
        revision="0" * 40,
        production_targets=(),
        families_present=("script",),
        families_absent=tuple(n for n in sweep.FAMILY_NAMES if n != "script"),
        absences=(),
        entrypoints=(
            sweep.Entrypoint(
                name="gone",
                family="script",
                trigger="manual",
                credential="none",
                disposition="active_executor",
                path="scripts/gone.sh",
                targets=("host",),
            ),
        ),
    )
    problems = sweep.reconcile(inventory, tmp_path)
    assert any("needs a receipt" in problem for problem in problems), problems


def test_a_retired_row_without_a_receipt_is_refused(tmp_path) -> None:
    """The scoreboard counts receipts, not deletions."""
    sweep = _sweep()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "old.sh").write_text("echo hi\n", encoding="utf-8")
    inventory = sweep.Inventory(
        product="probe",
        revision="0" * 40,
        production_targets=(),
        families_present=("script",),
        families_absent=tuple(n for n in sweep.FAMILY_NAMES if n != "script"),
        absences=(),
        entrypoints=(
            sweep.Entrypoint(
                name="old",
                family="script",
                trigger="manual",
                credential="none",
                disposition="retired",
                path="scripts/old.sh",
            ),
        ),
    )
    problems = sweep.reconcile(inventory, tmp_path)
    assert any("no receipt identity" in problem for problem in problems), problems


def test_a_secret_shaped_credential_is_refused() -> None:
    """A credential is NAMED in an inventory, never held (ADR-0009). An
    inventory that held one would be the leak it was written to prevent."""
    sweep = _sweep()
    text = "\n".join(
        [
            'schema = "ExecutorInventory.v1"',
            'product = "probe"',
            f'revision = "{"0" * 40}"',
            'families_present = ["script"]',
            "families_absent = "
            + json.dumps([n for n in sweep.FAMILY_NAMES if n != "script"]),
        ]
        + [
            f'[[family_absence]]\nfamily = "{name}"\nscope = "repository_tree"\n'
            f'observed_at = "2026-08-30"\nobserved_by = "probe"\nmethod = "walked"'
            for name in sweep.FAMILY_NAMES
            if name != "script" and not sweep.FAMILY_BY_NAME[name].tree_complete
        ]
        + [
            "[[entrypoint]]",
            'name = "leaky"',
            'family = "script"',
            'trigger = "manual"',
            'credential = "password: hunter2"',
            'disposition = "active_executor"',
        ]
    )
    with pytest.raises(sweep.InventoryError) as caught:
        sweep.parse_inventory(text, source="probe.toml")
    assert "NAMED here, never held" in str(caught.value)


# ── The in-situ proof ───────────────────────────────────────────────────────


def test_a_real_planted_script_is_named_by_the_real_checked_in_census() -> None:
    """IN-SITU, not fixture-only. Bytes on disk in the real `scripts/` tree,
    the real checked-in inventory, the real discovery, inside try/finally —
    then the tree is restored and proven Git-clean.

    A fixture proves the comparator works on data somebody handed it. This
    proves the guard reaches the actual corpus it claims to cover, which is the
    property a near-clean tree makes impossible to infer from a green run.
    """
    sweep = _sweep()
    planted = PROJECT_ROOT / "scripts" / "_planted_second_executor.sh"
    assert not planted.exists(), "the probe path must start clean"

    before = sweep.reconcile(_self_inventory(), PROJECT_ROOT)
    assert not before, "the real census must start clean: " + "\n".join(before)

    try:
        planted.write_text(
            "#!/usr/bin/env bash\ndocker compose up -d\n", encoding="utf-8"
        )
        problems = sweep.reconcile(_self_inventory(), PROJECT_ROOT)
        assert any(
            "scripts/_planted_second_executor.sh" in problem for problem in problems
        ), problems
        assert any("Absence is never a disposition" in p for p in problems), problems
    finally:
        planted.unlink(missing_ok=True)

    assert not planted.exists()
    assert not sweep.reconcile(_self_inventory(), PROJECT_ROOT)
    status = sweep._git(PROJECT_ROOT, "status", "--porcelain", "scripts/") or ""
    assert "_planted_second_executor" not in status, status


def test_the_walk_is_not_vacuous() -> None:
    """A guard that scanned nothing and a guard that found nothing produce the
    same green. Only one of them is working."""
    sweep = _sweep()
    discovered = sweep.discover(PROJECT_ROOT)
    assert len(discovered["workflow"]) >= 10, discovered["workflow"]
    assert len(discovered["script"]) >= 5, discovered["script"]
    assert len(sweep.known_paths(PROJECT_ROOT)) >= 20
