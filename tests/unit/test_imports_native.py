"""Native row transformation preserves the source-backed CSV contract."""

from __future__ import annotations

import csv
import io
import random

import pytest

from dotmac_imports import _native
from dotmac_imports.contracts import MalformedSource
from dotmac_imports.tabular import decode_csv


def test_native_keeps_codes_short_rows_and_python_whitespace() -> None:
    assert _native.normalize_rows(
        ["code", "", "name"],
        [(2, [" 0012 ", "unused", "\x1c Ada \u00a0"]), (3, ["9"])],
        2,
    ) == [[("code", "0012"), ("name", "Ada")], [("code", "9"), ("name", "")]]


def test_extra_cells_refused_before_blank_row_filter() -> None:
    with pytest.raises(ValueError, match="row 4 has 2 cells for 1 headings"):
        _native.normalize_rows(["a"], [(4, ["", ""])], 0)


def test_blank_rows_do_not_consume_ceiling() -> None:
    assert _native.normalize_rows(["a"], [(2, ["\x1f\t"]), (3, ["x"])], 1) == [
        [("a", "x")]
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


def test_deterministic_generated_row_parity() -> None:
    # Independent reference reproduces the existing product's simple projection,
    # with adversarial Unicode/control whitespace and actual CSV wire quoting.
    rng = random.Random(491)
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
