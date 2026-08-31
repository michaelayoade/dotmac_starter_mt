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
        "runtime_reactivation",
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
    assert sorted(baseline["unadopted"]) == [
        "dotmac_erp",
        "dotmac_sub",
        "dotmac_vendor_control_plane",
    ]


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
    "runtime_reactivation": (
        "deploy/docker-compose._planted.yml",
        "services:\n  app:\n    restart: unless-stopped\n",
    ),
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


# ── The eighth family: runtime reactivation (2026-08-31 amendment) ──────────


def test_the_family_is_named_for_the_capability_not_the_policy() -> None:
    """`runtime_reactivation`, deliberately NOT `restart_policy`. Docker's
    `restart: unless-stopped` normally brings back THE SAME CONTAINER, which is
    not a deployment — a family named after the policy would describe the wrong
    thing and sweep in every benign case. The property that makes one of these
    an executor is that it can return a DISPLACED executor after a reboot."""
    sweep = _sweep()
    assert "runtime_reactivation" in sweep.FAMILY_NAMES
    assert "restart_policy" not in sweep.FAMILY_NAMES
    assert not sweep.FAMILY_BY_NAME["runtime_reactivation"].tree_complete


def test_the_directive_detector_fires_on_a_reactivating_policy() -> None:
    sweep = _sweep()
    assert sweep.reactivation_directives("    restart: unless-stopped\n")
    assert sweep.reactivation_directives("Restart=always\n")
    assert sweep.reactivation_directives("@reboot /opt/deploy.sh\n")
    assert sweep.reactivation_directives("WantedBy=multi-user.target\n")


def test_the_directive_detector_does_not_fire_on_a_non_reactivating_policy() -> None:
    """SPECIFICITY, and it is the whole distinction: `restart: "no"` is the
    correct shape for a one-shot migration service, and a detector that fired
    on it would refuse the verdict of the most carefully written file in the
    tree."""
    sweep = _sweep()
    assert sweep.reactivation_directives('    restart: "no"\n') == {}
    assert sweep.reactivation_directives("    restart: no\n") == {}
    assert sweep.reactivation_directives("# restart: always was removed\n") == {}


def test_calls_not_mentions_a_usage_comment_is_not_a_deployment() -> None:
    """The defect the eighth family exposed on its first run.
    `docker-compose.dev.yml` carries a usage comment reading `docker compose -f
    ... up`, and the verb detector read it as a deployment. A guard that fires
    on documentation gets overridden, and then it gets ignored."""
    sweep = _sweep()
    assert sweep.deployment_verbs("#   docker compose -f a.yml up\n") == {}
    assert sweep.deployment_verbs("   # rsync the static files\n") == {}
    assert sweep.deployment_verbs("docker compose up -d  # start it\n"), (
        "an INLINE trailing comment must keep its command; over-stripping "
        "produces false negatives, which is the direction that hides an executor"
    )


def test_a_path_named_in_prose_does_not_draw_a_call_edge() -> None:
    """A path mention is symmetric; invocation is not. `docker-compose.yml`
    names `scripts/deploy.sh` in a comment — the script that operates ON it —
    and matching raw text drew the edge backwards, so the topology inherited
    the verbs of the executor that deploys it."""
    sweep = _sweep()
    root = PROJECT_ROOT
    known = sweep.known_paths(root)
    compose = sweep.read_artifact(root, "docker-compose.yml") or ""
    assert "scripts/deploy.sh" in compose, "the fixture premise must still hold"
    assert sweep.resolve_verbs(root, "docker-compose.yml", known) == {}
    # and the real `uses:`/`run:` edge must survive the same change
    adopter = sweep.resolve_verbs(
        root, ".github/workflows/deployment-adopter.yml", known
    )
    assert "docker-compose" in adopter


def test_not_an_executor_is_refused_over_a_live_reactivation_directive(
    tmp_path,
) -> None:
    sweep = _sweep()
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "c.yml").write_text(
        "services:\n  a:\n    restart: always\n", encoding="utf-8"
    )
    problems = sweep.reconcile(
        _probe_inventory(
            sweep,
            "runtime_reactivation",
            sweep.Entrypoint(
                name="r",
                family="runtime_reactivation",
                trigger="daemon",
                credential="none",
                disposition="not_an_executor",
                path="deploy/c.yml",
                premise="looks inert",
            ),
        ),
        tmp_path,
    )
    assert any("is not nothing" in problem for problem in problems), problems


def test_the_weaker_verdict_is_refused_when_there_is_no_directive(tmp_path) -> None:
    """The other direction, so `reactivates_no_declared_executor` does not
    become the place everything lands."""
    sweep = _sweep()
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "c.yml").write_text("services:\n  a: {}\n", encoding="utf-8")
    problems = sweep.reconcile(
        _probe_inventory(
            sweep,
            "runtime_reactivation",
            sweep.Entrypoint(
                name="r",
                family="runtime_reactivation",
                trigger="daemon",
                credential="none",
                disposition="reactivates_no_declared_executor",
                path="deploy/c.yml",
                premise="capability with no subject",
            ),
        ),
        tmp_path,
    )
    assert any("is `not_an_executor`" in problem for problem in problems), problems


def test_the_no_subject_claim_is_checked_in_both_directions(tmp_path) -> None:
    """A one-way check is satisfied by declining to fill in your own field."""
    sweep = _sweep()
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "c.yml").write_text(
        "services:\n  a:\n    restart: always\n", encoding="utf-8"
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "d.sh").write_text("docker compose up\n", encoding="utf-8")
    inventory = sweep.Inventory(
        product="probe",
        revision="0" * 40,
        production_targets=(),
        families_present=("runtime_reactivation", "script"),
        families_absent=tuple(
            n for n in sweep.FAMILY_NAMES if n not in ("runtime_reactivation", "script")
        ),
        absences=(),
        entrypoints=(
            sweep.Entrypoint(
                name="r",
                family="runtime_reactivation",
                trigger="daemon",
                credential="none",
                disposition="reactivates_no_declared_executor",
                path="deploy/c.yml",
                premise="claims no subject",
            ),
            sweep.Entrypoint(
                name="d",
                family="script",
                trigger="manual",
                credential="none",
                disposition="active_executor",
                path="scripts/d.sh",
                targets=("host",),
                reactivates=("r",),
            ),
        ),
    )
    problems = sweep.reconcile(inventory, tmp_path)
    assert any("BOTH directions" in problem for problem in problems), problems


def test_a_reactivation_pointing_at_nothing_is_refused() -> None:
    """Unfalsifiable. A mechanism naming a subject nobody declared can never be
    proven gone."""
    sweep = _sweep()
    text = "\n".join(
        [
            'schema = "ExecutorInventory.v1"',
            'product = "probe"',
            f'revision = "{"0" * 40}"',
            'families_present = ["runtime_reactivation"]',
            "families_absent = "
            + json.dumps(
                [n for n in sweep.FAMILY_NAMES if n != "runtime_reactivation"]
            ),
        ]
        + [
            f'[[family_absence]]\nfamily = "{name}"\nscope = "repository_tree"\n'
            f'observed_at = "2026-08-31"\nobserved_by = "probe"\nmethod = "walked"'
            for name in sweep.FAMILY_NAMES
            if name != "runtime_reactivation"
            and not sweep.FAMILY_BY_NAME[name].tree_complete
        ]
        + [
            "[[entrypoint]]",
            'name = "r"',
            'family = "runtime_reactivation"',
            'trigger = "daemon"',
            'credential = "none"',
            'disposition = "reactivation_capable"',
            'reactivates = ["a-ghost"]',
        ]
    )
    with pytest.raises(sweep.InventoryError) as caught:
        sweep.parse_inventory(text, source="probe.toml")
    assert "cannot be proven gone" in str(caught.value)


def _probe_inventory(sweep, family, entry):
    return sweep.Inventory(
        product="probe",
        revision="0" * 40,
        production_targets=(),
        families_present=(family,),
        families_absent=tuple(n for n in sweep.FAMILY_NAMES if n != family),
        absences=(),
        entrypoints=(entry,),
    )


# ── SshCredentialConstraintV1 (2026-08-31 amendment, v3) ────────────────────
#
# v2 could COUNT a key and could not CHARACTERISE it. ERP is the live instance:
# eight root keys on the production host, none carrying `from=`, `command=` or
# `restrict`, and the deployment authority is those keys rather than any
# workflow.
#
# The gate is on `retained_rollback` because THIS model creates that retention.
# The legacy executor's bytes are kept rather than deleted, so a rollback
# credential survives every retirement — and a retained key that can open an
# interactive shell is not a rollback path, it is the executor still reachable
# by hand.


def _constraint(**overrides) -> dict:
    """A fully constrained rollback key. Each test below breaks exactly one."""
    row = {
        "fingerprint": "SHA256:0xJ7Yq2mQz8fVn4pL1sT6wXbC3dE5gH9kM2nR4tU7vY0",
        "principal": "root",
        "source_restriction": "10.10.0.4/32",
        "forced_command_digest": "sha256:" + "b" * 64,
        "restrict": "present",
        "pty": "denied",
        "agent_forwarding": "denied",
        "port_forwarding": "denied",
        "x11_forwarding": "denied",
        "host": "erp.dotmac.io",
        "observed_at": "2026-08-31",
        "observed_by": "michaelayoade",
        "method": "read /root/.ssh/authorized_keys on the named host",
    }
    row.update(overrides)
    return {key: value for key, value in row.items() if value is not None}


def _key_entry(sweep, **overrides):
    return sweep.Entrypoint(
        name="ssh:erp-rollback-key",
        family="ssh_credential",
        trigger="an operator opening a session to the deploy host",
        credential="erp-deploy-host-rollback-key",
        disposition="retained_rollback",
        rollback_for="erp-deploy-sh-2026-09-12",
        host="erp.dotmac.io",
        ssh_constraint=sweep.parse_ssh_constraint(_constraint(**overrides), "probe"),
    )


def test_the_constraint_records_every_field_michael_enumerated() -> None:
    sweep = _sweep()
    assert {
        "fingerprint",
        "principal",
        "source_restriction",
        "forced_command_digest",
        "restrict",
        "pty",
        "agent_forwarding",
        "port_forwarding",
        "x11_forwarding",
        "host",
        "observed_at",
        "observed_by",
        "method",
    } == set(sweep.SSH_CONSTRAINT_REQUIRED)


def test_a_fully_constrained_retained_key_is_admitted() -> None:
    """The negative control. Three refusals below prove the gate can say no;
    only this proves it says no to the right thing, at the same reach."""
    sweep = _sweep()
    assert sweep.rollback_key_failures(_key_entry(sweep)) == []


def test_removing_restrict_alone_is_refused() -> None:
    """Planted SEPARATELY. A detector that fires only when everything is wrong
    passes the realistic failure: one protection quietly dropped."""
    sweep = _sweep()
    failures = sweep.rollback_key_failures(
        _key_entry(sweep, restrict="absent", pty="permitted")
    )
    assert len(failures) == 1, failures
    assert "INCAPABLE OF AN INTERACTIVE SHELL" in failures[0]


def test_removing_the_source_restriction_alone_is_refused() -> None:
    sweep = _sweep()
    failures = sweep.rollback_key_failures(
        _key_entry(sweep, source_restriction=sweep.SOURCE_UNRESTRICTED)
    )
    assert len(failures) == 1, failures
    assert "SOURCE-RESTRICTED" in failures[0]


def test_removing_the_forced_command_alone_is_refused() -> None:
    sweep = _sweep()
    failures = sweep.rollback_key_failures(
        _key_entry(sweep, forced_command_digest=sweep.FORCED_COMMAND_NONE)
    )
    assert len(failures) == 1, failures
    assert "FORCED-COMMAND-ONLY" in failures[0]


def test_erps_measured_shape_fails_all_three_independently() -> None:
    """Eight unrestricted root keys, none carrying `from=`, `command=` or
    `restrict`. Asserted as three separate findings, because that is how a
    partial repair gets reported honestly."""
    sweep = _sweep()
    failures = sweep.rollback_key_failures(
        _key_entry(
            sweep,
            source_restriction=sweep.SOURCE_UNRESTRICTED,
            forced_command_digest=sweep.FORCED_COMMAND_NONE,
            restrict="absent",
            pty="permitted",
            agent_forwarding="permitted",
        )
    )
    assert len(failures) == 3, failures


def test_a_forced_command_is_recorded_as_a_digest_not_a_string() -> None:
    """A digest makes a CHANGED forced command detectable. A string invites a
    near-match being waved through: `/usr/bin/deploy` becoming
    `/usr/bin/deploy --shell` reads the same at a glance."""
    sweep = _sweep()
    with pytest.raises(sweep.InventoryError, match="DIGEST and not the command"):
        sweep.parse_ssh_constraint(
            _constraint(forced_command_digest="/usr/bin/dotmac-deploy"), "probe"
        )


def test_restrict_present_beside_a_permitted_capability_is_refused() -> None:
    """OpenSSH's `restrict` denies all current and future permissions, so this
    key cannot exist — the row was written rather than observed."""
    sweep = _sweep()
    with pytest.raises(sweep.InventoryError, match="cannot exist"):
        sweep.parse_ssh_constraint(_constraint(pty="permitted"), "probe")


def test_an_unstated_restriction_is_refused_not_assumed_safe() -> None:
    """Absence is never a disposition, applied to a key. An unrestricted key
    must SAY it is unrestricted; a blank field reads as 'nobody looked', which
    is the state the eight keys were already in."""
    sweep = _sweep()
    with pytest.raises(sweep.InventoryError, match="nobody looked"):
        sweep.parse_ssh_constraint(_constraint(source_restriction=None), "probe")


def test_a_permission_must_be_denied_or_permitted() -> None:
    sweep = _sweep()
    with pytest.raises(sweep.InventoryError, match="must be one of"):
        sweep.parse_ssh_constraint(_constraint(pty="maybe"), "probe")


def test_a_key_is_identified_by_fingerprint_never_by_material() -> None:
    sweep = _sweep()
    with pytest.raises(sweep.InventoryError, match="must be the SHA256 form"):
        sweep.parse_ssh_constraint(_constraint(fingerprint="the deploy key"), "probe")
    with pytest.raises(sweep.InventoryError, match="material never"):
        sweep.parse_ssh_constraint(
            _constraint(method="-----BEGIN OPENSSH PRIVATE KEY-----"), "probe"
        )


def test_an_ssh_credential_row_must_be_characterised() -> None:
    """The v2 gap, closed. A row that counts a key and says nothing about it is
    refused at parse."""
    sweep = _sweep()
    text = "\n".join(
        [
            'schema = "ExecutorInventory.v1"',
            'product = "probe"',
            f'revision = "{"0" * 40}"',
            'families_present = ["ssh_credential"]',
            "families_absent = "
            + json.dumps([n for n in sweep.FAMILY_NAMES if n != "ssh_credential"]),
        ]
        + [
            f'[[family_absence]]\nfamily = "{name}"\nscope = "repository_tree"\n'
            f'observed_at = "2026-08-31"\nobserved_by = "probe"\nmethod = "walked"'
            for name in sweep.FAMILY_NAMES
            if name != "ssh_credential" and not sweep.FAMILY_BY_NAME[name].tree_complete
        ]
        + [
            "[[entrypoint]]",
            'name = "ssh:k"',
            'family = "ssh_credential"',
            'trigger = "operator session"',
            'credential = "deploy-key"',
            'disposition = "active_executor"',
            'host = "erp.dotmac.io"',
        ]
    )
    with pytest.raises(sweep.InventoryError, match="declares no `ssh_constraint`"):
        sweep.parse_inventory(text, source="probe.toml")


def test_loosening_a_key_moves_the_census_digest() -> None:
    """Without the constraint in the canonical form, a key could be quietly
    unrestricted between two receipts and every recorded digest would match."""
    sweep = _sweep()

    def census(entry):
        return sweep.inventory_digest(
            sweep.Inventory(
                product="probe",
                revision="0" * 40,
                production_targets=(),
                families_present=("ssh_credential",),
                families_absent=tuple(
                    n for n in sweep.FAMILY_NAMES if n != "ssh_credential"
                ),
                absences=(),
                entrypoints=(entry,),
            )
        )

    tight = census(_key_entry(sweep))
    loose = census(_key_entry(sweep, source_restriction=sweep.SOURCE_UNRESTRICTED))
    assert tight != loose


def test_the_gate_only_bites_on_a_retained_rollback_key(tmp_path) -> None:
    """SPECIFICITY, and it is deliberate. ERP's eight unrestricted keys are
    `active_executor` debt the ratchet counts; refusing them at parse would
    make an honest census impossible on day one. The census records reality;
    the gate guards the retention this contract itself creates."""
    sweep = _sweep()
    live = _key_entry(sweep, source_restriction=sweep.SOURCE_UNRESTRICTED)
    live = sweep.Entrypoint(
        name=live.name,
        family=live.family,
        trigger=live.trigger,
        credential=live.credential,
        disposition="active_executor",
        host=live.host,
        targets=("erp.dotmac.io",),
        ssh_constraint=live.ssh_constraint,
    )
    assert sweep.rollback_key_failures(live), "the key itself is still unconstrained"
    inventory = sweep.Inventory(
        product="probe",
        revision="0" * 40,
        production_targets=(),
        families_present=("ssh_credential",),
        families_absent=tuple(n for n in sweep.FAMILY_NAMES if n != "ssh_credential"),
        absences=(),
        entrypoints=(live,),
    )
    problems = sweep.reconcile(inventory, tmp_path)
    assert not any("rollback" in problem for problem in problems), problems


def test_the_census_digest_is_ratcheted_not_merely_recorded() -> None:
    """A digest nothing compares is a digest that moves unobserved.

    Counts alone miss every change that alters a row's CONTENT without altering
    how many rows there are — a target repointed, a trigger changed, or an SSH
    key quietly loosened. `SshCredentialConstraintV1` sits in the canonical form
    precisely so that loosening moves this digest, which is worth nothing unless
    something fails when it moves.
    """
    sweep = _sweep()
    baseline = _baseline()
    measured, _unadopted, _unverified = sweep.measure(baseline, INVENTORY_DIR)

    drifted = json.loads(json.dumps(baseline))
    drifted["products"][sweep.SELF_REPOSITORY]["inventory_digest"] = (
        "sha256:" + "0" * 64
    )
    failures, _ = sweep.ratchet(measured, drifted)
    assert any("census digest moved" in failure for failure in failures), failures

    # and the recorded digest is the live one, so the gate is not vacuous
    failures, _ = sweep.ratchet(measured, baseline)
    assert not any("census digest" in failure for failure in failures), failures


# ── Compose sanction is ENTRY-POINT IDENTITY (2026-08-31, v3) ───────────────
#
# "Remove direct Compose mutation outside Foundation" is an identity test, not
# a disposition test. A compose verb is sanctioned iff it is reached through
# the installed `dotmac-deployment-foundation` entry point, resolved from
# distribution metadata — never from a path, a comment, a filename or a
# declared premise.
#
# The reason to prefer identity is a defect this module already produced: the
# verb detector read a usage comment as a deployment and drew a call edge
# backwards, because a path mention is symmetric while invocation is not. "Is
# this the sanctioned compose call?" has the same shape — it asks about intent,
# which a tree cannot answer.


def test_vendor_cp_is_on_the_roster() -> None:
    """A named production host retaining a rollback credential sat outside the
    roster, so it would have been SILENTLY UNMONITORED rather than reported
    UNADOPTED — the roster reproducing the failure the code prevents.

    The entry is the REPOSITORY that owes the inventory, not the host that
    retains the credential: `measure()` resolves `<product>.toml`, so a host
    identity here would name a file nobody can ever write.
    """
    sweep = _sweep()
    assert "dotmac_vendor_control_plane" in sweep.ADOPTION_TARGETS
    assert "dotmac_vendor_control_plane" in _baseline()["unadopted"]
    assert not any("-prod" in target for target in sweep.ADOPTION_TARGETS)


def test_the_sanctioned_entry_point_is_resolved_from_installed_metadata() -> None:
    """The positive half. In CI the distribution is installed (it is a dev
    dependency and `poetry install` takes the dev group), so this resolves to a
    real console script."""
    sweep = _sweep()
    resolved = sweep.sanctioned_entry_points()
    assert resolved, (
        f"`{sweep.SANCTIONED_DISTRIBUTION}` must be installed where this check "
        "runs; without it the sanction question is UNMONITORED"
    )
    assert all(isinstance(name, str) and name for name in resolved)


def test_an_unresolvable_distribution_is_unmonitored_never_a_pass() -> None:
    """The other half. A check that cannot establish its premise says so."""
    assert _sweep().sanctioned_entry_points("dotmac-no-such-distribution-xyz") is None


def test_the_console_script_name_is_never_written_down_here() -> None:
    """THE test that keeps this an identity check.

    A hardcoded console-script name would turn metadata resolution back into a
    string match — the same failure as judging a call by its filename. The
    module may name the DISTRIBUTION (that is the identity it resolves against)
    and must not name the script the distribution happens to install.
    """
    sweep = _sweep()
    resolved = sweep.sanctioned_entry_points()
    assert resolved, "cannot prove absence of a name we could not resolve"
    source = SCRIPT.read_text("utf-8")
    assert sweep.SANCTIONED_DISTRIBUTION in source, (
        "the module must name the DISTRIBUTION — that is the identity it "
        "resolves against"
    )
    # The distribution name is removed FIRST, because the console script is a
    # PREFIX of it (`dotmac-deploy` inside `dotmac-deployment-foundation`) and a
    # naive substring check would fail on the very line that makes this an
    # identity check. Which is the near-match hazard this contract warns about,
    # arriving inside its own test.
    residue = source.replace(sweep.SANCTIONED_DISTRIBUTION, "")
    for name in resolved:
        assert name not in residue, (
            f"the console script {name!r} is written into the module; sanction "
            "must be resolved from metadata, not matched as a literal"
        )


def test_delegation_to_a_name_the_distribution_does_not_provide_is_refused(
    tmp_path,
) -> None:
    sweep = _sweep()
    if sweep.sanctioned_entry_points() is None:
        pytest.skip("the sanctioned distribution is not installed here")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "d.sh").write_text(
        "dotmac-deploy apply\n", encoding="utf-8"
    )
    entry = sweep.Entrypoint(
        name="d",
        family="script",
        trigger="manual",
        credential="none",
        disposition="active_executor",
        path="scripts/d.sh",
        targets=("host",),
        delegates_to="deploy-everything",
    )
    problems = sweep.reconcile(_probe_inventory(sweep, "script", entry), tmp_path)
    assert any("ENTRY-POINT IDENTITY" in problem for problem in problems), problems


def test_delegation_to_a_real_console_script_is_admitted(tmp_path) -> None:
    """Both halves at the same reach."""
    sweep = _sweep()
    resolved = sweep.sanctioned_entry_points()
    if resolved is None:
        pytest.skip("the sanctioned distribution is not installed here")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "d.sh").write_text("run it\n", encoding="utf-8")
    entry = sweep.Entrypoint(
        name="d",
        family="script",
        trigger="manual",
        credential="none",
        disposition="active_executor",
        path="scripts/d.sh",
        targets=("host",),
        delegates_to=sorted(resolved)[0],
    )
    problems = sweep.reconcile(_probe_inventory(sweep, "script", entry), tmp_path)
    assert not any("ENTRY-POINT IDENTITY" in problem for problem in problems), problems
    assert not any("UNMONITORED" in problem for problem in problems), problems


def test_an_in_tree_compose_verb_is_an_unsanctioned_mutation(tmp_path) -> None:
    """A sanctioned mutation happens inside the installed distribution, which
    is not in the tree, so it never appears in a resolved verb set. An
    unsanctioned one is in the tree, so it always does."""
    sweep = _sweep()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "d.sh").write_text(
        "docker compose up -d\n", encoding="utf-8"
    )
    entry = sweep.Entrypoint(
        name="d",
        family="script",
        trigger="manual",
        credential="none",
        disposition="active_executor",
        path="scripts/d.sh",
        targets=("host",),
    )
    found = sweep.compose_mutations(_probe_inventory(sweep, "script", entry), tmp_path)
    assert found.get("d"), found


def test_delegating_does_not_excuse_a_direct_call_beside_it(tmp_path) -> None:
    """SPECIFICITY in the dangerous direction. A script that shells out to the
    sanctioned entry point AND runs its own `docker compose` is still mutating
    a topology outside Foundation."""
    sweep = _sweep()
    resolved = sweep.sanctioned_entry_points()
    if resolved is None:
        pytest.skip("the sanctioned distribution is not installed here")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "d.sh").write_text(
        f"{sorted(resolved)[0]} apply\ndocker compose up -d\n", encoding="utf-8"
    )
    entry = sweep.Entrypoint(
        name="d",
        family="script",
        trigger="manual",
        credential="none",
        disposition="active_executor",
        path="scripts/d.sh",
        targets=("host",),
        delegates_to=sorted(resolved)[0],
    )
    found = sweep.compose_mutations(_probe_inventory(sweep, "script", entry), tmp_path)
    assert "d" in found, "delegation must not launder the direct call"


def test_a_clean_tree_records_no_unsanctioned_mutation(tmp_path) -> None:
    """The negative control, so the measurement is not "everything is a
    mutation"."""
    sweep = _sweep()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "d.sh").write_text("echo hello\n", encoding="utf-8")
    entry = sweep.Entrypoint(
        name="d",
        family="script",
        trigger="manual",
        credential="none",
        disposition="not_an_executor",
        path="scripts/d.sh",
        premise="commands no deployment verb",
    )
    assert (
        sweep.compose_mutations(_probe_inventory(sweep, "script", entry), tmp_path)
        == {}
    )


def test_the_unsanctioned_set_is_ratcheted_in_both_directions() -> None:
    """Wave 7C drives this set to EMPTY. The SET rather than the count: a swap
    — one path retired while another gains the ability — leaves the count still
    and is exactly the move that matters."""
    sweep = _sweep()
    baseline = _baseline()
    measured, _u, _v = sweep.measure(baseline, INVENTORY_DIR)
    row = baseline["products"][sweep.SELF_REPOSITORY]
    assert row["unsanctioned_compose_mutation_paths"], "must not pass over an empty set"

    grown = json.loads(json.dumps(baseline))
    grown["products"][sweep.SELF_REPOSITORY]["unsanctioned_compose_mutation_paths"] = []
    failures, _ = sweep.ratchet(measured, grown)
    assert any("gained the ability" in failure for failure in failures), failures

    shrunk = json.loads(json.dumps(baseline))
    shrunk["products"][sweep.SELF_REPOSITORY]["unsanctioned_compose_mutation_paths"] = (
        row["unsanctioned_compose_mutation_paths"] + ["script:_retired.sh"]
    )
    failures, _ = sweep.ratchet(measured, shrunk)
    assert any("drives this set to EMPTY" in failure for failure in failures), failures


def test_the_environment_dependent_sanction_state_is_not_frozen_in_the_baseline() -> (
    None
):
    """Whether the distribution is installed is a property of WHERE the check
    runs, not of the product. Freezing one machine's answer into a committed
    file is how a local venv becomes a fleet fact."""
    row = _baseline()["products"][_sweep().SELF_REPOSITORY]
    assert "compose_sanction_state" not in row
