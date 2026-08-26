"""Canonical JSON Schema bytes for managed email operations."""

from __future__ import annotations

from collections.abc import Mapping

from dotmac_kernel import CAPABILITY_SCHEMA_DIALECT, CapabilitySchemaDocument

_PUBLIC = {"x-dotmac-data-classification": "public_non_secret"}
_MIB = 1_048_576
_HTTPS_PATTERN = r"^https://[^\s]+$"
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$"
_FQDN_PATTERN = (
    r"^(?=.{1,253}\.?$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}"
    r"[A-Za-z0-9])?\.)+[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$"
)
_LOCAL_PART_PATTERN = r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}$"
_EMAIL_PATTERN = r"^[^\s@]+@[^\s@]+\.[^\s@]+$"
_DNS_REQUIREMENT_KINDS = (
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
_DNS_RECORD_TYPES = ("A", "AAAA", "CAA", "CNAME", "MX", "PTR", "SRV", "TXT")
_RESOURCE_KINDS = (
    "alias",
    "app_password",
    "application",
    "delivery",
    "dkim",
    "domain",
    "mailbox",
    "quota",
)


def _string(**keywords: object) -> dict[str, object]:
    return {"type": "string", **keywords}


def _public_string(**keywords: object) -> dict[str, object]:
    return _string(**_PUBLIC, **keywords)


def _public_boolean() -> dict[str, object]:
    return {"type": "boolean", **_PUBLIC}


def _public_integer(**keywords: object) -> dict[str, object]:
    return {"type": "integer", **_PUBLIC, **keywords}


def _public_dns_requirement() -> dict[str, object]:
    return {
        "additionalProperties": False,
        "properties": {
            "owner_name": _public_string(pattern=_FQDN_PATTERN),
            "priority": _public_integer(minimum=0),
            "record_type": {
                "enum": list(_DNS_RECORD_TYPES),
                "type": "string",
                **_PUBLIC,
            },
            "required": _public_boolean(),
            "requirement_kind": {
                "enum": list(_DNS_REQUIREMENT_KINDS),
                "type": "string",
                **_PUBLIC,
            },
            "ttl": _public_integer(minimum=30),
            "values": {
                "items": _public_string(minLength=1, maxLength=4096),
                "minItems": 1,
                "type": "array",
                "uniqueItems": True,
                **_PUBLIC,
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
        **_PUBLIC,
    }


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


def _schema_ref(capability: str, operation: str, direction: str) -> str:
    return f"schema:dotmac-managed-email/{capability}/{operation}/{direction}@v1"


def _requires(kind: str, *fields: str) -> dict[str, object]:
    return {
        "if": {
            "properties": {"resource_kind": {"const": kind}},
            "required": ["resource_kind"],
        },
        "then": {"required": list(fields)},
    }


def _requires_when_present(kind: str, *fields: str) -> dict[str, object]:
    return {
        "if": {
            "properties": {
                "lifecycle_state": {"enum": ["disabled", "enabled"]},
                "resource_kind": {"const": kind},
            },
            "required": ["lifecycle_state", "resource_kind"],
        },
        "then": {"required": list(fields)},
    }


def _email_desired_properties() -> dict[str, object]:
    return {
        "alias_local_part": _string(pattern=_LOCAL_PART_PATTERN),
        "backup_mx_enabled": {"const": False, "type": "boolean"},
        "alias_targets": {
            "items": _string(pattern=_EMAIL_PATTERN),
            "minItems": 1,
            "type": "array",
            "uniqueItems": True,
        },
        "application_ref": _string(pattern=_REF_PATTERN),
        "delivery_enabled": {"type": "boolean"},
        "dav_access_enabled": {"type": "boolean"},
        "domain_alias_limit": {"minimum": 0, "type": "integer"},
        "domain_mailbox_limit": {"minimum": 1, "type": "integer"},
        "domain_quota_bytes": {
            "minimum": _MIB,
            "multipleOf": _MIB,
            "type": "integer",
        },
        "desired_lifecycle_state": {
            "enum": ["absent", "disabled", "enabled", "present"],
            "type": "string",
        },
        "domain_name": _string(pattern=_FQDN_PATTERN),
        "global_address_list_enabled": {"type": "boolean"},
        "eas_access_enabled": {"type": "boolean"},
        "imap_access_enabled": {"type": "boolean"},
        "mailbox_local_part": _string(pattern=_LOCAL_PART_PATTERN),
        "mailbox_quota_default_bytes": {
            "minimum": _MIB,
            "multipleOf": _MIB,
            "type": "integer",
        },
        "mailbox_quota_max_bytes": {
            "minimum": _MIB,
            "multipleOf": _MIB,
            "type": "integer",
        },
        "pop3_access_enabled": {"type": "boolean"},
        "oidc_account_creation_enabled": {"const": False, "type": "boolean"},
        "oidc_client_id": _string(minLength=1, maxLength=255),
        "oidc_email_linking_enabled": {"const": False, "type": "boolean"},
        "oidc_enabled": {"const": True, "type": "boolean"},
        "oidc_id_token_signing_algorithm": {"const": "RS256", "type": "string"},
        "oidc_issuer_url": _string(format="uri", pattern=_HTTPS_PATTERN),
        "oidc_logout_uri": _string(format="uri", pattern=_HTTPS_PATTERN),
        "oidc_mailpassword_flow_enabled": {"const": False, "type": "boolean"},
        "oidc_pkce_method": {"const": "S256", "type": "string"},
        "oidc_redirect_uri": _string(format="uri", pattern=_HTTPS_PATTERN),
        "oidc_require_aud_azp_validation": {"const": True, "type": "boolean"},
        "oidc_subject_binding": {
            "const": "immutable_issuer_subject",
            "type": "string",
        },
        "quota_bytes": {
            "minimum": _MIB,
            "multipleOf": _MIB,
            "type": "integer",
        },
        "relay_all_recipients_enabled": {"const": False, "type": "boolean"},
        "relay_unknown_recipients_enabled": {"const": False, "type": "boolean"},
        "sieve_access_enabled": {"type": "boolean"},
        "smtp_access_enabled": {"type": "boolean"},
        "webmail_access_enabled": {"type": "boolean"},
        "resource_kind": {"enum": list(_RESOURCE_KINDS), "type": "string"},
        "resource_ref": _string(pattern=_REF_PATTERN),
    }


def _email_conditions() -> tuple[dict[str, object], ...]:
    return (
        _requires("alias", "alias_local_part", "alias_targets", "domain_name"),
        _requires(
            "app_password",
            "domain_name",
            "mailbox_local_part",
        ),
        _requires(
            "application",
            "application_ref",
            "oidc_account_creation_enabled",
            "oidc_client_id",
            "oidc_email_linking_enabled",
            "oidc_enabled",
            "oidc_id_token_signing_algorithm",
            "oidc_issuer_url",
            "oidc_logout_uri",
            "oidc_mailpassword_flow_enabled",
            "oidc_pkce_method",
            "oidc_redirect_uri",
            "oidc_require_aud_azp_validation",
            "oidc_subject_binding",
        ),
        _requires("delivery", "delivery_enabled", "domain_name", "mailbox_local_part"),
        _requires(
            "dkim",
            "domain_name",
        ),
        _requires(
            "domain",
            "backup_mx_enabled",
            "domain_alias_limit",
            "domain_mailbox_limit",
            "domain_name",
            "domain_quota_bytes",
            "global_address_list_enabled",
            "mailbox_quota_default_bytes",
            "mailbox_quota_max_bytes",
            "relay_all_recipients_enabled",
            "relay_unknown_recipients_enabled",
        ),
        _requires(
            "mailbox",
            "dav_access_enabled",
            "delivery_enabled",
            "domain_name",
            "eas_access_enabled",
            "imap_access_enabled",
            "mailbox_local_part",
            "pop3_access_enabled",
            "quota_bytes",
            "sieve_access_enabled",
            "smtp_access_enabled",
            "webmail_access_enabled",
        ),
        _requires("quota", "domain_name", "mailbox_local_part", "quota_bytes"),
    )


def _email_observation_properties() -> dict[str, object]:
    return {
        "alias_ref": _public_string(pattern=_REF_PATTERN),
        "app_password_configured": _public_boolean(),
        "application_ref": _public_string(pattern=_REF_PATTERN),
        "delivery_enabled": _public_boolean(),
        "dns_requirements": {
            "items": _public_dns_requirement(),
            "minItems": 1,
            "type": "array",
            **_PUBLIC,
        },
        "dkim_public_key_digest": _public_string(pattern=_DIGEST_PATTERN),
        "dkim_record_name": _public_string(pattern=_FQDN_PATTERN),
        "dkim_record_value": _public_string(minLength=1, maxLength=4096),
        "dkim_selector": _public_string(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$"),
        "domain_name": _public_string(pattern=_FQDN_PATTERN),
        "healthy": _public_boolean(),
        "lifecycle_state": {
            "enum": ["absent", "disabled", "enabled"],
            "type": "string",
            **_PUBLIC,
        },
        "mail_hostname": _public_string(pattern=_FQDN_PATTERN),
        "mailbox_ref": _public_string(pattern=_REF_PATTERN),
        "oidc_account_creation_enabled": {
            "const": False,
            "type": "boolean",
            **_PUBLIC,
        },
        "oidc_client_id": _public_string(minLength=1, maxLength=255),
        "oidc_email_linking_enabled": {
            "const": False,
            "type": "boolean",
            **_PUBLIC,
        },
        "oidc_enabled": {"const": True, "type": "boolean", **_PUBLIC},
        "oidc_id_token_signing_algorithm": {
            "const": "RS256",
            "type": "string",
            **_PUBLIC,
        },
        "oidc_issuer_url": _public_string(format="uri", pattern=_HTTPS_PATTERN),
        "oidc_logout_uri": _public_string(format="uri", pattern=_HTTPS_PATTERN),
        "oidc_mailpassword_flow_enabled": {
            "const": False,
            "type": "boolean",
            **_PUBLIC,
        },
        "oidc_pkce_method": {"const": "S256", "type": "string", **_PUBLIC},
        "oidc_redirect_uri": _public_string(format="uri", pattern=_HTTPS_PATTERN),
        "oidc_require_aud_azp_validation": {
            "const": True,
            "type": "boolean",
            **_PUBLIC,
        },
        "oidc_subject_binding": {
            "const": "immutable_issuer_subject",
            "type": "string",
            **_PUBLIC,
        },
        "observed_configuration_digest": _public_string(pattern=_DIGEST_PATTERN),
        "quota_bytes": _public_integer(minimum=0),
        "resource_kind": {
            "enum": list(_RESOURCE_KINDS),
            "type": "string",
            **_PUBLIC,
        },
        "resource_ref": _public_string(pattern=_REF_PATTERN),
        "smtp_delivery_observed": _public_boolean(),
    }


def _email_observation_conditions() -> tuple[dict[str, object], ...]:
    oidc_fields = (
        "oidc_account_creation_enabled",
        "oidc_client_id",
        "oidc_email_linking_enabled",
        "oidc_enabled",
        "oidc_id_token_signing_algorithm",
        "oidc_issuer_url",
        "oidc_logout_uri",
        "oidc_mailpassword_flow_enabled",
        "oidc_pkce_method",
        "oidc_redirect_uri",
        "oidc_require_aud_azp_validation",
        "oidc_subject_binding",
    )
    return (
        _requires("alias", "alias_ref", "domain_name"),
        _requires(
            "app_password",
            "app_password_configured",
            "domain_name",
            "mailbox_ref",
        ),
        _requires("application", *oidc_fields),
        _requires(
            "delivery",
            "delivery_enabled",
            "domain_name",
            "mailbox_ref",
            "smtp_delivery_observed",
        ),
        _requires("dkim", "domain_name", "mail_hostname"),
        _requires_when_present(
            "dkim",
            "dkim_public_key_digest",
            "dkim_record_name",
            "dkim_record_value",
            "dkim_selector",
            "dns_requirements",
        ),
        _requires(
            "domain",
            "dns_requirements",
            "domain_name",
            "mail_hostname",
        ),
        _requires(
            "mailbox",
            "delivery_enabled",
            "domain_name",
            "mailbox_ref",
            "quota_bytes",
        ),
        _requires("quota", "domain_name", "mailbox_ref", "quota_bytes"),
    )


def _build_email_schemas() -> tuple[CapabilitySchemaDocument, ...]:
    capability = "email-lifecycle"
    desired = _email_desired_properties()
    observed = _email_observation_properties()
    base_required = (
        "application_ref",
        "desired_lifecycle_state",
        "resource_kind",
        "resource_ref",
    )
    observed_required = (
        "application_ref",
        "healthy",
        "lifecycle_state",
        "observed_configuration_digest",
        "resource_kind",
        "resource_ref",
    )
    return (
        _object_schema(
            _schema_ref(capability, "apply", "input"),
            properties=desired,
            required=base_required,
            all_of=_email_conditions(),
        ),
        _object_schema(
            _schema_ref(capability, "apply", "output"),
            properties=observed,
            required=observed_required,
            all_of=_email_observation_conditions(),
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "input"),
            properties={
                "application_ref": _string(pattern=_REF_PATTERN),
                "resource_kind": {"enum": list(_RESOURCE_KINDS), "type": "string"},
                "resource_ref": _string(pattern=_REF_PATTERN),
            },
            required=(
                "application_ref",
                "resource_kind",
                "resource_ref",
            ),
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "output"),
            properties={
                "cancelled": _public_boolean(),
                "resource_kind": {
                    "enum": list(_RESOURCE_KINDS),
                    "type": "string",
                    **_PUBLIC,
                },
                "resource_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=("cancelled", "resource_kind", "resource_ref"),
        ),
        _object_schema(
            _schema_ref(capability, "observe", "input"),
            properties={
                "application_ref": _string(pattern=_REF_PATTERN),
                "resource_kind": {"enum": list(_RESOURCE_KINDS), "type": "string"},
                "resource_ref": _string(pattern=_REF_PATTERN),
            },
            required=(
                "application_ref",
                "resource_kind",
                "resource_ref",
            ),
        ),
        _object_schema(
            _schema_ref(capability, "observe", "output"),
            properties=observed,
            required=observed_required,
            all_of=_email_observation_conditions(),
        ),
        _object_schema(
            _schema_ref(capability, "plan", "input"),
            properties=desired,
            required=base_required,
            all_of=_email_conditions(),
        ),
        _object_schema(
            _schema_ref(capability, "plan", "output"),
            properties={
                "changes": {
                    "items": _public_string(minLength=1, maxLength=512),
                    "type": "array",
                    **_PUBLIC,
                },
                "resource_kind": {
                    "enum": list(_RESOURCE_KINDS),
                    "type": "string",
                    **_PUBLIC,
                },
                "resource_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=("changes", "resource_kind", "resource_ref"),
        ),
    )


CAPABILITY_SCHEMAS = tuple(
    sorted(_build_email_schemas(), key=lambda item: item.schema_ref)
)
SCHEMAS_BY_REF = {schema.schema_ref: schema for schema in CAPABILITY_SCHEMAS}

__all__ = ["CAPABILITY_SCHEMAS", "SCHEMAS_BY_REF"]
