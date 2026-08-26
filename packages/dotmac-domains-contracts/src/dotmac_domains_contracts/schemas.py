"""Canonical JSON Schema bytes for authoritative DNS and TLS operations."""

from __future__ import annotations

from collections.abc import Mapping

from dotmac_kernel import CAPABILITY_SCHEMA_DIALECT, CapabilitySchemaDocument

_PUBLIC = {"x-dotmac-data-classification": "public_non_secret"}
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$"
_FQDN_PATTERN = (
    r"^(?=.{1,253}\.?$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}"
    r"[A-Za-z0-9])?\.)+[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$"
)
_RESOURCE_KINDS = ("observation", "recordset", "zone")
_REQUIREMENT_KINDS = (
    "autoconfig",
    "autodiscover",
    "dkim",
    "dmarc",
    "mta_sts",
    "mx",
    "ptr",
    "spf",
    "tls_rpt",
)
_RECORD_TYPES = ("A", "AAAA", "CAA", "CNAME", "MX", "PTR", "SRV", "TXT")


def _string(**keywords: object) -> dict[str, object]:
    return {"type": "string", **keywords}


def _public_string(**keywords: object) -> dict[str, object]:
    return _string(**_PUBLIC, **keywords)


def _public_boolean() -> dict[str, object]:
    return {"type": "boolean", **_PUBLIC}


def _object_schema(
    schema_ref: str,
    *,
    properties: Mapping[str, object],
    required: tuple[str, ...],
    all_of: tuple[dict[str, object], ...] = (),
) -> CapabilitySchemaDocument:
    document: dict[str, object] = {
        "$id": schema_ref,
        "$schema": CAPABILITY_SCHEMA_DIALECT,
        "additionalProperties": False,
        "properties": dict(properties),
        "required": list(required),
        "type": "object",
    }
    if all_of:
        document["allOf"] = list(all_of)
    return CapabilitySchemaDocument.from_mapping(document)


def _schema_ref(operation: str, direction: str) -> str:
    return f"schema:dotmac-domains/dns-authoritative/{operation}/{direction}@v1"


def _dns_requirement(*, public: bool) -> dict[str, object]:
    public_marker = _PUBLIC if public else {}
    string = _public_string if public else _string
    return {
        "additionalProperties": False,
        "properties": {
            "owner_name": string(pattern=_FQDN_PATTERN),
            "priority": {"minimum": 0, "type": "integer", **public_marker},
            "record_type": {
                "enum": list(_RECORD_TYPES),
                "type": "string",
                **public_marker,
            },
            "required": {"type": "boolean", **public_marker},
            "requirement_kind": {
                "enum": list(_REQUIREMENT_KINDS),
                "type": "string",
                **public_marker,
            },
            "ttl": {"minimum": 30, "type": "integer", **public_marker},
            "values": {
                "items": string(minLength=1, maxLength=4096),
                "minItems": 1,
                "type": "array",
                "uniqueItems": True,
                **public_marker,
            },
        },
        "required": [
            "owner_name",
            "record_type",
            "required",
            "requirement_kind",
            "ttl",
            "values",
        ],
        "type": "object",
        **public_marker,
    }


def _tls_requirement() -> dict[str, object]:
    return {
        "additionalProperties": False,
        "properties": {
            "certificate_required": {"type": "boolean"},
            "hostname": _string(pattern=_FQDN_PATTERN),
            "https_required": {"type": "boolean"},
            "mta_sts_policy_required": {"type": "boolean"},
            "redirect_http_to_https": {"type": "boolean"},
            "tls_reporting_required": {"type": "boolean"},
        },
        "required": [
            "certificate_required",
            "hostname",
            "https_required",
            "mta_sts_policy_required",
            "redirect_http_to_https",
            "tls_reporting_required",
        ],
        "type": "object",
    }


def _tls_evidence() -> dict[str, object]:
    return {
        "additionalProperties": False,
        "properties": {
            "certificate_expires_at": _public_string(format="date-time"),
            "certificate_hostname_valid": _public_boolean(),
            "certificate_valid": _public_boolean(),
            "hostname": _public_string(pattern=_FQDN_PATTERN),
            "http_redirects_to_https": _public_boolean(),
            "https_reachable": _public_boolean(),
            "mta_sts_policy_valid": _public_boolean(),
            "tls_rpt_uri_valid": _public_boolean(),
        },
        "required": [
            "certificate_expires_at",
            "certificate_hostname_valid",
            "certificate_valid",
            "hostname",
            "http_redirects_to_https",
            "https_reachable",
            "mta_sts_policy_valid",
            "tls_rpt_uri_valid",
        ],
        "type": "object",
        **_PUBLIC,
    }


def _desired_properties() -> dict[str, object]:
    return {
        "dns_requirements": {
            "items": _dns_requirement(public=False),
            "type": "array",
        },
        "resource_kind": {"enum": list(_RESOURCE_KINDS), "type": "string"},
        "resource_ref": _string(pattern=_REF_PATTERN),
        "tls_requirements": {"items": _tls_requirement(), "type": "array"},
        "zone_name": _string(pattern=_FQDN_PATTERN),
    }


def _desired_conditions() -> tuple[dict[str, object], ...]:
    return (
        {
            "if": {
                "properties": {"resource_kind": {"const": "recordset"}},
                "required": ["resource_kind"],
            },
            "then": {
                "properties": {"dns_requirements": {"minItems": 1}},
                "required": ["dns_requirements"],
            },
        },
    )


def _observed_properties() -> dict[str, object]:
    return {
        "assigned_nameservers": {
            "items": _public_string(pattern=_FQDN_PATTERN),
            "type": "array",
            "uniqueItems": True,
            **_PUBLIC,
        },
        "observed_configuration_digest": _public_string(pattern=_DIGEST_PATTERN),
        "observed_dns_requirements": {
            "items": _dns_requirement(public=True),
            "type": "array",
            **_PUBLIC,
        },
        "outstanding_resource_refs": {
            "items": _public_string(pattern=_REF_PATTERN),
            "type": "array",
            "uniqueItems": True,
            **_PUBLIC,
        },
        "partial": _public_boolean(),
        "provider_zone_ref": _public_string(pattern=_REF_PATTERN),
        "resource_kind": {
            "enum": list(_RESOURCE_KINDS),
            "type": "string",
            **_PUBLIC,
        },
        "resource_ref": _public_string(pattern=_REF_PATTERN),
        "tls_evidence": {"items": _tls_evidence(), "type": "array", **_PUBLIC},
        "zone_name": _public_string(pattern=_FQDN_PATTERN),
    }


def _build_schemas() -> tuple[CapabilitySchemaDocument, ...]:
    desired = _desired_properties()
    observed = _observed_properties()
    base_required = (
        "resource_kind",
        "resource_ref",
        "zone_name",
    )
    observed_required = tuple(sorted(observed))
    return (
        _object_schema(
            _schema_ref("apply", "input"),
            properties=desired,
            required=base_required,
            all_of=_desired_conditions(),
        ),
        _object_schema(
            _schema_ref("apply", "output"),
            properties=observed,
            required=observed_required,
        ),
        _object_schema(
            _schema_ref("cancel", "input"),
            properties={
                "resource_kind": {"enum": list(_RESOURCE_KINDS), "type": "string"},
                "resource_ref": _string(pattern=_REF_PATTERN),
                "zone_name": _string(pattern=_FQDN_PATTERN),
            },
            required=(
                "resource_kind",
                "resource_ref",
                "zone_name",
            ),
        ),
        _object_schema(
            _schema_ref("cancel", "output"),
            properties={
                "cancelled": _public_boolean(),
                "resource_kind": {
                    "enum": list(_RESOURCE_KINDS),
                    "type": "string",
                    **_PUBLIC,
                },
                "resource_ref": _public_string(pattern=_REF_PATTERN),
                "zone_name": _public_string(pattern=_FQDN_PATTERN),
            },
            required=("cancelled", "resource_kind", "resource_ref", "zone_name"),
        ),
        _object_schema(
            _schema_ref("observe", "input"),
            properties={
                "resource_kind": {"enum": list(_RESOURCE_KINDS), "type": "string"},
                "resource_ref": _string(pattern=_REF_PATTERN),
                "zone_name": _string(pattern=_FQDN_PATTERN),
            },
            required=(
                "resource_kind",
                "resource_ref",
                "zone_name",
            ),
        ),
        _object_schema(
            _schema_ref("observe", "output"),
            properties=observed,
            required=observed_required,
        ),
        _object_schema(
            _schema_ref("plan", "input"),
            properties=desired,
            required=base_required,
            all_of=_desired_conditions(),
        ),
        _object_schema(
            _schema_ref("plan", "output"),
            properties={
                "changes": {
                    "items": _public_string(minLength=1, maxLength=512),
                    "type": "array",
                    **_PUBLIC,
                },
                "desired_state_digest": _public_string(pattern=_DIGEST_PATTERN),
                "resource_kind": {
                    "enum": list(_RESOURCE_KINDS),
                    "type": "string",
                    **_PUBLIC,
                },
                "resource_ref": _public_string(pattern=_REF_PATTERN),
                "zone_name": _public_string(pattern=_FQDN_PATTERN),
            },
            required=(
                "changes",
                "desired_state_digest",
                "resource_kind",
                "resource_ref",
                "zone_name",
            ),
        ),
    )


CAPABILITY_SCHEMAS = tuple(sorted(_build_schemas(), key=lambda item: item.schema_ref))
SCHEMAS_BY_REF = {schema.schema_ref: schema for schema in CAPABILITY_SCHEMAS}

__all__ = ["CAPABILITY_SCHEMAS", "SCHEMAS_BY_REF"]
