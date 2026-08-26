"""Canonical JSON Schema bytes for managed identity lifecycle operations."""

from __future__ import annotations

from collections.abc import Mapping

from dotmac_kernel import (
    CAPABILITY_SCHEMA_DIALECT,
    CapabilitySchemaDocument,
)

_PUBLIC = {"x-dotmac-data-classification": "public_non_secret"}
_HTTPS_PATTERN = r"^https://[^\s]+$"
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_EMAIL_PATTERN = r"^[^\s@]+@[^\s@]+\.[^\s@]+$"
_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$"
_FQDN_PATTERN = (
    r"^(?=.{1,253}\.?$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}"
    r"[A-Za-z0-9])?\.)+[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$"
)


def _string(**keywords: object) -> dict[str, object]:
    return {"type": "string", **keywords}


def _public_string(**keywords: object) -> dict[str, object]:
    return _string(**_PUBLIC, **keywords)


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
    return "schema:dotmac-managed-identity/" f"{capability}/{operation}/{direction}@v1"


def _realm_desired_properties() -> dict[str, object]:
    return {
        "display_name": _string(minLength=1, maxLength=120),
        "public_hostname": _string(pattern=_FQDN_PATTERN),
        "realm_ref": _string(pattern=_REF_PATTERN),
    }


def _realm_observation_properties() -> dict[str, object]:
    return {
        "admin_endpoint_public": {"const": False, "type": "boolean", **_PUBLIC},
        "authorization_code_enabled": {
            "const": True,
            "type": "boolean",
            **_PUBLIC,
        },
        "discovery_url": _public_string(format="uri", pattern=_HTTPS_PATTERN),
        "issuer_url": _public_string(format="uri", pattern=_HTTPS_PATTERN),
        "jwks_uri": _public_string(format="uri", pattern=_HTTPS_PATTERN),
        "observed_configuration_digest": _public_string(pattern=_DIGEST_PATTERN),
        "realm_ref": _public_string(pattern=_REF_PATTERN),
        "signing_algorithm": {
            "const": "RS256",
            "type": "string",
            **_PUBLIC,
        },
    }


def _client_desired_properties() -> dict[str, object]:
    return {
        "audience": _string(minLength=1, maxLength=255),
        "authorization_code_enabled": {"const": True, "type": "boolean"},
        "client_authentication_required": {"const": True, "type": "boolean"},
        "client_id": _string(minLength=1, maxLength=255),
        "client_ref": _string(pattern=_REF_PATTERN),
        "id_token_signing_algorithm": {"const": "RS256", "type": "string"},
        # A suite composition may supply this from the realm APPLY receipt.
        # It is public routing evidence, never a credential.
        "issuer_url": _public_string(format="uri", pattern=_HTTPS_PATTERN),
        "pkce_method": {"const": "S256", "type": "string"},
        "redirect_uris": {
            "items": _string(format="uri", pattern=_HTTPS_PATTERN),
            "minItems": 1,
            "type": "array",
            "uniqueItems": True,
        },
        "require_aud_azp_validation": {"const": True, "type": "boolean"},
    }


def _client_observation_properties() -> dict[str, object]:
    return {
        "audience": _public_string(minLength=1, maxLength=255),
        "authorization_code_enabled": {
            "const": True,
            "type": "boolean",
            **_PUBLIC,
        },
        "client_authentication_required": {
            "const": True,
            "type": "boolean",
            **_PUBLIC,
        },
        "client_id": _public_string(minLength=1, maxLength=255),
        "client_ref": _public_string(pattern=_REF_PATTERN),
        "client_secret_configured": {"type": "boolean", **_PUBLIC},
        "discovery_url": _public_string(format="uri", pattern=_HTTPS_PATTERN),
        "id_token_signing_algorithm": {
            "const": "RS256",
            "type": "string",
            **_PUBLIC,
        },
        "issuer_url": _public_string(format="uri", pattern=_HTTPS_PATTERN),
        "observed_configuration_digest": _public_string(pattern=_DIGEST_PATTERN),
        "pkce_method": {"const": "S256", "type": "string", **_PUBLIC},
        "redirect_uris": {
            "items": _public_string(format="uri", pattern=_HTTPS_PATTERN),
            "minItems": 1,
            "type": "array",
            "uniqueItems": True,
            **_PUBLIC,
        },
        "require_aud_azp_validation": {
            "const": True,
            "type": "boolean",
            **_PUBLIC,
        },
    }


def _user_desired_properties() -> dict[str, object]:
    return {
        "desired_lifecycle_state": {
            "enum": ["active", "disabled"],
            "type": "string",
        },
        # Email and names are mutable display/contact attributes. The connector
        # locates an account only by identity_ref and returns the provider's
        # immutable subject; neither field may become the binding key.
        "email_address": _string(format="email", maxLength=254, pattern=_EMAIL_PATTERN),
        "enrollment_client_id": _string(minLength=1, maxLength=255),
        "enrollment_lifespan_seconds": {
            "maximum": 86400,
            "minimum": 300,
            "type": "integer",
        },
        "enrollment_redirect_uri": _string(format="uri", pattern=_HTTPS_PATTERN),
        "enrollment_revision": _string(pattern=_REF_PATTERN),
        "family_name": _string(minLength=1, maxLength=120),
        "given_name": _string(minLength=1, maxLength=120),
        "identity_ref": _string(pattern=_REF_PATTERN),
        # A suite composition supplies the exact realm issuer. It is public
        # routing evidence and is verified before a provider mutation.
        "issuer_url": _public_string(format="uri", pattern=_HTTPS_PATTERN),
        "login_name": _string(minLength=1, maxLength=255),
        "realm_ref": _string(pattern=_REF_PATTERN),
    }


def _user_observation_properties() -> dict[str, object]:
    return {
        "credential_enrollment_pending": {"type": "boolean", **_PUBLIC},
        # These mutable PII attributes are schema-validated connector evidence
        # but deliberately unclassified, so Integration does not copy them into
        # its public receipt projection or a downstream composition.
        "email_address": _string(format="email", maxLength=254, pattern=_EMAIL_PATTERN),
        "email_verified": {"type": "boolean"},
        "family_name": _string(minLength=1, maxLength=120),
        "given_name": _string(minLength=1, maxLength=120),
        "identity_ref": _public_string(pattern=_REF_PATTERN),
        "issuer_url": _public_string(format="uri", pattern=_HTTPS_PATTERN),
        "lifecycle_state": {
            "enum": ["active", "disabled"],
            "type": "string",
            **_PUBLIC,
        },
        "login_name": _string(minLength=1, maxLength=255),
        "observed_configuration_digest": _public_string(pattern=_DIGEST_PATTERN),
        "realm_ref": _public_string(pattern=_REF_PATTERN),
        "sessions_revoked": {"type": "boolean", **_PUBLIC},
        "subject": _public_string(minLength=1, maxLength=255),
    }


def _build_realm_schemas() -> tuple[CapabilitySchemaDocument, ...]:
    capability = "realm-lifecycle"
    desired = _realm_desired_properties()
    observed = _realm_observation_properties()
    return (
        _object_schema(
            _schema_ref(capability, "apply", "input"),
            properties=desired,
            required=(
                "display_name",
                "public_hostname",
                "realm_ref",
            ),
        ),
        _object_schema(
            _schema_ref(capability, "apply", "output"),
            properties=observed,
            required=tuple(sorted(observed)),
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "input"),
            properties={
                "realm_ref": _string(pattern=_REF_PATTERN),
            },
            required=("realm_ref",),
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "output"),
            properties={
                "cancelled": {"type": "boolean", **_PUBLIC},
                "realm_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=("cancelled", "realm_ref"),
        ),
        _object_schema(
            _schema_ref(capability, "observe", "input"),
            properties={
                "realm_ref": _string(pattern=_REF_PATTERN),
            },
            required=("realm_ref",),
        ),
        _object_schema(
            _schema_ref(capability, "observe", "output"),
            properties=observed,
            required=tuple(sorted(observed)),
        ),
        _object_schema(
            _schema_ref(capability, "plan", "input"),
            properties=desired,
            required=(
                "display_name",
                "public_hostname",
                "realm_ref",
            ),
        ),
        _object_schema(
            _schema_ref(capability, "plan", "output"),
            properties={
                "changes": {
                    "items": _public_string(minLength=1),
                    "type": "array",
                    **_PUBLIC,
                },
                "realm_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=("changes", "realm_ref"),
        ),
    )


def _build_client_schemas() -> tuple[CapabilitySchemaDocument, ...]:
    capability = "oidc-client-lifecycle"
    desired = _client_desired_properties()
    observed = _client_observation_properties()
    return (
        _object_schema(
            _schema_ref(capability, "apply", "input"),
            properties=desired,
            required=tuple(sorted(desired)),
        ),
        _object_schema(
            _schema_ref(capability, "apply", "output"),
            properties=observed,
            required=tuple(sorted(observed)),
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "input"),
            properties={
                "client_ref": _string(pattern=_REF_PATTERN),
            },
            required=("client_ref",),
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "output"),
            properties={
                "cancelled": {"type": "boolean", **_PUBLIC},
                "client_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=("cancelled", "client_ref"),
        ),
        _object_schema(
            _schema_ref(capability, "observe", "input"),
            properties={
                "client_ref": _string(pattern=_REF_PATTERN),
            },
            required=("client_ref",),
        ),
        _object_schema(
            _schema_ref(capability, "observe", "output"),
            properties=observed,
            required=tuple(sorted(observed)),
        ),
        _object_schema(
            _schema_ref(capability, "plan", "input"),
            properties=desired,
            required=tuple(sorted(desired)),
        ),
        _object_schema(
            _schema_ref(capability, "plan", "output"),
            properties={
                "changes": {
                    "items": _public_string(minLength=1),
                    "type": "array",
                    **_PUBLIC,
                },
                "client_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=("changes", "client_ref"),
        ),
    )


def _build_user_schemas() -> tuple[CapabilitySchemaDocument, ...]:
    capability = "user-lifecycle"
    desired = _user_desired_properties()
    observed = _user_observation_properties()
    identity = {
        "identity_ref": _string(pattern=_REF_PATTERN),
        "issuer_url": _string(format="uri", pattern=_HTTPS_PATTERN),
        "realm_ref": _string(pattern=_REF_PATTERN),
    }
    return (
        _object_schema(
            _schema_ref(capability, "apply", "input"),
            properties=desired,
            required=tuple(sorted(desired)),
        ),
        _object_schema(
            _schema_ref(capability, "apply", "output"),
            properties=observed,
            required=tuple(sorted(observed)),
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "input"),
            properties=identity,
            required=tuple(sorted(identity)),
        ),
        _object_schema(
            _schema_ref(capability, "cancel", "output"),
            properties={
                "cancelled": {"type": "boolean", **_PUBLIC},
                "identity_ref": _public_string(pattern=_REF_PATTERN),
                "subject": _public_string(minLength=1, maxLength=255),
            },
            required=("cancelled", "identity_ref", "subject"),
        ),
        _object_schema(
            _schema_ref(capability, "observe", "input"),
            properties=identity,
            required=tuple(sorted(identity)),
        ),
        _object_schema(
            _schema_ref(capability, "observe", "output"),
            properties=observed,
            required=tuple(sorted(observed)),
        ),
        _object_schema(
            _schema_ref(capability, "plan", "input"),
            properties=desired,
            required=tuple(sorted(desired)),
        ),
        _object_schema(
            _schema_ref(capability, "plan", "output"),
            properties={
                "changes": {
                    "items": _public_string(minLength=1),
                    "type": "array",
                    **_PUBLIC,
                },
                "identity_ref": _public_string(pattern=_REF_PATTERN),
            },
            required=("changes", "identity_ref"),
        ),
    )


CAPABILITY_SCHEMAS = tuple(
    sorted(
        (*_build_realm_schemas(), *_build_client_schemas(), *_build_user_schemas()),
        key=lambda item: item.schema_ref,
    )
)
SCHEMAS_BY_REF = {schema.schema_ref: schema for schema in CAPABILITY_SCHEMAS}

__all__ = ["CAPABILITY_SCHEMAS", "SCHEMAS_BY_REF"]
