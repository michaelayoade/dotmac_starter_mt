"""Canonical, product-owned capability contract snapshots.

The kernel owns only the document grammar and exact-byte verification.  A
product/domain owner supplies every code and meaning; the kernel deliberately
contains no product list, provider enum, capability catalogue or activation
state.  Runtime declaration and connector binding remain the Integrator's
concern.

The document is also deliberately value-free.  ``config_fields`` describes
which typed values an eventual binding must provide, including the important
difference between an ordinary opaque reference and a ``secret_reference``.
It never contains a configuration value or dereferences a secret.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, TypeVar, cast

from dotmac_kernel.product_manifest import ProductManifestSnapshot

CAPABILITY_CONTRACT_SCHEMA = "dotmac.capability-contract/v1"
CAPABILITY_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
CAPABILITY_SCHEMA_DATA_CLASSIFICATION_KEY = "x-dotmac-data-classification"

_STABLE_CODE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,118}[a-z0-9])?$")
_VERSIONED_CAPABILITY_SUFFIX_RE = re.compile(r"\.v[1-9][0-9]*$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SCHEMA_REF_RE = re.compile(
    r"^schema:[a-z0-9](?:[a-z0-9._/-]{0,218}[a-z0-9])?@v[1-9][0-9]*$"
)
_DOCUMENT_FIELDS = frozenset(
    {
        "schema",
        "owner_code",
        "capability_code",
        "schema_version",
        "operations",
        "config_fields",
        "endpoint_requirements",
        "checks",
    }
)

_ContractItem = TypeVar("_ContractItem")
_EnumMember = TypeVar("_EnumMember", bound=StrEnum)
_SortKey = str | tuple[str, str]


class CapabilityContractError(ValueError):
    """A capability-contract declaration or document is invalid."""


class CapabilityContractDigestMismatchError(CapabilityContractError):
    """The supplied bytes are not the attested capability contract."""


class CapabilitySchemaDigestMismatchError(CapabilityContractError):
    """The supplied bytes are not the attested capability schema."""


class CapabilityConfigValueType(StrEnum):
    """Closed, provider-neutral types accepted by configuration schemas.

    ``REFERENCE`` is a non-secret external identity.  ``SECRET_REFERENCE`` is
    a held secret locator and is intentionally a different type; neither is a
    secret value and this snapshot never resolves either one.
    """

    BOOLEAN = "boolean"
    DECIMAL = "decimal"
    INTEGER = "integer"
    REFERENCE = "reference"
    SECRET_REFERENCE = "secret_reference"  # noqa: S105  # nosec B105 -- schema type
    STRING = "string"
    STRING_LIST = "string_list"


class CapabilityConfigValueFormat(StrEnum):
    """Closed semantic formats validated without product/provider branches."""

    NONE = "none"
    BYTE_QUANTITY = "byte_quantity"
    EMAIL_ADDRESS = "email_address"
    FQDN = "fqdn"
    FQDN_LIST = "fqdn_list"
    HTTPS_URL = "https_url"
    NONNEGATIVE_INTEGER = "nonnegative_integer"
    POSITIVE_INTEGER = "positive_integer"
    STABLE_CODE = "stable_code"


_FORMATS_BY_TYPE: dict[
    CapabilityConfigValueType, frozenset[CapabilityConfigValueFormat]
] = {
    CapabilityConfigValueType.BOOLEAN: frozenset({CapabilityConfigValueFormat.NONE}),
    CapabilityConfigValueType.DECIMAL: frozenset({CapabilityConfigValueFormat.NONE}),
    CapabilityConfigValueType.INTEGER: frozenset(
        {
            CapabilityConfigValueFormat.NONE,
            CapabilityConfigValueFormat.BYTE_QUANTITY,
            CapabilityConfigValueFormat.NONNEGATIVE_INTEGER,
            CapabilityConfigValueFormat.POSITIVE_INTEGER,
        }
    ),
    CapabilityConfigValueType.REFERENCE: frozenset({CapabilityConfigValueFormat.NONE}),
    CapabilityConfigValueType.SECRET_REFERENCE: frozenset(
        {CapabilityConfigValueFormat.NONE}
    ),
    CapabilityConfigValueType.STRING: frozenset(
        {
            CapabilityConfigValueFormat.NONE,
            CapabilityConfigValueFormat.EMAIL_ADDRESS,
            CapabilityConfigValueFormat.FQDN,
            CapabilityConfigValueFormat.HTTPS_URL,
            CapabilityConfigValueFormat.STABLE_CODE,
        }
    ),
    CapabilityConfigValueType.STRING_LIST: frozenset(
        {CapabilityConfigValueFormat.NONE, CapabilityConfigValueFormat.FQDN_LIST}
    ),
}


class CapabilityEndpointType(StrEnum):
    """Closed, provider-neutral endpoint shapes a binding may have to supply."""

    FQDN = "fqdn"
    HOST_PORT = "host_port"
    HTTPS_URL = "https_url"


class CapabilityCheckStage(StrEnum):
    """When a declared check is load-bearing."""

    ACTIVATION = "activation"
    EVIDENCE = "evidence"


class CapabilityEvidenceType(StrEnum):
    """Closed shapes for check results, independent of product meaning."""

    BOOLEAN = "boolean"
    DIGEST = "digest"
    DOCUMENT = "document"
    RECEIPT_REFERENCE = "receipt_reference"


class CapabilitySchemaDataClassification(StrEnum):
    """Whether a schema location may cross a capability boundary as evidence."""

    PUBLIC_NON_SECRET = "public_non_secret"  # noqa: S105  # nosec B105 -- classification
    SECRET = "secret"  # noqa: S105  # nosec B105 -- schema classification


@dataclass(frozen=True, slots=True)
class CapabilitySchemaDocument:
    """One self-contained canonical JSON Schema document.

    Product owners define schema meaning. The kernel only makes the held bytes
    unambiguous: exact ``$id``, one JSON dialect, no duplicate keys or
    non-finite numbers, canonical encoding and no reference that would require
    a consumer to fetch mutable network content while validating a command.
    """

    canonical_bytes: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_bytes, bytes):
            raise CapabilityContractError("capability schema payload must be bytes")
        document = _parse_schema_json(self.canonical_bytes)
        canonical = _canonical_json_bytes(document, context="capability schema")
        if self.canonical_bytes != canonical:
            raise CapabilityContractError(
                "capability schema is valid JSON but not the canonical document"
            )

    @classmethod
    def from_mapping(cls, document: Mapping[str, object]) -> CapabilitySchemaDocument:
        """Create the canonical held document from a product-owned mapping."""

        if not isinstance(document, Mapping) or not all(
            isinstance(key, str) for key in document
        ):
            raise CapabilityContractError("capability schema must be a JSON object")
        return cls(_canonical_json_bytes(dict(document), context="capability schema"))

    @classmethod
    def from_json_bytes(
        cls,
        payload: bytes,
        *,
        expected_ref: str | None = None,
        expected_digest: str | None = None,
    ) -> CapabilitySchemaDocument:
        """Parse canonical bytes and bind both catalogue identity pins."""

        schema = cls(payload)
        if expected_ref is not None:
            _validate_schema_ref(expected_ref, field_name="expected schema reference")
            if schema.schema_ref != expected_ref:
                raise CapabilityContractError(
                    f"capability schema reference {schema.schema_ref!r} does not "
                    f"match expected schema reference {expected_ref!r}"
                )
        if expected_digest is not None:
            _validate_digest(expected_digest, field_name="expected schema digest")
            if schema.digest != expected_digest:
                raise CapabilitySchemaDigestMismatchError(
                    f"capability schema digest {schema.digest} does not match "
                    f"expected {expected_digest}"
                )
        return schema

    @property
    def schema_ref(self) -> str:
        document = _parse_schema_json(self.canonical_bytes)
        return cast(str, document["$id"])

    @property
    def digest(self) -> str:
        return _digest(self.canonical_bytes)

    def to_json_bytes(self) -> bytes:
        return self.canonical_bytes

    def to_mapping(self) -> Mapping[str, object]:
        """Return a fresh parsed mapping for a local schema validator.

        The held canonical bytes remain the authority. Returning a fresh tree
        prevents a connector or validator from mutating the contract object
        that later execution relies on.
        """

        return _parse_schema_json(self.canonical_bytes)

    def instance_value_at(self, instance: object, pointer: str) -> object:
        """Read one approved instance pointer without interpreting its value."""

        self.require_instance_pointer(pointer)
        current = instance
        for token in _json_pointer_tokens(pointer, context="instance JSON pointer"):
            if not isinstance(current, Mapping) or token not in current:
                raise CapabilityContractError(
                    f"capability instance does not contain pointer {pointer!r}"
                )
            current = current[token]
        return current

    def public_non_secret_projection(
        self, instance: Mapping[str, object]
    ) -> Mapping[str, object]:
        """Copy only explicitly public/non-secret declared object properties.

        Unclassified parents are traversed but never copied wholesale. This is
        intentionally conservative: one public child must not make its secret
        sibling durable evidence.
        """

        document = _parse_schema_json(self.canonical_bytes)
        return _public_projection(document, document, instance)

    def require_instance_pointer(self, pointer: str) -> Mapping[str, object]:
        """Resolve a declared object-property path against this held schema.

        Cross-capability evidence uses instance JSON pointers, not schema
        pointers. Only statically declared object properties are admissible;
        pattern/additional-property inference would make the approved dataflow
        depend on runtime spelling rather than exact held bytes.
        """

        tokens = _json_pointer_tokens(pointer, context="instance JSON pointer")
        document = _parse_schema_json(self.canonical_bytes)
        current = _effective_schema(document, document)
        for token in tokens:
            properties = current.get("properties")
            if not isinstance(properties, dict) or token not in properties:
                raise CapabilityContractError(
                    f"capability schema {self.schema_ref!r} does not declare "
                    f"instance pointer {pointer!r}"
                )
            nested = properties[token]
            if not isinstance(nested, dict):
                raise CapabilityContractError(
                    f"schema at instance pointer {pointer!r} is not an object"
                )
            current = _effective_schema(document, cast(dict[str, object], nested))
        return current

    def require_public_non_secret_pointer(self, pointer: str) -> Mapping[str, object]:
        """Resolve an evidence path and refuse secret or unclassified output."""

        schema = self.require_instance_pointer(pointer)
        expected = CapabilitySchemaDataClassification.PUBLIC_NON_SECRET.value
        if schema.get(CAPABILITY_SCHEMA_DATA_CLASSIFICATION_KEY) != expected:
            raise CapabilityContractError(
                f"instance pointer {pointer!r} is not {expected} evidence"
            )
        return schema


def _validate_code(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _STABLE_CODE_RE.fullmatch(value) is None:
        raise CapabilityContractError(
            f"{field_name} must be a lowercase stable code of at most 120 "
            "characters using letters, digits, '.', '_' or '-'"
        )
    return value


def _validate_bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise CapabilityContractError(f"{field_name} must be a boolean")
    return value


def _validate_digest(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise CapabilityContractError(
            f"{field_name} must be 'sha256:' plus 64 lowercase hex digits"
        )
    return value


def _validate_schema_ref(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _SCHEMA_REF_RE.fullmatch(value) is None:
        raise CapabilityContractError(
            f"{field_name} must be a canonical schema:<owner-path>@vN reference"
        )
    return value


@dataclass(frozen=True, slots=True)
class CapabilityOperation:
    """One operation with exact request and result schema identities."""

    operation_code: str
    input_schema_ref: str
    input_schema_digest: str
    output_schema_ref: str
    output_schema_digest: str

    def __post_init__(self) -> None:
        _validate_code(self.operation_code, field_name="operation_code")
        _validate_schema_ref(self.input_schema_ref, field_name="input_schema_ref")
        _validate_digest(self.input_schema_digest, field_name="input_schema_digest")
        _validate_schema_ref(self.output_schema_ref, field_name="output_schema_ref")
        _validate_digest(self.output_schema_digest, field_name="output_schema_digest")


@dataclass(frozen=True, slots=True)
class CapabilityConfigField:
    """One typed, value-free configuration requirement."""

    field_code: str
    value_type: CapabilityConfigValueType
    value_format: CapabilityConfigValueFormat = CapabilityConfigValueFormat.NONE
    required: bool = True

    def __post_init__(self) -> None:
        _validate_code(self.field_code, field_name="field_code")
        if not isinstance(self.value_type, CapabilityConfigValueType):
            raise CapabilityContractError(
                "value_type must be a CapabilityConfigValueType"
            )
        if not isinstance(self.value_format, CapabilityConfigValueFormat):
            raise CapabilityContractError(
                "value_format must be a CapabilityConfigValueFormat"
            )
        if self.value_format not in _FORMATS_BY_TYPE[self.value_type]:
            raise CapabilityContractError(
                f"value_format {self.value_format.value!r} is incompatible with "
                f"value_type {self.value_type.value!r}"
            )
        _validate_bool(self.required, field_name="config field required")


@dataclass(frozen=True, slots=True)
class CapabilityEndpointRequirement:
    """One typed endpoint a binding must or may supply."""

    endpoint_code: str
    endpoint_type: CapabilityEndpointType
    operation_codes: tuple[str, ...]
    required: bool = True

    def __post_init__(self) -> None:
        _validate_code(self.endpoint_code, field_name="endpoint_code")
        if not isinstance(self.endpoint_type, CapabilityEndpointType):
            raise CapabilityContractError(
                "endpoint_type must be a CapabilityEndpointType"
            )
        if not isinstance(self.operation_codes, tuple) or not all(
            isinstance(code, str) for code in self.operation_codes
        ):
            raise CapabilityContractError("endpoint operation_codes must be a tuple")
        for code in self.operation_codes:
            _validate_code(code, field_name="endpoint operation_code")
        if not self.operation_codes or self.operation_codes != tuple(
            sorted(set(self.operation_codes))
        ):
            raise CapabilityContractError(
                "endpoint operation_codes must be non-empty, unique and sorted"
            )
        _validate_bool(self.required, field_name="endpoint requirement required")


@dataclass(frozen=True, slots=True)
class CapabilityCheck:
    """One activation gate or post-operation evidence requirement."""

    check_code: str
    stage: CapabilityCheckStage
    evidence_type: CapabilityEvidenceType
    required: bool = True

    def __post_init__(self) -> None:
        _validate_code(self.check_code, field_name="check_code")
        if not isinstance(self.stage, CapabilityCheckStage):
            raise CapabilityContractError("stage must be a CapabilityCheckStage")
        if not isinstance(self.evidence_type, CapabilityEvidenceType):
            raise CapabilityContractError(
                "evidence_type must be a CapabilityEvidenceType"
            )
        _validate_bool(self.required, field_name="check required")


@dataclass(frozen=True, slots=True)
class CapabilityContractSnapshot:
    """One product owner's immutable version of one capability contract.

    Tuple order is part of the signed document.  Construction and parsing both
    refuse non-canonical order instead of repairing it, so the digest can never
    attest one spelling while callers reason about another.
    """

    owner_code: str
    capability_code: str
    schema_version: int
    operations: tuple[CapabilityOperation, ...]
    config_fields: tuple[CapabilityConfigField, ...] = ()
    endpoint_requirements: tuple[CapabilityEndpointRequirement, ...] = ()
    checks: tuple[CapabilityCheck, ...] = ()

    schema: ClassVar[str] = CAPABILITY_CONTRACT_SCHEMA

    def __post_init__(self) -> None:
        _validate_code(self.owner_code, field_name="owner_code")
        _validate_code(self.capability_code, field_name="capability_code")
        if _VERSIONED_CAPABILITY_SUFFIX_RE.search(self.capability_code):
            raise CapabilityContractError(
                "capability_code is unversioned; schema_version supplies the "
                "public capability id suffix"
            )
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise CapabilityContractError(
                "schema_version must be an integer greater than or equal to 1"
            )
        self._validate_tuple(
            self.operations,
            field_name="operations",
            item_type=CapabilityOperation,
            key=lambda item: item.operation_code,
            require_nonempty=True,
        )
        self._validate_tuple(
            self.config_fields,
            field_name="config_fields",
            item_type=CapabilityConfigField,
            key=lambda item: item.field_code,
        )
        self._validate_tuple(
            self.endpoint_requirements,
            field_name="endpoint_requirements",
            item_type=CapabilityEndpointRequirement,
            key=lambda item: item.endpoint_code,
        )
        config_codes = {item.field_code for item in self.config_fields}
        endpoint_codes = {item.endpoint_code for item in self.endpoint_requirements}
        collisions = config_codes & endpoint_codes
        if collisions:
            raise CapabilityContractError(
                f"owner contract declares {sorted(collisions)[0]!r} as both a "
                "configuration field and an endpoint"
            )
        declared_operations = {item.operation_code for item in self.operations}
        for endpoint in self.endpoint_requirements:
            unknown = set(endpoint.operation_codes) - declared_operations
            if unknown:
                raise CapabilityContractError(
                    f"endpoint {endpoint.endpoint_code!r} references undeclared "
                    f"operation {sorted(unknown)[0]!r}"
                )
        self._validate_tuple(
            self.checks,
            field_name="checks",
            item_type=CapabilityCheck,
            key=lambda item: (item.stage.value, item.check_code),
            uniqueness_key=lambda item: item.check_code,
        )

    @staticmethod
    def _validate_tuple(
        values: object,
        *,
        field_name: str,
        item_type: type[_ContractItem],
        key: Callable[[_ContractItem], _SortKey],
        uniqueness_key: Callable[[_ContractItem], _SortKey] | None = None,
        require_nonempty: bool = False,
    ) -> None:
        if not isinstance(values, tuple) or not all(
            isinstance(value, item_type) for value in values
        ):
            raise CapabilityContractError(
                f"{field_name} must be a tuple of {item_type.__name__} values"
            )
        if require_nonempty and not values:
            raise CapabilityContractError(f"{field_name} must not be empty")
        typed_values = cast(tuple[_ContractItem, ...], values)
        keys = tuple(key(value) for value in typed_values)
        unique_keys = tuple((uniqueness_key or key)(value) for value in typed_values)
        if len(unique_keys) != len(set(unique_keys)):
            raise CapabilityContractError(f"{field_name} must be unique")
        if keys != tuple(sorted(keys)):
            raise CapabilityContractError(
                f"{field_name} must be sorted in canonical order"
            )

    @property
    def identity(self) -> tuple[str, str, int]:
        """The exact product-owned contract identity used by a binding."""

        return (self.owner_code, self.capability_code, self.schema_version)

    @property
    def capability_id(self) -> str:
        """The public manifest/wire id derived from code plus schema version."""

        return f"{self.capability_code}.v{self.schema_version}"

    def require_operation(self, operation_code: str) -> CapabilityOperation:
        """Return the exact operation declaration or refuse an invented one."""

        _validate_code(operation_code, field_name="operation_code")
        for operation in self.operations:
            if operation.operation_code == operation_code:
                return operation
        raise CapabilityContractError(
            f"capability {self.capability_code!r} does not declare operation "
            f"{operation_code!r}"
        )

    def require_declared_by(self, manifest: ProductManifestSnapshot) -> None:
        """Prove that the named product release owns this capability code."""

        if not isinstance(manifest, ProductManifestSnapshot):
            raise CapabilityContractError("manifest must be a ProductManifestSnapshot")
        if manifest.product_code != self.owner_code:
            raise CapabilityContractError(
                f"owner_code {self.owner_code!r} does not match product manifest "
                f"{manifest.product_code!r}"
            )
        manifest.require_capability(self.capability_id)

    def to_json_bytes(self) -> bytes:
        """Return the only byte representation whose digest may be attested."""

        payload = {
            "schema": self.schema,
            "owner_code": self.owner_code,
            "capability_code": self.capability_code,
            "schema_version": self.schema_version,
            "operations": [
                {
                    "operation_code": operation.operation_code,
                    "input_schema_ref": operation.input_schema_ref,
                    "input_schema_digest": operation.input_schema_digest,
                    "output_schema_ref": operation.output_schema_ref,
                    "output_schema_digest": operation.output_schema_digest,
                }
                for operation in self.operations
            ],
            "config_fields": [
                {
                    "field_code": field.field_code,
                    "value_type": field.value_type.value,
                    "value_format": field.value_format.value,
                    "required": field.required,
                }
                for field in self.config_fields
            ],
            "endpoint_requirements": [
                {
                    "endpoint_code": requirement.endpoint_code,
                    "endpoint_type": requirement.endpoint_type.value,
                    "operation_codes": list(requirement.operation_codes),
                    "required": requirement.required,
                }
                for requirement in self.endpoint_requirements
            ],
            "checks": [
                {
                    "check_code": check.check_code,
                    "stage": check.stage.value,
                    "evidence_type": check.evidence_type.value,
                    "required": check.required,
                }
                for check in self.checks
            ],
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @property
    def digest(self) -> str:
        """The SHA-256 digest of the exact canonical document bytes."""

        return _digest(self.to_json_bytes())

    @classmethod
    def from_json_bytes(
        cls,
        payload: bytes,
        *,
        expected_digest: str | None = None,
    ) -> CapabilityContractSnapshot:
        """Parse exact canonical bytes and optionally verify their digest."""

        if not isinstance(payload, bytes):
            raise CapabilityContractError("capability contract payload must be bytes")
        actual_digest = _digest(payload)
        if expected_digest is not None:
            if _SHA256_RE.fullmatch(expected_digest) is None:
                raise CapabilityContractError(
                    "expected_digest must be 'sha256:' plus 64 lowercase hex digits"
                )
            if actual_digest != expected_digest:
                raise CapabilityContractDigestMismatchError(
                    f"capability contract digest {actual_digest} does not match "
                    f"expected {expected_digest}"
                )
        try:
            raw: object = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CapabilityContractError(
                "capability contract must be a UTF-8 JSON document"
            ) from exc
        document = _require_object(
            raw, fields=_DOCUMENT_FIELDS, context="capability contract"
        )
        if document["schema"] != CAPABILITY_CONTRACT_SCHEMA:
            raise CapabilityContractError(
                f"unsupported capability contract schema {document['schema']!r}"
            )
        owner_code = _require_string(document["owner_code"], "owner_code")
        capability_code = _require_string(
            document["capability_code"], "capability_code"
        )
        schema_version = document["schema_version"]
        if type(schema_version) is not int:
            raise CapabilityContractError(
                "schema_version must be an integer greater than or equal to 1"
            )
        snapshot = cls(
            owner_code=owner_code,
            capability_code=capability_code,
            schema_version=schema_version,
            operations=_parse_operations(document["operations"]),
            config_fields=_parse_config_fields(document["config_fields"]),
            endpoint_requirements=_parse_endpoint_requirements(
                document["endpoint_requirements"]
            ),
            checks=_parse_checks(document["checks"]),
        )
        if payload != snapshot.to_json_bytes():
            raise CapabilityContractError(
                "capability contract is valid JSON but not the canonical document"
            )
        return snapshot


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _canonical_json_bytes(value: object, *, context: str) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CapabilityContractError(
            f"{context} contains a value JSON cannot represent"
        ) from exc


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise CapabilityContractError(
                f"capability schema contains duplicate JSON key {key!r}"
            )
        document[key] = value
    return document


def _reject_json_constant(value: str) -> object:
    raise CapabilityContractError(
        f"capability schema contains non-finite number {value!r}"
    )


def _parse_schema_json(payload: bytes) -> dict[str, object]:
    try:
        raw: object = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityContractError(
            "capability schema must be a UTF-8 JSON document"
        ) from exc
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        raise CapabilityContractError("capability schema must be a JSON object")
    document = cast(dict[str, object], raw)
    schema_ref = document.get("$id")
    _validate_schema_ref(schema_ref, field_name="capability schema $id")
    if document.get("$schema") != CAPABILITY_SCHEMA_DIALECT:
        raise CapabilityContractError(
            "capability schema $schema must name JSON Schema draft 2020-12"
        )
    _validate_local_schema_references(document)
    return document


def _validate_local_schema_references(value: object) -> None:
    if isinstance(value, dict):
        document = cast(dict[object, object], value)
        for key, nested in document.items():
            if key in {"$ref", "$dynamicRef", "$recursiveRef"}:
                if not isinstance(nested, str) or not (
                    nested == "#" or nested.startswith("#/")
                ):
                    raise CapabilityContractError(
                        "capability schema references must be local JSON pointers"
                    )
            _validate_local_schema_references(nested)
        return
    if isinstance(value, list):
        for nested in cast(list[object], value):
            _validate_local_schema_references(nested)
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise CapabilityContractError("capability schema contains a non-finite number")


def _json_pointer_tokens(pointer: str, *, context: str) -> tuple[str, ...]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise CapabilityContractError(
            f"{context} must be a non-empty RFC 6901 JSON pointer"
        )
    tokens: list[str] = []
    for raw in pointer[1:].split("/"):
        if re.search(r"~(?![01])", raw):
            raise CapabilityContractError(f"{context} has invalid '~' escape")
        tokens.append(raw.replace("~1", "/").replace("~0", "~"))
    return tuple(tokens)


def _schema_location(document: dict[str, object], reference: str) -> dict[str, object]:
    if reference == "#":
        return document
    current: object = document
    for token in _json_pointer_tokens(
        reference.removeprefix("#"), context="schema reference JSON pointer"
    ):
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit():
            index = int(token)
            if index >= len(current):
                raise CapabilityContractError(
                    f"schema reference {reference!r} does not resolve"
                )
            current = current[index]
        else:
            raise CapabilityContractError(
                f"schema reference {reference!r} does not resolve"
            )
    if not isinstance(current, dict):
        raise CapabilityContractError(
            f"schema reference {reference!r} does not resolve to a schema object"
        )
    return cast(dict[str, object], current)


def _effective_schema(
    document: dict[str, object],
    schema: dict[str, object],
    *,
    seen: frozenset[str] = frozenset(),
) -> dict[str, object]:
    reference = schema.get("$ref")
    if reference is None:
        return dict(schema)
    if not isinstance(reference, str):
        raise CapabilityContractError("capability schema $ref must be a string")
    if reference in seen:
        raise CapabilityContractError(
            f"capability schema reference cycle at {reference!r}"
        )
    target = _effective_schema(
        document,
        _schema_location(document, reference),
        seen=seen | {reference},
    )
    target.update({key: value for key, value in schema.items() if key != "$ref"})
    return target


def _public_projection(
    document: dict[str, object],
    schema: dict[str, object],
    instance: Mapping[str, object],
) -> dict[str, object]:
    current = _effective_schema(document, schema)
    properties = current.get("properties")
    if not isinstance(properties, dict):
        return {}
    projected: dict[str, object] = {}
    for key, nested in properties.items():
        if key not in instance or not isinstance(nested, dict):
            continue
        effective = _effective_schema(document, cast(dict[str, object], nested))
        value = instance[key]
        if effective.get(CAPABILITY_SCHEMA_DATA_CLASSIFICATION_KEY) == (
            CapabilitySchemaDataClassification.PUBLIC_NON_SECRET.value
        ):
            projected[key] = value
            continue
        if isinstance(value, Mapping):
            child = _public_projection(document, effective, value)
            if child:
                projected[key] = child
    return projected


def _require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise CapabilityContractError(f"{field_name} must be a string")
    return value


def _require_list(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise CapabilityContractError(f"{context} must be a JSON array")
    return cast(list[object], value)


def _require_object(
    value: object, *, fields: frozenset[str], context: str
) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CapabilityContractError(f"{context} must be a JSON object")
    document = cast(dict[str, object], value)
    if set(document) != fields:
        missing = sorted(fields - set(document))
        unknown = sorted(set(document) - fields)
        raise CapabilityContractError(
            f"{context} fields differ: missing={missing}, unknown={unknown}"
        )
    return document


def _enum_value(
    enum_type: type[_EnumMember], value: object, field_name: str
) -> _EnumMember:
    if not isinstance(value, str):
        raise CapabilityContractError(f"{field_name} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise CapabilityContractError(f"unsupported {field_name} {value!r}") from exc


def _parse_operations(value: object) -> tuple[CapabilityOperation, ...]:
    result: list[CapabilityOperation] = []
    expected = frozenset(
        {
            "operation_code",
            "input_schema_ref",
            "input_schema_digest",
            "output_schema_ref",
            "output_schema_digest",
        }
    )
    for item in _require_list(value, "operations"):
        document = _require_object(item, fields=expected, context="operation")
        result.append(
            CapabilityOperation(
                operation_code=_require_string(
                    document["operation_code"], "operation_code"
                ),
                input_schema_ref=_require_string(
                    document["input_schema_ref"], "input_schema_ref"
                ),
                input_schema_digest=_require_string(
                    document["input_schema_digest"], "input_schema_digest"
                ),
                output_schema_ref=_require_string(
                    document["output_schema_ref"], "output_schema_ref"
                ),
                output_schema_digest=_require_string(
                    document["output_schema_digest"], "output_schema_digest"
                ),
            )
        )
    return tuple(result)


def _parse_config_fields(value: object) -> tuple[CapabilityConfigField, ...]:
    result: list[CapabilityConfigField] = []
    expected = frozenset({"field_code", "value_type", "value_format", "required"})
    for item in _require_list(value, "config_fields"):
        document = _require_object(item, fields=expected, context="configuration field")
        result.append(
            CapabilityConfigField(
                field_code=_require_string(document["field_code"], "field_code"),
                value_type=_enum_value(
                    CapabilityConfigValueType,
                    document["value_type"],
                    "value_type",
                ),
                value_format=_enum_value(
                    CapabilityConfigValueFormat,
                    document["value_format"],
                    "value_format",
                ),
                required=_validate_bool(
                    document["required"], field_name="config field required"
                ),
            )
        )
    return tuple(result)


def _parse_endpoint_requirements(
    value: object,
) -> tuple[CapabilityEndpointRequirement, ...]:
    result: list[CapabilityEndpointRequirement] = []
    expected = frozenset(
        {"endpoint_code", "endpoint_type", "operation_codes", "required"}
    )
    for item in _require_list(value, "endpoint_requirements"):
        document = _require_object(
            item, fields=expected, context="endpoint requirement"
        )
        result.append(
            CapabilityEndpointRequirement(
                endpoint_code=_require_string(
                    document["endpoint_code"], "endpoint_code"
                ),
                endpoint_type=_enum_value(
                    CapabilityEndpointType,
                    document["endpoint_type"],
                    "endpoint_type",
                ),
                operation_codes=tuple(
                    _require_string(item, "endpoint operation_code")
                    for item in _require_list(
                        document["operation_codes"], "endpoint operation_codes"
                    )
                ),
                required=_validate_bool(
                    document["required"], field_name="endpoint requirement required"
                ),
            )
        )
    return tuple(result)


def _parse_checks(value: object) -> tuple[CapabilityCheck, ...]:
    result: list[CapabilityCheck] = []
    expected = frozenset({"check_code", "stage", "evidence_type", "required"})
    for item in _require_list(value, "checks"):
        document = _require_object(item, fields=expected, context="check")
        result.append(
            CapabilityCheck(
                check_code=_require_string(document["check_code"], "check_code"),
                stage=_enum_value(CapabilityCheckStage, document["stage"], "stage"),
                evidence_type=_enum_value(
                    CapabilityEvidenceType,
                    document["evidence_type"],
                    "evidence_type",
                ),
                required=_validate_bool(
                    document["required"], field_name="check required"
                ),
            )
        )
    return tuple(result)


__all__ = [
    "CAPABILITY_CONTRACT_SCHEMA",
    "CAPABILITY_SCHEMA_DIALECT",
    "CAPABILITY_SCHEMA_DATA_CLASSIFICATION_KEY",
    "CapabilityCheck",
    "CapabilityCheckStage",
    "CapabilityConfigField",
    "CapabilityConfigValueFormat",
    "CapabilityConfigValueType",
    "CapabilityContractDigestMismatchError",
    "CapabilityContractError",
    "CapabilityContractSnapshot",
    "CapabilityEndpointRequirement",
    "CapabilityEndpointType",
    "CapabilityEvidenceType",
    "CapabilityOperation",
    "CapabilitySchemaDigestMismatchError",
    "CapabilitySchemaDataClassification",
    "CapabilitySchemaDocument",
]
