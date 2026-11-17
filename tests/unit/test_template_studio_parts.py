"""Part-aware renderer and logic-less sections (ADR-0006 § 5h, § 5i).

Includes the proof that existing flat templates render byte-identically, and the
adversarial cases (size bounds, nesting, ReDoS timing, no re-interpretation).
"""

from __future__ import annotations

import re
import time
from uuid import uuid4

import pytest
from dotmac_kernel.models import Tenant
from dotmac_template_studio import TemplateSeed, seed_templates, service
from dotmac_template_studio.contexts import RenderContext, _reset_for_tests
from dotmac_template_studio.rendering import (
    HARD_MAX_LIST_ITEMS,
    MAX_LIST_ITEMS,
    MAX_RENDERED_CHARS,
    MissingTemplateValueError,
    RenderRefusedError,
    check_url,
    render,
    render_part,
)
from dotmac_template_studio.sections import TemplateSyntaxError, parse

CTX = RenderContext(
    name="digest",
    variables=("name", "who"),
    required=("link",),
    optional=("note",),
    url=("link", "item_url"),
    lists={"items": ("title", "item_url"), "tags": ("tag",)},
)


# ── Old templates render identically ────────────────────────────────────────

# The renderer as it was before this change, frozen here as the oracle.
_LEGACY = re.compile(r"(?<!\{)\{\s*([a-z][a-z0-9_]*)\s*\}(?!\})")


def _legacy_render(body: str, values: dict[str, str], *, strict: bool = True) -> str:
    missing: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        if match.group(1) in values:
            return values[match.group(1)]
        missing.append(match.group(1))
        return match.group(0)

    out = _LEGACY.sub(_sub, body)
    if strict and missing:
        raise MissingTemplateValueError("missing")
    return out


LEGACY_CORPUS = [
    "",
    "plain text, no placeholders",
    "Hello {name}",
    "Hello { name } and {  who  }!",
    "{name}{who}{name}",
    "{{name}} stays {name}",
    "{{{name}}} triple",
    "{name}} and {{name}",
    "{ Name } {1abc} {a-b} {} { } {name",
    "multi\nline {name}\r\nwith {who}\n",
    "unicode é中 {name} \U0001f600",
    "braces {  } { # } {|} {$x}",
    "{" * 50 + "name" + "}" * 50,
    "{name}" * 200,
    "<b>{name}</b> & {who}",
    "{{#x}} {{^x}} {{/x}} literal",
]
LEGACY_VALUES = {"name": "Jane <&> Doe", "who": "{name}"}


@pytest.mark.parametrize("body", LEGACY_CORPUS)
@pytest.mark.parametrize("strict", [True, False])
def test_flat_templates_render_byte_identically(body: str, strict: bool) -> None:
    values = LEGACY_VALUES
    try:
        expected: str | Exception = _legacy_render(body, values, strict=strict)
    except MissingTemplateValueError as exc:
        expected = exc
    legacy_public = None
    try:
        legacy_public = render(body, values, strict=strict)
    except MissingTemplateValueError:
        pass
    for part in ("text",):
        try:
            got: str | Exception = render_part(
                body,
                values,
                part,
                strict=strict,  # type: ignore[arg-type]
            )
        except MissingTemplateValueError as exc:
            got = exc
        if isinstance(expected, Exception):
            assert isinstance(got, Exception)
            assert legacy_public is None
        else:
            assert got == expected == legacy_public


def test_section_tokens_do_not_match_the_legacy_placeholder_pattern() -> None:
    from dotmac_template_studio.rendering import _PLACEHOLDER

    for token in ("{#a}", "{^a}", "{/a}", "{ # a }"):
        assert _PLACEHOLDER.search(token) is None
    assert _PLACEHOLDER.pattern == _LEGACY.pattern


def test_seeded_default_templates_render_identically(db) -> None:
    ctx = RenderContext(name="billing", variables=("customer_name", "invoice_number"))
    previous = _reset_for_tests([ctx])
    try:
        tenant = Tenant(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
        db.add(tenant)
        db.flush()
        seeds = (
            TemplateSeed(
                slug="invoice-issued",
                channel="email",
                context="billing",
                name="Invoice issued",
                subject="Invoice {invoice_number}",
                body=(
                    "Hi {customer_name}, invoice {invoice_number} is ready.\n"
                    "{ customer_name }"
                ),
            ),
            TemplateSeed(
                slug="invoice-issued",
                channel="sms",
                context="billing",
                name="SMS",
                body="Invoice { invoice_number } is ready.",
            ),
        )
        seed_templates(db, tenant.id, seeds)
        values = {"customer_name": "Jane", "invoice_number": "INV-1"}
        for seed in seeds:
            subject, body = service.render_published(
                db, tenant.id, seed.slug, seed.channel, values, strict=True
            )
            assert body == _legacy_render(seed.body, values)
            if seed.subject:
                assert subject == _legacy_render(seed.subject, values)
            else:
                assert subject is None
    finally:
        _reset_for_tests(previous.values())


# ── Escaping per part ───────────────────────────────────────────────────────


def test_text_part_is_raw() -> None:
    assert render_part("{name}", {"name": "<b>&\"'"}, "text") == "<b>&\"'"


def test_html_part_escapes_every_value() -> None:
    out = render_part("<p>{name}</p>", {"name": "<script>a&b\"'</script>"}, "html")
    assert out == "<p>&lt;script&gt;a&amp;b&quot;&#x27;&lt;/script&gt;</p>"


def test_html_template_markup_itself_is_not_escaped() -> None:
    assert render_part("<b>x</b>", {}, "html") == "<b>x</b>"


def test_html_url_value_is_attribute_escaped() -> None:
    out = render_part(
        '<a href="{link}">x</a>',
        {"link": 'https://example.com/?a=1&b="2"\'<'},
        "html",
        context=CTX,
    )
    assert out == (
        '<a href="https://example.com/?a=1&amp;b=&quot;2&quot;&#x27;&lt;">x</a>'
    )
    assert '"2"' not in out


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        " javascript:alert(1)",
        "java\tscript:alert(1)",
        "data:text/html;base64,AAAA",
        "vbscript:x",
        "file:///etc/passwd",
        "//evil.example/x",
        "/relative/path",
        "example.com",
        "",
        "https://",
        "https:///nohost",
        "mailto:",
        "https://ex ample.com",
        "https://example.com/\n",
        "https://example.com/\x00",
        "ftp://example.com/",
    ],
)
def test_url_scheme_refusal(value: str) -> None:
    with pytest.raises(RenderRefusedError):
        render_part("<a href='{link}'>", {"link": value}, "html", context=CTX)


@pytest.mark.parametrize(
    "value",
    ["https://example.com/a?b=c", "http://example.com", "MAILTO:a@example.com"],
)
def test_allowed_urls_pass(value: str) -> None:
    assert check_url(value) == value


def test_url_variable_in_a_list_item_is_checked_too() -> None:
    src = "{#items}<a href='{item_url}'>{title}</a>{/items}"
    ok = render_part(
        src,
        {"items": [{"title": "<T>", "item_url": "https://example.com/1"}]},
        "html",
        context=CTX,
    )
    assert ok == "<a href='https://example.com/1'>&lt;T&gt;</a>"
    with pytest.raises(RenderRefusedError):
        render_part(
            src,
            {"items": [{"title": "T", "item_url": "javascript:x"}]},
            "html",
            context=CTX,
        )


def test_url_values_are_not_scheme_checked_in_the_text_part() -> None:
    # Documented choice: the check guards HTML attributes only.
    assert (
        render_part("{link}", {"link": "ftp://example.com"}, "text", context=CTX)
        == "ftp://example.com"
    )


def test_non_string_values_are_refused() -> None:
    with pytest.raises(RenderRefusedError):
        render_part("{name}", {"name": 5}, "html")  # type: ignore[dict-item]


# ── Subject ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["a\nb", "a\rb", "a\r\nBcc: x@example.com"])
def test_subject_refuses_cr_and_lf_in_values(value: str) -> None:
    with pytest.raises(RenderRefusedError):
        render_part("Hi {name}", {"name": value}, "subject")


def test_subject_refuses_cr_lf_in_the_template_itself() -> None:
    with pytest.raises(RenderRefusedError):
        render_part("line1\nline2", {}, "subject")


def test_subject_values_are_raw_and_sections_are_refused() -> None:
    assert render_part("{name}", {"name": "A & <B>"}, "subject") == "A & <B>"
    with pytest.raises(TemplateSyntaxError):
        render_part("{#note}x{/note}", {}, "subject", context=CTX)


def test_unknown_part_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown template part"):
        render_part("x", {}, "pdf")  # type: ignore[arg-type]


# ── Sections ────────────────────────────────────────────────────────────────


def test_list_iterates_with_item_vocabulary() -> None:
    out = render_part(
        "{#items}[{title}]{/items}",
        {"items": [{"title": "A", "item_url": "x"}, {"title": "B", "item_url": "y"}]},
        "text",
        context=CTX,
    )
    assert out == "[A][B]"


def test_outer_scalars_are_visible_inside_a_list_and_items_shadow() -> None:
    out = render_part(
        "{#items}{name}:{title};{/items}",
        {"name": "Ada", "items": [{"title": "A", "item_url": "x"}]},
        "text",
        context=CTX,
    )
    assert out == "Ada:A;"


def test_only_the_declared_item_vocabulary_is_visible() -> None:
    with pytest.raises(MissingTemplateValueError, match="secret"):
        render_part(
            "{#items}{secret}{/items}",
            {"items": [{"title": "A", "item_url": "x", "secret": "s"}]},
            "text",
            context=CTX,
        )
    # An item lacking a declared variable is a strict miss, left literal otherwise.
    out = render_part(
        "{#items}{title}|{item_url}{/items}",
        {"items": [{"title": "A"}]},
        "text",
        context=CTX,
        strict=False,
    )
    assert out == "A|{item_url}"


def test_empty_and_absent_list_render_the_inverted_branch() -> None:
    src = "{#items}row{/items}{^items}none{/items}"
    assert render_part(src, {"items": []}, "text", context=CTX) == "none"
    assert render_part(src, {}, "text", context=CTX) == "none"
    assert (
        render_part(
            src, {"items": [{"title": "a", "item_url": "b"}]}, "text", context=CTX
        )
        == "row"
    )


def test_optional_scalar_renders_zero_or_one_time() -> None:
    src = "{#note}Note: {note}.{/note}{^note}No note.{/note}"
    assert render_part(src, {"note": "hi"}, "text", context=CTX) == "Note: hi."
    assert render_part(src, {}, "text", context=CTX) == "No note."
    assert render_part(src, {"note": ""}, "text", context=CTX) == "No note."


def test_optional_value_is_escaped_in_html() -> None:
    assert (
        render_part("{#note}<i>{note}</i>{/note}", {"note": "<x>"}, "html", context=CTX)
        == "<i>&lt;x&gt;</i>"
    )


def test_nesting_two_levels_renders_and_three_is_a_syntax_error() -> None:
    src = "{#items}{#note}{title}/{note}{/note}{/items}"
    out = render_part(
        src,
        {"note": "n", "items": [{"title": "A", "item_url": "x"}]},
        "text",
        context=CTX,
    )
    assert out == "A/n"
    with pytest.raises(TemplateSyntaxError, match="deeper than 2"):
        parse("{#a}{#b}{#c}x{/c}{/b}{/a}")


@pytest.mark.parametrize(
    "src",
    ["{#a}", "{/a}", "{#a}{/b}", "{#a}{#b}{/a}{/b}", "{#a}{#a}x{/a}{/a}"],
)
def test_unbalanced_tags_raise(src: str) -> None:
    with pytest.raises(TemplateSyntaxError):
        parse(src)


def test_undeclared_section_names_raise_at_render() -> None:
    with pytest.raises(TemplateSyntaxError, match="not a declared"):
        render_part("{#ghost}x{/ghost}", {}, "text", context=CTX)
    with pytest.raises(TemplateSyntaxError, match="not a declared"):
        render_part("{#name}x{/name}", {"name": "n"}, "text", context=CTX)
    with pytest.raises(TemplateSyntaxError):
        render_part("{#items}x{/items}", {}, "text")  # no context: no sections


def test_strict_reports_missing_scalar_and_non_strict_keeps_literal() -> None:
    with pytest.raises(MissingTemplateValueError, match="name"):
        render_part("{name}", {}, "html")
    assert render_part("{name}", {}, "html", strict=False) == "{name}"


def test_double_brace_section_tags_are_literal_text() -> None:
    assert render_part("{{#items}}x{{/items}}", {}, "text", context=CTX) == (
        "{{#items}}x{{/items}}"
    )


def test_whitespace_inside_tags_is_tolerated_like_placeholders() -> None:
    out = render_part("{ #note }{ note }{ /note }", {"note": "z"}, "text", context=CTX)
    assert out == "z"


# ── Adversarial ─────────────────────────────────────────────────────────────


def test_list_over_the_bound_is_refused_not_truncated() -> None:
    rows = [{"title": "t", "item_url": "https://e.com"}] * (MAX_LIST_ITEMS + 1)
    with pytest.raises(RenderRefusedError, match="at most"):
        render_part("{#items}{title}{/items}", {"items": rows}, "text", context=CTX)
    # exactly at the bound is fine
    ok = render_part(
        "{#items}x{/items}", {"items": rows[:MAX_LIST_ITEMS]}, "text", context=CTX
    )
    assert ok == "x" * MAX_LIST_ITEMS


def test_context_may_lower_the_bound_but_not_past_the_cap() -> None:
    small = RenderContext(name="s", lists={"l": ("x",)}, max_list_items=2)
    with pytest.raises(RenderRefusedError):
        render_part("{#l}{x}{/l}", {"l": [{"x": "1"}] * 3}, "text", context=small)
    with pytest.raises(ValueError):
        RenderContext(
            name="s", lists={"l": ("x",)}, max_list_items=HARD_MAX_LIST_ITEMS + 1
        )


def test_list_value_shape_is_enforced() -> None:
    for bad in ("string", b"bytes", 5, {"a": "b"}, [1], ["s"]):
        with pytest.raises(RenderRefusedError):
            render_part("{#items}{title}{/items}", {"items": bad}, "text", context=CTX)


def test_nested_lists_cannot_multiply_past_the_output_bound() -> None:
    ctx = RenderContext(name="m", lists={"a": ("x",), "b": ("y",)})
    rows = [{"x": "1", "y": "2"}] * MAX_LIST_ITEMS
    big = "Z" * 1000
    with pytest.raises(RenderRefusedError, match="exceeds"):
        render_part(
            "{#a}{#b}" + big + "{/b}{/a}", {"a": rows, "b": rows}, "text", context=ctx
        )
    assert MAX_RENDERED_CHARS < 100 * 100 * 1000


def test_deeply_nested_input_is_refused_quickly_not_recursed() -> None:
    src = "{#a}" * 5000 + "x" + "{/a}" * 5000
    start = time.perf_counter()
    with pytest.raises(TemplateSyntaxError):
        parse(src)
    assert time.perf_counter() - start < 1.0


@pytest.mark.parametrize(
    "src",
    [
        "{" + " " * 200_000,
        "{ " * 100_000,
        "{#" + " " * 200_000 + "a",
        "{" * 200_000,
        "{#a" * 100_000,
        "{name" * 100_000,
        "{" + "a" * 500_000,
        "{" + " " * 100_000 + "a" * 100_000 + " " * 100_000,
        "}" * 200_000 + "{{" * 100_000,
    ],
)
def test_pathological_input_parses_in_linear_time(src: str) -> None:
    start = time.perf_counter()
    nodes = parse(src)
    assert time.perf_counter() - start < 2.0
    assert nodes  # parsed as literal text, not an error


def test_values_are_never_reinterpreted_after_substitution() -> None:
    values = {
        "name": "{#items}{title}{/items}{who}{{name}}{/items}",
        "who": "{name}",
        "link": "https://example.com/{name}",
        "items": [
            {"title": "{#note}x{/note}{name}", "item_url": "https://e.com/{who}"}
        ],
    }
    out = render_part(
        "{name}|{who}|{#items}{title}{/items}", values, "text", context=CTX
    )
    assert out == (
        "{#items}{title}{/items}{who}{{name}}{/items}|{name}|{#note}x{/note}{name}"
    )
    html = render_part("{name}|{#items}{title}{/items}", values, "html", context=CTX)
    assert "{#items}" in html and "{#note}" in html
    # A hostile value with section syntax cannot open a section either.
    assert render_part("{name}", {"name": "{^note}x{/note}"}, "text", context=CTX) == (
        "{^note}x{/note}"
    )


def _best_time(make, n: int) -> float:
    best = float("inf")
    for _ in range(3):
        src = make(n)
        start = time.perf_counter()
        try:
            render_part(src, {"name": "x"}, "html")
        except RenderRefusedError:
            pass  # a refusal must scale linearly too, so time it
        best = min(best, time.perf_counter() - start)
    return best


@pytest.mark.parametrize(
    "make",
    [
        lambda n: "{name} " * n,
        lambda n: "<p>{name}</p>" * n,
        lambda n: "<" + "a" * n + ">",
        lambda n: "<a " + "b" * n + "=1>",
        lambda n: "<a " + "b=" + "c" * n + ">",
        lambda n: '<a title="' + "c" * n + '">',
    ],
    ids=[
        "flat",
        "tags",
        "long-tag-name",
        "long-attr-name",
        "long-unquoted",
        "long-quoted",
    ],
)
def test_html_render_time_scales_roughly_linearly(make) -> None:
    n = 40_000
    small = _best_time(make, n)
    large = _best_time(make, 4 * n)
    # Linear growth gives about 4x; quadratic would give about 16x.
    assert large < max(small, 0.005) * 9 + 0.05
