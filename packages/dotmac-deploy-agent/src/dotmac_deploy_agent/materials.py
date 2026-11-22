"""Typed, exact-key parse of the signed dispatch materials the agent acts on.

The schema name and field names are this package's reading of DESIGN-rev3 s2.2,
s2.4 and s2.6; they live only here so alignment with CP (WP5b) is one file.
Unknown keys are refused: the agent never acts on a field it does not know.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final, cast

from .canon import DIGEST
from .config import AgentConfig
from .params import Param
from .refusal import Refusal
from .wire import parse_stamp

SCHEMA: Final = "dotmac.deploy_target_materials/1"
_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_NAME: Final = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}")
_ROLE: Final = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_SECRET_REF: Final = re.compile(
    r"bao://([a-z0-9][a-z0-9_-]*)/([a-z0-9][a-z0-9_-]*)/kv/"
    r"([a-z][a-z0-9_-]*)/([A-Za-z][A-Za-z0-9_]*)"
)
_TOP: Final = frozenset(
    {
        "schema", "pending", "store_now", "dispatch_id", "target_ref", "params",
        "app_image", "previous_app_image", "params_env_digest", "bindings", "legacy",
    }
)  # fmt: skip


@dataclass(frozen=True)
class BindingName:
    name: str
    environment: str
    product: str
    role: str


@dataclass(frozen=True)
class RoleBindings:
    role: str
    container_uid: int | None
    single_use: bool
    names: tuple[BindingName, ...]


@dataclass(frozen=True)
class LegacyPolicy:
    version: str
    names: tuple[str, ...]
    sha256: str
    expires_at: datetime


@dataclass(frozen=True)
class Materials:
    dispatch_id: str
    target_ref: str
    params: tuple[Param, ...]
    app_image: str
    previous_app_image: str | None
    params_env_digest: str
    bindings: tuple[RoleBindings, ...]
    legacy: LegacyPolicy | None


def _exact(value: object, keys: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise Refusal("materials_invalid")
    return cast("dict[str, object]", value)


def _text(value: object, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise Refusal("materials_invalid")
    return value


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise Refusal("materials_invalid")
    return value


def parse_materials(document: Mapping[str, object], config: AgentConfig) -> Materials:
    if not set(document) <= _TOP or document.get("schema") != SCHEMA:
        raise Refusal("materials_invalid")
    if document.get("target_ref") != config.target_ref:
        raise Refusal("materials_target_mismatch")

    params: list[Param] = []
    names_seen: set[str] = set()
    for item in _list(document.get("params")):
        row = _exact(item, frozenset({"name", "material", "type", "value"}))
        name = _text(row["name"], _NAME)
        material = row["material"]
        value = row["value"]
        kind = row["type"]
        if (
            name in names_seen
            or not (material is None or isinstance(material, str))
            or not isinstance(value, str)
            or not isinstance(kind, str)
        ):
            raise Refusal("materials_invalid")
        names_seen.add(name)
        params.append(Param(name, material, kind, value))

    app_image = document.get("app_image")
    previous = document.get("previous_app_image")
    if not isinstance(app_image, str) or not (
        previous is None or isinstance(previous, str)
    ):
        raise Refusal("materials_invalid")
    expected_digest = document.get("params_env_digest")
    if not isinstance(expected_digest, str) or not DIGEST.fullmatch(expected_digest):
        raise Refusal("materials_invalid")

    roles: list[RoleBindings] = []
    role_names: set[str] = set()
    for item in _list(document.get("bindings")):
        row = _exact(item, frozenset({"role", "container_uid", "single_use", "names"}))
        role = _text(row["role"], _ROLE)
        uid = row["container_uid"]
        single = row["single_use"]
        if (
            role in role_names
            or type(single) is not bool
            or not (uid is None or (type(uid) is int and 0 <= uid < 2**31))
        ):
            raise Refusal("materials_invalid")
        role_names.add(role)
        bound: list[BindingName] = []
        for entry in _list(row["names"]):
            parts = _exact(entry, frozenset({"name", "secret_ref"}))
            ref = _SECRET_REF.fullmatch(str(parts["secret_ref"]))
            name = _text(parts["name"], _NAME)
            # The ref must point at THIS product, THIS role and THIS binding name.
            if (
                ref is None
                or ref[2] != config.product
                or ref[1] != config.environment
                or ref[3] != role
                or ref[4] != name
            ):
                raise Refusal("materials_invalid")
            bound.append(BindingName(name, ref[1], ref[2], role))
        roles.append(RoleBindings(role, uid, single, tuple(bound)))

    legacy: LegacyPolicy | None = None
    raw_legacy = document.get("legacy")
    if raw_legacy is not None:
        row = _exact(
            raw_legacy, frozenset({"version", "names", "sha256", "expires_at"})
        )
        names = tuple(str(n) for n in _list(row["names"]))
        sha = row["sha256"]
        expires = row["expires_at"]
        if (
            not isinstance(sha, str)
            or not DIGEST.fullmatch(sha)
            or not isinstance(expires, str)
            or list(names) != sorted(set(names))
            or not all(_NAME.fullmatch(n) for n in names)
        ):
            raise Refusal("materials_invalid")
        legacy = LegacyPolicy(
            _text(row["version"], _ID), names, sha, parse_stamp(expires)
        )

    return Materials(
        dispatch_id=_text(document.get("dispatch_id"), _ID),
        target_ref=config.target_ref,
        params=tuple(params),
        app_image=app_image,
        previous_app_image=previous,
        params_env_digest=expected_digest,
        bindings=tuple(roles),
        legacy=legacy,
    )
