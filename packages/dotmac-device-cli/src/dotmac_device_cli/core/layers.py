"""Layered profiles: vendor -> family -> model -> firmware.

Each layer is a small TOML file holding only what differs from its parent. An
ordered deep merge produces one complete document, which the strict profile
loader then validates in full. Dictionaries merge key by key; every other value
(scalars, lists) is replaced wholesale by the later layer. A layer file that
carries an unknown key, or a key its kind may not set, fails to load.

Directory shape (``vendor.toml`` / ``family.toml`` / ``model.toml`` are fixed
names; firmware layers are ``<firmware>.toml`` beside ``model.toml``)::

    profiles/<vendor>/vendor.toml
    profiles/<vendor>/<family>/family.toml
    profiles/<vendor>/<family>/<model>/model.toml
    profiles/<vendor>/<family>/<model>/<firmware>.toml
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from importlib.resources.abc import Traversable
from typing import Any

from dotmac_device_cli.core.profile import Profile, ProfileError, load_profile_document

KINDS = ("vendor", "family", "model", "firmware")
_FIXED_NAMES = {"vendor.toml": "vendor", "family.toml": "family", "model.toml": "model"}

# Fixed sections list their keys; dynamic sections (None) take any key whose
# value and name the full profile loader then validates.
_SECTION_KEYS: dict[str, set[str] | None] = {
    "layer": {"id", "kind"},
    "profile": {
        "vendor",
        "family",
        "dialect",
        "model_patterns",
        "firmware_patterns",
        "verification",
    },  # fmt: skip
    "ssh": {"kex", "host_key_types", "ciphers", "macs", "password_only"},
    "prompts": {
        "user",
        "privileged",
        "config",
        "derive_from_prompt_line",
        "pager",
        "continuation",
        "confirm",
    },  # fmt: skip
    "privilege": {"enter", "expect", "elevate"},
    "paging": {"off_command", "on_pager", "strip"},
    "send": {
        "atomic_line",
        "settle_seconds",
        "drain_before_command",
        "inter_command_delay",
        "max_line_length",
        "verify_echo",
    },  # fmt: skip
    "continuation": {"answer_cr_only_if_offered", "max_cr_answers"},
    "logout": {
        "command",
        "max_attempts",
        "confirm_regex",
        "confirm_answer",
        "only_from_states",
        "max_seconds",
    },  # fmt: skip
    "limits": {
        "session_slots",
        "reserved_priority_slots",
        "release_grace_seconds",
        "relogin_grace_seconds",
        "max_reopens_per_scope",
        "write_max_logins",
    },  # fmt: skip
    "detect": {"model", "firmware"},
    "templates": {"read", "write"},
    "confirmations": None,
    "error_markers": None,
    "capabilities": None,
    "operations": None,
    "quirks": None,
}
# Which profile keys each layer kind may set, so a firmware file cannot quietly
# rewrite what vendor it belongs to.
_KIND_PROFILE_KEYS = {
    "vendor": {"vendor", "family", "dialect", "verification"},
    "family": set(),
    "model": {"model_patterns"},
    "firmware": {"firmware_patterns", "verification"},
}


@dataclass(frozen=True, slots=True)
class Layer:
    kind: str
    id: str
    path: tuple[str, ...]  # directory parts from the profiles root
    data: Mapping[str, Any]


def parse_layer(text: str, *, kind: str, path: tuple[str, ...]) -> Layer:
    """Validate one layer file against the schema. Unknown keys fail."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"{'/'.join(path)}: not valid TOML: {exc}") from exc
    where = "/".join(path) or "<root>"
    for section, body in data.items():
        if section not in _SECTION_KEYS:
            raise ProfileError(f"{where}: unknown section [{section}]")
        if not isinstance(body, dict):
            raise ProfileError(f"{where}: [{section}] must be a table")
        allowed = _SECTION_KEYS[section]
        if allowed is not None:
            extra = set(body) - allowed
            if section == "prompts":
                extra = {k for k in extra if not k.startswith("interface_")}
            if extra:
                raise ProfileError(
                    f"{where}: [{section}] unknown key(s) {sorted(extra)}"
                )
    meta = data.get("layer")
    if not meta or meta.get("kind") != kind or not meta.get("id"):
        raise ProfileError(f"{where}: [layer] must declare id and kind={kind!r}")
    extra_profile = set(data.get("profile", {})) - _KIND_PROFILE_KEYS[kind]
    if extra_profile:
        raise ProfileError(
            f"{where}: a {kind} layer may not set profile key(s) "
            f"{sorted(extra_profile)}"
        )
    return Layer(kind=kind, id=str(meta["id"]), path=path, data=data)


def merge(base: Mapping[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    """Ordered deep merge: dicts recurse, everything else is replaced."""
    out = deepcopy(dict(base))
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def resolve_chain(chain: tuple[Layer, ...]) -> Profile:
    """Merge vendor -> family -> model -> firmware and validate the result."""
    if tuple(layer.kind for layer in chain) != KINDS:
        raise ProfileError(
            f"a chain needs exactly {KINDS}, got {[x.kind for x in chain]}"
        )
    doc: dict[str, Any] = {}
    for layer in chain:
        doc = merge(doc, {k: v for k, v in layer.data.items() if k != "layer"})
    doc.setdefault("profile", {})["id"] = chain[-1].id
    return replace(load_profile_document(doc), layer_path=chain[-1].path)


def discover(root: Traversable) -> tuple[Profile, ...]:
    """Walk a profiles tree and resolve every vendor/family/model/firmware chain."""
    profiles: list[Profile] = []
    for vendor_dir in sorted(_dirs(root), key=lambda d: d.name):
        vendor = _layer(vendor_dir, "vendor.toml", "vendor", (vendor_dir.name,))
        for family_dir in sorted(_dirs(vendor_dir), key=lambda d: d.name):
            fpath = (vendor_dir.name, family_dir.name)
            family = _layer(family_dir, "family.toml", "family", fpath)
            for model_dir in sorted(_dirs(family_dir), key=lambda d: d.name):
                mpath = (*fpath, model_dir.name)
                model = _layer(model_dir, "model.toml", "model", mpath)
                for entry in sorted(model_dir.iterdir(), key=lambda e: e.name):
                    if not entry.name.endswith(".toml") or entry.name in _FIXED_NAMES:
                        continue
                    fw_path = (*mpath, entry.name.removesuffix(".toml"))
                    fw = parse_layer(
                        entry.read_text(encoding="utf-8"), kind="firmware", path=fw_path
                    )
                    profiles.append(resolve_chain((vendor, family, model, fw)))
    ids = [p.id for p in profiles]
    if len(set(ids)) != len(ids):
        raise ProfileError("duplicate profile ids")
    return tuple(profiles)


def _dirs(parent: Traversable) -> list[Traversable]:
    return [e for e in parent.iterdir() if e.is_dir() and not e.name.startswith("__")]


def _layer(
    directory: Traversable, name: str, kind: str, path: tuple[str, ...]
) -> Layer:
    file = directory / name
    if not file.is_file():
        raise ProfileError(f"{'/'.join(path)}: missing {name}")
    return parse_layer(file.read_text(encoding="utf-8"), kind=kind, path=path)


__all__ = [
    "KINDS",
    "Layer",
    "discover",
    "merge",
    "parse_layer",
    "resolve_chain",
]
