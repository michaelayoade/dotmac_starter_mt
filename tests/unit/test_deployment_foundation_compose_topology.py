"""The v3 topology extension renders CP-shaped assets without product branches."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from dotmac_deployment_foundation.document import build_canonical_document
from dotmac_deployment_foundation.engine.plan import Phase, StepKind, build_plan
from dotmac_deployment_foundation.engine.run import CommandResult
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.providers.compose_host import (
    ComposeHostEffects,
    RetainedComposeAsset,
)
from dotmac_deployment_foundation.render.compose import render_compose
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

_IMAGE = "registry.example.invalid/control@sha256:" + "a" * 64
_POSTGRES = "registry.example.invalid/postgres@sha256:" + "b" * 64
_INIT_ROLES_DIGEST = "sha256:" + hashlib.sha256(b"#!/bin/sh\nroles\n").hexdigest()
_BOOTSTRAP_CREDENTIAL_DIGEST = (
    "sha256:" + hashlib.sha256(b"-- bootstrap credential function\n").hexdigest()
)
_RELAY_PING_SCRIPT = (
    "import json,subprocess,sys; "
    "p=subprocess.run(['dotmac-platform','--format','json','relay','health'],"
    "capture_output=True,text=True); "
    "sys.exit(0 if p.returncode == 0 and "
    "json.loads(p.stdout)['data']['verdict'] == 'relay_draining' else 1)"
)

DESCRIPTOR = f"""
schema = "ProductDeploymentSpec.v3"
product = "control"
environment = "fixture"

[assembly]
manifest_path = "deploy/product-manifest.json"
manifest_digest = "sha256:{"c" * 64}"

[image]
reference = "{_IMAGE}"
source_revision = "{"d" * 40}"

[runtime_materials]
names = [
  "DATABASE_URL", "PLATFORM_DATABASE_URL", "VENDOR_RELAY_DISPATCHER_DATABASE_URL",
  "MIGRATION_DATABASE_URL",
  "SIGNING_KEY_SOURCE", "SIGNING_KEY_TARGET"
]

[[roles]]
code = "app"
command = ["python", "-m", "app"]
materials = ["DATABASE_URL", "PLATFORM_DATABASE_URL"]
[roles.resources]
cpus = "1.0"
memory = "512m"
[roles.health.live]
path = "/health"
port = 8000

[[roles]]
code = "relay"
command = ["dotmac-platform", "relay", "run", "--worker-id", "fixture-relay-1"]
materials = [
  "DATABASE_URL", "PLATFORM_DATABASE_URL",
  "VENDOR_RELAY_DISPATCHER_DATABASE_URL",
]
[roles.resources]
cpus = "0.5"
memory = "256m"
[roles.worker]
kind = "custom"
ping_command = [
  "python", "-c",
  {json.dumps(_RELAY_PING_SCRIPT)},
]
heartbeat_max_age_seconds = 120
max_backlog = 1000

[migration]
command = ["python", "-m", "migrate"]
heads_command = ["python", "-m", "migrate", "current"]
owner_material = "MIGRATION_DATABASE_URL"
expected_heads = ["head_1"]
compatibility = "maintenance_required"

[[external_dependencies]]
code = "db"
kind = "postgres"
required_for = ["ready", "migrate", "backup"]
material = "DATABASE_URL"
image = "{_POSTGRES}"
health_probe = ["pg_isready"]
[[external_dependencies.volumes]]
name = "pgdata"
target = "/var/lib/postgresql/data"

[compose_topology]
roles_depend_on_migrate = false

[[compose_topology.networks]]
code = "front"
loopback_default = true

[[compose_topology.networks]]
code = "back"
internal = true

[[compose_topology.placements]]
code = "db"
networks = ["back"]
[[compose_topology.placements.mounts]]
kind = "repository"
source = "deploy/postgres/init-roles.sh"
source_digest = "{_INIT_ROLES_DIGEST}"
target = "/docker-entrypoint-initdb.d/001-vendor-roles.sh"
justification = "Install the reviewed first-cluster database roles script."
approved_by = "deployment-owner"
[[compose_topology.placements.mounts]]
kind = "repository"
source = "deploy/postgres/bootstrap-credential-function.sql"
source_digest = "{_BOOTSTRAP_CREDENTIAL_DIGEST}"
target = "/docker-entrypoint-initdb.d/002-bootstrap-credential-function.sql"
justification = "Install the reviewed first-cluster credential function script."
approved_by = "deployment-owner"

[[compose_topology.placements]]
code = "migrate"
networks = ["back"]
profiles = ["ops"]

[[compose_topology.placements]]
code = "app"
networks = ["front", "back"]
[[compose_topology.placements.mounts]]
kind = "named"
source = "manifests"
target = "/run/dotmac/product-manifests"
read_only = true
[[compose_topology.placements.mounts]]
kind = "host_material"
source_material = "SIGNING_KEY_SOURCE"
target_material = "SIGNING_KEY_TARGET"
justification = "Mount the held signing material only into this process."
approved_by = "security-owner"

[[compose_topology.placements]]
code = "relay"
networks = ["back"]
[[compose_topology.placements.mounts]]
kind = "named"
source = "manifests"
target = "/run/dotmac/product-manifests"
read_only = true
[[compose_topology.placements.mounts]]
kind = "host_material"
source_material = "SIGNING_KEY_SOURCE"
target_material = "SIGNING_KEY_TARGET"
justification = "Mount the held signing material only into this process."
approved_by = "security-owner"

[[compose_topology.jobs]]
code = "manifest-init"
image_source = "dependency:db"
command = ["/bin/sh", "-ec", "chown 10001:10001 /manifests && chmod 0750 /manifests"]
verify_command = ["stat", "-c", "%u:%g:%a", "/manifests"]
verify_stdout = "10001:10001:750"
run_during_deploy = true
profiles = ["ops"]
network_mode = "none"
root_justification = "One-shot volume ownership requires root at initialization."
root_approved_by = "deployment-owner"
[compose_topology.jobs.security]
user = "0:0"
[[compose_topology.jobs.security.exceptions]]
kind = "capability"
value = "CHOWN"
justification = "Permit the one-shot volume ownership transition."
approved_by = "deployment-owner"
[[compose_topology.jobs.security.exceptions]]
kind = "capability"
value = "FOWNER"
justification = "Permit the one-shot volume mode transition after ownership."
approved_by = "deployment-owner"
[[compose_topology.jobs.volumes]]
name = "manifests"
target = "/manifests"

[[compose_topology.jobs]]
code = "ops"
image_source = "product"
command = ["python", "-m", "app", "diagnose"]
profiles = ["ops"]
networks = ["back"]
depends_on = ["db"]
materials = ["DATABASE_URL"]
[[compose_topology.jobs.mounts]]
kind = "named"
source = "manifests"
target = "/run/dotmac/product-manifests"
read_only = false
[[compose_topology.jobs.mounts]]
kind = "host_material"
source_material = "SIGNING_KEY_SOURCE"
target_material = "SIGNING_KEY_TARGET"
justification = "Mount the held signing material only into this process."
approved_by = "security-owner"
"""


def _parse(text: str = DESCRIPTOR) -> ProductDeploymentSpec:
    return ProductDeploymentSpec.loads(text)


def test_v3_renders_complete_isolated_support_topology() -> None:
    spec = _parse()
    rendered = render_compose(spec)
    project = yaml.safe_load(rendered)
    services = project["services"]
    assert set(services) == {"app", "db", "manifest-init", "migrate", "ops", "relay"}
    assert project["networks"]["back"]["internal"] is True
    assert services["app"]["networks"] == ["front", "back"]
    assert services["relay"]["networks"] == ["back"]
    assert services["manifest-init"]["network_mode"] == "none"
    assert services["manifest-init"]["profiles"] == ["ops"]
    assert services["ops"]["profiles"] == ["ops"]
    assert services["ops"]["image"] == _IMAGE
    assert services["manifest-init"]["image"] == _POSTGRES
    assert services["manifest-init"]["cap_add"] == ["CHOWN", "FOWNER"]
    assert services["manifest-init"]["volumes"] == ["manifests:/manifests"]
    assert services["migrate"]["profiles"] == ["ops"]
    assert "migrate" not in services["app"].get("depends_on", {})
    assert "MIGRATION_DATABASE_URL" not in services["app"].get("environment", {})
    assert "MIGRATION_DATABASE_URL" not in services["relay"].get("environment", {})
    assert services["app"]["environment"]["PLATFORM_DATABASE_URL"].startswith("${")
    assert services["relay"]["environment"][
        "VENDOR_RELAY_DISPATCHER_DATABASE_URL"
    ].startswith("${")
    assert "healthcheck" not in services["relay"]
    assert any("SIGNING_KEY_SOURCE" in mount for mount in services["app"]["volumes"])
    assert any("SIGNING_KEY_SOURCE" in mount for mount in services["relay"]["volumes"])
    assert "manifests:/run/dotmac/product-manifests:ro" in services["app"]["volumes"]
    assert "manifests:/run/dotmac/product-manifests:ro" in services["relay"]["volumes"]
    assert "manifests:/run/dotmac/product-manifests" in services["ops"]["volumes"]
    assert "compose_topology" in build_canonical_document(spec).content["descriptor"]
    assert render_compose(spec) == rendered


@pytest.mark.parametrize(
    ("returncode", "stdout", "ok"),
    [
        (0, '{"data":{"verdict":"relay_draining"}}', True),
        (0, '{"data":{"verdict":"relay_not_running"}}', False),
        (1, '{"data":{"verdict":"relay_draining"}}', False),
        (0, "not-json", False),
    ],
)
def test_relay_ping_requires_durable_health_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    ok: bool,
) -> None:
    worker = _parse().role("relay").worker
    assert worker is not None
    fake_cli = tmp_path / "dotmac-platform"
    fake_cli.write_text(
        "#!/bin/sh\nprintf '%s' \"$FAKE_RELAY_RESPONSE\"\n" 'exit "$FAKE_RELAY_EXIT"\n'
    )
    fake_cli.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_RELAY_RESPONSE", stdout)
    monkeypatch.setenv("FAKE_RELAY_EXIT", str(returncode))
    observed = subprocess.run(  # noqa: S603 -- exact fixture argv, fake CLI in PATH
        [sys.executable, *worker.ping_command[1:]],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (observed.returncode == 0) is ok


def test_support_job_is_an_authorized_mutation_before_migration() -> None:
    plan = build_plan(_parse())
    kinds = [step.kind for step in plan.steps]
    assert kinds.count(StepKind.SUPPORT_JOB) == 1
    assert kinds.index(StepKind.SUPPORT_JOB) < kinds.index(StepKind.MIGRATION_PREFLIGHT)
    step = plan.steps[kinds.index(StepKind.SUPPORT_JOB)]
    assert step.phase is Phase.MUTATE
    assert step.target == "manifest-init"


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    [
        (
            'code = "relay"\nnetworks = ["back"]',
            'code = "relay"\nnetworks = ["missing"]',
            "unknown networks",
        ),
        ('image = "' + _POSTGRES + '"', 'image = "postgres:latest"', "exact digest"),
        (
            'code = "migrate"\nnetworks = ["back"]\nprofiles = ["ops"]',
            'code = "migrate"\nnetworks = ["back"]',
            "profile-gated",
        ),
        ('approved_by = "security-owner"', 'approved_by = ""', "named approver"),
        (
            f'source_digest = "{_INIT_ROLES_DIGEST}"',
            f'source_digest = "{_INIT_ROLES_DIGEST}"\nread_only = false',
            "repository mount must be read-only",
        ),
        (
            'root_approved_by = "deployment-owner"',
            'root_approved_by = ""',
            "root support job",
        ),
        (
            'verify_stdout = "10001:10001"',
            'verify_stdout = ""',
            "expected stdout",
        ),
        (
            "roles_depend_on_migrate = false",
            "roles_depend_on_migrate = true",
            "may not depend on migrate",
        ),
    ],
)
def test_v3_refuses_unbound_or_unsafe_topology(old: str, new: str, reason: str) -> None:
    with pytest.raises(SpecError, match=reason):
        _parse(DESCRIPTOR.replace(old, new))


def test_v1_and_v2_do_not_accept_v3_topology() -> None:
    for schema in ("ProductDeploymentSpec.v1", "ProductDeploymentSpec.v2"):
        with pytest.raises(SpecError):
            _parse(DESCRIPTOR.replace("ProductDeploymentSpec.v3", schema))


def test_support_job_cannot_receive_migration_owner_material() -> None:
    before, marker, ops = DESCRIPTOR.partition(
        '[[compose_topology.jobs]]\ncode = "ops"'
    )
    assert marker
    with pytest.raises(SpecError, match="may not hold the migration owner"):
        _parse(
            before
            + marker
            + ops.replace(
                'materials = ["DATABASE_URL"]',
                'materials = ["MIGRATION_DATABASE_URL"]',
                1,
            )
        )


def test_support_job_cannot_mount_migration_owner_material() -> None:
    mount = """
[[compose_topology.jobs.mounts]]
kind = "host_material"
source_material = "MIGRATION_DATABASE_URL"
target = "/run/owner"
justification = "Do not hand the migration credential to an ops job."
approved_by = "deployment-owner"
"""
    with pytest.raises(SpecError, match="may not hold the migration owner"):
        _parse(DESCRIPTOR + mount)


def _staged_host(tmp_path: Path) -> tuple[Path, Path]:
    (tmp_path / "deploy" / "postgres").mkdir(parents=True)
    (tmp_path / "deploy" / "postgres" / "init-roles.sh").write_text(
        "#!/bin/sh\nroles\n"
    )
    (tmp_path / "deploy" / "postgres" / "bootstrap-credential-function.sql").write_text(
        "-- bootstrap credential function\n"
    )
    source = tmp_path / "held-key"
    source.write_text("fixture material")
    (tmp_path / ".env").write_text(
        f"SIGNING_KEY_SOURCE={source}\nSIGNING_KEY_TARGET=/run/key\n"
    )
    return source, tmp_path / ".env"


def _retained_asset(tmp_path: Path) -> dict[str, RetainedComposeAsset]:
    content = render_compose(_parse()).encode("utf-8")
    path = tmp_path / "candidate-compose.yml"
    path.write_bytes(content)
    return {
        _IMAGE: RetainedComposeAsset(
            path, "sha256:" + hashlib.sha256(content).hexdigest()
        )
    }


def test_deploy_support_job_verifies_exact_output_without_exposing_it(
    tmp_path: Path,
) -> None:
    _staged_host(tmp_path)
    calls: list[list[str]] = []
    candidate_bytes: list[bytes] = []

    def runner(argv: list[str], **kwargs: object) -> CommandResult:
        calls.append(argv)
        candidate_bytes.append(Path(argv[argv.index("-f") + 1]).read_bytes())
        return CommandResult(0, "10001:10001:750\n" if len(calls) == 2 else "", "")

    effects = ComposeHostEffects(
        _parse(),
        tmp_path,
        runner=runner,
        retained_compose_assets=_retained_asset(tmp_path),
    )
    result = effects.run_support_job("manifest-init", timeout_seconds=120, image=_IMAGE)
    assert result.ok
    assert len(calls) == 2
    assert all("--project-directory" in argv for argv in calls)
    assert all(str(tmp_path) in argv for argv in calls)
    assert calls[0][-1] == "manifest-init"
    assert calls[1][-4:] == ["stat", "-c", "%u:%g:%a", "/manifests"]
    assert candidate_bytes == [render_compose(_parse()).encode("utf-8")] * 2
    assert not list(tmp_path.glob(".foundation-candidate-*"))


def test_v3_migration_commands_consume_retained_candidate_bytes(tmp_path: Path) -> None:
    _staged_host(tmp_path)
    calls: list[list[str]] = []

    def runner(argv: list[str], **kwargs: object) -> CommandResult:
        calls.append(argv)
        return CommandResult(0, "head_1\n", "")

    effects = ComposeHostEffects(
        _parse(),
        tmp_path,
        runner=runner,
        retained_compose_assets=_retained_asset(tmp_path),
    )
    assert effects.run_migration_command(
        ["python", "-m", "migrate"], timeout_seconds=120, image=_IMAGE
    ).ok
    assert effects.migration_heads(image=_IMAGE) == ["head_1"]
    assert len(calls) == 2
    assert all(
        argv[argv.index("-f") + 1] != str(tmp_path / "docker-compose.yml")
        for argv in calls
    )
    assert all(".foundation-candidate-" in argv[argv.index("-f") + 1] for argv in calls)
    assert not list(tmp_path.glob(".foundation-candidate-*"))


def test_deploy_support_job_refuses_wrong_postcondition(tmp_path: Path) -> None:
    _staged_host(tmp_path)
    calls = 0

    def runner(argv: list[str], **kwargs: object) -> CommandResult:
        nonlocal calls
        calls += 1
        return CommandResult(0, "0:0\n" if calls == 2 else "", "")

    effects = ComposeHostEffects(
        _parse(),
        tmp_path,
        runner=runner,
        retained_compose_assets=_retained_asset(tmp_path),
    )
    result = effects.run_support_job("manifest-init", timeout_seconds=120, image=_IMAGE)
    assert not result.ok
    assert "0:0" not in repr(result)


def test_deploy_support_job_refuses_missing_repository_mount(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / ".env").write_text(
        "SIGNING_KEY_SOURCE=/missing\nSIGNING_KEY_TARGET=/run/key\n"
    )
    effects = ComposeHostEffects(
        _parse(), tmp_path, retained_compose_assets=_retained_asset(tmp_path)
    )
    with pytest.raises(PreconditionFailed, match="repository mount"):
        effects.run_support_job("manifest-init", timeout_seconds=120, image=_IMAGE)


def test_deploy_support_job_refuses_changed_repository_mount(tmp_path: Path) -> None:
    _staged_host(tmp_path)
    (tmp_path / "deploy" / "postgres" / "init.sh").write_text("changed\n")
    effects = ComposeHostEffects(
        _parse(), tmp_path, retained_compose_assets=_retained_asset(tmp_path)
    )
    with pytest.raises(PreconditionFailed, match="source digest"):
        effects.run_support_job("manifest-init", timeout_seconds=120, image=_IMAGE)


def test_v3_refuses_handwritten_host_compose_mode(tmp_path: Path) -> None:
    with pytest.raises(SpecError, match="Foundation-managed Compose asset"):
        ComposeHostEffects(_parse(), tmp_path, manage_compose_file=False)


def test_v3_refuses_missing_retained_asset(tmp_path: Path) -> None:
    with pytest.raises(SpecError, match="retained Compose asset"):
        ComposeHostEffects(_parse(), tmp_path)


def test_v3_refuses_rebuilt_retained_asset(tmp_path: Path) -> None:
    _staged_host(tmp_path)
    assets = _retained_asset(tmp_path)
    assets[_IMAGE].path.write_text("services: {}\n")
    effects = ComposeHostEffects(_parse(), tmp_path, retained_compose_assets=assets)
    with pytest.raises(PreconditionFailed, match="differ from supplied digest"):
        effects.run_support_job("manifest-init", timeout_seconds=120, image=_IMAGE)


def test_v3_refuses_unbound_rollback_asset(tmp_path: Path) -> None:
    _staged_host(tmp_path)
    effects = ComposeHostEffects(
        _parse(), tmp_path, retained_compose_assets=_retained_asset(tmp_path)
    )
    with pytest.raises(PreconditionFailed, match="separately authorized"):
        effects.switch(timeout_seconds=120, image=_POSTGRES)


def test_v3_plan_does_not_promise_rollback_from_previous_image_alone() -> None:
    online = _parse(
        DESCRIPTOR.replace(
            'compatibility = "maintenance_required"', 'compatibility = "online"'
        )
    )
    plan = build_plan(online, previous_image=_POSTGRES)
    assert not plan.rollback_permitted
    assert "retained Compose asset" in plan.rollback_reason
