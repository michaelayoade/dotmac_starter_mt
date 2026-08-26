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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from ipaddress import ip_address
from math import isfinite
from typing import Final
from urllib.parse import urlsplit

from dotmac_kernel.capability_contract import (
    CapabilityCheckStage,
    CapabilityConfigValueFormat,
    CapabilityConfigValueType,
    CapabilityEndpointType,
)

from dotmac_integration.spi import CapabilityDeclaration

__all__ = [
    "SECRET_REFERENCE_SCHEMES",
    "CapabilityConfigurationError",
    "SecretValueError",
    "VerifiedCapabilityConfiguration",
    "validate_config_revision",
    "validate_secret_refs",
    "verify_capability_configuration",
]


class SecretValueError(ValueError):
    """Secret MATERIAL was supplied where a reference was required."""


class CapabilityConfigurationError(ValueError):
    """Configuration does not satisfy its exact product-owned contract."""


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedCapabilityConfiguration:
    """Value-free evidence returned by generic contract verification."""

    owner_code: str
    capability_code: str
    schema_version: int
    contract_digest: str
    operation_codes: tuple[str, ...]
    activation_check_codes: tuple[str, ...]


#: Recognised pointer schemes. `bao://` is the fleet's store; `env://` and
#: `file://` exist for deployments that inject material out of band. Adding a
#: scheme is a reviewed diff, not a config toggle — an unrecognised scheme is
#: indistinguishable from a password that happens to contain "://".
SECRET_REFERENCE_SCHEMES: Final[frozenset[str]] = frozenset(
    {"bao", "env", "file", "aws-sm", "gcp-sm"}
)

_REFERENCE_RE: Final[re.Pattern[str]] = re.compile(r"^([a-z][a-z0-9-]*)://(\S+)$")
_STABLE_VALUE_RE: Final[re.Pattern[str]] = re.compile(
    r"^[a-z0-9](?:[a-z0-9._-]{0,118}[a-z0-9])?$"
)
_EMAIL_LOCAL_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}$"
)
_FQDN_LABEL_RE: Final[re.Pattern[str]] = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)

#: Config key names that imply secret material. Substring match, because
#: `vendor_api_key` and `apiKey` must both be caught. (The illustration is
#: deliberately provider-neutral: `tests/architecture/
#: test_integration_ingress_hygiene.py` scans this package for provider names,
#: and a name in a comment is a name in the module — ADR-0024 § 7.)
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
    return match is not None and match.group(1) in SECRET_REFERENCE_SCHEMES


def validate_secret_refs(secret_refs: dict[str, object]) -> None:
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


def validate_config_revision(
    config_json: dict[str, object], secret_refs: dict[str, object]
) -> None:
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


def _valid_fqdn(value: object) -> bool:
    if not isinstance(value, str) or value != value.strip().lower():
        return False
    if len(value) > 253 or "." not in value or value.endswith("."):
        return False
    return all(_FQDN_LABEL_RE.fullmatch(label) for label in value.split("."))


def _valid_https_url(value: object) -> bool:
    if not isinstance(value, str) or value != value.strip():
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return False
    if port is not None and not 1 <= port <= 65535:
        return False
    return _valid_fqdn(parsed.hostname)


def _valid_host_port(value: object) -> bool:
    if not isinstance(value, str) or value != value.strip():
        return False
    try:
        parsed = urlsplit(f"//{value}")
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    if (
        host is None
        or port is None
        or not 1 <= port <= 65535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return False
    if _valid_fqdn(host):
        return True
    try:
        ip_address(host)
    except ValueError:
        return False
    return True


def _valid_email(value: object) -> bool:
    if not isinstance(value, str) or value != value.strip() or value.count("@") != 1:
        return False
    local, domain = value.rsplit("@", 1)
    return _EMAIL_LOCAL_RE.fullmatch(local) is not None and _valid_fqdn(domain)


def _field_error(field_code: str, expectation: str) -> CapabilityConfigurationError:
    # Never include the supplied value: this verifier handles held secret
    # references and its exceptions routinely reach logs and receipts.
    return CapabilityConfigurationError(
        f"configuration field {field_code!r} {expectation}"
    )


def _verify_typed_value(
    field_code: str,
    value: object,
    value_type: CapabilityConfigValueType,
    value_format: CapabilityConfigValueFormat,
) -> None:
    if value_type is CapabilityConfigValueType.BOOLEAN:
        if type(value) is not bool:
            raise _field_error(field_code, "must be a boolean")
        return
    if value_type is CapabilityConfigValueType.DECIMAL:
        if (
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or not isfinite(float(value))
        ):
            raise _field_error(field_code, "must be a finite decimal")
        return
    if value_type is CapabilityConfigValueType.INTEGER:
        if type(value) is not int:
            raise _field_error(field_code, "must be an integer")
        if (
            value_format
            in {
                CapabilityConfigValueFormat.BYTE_QUANTITY,
                CapabilityConfigValueFormat.NONNEGATIVE_INTEGER,
            }
            and value < 0
        ):
            raise _field_error(field_code, "must be nonnegative")
        if value_format is CapabilityConfigValueFormat.POSITIVE_INTEGER and value < 1:
            raise _field_error(field_code, "must be positive")
        return
    if value_type is CapabilityConfigValueType.REFERENCE:
        if not isinstance(value, str) or not value or value != value.strip():
            raise _field_error(field_code, "must be a non-empty reference")
        return
    if value_type is CapabilityConfigValueType.SECRET_REFERENCE:
        if not _is_reference(value):
            raise _field_error(field_code, "must be a recognised secret reference")
        return
    if value_type is CapabilityConfigValueType.STRING_LIST:
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise _field_error(field_code, "must be a list of strings")
        if value_format is CapabilityConfigValueFormat.FQDN_LIST and not all(
            _valid_fqdn(item) for item in value
        ):
            raise _field_error(field_code, "must contain only canonical FQDNs")
        return
    if not isinstance(value, str):
        raise _field_error(field_code, "must be a string")
    if value_format is CapabilityConfigValueFormat.FQDN and not _valid_fqdn(value):
        raise _field_error(field_code, "must be a canonical FQDN")
    if value_format is CapabilityConfigValueFormat.HTTPS_URL and not _valid_https_url(
        value
    ):
        raise _field_error(field_code, "must be an HTTPS URL with a canonical host")
    if value_format is CapabilityConfigValueFormat.EMAIL_ADDRESS and not _valid_email(
        value
    ):
        raise _field_error(field_code, "must be an email address")
    if (
        value_format is CapabilityConfigValueFormat.STABLE_CODE
        and _STABLE_VALUE_RE.fullmatch(value) is None
    ):
        raise _field_error(field_code, "must be a lowercase stable code")


def _verify_endpoint_value(
    endpoint_code: str, value: object, endpoint_type: CapabilityEndpointType
) -> None:
    valid = False
    if endpoint_type is CapabilityEndpointType.FQDN:
        valid = _valid_fqdn(value)
    elif endpoint_type is CapabilityEndpointType.HTTPS_URL:
        valid = _valid_https_url(value)
    elif endpoint_type is CapabilityEndpointType.HOST_PORT:
        valid = _valid_host_port(value)
    if not valid:
        raise _field_error(
            endpoint_code,
            f"must satisfy endpoint type {endpoint_type.value!r}",
        )


def verify_capability_configuration(
    declaration: CapabilityDeclaration,
    *,
    config: Mapping[str, object],
    secret_refs: Mapping[str, object],
    required_operation_codes: Iterable[str] = (),
) -> VerifiedCapabilityConfiguration:
    """Verify desired values against one exact product-owned snapshot.

    This is the reusable pre-I/O gate for binding activation and provisioning
    plan/command acceptance.  It is deliberately provider-neutral: all names,
    types, formats, endpoints and checks come from the supplied snapshot.
    Secret references occupy their own mapping and are never dereferenced or
    rendered into a result or exception.
    """

    if not isinstance(declaration, CapabilityDeclaration):
        raise CapabilityConfigurationError(
            "declaration must be a CapabilityDeclaration"
        )
    snapshot = declaration.contract_snapshot
    if snapshot is None:
        raise CapabilityConfigurationError(
            f"capability {declaration.capability_id!r} has no owner contract snapshot"
        )
    config_copy = dict(config)
    refs_copy = dict(secret_refs)
    try:
        validate_config_revision(config_copy, refs_copy)
    except SecretValueError as exc:
        # The existing validator's messages are value-free, but translating to
        # the owner-contract error keeps this public gate one typed refusal.
        raise CapabilityConfigurationError(str(exc)) from None

    fields = {field.field_code: field for field in snapshot.config_fields}
    endpoints = {
        endpoint.endpoint_code: endpoint for endpoint in snapshot.endpoint_requirements
    }
    collisions = set(fields) & set(endpoints)
    if collisions:
        raise CapabilityConfigurationError(
            f"owner contract declares {sorted(collisions)[0]!r} as both a "
            "configuration field and an endpoint"
        )
    secret_fields = {
        code
        for code, field in fields.items()
        if field.value_type is CapabilityConfigValueType.SECRET_REFERENCE
    }
    ordinary_fields = set(fields) - secret_fields
    known_config = ordinary_fields | set(endpoints)

    unknown_config = set(config_copy) - known_config
    if unknown_config:
        raise _field_error(sorted(unknown_config)[0], "is not declared")
    unknown_refs = set(refs_copy) - secret_fields
    if unknown_refs:
        raise _field_error(sorted(unknown_refs)[0], "is not a declared secret field")
    misplaced = set(config_copy) & secret_fields
    if misplaced:
        raise _field_error(sorted(misplaced)[0], "must be supplied through secret_refs")

    for code, field in fields.items():
        source = refs_copy if code in secret_fields else config_copy
        if code not in source:
            if field.required:
                raise _field_error(code, "is required")
            continue
        _verify_typed_value(code, source[code], field.value_type, field.value_format)

    for code, endpoint in endpoints.items():
        if code not in config_copy:
            if endpoint.required:
                raise _field_error(code, "is a required endpoint")
            continue
        _verify_endpoint_value(code, config_copy[code], endpoint.endpoint_type)

    required = tuple(required_operation_codes)
    if required != tuple(sorted(set(required))):
        raise CapabilityConfigurationError(
            "required_operation_codes must be unique and sorted"
        )
    declared_operations = tuple(
        operation.operation_code for operation in snapshot.operations
    )
    unknown_operations = set(required) - set(declared_operations)
    if unknown_operations:
        raise CapabilityConfigurationError(
            f"capability {declaration.capability_id!r} does not declare operation "
            f"{sorted(unknown_operations)[0]!r}"
        )

    activation_checks = tuple(
        check.check_code
        for check in snapshot.checks
        if check.stage is CapabilityCheckStage.ACTIVATION and check.required
    )
    return VerifiedCapabilityConfiguration(
        owner_code=snapshot.owner_code,
        capability_code=snapshot.capability_code,
        schema_version=snapshot.schema_version,
        contract_digest=snapshot.digest,
        operation_codes=declared_operations,
        activation_check_codes=activation_checks,
    )
