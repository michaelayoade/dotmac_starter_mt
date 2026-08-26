"""Canonical JSON Schema bytes for managed collaboration operations."""

from __future__ import annotations

from collections.abc import Mapping

from dotmac_kernel import CAPABILITY_SCHEMA_DIALECT, CapabilitySchemaDocument

_PUBLIC = {"x-dotmac-data-classification": "public_non_secret"}
_HTTPS_PATTERN = r"^https://[^\s]+$"
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$"
_LOGICAL_PATH_PATTERN = (
    r"^/(?:[A-Za-z0-9][A-Za-z0-9._-]{0,127}/)*" r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
_APPLICATION_ACTIONS = (
    "backup",
    "decommission",
    "ensure_active",
    "restore",
    "resume",
    "suspend",
    "upgrade",
)
_ACCOUNT_RESOURCE_KINDS = ("group", "group_membership", "quota", "user")


def _string(**keywords: object) -> dict[str, object]:
    return {"type": "string", **keywords}


def _public_string(**keywords: object) -> dict[str, object]:
    return _string(**_PUBLIC, **keywords)


def _public_boolean(**keywords: object) -> dict[str, object]:
    return {"type": "boolean", **_PUBLIC, **keywords}


def _public_integer(**keywords: object) -> dict[str, object]:
    return {"type": "integer", **_PUBLIC, **keywords}


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
    return (
        "schema:dotmac-managed-collaboration/"
        f"{capability}/{operation}/{direction}@v1"
    )


def _requires(discriminator: str, value: str, *fields: str) -> dict[str, object]:
    return {
        "if": {
            "properties": {discriminator: {"const": value}},
            "required": [discriminator],
        },
        "then": {"required": list(fields)},
    }


def _changes() -> dict[str, object]:
    return {
        "items": _public_string(minLength=1, maxLength=512),
        "type": "array",
        **_PUBLIC,
    }


def _application_desired_properties() -> dict[str, object]:
    return {
        "action": {"enum": list(_APPLICATION_ACTIONS), "type": "string"},
        "application_ref": _string(pattern=_REF_PATTERN),
        "artifact_digest": _string(pattern=_DIGEST_PATTERN),
        "backup_ref": _string(pattern=_REF_PATTERN),
        "backup_version_ref": _string(pattern=_REF_PATTERN),
        "configuration_digest": _string(pattern=_DIGEST_PATTERN),
        "reason_ref": _string(pattern=_REF_PATTERN),
        "target_version": _string(minLength=1, maxLength=120),
    }


def _application_conditions() -> tuple[dict[str, object], ...]:
    return (
        _requires("action", "backup", "backup_ref"),
        _requires("action", "decommission", "reason_ref"),
        _requires(
            "action",
            "ensure_active",
            "artifact_digest",
            "target_version",
        ),
        _requires(
            "action",
            "restore",
            "backup_ref",
            "backup_version_ref",
        ),
        _requires("action", "resume", "reason_ref"),
        _requires("action", "suspend", "reason_ref"),
        _requires("action", "upgrade", "artifact_digest", "target_version"),
    )


def _application_observation_properties() -> dict[str, object]:
    return {
        "action": {
            "enum": list(_APPLICATION_ACTIONS),
            "type": "string",
            **_PUBLIC,
        },
        "application_ref": _public_string(pattern=_REF_PATTERN),
        "backup_digest": _public_string(pattern=_DIGEST_PATTERN),
        "backup_object_ref": _public_string(pattern=_REF_PATTERN),
        "backup_version_ref": _public_string(pattern=_REF_PATTERN),
        "completed_at": _public_string(format="date-time"),
        "health_validated": _public_boolean(),
        "installed_version": _public_string(minLength=1, maxLength=120),
        "lifecycle_state": {
            "enum": ["absent", "active", "decommissioned", "suspended"],
            "type": "string",
            **_PUBLIC,
        },
        "observed_configuration_digest": _public_string(pattern=_DIGEST_PATTERN),
        "restore_validated": _public_boolean(),
        "rollback_available": _public_boolean(),
        "suspension_reason_ref": _public_string(pattern=_REF_PATTERN),
        "upgrade_validated": _public_boolean(),
    }


def _application_observation_conditions() -> tuple[dict[str, object], ...]:
    return (
        _requires(
            "action",
            "backup",
            "backup_digest",
            "backup_object_ref",
            "backup_version_ref",
        ),
        _requires("action", "ensure_active", "installed_version"),
        _requires("action", "restore", "restore_validated"),
        _requires("action", "resume", "installed_version"),
        _requires("action", "suspend", "suspension_reason_ref"),
        _requires(
            "action",
            "upgrade",
            "installed_version",
            "rollback_available",
            "upgrade_validated",
        ),
    )


def _build_application_schemas() -> tuple[CapabilitySchemaDocument, ...]:
    capability = "application-lifecycle"
    desired = _application_desired_properties()
    observed = _application_observation_properties()
    required = (
        "action",
        "application_ref",
        "configuration_digest",
    )
    observed_required = (
        "action",
        "application_ref",
        "completed_at",
        "health_validated",
        "lifecycle_state",
        "observed_configuration_digest",
    )
    conditions = _application_conditions()
    observed_conditions = _application_observation_conditions()
    locator = {
        "action": {"enum": list(_APPLICATION_ACTIONS), "type": "string"},
        "application_ref": _string(pattern=_REF_PATTERN),
    }
    locator_required = tuple(sorted(locator))
    return (
        _object_schema(
            _schema_ref(capability, "apply", "input"),
            properties=desired,
            required=required,
            all_of=conditions,
        ),
        _object_schema(
            _schema_ref(capability, "apply", "output"),
            properties=observed,
            required=observed_required,
            all_of=observed_conditions,
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "input"),
            properties=locator,
            required=locator_required,
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "output"),
            properties={
                "application_ref": _public_string(pattern=_REF_PATTERN),
                "cancelled": _public_boolean(),
            },
            required=("application_ref", "cancelled"),
        ),
        _object_schema(
            _schema_ref(capability, "observe", "input"),
            properties=locator,
            required=locator_required,
        ),
        _object_schema(
            _schema_ref(capability, "observe", "output"),
            properties=observed,
            required=observed_required,
            all_of=observed_conditions,
        ),
        _object_schema(
            _schema_ref(capability, "plan", "input"),
            properties=desired,
            required=required,
            all_of=conditions,
        ),
        _object_schema(
            _schema_ref(capability, "plan", "output"),
            properties={
                "action": {
                    "enum": list(_APPLICATION_ACTIONS),
                    "type": "string",
                    **_PUBLIC,
                },
                "application_ref": _public_string(pattern=_REF_PATTERN),
                "changes": _changes(),
            },
            required=("action", "application_ref", "changes"),
        ),
    )


def _oidc_desired_properties() -> dict[str, object]:
    return {
        "account_creation_mode": {"const": "preprovisioned_only", "type": "string"},
        "application_ref": _string(pattern=_REF_PATTERN),
        "audience": _string(minLength=1, maxLength=255),
        "backchannel_logout_enabled": {"const": True, "type": "boolean"},
        "client_id": _string(minLength=1, maxLength=255),
        "direct_login_mode": {"const": "break_glass", "type": "string"},
        "email_linking_enabled": {"const": False, "type": "boolean"},
        "identity_binding_key": {"const": "issuer_subject", "type": "string"},
        "id_token_signing_algorithm": {"const": "RS256", "type": "string"},
        "issuer_url": _string(format="uri", pattern=_HTTPS_PATTERN),
        "oidc_configuration_ref": _string(pattern=_REF_PATTERN),
        "pkce_method": {"const": "S256", "type": "string"},
        "redirect_uris": {
            "items": _string(format="uri", pattern=_HTTPS_PATTERN),
            "minItems": 1,
            "type": "array",
            "uniqueItems": True,
        },
        "require_aud_azp_validation": {"const": True, "type": "boolean"},
        "session_provenance_required": {"const": True, "type": "boolean"},
        "session_revocation_required": {"const": True, "type": "boolean"},
        "subject_claim": {"const": "sub", "type": "string"},
        "subject_mapping_mode": {"const": "immutable", "type": "string"},
    }


def _oidc_observation_properties() -> dict[str, object]:
    return {
        "account_creation_mode": {
            "const": "preprovisioned_only",
            "type": "string",
            **_PUBLIC,
        },
        "application_ref": _public_string(pattern=_REF_PATTERN),
        "audience": _public_string(minLength=1, maxLength=255),
        "backchannel_logout_enabled": _public_boolean(const=True),
        "client_id": _public_string(minLength=1, maxLength=255),
        "client_secret_configured": _public_boolean(),
        "direct_login_mode": {
            "const": "break_glass",
            "type": "string",
            **_PUBLIC,
        },
        "email_linking_enabled": _public_boolean(const=False),
        "identity_binding_key": {
            "const": "issuer_subject",
            "type": "string",
            **_PUBLIC,
        },
        "id_token_signing_algorithm": {
            "const": "RS256",
            "type": "string",
            **_PUBLIC,
        },
        "issuer_url": _public_string(format="uri", pattern=_HTTPS_PATTERN),
        "observed_configuration_digest": _public_string(pattern=_DIGEST_PATTERN),
        "oidc_configuration_ref": _public_string(pattern=_REF_PATTERN),
        "pkce_method": {"const": "S256", "type": "string", **_PUBLIC},
        "redirect_uris": {
            "items": _public_string(format="uri", pattern=_HTTPS_PATTERN),
            "minItems": 1,
            "type": "array",
            "uniqueItems": True,
            **_PUBLIC,
        },
        "require_aud_azp_validation": _public_boolean(const=True),
        "session_provenance_required": _public_boolean(const=True),
        "session_revocation_required": _public_boolean(const=True),
        "subject_claim": {"const": "sub", "type": "string", **_PUBLIC},
        "subject_mapping_mode": {
            "const": "immutable",
            "type": "string",
            **_PUBLIC,
        },
    }


def _build_oidc_schemas() -> tuple[CapabilitySchemaDocument, ...]:
    capability = "user-oidc-configuration-lifecycle"
    desired = _oidc_desired_properties()
    observed = _oidc_observation_properties()
    required = tuple(sorted(desired))
    observed_required = tuple(sorted(observed))
    locator = {
        "application_ref": _string(pattern=_REF_PATTERN),
        "oidc_configuration_ref": _string(pattern=_REF_PATTERN),
    }
    locator_required = tuple(sorted(locator))
    return (
        _object_schema(
            _schema_ref(capability, "apply", "input"),
            properties=desired,
            required=required,
        ),
        _object_schema(
            _schema_ref(capability, "apply", "output"),
            properties=observed,
            required=observed_required,
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "input"),
            properties=locator,
            required=locator_required,
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "output"),
            properties={
                "cancelled": _public_boolean(),
                "oidc_configuration_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=("cancelled", "oidc_configuration_ref"),
        ),
        _object_schema(
            _schema_ref(capability, "observe", "input"),
            properties=locator,
            required=locator_required,
        ),
        _object_schema(
            _schema_ref(capability, "observe", "output"),
            properties=observed,
            required=observed_required,
        ),
        _object_schema(
            _schema_ref(capability, "plan", "input"),
            properties=desired,
            required=required,
        ),
        _object_schema(
            _schema_ref(capability, "plan", "output"),
            properties={
                "changes": _changes(),
                "oidc_configuration_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=("changes", "oidc_configuration_ref"),
        ),
    )


def _account_desired_properties() -> dict[str, object]:
    return {
        "application_ref": _string(pattern=_REF_PATTERN),
        "desired_state": {
            "enum": ["absent", "disabled", "present"],
            "type": "string",
        },
        "display_name": _string(minLength=1, maxLength=255),
        "group_ref": _string(pattern=_REF_PATTERN),
        "identity_issuer": _string(format="uri", pattern=_HTTPS_PATTERN),
        "identity_subject": _string(minLength=1, maxLength=255),
        "quota_bytes": {"minimum": 0, "type": "integer"},
        "resource_kind": {"enum": list(_ACCOUNT_RESOURCE_KINDS), "type": "string"},
        "resource_ref": _string(pattern=_REF_PATTERN),
        "user_ref": _string(pattern=_REF_PATTERN),
    }


def _account_conditions() -> tuple[dict[str, object], ...]:
    return (
        _requires("resource_kind", "group", "group_ref"),
        _requires("resource_kind", "group_membership", "group_ref", "user_ref"),
        _requires("resource_kind", "quota", "quota_bytes", "user_ref"),
        _requires(
            "resource_kind",
            "user",
            "identity_issuer",
            "identity_subject",
            "user_ref",
        ),
    )


def _account_observation_properties() -> dict[str, object]:
    return {
        "application_ref": _public_string(pattern=_REF_PATTERN),
        "display_name": _public_string(minLength=1, maxLength=255),
        "group_ref": _public_string(pattern=_REF_PATTERN),
        "identity_issuer": _public_string(format="uri", pattern=_HTTPS_PATTERN),
        "identity_subject": _public_string(minLength=1, maxLength=255),
        "lifecycle_state": {
            "enum": ["absent", "disabled", "present"],
            "type": "string",
            **_PUBLIC,
        },
        "membership_present": _public_boolean(),
        "observed_configuration_digest": _public_string(pattern=_DIGEST_PATTERN),
        "quota_bytes": _public_integer(minimum=0),
        "resource_kind": {
            "enum": list(_ACCOUNT_RESOURCE_KINDS),
            "type": "string",
            **_PUBLIC,
        },
        "resource_ref": _public_string(pattern=_REF_PATTERN),
        "user_ref": _public_string(pattern=_REF_PATTERN),
    }


def _account_observation_conditions() -> tuple[dict[str, object], ...]:
    return (
        _requires("resource_kind", "group", "group_ref"),
        _requires(
            "resource_kind",
            "group_membership",
            "group_ref",
            "membership_present",
            "user_ref",
        ),
        _requires("resource_kind", "quota", "quota_bytes", "user_ref"),
        _requires(
            "resource_kind",
            "user",
            "identity_issuer",
            "identity_subject",
            "user_ref",
        ),
    )


def _build_account_schemas() -> tuple[CapabilitySchemaDocument, ...]:
    capability = "user-group-quota-lifecycle"
    desired = _account_desired_properties()
    observed = _account_observation_properties()
    required = (
        "application_ref",
        "desired_state",
        "resource_kind",
        "resource_ref",
    )
    observed_required = (
        "application_ref",
        "lifecycle_state",
        "observed_configuration_digest",
        "resource_kind",
        "resource_ref",
    )
    conditions = _account_conditions()
    observed_conditions = _account_observation_conditions()
    locator = {
        "application_ref": _string(pattern=_REF_PATTERN),
        "resource_kind": {"enum": list(_ACCOUNT_RESOURCE_KINDS), "type": "string"},
        "resource_ref": _string(pattern=_REF_PATTERN),
    }
    locator_required = tuple(sorted(locator))
    public_locator = {
        "resource_kind": {
            "enum": list(_ACCOUNT_RESOURCE_KINDS),
            "type": "string",
            **_PUBLIC,
        },
        "resource_ref": _public_string(pattern=_REF_PATTERN),
    }
    return (
        _object_schema(
            _schema_ref(capability, "apply", "input"),
            properties=desired,
            required=required,
            all_of=conditions,
        ),
        _object_schema(
            _schema_ref(capability, "apply", "output"),
            properties=observed,
            required=observed_required,
            all_of=observed_conditions,
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "input"),
            properties=locator,
            required=locator_required,
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "output"),
            properties={"cancelled": _public_boolean(), **public_locator},
            required=("cancelled", "resource_kind", "resource_ref"),
        ),
        _object_schema(
            _schema_ref(capability, "observe", "input"),
            properties=locator,
            required=locator_required,
        ),
        _object_schema(
            _schema_ref(capability, "observe", "output"),
            properties=observed,
            required=observed_required,
            all_of=observed_conditions,
        ),
        _object_schema(
            _schema_ref(capability, "plan", "input"),
            properties=desired,
            required=required,
            all_of=conditions,
        ),
        _object_schema(
            _schema_ref(capability, "plan", "output"),
            properties={
                "changes": _changes(),
                **public_locator,
            },
            required=("changes", "resource_kind", "resource_ref"),
        ),
    )


def _roundtrip_desired_properties() -> dict[str, object]:
    return {
        "application_ref": _string(pattern=_REF_PATTERN),
        "cleanup_required": {"const": True, "type": "boolean"},
        "expected_content_digest": _string(pattern=_DIGEST_PATTERN),
        "logical_path": _string(maxLength=1024, pattern=_LOGICAL_PATH_PATTERN),
        "probe_content": _string(
            minLength=1,
            maxLength=4096,
            **_PUBLIC,
        ),
        "roundtrip_ref": _string(pattern=_REF_PATTERN),
        "user_ref": _string(pattern=_REF_PATTERN),
    }


def _roundtrip_observation_properties() -> dict[str, object]:
    return {
        "application_ref": _public_string(pattern=_REF_PATTERN),
        "cleanup_succeeded": _public_boolean(),
        "completed_at": _public_string(format="date-time"),
        "digest_matches": _public_boolean(),
        "logical_path": _public_string(maxLength=1024, pattern=_LOGICAL_PATH_PATTERN),
        "read_digest": _public_string(pattern=_DIGEST_PATTERN),
        "read_succeeded": _public_boolean(),
        "roundtrip_ref": _public_string(pattern=_REF_PATTERN),
        "user_ref": _public_string(pattern=_REF_PATTERN),
        "write_digest": _public_string(pattern=_DIGEST_PATTERN),
        "write_succeeded": _public_boolean(),
    }


def _build_roundtrip_schemas() -> tuple[CapabilitySchemaDocument, ...]:
    capability = "file-roundtrip-lifecycle"
    desired = _roundtrip_desired_properties()
    observed = _roundtrip_observation_properties()
    required = tuple(sorted(desired))
    observed_required = tuple(sorted(observed))
    locator = {
        "application_ref": _string(pattern=_REF_PATTERN),
        "logical_path": _string(maxLength=1024, pattern=_LOGICAL_PATH_PATTERN),
        "roundtrip_ref": _string(pattern=_REF_PATTERN),
        "user_ref": _string(pattern=_REF_PATTERN),
    }
    locator_required = tuple(sorted(locator))
    return (
        _object_schema(
            _schema_ref(capability, "apply", "input"),
            properties=desired,
            required=required,
        ),
        _object_schema(
            _schema_ref(capability, "apply", "output"),
            properties=observed,
            required=observed_required,
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "input"),
            properties=locator,
            required=locator_required,
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "output"),
            properties={
                "cancelled": _public_boolean(),
                "cleanup_succeeded": _public_boolean(),
                "roundtrip_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=("cancelled", "cleanup_succeeded", "roundtrip_ref"),
        ),
        _object_schema(
            _schema_ref(capability, "observe", "input"),
            properties=locator,
            required=locator_required,
        ),
        _object_schema(
            _schema_ref(capability, "observe", "output"),
            properties=observed,
            required=observed_required,
        ),
        _object_schema(
            _schema_ref(capability, "plan", "input"),
            properties=desired,
            required=required,
        ),
        _object_schema(
            _schema_ref(capability, "plan", "output"),
            properties={
                "changes": _changes(),
                "roundtrip_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=("changes", "roundtrip_ref"),
        ),
    )


CAPABILITY_SCHEMAS = tuple(
    sorted(
        (
            *_build_application_schemas(),
            *_build_oidc_schemas(),
            *_build_account_schemas(),
            *_build_roundtrip_schemas(),
        ),
        key=lambda item: item.schema_ref,
    )
)
SCHEMAS_BY_REF = {schema.schema_ref: schema for schema in CAPABILITY_SCHEMAS}

__all__ = ["CAPABILITY_SCHEMAS", "SCHEMAS_BY_REF"]
