"""Decoding bytes into rows, and resolving headings onto declared fields.

Ported from ERP's `finance/import_export/base.py` — its alias resolution,
auto-mapping and preview, and Sub's row ceiling — with the accounting
vocabulary left behind in ERP where it belongs (ADR-0025 § 5).

Pure: no database, no file access, no domain. Callers hand in bytes they
already fetched through `dotmac-files`.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator, Mapping, Sequence

from dotmac_imports.contracts import (
    DEFAULT_MAX_ROWS,
    ColumnMapping,
    FieldSet,
    MalformedSource,
    Preview,
    SourceDocument,
    SourceLayout,
    UnmappedRequiredField,
    normalize_column,
)

# How many decoded rows a preview shows an operator. Enough to see that a
# mapping is right, small enough that a preview never becomes an import.
PREVIEW_ROWS: int = 5

# Excel and Numbers write a UTF-8 BOM into exported CSV, which lands on the
# first heading and silently makes it a column nothing matches. `utf-8-sig`
# strips it when present and is identical to `utf-8` when it is not.
_BOM_TOLERANT = {"utf-8": "utf-8-sig", "utf8": "utf-8-sig"}


def decode(
    data: bytes, *, source: SourceDocument, max_rows: int = DEFAULT_MAX_ROWS
) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    """Decode stored bytes into headings and raw rows.

    Only `SourceLayout.CSV` is decodable in this release. A spreadsheet decoder
    is a separate adapter that arrives with the first adopter whose environment
    already carries the library and whose parity tests come with it; refusing
    loudly is better than shipping an untested one.
    """
    if source.layout is not SourceLayout.CSV:
        raise MalformedSource(
            f"layout {source.layout.value!r} has no decoder in this release — "
            "only CSV is decodable; a spreadsheet decoder ships with the "
            "adopter that needs it"
        )
    return decode_csv(
        data,
        delimiter=source.delimiter,
        encoding=source.encoding,
        max_rows=max_rows,
    )


def decode_csv(
    data: bytes,
    *,
    delimiter: str = ",",
    encoding: str = "utf-8",
    max_rows: int = DEFAULT_MAX_ROWS,
) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    """Headings and rows, with every value a string.

    Coercion is the domain's job: a validator that receives `"0012"` can decide
    it is an account code, and one that receives an int cannot decide it was
    ever anything else.
    """
    if max_rows <= 0:
        raise MalformedSource("max_rows must be positive")
    try:
        text = data.decode(_BOM_TOLERANT.get(encoding.lower(), encoding))
    except (UnicodeDecodeError, LookupError) as exc:
        raise MalformedSource(f"cannot decode source as {encoding}: {exc}") from exc

    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        headings = next(reader)
    except StopIteration:
        raise MalformedSource("source has no header row") from None
    except csv.Error as exc:
        raise MalformedSource(f"cannot read source as CSV: {exc}") from exc

    columns = tuple(heading.strip() for heading in headings)
    if not any(columns):
        raise MalformedSource("source header row is empty")
    duplicates = _duplicates(normalize_column(column) for column in columns if column)
    if duplicates:
        raise MalformedSource(
            f"duplicate heading(s) {sorted(duplicates)} — two columns that "
            "normalise to one name make every mapped value ambiguous"
        )

    rows: list[dict[str, str]] = []
    try:
        for values in reader:
            if len(values) > len(columns):
                raise MalformedSource(
                    f"row {reader.line_num} has {len(values)} cells for "
                    f"{len(columns)} headings — extra cells cannot be "
                    "silently discarded"
                )
            if not any(value.strip() for value in values):
                continue
            if len(rows) >= max_rows:
                raise MalformedSource(
                    f"source exceeds the {max_rows}-row ceiling for one run"
                )
            rows.append(
                {
                    column: (values[index].strip() if index < len(values) else "")
                    for index, column in enumerate(columns)
                    if column
                }
            )
    except csv.Error as exc:
        raise MalformedSource(f"cannot read source as CSV: {exc}") from exc
    return columns, tuple(rows)


def auto_map(columns: Sequence[str], fields: FieldSet) -> ColumnMapping:
    """Resolve source headings onto declared fields by name and alias.

    A heading that resolves to nothing is simply unmapped — not an error. A
    source system exporting twelve columns when the domain declares four is the
    normal case, not a malformed file.
    """
    pairs: list[tuple[str, str]] = []
    taken: set[str] = set()
    for column in columns:
        if not column:
            continue
        field = fields.field_for(column)
        if field is None or field in taken:
            continue
        taken.add(field)
        pairs.append((field, column))
    return ColumnMapping(tuple(sorted(pairs)))


def preview(
    columns: Sequence[str],
    rows: Sequence[Mapping[str, str]],
    fields: FieldSet,
    *,
    mapping: ColumnMapping | None = None,
) -> Preview:
    """What an operator confirms before a run exists.

    Reports what is unmapped and what is missing rather than refusing: the
    operator's next action is to correct the mapping, and a refusal with no
    inventory of the problem makes that guesswork.
    """
    resolved = mapping if mapping is not None else auto_map(columns, fields)
    named = {column for column in columns if column}
    unknown = tuple(
        sorted(column for _, column in resolved.pairs if column not in named)
    )
    if unknown:
        raise UnmappedRequiredField(
            f"mapping names source column(s) {list(unknown)} that the document "
            "does not have"
        )
    mapped_columns = {column for _, column in resolved.pairs}
    return Preview(
        columns=tuple(columns),
        mapping=resolved,
        unmapped_columns=tuple(
            column for column in columns if column and column not in mapped_columns
        ),
        missing_required_fields=tuple(sorted(fields.required_names - resolved.fields)),
        sample=tuple(
            tuple(sorted(apply_mapping(row, resolved).items()))
            for row in rows[:PREVIEW_ROWS]
        ),
    )


def apply_mapping(row: Mapping[str, str], mapping: ColumnMapping) -> dict[str, str]:
    """One raw row projected onto declared field names."""
    return {field: row.get(column, "") for field, column in mapping.pairs}


def mapped_rows(
    rows: Sequence[Mapping[str, str]], mapping: ColumnMapping, fields: FieldSet
) -> Iterator[dict[str, str]]:
    """Every row projected onto declared fields, required fields checked once.

    Checked here rather than per row because a missing required *field* is a
    property of the mapping: reporting it as N row errors would bury one
    operator mistake under one error per line of the file.
    """
    missing = fields.required_names - mapping.fields
    if missing:
        raise UnmappedRequiredField(
            f"required field(s) {sorted(missing)} have no source column"
        )
    for row in rows:
        yield apply_mapping(row, mapping)


def _duplicates(values: Iterator[str]) -> set[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for value in values:
        if value in seen:
            repeated.add(value)
        seen.add(value)
    return repeated


__all__ = [
    "PREVIEW_ROWS",
    "apply_mapping",
    "auto_map",
    "decode",
    "decode_csv",
    "mapped_rows",
    "preview",
]
