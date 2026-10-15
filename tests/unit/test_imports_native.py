"""Native row transformation preserves the source-backed CSV contract."""

from __future__ import annotations

import csv
import io
import random

import pytest
from dotmac_imports import _native
from dotmac_imports.contracts import (
    ColumnMapping,
    FieldSet,
    FieldSpec,
    MalformedSource,
    SourceDocument,
    SourceLayout,
    UnmappedRequiredField,
)
from dotmac_imports.tabular import _decode_mapped, decode_csv


def test_native_keeps_codes_short_rows_and_python_whitespace() -> None:
    assert _native.normalize_rows(
        3,
        [(2, [b" 0012 ", b"unused", "\x1c Ada \u00a0".encode()]), (3, [b"9"])],
        2,
        [0, 2],
    ) == [[b"0012", b"Ada"], [b"9", b""]]


def test_extra_cells_refused_before_blank_row_filter() -> None:
    with pytest.raises(ValueError, match="row 4 has 2 cells for 1 headings"):
        _native.normalize_rows(1, [(4, [b"", b""])], 0, [0])


def test_blank_rows_do_not_consume_ceiling() -> None:
    assert _native.normalize_rows(1, [(2, [b"\x1f\t"]), (3, [b"x"])], 1, [0]) == [
        [b"x"]
    ]
    with pytest.raises(MalformedSource, match="1-row ceiling"):
        decode_csv(b"a\nx\n \ny\n", max_rows=1)


def test_legacy_csv_grammar_is_retained() -> None:
    columns, rows = decode_csv(b'a,b\r\n"first\r\nsecond",0012\r\n"x"tail,z')
    assert columns == ("a", "b")
    assert rows == ({"a": "first\r\nsecond", "b": "0012"}, {"a": "xtail", "b": "z"})


def test_first_invalid_row_precedes_later_parser_error() -> None:
    previous = csv.field_size_limit(8)
    try:
        with pytest.raises(MalformedSource, match="row 2 has 2 cells"):
            decode_csv(b"a\nx,y\n0123456789\n")
        with pytest.raises(MalformedSource, match="1-row ceiling"):
            decode_csv(b"a\nx\ny\n0123456789\n", max_rows=1)
        with pytest.raises(MalformedSource, match="field larger than field limit"):
            decode_csv(b"a\nx\n0123456789\n")
    finally:
        csv.field_size_limit(previous)


def test_all_python_whitespace_and_surrogate_text_are_preserved() -> None:
    whitespace = "".join(chr(i) for i in range(0x3100) if chr(i).isspace())
    source = whitespace + "\ud800" + whitespace
    encoded = source.encode("utf-8", "surrogatepass")
    result = _native.normalize_rows(1, [(2, [encoded])], 1, [0])
    assert result == [[b"\xed\xa0\x80"]]
    assert decode_csv(b"a\n\\ud800", encoding="unicode_escape")[1] == ({"a": "\ud800"},)


def test_verified_source_mapping_is_fused_without_raw_row_dictionaries() -> None:
    from uuid import uuid4

    source = SourceDocument(
        file_id=uuid4(),
        checksum_sha256="a" * 64,
        layout=SourceLayout.CSV,
    )
    fields = FieldSet((FieldSpec("account", required=True), FieldSpec("label")))
    mapping = ColumnMapping((("account", "Code"), ("label", "Missing")))
    assert _decode_mapped(
        b"Code,Unused\n0012,discard\n", source=source, mapping=mapping, fields=fields
    ) == ({"account": "0012", "label": ""},)
    missing = ColumnMapping((("label", "Code"),))
    with pytest.raises(MalformedSource, match="row 2 has 3 cells"):
        _decode_mapped(
            b"Code,Unused\nx,y,z\n", source=source, mapping=missing, fields=fields
        )
    with pytest.raises(UnmappedRequiredField, match="account"):
        _decode_mapped(b"Code\nx\n", source=source, mapping=missing, fields=fields)


def test_deterministic_generated_row_parity() -> None:
    # Independent reference reproduces the existing product's simple projection,
    # with adversarial Unicode/control whitespace and actual CSV wire quoting.
    rng = random.Random(491)  # noqa: S311 - deterministic parity corpus, no secrets
    choices = ["", "0012", " x ", "\x1c\u00a0", "a,b", 'a"b', "a\nb", "é"]
    raw = [
        [rng.choice(choices) for _ in range(rng.randrange(1, 4))] for _ in range(257)
    ]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["a", "", "c"])
    writer.writerows(raw)
    expected = tuple(
        {
            column: (values[i].strip() if i < len(values) else "")
            for i, column in enumerate(("a", "", "c"))
            if column
        }
        for values in raw
        if any(value.strip() for value in values)
    )
    assert decode_csv(stream.getvalue().encode())[1] == expected
