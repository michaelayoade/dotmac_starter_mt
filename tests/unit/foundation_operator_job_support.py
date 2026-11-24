"""Shared builders and fakes for the operator-job tests (documentation ranges only)."""

from __future__ import annotations

import copy
import hashlib
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from dotmac_deployment_foundation import operator_job_contract as contract
from dotmac_deployment_foundation.engine.run import CommandResult
from dotmac_deployment_foundation.operator_job import (
    OperatorJobPair,
    OperatorJobPlan,
    RunJobConsumptionRequest,
    WaitResult,
    plan_operator_job,
)
from dotmac_deployment_foundation.providers.compose_operator_job import (
    build_create_argv,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

from tests.unit.test_deployment_foundation_live_instance import (
    document as v5_document,
)

JOB_TOML = """
[[compose_topology.jobs]]
code = "importer"
image_source = "product"
command = ["python", "-m", "importer"]
entrypoint = []
profiles = ["ops"]
networks = ["back"]
timeout_seconds = 2400
[compose_topology.jobs.operator]
secrets_target = "/run/secrets/dotmac"
inputs_root_material = "JOB_INPUTS_ROOT"
[compose_topology.jobs.operator.commands]
import_execute = ["python", "-m", "importer", "execute"]
"""

IMAGE_REPOSITORY = "registry.example.invalid/app"
IMAGE_DIGEST = "sha256:" + "e" * 64
PARAMS = b"IMPORT_BATCH=batch-1\n"
PARAMS_DIGEST = "sha256:" + hashlib.sha256(PARAMS).hexdigest()
INPUT_BYTES = b'{"rows": 1}'
INPUT_SHA = "sha256:" + hashlib.sha256(INPUT_BYTES).hexdigest()
INPUTS_ROOT = "/srv/example/job-work"
INVOCATION = "a" * 32
BOUND = ("MIGRATION_DATABASE_URL",)
NOW_EPOCH = int(datetime(2026, 11, 23, tzinfo=UTC).timestamp())
IDENTITY = {
    contract.IDENTITY_JOB_UID: 10001,
    contract.IDENTITY_JOB_USER: "importer",
    contract.IDENTITY_INPUTS_ROOT: INPUTS_ROOT,
}
ARGV = ["python", "-m", "importer", "execute", "--manifest", "/job/in/manifest.json"]


def make_document(job_toml: str = JOB_TOML) -> dict[str, Any]:
    """A complete ProductDeploymentSpec.v5 document plus the given job tables.

    The operator table is v5-gated (inside the approved digest), so every
    operator fixture is a v5 descriptor.
    """
    doc = v5_document()
    jobs = tomllib.loads(job_toml)["compose_topology"]["jobs"]
    doc["compose_topology"]["jobs"] = [*doc["compose_topology"]["jobs"], *jobs]
    return doc


def make_spec(job_toml: str = JOB_TOML) -> ProductDeploymentSpec:
    return ProductDeploymentSpec.from_document(make_document(job_toml))


def make_resolved(**overrides: Any) -> dict[str, Any]:
    resolved: dict[str, Any] = {
        contract.RESOLVED_OPERATION: contract.OPERATION_RUN_JOB,
        contract.RESOLVED_DISPATCH_ID: "disp-0001",
        contract.RESOLVED_DISPATCHED_JOB: {
            contract.JOB_NAME: "import_execute",
            contract.JOB_ARGV: list(ARGV),
            contract.JOB_ROLE: "import",
            contract.JOB_MATERIALS: ["IMPORT_BATCH", "MIGRATION_DATABASE_URL"],
            contract.JOB_PARAMS_ENV_DIGEST: PARAMS_DIGEST,
            contract.JOB_INPUTS: [
                {
                    contract.INPUT_FLAG: "--manifest",
                    contract.INPUT_PATH: "/job/in/manifest.json",
                    contract.INPUT_SHA256: INPUT_SHA,
                }
            ],
            contract.JOB_TIMEOUT_SECONDS: 600,
        },
        contract.RESOLVED_IMAGE: {
            contract.IMAGE_REPOSITORY: IMAGE_REPOSITORY,
            contract.IMAGE_DIGEST: IMAGE_DIGEST,
            contract.IMAGE_SOURCE: contract.IMAGE_SOURCE_PRESENT,
        },
        contract.RESOLVED_JOB_BINDING_NAMES: [
            "LEGACY_DATABASE_URL",
            "MIGRATION_DATABASE_URL",
        ],
        contract.RESOLVED_SECRETS_NAMESPACE: "appns",
        contract.RESOLVED_DESCRIPTOR_DIGEST: make_spec()
        .to_canonical_document()
        .sha256_digest(),
    }
    resolved.update(overrides)
    return resolved


def make_plan(
    resolved: Mapping[str, Any] | None = None,
    identity: Mapping[str, Any] | None = IDENTITY,
) -> OperatorJobPlan:
    return plan_operator_job(make_spec(), resolved or make_resolved(), identity)


def good_env(plan: OperatorJobPlan) -> dict[str, str]:
    from dotmac_deployment_foundation.operator_job import argv_digest

    return {
        contract.ENV_JOB: plan.name,
        contract.ENV_ARGV_DIGEST: argv_digest(plan.argv),
        contract.ENV_PARAMS_ENV: "/run/example/params.env",
        contract.ENV_SECRETS_DIR: plan.secrets_dir,
        contract.ENV_SECRET_FILES: ",".join(plan.secret_files),
        contract.ENV_RENDER_INVOCATION: INVOCATION,
        contract.ENV_DEADLINE: str(NOW_EPOCH + 3600),
        contract.ENV_RENDER_UNIT: plan.render_unit,
    }


def good_container(plan: OperatorJobPlan) -> dict[str, Any]:
    mounts: list[dict[str, Any]] = []
    for secret in plan.secret_mounts:
        mounts.append(
            {
                "Type": "bind",
                "Source": secret.source,
                "Destination": secret.target,
                "RW": False,
            }
        )
    for item in plan.input_mounts:
        mounts.append(
            {
                "Type": "bind",
                "Source": item.source,
                "Destination": item.target,
                "RW": False,
            }
        )
    mounts.append(
        {
            "Type": "volume",
            "Name": plan.volume_name,
            "Destination": plan.outputs_target,
            "RW": True,
        }
    )
    return {
        "HostConfig": {
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "AutoRemove": False,
            "Privileged": False,
            "CapDrop": list(plan.cap_drop),
            "SecurityOpt": ["no-new-privileges"],
            "CapAdd": None,
            "Devices": None,
            "PidMode": "",
            "IpcMode": "private",
            "Binds": None,
            "ReadonlyRootfs": True,
            "Tmpfs": {path: "" for path in plan.tmpfs},
        },
        "Config": {
            "User": plan.user,
            "Entrypoint": None,
            "Cmd": list(plan.argv),
            "Image": plan.image_ref,
            "Env": ["PATH=/usr/bin", *[f"{n}=x" for n in plan.scoped_names]],
        },
        "Image": "sha256:" + "1" * 64,
        "NetworkSettings": {"Networks": {n: {} for n in plan.networks}},
        "Mounts": mounts,
    }


def good_image(plan: OperatorJobPlan) -> dict[str, Any]:
    return {
        "Id": "sha256:" + "1" * 64,
        "RepoDigests": [plan.image_ref],
        "Config": {"Env": ["PATH=/usr/bin"]},
    }


class FakeAuthority:
    """The provisional run_job half of the bindings' provider."""

    def __init__(self) -> None:
        self.consumed: list[str] = []
        self.requests: list[RunJobConsumptionRequest] = []
        self.lease_expires_at: int | None = NOW_EPOCH + 7200

    def resolve_run_job(self, **_: Any) -> Mapping[str, Any]:
        return make_resolved()

    def consume_run_job(
        self, *, request: RunJobConsumptionRequest
    ) -> Mapping[str, Any] | None:
        self.requests.append(request)
        if request.dispatch_id in self.consumed:
            raise Conflict()
        self.consumed.append(request.dispatch_id)
        if self.lease_expires_at is None:
            return None
        return {contract.CONSUME_LEASE_EXPIRES: self.lease_expires_at}


class Conflict(Exception):
    status = 409


def make_bindings(authority: FakeAuthority | None = None) -> Any:
    return SimpleNamespace(authorization_v3_provider=authority or FakeAuthority())


PAIR = OperatorJobPair(b"{}", b"{}")


class FakeEffects:
    """Records every call; returns fixtures. No Docker, no filesystem."""

    def __init__(self, plan: OperatorJobPlan) -> None:
        self.plan = plan
        self.env = good_env(plan)
        self.container = good_container(plan)
        self.image = good_image(plan)
        self.params = PARAMS
        self.show = {
            "Result": "success",
            "ExecMainStatus": "0",
            "InvocationID": INVOCATION,
        }
        self.input_hash = hashlib.sha256(INPUT_BYTES).hexdigest()
        self.volume_present = False
        self.docker_uids: frozenset[int] = frozenset({1000})
        self.docker_gids: frozenset[int] = frozenset({999})
        self.wait = WaitResult(0, False)
        self.calls: list[str] = []
        self.create_argv: list[list[str]] = []
        self.removed: list[str] = []
        self.terminate = False
        self.removed_for: list[str] = []
        self.accounts: dict[str, int] = {"importer": 10001}
        self.image_dir: tuple[int, int, int] | None = (10001, 10001, 0o700)
        self.net_labels: dict[str, Mapping[str, str] | None] = {
            name: {
                contract.LABEL_COMPOSE_PROJECT: plan.product,
                contract.LABEL_COMPOSE_NETWORK: code,
            }
            for name, code in zip(plan.networks, plan.network_codes, strict=True)
        }
        self.now_value = datetime(2026, 11, 23, tzinfo=UTC)

    def environ(self) -> Mapping[str, str]:
        return copy.copy(self.env)

    def now(self) -> datetime:
        return self.now_value

    def read_params_env(self, path: str) -> bytes:
        self.calls.append("read_params_env")
        return self.params

    def docker_group_ids(self) -> tuple[frozenset[int], frozenset[int]]:
        return self.docker_uids, self.docker_gids

    def systemctl_show(self, unit: str) -> Mapping[str, str]:
        self.calls.append(f"show:{unit}")
        return self.show

    def image_present(self, ref: str) -> bool:
        return True

    def inspect_image(self, ref: str) -> Mapping[str, Any]:
        return self.image

    def hash_input(self, path: str) -> str:
        self.calls.append(f"hash:{path}")
        return self.input_hash

    def volume_exists(self, name: str) -> bool:
        return self.volume_present

    def create_volume(self, name: str, *, dispatch_id: str) -> None:
        self.calls.append("create_volume")

    def create_container(
        self, plan: OperatorJobPlan, *, env_file: str | None
    ) -> CommandResult:
        self.calls.append("create")
        self.create_argv.append(
            build_create_argv("/usr/bin/docker", plan, env_file=env_file)
        )
        return CommandResult(0)

    def connect_networks(self, plan: OperatorJobPlan) -> CommandResult:
        self.calls.append("connect")
        return CommandResult(0)

    def inspect_container(self, name: str) -> Mapping[str, Any]:
        return self.container

    def start_and_wait(self, name: str, *, timeout_seconds: int) -> WaitResult:
        self.calls.append(f"start:{timeout_seconds}")
        return self.wait

    def remove_container(self, name: str, *, dispatch_id: str) -> bool:
        self.calls.append("remove")
        self.removed.append(name)
        self.removed_for.append(dispatch_id)
        return True

    def read_job_identity(self) -> Mapping[str, Any] | None:
        return dict(IDENTITY)

    def host_uid(self, account: str) -> int | None:
        return self.accounts.get(account)

    def stat_image_dir(
        self, image_ref: str, path: str, *, probe_name: str, dispatch_id: str
    ) -> tuple[int, int, int] | None:
        self.calls.append(f"stat:{path}")
        return self.image_dir

    def network_labels(self, name: str) -> Mapping[str, str] | None:
        return self.net_labels.get(name)

    @contextmanager
    def sigterm_trap(self) -> Iterator[None]:
        yield

    def terminate_requested(self) -> bool:
        return self.terminate
