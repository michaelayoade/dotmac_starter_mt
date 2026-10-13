"""Closed HTML/CSS and bounded in-memory PDF checks for composed V2 engines.

PDF parsing uses upstream pypdf's fixed decompression ceiling before applying
our stricter aggregate decoded-stream budget. These are trusted composed engine
outputs, not an arbitrary-upload parser sandbox or a hard CPU deadline.
"""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any
from zlib import error as ZlibError

from pypdf import PdfReader
from pypdf.errors import PyPdfError
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, StreamObject

from dotmac_document_rendering.contracts import (
    FactShapeInvalid,
    LabelledValue,
    StaticText,
    Table,
)
from dotmac_document_rendering.contracts_v2 import (
    DocumentProjectionV2,
    _require_utf8_text,
)

MAX_PDF_BYTES = 16 * 1024 * 1024
MAX_PDF_DECODED_BYTES = 32 * 1024 * 1024
MAX_PDF_TEXT = 2 * 1024 * 1024
MAX_PDF_OBJECTS = 20_000
MAX_PDF_PAGES = 256
MAX_PDF_DEPTH = 64

# A closed passive CSS subset. No escapes/comments/functions/custom properties,
# arbitrary at-rules, selectors or URL-valued properties enter the engine.
_PROPERTIES = {
    "color",
    "background-color",
    "font-family",
    "font-size",
    "font-weight",
    "line-height",
    "margin",
    "margin-block",
    "margin-top",
    "margin-bottom",
    "margin-left",
    "margin-right",
    "padding",
    "padding-top",
    "padding-bottom",
    "padding-left",
    "padding-right",
    "display",
    "grid-template-columns",
    "gap",
    "border",
    "border-collapse",
    "width",
    "height",
    "text-align",
    "size",
    "page-break-before",
    "page-break-after",
    "page-break-inside",
    "break-before",
    "break-after",
    "break-inside",
    "white-space",
    "vertical-align",
}
_TAGS = "html|head|body|section|h2|p|dl|dt|dd|table|thead|tbody|tr|th|td|a"
_SELECTOR = re.compile(rf"(?:{_TAGS})(?:(?:\s+|\s*>\s*)(?:{_TAGS}))*\Z")
_VALUE = re.compile(r"[a-zA-Z0-9#.% ,+\-]+\Z")


def validate_stylesheet_v2(stylesheet: str) -> None:
    if not isinstance(stylesheet, str) or len(stylesheet) > 32_768:
        raise FactShapeInvalid("stylesheet must be bounded passive CSS text")
    _require_utf8_text(stylesheet, "stylesheet")
    if any(char in stylesheet for char in "\\/<>()\"'[]") or "/*" in stylesheet:
        raise FactShapeInvalid(
            "stylesheet escapes, comments and active syntax are refused"
        )
    remaining = stylesheet.strip()
    while remaining:
        match = re.match(r"([^{}]+)\{([^{}]*)\}", remaining)
        if match is None:
            raise FactShapeInvalid("stylesheet must use the closed passive CSS grammar")
        selector, declarations = match.groups()
        selector = selector.strip()
        if selector != "@page" and not all(
            _SELECTOR.fullmatch(part.strip()) for part in selector.split(",")
        ):
            raise FactShapeInvalid("stylesheet selector is outside the passive grammar")
        for declaration in declarations.split(";"):
            if not declaration.strip():
                continue
            name, separator, value = declaration.partition(":")
            if (
                not separator
                or name.strip() not in _PROPERTIES
                or not _VALUE.fullmatch(value.strip())
            ):
                raise FactShapeInvalid(
                    "stylesheet property/value is outside the passive grammar"
                )
        remaining = remaining[match.end() :].strip()


def projected_text_v2(projection: DocumentProjectionV2) -> tuple[str, ...]:
    values = []
    for section in projection.sections:
        for block in section.blocks:
            if isinstance(block, StaticText):
                values.append(block.text)
            elif isinstance(block, LabelledValue):
                values.extend((block.label_text, block.value.text))
            elif isinstance(block, Table):
                values.extend(column.label_text for column in block.columns)
                values.extend(value.text for row in block.rows for value in row)
    return tuple(values)


class _PdfViolation(ValueError):
    pass


def pdf_contract_violations_v2(
    payload: bytes, projection: DocumentProjectionV2
) -> tuple[str, ...]:
    """Parse structure, inspect the entire reachable graph, then verify ordered text."""
    if len(payload) > MAX_PDF_BYTES:
        return ("PDF exceeds the bounded parser byte limit",)
    try:
        reader = PdfReader(BytesIO(payload), strict=True, root_object_recovery_limit=0)
        if reader.is_encrypted:
            raise _PdfViolation("encrypted PDF is refused")
        size = reader.trailer.get("/Size")
        if not isinstance(size, int) or not 1 <= size <= MAX_PDF_OBJECTS:
            raise _PdfViolation("PDF cross-reference object count exceeds its bound")
        forbidden = {
            "/AA",
            "/OpenAction",
            "/JS",
            "/JavaScript",
            "/Launch",
            "/URI",
            "/AcroForm",
            "/XFA",
            "/EmbeddedFiles",
            "/EF",
            "/Annots",
            "/RichMedia",
            "/Sound",
            "/Movie",
            "/SubmitForm",
            "/ImportData",
            "/GoToR",
            "/GoToE",
            "/F",
            "/FFilter",
            "/FDecodeParms",
            "/OPI",
            "/A",
        }
        seen: set[tuple[int, int] | int] = set()
        pending: list[tuple[Any, int]] = [(reader.trailer, 0)]
        decoded_bytes = 0
        pages_seen = 0
        while pending:
            obj, depth = pending.pop()
            identity: tuple[int, int] | int = (
                (obj.idnum, obj.generation)
                if isinstance(obj, IndirectObject)
                else id(obj)
            )
            if identity in seen:
                continue
            if depth > MAX_PDF_DEPTH or len(seen) >= MAX_PDF_OBJECTS:
                raise _PdfViolation("PDF object graph exceeds its depth/object bound")
            seen.add(identity)
            if isinstance(obj, IndirectObject):
                pending.append((obj.get_object(), depth + 1))
                continue
            if isinstance(obj, DictionaryObject):
                if forbidden.intersection(obj.keys()) or obj.get("/Type") in {
                    "/Filespec",
                    "/EmbeddedFile",
                }:
                    raise _PdfViolation(
                        "PDF active, embedded or external content is refused"
                    )
                if obj.get("/Type") == "/Page":
                    pages_seen += 1
                    if pages_seen > MAX_PDF_PAGES:
                        raise _PdfViolation("PDF page count exceeds its bound")
                pending.extend((value, depth + 1) for value in obj.values())
                if isinstance(obj, StreamObject):
                    decoded_bytes += len(obj.get_data())
                    if decoded_bytes > MAX_PDF_DECODED_BYTES:
                        raise _PdfViolation(
                            "PDF decoded streams exceed their aggregate bound"
                        )
            elif isinstance(obj, ArrayObject):
                pending.extend((value, depth + 1) for value in obj)
            if len(pending) > MAX_PDF_OBJECTS:
                raise _PdfViolation("PDF object graph exceeds its pending-object bound")
        if not 1 <= pages_seen <= MAX_PDF_PAGES:
            raise _PdfViolation("PDF contains no bounded page content")
        extracted: list[str] = []
        text_size = 0

        def visitor(text: str, _cm: Any, _tm: Any, _font: Any, _size: Any) -> None:
            nonlocal text_size
            text_size += len(text)
            if text_size > MAX_PDF_TEXT:
                raise _PdfViolation("PDF extracted text exceeds its bound")

        page_count = 0
        returned_text_size = 0
        for page in reader.pages:
            page_count += 1
            if page_count > MAX_PDF_PAGES:
                raise _PdfViolation("PDF page count exceeds its bound")
            text = page.extract_text(visitor_text=visitor, extraction_mode="plain")
            returned_text_size += len(text)
            if returned_text_size > MAX_PDF_TEXT:
                raise _PdfViolation("PDF extracted text exceeds its bound")
            if not text.strip():
                raise _PdfViolation("PDF contains an empty page")
            extracted.append(text)
        expected_text = " ".join(
            " ".join(text.split())
            for text in projected_text_v2(projection)
            if text.strip()
        )
        actual_text = " ".join(" ".join(extracted).split())
        if actual_text != expected_text:
            raise _PdfViolation("PDF omitted, changed or reordered projected text")
        return ()
    except _PdfViolation as exc:
        return (str(exc),)
    except (
        PyPdfError,
        ZlibError,
        NotImplementedError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        RecursionError,
        OverflowError,
    ) as exc:
        return (
            f"PDF failed strict structure/content validation ({type(exc).__name__})",
        )


__all__ = ["pdf_contract_violations_v2", "projected_text_v2", "validate_stylesheet_v2"]
