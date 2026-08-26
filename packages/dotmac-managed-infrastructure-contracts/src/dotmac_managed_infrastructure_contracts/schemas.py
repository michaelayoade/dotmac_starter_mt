"""Canonical JSON Schema bytes for managed infrastructure operations."""

from __future__ import annotations

from collections.abc import Mapping

from dotmac_kernel import CAPABILITY_SCHEMA_DIALECT, CapabilitySchemaDocument

_PUBLIC = {"x-dotmac-data-classification": "public_non_secret"}
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$"
_CIDR_PATTERN = r"^[0-9A-Fa-f:.]+/[0-9]{1,3}$"


def _string(**keywords: object) -> dict[str, object]:
    return {"type": "string", **keywords}


def _public_string(**keywords: object) -> dict[str, object]:
    return _string(**_PUBLIC, **keywords)


def _public_boolean() -> dict[str, object]:
    return {"type": "boolean", **_PUBLIC}


def _public_integer(**keywords: object) -> dict[str, object]:
    return {"type": "integer", **_PUBLIC, **keywords}


def _string_array(
    *, public: bool = False, pattern: str | None = None
) -> dict[str, object]:
    marker = _PUBLIC if public else {}
    if public:
        item = (
            _public_string(pattern=pattern)
            if pattern is not None
            else _public_string(minLength=1)
        )
    else:
        item = _string(pattern=pattern) if pattern is not None else _string(minLength=1)
    return {
        "items": item,
        "type": "array",
        "uniqueItems": True,
        **marker,
    }


def _object_schema(
    schema_ref: str,
    *,
    properties: Mapping[str, object],
    required: tuple[str, ...],
) -> CapabilitySchemaDocument:
    return CapabilitySchemaDocument.from_mapping(
        {
            "$id": schema_ref,
            "$schema": CAPABILITY_SCHEMA_DIALECT,
            "additionalProperties": False,
            "properties": dict(properties),
            "required": list(required),
            "type": "object",
        }
    )


def _schema_ref(capability: str, operation: str, direction: str) -> str:
    return (
        f"schema:dotmac-managed-infrastructure/"
        f"{capability}/{operation}/{direction}@v1"
    )


def _common_observation(states: tuple[str, ...]) -> dict[str, object]:
    return {
        "lifecycle_state": {"enum": list(states), "type": "string", **_PUBLIC},
        "observed_at": _public_string(format="date-time"),
        "observed_configuration_digest": _public_string(pattern=_DIGEST_PATTERN),
        "provider_resource_ref": _public_string(pattern=_REF_PATTERN),
        "resource_ref": _public_string(pattern=_REF_PATTERN),
    }


def _build_family_schemas(
    capability: str,
    *,
    desired: dict[str, object],
    desired_required: tuple[str, ...],
    observed: dict[str, object],
    observed_required: tuple[str, ...],
) -> tuple[CapabilitySchemaDocument, ...]:
    return (
        _object_schema(
            _schema_ref(capability, "apply", "input"),
            properties=desired,
            required=desired_required,
        ),
        _object_schema(
            _schema_ref(capability, "apply", "output"),
            properties=observed,
            required=observed_required,
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "input"),
            properties={"resource_ref": _string(pattern=_REF_PATTERN)},
            required=("resource_ref",),
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "output"),
            properties={
                "cancelled": _public_boolean(),
                "resource_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=("cancelled", "resource_ref"),
        ),
        _object_schema(
            _schema_ref(capability, "observe", "input"),
            properties={"resource_ref": _string(pattern=_REF_PATTERN)},
            required=("resource_ref",),
        ),
        _object_schema(
            _schema_ref(capability, "observe", "output"),
            properties=observed,
            required=observed_required,
        ),
        _object_schema(
            _schema_ref(capability, "plan", "input"),
            properties=desired,
            required=desired_required,
        ),
        _object_schema(
            _schema_ref(capability, "plan", "output"),
            properties={
                "changes": {
                    "items": _public_string(minLength=1, maxLength=512),
                    "type": "array",
                    **_PUBLIC,
                },
                "desired_state_digest": _public_string(pattern=_DIGEST_PATTERN),
                "resource_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=(
                "changes",
                "desired_state_digest",
                "resource_ref",
            ),
        ),
    )


def _instance_schemas() -> tuple[CapabilitySchemaDocument, ...]:
    states = ("absent", "running", "stopped")
    desired = {
        "artifact_digest": _string(pattern=_DIGEST_PATTERN),
        "configuration_digest": _string(pattern=_DIGEST_PATTERN),
        "desired_lifecycle_state": {"enum": list(states), "type": "string"},
        "firewall_resource_refs": _string_array(),
        "instance_type": _string(pattern=_REF_PATTERN),
        "network_resource_refs": _string_array(),
        "resource_ref": _string(pattern=_REF_PATTERN),
        "volume_resource_refs": _string_array(),
    }
    observed = {
        **_common_observation(states),
        "applied_artifact_digest": _public_string(pattern=_DIGEST_PATTERN),
        "health_state": {
            "enum": ["degraded", "healthy", "unhealthy"],
            "type": "string",
            **_PUBLIC,
        },
        "instance_type": _public_string(pattern=_REF_PATTERN),
        "ipv4_addresses": _string_array(public=True),
        "ipv6_addresses": _string_array(public=True),
    }
    return _build_family_schemas(
        "instance-lifecycle",
        desired=desired,
        desired_required=tuple(sorted(desired)),
        observed=observed,
        observed_required=tuple(sorted(observed)),
    )


def _network_schemas() -> tuple[CapabilitySchemaDocument, ...]:
    states = ("absent", "available")
    desired = {
        "cidr": _string(pattern=_CIDR_PATTERN),
        "configuration_digest": _string(pattern=_DIGEST_PATTERN),
        "desired_lifecycle_state": {"enum": list(states), "type": "string"},
        "dns_resolver_addresses": _string_array(),
        "resource_ref": _string(pattern=_REF_PATTERN),
    }
    observed = {
        **_common_observation(states),
        "cidr": _public_string(pattern=_CIDR_PATTERN),
        "dns_resolver_addresses": _string_array(public=True),
        "gateway_address": _public_string(minLength=1, maxLength=64),
    }
    return _build_family_schemas(
        "network-lifecycle",
        desired=desired,
        desired_required=tuple(sorted(desired)),
        observed=observed,
        observed_required=tuple(sorted(observed)),
    )


def _volume_schemas() -> tuple[CapabilitySchemaDocument, ...]:
    states = ("absent", "attached", "available")
    desired = {
        "attachment_instance_ref": _string(pattern=_REF_PATTERN),
        "configuration_digest": _string(pattern=_DIGEST_PATTERN),
        "desired_lifecycle_state": {"enum": list(states), "type": "string"},
        "resource_ref": _string(pattern=_REF_PATTERN),
        "size_bytes": {"minimum": 1, "type": "integer"},
        "volume_type": _string(pattern=_REF_PATTERN),
    }
    observed = {
        **_common_observation(states),
        "attached_instance_ref": _public_string(pattern=_REF_PATTERN),
        "provider_device_ref": _public_string(pattern=_REF_PATTERN),
        "size_bytes": _public_integer(minimum=1),
        "volume_type": _public_string(pattern=_REF_PATTERN),
    }
    return _build_family_schemas(
        "volume-lifecycle",
        desired=desired,
        desired_required=(
            "configuration_digest",
            "desired_lifecycle_state",
            "resource_ref",
            "size_bytes",
            "volume_type",
        ),
        observed=observed,
        observed_required=tuple(sorted(observed)),
    )


def _firewall_rule(*, public: bool) -> dict[str, object]:
    marker = _PUBLIC if public else {}
    string = _public_string if public else _string
    return {
        "additionalProperties": False,
        "properties": {
            "description": string(maxLength=240),
            "destination_ports": {
                "items": {"maximum": 65535, "minimum": 1, "type": "integer"},
                "type": "array",
                "uniqueItems": True,
                **marker,
            },
            "direction": {
                "enum": ["egress", "ingress"],
                "type": "string",
                **marker,
            },
            "protocol": {
                "enum": ["any", "icmp", "tcp", "udp"],
                "type": "string",
                **marker,
            },
            "source_cidrs": _string_array(public=public, pattern=_CIDR_PATTERN),
        },
        "required": ["destination_ports", "direction", "protocol", "source_cidrs"],
        "type": "object",
        **marker,
    }


def _firewall_schemas() -> tuple[CapabilitySchemaDocument, ...]:
    states = ("absent", "disabled", "enabled")
    desired = {
        "configuration_digest": _string(pattern=_DIGEST_PATTERN),
        "desired_lifecycle_state": {"enum": list(states), "type": "string"},
        "desired_rules_digest": _string(pattern=_DIGEST_PATTERN),
        "firewall_rules": {"items": _firewall_rule(public=False), "type": "array"},
        "resource_ref": _string(pattern=_REF_PATTERN),
    }
    observed = {
        **_common_observation(states),
        "observed_rules_digest": _public_string(pattern=_DIGEST_PATTERN),
        "rule_count": _public_integer(minimum=0),
    }
    return _build_family_schemas(
        "firewall-lifecycle",
        desired=desired,
        desired_required=tuple(sorted(desired)),
        observed=observed,
        observed_required=tuple(sorted(observed)),
    )


CAPABILITY_SCHEMAS = tuple(
    sorted(
        (
            *_firewall_schemas(),
            *_instance_schemas(),
            *_network_schemas(),
            *_volume_schemas(),
        ),
        key=lambda item: item.schema_ref,
    )
)
SCHEMAS_BY_REF = {schema.schema_ref: schema for schema in CAPABILITY_SCHEMAS}

__all__ = ["CAPABILITY_SCHEMAS", "SCHEMAS_BY_REF"]
