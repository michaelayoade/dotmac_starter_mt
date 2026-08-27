"""The engine parse must run in REQUIRED pull-request CI, not only where it is
convenient to have written it.

The deployment renderer emits TEXT. Whether an engine will load that text is a
question only an engine can answer, and `make check` — which must stay
Docker-free and offline so it runs on a laptop and in an air-gapped checkout —
can never ask it.

The gap this file exists to close is a real one that survived review for a
whole PR: `docker compose config` was added to `deployment-conformance.yml`,
which is `workflow_call`-only, and its single caller `deployment-adopter.yml`
is `workflow_dispatch`-only. So the check existed, read as coverage, and never
executed on a pull request — including the pull request that added it.

Two placements, two audiences, both required:

* `ci.yml`'s `docker-build` job — what THIS repository runs on every PR. It is
  the only required job with a Docker daemon.
* `deployment-conformance.yml` — what every CONSUMING product runs.

`AGENTS.md` rule 25 / ADR-0018: a guard whose premise is not enforceable is an
unmonitored region, not a guard.
"""

from __future__ import annotations

import pathlib
import re

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
CI = REPO / ".github" / "workflows" / "ci.yml"
CONFORMANCE = REPO / ".github" / "workflows" / "deployment-conformance.yml"
MAKEFILE = REPO / "Makefile"

ENGINE_PARSE = re.compile(r"docker\s+compose\s+(?:-f\s+\S+\s+)?config")


def _run_script(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) for step in job.get("steps", []))


def _job(path: pathlib.Path, name: str) -> dict:
    return yaml.safe_load(path.read_text())["jobs"][name]


def test_required_pr_ci_parses_the_rendered_compose_file() -> None:
    job = _job(CI, "docker-build")
    assert ENGINE_PARSE.search(_run_script(job)), (
        "ci.yml's docker-build job must run `docker compose config` on the "
        "rendered compose file. It is the only REQUIRED job with a Docker "
        "daemon, so without it no pull request ever asks an engine whether the "
        "rendered document loads"
    )


def test_the_parse_runs_before_the_image_is_built() -> None:
    """A gate after the build still pays for the build.

    The rendered file is checked in; nothing about it depends on the image, so
    there is no reason to spend a build discovering it is unloadable.
    """
    runs = [str(step.get("run", "")) for step in _job(CI, "docker-build")["steps"]]
    parse = next(i for i, r in enumerate(runs) if ENGINE_PARSE.search(r))
    build = next(i for i, r in enumerate(runs) if "docker build" in r)
    assert parse < build, "parse the rendered project before building an image"


def test_the_reusable_workflow_keeps_its_own_parse_for_consumers() -> None:
    """Products do not run this repository's `ci.yml`.

    Removing the conformance copy once `ci.yml` has one would silently drop the
    check for ERP, Integrator, Sub and every future adopter.
    """
    job = _job(CONFORMANCE, "descriptor")
    assert ENGINE_PARSE.search(_run_script(job)), (
        "deployment-conformance.yml must keep its own engine parse — it is the "
        "only one a consuming product runs"
    )


def test_make_check_stays_docker_free_and_offline() -> None:
    """`make check` is the laptop and air-gapped contract.

    The engine parse belongs in CI precisely BECAUSE it needs a daemon. Moving
    it into `check` would make the canonical local gate unrunnable without
    Docker, which is a worse trade than the coverage is worth.
    """
    text = MAKEFILE.read_text()
    body = re.search(r"^check:.*?(?=^\S)", text, re.S | re.M)
    assert body, "the Makefile must define a `check` target"
    assert (
        "docker" not in body.group(0).lower()
    ), "`make check` must not require Docker; the engine parse lives in CI"


def test_the_parse_supplies_placeholders_rather_than_failing_on_interpolation() -> None:
    """Interpolation fails closed on an unset variable.

    Without placeholders the step would abort on the first `${VAR}` and report
    an interpolation error, which reads like a structural failure and hides
    whether the document is actually valid — a green-looking check that never
    reached its own subject.
    """
    for path, job in ((CI, "docker-build"), (CONFORMANCE, "descriptor")):
        script = _run_script(_job(path, job))
        parse_step = next(
            step
            for step in _job(path, job)["steps"]
            if ENGINE_PARSE.search(str(step.get("run", "")))
        )
        run = str(parse_step["run"])
        assert "placeholder" in run, (
            f"{path.name}:{job} must interpolate runtime variables with "
            "placeholders before parsing"
        )
        assert (
            "env " in run
        ), f"{path.name}:{job} must pass the placeholders to the parse"
        assert script  # the script was actually read


def test_the_parse_does_not_start_anything() -> None:
    """`config` validates without pulling, running or networking.

    A check that started containers would be a deployment, not a parse, and
    would belong in the rehearsal rather than in every pull request.
    """
    for path, job in ((CI, "docker-build"), (CONFORMANCE, "descriptor")):
        parse_step = next(
            step
            for step in _job(path, job)["steps"]
            if ENGINE_PARSE.search(str(step.get("run", "")))
        )
        run = str(parse_step["run"])
        for forbidden in ("compose up", "compose run", "compose start", "compose pull"):
            assert (
                forbidden not in run
            ), f"{path.name}:{job} must only `config`, never `{forbidden}`"
