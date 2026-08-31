"""A guard's SUBJECT, EXTENT and LIFETIME must each be correct.

ADR-0018 amendment, 2026-08-31. One rule with three shapes, not three rules:

> A guard makes a claim about a **subject**, over an **extent**, for a
> **duration** — and each of the three can be wrong independently, while the
> other two look fine.

This file enforces the two shapes that are mechanizable in this repository and
carries a sensitivity proof for each. The third is enforced elsewhere and is
cited rather than re-implemented.

## Shape 1 — wrong subject: a guard that checks a NAME, not the property

`"PYTHONPATH" not in dockerfile` fails on the comment explaining its absence. A
ledger test catches its own docstring. A verb detector reads a *usage comment*
as a deployment and draws a call edge backwards, because a path mention is
symmetric while invocation is not.

Enforcement SHIPPED, in `scripts/executor_retirement.py`: `executable_text()`
strips whole-line comments before both verb detection and edge resolution, and
`sanctioned_entry_points()` resolves an identity from installed metadata rather
than matching a name. The proof below asserts the RULE against that helper — a
guard reading prose is reading the wrong subject.

## Shape 2 — wrong extent: a hand-maintained list, or authored files only

Platform CP's profile guard enumerated five stateful modules by hand while the
assembly composed six, so one module was covered by nothing.

And the sharper one, from `dotmac_erp` PR #426 (merged `4ab8761d`):
`test_boot_time_installer_is_retired_from_all_compose_roles` asserted a deleted
entrypoint's absence **scoped to the root compose only**. The rendered sibling
`deploy/rendered/docker-compose.yml` still named `/app/entrypoint-monitoring.sh`
in every role — a file absent from the image's COPY allowlist. **The rendered
project could not have started, and the guard for exactly that defect was
looking one file away.**

That deserves its own name: **a repository that renders deployment artifacts has
TWO populations, and a guard written against the authored one is silent over the
deployed one — which is the population that matters.**

## Shape 3 — no expiry: a relaxation that never names when it ends

ERP carried `require-real-digests: false` while its descriptor already held a
real digest. Nothing recorded when it should be armed, so it stayed off.

This connects to ADR-0018's existing text rather than extending it sideways: an
exemption must carry an **enforceable premise**, and a premise with no expiry is
not enforceable — it is permanent by default while reading as temporary.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"
BASELINE = PROJECT_ROOT / "docs" / "inventories" / "guard-lifetime-baseline.json"

# ── Shape 2: the population is DISCOVERED, never listed ─────────────────────


def compose_population(root: pathlib.Path) -> set[str]:
    """Every Compose file in the tree, authored and RENDERED alike.

    Discovered by glob and never by a literal list, because the guard against
    hand-maintained extents must not itself keep one. `deploy/rendered/` is not
    special-cased — it is simply not excluded, which is the entire fix.
    """
    found: set[str] = set()
    for pattern in ("docker-compose*.yml", "docker-compose*.yaml", "compose*.yml"):
        for path in root.rglob(pattern):
            if any(
                part in {".git", "node_modules", ".venv", "__pycache__"}
                for part in path.parts
            ):
                continue
            found.add(path.relative_to(root).as_posix())
    return found


def test_the_population_includes_both_authored_and_rendered_compose() -> None:
    """The ERP defect, generalised. A guard over "the compose file" is silent
    over the one that actually gets deployed."""
    population = compose_population(PROJECT_ROOT)
    assert len(population) >= 4, population
    authored = {p for p in population if "/" not in p}
    rendered = {p for p in population if "rendered/" in p}
    assert authored, "no authored compose found; the walk is broken"
    assert rendered, (
        "no RENDERED compose found. A repository that renders deployment "
        "artifacts has two populations, and this guard must see both"
    )


def test_a_planted_rendered_artifact_is_reached(tmp_path) -> None:
    """Sensitivity. The tree is otherwise clean, so a walk that silently
    stopped descending into `deploy/rendered/` would pass every assertion
    above."""
    (tmp_path / "deploy" / "rendered").mkdir(parents=True)
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "deploy" / "rendered" / "docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    found = compose_population(tmp_path)
    assert "deploy/rendered/docker-compose.yml" in found, found
    assert "docker-compose.yml" in found, found


def test_the_population_is_not_a_literal_list() -> None:
    """A guard whose extent is a hand-maintained list drifts the moment the
    tree grows — which is how five enumerated modules covered six composed
    ones."""
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    body = source.split("def compose_population", 1)[1].split("\ndef ", 1)[0]
    assert "rglob" in body
    assert "deploy/rendered/docker-compose.yml" not in body


# ── Shape 2, applied: a subset-scoped guard's premise must be checked ───────

TRUST_AUTH = "POSTGRES_HOST_AUTH_METHOD"


def trust_auth_files(root: pathlib.Path) -> set[str]:
    return {
        relative
        for relative in compose_population(root)
        if TRUST_AUTH in (root / relative).read_text(encoding="utf-8")
    }


def test_the_loopback_guards_scoping_premise_is_enforced() -> None:
    """`test_test_database_is_loopback_bound.py` covers ONE compose file, and
    its stated justification is that this file runs trust auth. That premise
    was true and unchecked — so the day a second compose file gained
    `POSTGRES_HOST_AUTH_METHOD`, the loopback guard would have been silent over
    an unauthenticated Postgres.

    An exemption states an enforceable premise (ADR-0018 §2). This is that
    premise, made enforceable.
    """
    covered = "docker-compose.test.yml"
    live = trust_auth_files(PROJECT_ROOT)
    assert live == {covered}, (
        f"trust auth appears in {sorted(live)}, but the loopback guard covers "
        f"only {covered!r}. Either widen that guard's extent or remove the "
        "trust-auth setting from the other file(s)"
    )


def test_the_scoping_premise_check_fires_on_a_second_trust_auth_file(tmp_path) -> None:
    """Sensitivity, and NOT over an empty set: the conforming case has exactly
    one member, so a check that always returned a singleton would pass it."""
    (tmp_path / "docker-compose.test.yml").write_text(
        f"    {TRUST_AUTH}: trust\n", encoding="utf-8"
    )
    assert trust_auth_files(tmp_path) == {"docker-compose.test.yml"}

    (tmp_path / "docker-compose.staging.yml").write_text(
        f"    {TRUST_AUTH}: trust\n", encoding="utf-8"
    )
    assert trust_auth_files(tmp_path) == {
        "docker-compose.test.yml",
        "docker-compose.staging.yml",
    }


# ── Shape 3: a relaxation names where it is re-armed, and is armed there ────

#: `default: false` on a workflow input is a RELAXATION. Its description is the
#: premise; if that premise names another lane as the place the input is turned
#: on, the claim is checkable — and ERP's defect is exactly the case where
#: nobody ever turned it on.
_INPUT_RE = re.compile(
    r"^      (?P<name>[a-z][a-z0-9-]*):\n" r"(?P<body>(?:^ {8}.*\n|^\s*\n)*)",
    re.MULTILINE,
)

#: Words that make a description NAME a place the relaxation is armed. Narrow
#: on purpose: a generic "every `false` needs a comment" guard fires on
#: `required: false` and `cancel-in-progress: false`, which are not relaxations
#: at all — and a guard that fires on ordinary declarations is one reviewers
#: learn to override. That is shape 1, arriving inside shape 3's guard.
_ARMING_PHRASES = ("turns it on", "turn it on", "re-enabl", "re-arm", "enables it")


def relaxations(root: pathlib.Path) -> dict[str, dict[str, str]]:
    """Workflow inputs defaulting to false, with their stated premise."""
    found: dict[str, dict[str, str]] = {}
    for path in sorted((root / ".github" / "workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for match in _INPUT_RE.finditer(text):
            body = match.group("body")
            if not re.search(r"^ {8}default:\s*false\s*$", body, re.MULTILINE):
                continue
            found[f"{path.name}:{match.group('name')}"] = {
                "workflow": path.name,
                "input": match.group("name"),
                "premise": " ".join(body.split()),
            }
    return found


def unexpired_relaxations(root: pathlib.Path) -> dict[str, str]:
    """Relaxations whose premise names an arming place that never arms them."""
    findings: dict[str, str] = {}
    for key, row in relaxations(root).items():
        premise = row["premise"]
        if not any(phrase in premise for phrase in _ARMING_PHRASES):
            continue
        armed = False
        for path in sorted((root / ".github" / "workflows").glob("*.yml")):
            if path.name == row["workflow"]:
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(rf"^\s*{re.escape(row['input'])}:\s*true\s*$", text, re.M):
                armed = True
                break
        if not armed:
            findings[key] = (
                f"{row['input']} defaults to false and its premise names a lane "
                "that turns it on; no workflow in this repository passes it true"
            )
    return findings


def _baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_the_lifetime_ratchet_is_two_directional() -> None:
    """ADR-0018 §3: retire a backlog with a two-directional ratchet, never a
    blanket allow. A guard introduced over a region that already has debt
    RECORDS the debt exactly, and this file is that rule applied to itself."""
    recorded = set(_baseline()["unexpired_relaxations"])
    live = set(unexpired_relaxations(PROJECT_ROOT))
    gained = sorted(live - recorded)
    lost = sorted(recorded - live)
    assert not gained, (
        f"{gained} is a relaxation whose premise names where it is armed, and "
        "nothing arms it. A premise with no expiry is not enforceable — it is "
        "permanent by default while reading as temporary"
    )
    assert not lost, (
        f"{lost} no longer reads as unexpired and the baseline still lists it. "
        "Lower the baseline in the SAME change that arms it"
    )


def test_the_lifetime_detector_is_not_vacuous() -> None:
    """The repository has a live instance, so a detector that had silently
    stopped parsing workflows would fail here rather than pass green."""
    assert relaxations(PROJECT_ROOT), "no workflow relaxation was parsed at all"
    assert _baseline()["unexpired_relaxations"], (
        "the baseline records no debt; if the tree is genuinely clean this "
        "assertion must be replaced with a planted-only proof"
    )


def test_the_lifetime_detector_stays_silent_on_a_conforming_relaxation(
    tmp_path,
) -> None:
    """Both halves. A relaxation whose premise names a lane THAT ARMS IT is
    conforming and must not be reported."""
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "gate.yml").write_text(
        "on:\n  workflow_call:\n    inputs:\n"
        "      strict-mode:\n"
        "        description: >-\n"
        "          Off by default; the release lane turns it on.\n"
        "        required: false\n"
        "        type: boolean\n"
        "        default: false\n",
        encoding="utf-8",
    )
    assert unexpired_relaxations(tmp_path), "the unarmed case must be reported"

    (workflows / "release.yml").write_text(
        "jobs:\n  go:\n    uses: ./.github/workflows/gate.yml\n"
        "    with:\n      strict-mode: true\n",
        encoding="utf-8",
    )
    assert (
        unexpired_relaxations(tmp_path) == {}
    ), "a relaxation that IS armed where its premise says must not be reported"


def test_the_lifetime_detector_ignores_declarations_that_are_not_relaxations(
    tmp_path,
) -> None:
    """SPECIFICITY, and it is shape 1 arriving inside shape 3's guard: a
    generic "every false needs a comment" check fires on `required: false` and
    `cancel-in-progress: false`, which are not relaxations at all."""
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ordinary.yml").write_text(
        "concurrency:\n  cancel-in-progress: false\n"
        "on:\n  workflow_call:\n    inputs:\n"
        "      tag:\n"
        "        description: the tag\n"
        "        required: false\n"
        "        type: string\n",
        encoding="utf-8",
    )
    assert unexpired_relaxations(tmp_path) == {}
    assert relaxations(tmp_path) == {}


# ── Shape 1: the subject, enforced where it ships ───────────────────────────


def _executor_module():
    name = "executor_retirement"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, PROJECT_ROOT / "scripts" / "executor_retirement.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


def test_a_guard_reading_prose_is_reading_the_wrong_subject() -> None:
    """Shape 1, asserted as the RULE against the helper that ships it.

    Not a duplicate of the executor suite's own tests: those assert the
    helper's behaviour, this asserts that the repository HAS an answer to
    "a comment is not a call" and that the answer works in both directions.
    """
    sweep = _executor_module()
    assert (
        sweep.deployment_verbs("#   docker compose -f a.yml up\n") == {}
    ), "a usage comment must not read as a deployment"
    assert sweep.deployment_verbs("docker compose up -d  # start it\n"), (
        "an inline trailing comment must keep its command; over-stripping "
        "produces false negatives, which is the direction that HIDES a defect"
    )


def test_the_subject_rule_has_a_named_home() -> None:
    """So the rule is findable from here rather than folklore."""
    sweep = _executor_module()
    assert callable(sweep.executable_text)
    assert "CALLS, NOT MENTIONS" in sweep.executable_text.__doc__
