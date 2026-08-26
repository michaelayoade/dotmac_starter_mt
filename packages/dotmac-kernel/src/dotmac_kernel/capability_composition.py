"""Canonical product-owned mappings between capability evidence and inputs.

The document contains no runtime value. It approves the *shape* of dataflow:
which exact capability APPLY output schema and public/non-secret instance path
may satisfy which exact downstream APPLY input path. Vendor resolves abstract
capability identities to deployment binding/step ids; Integrator resolves the
later value only from an immutable, schema-checked upstream receipt.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import ClassVar, cast

from dotmac_kernel.capability_contract import (
    CapabilityContractError,
    CapabilityContractSnapshot,
    CapabilitySchemaDocument,
)
from dotmac_kernel.product_manifest import ProductManifestSnapshot

CAPABILITY_COMPOSITION_SCHEMA = "dotmac.capability-composition/v1"

_CODE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,118}[a-z0-9])?$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SCHEMA_REF = re.compile(
    r"^schema:[a-z0-9](?:[a-z0-9._/-]{0,218}[a-z0-9])?@v[1-9][0-9]*$"
)
_FIELDS = frozenset(
    {
        "schema",
        "owner_code",
        "composition_code",
        "schema_version",
        "evidence_bindings",
    }
)
_BINDING_FIELDS = frozenset(
    {
        "binding_code",
        "source_owner_code",
        "source_capability_code",
        "source_capability_schema_version",
        "source_operation_code",
        "source_output_schema_ref",
        "source_output_schema_digest",
        "source_pointer",
        "source_classification",
        "source_selector_pointer",
        "source_selector_value",
        "target_owner_code",
        "target_capability_code",
        "target_capability_schema_version",
        "target_operation_code",
        "target_input_schema_ref",
        "target_input_schema_digest",
        "target_pointer",
        "target_selector_pointer",
        "target_selector_value",
        "coverage",
        "required",
    }
)
_COVERAGE_RULES = frozenset({"each_source_exactly_one", "each_target_exactly_one"})


class CapabilityCompositionError(ValueError):
    """A capability-composition declaration or document is invalid."""


class CapabilityCompositionDigestMismatchError(CapabilityCompositionError):
    """The supplied bytes are not the attested composition document."""


def _code(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _CODE.fullmatch(value) is None:
        raise CapabilityCompositionError(f"{field_name} must be a stable code")
    return value


def _schema_version(value: object, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise CapabilityCompositionError(f"{field_name} must be a positive integer")
    return value


def _schema_ref(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SCHEMA_REF.fullmatch(value) is None:
        raise CapabilityCompositionError(f"{field_name} is not a schema:...@vN ref")
    return value


def _digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise CapabilityCompositionError(
            f"{field_name} must be sha256 plus 64 lowercase hex digits"
        )
    return value


def _pointer(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise CapabilityCompositionError(
            f"{field_name} must be a non-empty RFC 6901 JSON pointer"
        )
    for token in value[1:].split("/"):
        if re.search(r"~(?![01])", token):
            raise CapabilityCompositionError(f"{field_name} has invalid '~' escape")
    return value


def _selector(
    pointer: object,
    value: object,
    *,
    side: str,
) -> tuple[str, str] | None:
    if pointer is None and value is None:
        return None
    if pointer is None or value is None:
        raise CapabilityCompositionError(
            f"{side} selector pointer and value must both be set or both be null"
        )
    return (
        _pointer(pointer, f"{side}_selector_pointer"),
        _code(value, f"{side}_selector_value"),
    )


@dataclass(frozen=True, slots=True)
class CapabilityEvidenceBinding:
    """One approved abstract output-to-input mapping."""

    binding_code: str
    source_owner_code: str
    source_capability_code: str
    source_capability_schema_version: int
    source_operation_code: str
    source_output_schema_ref: str
    source_output_schema_digest: str
    source_pointer: str
    target_owner_code: str
    target_capability_code: str
    target_capability_schema_version: int
    target_operation_code: str
    target_input_schema_ref: str
    target_input_schema_digest: str
    target_pointer: str
    coverage: str
    required: bool = True
    source_classification: str = "public_non_secret"
    source_selector_pointer: str | None = None
    source_selector_value: str | None = None
    target_selector_pointer: str | None = None
    target_selector_value: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "binding_code",
            "source_owner_code",
            "source_capability_code",
            "source_operation_code",
            "target_owner_code",
            "target_capability_code",
            "target_operation_code",
        ):
            _code(getattr(self, field_name), field_name)
        _schema_version(
            self.source_capability_schema_version,
            "source_capability_schema_version",
        )
        _schema_version(
            self.target_capability_schema_version,
            "target_capability_schema_version",
        )
        _schema_ref(self.source_output_schema_ref, "source_output_schema_ref")
        _digest(self.source_output_schema_digest, "source_output_schema_digest")
        _pointer(self.source_pointer, "source_pointer")
        _schema_ref(self.target_input_schema_ref, "target_input_schema_ref")
        _digest(self.target_input_schema_digest, "target_input_schema_digest")
        _pointer(self.target_pointer, "target_pointer")
        if self.source_operation_code != "apply" or self.target_operation_code != (
            "apply"
        ):
            raise CapabilityCompositionError(
                "capability evidence composition is APPLY-to-APPLY in v1"
            )
        if self.source_classification != "public_non_secret":
            raise CapabilityCompositionError(
                "source_classification must be public_non_secret"
            )
        if not isinstance(self.required, bool):
            raise CapabilityCompositionError("required must be a boolean")
        if self.coverage not in _COVERAGE_RULES:
            raise CapabilityCompositionError(
                "coverage must be each_source_exactly_one or " "each_target_exactly_one"
            )
        if not self.required:
            raise CapabilityCompositionError(
                "v1 composition bindings are required; optional runtime edges "
                "need a separately defined coverage contract"
            )
        _selector(
            self.source_selector_pointer,
            self.source_selector_value,
            side="source",
        )
        _selector(
            self.target_selector_pointer,
            self.target_selector_value,
            side="target",
        )


@dataclass(frozen=True, slots=True)
class CapabilityCompositionSnapshot:
    """One immutable suite/product owner's cross-capability dataflow contract."""

    owner_code: str
    composition_code: str
    schema_version: int
    evidence_bindings: tuple[CapabilityEvidenceBinding, ...]

    schema: ClassVar[str] = CAPABILITY_COMPOSITION_SCHEMA

    def __post_init__(self) -> None:
        _code(self.owner_code, "owner_code")
        _code(self.composition_code, "composition_code")
        _schema_version(self.schema_version, "schema_version")
        if not isinstance(self.evidence_bindings, tuple) or not all(
            isinstance(item, CapabilityEvidenceBinding)
            for item in self.evidence_bindings
        ):
            raise CapabilityCompositionError(
                "evidence_bindings must be a tuple of CapabilityEvidenceBinding"
            )
        if not self.evidence_bindings:
            raise CapabilityCompositionError("evidence_bindings must not be empty")
        codes = tuple(item.binding_code for item in self.evidence_bindings)
        if codes != tuple(sorted(set(codes))):
            raise CapabilityCompositionError(
                "evidence_bindings must have unique canonically sorted codes"
            )

    @property
    def identity(self) -> tuple[str, str, int]:
        return (self.owner_code, self.composition_code, self.schema_version)

    def require_owned_by(self, manifest: ProductManifestSnapshot) -> None:
        if not isinstance(manifest, ProductManifestSnapshot):
            raise CapabilityCompositionError(
                "manifest must be a ProductManifestSnapshot"
            )
        if manifest.product_code != self.owner_code:
            raise CapabilityCompositionError(
                f"composition owner {self.owner_code!r} does not match product "
                f"manifest {manifest.product_code!r}"
            )

    def require_compatible_with(
        self,
        *,
        contracts: tuple[CapabilityContractSnapshot, ...],
        schemas: tuple[CapabilitySchemaDocument, ...],
    ) -> None:
        """Cross-check every abstract edge against exact held owner documents."""

        contract_index: dict[tuple[str, str, int], CapabilityContractSnapshot] = {}
        for contract in contracts:
            if contract.identity in contract_index:
                raise CapabilityCompositionError(
                    f"duplicate capability contract {contract.identity!r}"
                )
            contract_index[contract.identity] = contract
        schema_index: dict[str, CapabilitySchemaDocument] = {}
        for schema in schemas:
            if schema.schema_ref in schema_index:
                raise CapabilityCompositionError(
                    f"duplicate capability schema {schema.schema_ref!r}"
                )
            schema_index[schema.schema_ref] = schema

        for binding in self.evidence_bindings:
            source = _contract(
                contract_index,
                (
                    binding.source_owner_code,
                    binding.source_capability_code,
                    binding.source_capability_schema_version,
                ),
                "source",
            )
            target = _contract(
                contract_index,
                (
                    binding.target_owner_code,
                    binding.target_capability_code,
                    binding.target_capability_schema_version,
                ),
                "target",
            )
            source_operation = source.require_operation(binding.source_operation_code)
            target_operation = target.require_operation(binding.target_operation_code)
            if (
                source_operation.output_schema_ref != binding.source_output_schema_ref
                or source_operation.output_schema_digest
                != binding.source_output_schema_digest
            ):
                raise CapabilityCompositionError(
                    f"binding {binding.binding_code!r} source schema pins do not "
                    "match its capability contract"
                )
            if (
                target_operation.input_schema_ref != binding.target_input_schema_ref
                or target_operation.input_schema_digest
                != binding.target_input_schema_digest
            ):
                raise CapabilityCompositionError(
                    f"binding {binding.binding_code!r} target schema pins do not "
                    "match its capability contract"
                )
            source_schema = _held_schema(
                schema_index,
                binding.source_output_schema_ref,
                binding.source_output_schema_digest,
                "source",
            )
            target_schema = _held_schema(
                schema_index,
                binding.target_input_schema_ref,
                binding.target_input_schema_digest,
                "target",
            )
            try:
                source_shape = source_schema.require_public_non_secret_pointer(
                    binding.source_pointer
                )
                target_shape = target_schema.require_instance_pointer(
                    binding.target_pointer
                )
            except CapabilityContractError as exc:
                raise CapabilityCompositionError(str(exc)) from exc
            if source_shape.get("type") != target_shape.get("type") or (
                source_shape.get("format") != target_shape.get("format")
            ):
                raise CapabilityCompositionError(
                    f"binding {binding.binding_code!r} source and target schema "
                    "types/formats differ"
                )
            # A source selector is the only part of this edge that reads the
            # source APPLY-input schema. Resolve it only when one is declared;
            # the edge itself binds source output to target input.
            source_selector = _selector(
                binding.source_selector_pointer,
                binding.source_selector_value,
                side="source",
            )
            if source_selector is not None:
                _require_selector_matches_schema(
                    _held_schema(
                        schema_index,
                        source_operation.input_schema_ref,
                        source_operation.input_schema_digest,
                        "source input",
                    ),
                    pointer=binding.source_selector_pointer,
                    value=binding.source_selector_value,
                    binding_code=binding.binding_code,
                    side="source",
                )
            _require_selector_matches_schema(
                target_schema,
                pointer=binding.target_selector_pointer,
                value=binding.target_selector_value,
                binding_code=binding.binding_code,
                side="target",
            )

    def to_json_bytes(self) -> bytes:
        document = {
            "schema": self.schema,
            "owner_code": self.owner_code,
            "composition_code": self.composition_code,
            "schema_version": self.schema_version,
            "evidence_bindings": [
                {
                    "binding_code": item.binding_code,
                    "source_owner_code": item.source_owner_code,
                    "source_capability_code": item.source_capability_code,
                    "source_capability_schema_version": (
                        item.source_capability_schema_version
                    ),
                    "source_operation_code": item.source_operation_code,
                    "source_output_schema_ref": item.source_output_schema_ref,
                    "source_output_schema_digest": (item.source_output_schema_digest),
                    "source_pointer": item.source_pointer,
                    "source_classification": item.source_classification,
                    "source_selector_pointer": item.source_selector_pointer,
                    "source_selector_value": item.source_selector_value,
                    "target_owner_code": item.target_owner_code,
                    "target_capability_code": item.target_capability_code,
                    "target_capability_schema_version": (
                        item.target_capability_schema_version
                    ),
                    "target_operation_code": item.target_operation_code,
                    "target_input_schema_ref": item.target_input_schema_ref,
                    "target_input_schema_digest": item.target_input_schema_digest,
                    "target_pointer": item.target_pointer,
                    "target_selector_pointer": item.target_selector_pointer,
                    "target_selector_value": item.target_selector_value,
                    "coverage": item.coverage,
                    "required": item.required,
                }
                for item in self.evidence_bindings
            ],
        }
        return json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @property
    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.to_json_bytes()).hexdigest()

    @classmethod
    def from_json_bytes(
        cls,
        payload: bytes,
        *,
        expected_digest: str | None = None,
    ) -> CapabilityCompositionSnapshot:
        if not isinstance(payload, bytes):
            raise CapabilityCompositionError("composition payload must be bytes")
        actual = "sha256:" + hashlib.sha256(payload).hexdigest()
        if expected_digest is not None:
            _digest(expected_digest, "expected_digest")
            if actual != expected_digest:
                raise CapabilityCompositionDigestMismatchError(
                    f"capability composition digest {actual} does not match "
                    f"expected {expected_digest}"
                )
        try:
            raw: object = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CapabilityCompositionError(
                "composition must be a UTF-8 JSON document"
            ) from exc
        document = _object(raw, _FIELDS, "capability composition")
        if document["schema"] != CAPABILITY_COMPOSITION_SCHEMA:
            raise CapabilityCompositionError(
                f"unsupported composition schema {document['schema']!r}"
            )
        raw_bindings = document["evidence_bindings"]
        if not isinstance(raw_bindings, list):
            raise CapabilityCompositionError("evidence_bindings must be an array")
        bindings = tuple(
            _binding_from_document(_object(item, _BINDING_FIELDS, "binding"))
            for item in raw_bindings
        )
        snapshot = cls(
            owner_code=cast(str, document["owner_code"]),
            composition_code=cast(str, document["composition_code"]),
            schema_version=cast(int, document["schema_version"]),
            evidence_bindings=bindings,
        )
        if payload != snapshot.to_json_bytes():
            raise CapabilityCompositionError(
                "composition is valid JSON but not the canonical document"
            )
        return snapshot


def _object(value: object, fields: frozenset[str], context: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CapabilityCompositionError(f"{context} must be an object")
    document = cast(dict[str, object], value)
    if set(document) != fields:
        raise CapabilityCompositionError(
            f"{context} fields differ: missing={sorted(fields - set(document))}, "
            f"unknown={sorted(set(document) - fields)}"
        )
    return document


def _binding_from_document(document: dict[str, object]) -> CapabilityEvidenceBinding:
    return CapabilityEvidenceBinding(
        binding_code=cast(str, document["binding_code"]),
        source_owner_code=cast(str, document["source_owner_code"]),
        source_capability_code=cast(str, document["source_capability_code"]),
        source_capability_schema_version=cast(
            int, document["source_capability_schema_version"]
        ),
        source_operation_code=cast(str, document["source_operation_code"]),
        source_output_schema_ref=cast(str, document["source_output_schema_ref"]),
        source_output_schema_digest=cast(str, document["source_output_schema_digest"]),
        source_pointer=cast(str, document["source_pointer"]),
        source_classification=cast(str, document["source_classification"]),
        source_selector_pointer=cast(str | None, document["source_selector_pointer"]),
        source_selector_value=cast(str | None, document["source_selector_value"]),
        target_owner_code=cast(str, document["target_owner_code"]),
        target_capability_code=cast(str, document["target_capability_code"]),
        target_capability_schema_version=cast(
            int, document["target_capability_schema_version"]
        ),
        target_operation_code=cast(str, document["target_operation_code"]),
        target_input_schema_ref=cast(str, document["target_input_schema_ref"]),
        target_input_schema_digest=cast(str, document["target_input_schema_digest"]),
        target_pointer=cast(str, document["target_pointer"]),
        target_selector_pointer=cast(str | None, document["target_selector_pointer"]),
        target_selector_value=cast(str | None, document["target_selector_value"]),
        coverage=cast(str, document["coverage"]),
        required=cast(bool, document["required"]),
    )


def _require_selector_matches_schema(
    schema: CapabilitySchemaDocument,
    *,
    pointer: str | None,
    value: str | None,
    binding_code: str,
    side: str,
) -> None:
    selector = _selector(pointer, value, side=side)
    if selector is None:
        return
    selector_pointer, selector_value = selector
    try:
        shape = schema.require_instance_pointer(selector_pointer)
    except CapabilityContractError as exc:
        raise CapabilityCompositionError(str(exc)) from exc
    const = shape.get("const")
    enum = shape.get("enum")
    if const is not None:
        permitted = const == selector_value
    elif isinstance(enum, list) and all(isinstance(item, str) for item in enum):
        permitted = selector_value in enum
    else:
        raise CapabilityCompositionError(
            f"binding {binding_code!r} {side} selector must address a string "
            "property closed by const or enum"
        )
    if not permitted:
        raise CapabilityCompositionError(
            f"binding {binding_code!r} {side} selector value "
            f"{selector_value!r} is not admitted by its input schema"
        )


def _contract(
    index: dict[tuple[str, str, int], CapabilityContractSnapshot],
    identity: tuple[str, str, int],
    side: str,
) -> CapabilityContractSnapshot:
    try:
        return index[identity]
    except KeyError as exc:
        raise CapabilityCompositionError(
            f"{side} capability contract {identity!r} is not held"
        ) from exc


def _held_schema(
    index: dict[str, CapabilitySchemaDocument],
    reference: str,
    digest: str,
    side: str,
) -> CapabilitySchemaDocument:
    try:
        schema = index[reference]
    except KeyError as exc:
        raise CapabilityCompositionError(
            f"{side} capability schema {reference!r} is not held"
        ) from exc
    if schema.digest != digest:
        raise CapabilityCompositionError(
            f"{side} schema digest {schema.digest} does not match {digest}"
        )
    return schema


__all__ = [
    "CAPABILITY_COMPOSITION_SCHEMA",
    "CapabilityCompositionDigestMismatchError",
    "CapabilityCompositionError",
    "CapabilityCompositionSnapshot",
    "CapabilityEvidenceBinding",
]
