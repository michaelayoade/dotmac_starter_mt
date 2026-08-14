"""Secret REFERENCES only — the refusal that keeps `secret_refs` honest.

Named for what it enforces, not for what it forbids. It was `secrets.py`, which
the release wheel-content policy correctly refuses: a name-shaped check cannot
tell a module ABOUT secret handling from a file CONTAINING secret material, and
the guard is right to assume the worse of the two. Weakening a security check to
accommodate a filename would trade a real protection for a cosmetic one — and
`secret_refs` is the more accurate name anyway, since references are the only
thing here.


ADR-0024 § 7 requires a connector's configuration contract to carry "secret
REFERENCES, never secret values". A column named `secret_refs` does not enforce
that; this does.

## Why a refusal and not a convention

The failure is silent and permanent. A connector author who writes the API key
straight into config gets a working integration, and the value is then in a
config revision that is IMMUTABLE by design and replicated into every backup.
Nothing downstream would ever complain. So the check belongs at the write, and
it belongs on both fields — a value smuggled into `config_json` is exactly as
leaked as one in `secret_refs`.

## The rule

A reference is `<scheme>://<opaque>` with a scheme this deployment recognises.
Anything else in `secret_refs` is refused. In `config_json` the rule is the
mirror image: a key whose NAME suggests secret material must not carry a
literal — it must be a reference, or live in `secret_refs`.

This deliberately does not dereference anything. ADR-0009 already settled that:
a secret is HELD, never fetched on a resolution path. The module stores a
pointer and hands it to whatever materialises secrets at the deployment
boundary.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = [
    "SECRET_REFERENCE_SCHEMES",
    "SecretValueError",
    "validate_config_revision",
    "validate_secret_refs",
]


class SecretValueError(ValueError):
    """Secret MATERIAL was supplied where a reference was required."""


#: Recognised pointer schemes. `bao://` is the fleet's store; `env://` and
#: `file://` exist for deployments that inject material out of band. Adding a
#: scheme is a reviewed diff, not a config toggle — an unrecognised scheme is
#: indistinguishable from a password that happens to contain "://".
SECRET_REFERENCE_SCHEMES: Final[frozenset[str]] = frozenset(
    {"bao", "env", "file", "aws-sm", "gcp-sm"}
)

_REFERENCE_RE: Final[re.Pattern[str]] = re.compile(r"^([a-z][a-z0-9-]*)://(\S+)$")

#: Config key names that imply secret material. Substring match, because
#: `stripe_api_key` and `apiKey` must both be caught.
_SECRET_NAME_HINTS: Final[tuple[str, ...]] = (
    "secret",
    "password",
    "passwd",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "credential",
    "client_secret",
)


def _is_reference(value: object) -> bool:
    if not isinstance(value, str):
        return False
    match = _REFERENCE_RE.fullmatch(value.strip())
    return bool(match) and match.group(1) in SECRET_REFERENCE_SCHEMES


def validate_secret_refs(secret_refs: dict) -> None:
    """Every value in `secret_refs` must be a recognised reference."""
    for name, value in (secret_refs or {}).items():
        if not _is_reference(value):
            raise SecretValueError(
                f"secret_refs[{name!r}] is not a reference. Expected "
                f"'<scheme>://<id>' with scheme in "
                f"{sorted(SECRET_REFERENCE_SCHEMES)}; a literal secret must "
                "never be stored in a configuration revision"
            )


def _walk(node: object, path: str = "") -> list[tuple[str, str]]:
    """Every (dotted path, string value) pair in a nested config."""
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(_walk(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_walk(value, f"{path}[{index}]"))
    elif isinstance(node, str):
        found.append((path, node))
    return found


def validate_config_revision(config_json: dict, secret_refs: dict) -> None:
    """Refuse a revision that carries secret material anywhere in it.

    Walks `config_json` recursively: a nested `{"auth": {"api_key": "sk_live_…"}}`
    leaks exactly as much as a top-level one, and a check that only looked at
    the first level would pass the shape most connectors actually use.
    """
    validate_secret_refs(secret_refs)
    for path, value in _walk(config_json or {}):
        leaf = path.rsplit(".", 1)[-1].split("[", 1)[0].lower()
        if not any(hint in leaf for hint in _SECRET_NAME_HINTS):
            continue
        if _is_reference(value):
            continue
        raise SecretValueError(
            f"config_json.{path} names secret material but holds a literal. "
            "Put a '<scheme>://<id>' reference here, or move it to secret_refs "
            "— a configuration revision is immutable and ends up in every "
            "backup"
        )
