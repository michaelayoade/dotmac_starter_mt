"""Opaque product database-catalogue coordinates shared by spec and evidence.

Foundation owns this coordinate shape, not the document named by ``schema``.
The publisher's typed reader remains the only authority for those bytes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Final

from .digest import Digest
from .errors import SpecError

__all__ = [
    "MODULE_DATABASE_CATALOG_SCHEMA",
    "PRODUCT_DATABASE_CATALOG_SCHEMA",
    "DatabaseCatalogCoordinateV1",
    "DatabaseCatalogProductIdentityV1",
    "DatabaseCatalogScope",
]

MODULE_DATABASE_CATALOG_SCHEMA: Final = "dotmac.module-database-catalog/v1"
PRODUCT_DATABASE_CATALOG_SCHEMA: Final = "dotmac.product-database-catalog/v1"


class DatabaseCatalogScope(str, Enum):
    MODULE = "module"
    PRODUCT = "product"


# A schema is admitted only with a reader integration that proves the payload
# named by the coordinate.  Extending the open catalogue family means adding a
# new versioned schema here with its exact scope; an unknown schema must not
# inherit the rules of either current kernel document by accident.
_CATALOG_SCHEMA_SCOPE: Final = {
    MODULE_DATABASE_CATALOG_SCHEMA: DatabaseCatalogScope.MODULE,
    PRODUCT_DATABASE_CATALOG_SCHEMA: DatabaseCatalogScope.PRODUCT,
}


def _required(value: str, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"{where} is required and cannot be empty")
    if value != value.strip():
        raise SpecError(f"{where} must be trimmed")
    return value


def _contained_path(value: str) -> str:
    text = _required(value, where="database catalog path")
    if "\\" in text:
        raise SpecError("database catalog path must use POSIX '/' separators")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise SpecError("database catalog path must stay inside the release root")
    return text


def _mapping(value: object, *, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SpecError(f"{where} must be an object")
    return value


def _strict(
    document: Mapping[str, Any],
    *,
    where: str,
    fields: set[str],
    optional: set[str] | None = None,
) -> None:
    optional_fields = optional or set()
    unknown = sorted(set(document) - fields - optional_fields)
    missing = sorted(fields - set(document))
    if unknown:
        raise SpecError(f"{where} has unknown field(s) {unknown}")
    if missing:
        raise SpecError(f"{where} is missing required field(s) {missing}")


def _string(document: Mapping[str, Any], name: str, *, where: str) -> str:
    value = document[name]
    if not isinstance(value, str):
        raise SpecError(f"{where}.{name} must be a string")
    return value


@dataclass(frozen=True, slots=True)
class DatabaseCatalogProductIdentityV1:
    """Declared mapping from descriptor identity to product-catalog identity.

    Product codes are not normalized.  If the deployment descriptor and the
    product catalogue use different codes, the coordinate must name the
    versioned decision that declares that alias.
    """

    descriptor_product: str
    catalog_product: str
    catalog_version: str
    mapping_ref: str = ""

    def __post_init__(self) -> None:
        for name in ("descriptor_product", "catalog_product", "catalog_version"):
            object.__setattr__(
                self,
                name,
                _required(
                    getattr(self, name), where=f"catalog.product_identity.{name}"
                ),
            )
        if (
            not isinstance(self.mapping_ref, str)
            or self.mapping_ref != self.mapping_ref.strip()
        ):
            raise SpecError(
                "catalog.product_identity.mapping_ref must be a trimmed string"
            )
        if self.descriptor_product != self.catalog_product and not self.mapping_ref:
            raise SpecError(
                "different descriptor and catalog product codes require an explicit "
                "product identity mapping_ref"
            )

    def as_document(self) -> dict[str, str]:
        return {
            "descriptor_product": self.descriptor_product,
            "catalog_product": self.catalog_product,
            "catalog_version": self.catalog_version,
            "mapping_ref": self.mapping_ref,
        }

    @classmethod
    def from_document(cls, value: object) -> DatabaseCatalogProductIdentityV1:
        document = _mapping(value, where="DatabaseCatalogProductIdentityV1")
        _strict(
            document,
            where="DatabaseCatalogProductIdentityV1",
            fields={
                "descriptor_product",
                "catalog_product",
                "catalog_version",
                "mapping_ref",
            },
        )
        return cls(
            descriptor_product=_string(
                document,
                "descriptor_product",
                where="DatabaseCatalogProductIdentityV1",
            ),
            catalog_product=_string(
                document,
                "catalog_product",
                where="DatabaseCatalogProductIdentityV1",
            ),
            catalog_version=_string(
                document,
                "catalog_version",
                where="DatabaseCatalogProductIdentityV1",
            ),
            mapping_ref=_string(
                document,
                "mapping_ref",
                where="DatabaseCatalogProductIdentityV1",
            ),
        )


@dataclass(frozen=True, slots=True)
class DatabaseCatalogCoordinateV1:
    """Opaque coordinate with explicit completeness scope."""

    schema: str
    path: str
    digest: str
    scope: DatabaseCatalogScope
    complete_schemas: tuple[str, ...]
    product_identity: DatabaseCatalogProductIdentityV1 | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "schema", _required(self.schema, where="catalog.schema")
        )
        object.__setattr__(self, "path", _contained_path(self.path))
        object.__setattr__(
            self,
            "digest",
            str(Digest.parse(self.digest, where="catalog.digest")),
        )
        try:
            scope = DatabaseCatalogScope(self.scope)
        except ValueError as exc:
            raise SpecError("catalog scope must be 'module' or 'product'") from exc
        object.__setattr__(self, "scope", scope)
        expected_scope = _CATALOG_SCHEMA_SCOPE.get(self.schema)
        if expected_scope is None:
            raise SpecError(
                f"unsupported database catalog schema {self.schema!r}; a future "
                "schema requires an explicit schema/scope registration and typed "
                "payload verifier"
            )
        if scope is not expected_scope:
            raise SpecError(
                f"database catalog schema {self.schema!r} requires scope "
                f"{expected_scope.value!r}, not {scope.value!r}"
            )
        if (
            not isinstance(self.complete_schemas, tuple)
            or not self.complete_schemas
            or not all(
                isinstance(schema, str) and schema and schema == schema.strip()
                for schema in self.complete_schemas
            )
            or self.complete_schemas != tuple(sorted(set(self.complete_schemas)))
        ):
            raise SpecError(
                "catalog complete_schemas must be a non-empty sorted unique tuple"
            )
        if scope is DatabaseCatalogScope.MODULE and len(self.complete_schemas) != 1:
            raise SpecError("a module catalog must cover exactly one complete schema")
        if scope is DatabaseCatalogScope.PRODUCT:
            if not isinstance(self.product_identity, DatabaseCatalogProductIdentityV1):
                raise SpecError("a product catalog requires typed product_identity")
        elif self.product_identity is not None:
            raise SpecError("a module catalog cannot carry product_identity")

    def as_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "schema": self.schema,
            "path": self.path,
            "digest": self.digest,
            "scope": self.scope.value,
            "complete_schemas": list(self.complete_schemas),
        }
        if self.product_identity is not None:
            document["product_identity"] = self.product_identity.as_document()
        return document

    @classmethod
    def from_document(cls, value: object) -> DatabaseCatalogCoordinateV1:
        document = _mapping(value, where="DatabaseCatalogCoordinateV1")
        _strict(
            document,
            where="DatabaseCatalogCoordinateV1",
            fields={
                "schema",
                "path",
                "digest",
                "scope",
                "complete_schemas",
            },
            optional={"product_identity"},
        )
        schemas = document["complete_schemas"]
        if not isinstance(schemas, list) or not all(
            isinstance(item, str) for item in schemas
        ):
            raise SpecError("catalog complete_schemas must be an array of strings")
        raw_scope = _string(document, "scope", where="DatabaseCatalogCoordinateV1")
        try:
            scope = DatabaseCatalogScope(raw_scope)
        except ValueError as exc:
            raise SpecError("catalog scope must be 'module' or 'product'") from exc
        return cls(
            schema=_string(document, "schema", where="DatabaseCatalogCoordinateV1"),
            path=_string(document, "path", where="DatabaseCatalogCoordinateV1"),
            digest=_string(document, "digest", where="DatabaseCatalogCoordinateV1"),
            scope=scope,
            complete_schemas=tuple(schemas),
            product_identity=DatabaseCatalogProductIdentityV1.from_document(
                document["product_identity"]
            )
            if "product_identity" in document
            else None,
        )
