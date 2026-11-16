"""Review fixes on S1: item scope, html tag positions, subject controls, hashing."""

from __future__ import annotations

import pytest
from dotmac_template_studio.contexts import RenderContext
from dotmac_template_studio.rendering import (
    MAX_TEMPLATE_CHARS,
    MissingTemplateValueError,
    RenderRefusedError,
    check_url,
    render_part,
    subject_problem,
)
from dotmac_template_studio.validation import validate_revision

CTX = RenderContext(
    name="d",
    variables=("name", "email"),
    required=("link",),
    optional=("note",),
    url=("link", "item_url"),
    lists={"courses": ("title", "item_url", "contact")},
)


# ── Item scope never falls back to outer values ─────────────────────────────


def test_colliding_scalar_and_item_names_are_refused_at_declaration() -> None:
    with pytest.raises(ValueError, match="both as a scalar variable and as a list"):
        RenderContext(name="x", variables=("email",), lists={"c": ("email",)})
    with pytest.raises(ValueError, match="distinct names"):
        RenderContext(name="x", required=("email",), lists={"c": ("email",)})
    with pytest.raises(ValueError, match="distinct names"):
        RenderContext(name="x", optional=("email",), lists={"c": ("email",)})


def test_missing_item_variable_is_a_refusal_not_an_outer_value() -> None:
    values = {"name": "N", "contact": "outer@x", "courses": [{"title": "a"}]}
    with pytest.raises(MissingTemplateValueError, match="contact"):
        render_part("{#courses}[{contact}]{/courses}", values, "text", context=CTX)
    out = render_part(
        "{#courses}[{contact}]{/courses}", values, "text", context=CTX, strict=False
    )
    assert out == "[{contact}]"


def test_item_variable_outside_a_list_is_never_read_from_values() -> None:
    with pytest.raises(MissingTemplateValueError):
        render_part("{title}", {"title": "x"}, "text", context=CTX)


# ── Placeholders inside HTML tags ───────────────────────────────────────────

BAD_HTML = [
    '<a href="{name}">x</a>',
    "<a href='{name}'>x</a>",
    "<a href={name}>x</a>",
    '<img src="{name}">',
    '<form action="{name}">',
    '<button formaction="{name}">',
    '<p style="{name}">',
    '<img srcset="{name}">',
    '<p onclick="{name}">',
    '<p title="{name}">',
    "<p {name}>",
    "<{name}>",
    "<a {name}=1>",
    "<p>x</p><!-- {name} -->",
    "<script>var a = '{name}';</script>",
    "<style>p{{ color: {name} }}</style>",
    '<a href="x{link}">',
    '<a href="{link}x">',
    '<a href="{link}{link}">',
    '<p onclick="{link}">',
    '<a title="{link}">',
    "<a href={link}>",
    '<A HREF="{name}">',
    "<a\nhref\n=\n'{name}'>",
    '<a href="{#note}x{/note}{name}">',
    '<a {#note}href="x"{/note}>',
]


@pytest.mark.parametrize("src", BAD_HTML)
def test_unsafe_html_positions_are_refused_at_render(src: str) -> None:
    values = {"name": "javascript:alert(1)", "link": "https://example.com", "note": "n"}
    with pytest.raises(RenderRefusedError):
        render_part(src, values, "html", context=CTX)


@pytest.mark.parametrize("src", BAD_HTML)
def test_unsafe_html_positions_are_refused_at_save(src: str) -> None:
    problems = validate_revision(CTX, text="{link}", html=src)
    assert any(p.startswith("html:") for p in problems), problems


@pytest.mark.parametrize(
    "src",
    [
        "<p>{name}</p>",
        "text {name} text",
        '<a href="{link}">{name}</a>',
        "<a class='x' href='{link}' title='fixed'>{name}</a>",
        '<img src="{link}" alt="fixed">',
        "<p>a < b {name}</p>",
        "<!-- note --><p>{name}</p>",
        "<script>1</script><p>{name}</p>",
        '<ul>{#courses}<li><a href="{item_url}">{title}</a></li>{/courses}</ul>',
    ],
)
def test_safe_html_positions_pass(src: str) -> None:
    assert [
        p
        for p in validate_revision(CTX, text="{link}", html=src)
        if "html" in p and "required" not in p
    ] == [], src
    render_part(
        src,
        {
            "name": "n",
            "link": "https://example.com",
            "courses": [{"title": "t", "item_url": "https://e.com", "contact": "c"}],
        },
        "html",
        context=CTX,
    )


def test_section_that_changes_tag_state_is_refused() -> None:
    problems = validate_revision(
        CTX, text="{link}", html='<a {#note}href="{link}"{/note}>'
    )
    assert any("changes the HTML tag state" in p for p in problems)


# ── Subject controls ────────────────────────────────────────────────────────

SUBJECT_BAD = [
    "\n",
    "\r",
    "\v",
    "\f",
    "\x85",
    "\u2028",
    "\u2029",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x00",
    "\x7f",
    "\t",
    "\x1b",
]


@pytest.mark.parametrize("ch", SUBJECT_BAD)
def test_subject_refuses_every_break_and_control_in_values_and_source(ch: str) -> None:
    with pytest.raises(RenderRefusedError):
        render_part("Hi {name}", {"name": "a" + ch + "b"}, "subject", context=CTX)
    with pytest.raises(RenderRefusedError):
        render_part("Hi" + ch + "there", {}, "subject", context=CTX)
    assert subject_problem("a" + ch) is not None
    problems = validate_revision(CTX, subject="Hi" + ch + "x", text="{link}")
    assert any(p.startswith("subject:") for p in problems), repr(ch)


def test_ordinary_subject_is_fine() -> None:
    assert subject_problem("Resume ok - 100%") is None


# ── URL characters and length bound ─────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "https://exa​mple.com",
        "https://example.com/‮",
        "https://a.com/\x9b",
        "https://a.com/­",
        "https://a.com/﻿",
    ],
)
def test_url_refuses_invisible_format_and_c1_characters(value: str) -> None:
    with pytest.raises(RenderRefusedError):
        check_url(value)


def test_oversized_source_is_refused_at_save_and_render() -> None:
    big = "x" * (MAX_TEMPLATE_CHARS + 1)
    assert any("longer than" in p for p in validate_revision(CTX, text=big))
    with pytest.raises(RenderRefusedError):
        render_part(big, {}, "text", context=CTX)


# ── Hashing ─────────────────────────────────────────────────────────────────


def test_contexts_are_hashable_legacy_and_v2() -> None:
    legacy = RenderContext(name="l", variables=("a",))
    v2 = RenderContext(
        name="v",
        variables=("a",),
        url=("u",),
        optional=("u",),
        lists={"l": ("x", "y")},
        sample={"a": "1", "l": [{"x": "1"}]},
    )
    assert hash(legacy) == hash(RenderContext(name="l", variables=("a",)))
    assert hash(v2) == hash(v2)
    assert len({legacy, v2, RenderContext(name="l", variables=("a",))}) == 2
