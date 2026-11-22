"""Typed parse of the dispatch materials the agent acts on (ruling R1).

`dotmac.deploy_target_materials/1` carries a Control-signed `dispatch_envelope`
and the `plan_snapshot` it binds. The agent reads the params digest, the images,
the legacy policy and the lease TTL ONLY from `plan_snapshot.deploy_binding`,
after `dispatch.verify_dispatch` has proved the envelope and the snapshot digest.
CP-supplied fields are limited to what the plan cannot hold: the param VALUES
(checked only by digest), the planned `APP_IMAGE`, the previous pin, and the
role list. Unknown top-level keys are refused.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final, cast

from .canon import DIGEST
from .config import AgentConfig
from .dispatch import PinnedIdentity, verify_dispatch
from .params import Param
from .refusal import Refusal
from .wire import parse_stamp

SCHEMA: Final = "dotmac.deploy_target_materials/1"
_NAME: Final = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}")
_SEGMENT: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_ROLE: Final = _SEGMENT
_SERVICE: Final = _SEGMENT
_REPOSITORY: Final = re.compile(r"[a-z0-9][a-z0-9._:/-]{0,254}")
MATERIALS_KEYS: Final = frozenset(
    {
        "schema", "pending", "store_now", "dispatch_id", "target_ref", "params",
        "app_image", "previous_app_image", "dispatch_envelope", "plan_snapshot",
    }
)  # fmt: skip
#: Removed from the first draft: they are READ from the signed plan instead, so a
#: CP-supplied copy could only ever disagree with what Control signed.
FORBIDDEN_KEYS: Final = frozenset(
    {"params_env_digest", "images", "legacy", "lease_ttl_seconds", "bindings", "roles"}
)
ROLE_KEYS: Final = frozenset({"role", "single_use"})
MAX_LEASE_TTL: Final = 3600


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
    previous_app_image: str | None
    params_env_digest: str
    roles: tuple[RoleSpec, ...]
    legacy: LegacyPolicy | None
    images: tuple[ImageSpec, ...]
    lease_ttl_seconds: int
    active_binding_names: tuple[str, ...]


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


def _parse_params(raw: object) -> tuple[Param, ...]:
    params: list[Param] = []
    seen: set[str] = set()
    for item in _list(raw):
        row = _mapping(item)
        if set(row) != {"name", "material", "type", "value"}:
            raise Refusal("materials_invalid")
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
            or row["source"] not in ("transfer", "pull")
            or planned_digest
            != release_digest  # Control guarantees it; the agent re-checks
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


def _parse_roles(raw: object) -> tuple[RoleSpec, ...]:
    roles: list[RoleSpec] = []
    seen: set[str] = set()
    for item in _list(raw):
        row = _mapping(item)
        if set(row) != {"role", "single_use"}:
            raise Refusal("materials_invalid")
        role = _text(row["role"], _ROLE)
        if role in seen or type(row["single_use"]) is not bool:
            raise Refusal("materials_invalid")
        seen.add(role)
        roles.append(RoleSpec(role, row["single_use"]))
    return tuple(roles)


def primary_image(plan_snapshot: Mapping[str, object]) -> dict[str, object]:
    """The primary service's image: the one named for the product, or the only one."""
    binding = plan_snapshot.get("deploy_binding")
    raw = binding.get("images") if isinstance(binding, dict) else None
    images = [i for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []
    product = plan_snapshot.get("product_code")
    chosen = [i for i in images if i.get("service") == product] or (
        images if len(images) == 1 else []
    )
    if len(chosen) != 1:
        raise Refusal("materials_invalid")
    return chosen[0]


def expected_app_image(plan_snapshot: Mapping[str, object]) -> str:
    image = primary_image(plan_snapshot)
    return f"{image.get('planned_repository')}@{image.get('planned_digest')}"


def check_shape(document: Mapping[str, object]) -> None:
    """The structural contract of the document (Control's `check_materials_shape`),
    before and independent of any signature."""
    if not isinstance(document, Mapping):
        raise Refusal("materials_invalid")
    if set(document) & FORBIDDEN_KEYS or not set(document) <= MATERIALS_KEYS:
        raise Refusal("materials_invalid")
    if document.get("schema") != SCHEMA:
        raise Refusal("materials_invalid")
    for key in ("dispatch_envelope", "plan_snapshot"):
        if not isinstance(document.get(key), Mapping):
            raise Refusal("materials_invalid")
    snapshot = cast("Mapping[str, object]", document["plan_snapshot"])
    if document.get("app_image") != expected_app_image(snapshot):
        raise Refusal("transfer_repository_mismatch")


def parse_materials(
    document: Mapping[str, object],
    config: AgentConfig,
    *,
    identity: PinnedIdentity,
    store_now: datetime,
) -> Materials:
    check_shape(document)
    # 1. Nothing below is believed until Control's signature and the plan digest hold.
    verified = verify_dispatch(
        document.get("dispatch_envelope"),
        document.get("plan_snapshot"),
        config=config,
        identity=identity,
        store_now=store_now,
    )
    # The CP-supplied copies, where present, may only agree with what was signed.
    if document.get("dispatch_id", verified.dispatch_id) != verified.dispatch_id:
        raise Refusal("materials_invalid")
    if document.get("target_ref", config.target_ref) != config.target_ref:
        raise Refusal("materials_target_mismatch")
    # 2. The plan binding is the only source for digest, images, legacy and TTL.
    binding = _mapping(_mapping(document["plan_snapshot"]).get("deploy_binding"))
    ttl = binding.get("lease_ttl_seconds")
    if type(ttl) is not int or not 1 <= ttl <= MAX_LEASE_TTL:
        raise Refusal("lease_ttl_invalid")
    app_image, previous = document.get("app_image"), document.get("previous_app_image")
    if not isinstance(app_image, str) or not (
        previous is None or isinstance(previous, str)
    ):
        raise Refusal("materials_invalid")
    return Materials(
        dispatch_id=verified.dispatch_id,
        plan_digest=verified.plan_digest,
        target_ref=config.target_ref,
        params=_parse_params(document.get("params")),
        app_image=app_image,
        previous_app_image=previous,
        params_env_digest=_digest(binding.get("params_env_digest")),
        # Roles come ONLY from the signed plan: mint for nothing else.
        roles=_parse_roles(binding.get("roles", [])),
        legacy=_parse_legacy(binding.get("legacy")),
        images=_parse_images(binding.get("images")),
        lease_ttl_seconds=ttl,
        active_binding_names=tuple(
            _text(n, _NAME) for n in _list(binding.get("active_binding_names", []))
        ),
    )
