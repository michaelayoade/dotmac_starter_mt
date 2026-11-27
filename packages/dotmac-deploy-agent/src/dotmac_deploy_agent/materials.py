"""Typed parse of the dispatch materials the agent acts on (ruling R1).

`dotmac.deploy_target_materials/1` is exactly {schema, pending, store_now,
target_ref}, plus {dispatch_id, params, app_image, dispatch_envelope,
plan_snapshot} when `pending` is true. The params digest, images, primary
service, roles, legacy policy and lease TTL are read ONLY from
`plan_snapshot.deploy_binding`, after `dispatch.verify_dispatch` has proved the
envelope and the snapshot digest. CP-supplied fields are limited to what the plan
cannot hold: the param VALUES (checked only by digest) and the planned
`APP_IMAGE` (which must be the primary service's planned reference). Every
unsigned field is cross-checked against the signed statement and the snapshot.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final, cast

from .canon import DIGEST
from .config import AgentConfig
from .dispatch import PinnedIdentity, plan_digest_of, verify_dispatch
from .jobs import (
    DispatchedJob,
    JobDefinition,
    check_job_admissible,
    check_matches_definition,
    check_plan_binding,
    job_roles,
    plan_jobs,
    refuse_cp_scoped_params,
    refuse_job_role_serving,
    split_params,
)
from .params import Param
from .refusal import Refusal
from .wire import parse_stamp

SCHEMA: Final = "dotmac.deploy_target_materials/1"
_NAME: Final = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}")
_SEGMENT: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_ROLE: Final = _SEGMENT
_SERVICE: Final = _SEGMENT
_DISPATCH_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_REPOSITORY: Final = re.compile(r"[a-z0-9][a-z0-9._:/-]{0,254}")
MATERIALS_ALWAYS: Final = frozenset({"schema", "pending", "store_now", "target_ref"})
MATERIALS_PENDING: Final = frozenset(
    {"dispatch_id", "params", "app_image", "dispatch_envelope", "plan_snapshot"}
)
MATERIALS_KEYS: Final = MATERIALS_ALWAYS | MATERIALS_PENDING
#: Removed: everything the plan binds is READ from the signed snapshot, so a
#: CP-supplied copy could only disagree. `previous_app_image` was unsigned and
#: widened post-run classification; the agent keeps its own last applied pin.
FORBIDDEN_KEYS: Final = frozenset(
    {
        "params_env_digest", "images", "legacy", "lease_ttl_seconds", "bindings",
        "roles", "previous_app_image",
    }
)  # fmt: skip
STATEMENT_KEYS: Final = frozenset(
    {"dispatch_id", "target_ref", "product_code", "environment", "plan_digest"}
)
MAX_LEASE_TTL: Final = 3600
#: Total a plan may hold one lease across renewals (Control's hard maximum).
MAX_LEASE_SECONDS: Final = 10800


@dataclass(frozen=True)
class RoleSpec:
    role: str
    single_use: bool


@dataclass(frozen=True)
class LegacyPolicy:
    version: int
    names: tuple[str, ...]
    sha256: str
    expires_at: datetime


@dataclass(frozen=True)
class ImageSpec:
    """One planned image, in Control's `PlannedImage` names. For `source =
    transfer`, `planned_repository` is the loopback form of the release path and
    `planned_digest` is the release digest, both fixed by Control."""

    service: str
    source: str
    release_repository: str
    release_digest: str
    planned_repository: str
    planned_digest: str

    @property
    def planned_reference(self) -> str:
        return f"{self.planned_repository}@{self.planned_digest}"

    @property
    def archive(self) -> str:
        """The inbox file name is derived, never supplied: `<service>-<hex>.oci.tar`."""
        return f"{self.service}-{self.release_digest.removeprefix('sha256:')}.oci.tar"


@dataclass(frozen=True)
class Materials:
    dispatch_id: str
    plan_digest: str
    target_ref: str
    params: tuple[Param, ...]
    app_image: str
    primary_service: str
    params_env_digest: str
    roles: tuple[RoleSpec, ...]
    legacy: LegacyPolicy | None
    images: tuple[ImageSpec, ...]
    lease_ttl_seconds: int
    #: v2 split lease: the maximum the lease may be renewed to (None on v1 plans).
    lease_max_seconds: int | None
    active_binding_names: tuple[str, ...]
    #: From the SIGNED plan binding (never the target ref): the descriptor's
    #: `secrets_namespace` and the namespace-env fixed on the target. Required
    #: whenever the plan binds roles.
    secrets_namespace: str | None = None
    secrets_environment: str | None = None
    #: From the signed statement: the ordering the replay journal enforces.
    execution_sequence: int = 0
    attempt_no: int = 0
    #: `deploy_binding.jobs` DEFINITIONS (nothing runs from them). `params` above is
    #: the serving set only: a param a job scopes never reaches `params.env`.
    jobs: tuple[JobDefinition, ...] = ()
    #: The one job a `run_job` plan runs (its spec is also the last of `jobs`).
    operation: str = "deploy"
    dispatched_job: DispatchedJob | None = None


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise Refusal("materials_invalid")
    return value


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise Refusal("materials_invalid")
    return cast("dict[str, object]", value)


def _text(value: object, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise Refusal("materials_invalid")
    return value


def _digest(value: object) -> str:
    return _text(value, DIGEST)


def primary_image(plan_snapshot: Mapping[str, object]) -> dict[str, object]:
    """The image the plan binding NAMES as primary. No fallback to "the only
    image": a snapshot that names nothing, or an unknown service, is refused."""
    binding = plan_snapshot.get("deploy_binding")
    if not isinstance(binding, dict):
        raise Refusal("materials_invalid")
    service = binding.get("primary_service")
    raw = binding.get("images")
    chosen = [
        image
        for image in (raw if isinstance(raw, list) else [])
        if isinstance(image, dict)
        and isinstance(service, str)
        and service
        and image.get("service") == service
    ]
    if len(chosen) != 1:
        raise Refusal("materials_invalid")
    return chosen[0]


def plan_roles(plan_snapshot: Mapping[str, object]) -> tuple[RoleSpec, ...]:
    """`(role, single_use)` from the SIGNED plan only; unique and sorted."""
    binding = plan_snapshot.get("deploy_binding")
    rows = binding.get("roles") if isinstance(binding, dict) else None
    if not isinstance(rows, list):
        raise Refusal("materials_invalid")
    roles: list[RoleSpec] = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"role", "single_use"}
            or not isinstance(row["role"], str)
            or not _ROLE.fullmatch(row["role"])
            or type(row["single_use"]) is not bool
        ):
            raise Refusal("materials_invalid")
        roles.append(RoleSpec(row["role"], row["single_use"]))
    names = [r.role for r in roles]
    if names != sorted(set(names)):
        raise Refusal("materials_invalid")
    return tuple(roles)


def expected_app_image(plan_snapshot: Mapping[str, object]) -> str:
    image = primary_image(plan_snapshot)
    return f"{image.get('planned_repository')}@{image.get('planned_digest')}"


def check_shape(document: Mapping[str, object]) -> None:
    """Control's `check_materials_shape`: structure and cross-checks of every
    unsigned field against the statement and the snapshot, with no signature."""
    if not isinstance(document, Mapping):
        raise Refusal("materials_invalid")
    if set(document) & FORBIDDEN_KEYS or not set(document) <= MATERIALS_KEYS:
        raise Refusal("materials_invalid")
    if document.get("schema") != SCHEMA:
        raise Refusal("materials_invalid")
    if not MATERIALS_ALWAYS <= set(document):
        raise Refusal("materials_invalid")
    target_ref = document["target_ref"]
    if not isinstance(target_ref, str):
        raise Refusal("materials_invalid")
    parts = target_ref.split("/")
    if len(parts) != 3 or not all(_SEGMENT.fullmatch(p) for p in parts):
        raise Refusal("materials_invalid")
    try:
        parse_stamp(cast("str", document["store_now"]))
    except Refusal:
        raise Refusal("materials_invalid") from None
    pending = document["pending"]
    if type(pending) is not bool:
        raise Refusal("materials_invalid")
    present = set(document) & MATERIALS_PENDING
    if not pending:
        if present:
            raise Refusal("materials_invalid")
        return
    if present != MATERIALS_PENDING:
        raise Refusal("materials_invalid")
    envelope, snapshot = document["dispatch_envelope"], document["plan_snapshot"]
    if not isinstance(envelope, dict) or not isinstance(snapshot, dict):
        raise Refusal("materials_invalid")
    statement = envelope.get("statement")
    if not isinstance(statement, dict) or not STATEMENT_KEYS <= set(statement):
        raise Refusal("materials_invalid")
    dispatch_id = document["dispatch_id"]
    if not isinstance(dispatch_id, str) or not _DISPATCH_ID.fullmatch(dispatch_id):
        raise Refusal("materials_invalid")
    for key, expected in (
        ("dispatch_id", dispatch_id),
        ("target_ref", target_ref),
        ("product_code", parts[0]),
        ("environment", parts[1]),
        ("plan_digest", plan_digest_of(snapshot)),
    ):
        if statement.get(key) != expected:
            raise Refusal("materials_invalid")
    for key, expected in (
        ("target_ref", target_ref),
        ("product_code", parts[0]),
        ("environment", parts[1]),
    ):
        if snapshot.get(key) != expected:
            raise Refusal("materials_invalid")
    params = document["params"]
    if not isinstance(params, list) or any(
        not isinstance(row, dict) or set(row) != {"name", "material", "type", "value"}
        for row in params
    ):
        raise Refusal("materials_invalid")
    plan_roles(snapshot)
    if document["app_image"] != expected_app_image(snapshot):
        raise Refusal("transfer_repository_mismatch")


def _parse_params(raw: object) -> tuple[Param, ...]:
    params: list[Param] = []
    seen: set[str] = set()
    for item in _list(raw):
        row = _mapping(item)
        name = _text(row["name"], _NAME)
        material, value, kind = row["material"], row["value"], row["type"]
        if (
            name in seen
            or not (material is None or isinstance(material, str))
            or not isinstance(value, str)
            or not isinstance(kind, str)
        ):
            raise Refusal("materials_invalid")
        seen.add(name)
        params.append(Param(name, material, kind, value))
    return tuple(params)


def _parse_images(raw: object) -> tuple[ImageSpec, ...]:
    images: list[ImageSpec] = []
    services: set[str] = set()
    for item in _list(raw):
        row = _mapping(item)
        needed = {
            "service", "source", "release_repository", "release_digest",
            "planned_repository", "planned_digest",
        }  # fmt: skip
        if not needed <= set(row):
            raise Refusal("materials_invalid")
        service = _text(row["service"], _SERVICE)
        release_digest = _digest(row["release_digest"])
        planned_digest = _digest(row["planned_digest"])
        if (
            service in services
            or row["source"] not in ("transfer", "pull", "present")
            or planned_digest != release_digest  # Control guarantees it; re-checked
        ):
            raise Refusal("materials_invalid")
        services.add(service)
        images.append(
            ImageSpec(
                service,
                str(row["source"]),
                _text(row["release_repository"], _REPOSITORY),
                release_digest,
                _text(row["planned_repository"], _REPOSITORY),
                planned_digest,
            )
        )
    if not images:
        raise Refusal("materials_invalid")
    return tuple(images)


def _parse_legacy(raw: object) -> LegacyPolicy | None:
    if raw is None:
        return None
    row = _mapping(raw)
    if not {"policy_version", "sha256", "names", "expires_at"} <= set(row):
        raise Refusal("materials_invalid")
    version, expires = row["policy_version"], row["expires_at"]
    names = tuple(str(n) for n in _list(row["names"]))
    if (
        type(version) is not int
        or version < 1
        or not isinstance(expires, str)
        or list(names) != sorted(set(names))
        or not all(_NAME.fullmatch(n) for n in names)
    ):
        raise Refusal("materials_invalid")
    return LegacyPolicy(version, names, _digest(row["sha256"]), parse_stamp(expires))


def parse_materials(
    document: Mapping[str, object],
    config: AgentConfig,
    *,
    identity: PinnedIdentity,
    store_now: datetime,
) -> Materials:
    """A PENDING document only (an idle one never reaches here)."""
    check_shape(document)
    if document.get("pending") is not True:
        raise Refusal("materials_invalid")
    if document["target_ref"] != config.target_ref:
        raise Refusal("materials_target_mismatch")
    # 1. Nothing below is believed until Control's signature and the plan digest hold.
    verified = verify_dispatch(
        document["dispatch_envelope"],
        document["plan_snapshot"],
        config=config,
        identity=identity,
        store_now=store_now,
        max_age_seconds=config.dispatch_max_age_seconds,
    )
    if document["dispatch_id"] != verified.dispatch_id:
        raise Refusal("materials_invalid")
    # 2. The plan binding is the only source for everything the plan binds.
    snapshot = _mapping(document["plan_snapshot"])
    binding = _mapping(snapshot.get("deploy_binding"))
    ttl = binding.get("lease_ttl_seconds")
    if type(ttl) is not int or not 1 <= ttl <= MAX_LEASE_TTL:
        raise Refusal("lease_ttl_invalid")
    roles = plan_roles(snapshot)
    lease_max = binding.get("lease_max_seconds")
    if lease_max is not None and (
        type(lease_max) is not int or not ttl <= lease_max <= MAX_LEASE_SECONDS
    ):
        raise Refusal("lease_ttl_invalid")
    jobs = plan_jobs(snapshot)  # definitions: nothing runs from them
    if verified.operation not in ("deploy", "rollback", "run_job"):
        raise Refusal("materials_invalid", "operation_unsupported")  # fail closed
    dispatched = check_plan_binding(binding, verified.operation)
    cp_params = _parse_params(document["params"])
    if dispatched is not None:
        if roles:
            raise Refusal("materials_invalid", "run_job_mints_no_serving_role")
        check_matches_definition(dispatched, jobs)
        check_job_admissible(
            dispatched,
            enrolled_roles=config.job_roles,
            store_now=store_now,
            lease_ttl_seconds=lease_max if lease_max is not None else ttl,
            foundation_timeout_seconds=config.foundation_timeout_seconds,
        )
        refuse_cp_scoped_params(cp_params, jobs)
    all_roles = job_roles(jobs) | (
        {dispatched.role} if dispatched and dispatched.role else set()
    )
    refuse_job_role_serving([r.role for r in roles], jobs)
    serving_params = split_params(cp_params, jobs)
    secrets_namespace = binding.get("secrets_namespace")
    secrets_environment = binding.get("secrets_environment")
    for value in (secrets_namespace, secrets_environment):
        # Control emits a namespace with a NULL namespace-env when no role is
        # bound; a value that is present must be a segment.
        if value is not None and not (
            isinstance(value, str) and _SEGMENT.fullmatch(value)
        ):
            raise Refusal("materials_invalid", "bad_secrets_name")
    if roles or all_roles:
        # Roles (serving OR job) mean secret_ids: the naming is then required
        # and must be the one pinned in the root-owned config.
        if secrets_namespace is None or secrets_environment is None:
            raise Refusal("materials_invalid", "bad_secrets_name")
        if (secrets_namespace, secrets_environment) != (
            config.secrets_namespace,
            config.secrets_environment,
        ):
            raise Refusal("secrets_naming_mismatch")
    images = _parse_images(binding.get("images"))
    return Materials(
        dispatch_id=verified.dispatch_id,
        plan_digest=verified.plan_digest,
        target_ref=config.target_ref,
        params=serving_params,
        app_image=cast("str", document["app_image"]),
        primary_service=cast("str", binding["primary_service"]),
        params_env_digest=_digest(binding.get("params_env_digest")),
        roles=roles,
        legacy=_parse_legacy(binding.get("legacy")),
        images=images,
        lease_ttl_seconds=ttl,
        lease_max_seconds=lease_max,
        active_binding_names=tuple(
            _text(n, _NAME) for n in _list(binding.get("active_binding_names", []))
        ),
        secrets_namespace=secrets_namespace,  # type: ignore[arg-type]
        secrets_environment=secrets_environment,  # type: ignore[arg-type]
        execution_sequence=verified.execution_sequence,
        attempt_no=verified.attempt_no,
        jobs=jobs,
        operation=verified.operation,
        dispatched_job=dispatched,
    )
