"""Canonical JSON Schema bytes for closed managed host operations."""

from __future__ import annotations

from collections.abc import Mapping

from dotmac_kernel import CAPABILITY_SCHEMA_DIALECT, CapabilitySchemaDocument

_PUBLIC = {"x-dotmac-data-classification": "public_non_secret"}
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$"
_VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,126}$"


def _string(**keywords: object) -> dict[str, object]:
    return {"type": "string", **keywords}


def _public_string(**keywords: object) -> dict[str, object]:
    return _string(**_PUBLIC, **keywords)


def _public_boolean() -> dict[str, object]:
    return {"type": "boolean", **_PUBLIC}


def _public_integer(**keywords: object) -> dict[str, object]:
    return {"type": "integer", **_PUBLIC, **keywords}


def _object_schema(
    schema_ref: str,
    *,
    properties: Mapping[str, object],
    required: tuple[str, ...],
    conditions: tuple[Mapping[str, object], ...] = (),
) -> CapabilitySchemaDocument:
    document: dict[str, object] = {
        "$id": schema_ref,
        "$schema": CAPABILITY_SCHEMA_DIALECT,
        "additionalProperties": False,
        "properties": dict(properties),
        "required": list(required),
        "type": "object",
    }
    if conditions:
        document["allOf"] = list(conditions)
    return CapabilitySchemaDocument.from_mapping(document)


def _schema_ref(capability: str, operation: str, direction: str) -> str:
    return f"schema:dotmac-managed-host/{capability}/{operation}/{direction}@v1"


def _plan_output() -> dict[str, object]:
    return {
        "changes": {
            "items": _public_string(minLength=1, maxLength=512),
            "type": "array",
            **_PUBLIC,
        },
        "desired_state_digest": _public_string(pattern=_DIGEST_PATTERN),
    }


def _bundle_schemas() -> tuple[CapabilitySchemaDocument, ...]:
    action = {
        "enum": [
            "decommission",
            "install",
            "repair",
            "resume",
            "rollback",
            "suspend",
            "upgrade",
        ],
        "type": "string",
    }
    desired = {
        "artifact_digest": _string(pattern=_DIGEST_PATTERN),
        "bundle_operation_code": action,
        "bundle_operation_version": {"const": 1, "type": "integer"},
        "bundle_ref": _string(pattern=_REF_PATTERN),
        "configuration_digest": _string(pattern=_DIGEST_PATTERN),
        "desired_bundle_version": _string(pattern=_VERSION_PATTERN),
        "host_ref": _string(pattern=_REF_PATTERN),
        "rollback_bundle_version": _string(pattern=_VERSION_PATTERN),
    }
    desired_required = (
        "artifact_digest",
        "bundle_operation_code",
        "bundle_operation_version",
        "bundle_ref",
        "configuration_digest",
        "desired_bundle_version",
        "host_ref",
    )
    rollback_condition: Mapping[str, object] = {
        "if": {
            "properties": {"bundle_operation_code": {"const": "rollback"}},
            "required": ["bundle_operation_code"],
        },
        "then": {"required": ["rollback_bundle_version"]},
    }
    observed = {
        "applied_artifact_digest": _public_string(pattern=_DIGEST_PATTERN),
        "applied_configuration_digest": _public_string(pattern=_DIGEST_PATTERN),
        "bundle_operation_code": {**action, **_PUBLIC},
        "bundle_operation_version": {
            "const": 1,
            "type": "integer",
            **_PUBLIC,
        },
        "bundle_ref": _public_string(pattern=_REF_PATTERN),
        "health_state": {
            "enum": ["degraded", "healthy", "unhealthy"],
            "type": "string",
            **_PUBLIC,
        },
        "host_ref": _public_string(pattern=_REF_PATTERN),
        "installed_version": _public_string(pattern=_VERSION_PATTERN),
        "observed_at": _public_string(format="date-time"),
        "rollback_available": _public_boolean(),
    }
    observed_required = tuple(sorted(observed))
    return (
        _object_schema(
            _schema_ref("deployment-bundle-lifecycle", "apply", "input"),
            properties=desired,
            required=desired_required,
            conditions=(rollback_condition,),
        ),
        _object_schema(
            _schema_ref("deployment-bundle-lifecycle", "apply", "output"),
            properties=observed,
            required=observed_required,
        ),
        _object_schema(
            _schema_ref("deployment-bundle-lifecycle", "cancel", "input"),
            properties={
                "bundle_ref": _string(pattern=_REF_PATTERN),
                "host_ref": _string(pattern=_REF_PATTERN),
            },
            required=("bundle_ref", "host_ref"),
        ),
        _object_schema(
            _schema_ref("deployment-bundle-lifecycle", "cancel", "output"),
            properties={
                "bundle_ref": _public_string(pattern=_REF_PATTERN),
                "cancelled": _public_boolean(),
                "host_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=("bundle_ref", "cancelled", "host_ref"),
        ),
        _object_schema(
            _schema_ref("deployment-bundle-lifecycle", "observe", "input"),
            properties={
                "bundle_ref": _string(pattern=_REF_PATTERN),
                "host_ref": _string(pattern=_REF_PATTERN),
            },
            required=("bundle_ref", "host_ref"),
        ),
        _object_schema(
            _schema_ref("deployment-bundle-lifecycle", "observe", "output"),
            properties=observed,
            required=observed_required,
        ),
        _object_schema(
            _schema_ref("deployment-bundle-lifecycle", "plan", "input"),
            properties=desired,
            required=desired_required,
            conditions=(rollback_condition,),
        ),
        _object_schema(
            _schema_ref("deployment-bundle-lifecycle", "plan", "output"),
            properties={
                **_plan_output(),
                "bundle_ref": _public_string(pattern=_REF_PATTERN),
                "host_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=(
                "bundle_ref",
                "changes",
                "desired_state_digest",
                "host_ref",
            ),
        ),
    )


def _backup_schemas() -> tuple[CapabilitySchemaDocument, ...]:
    action = {"enum": ["backup", "restore"], "type": "string"}
    desired = {
        "action": action,
        "backup_object_ref": _string(pattern=_REF_PATTERN),
        "backup_policy_ref": _string(pattern=_REF_PATTERN),
        "backup_version_ref": _string(pattern=_REF_PATTERN),
        "host_ref": _string(pattern=_REF_PATTERN),
    }
    restore_condition: Mapping[str, object] = {
        "if": {
            "properties": {"action": {"const": "restore"}},
            "required": ["action"],
        },
        "then": {"required": ["backup_object_ref", "backup_version_ref"]},
    }
    observed = {
        "action": {**action, **_PUBLIC},
        "backup_digest": _public_string(pattern=_DIGEST_PATTERN),
        "backup_object_ref": _public_string(pattern=_REF_PATTERN),
        "backup_version_ref": _public_string(pattern=_REF_PATTERN),
        "completed_at": _public_string(format="date-time"),
        "health_validated": _public_boolean(),
        "host_ref": _public_string(pattern=_REF_PATTERN),
        "restore_validated": _public_boolean(),
    }
    observed_required = tuple(sorted(observed))
    desired_required = ("action", "backup_policy_ref", "host_ref")
    return (
        _object_schema(
            _schema_ref("backup-restore-lifecycle", "apply", "input"),
            properties=desired,
            required=desired_required,
            conditions=(restore_condition,),
        ),
        _object_schema(
            _schema_ref("backup-restore-lifecycle", "apply", "output"),
            properties=observed,
            required=observed_required,
        ),
        _object_schema(
            _schema_ref("backup-restore-lifecycle", "cancel", "input"),
            properties={
                "backup_policy_ref": _string(pattern=_REF_PATTERN),
                "host_ref": _string(pattern=_REF_PATTERN),
            },
            required=("backup_policy_ref", "host_ref"),
        ),
        _object_schema(
            _schema_ref("backup-restore-lifecycle", "cancel", "output"),
            properties={
                "backup_policy_ref": _public_string(pattern=_REF_PATTERN),
                "cancelled": _public_boolean(),
                "host_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=("backup_policy_ref", "cancelled", "host_ref"),
        ),
        _object_schema(
            _schema_ref("backup-restore-lifecycle", "observe", "input"),
            properties={
                "backup_policy_ref": _string(pattern=_REF_PATTERN),
                "host_ref": _string(pattern=_REF_PATTERN),
            },
            required=("backup_policy_ref", "host_ref"),
        ),
        _object_schema(
            _schema_ref("backup-restore-lifecycle", "observe", "output"),
            properties=observed,
            required=observed_required,
        ),
        _object_schema(
            _schema_ref("backup-restore-lifecycle", "plan", "input"),
            properties=desired,
            required=desired_required,
            conditions=(restore_condition,),
        ),
        _object_schema(
            _schema_ref("backup-restore-lifecycle", "plan", "output"),
            properties={
                **_plan_output(),
                "backup_policy_ref": _public_string(pattern=_REF_PATTERN),
                "host_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=(
                "backup_policy_ref",
                "changes",
                "desired_state_digest",
                "host_ref",
            ),
        ),
    )


def _health_schemas() -> tuple[CapabilitySchemaDocument, ...]:
    probe_kind = {
        "enum": ["http_roundtrip", "liveness", "readiness", "service"],
        "type": "string",
    }
    desired = {
        "expected_response_digest": _string(pattern=_DIGEST_PATTERN),
        "host_ref": _string(pattern=_REF_PATTERN),
        "probe_kind": probe_kind,
        "probe_ref": _string(pattern=_REF_PATTERN),
        "timeout_seconds": {"maximum": 300, "minimum": 1, "type": "integer"},
    }
    desired_required = ("host_ref", "probe_kind", "probe_ref", "timeout_seconds")
    observed = {
        "health_state": {
            "enum": ["degraded", "healthy", "unhealthy"],
            "type": "string",
            **_PUBLIC,
        },
        "host_ref": _public_string(pattern=_REF_PATTERN),
        "latency_milliseconds": _public_integer(minimum=0),
        "observed_at": _public_string(format="date-time"),
        "probe_kind": {**probe_kind, **_PUBLIC},
        "probe_ref": _public_string(pattern=_REF_PATTERN),
        "response_digest": _public_string(pattern=_DIGEST_PATTERN),
    }
    observed_required = tuple(sorted(observed))
    return (
        _object_schema(
            _schema_ref("health-probe-lifecycle", "apply", "input"),
            properties=desired,
            required=desired_required,
        ),
        _object_schema(
            _schema_ref("health-probe-lifecycle", "apply", "output"),
            properties=observed,
            required=observed_required,
        ),
        _object_schema(
            _schema_ref("health-probe-lifecycle", "cancel", "input"),
            properties={
                "host_ref": _string(pattern=_REF_PATTERN),
                "probe_ref": _string(pattern=_REF_PATTERN),
            },
            required=("host_ref", "probe_ref"),
        ),
        _object_schema(
            _schema_ref("health-probe-lifecycle", "cancel", "output"),
            properties={
                "cancelled": _public_boolean(),
                "host_ref": _public_string(pattern=_REF_PATTERN),
                "probe_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=("cancelled", "host_ref", "probe_ref"),
        ),
        _object_schema(
            _schema_ref("health-probe-lifecycle", "observe", "input"),
            properties={
                "host_ref": _string(pattern=_REF_PATTERN),
                "probe_ref": _string(pattern=_REF_PATTERN),
            },
            required=("host_ref", "probe_ref"),
        ),
        _object_schema(
            _schema_ref("health-probe-lifecycle", "observe", "output"),
            properties=observed,
            required=observed_required,
        ),
        _object_schema(
            _schema_ref("health-probe-lifecycle", "plan", "input"),
            properties=desired,
            required=desired_required,
        ),
        _object_schema(
            _schema_ref("health-probe-lifecycle", "plan", "output"),
            properties={
                **_plan_output(),
                "host_ref": _public_string(pattern=_REF_PATTERN),
                "probe_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=(
                "changes",
                "desired_state_digest",
                "host_ref",
                "probe_ref",
            ),
        ),
    )


CAPABILITY_SCHEMAS = tuple(
    sorted(
        (*_backup_schemas(), *_bundle_schemas(), *_health_schemas()),
        key=lambda item: item.schema_ref,
    )
)
SCHEMAS_BY_REF = {schema.schema_ref: schema for schema in CAPABILITY_SCHEMAS}

__all__ = ["CAPABILITY_SCHEMAS", "SCHEMAS_BY_REF"]
