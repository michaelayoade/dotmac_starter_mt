"""V3 validation, logo verification and projection; V2 stays the single source.

V3 reuses the V2 projection of every financial and party field. It adds only
what is new: the frozen logo identity, byte-level logo verification, and a
projection in which a payment link is visible text rather than an anchor, so a
rendered document carries no URL a reader or engine could follow.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import struct
from dataclasses import fields, replace
from typing import Any

from dotmac_kernel.semantic_encoding import digest_of, encode_fields

from dotmac_document_rendering.contracts import (
    FactShapeInvalid,
    FactVersionUnsupported,
    LabelledValue,
    Section,
    TemplateDecision,
)
from dotmac_document_rendering.contracts_v2 import (
    InvoiceDocumentFactV2,
    PaymentLinkValueV2,
    PresentationAssetV2,
    SelectedTemplateV2,
)
from dotmac_document_rendering.contracts_v3 import (
    LOGO_MAX_BYTES,
    LOGO_MAX_DIMENSION,
    LOGO_MEDIA_TYPES,
    DocumentProjectionV3,
    EmbeddedLogoAssetV3,
    InvoiceDocumentFactV3,
)
from dotmac_document_rendering.projection_v2 import (
    _validate_typed_value,
    _walk,
    canonical_value_v2,
    project_invoice_v2,
    validate_fact_v2,
)

PROJECTION_CONTRACT_VERSION_V3 = 3

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_MAGIC = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
}


def validate_logo_asset_v3(asset: EmbeddedLogoAssetV3) -> None:
    """Check the frozen logo identity without reading any bytes."""
    _validate_typed_value(asset, EmbeddedLogoAssetV3, "brand_logo")
    if asset.media_type not in LOGO_MEDIA_TYPES:
        raise FactShapeInvalid("brand logo media type must be image/png or image/jpeg")
    if _SHA256_HEX.fullmatch(asset.sha256) is None:
        raise FactShapeInvalid("brand logo sha256 must be 64 lowercase hex digits")
    if not 1 <= asset.byte_length <= LOGO_MAX_BYTES:
        raise FactShapeInvalid(
            f"brand logo length must be between 1 and {LOGO_MAX_BYTES} bytes"
        )


_JPEG_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})
_JPEG_NOT_SOF = frozenset({0xC4, 0xC8, 0xCC})


def _png_dimensions(content: bytes) -> tuple[int, int]:
    # signature(8) + IHDR chunk: length(4)=13, "IHDR", width(4), height(4).
    if (
        len(content) < 24
        or content[8:12] != b"\x00\x00\x00\r"
        or content[12:16] != b"IHDR"
    ):
        raise FactShapeInvalid("brand logo PNG header is malformed")
    width, height = struct.unpack(">II", content[16:24])
    return width, height


def _jpeg_dimensions(content: bytes) -> tuple[int, int]:
    position = 2
    size = len(content)
    while position < size:
        if content[position] != 0xFF:
            raise FactShapeInvalid("brand logo JPEG header is malformed")
        while position < size and content[position] == 0xFF:
            position += 1
        if position >= size:
            break
        marker = content[position]
        position += 1
        if marker in _JPEG_STANDALONE:
            continue
        if marker in (0xD9, 0xDA, 0x00):
            raise FactShapeInvalid("brand logo JPEG has no frame header")
        if position + 2 > size:
            break
        segment = int.from_bytes(content[position : position + 2], "big")
        if segment < 2 or position + segment > size:
            raise FactShapeInvalid("brand logo JPEG segment is malformed")
        if 0xC0 <= marker <= 0xCF and marker not in _JPEG_NOT_SOF:
            if segment < 8:
                raise FactShapeInvalid("brand logo JPEG frame header is malformed")
            height, width = struct.unpack(">HH", content[position + 3 : position + 7])
            return width, height
        position += segment
    raise FactShapeInvalid("brand logo JPEG has no frame header")


def logo_dimensions_v3(media_type: str, content: bytes) -> tuple[int, int]:
    """Width and height from the image header only; never decodes pixels."""
    if media_type == "image/png":
        return _png_dimensions(content)
    return _jpeg_dimensions(content)


def verify_logo_content_v3(asset: EmbeddedLogoAssetV3, content: bytes) -> None:
    """Refuse bytes that are not exactly the frozen logo. Never echoes content."""
    validate_logo_asset_v3(asset)
    if not isinstance(content, bytes):
        raise FactShapeInvalid("brand logo content must be immutable bytes")
    if len(content) > LOGO_MAX_BYTES:
        raise FactShapeInvalid(f"brand logo exceeds {LOGO_MAX_BYTES} bytes")
    if len(content) != asset.byte_length:
        raise FactShapeInvalid("brand logo length does not match its frozen length")
    if not content.startswith(_MAGIC[asset.media_type]):
        raise FactShapeInvalid("brand logo content does not match its media type")
    width, height = logo_dimensions_v3(asset.media_type, content)
    if not (1 <= width <= LOGO_MAX_DIMENSION and 1 <= height <= LOGO_MAX_DIMENSION):
        raise FactShapeInvalid(
            f"brand logo dimensions must be 1 to {LOGO_MAX_DIMENSION} pixels"
        )
    if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), asset.sha256):
        raise FactShapeInvalid("brand logo digest does not match its frozen digest")


def fact_as_v2(fact: InvoiceDocumentFactV3) -> InvoiceDocumentFactV2:
    """The V2 view used to reuse V2 validation and projection, never persisted."""
    values: dict[str, Any] = {
        item.name: getattr(fact, item.name)
        for item in fields(fact)
        if item.name != "brand_logo"
    }
    values["contract_version"] = 2
    values["brand_asset"] = (
        PresentationAssetV2("none")
        if fact.brand_logo is None
        else PresentationAssetV2("file", fact.brand_logo.file_id)
    )
    return InvoiceDocumentFactV2(**values)


def validate_fact_v3(fact: InvoiceDocumentFactV3) -> None:
    if not isinstance(fact, InvoiceDocumentFactV3):
        raise FactShapeInvalid("fact must use the V3 immutable contract")
    _validate_typed_value(fact, InvoiceDocumentFactV3, "fact")
    if fact.contract_version != 3:
        raise FactVersionUnsupported("only fact contract V3 is accepted")
    if fact.brand_logo is not None:
        validate_logo_asset_v3(fact.brand_logo)
    validate_fact_v2(fact_as_v2(fact))


def fact_fingerprint_v3(fact: InvoiceDocumentFactV3) -> str:
    _validate_typed_value(fact, InvoiceDocumentFactV3, "fact")
    return digest_of("dotmac-document-rendering.fact.v3", canonical_value_v2(fact))


def canonical_projection_payload_v3(projection: DocumentProjectionV3) -> bytes:
    _validate_typed_value(projection, DocumentProjectionV3, "projection")
    return encode_fields(
        tuple(
            (item.name, canonical_value_v2(getattr(projection, item.name)))
            for item in fields(projection)
            if item.name != "digest"
        )
    )


def projection_digest_v3(projection: DocumentProjectionV3) -> str:
    return digest_of(
        "dotmac-document-rendering.projection.v3",
        canonical_projection_payload_v3(projection),
    )


def project_invoice_v3(
    fact: InvoiceDocumentFactV3,
    selected: SelectedTemplateV2,
    *,
    renderer_code: str,
    renderer_version: str,
    media_type: str,
) -> DocumentProjectionV3:
    validate_fact_v3(fact)
    as_v2 = fact_as_v2(fact)
    base = project_invoice_v2(
        as_v2,
        selected,
        renderer_code=renderer_code,
        renderer_version=renderer_version,
        media_type=media_type,
    )
    # V2 decisions begin with one entry per fact leaf, then the template
    # decisions. Re-derive the leaf entries from the V3 fact itself and swap
    # the V2 "reference only, not fetched" brand decision for the V3 one.
    leaf_count = len(tuple(_walk(as_v2)))
    leaves = tuple(
        TemplateDecision(path, "absent" if leaf is None else "present")
        for path, leaf in _walk(fact)
    )
    tail = tuple(
        decision
        for decision in base.template_decisions[leaf_count:]
        if decision.key != "brand_asset"
    )
    logo_decision = TemplateDecision(
        "brand_logo", "omitted" if fact.brand_logo is None else "embedded_data_uri"
    )
    sections = tuple(
        Section(
            section.key,
            section.position,
            tuple(
                LabelledValue(block.label_key, block.label_text, block.value)
                if isinstance(block, PaymentLinkValueV2)
                else block
                for block in section.blocks
            ),
        )
        for section in base.sections
    )
    projection = DocumentProjectionV3(
        PROJECTION_CONTRACT_VERSION_V3,
        fact.invoice_id,
        fact.fact_version,
        fact_fingerprint_v3(fact),
        fact.document_profile_code,
        fact.document_profile_version,
        base.template_code,
        base.template_version,
        renderer_code,
        renderer_version,
        media_type,
        (*leaves, *tail, logo_decision),
        sections,
        fact.brand_logo,
        "",
    )
    return replace(projection, digest=projection_digest_v3(projection))


__all__ = [
    "PROJECTION_CONTRACT_VERSION_V3",
    "canonical_projection_payload_v3",
    "fact_as_v2",
    "fact_fingerprint_v3",
    "logo_dimensions_v3",
    "project_invoice_v3",
    "projection_digest_v3",
    "validate_fact_v3",
    "validate_logo_asset_v3",
    "verify_logo_content_v3",
]
