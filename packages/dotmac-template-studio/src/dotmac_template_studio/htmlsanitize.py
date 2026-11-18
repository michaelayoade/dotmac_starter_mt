"""The html part's sanitizer (ADR-0006 § 5h).

A save-time gate, layered AFTER the placeholder scanner in `htmlctx`: the scanner
decides where a `{placeholder}` may sit, this module decides what markup a
tenant-authored html part may contain at all. It is allowlist-first and
FAIL-CLOSED: the sanitized html is what is stored, and if sanitizing changed
anything the revision is REFUSED with the list of what was removed, so an
operator sees why rather than finding that the email they sent differs from the
one they wrote.

## The allowlist (the whole of it; there is no per-tenant or per-call widening)

- Tags: `ALLOWED_TAGS`.
- Attributes: `ALLOWED_ATTRIBUTES` (per tag, plus `*`), each value shape-checked
  by `_attribute_filter`: `href` is a lone `{placeholder}` or an absolute
  `http`/`https`/`mailto` URL, `src` a lone placeholder or an `http`/`https`
  URL; `style` is checked declaration by declaration; the rest are numeric,
  enumerated or plain text.
- Inline-style properties: `ALLOWED_STYLE_PROPERTIES`; a value may contain only
  letters, digits and a few punctuation marks, quoted font names and
  `rgb()/rgba()/hsl()/hsla()`. No `url(`, `expression`, `behavior`, `@import`,
  backslash escape, comment or any other function, and no `position`.
- URL schemes: `ALLOWED_URL_SCHEMES` (`http`, `https`, `mailto`). Relative and
  protocol-relative URLs, fragments, `data:`, `javascript:`, `vbscript:` are
  refused.

Raw-text and embedding elements are dropped WITH their content
(`CLEAN_CONTENT_TAGS`); every other unlisted tag is dropped keeping its text.
Either way the difference is reported and the revision refused.

## Sections and the HTML parser

`{#x}` / `{^x}` / `{/x}` section tags are text to the sanitizer, but an HTML
parser moves text found directly inside `<table>`, `<tbody>` or `<tr>` out in
front of the table (foster parenting), which would silently break the very common
"one `<tr>` per list item" layout. So each section tag is swapped for a
placeholder COMMENT (`<!--ts<n>-->`) before cleaning, comments being legal in
those positions, and swapped back afterwards. Any comment, declaration or
processing instruction in the SOURCE is refused up front, so every comment in the
cleaned output is one this module wrote; the swap-back refuses if the counts do
not reconcile and if any `<!--` is left over.

## What "unchanged" means

The cleaned html is re-serialised by the parser (attribute quoting, `<br/>`
spelling, an implied `<tbody>`), so it is not compared byte for byte. Source and
output are both reduced to a canonical event stream (tags, attributes with
decoded values, merged decoded text, normalised `style`) and compared; any
difference, other than an implied `<tbody>`, is a refusal. The stored value is
always the sanitizer's OUTPUT, never the source: the comparison only decides
whether to refuse, so a mistake in it can wrongly refuse or wrongly accept a
re-serialisation, never store unsanitized markup.

## Not done here, and why

Rendered output is NOT re-sanitized at send time. Substitution only inserts
`html.escape`d text, or a `check_url`-validated, attribute-escaped URL as the
whole value of a quoted `href`/`src` (enforced by `htmlctx` at save and render),
and the stored skeleton is already sanitizer output. Re-cleaning the substituted
result would rewrite legitimate bytes (`&#x27;` becomes `'`, an implied `<tbody>`
is inserted) without closing any hole the placeholder rules leave open.

Importing this module needs the third-party `nh3` package; `rendering.py`,
`sections.py` and `htmlctx.py` stay standard-library only.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left
from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

import nh3

from dotmac_template_studio.htmlctx import ACCEPTED_DOCTYPES
from dotmac_template_studio.rendering import (
    RenderRefusedError,
    bidi_control,
    check_url,
)
from dotmac_template_studio.sections import _TOKEN

HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")

ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "a",
        "b",
        "strong",
        "i",
        "em",
        "u",
        "s",
        "small",
        "sub",
        "sup",
        "br",
        "hr",
        "p",
        "div",
        "span",
        "blockquote",
        "pre",
        "code",
        "ul",
        "ol",
        "li",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "td",
        "th",
        "caption",
        "img",
        *HEADING_TAGS,
    }
)

ALLOWED_ATTRIBUTES: Mapping[str, frozenset[str]] = {
    "*": frozenset({"style", "title", "lang", "dir"}),
    "a": frozenset({"href"}),
    "img": frozenset({"src", "alt", "width", "height"}),
    "table": frozenset({"width", "border", "cellpadding", "cellspacing", "align"}),
    "tr": frozenset({"align", "valign"}),
    "td": frozenset({"width", "height", "align", "valign", "colspan", "rowspan"}),
    "th": frozenset({"width", "height", "align", "valign", "colspan", "rowspan"}),
}

ALLOWED_STYLE_PROPERTIES: frozenset[str] = frozenset(
    {
        "color",
        "background-color",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "line-height",
        "text-align",
        "text-decoration",
        "text-transform",
        "letter-spacing",
        "vertical-align",
        "width",
        "max-width",
        "height",
        "margin",
        "margin-top",
        "margin-right",
        "margin-bottom",
        "margin-left",
        "padding",
        "padding-top",
        "padding-right",
        "padding-bottom",
        "padding-left",
        "border",
        "border-top",
        "border-right",
        "border-bottom",
        "border-left",
        "border-color",
        "border-style",
        "border-width",
        "border-collapse",
        "border-spacing",
        "border-radius",
        "list-style-type",
    }
)

ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})
# `src` is narrower than `href`: a mailto image is meaningless.
_SRC_SCHEMES = frozenset({"http", "https"})

# Dropped together with their content. Disjoint from `ALLOWED_TAGS`.
CLEAN_CONTENT_TAGS: frozenset[str] = frozenset(
    {
        "script",
        "style",
        "iframe",
        "frame",
        "frameset",
        "object",
        "embed",
        "applet",
        "noscript",
        "noembed",
        "noframes",
        "template",
        "textarea",
        "title",
        "xmp",
        "plaintext",
        "svg",
        "math",
        "select",
        "option",
    }
)

_VOID = frozenset({"br", "hr", "img"})
_LEADING_SPACE = " \t\r\n\f"

_PLACEHOLDER_ONLY = re.compile(r"^\{\s*[a-z][a-z0-9_]*\s*\}$")
_NUMBER = re.compile(r"^[0-9]{1,4}%?$")
_LANG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,3}$")
_ENUMS: Mapping[str, frozenset[str]] = {
    "align": frozenset({"left", "right", "center", "justify"}),
    "valign": frozenset({"top", "middle", "bottom", "baseline"}),
    "dir": frozenset({"ltr", "rtl", "auto"}),
}
_NUMERIC = frozenset(
    {"width", "height", "border", "cellpadding", "cellspacing", "colspan", "rowspan"}
)
_MAX_PROBLEMS = 20
_MAX_TEXT_ATTR = 500
_MAX_STYLE = 2000

_STYLE_VALUE = re.compile(
    r"""^(?:
        [A-Za-z0-9#%.,\-+ ]
      | (?:rgb|rgba|hsl|hsla)\([0-9 ,.%/\-]*\)
      | '[A-Za-z0-9 \-]*'
      | "[A-Za-z0-9 \-]*"
    )+$""",
    re.VERBOSE,
)
_STYLE_BANNED = (
    "url",
    "expression",
    "behavior",
    "binding",
    "import",
    "javascript",
    "vbscript",
)


class HtmlSanitizationRefused(ValueError):
    """The html part is not what the sanitizer would store; nothing was stored."""


@dataclass(frozen=True, slots=True)
class SanitizedHtml:
    """The sanitizer's output and what it had to change to get there.

    `problems` is empty exactly when the source was already clean; `html` is then
    safe to store. When it is not empty, callers REFUSE rather than store.
    """

    html: str
    problems: tuple[str, ...]


def _has_forbidden_chars(value: str) -> bool:
    return any(
        ord(ch) < 0x20 or unicodedata.category(ch) in ("Cc", "Cf") for ch in value
    )


def _style_ok(value: str) -> bool:
    if len(value) > _MAX_STYLE or "/*" in value or "\\" in value:
        return False
    declarations = [d for d in value.split(";") if d.strip()]
    if not declarations:
        return False
    for declaration in declarations:
        prop, sep, val = declaration.partition(":")
        if not sep or ":" in val:
            return False
        val = val.strip()
        lowered = val.lower()
        if not val or not prop.strip() or not _STYLE_VALUE.match(val):
            return False
        if any(token in lowered for token in _STYLE_BANNED):
            return False
        if "(" in val and not re.fullmatch(
            r"(?:[^()]|(?:rgb|rgba|hsl|hsla)\([^()]*\))*", val, re.IGNORECASE
        ):
            return False
    return True


def _url_ok(value: str, *, schemes: frozenset[str]) -> bool:
    if _PLACEHOLDER_ONLY.match(value):
        return True
    if any(ch in value for ch in "{}<>\"'`\\"):
        return False
    try:
        check_url(value)
    except RenderRefusedError:
        return False
    return value.split(":", 1)[0].lower() in schemes


def _attribute_filter(tag: str, attr: str, value: str) -> str | None:
    """Per-attribute value check; `None` drops the attribute.

    Runs on the DECODED value, so entity-encoded and control-split schemes are
    seen as the browser would see them.
    """
    if attr == "style":
        return value if _style_ok(value) else None
    if attr == "href":
        return value if _url_ok(value, schemes=ALLOWED_URL_SCHEMES) else None
    if attr == "src":
        return value if _url_ok(value, schemes=_SRC_SCHEMES) else None
    if attr in _NUMERIC:
        return value if _NUMBER.match(value) else None
    if attr in _ENUMS:
        return value if value.lower() in _ENUMS[attr] else None
    if attr == "lang":
        return value if _LANG.match(value) else None
    if attr in ("title", "alt"):
        if len(value) > _MAX_TEXT_ATTR or _has_forbidden_chars(value):
            return None
        return None if ("<" in value or ">" in value) else value
    return None


_CLEANER = nh3.Cleaner(
    tags=set(ALLOWED_TAGS),
    clean_content_tags=set(CLEAN_CONTENT_TAGS),
    attributes={tag: set(attrs) for tag, attrs in ALLOWED_ATTRIBUTES.items()},
    attribute_filter=_attribute_filter,
    strip_comments=False,
    link_rel=None,
    url_schemes=set(ALLOWED_URL_SCHEMES),
    filter_style_properties=set(ALLOWED_STYLE_PROPERTIES),
)


# ── Canonical comparison ────────────────────────────────────────────────────


def _style_canonical(value: str) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for declaration in value.split(";"):
        prop, sep, val = declaration.partition(":")
        if sep and prop.strip():
            norm = " ".join(val.split()).lower().replace("'", '"')
            pairs.append((prop.strip().lower(), re.sub(r"\s*,\s*", ",", norm)))
    return tuple(pairs)


class _Canonical(HTMLParser):
    """Tags, first-wins attributes, merged decoded text; nothing else."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.events: list[tuple[Any, ...]] = []
        self._text: list[str] = []

    def _flush(self) -> None:
        if self._text:
            text = "".join(self._text).replace("\r\n", "\n").replace("\r", "\n")
            if text:
                self.events.append(("t", text))
            self._text = []

    def handle_data(self, data: str) -> None:
        self._text.append(data)

    def _start(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._flush()
        if tag == "tbody":
            return
        seen: dict[str, object] = {}
        for name, value in attrs:
            name = name.lower()
            if name in seen:
                # html5 keeps the first and silently drops the rest; report it.
                seen[f"{name}#duplicate"] = True
                continue
            text = "" if value is None else value
            seen[name] = _style_canonical(text) if name == "style" else text
        self.events.append(("s", tag, tuple(sorted(seen.items()))))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        self._flush()
        if tag not in _VOID and tag != "tbody":
            self.events.append(("e", tag))

    def handle_comment(self, data: str) -> None:
        self._flush()
        self.events.append(("comment", data))

    def handle_decl(self, decl: str) -> None:
        self._flush()
        self.events.append(("decl", decl))

    def handle_pi(self, data: str) -> None:
        self._flush()
        self.events.append(("pi", data))

    def unknown_decl(self, data: str) -> None:
        self._flush()
        self.events.append(("decl", data))

    def close(self) -> None:
        super().close()
        self._flush()


def _events(html: str) -> list[tuple[Any, ...]]:
    parser = _Canonical()
    parser.feed(html)
    parser.close()
    return parser.events


def _shorten(text: str, limit: int = 40) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tag_counts(events: list[tuple[Any, ...]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        if event[0] == "s":
            counts[str(event[1])] = counts.get(str(event[1]), 0) + 1
    return counts


def _differences(
    source: list[tuple[Any, ...]], clean: list[tuple[Any, ...]]
) -> list[str]:
    """Human-readable reasons the cleaned stream differs from the source's."""
    if source == clean:
        return []
    problems: list[str] = []
    src_tags, out_tags = _tag_counts(source), _tag_counts(clean)
    for tag in sorted(src_tags):
        if src_tags[tag] > out_tags.get(tag, 0):
            kind = (
                "removed with its content" if tag in CLEAN_CONTENT_TAGS else "removed"
            )
            allowed = "" if tag in ALLOWED_TAGS else " (not an allowed tag)"
            problems.append(f"<{tag}> {kind}{allowed}")
    # Attributes and style properties, matched positionally on kept elements: each
    # source element pairs with the next kept element of the same tag at or after
    # the previous match. Per-tag index lists with a binary search keep this
    # linear in the document (a nested scan is quadratic on a large part).
    out_elements = [e for e in clean if e[0] == "s"]
    by_tag: dict[str, list[int]] = {}
    for index, element in enumerate(out_elements):
        by_tag.setdefault(str(element[1]), []).append(index)
    cursor = 0
    for event in (e for e in source if e[0] == "s"):
        tag = str(event[1])
        positions = by_tag.get(tag, [])
        spot = bisect_left(positions, cursor)
        if spot == len(positions):
            continue
        match = out_elements[positions[spot]]
        cursor = positions[spot] + 1
        kept = dict(match[2])
        for name, value in event[2]:
            if name.endswith("#duplicate"):
                problems.append(f"duplicate attribute {name[:-10]!r} on <{tag}>")
            elif name not in kept:
                problems.append(f"attribute {name!r} removed from <{tag}>")
            elif name == "style" and kept[name] != value:
                gone = sorted({p for p, _ in value} - {p for p, _ in kept[name]})
                problems.append(
                    f"style properties removed from <{tag}>: {', '.join(gone)}"
                    if gone
                    else f"style of <{tag}> was rewritten"
                )
            elif kept[name] != value:
                problems.append(f"attribute {name!r} of <{tag}> was rewritten")
    if not problems:
        for src, out in zip(source, clean, strict=False):
            if src != out:
                problems.append(
                    "markup structure or text differs from what a browser would "
                    f"build (first difference: {_shorten(repr(src))} became "
                    f"{_shorten(repr(out))})"
                )
                break
        else:
            problems.append("markup structure differs from what a browser would build")
    unique = list(dict.fromkeys(problems))
    if len(unique) > _MAX_PROBLEMS:
        extra = len(unique) - _MAX_PROBLEMS
        unique = [*unique[:_MAX_PROBLEMS], f"... and {extra} more"]
    return unique


# ── Entry points ────────────────────────────────────────────────────────────


def _split_doctype(source: str) -> tuple[str, str]:
    """`(prefix, rest)`: a leading accepted doctype, verbatim, and the remainder."""
    stripped = source.lstrip(_LEADING_SPACE)
    for literal in ACCEPTED_DOCTYPES:
        if stripped[: len(literal)].lower() == literal:
            cut = len(source) - len(stripped) + len(literal)
            return source[:cut], source[cut:]
    return "", source


def sanitize_html(source: str) -> SanitizedHtml:
    """Sanitize an html-part SOURCE (placeholders and sections still in it).

    Returns the cleaned html and the reasons, if any, it differs from `source`.
    Never raises for hostile input: anything it cannot keep is reported.
    """
    if not isinstance(source, str):
        raise TypeError("html source must be a string")
    prefix, rest = _split_doctype(source)
    if "<!" in rest or "<?" in rest:
        return SanitizedHtml(
            "",
            (
                "comments, CDATA, declarations and processing instructions "
                "(`<!`, `<?`) are not allowed",
            ),
        )
    if _has_nul(rest):
        return SanitizedHtml("", ("a NUL character is not allowed",))
    bidi = bidi_control(source)
    if bidi:
        return SanitizedHtml(
            "",
            (f"the bidirectional control character {bidi} is not allowed",),
        )
    source_events = _events(rest)
    decoded_bidi = _decoded_bidi(source_events)
    if decoded_bidi:
        # `&#x202e;` etc. decode to the control after the raw scan above.
        return SanitizedHtml(
            "",
            (
                f"a character reference decodes to the bidirectional control "
                f"character {decoded_bidi}, which is not allowed",
            ),
        )
    if _decoded_braces(source_events) != (rest.count("{"), rest.count("}")):
        # A character reference (`&#123;`, `&lbrace;`, ...) that decodes to a
        # brace would become a LIVE placeholder or section tag in the stored
        # html, after every placeholder rule has already run on the source.
        return SanitizedHtml(
            "",
            (
                "braces do not match the decoded text: a character reference "
                "that decodes to `{` or `}` (`&#123;`, `&lbrace;`, ...) is not "
                "allowed — write a brace literally, as part of a placeholder — "
                "and a brace may not sit in markup that is not kept",
            ),
        )

    tokens: list[str] = []

    def _swap(match: re.Match[str]) -> str:
        if match.group(1) is None:  # a flat placeholder, left as text
            return match.group(0)
        tokens.append(match.group(0))
        return f"<!--ts{len(tokens) - 1}-->"

    swapped = _TOKEN.sub(_swap, rest)
    cleaned = _CLEANER.clean(swapped)

    def _restore(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return tokens[index] if index < len(tokens) else match.group(0)

    restored = re.sub(r"<!--ts([0-9]+)-->", _restore, cleaned)
    if "<!--" in restored or len(re.findall(r"<!--ts[0-9]+-->", cleaned)) != len(
        tokens
    ):
        return SanitizedHtml(
            "",
            ("the parser rearranged or dropped a section tag; simplify the markup",),
        )
    out_events = _events(restored)
    if _decoded_braces(out_events) != _decoded_braces(source_events) or [
        m.group(0) for m in _TOKEN.finditer(restored)
    ] != [m.group(0) for m in _TOKEN.finditer(rest)]:
        return SanitizedHtml(
            "",
            ("the placeholders or section tags would change; refusing the markup",),
        )
    problems = _differences(source_events, out_events)
    return SanitizedHtml(prefix + restored, tuple(problems))


def _decoded_bidi(events: list[tuple[Any, ...]]) -> str | None:
    """First bidirectional control in decoded text or attribute values.

    LRM, RLM and ALM (U+200E, U+200F, U+061C) are deliberately NOT refused: they
    are non-overriding marks that legitimate right-to-left text needs.
    """
    for event in events:
        if event[0] == "t":
            found = bidi_control(event[1])
        elif event[0] == "s":
            found = next(
                (
                    hit
                    for _, value in event[2]
                    if isinstance(value, str)
                    for hit in [bidi_control(value)]
                    if hit
                ),
                None,
            )
        else:
            continue
        if found:
            return found
    return None


def _decoded_braces(events: list[tuple[Any, ...]]) -> tuple[int, int]:
    """`{` and `}` counts across decoded text and attribute values."""
    opens = closes = 0
    for event in events:
        if event[0] == "t":
            texts: list[str] = [event[1]]
        elif event[0] == "s":
            texts = []
            for _, value in event[2]:
                if isinstance(value, str):
                    texts.append(value)
                elif isinstance(value, tuple):
                    texts.extend(f"{prop}:{val}" for prop, val in value)
        else:
            continue
        for text in texts:
            opens += text.count("{")
            closes += text.count("}")
    return opens, closes


def _has_nul(text: str) -> bool:
    return "\x00" in text


def html_sanitizer_problems(source: str) -> list[str]:
    """Why `source` would not survive sanitizing unchanged; empty means it does."""
    return list(sanitize_html(source).problems)


def require_clean_html(source: str) -> str:
    """Return the sanitized html to STORE, or raise `HtmlSanitizationRefused`."""
    result = sanitize_html(source)
    if result.problems:
        raise HtmlSanitizationRefused(
            "the html part contains markup outside the allowed email subset and "
            "was refused (nothing was removed or stored): " + "; ".join(result.problems)
        )
    return result.html


__all__ = [
    "ALLOWED_ATTRIBUTES",
    "ALLOWED_STYLE_PROPERTIES",
    "ALLOWED_TAGS",
    "ALLOWED_URL_SCHEMES",
    "CLEAN_CONTENT_TAGS",
    "HtmlSanitizationRefused",
    "SanitizedHtml",
    "html_sanitizer_problems",
    "require_clean_html",
    "sanitize_html",
]
