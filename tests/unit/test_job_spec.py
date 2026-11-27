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


async def async_handler(session, scope, run_context):
    return None


class ClassHandler:
    def __init__(self, session, scope, run_context): ...


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
        (f"{_HERE}:NOT_CALLABLE", "plain function"),
        (f"{_HERE}:two_arg_handler", "must accept"),
        (f"{_HERE}:async_handler", "coroutine"),
        (f"{_HERE}:ClassHandler", "plain function"),
        ("subprocess:call", "lives in 'subprocess'"),
        ("dotmac_kernel.jobs:resolve_job_handler", "lives in 'dotmac_kernel'"),
        ("os.path:join", "lives in 'os'"),
    ],
)
def test_unresolvable_handler_fails_registry_construction(
    handler: str, message: str
) -> None:
    manifest = ModuleManifest(code="m", version="1", jobs=[_job(handler=handler)])
    with pytest.raises(ModuleRegistryError, match=message):
        ModuleRegistry([manifest])


def test_platform_job_requires_a_declared_platform_table() -> None:
    with pytest.raises(ModuleRegistryError, match="no platform_tables"):
        ModuleManifest(
            code="m",
            version="1",
            short_code="mm",
            migration_prefix="mm",
            tables=("t",),
            jobs=[_job(plane="platform")],
        )


def test_tenant_job_refused_when_module_owns_only_platform_tables() -> None:
    with pytest.raises(ModuleRegistryError, match="only platform tables"):
        ModuleManifest(
            code="m",
            version="1",
            short_code="mm",
            migration_prefix="mm",
            platform_tables=("t",),
            jobs=[_job(plane="tenant")],
        )


def test_jobs_are_filtered_by_the_assemblys_installed_planes() -> None:
    from dotmac_kernel import ModulePlane, ModulePlaneSelection

    both = ModuleManifest(
        code="files",
        version="1",
        short_code="files",
        migration_prefix="fi",
        tables=("a",),
        platform_tables=("b",),
        supported_plane_sets=[("tenant",), ("tenant", "platform")],
        jobs=[_job(code="t"), _job(code="p", plane="platform")],
    )
    reg = ModuleRegistry([both])
    tenant_only = [ModulePlaneSelection(module="files", planes=(ModulePlane.TENANT,))]
    assert [j.code for _, j in reg.jobs(module_planes=tenant_only)] == ["t"]
    assert [j.code for _, j in reg.jobs()] == ["t", "p"]
