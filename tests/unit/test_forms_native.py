"""Bulk structural validation and ordered Python parser ownership."""

from types import SimpleNamespace

import pytest
from dotmac_forms._native import review_answers as native_review_answers
from dotmac_forms.service import FormValidationError, _review_answers


def review_answers(fields):
    results = native_review_answers(
        [
            (
                key,
                kind,
                required,
                tag,
                text.encode("utf-8", "surrogatepass"),
                [item.encode("utf-8", "surrogatepass") for item in items],
                [
                    (
                        value.encode("utf-8", "surrogatepass"),
                        label.encode("utf-8", "surrogatepass"),
                    )
                    for value, label in options
                ],
            )
            for key, kind, required, tag, text, items, options in fields
        ]
    )
    return [
        (
            tag,
            text.decode("utf-8", "surrogatepass"),
            [item.decode("utf-8", "surrogatepass") for item in items],
            display.decode("utf-8", "surrogatepass") if display is not None else None,
            file.decode("utf-8", "surrogatepass") if file is not None else None,
        )
        for tag, text, items, display, file in results
    ]


def field(kind, value_tag, text="", items=(), required=False, options=()):
    return ("answer", kind, required, value_tag, text, list(items), list(options))


def test_blank_required_and_python_whitespace():
    results = review_answers(
        [
            field("text", "none", required=True),
            field("text", "text", ""),
            field("text", "text", "\x1c\x1d\x1e\x1f\u2003hello\u0085"),
            field("text", "text", "  ", required=True),
            field("file", "text", "\x1c"),
        ]
    )
    assert results == [
        ("error", "answer is required", [], None, None),
        ("blank", "", [], None, None),
        ("text", "hello", [], "hello", None),
        ("text", "", [], "", None),
        ("error", "answer must be an opaque file reference", [], None, None),
    ]


def test_choice_order_boolean_rating_and_parser_tags():
    options = [("b", "Bee"), ("a", "Aye")]
    results = review_answers(
        [
            field("multi_choice", "tuple", items=("b", "a", "b"), options=options),
            field("single_choice", "text", " a", options=options),
            field("checkbox", "bool", "false", required=True),
            field("rating", "bool", "true"),
            field("rating", "int", "5"),
            field("rating", "int", str(10**100)),
            field("date", "int", "123"),
        ]
    )
    assert results == [
        ("multi", "", ["b", "a"], "Bee, Aye", None),
        ("error", "answer must name a declared option", [], None, None),
        ("bool", "false", [], "No", None),
        ("error", "answer must be a rating from 1 to 5", [], None, None),
        ("rating", "5", [], "5", None),
        ("error", "answer must be a rating from 1 to 5", [], None, None),
        ("parser", "", [], None, None),
    ]


def test_earlier_python_parser_error_wins_over_later_native_error():
    fields = [
        SimpleNamespace(key="email", field_type="email", required=False, id=1),
        SimpleNamespace(key="choice", field_type="single_choice", required=True, id=2),
    ]
    with pytest.raises(FormValidationError, match="email must be a valid email"):
        _review_answers(fields, ["invalid", "unknown"], {})
    fields.reverse()
    with pytest.raises(FormValidationError, match="choice must name a declared option"):
        _review_answers(fields, ["unknown", "invalid"], {})


def test_preserved_python_number_date_url_and_email_parsers():
    fields = [
        SimpleNamespace(key=kind, field_type=kind, required=False, id=index)
        for index, kind in enumerate(["number", "date", "url", "email"])
    ]
    result = _review_answers(
        fields,
        ["1.2300", "20261015", " https://example.com/a ", " USER@Example.COM "],
        {},
    )
    assert [(answer.value, answer.display) for answer in result] == [
        ("1.2300", "1.2300"),
        ("2026-10-15", "2026-10-15"),
        ("https://example.com/a", "https://example.com/a"),
        ("user@example.com", "user@example.com"),
    ]


def test_lone_surrogates_round_trip_in_text_choices_and_labels():
    results = review_answers(
        [
            field("text", "text", " \ud800 "),
            field(
                "single_choice", "text", "\ud800", options=[("\ud800", "label\udfff")]
            ),
        ]
    )
    assert results == [
        ("text", "\ud800", [], "\ud800", None),
        ("text", "\ud800", [], "label\udfff", None),
    ]


def test_native_output_uses_bytes_for_every_text_payload():
    result = native_review_answers(
        [
            ("text", "file", False, "text", b" ref ", [], []),
            ("multi", "multi_choice", False, "tuple", b"", [b"a"], [(b"a", b"A")]),
            ("blank", "text", False, "none", b"", [], []),
        ]
    )
    assert result == [
        ("text", b"ref", [], b"ref", b"ref"),
        ("multi", b"", [b"a"], b"A", None),
        ("blank", b"", [], None, None),
    ]
    for _, text, items, display, file_ref in result:
        assert isinstance(text, bytes)
        assert all(isinstance(item, bytes) for item in items)
        assert display is None or isinstance(display, bytes)
        assert file_ref is None or isinstance(file_ref, bytes)
