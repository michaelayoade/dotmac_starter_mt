"""`JobSpec` + `ModuleManifest.jobs` + registry validation (surface contract 1.2)."""

from __future__ import annotations

import pytest
from dotmac_kernel import JobSpec, JobSpecError, ModuleManifest, ModuleRegistry
from dotmac_kernel.modules import ModuleRegistryError
from fastapi import APIRouter

_HERE = __name__


def good_handler(session, scope, run_context):
    return None


def two_arg_handler(session, scope):
    return None


NOT_CALLABLE = 3


def _job(**overrides: object) -> JobSpec:
    base: dict[str, object] = {
        "code": "sweep",
        "handler": f"{_HERE}:good_handler",
        "plane": "tenant",
        "interval_seconds": 60,
    }
    base.update(overrides)
    return JobSpec(**base)  # type: ignore[arg-type]


def test_valid_interval_and_cron_jobs() -> None:
    assert _job().plane.value == "tenant"
    cron = _job(interval_seconds=None, cron="*/15 2-4 * 1,6 1-5", plane="platform")
    assert cron.cron and cron.plane.value == "platform"
    dotted = _job(handler=f"{_HERE}.good_handler")
    assert dotted.handler.endswith("good_handler")


@pytest.mark.parametrize(
    "overrides",
    [
        {"code": "Bad Code"},
        {"handler": "no spaces allowed"},
        {"plane": "global"},
        {"interval_seconds": None},  # neither cadence
        {"cron": "* * * * *"},  # both cadences
        {"interval_seconds": 1},
        {"interval_seconds": True},
        {"interval_seconds": None, "cron": "* * * *"},
        {"interval_seconds": None, "cron": "61 * * * *"},
        {"interval_seconds": None, "cron": "5-1 * * * *"},
        {"interval_seconds": None, "cron": "*/0 * * * *"},
        {"max_attempts": 0},
        {"backoff_seconds": ()},
        {"backoff_seconds": (-1,)},
        {"concurrency": "many"},
    ],
)
def test_malformed_spec_is_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(JobSpecError):
        _job(**overrides)


def test_plane_is_mandatory() -> None:
    with pytest.raises(TypeError):
        JobSpec(code="x", handler=f"{_HERE}:good_handler", interval_seconds=10)  # type: ignore[call-arg]


def test_manifest_defaults_to_no_jobs_and_contract_one_still_loads() -> None:
    legacy = ModuleManifest(code="legacy", version="1", web_routers=[APIRouter()])
    assert legacy.contract_version == 1 and legacy.jobs == ()
    ModuleRegistry([legacy])


def test_contract_one_cannot_declare_jobs() -> None:
    with pytest.raises(ModuleRegistryError, match="requires contract 2"):
        ModuleManifest(code="legacy", version="1", contract_version=1, jobs=[_job()])


def test_duplicate_job_code_within_module_refused() -> None:
    with pytest.raises(ModuleRegistryError, match="twice"):
        ModuleManifest(code="m", version="1", jobs=[_job(), _job()])


def test_same_code_in_two_modules_is_allowed_and_listed_in_order() -> None:
    reg = ModuleRegistry(
        [
            ModuleManifest(code="a", version="1", jobs=[_job()]),
            ModuleManifest(code="b", version="1", jobs=[_job(), _job(code="other")]),
        ]
    )
    assert [(m, j.code) for m, j in reg.jobs()] == [
        ("a", "sweep"),
        ("b", "sweep"),
        ("b", "other"),
    ]
    assert [(m, j.code) for m, j in reg.jobs(disabled={"a"})] == [
        ("b", "sweep"),
        ("b", "other"),
    ]


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (f"{_HERE}:missing", "does not resolve"),
        ("no_such_pkg_xyz.mod:fn", "does not resolve"),
        (f"{_HERE}:NOT_CALLABLE", "not callable"),
        (f"{_HERE}:two_arg_handler", "must accept"),
    ],
)
def test_unresolvable_handler_fails_registry_construction(
    handler: str, message: str
) -> None:
    manifest = ModuleManifest(code="m", version="1", jobs=[_job(handler=handler)])
    with pytest.raises(ModuleRegistryError, match=message):
        ModuleRegistry([manifest])
