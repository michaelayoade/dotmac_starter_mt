"""Canonical product-manifest snapshots for release attestation.

A product assembly already declares the two inputs this document needs:
``ProductAssemblySpec.name`` is its stable product code, and its installed
module manifests declare the capability codes that exist. This module turns
those inputs into one deterministic JSON document a release pipeline can attest
and another control plane can verify.

The snapshot is deliberately smaller than ``ModuleRegistry.inventory_payload``.
It answers the cross-plane commercial question — "does this exact product
release declare this capability code?" — and nothing about deployment state,
startup order, database namespaces or enablement. It is not an entitlement, a
product registry, or a network client.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import ClassVar

from dotmac_kernel.assembly import ProductAssemblySpec
from dotmac_kernel.capabilities import (
    CapabilityCatalogue,
    UndeclaredCapabilityError,
)
from dotmac_kernel.modules import ModuleRegistry

PRODUCT_MANIFEST_SCHEMA = "dotmac.product-manifest/v1"

_PRODUCT_CODE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,118}[a-z0-9])?$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DOCUMENT_FIELDS = frozenset(
    {"schema", "product_code", "product_version", "capability_codes"}
)


class ProductManifestError(ValueError):
    """A product-manifest document is invalid or non-canonical."""


class ProductManifestDigestMismatchError(ProductManifestError):
    """The supplied bytes are not the attested product-manifest document."""


@dataclass(frozen=True, slots=True)
class ProductManifestSnapshot:
    """One product release's manifest-declared capability vocabulary.

    Concrete values come from a product assembly; the kernel owns only this
    shape, its canonical encoding and its verification rules.
    """

    product_code: str
    product_version: str
    capability_codes: tuple[str, ...]

    schema: ClassVar[str] = PRODUCT_MANIFEST_SCHEMA

    def __post_init__(self) -> None:
        if (
            not isinstance(self.product_code, str)
            or _PRODUCT_CODE_RE.fullmatch(self.product_code) is None
        ):
            raise ProductManifestError(
                "product_code must be a lowercase stable code of at most 120 "
                "characters using letters, digits, '.', '_' or '-'"
            )
        if (
            not isinstance(self.product_version, str)
            or not self.product_version
            or self.product_version != self.product_version.strip()
        ):
            raise ProductManifestError(
                "product_version must be a non-empty trimmed string"
            )
        if not isinstance(self.capability_codes, tuple) or not all(
            isinstance(code, str) for code in self.capability_codes
        ):
            raise ProductManifestError("capability_codes must be a tuple of strings")
        if any(not code or code != code.strip() for code in self.capability_codes):
            raise ProductManifestError(
                "capability codes must be non-empty trimmed strings"
            )
        canonical_codes = tuple(sorted(set(self.capability_codes)))
        if self.capability_codes != canonical_codes:
            raise ProductManifestError(
                "capability_codes must be unique and sorted canonically"
            )

    @classmethod
    def from_assembly(
        cls,
        assembly: ProductAssemblySpec,
        *,
        product_version: str,
    ) -> ProductManifestSnapshot:
        """Derive the snapshot from the assembly's installed manifests.

        ``ModuleRegistry`` validates the installed set first. The capability
        catalogue then applies the one-owner rule before any codes are emitted.
        A broken assembly therefore cannot be laundered into a plausible JSON
        document.
        """

        registry = ModuleRegistry(assembly.modules)
        catalogue = CapabilityCatalogue.from_manifests(registry.startup_order())
        return cls(
            product_code=assembly.name,
            product_version=product_version,
            capability_codes=tuple(sorted(catalogue.codes())),
        )

    def to_json_bytes(self) -> bytes:
        """The one byte representation whose digest may be attested."""

        payload = {
            "schema": self.schema,
            "product_code": self.product_code,
            "product_version": self.product_version,
            "capability_codes": list(self.capability_codes),
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @property
    def digest(self) -> str:
        """The standard SHA-256 digest of the exact canonical document bytes."""

        return _digest(self.to_json_bytes())

    @classmethod
    def from_json_bytes(
        cls,
        payload: bytes,
        *,
        expected_digest: str | None = None,
    ) -> ProductManifestSnapshot:
        """Parse an attested canonical document, refusing rather than repairing.

        Whitespace, field order and duplicate/sorted capability normalization
        are not silently repaired. A release attests exact bytes; accepting a
        second spelling would leave the digest and the object disagreeing about
        what document was verified.
        """

        if not isinstance(payload, bytes):
            raise ProductManifestError("product manifest payload must be bytes")
        actual_digest = _digest(payload)
        if expected_digest is not None:
            if _SHA256_RE.fullmatch(expected_digest) is None:
                raise ProductManifestError(
                    "expected_digest must be 'sha256:' plus 64 lowercase hex digits"
                )
            if actual_digest != expected_digest:
                raise ProductManifestDigestMismatchError(
                    f"product manifest digest {actual_digest} does not match "
                    f"expected {expected_digest}"
                )
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProductManifestError(
                "product manifest must be a UTF-8 JSON document"
            ) from exc
        if not isinstance(document, dict):
            raise ProductManifestError("product manifest root must be an object")
        if set(document) != _DOCUMENT_FIELDS:
            missing = sorted(_DOCUMENT_FIELDS - set(document))
            unknown = sorted(set(document) - _DOCUMENT_FIELDS)
            raise ProductManifestError(
                f"product manifest fields differ: missing={missing}, unknown={unknown}"
            )
        if document["schema"] != PRODUCT_MANIFEST_SCHEMA:
            raise ProductManifestError(
                f"unsupported product manifest schema {document['schema']!r}"
            )
        codes = document["capability_codes"]
        if not isinstance(codes, list) or not all(
            isinstance(code, str) for code in codes
        ):
            raise ProductManifestError(
                "product manifest capability_codes must be a JSON string array"
            )
        snapshot = cls(
            product_code=document["product_code"],
            product_version=document["product_version"],
            capability_codes=tuple(codes),
        )
        if payload != snapshot.to_json_bytes():
            raise ProductManifestError(
                "product manifest is valid JSON but not the canonical document"
            )
        return snapshot

    def require_capability(self, capability_code: str) -> None:
        """Raise unless this exact product release declares ``capability_code``."""

        if capability_code not in self.capability_codes:
            raise UndeclaredCapabilityError(
                f"product {self.product_code!r} release {self.product_version!r} "
                f"does not declare capability {capability_code!r}"
            )


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


__all__ = [
    "PRODUCT_MANIFEST_SCHEMA",
    "ProductManifestDigestMismatchError",
    "ProductManifestError",
    "ProductManifestSnapshot",
]
