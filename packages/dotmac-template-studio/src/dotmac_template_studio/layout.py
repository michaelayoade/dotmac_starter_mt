"""The optional per-tenant email layout and the product brand resolver (TS-5).

A layout is an ordinary template whose channel is `email_layout` and whose context
is the reserved `email_layout` context. It reuses the template/version/publish
machinery (and so the editor, CSRF, permissions, audit, confirm-and-publish and
the sanitizer/scanner rules) instead of adding a parallel entity. What is new:

- `BrandSpec` / `configure_brand`: the PRODUCT declares the brand variables a
  layout may use (names, which of them are `url`, which may be absent) and a
  resolver that supplies their values per tenant. Starter names no product and
  no variable. With no spec registered the layout vocabulary is the slot alone,
  so a layout that uses any brand variable fails validation (fail closed).
- `layout_context()`: the synthesized `RenderContext` for layouts: the brand
  variables plus the required `content` slot.
- `slot_problems` / `fragment_problem` / `wrap_part`: the slot rules. A layout
  holds exactly one top-level `{content}` per part. The content is rendered and
  validated on its own FIRST; the layout is then rendered with a random,
  per-render token standing in for the slot, and the finished content is spliced
  in at the token. The content is therefore never re-parsed as template text and
  never escaped twice, and a brand value cannot forge the token. For html the
  content must additionally be a strictly nested fragment, so it cannot close an
  element the layout opened or open one it does not close.

Nothing here imports the service or the kernel.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from uuid import UUID

from dotmac_template_studio.contexts import RenderContext
from dotmac_template_studio.rendering import (
    MAX_RENDERED_CHARS,
    RenderRefusedError,
    check_url,
    render_part,
)
from dotmac_template_studio.sections import (
    Section,
    TemplateSyntaxError,
    Var,
    parse,
    walk,
)

LAYOUT_CHANNEL = "email_layout"
LAYOUT_CONTEXT_NAME = "email_layout"
SLOT = "content"
# The only channel whose templates may opt in to a layout.
LAYOUT_TARGET_CHANNEL = "email"

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

BrandResolver = Callable[[Any, UUID], Mapping[str, str]]


class LayoutRefusedError(RenderRefusedError):
    """A layout could not be applied; nothing is returned (never an unwrapped send)."""


@dataclass(frozen=True, slots=True)
class BrandSpec:
    """The product's brand vocabulary and the resolver that fills it.

    `resolver(db, tenant_id)` returns `{name: str}`. Every declared name that is
    not in `optional` must be present; `url` names must pass `check_url`; any
    undeclared key is refused (a contract drift is an error, not a silent drop).
    The values then go through the same escaping as any other value.
    """

    variables: Collection[str]
    resolver: BrandResolver
    url: Collection[str] = field(default_factory=frozenset)
    optional: Collection[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        variables = frozenset(self.variables)
        url = frozenset(self.url)
        optional = frozenset(self.optional)
        for name in variables | url | optional:
            if not isinstance(name, str) or not _NAME.match(name):
                raise ValueError(f"brand variable name {name!r} is not valid")
        if SLOT in variables | url | optional:
            raise ValueError(f"{SLOT!r} is the layout slot, not a brand variable")
        if not callable(self.resolver):
            raise ValueError("a brand spec needs a resolver callable")
        stray = (url | optional) - variables
        if stray:
            raise ValueError(
                f"url/optional brand variable(s) {sorted(stray)} are not declared "
                "in `variables`"
            )
        object.__setattr__(self, "variables", variables)
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "optional", optional)


_BRAND: BrandSpec | None = None


def configure_brand(spec: BrandSpec | None) -> None:
    """Register (or with None, clear) the product's brand spec at assembly time."""
    global _BRAND
    _BRAND = spec


def current_brand() -> BrandSpec | None:
    return _BRAND


def layout_context() -> RenderContext:
    """The reserved layout context, from the current brand spec (slot only if none)."""
    spec = _BRAND
    variables = {SLOT}
    url: frozenset[str] = frozenset()
    optional: frozenset[str] = frozenset()
    if spec is not None:
        variables |= set(spec.variables)
        url = frozenset(spec.url)
        optional = frozenset(spec.optional)
    return RenderContext(
        name=LAYOUT_CONTEXT_NAME,
        variables=frozenset(variables) - optional,
        required=frozenset({SLOT}),
        optional=optional,
        url=url,
        description="Email layout: the {content} slot plus the brand variables.",
    )


def resolve_brand(db: Any, tenant_id: UUID) -> dict[str, str]:
    """The validated brand values for one tenant; raises `LayoutRefusedError`."""
    spec = _BRAND
    if spec is None:
        return {}
    try:
        raw = spec.resolver(db, tenant_id)
    except LayoutRefusedError:
        raise
    except Exception as exc:  # the product's resolver failed; never leak detail
        raise LayoutRefusedError(
            f"the brand resolver failed ({type(exc).__name__})"
        ) from exc
    if not isinstance(raw, Mapping):
        raise LayoutRefusedError("the brand resolver must return a mapping")
    unknown = set(raw) - set(spec.variables)
    if unknown:
        raise LayoutRefusedError(
            f"the brand resolver returned undeclared variable(s): {sorted(unknown)}"
        )
    values: dict[str, str] = {}
    for name in sorted(spec.variables):
        value = raw.get(name)
        if value is None or value == "":
            if name in spec.optional:
                continue
            raise LayoutRefusedError(f"the brand resolver gave no value for {name!r}")
        if not isinstance(value, str):
            raise LayoutRefusedError(f"brand value {name!r} must be a string")
        if name in spec.url:
            try:
                check_url(value)
            except RenderRefusedError as exc:
                raise LayoutRefusedError(f"brand value {name!r}: {exc}") from exc
        values[name] = value
    return values


# ── Slot rules (save time) ──────────────────────────────────────────────────


def slot_problems(label: str, source: str) -> list[str]:
    """Problems with where `{content}` sits in one layout part; empty is fine."""
    try:
        nodes = parse(source)
    except TemplateSyntaxError:
        return []  # the structural validator reports this
    top = 0
    nested = 0
    for node, _depth, enclosing in walk(nodes):
        if isinstance(node, Var) and node.name == SLOT:
            if enclosing:
                nested += 1
            else:
                top += 1
        elif isinstance(node, Section) and node.name == SLOT:
            nested += 1
    problems: list[str] = []
    if nested:
        problems.append(f"{label}: {{{SLOT}}} may not be used inside a section")
    if top + nested != 1:
        problems.append(
            f"{label}: a layout needs exactly one {{{SLOT}}} slot, found {top + nested}"
        )
    return problems


_VOID = frozenset({"br", "hr", "img"})
_WRAPPER_TAGS = frozenset({"html", "head", "body"})


class _Balance(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.problem: str | None = None

    def _fail(self, message: str) -> None:
        if self.problem is None:
            self.problem = message

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _WRAPPER_TAGS:
            self._fail(f"<{tag}> may not appear in a fragment")
        elif tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        if tag in _WRAPPER_TAGS:
            self._fail(f"<{tag}> may not appear in a fragment")
        elif tag not in _VOID:
            self._fail(f"<{tag}/> is not a void element")

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            self._fail(f"</{tag}> closes a void element")
        elif not self.stack or self.stack[-1] != tag:
            self._fail(f"</{tag}> does not close the innermost open element")
        else:
            self.stack.pop()

    def handle_comment(self, data: str) -> None:
        self._fail("comments are not allowed in a fragment")

    def handle_decl(self, decl: str) -> None:
        self._fail("a declaration is not allowed in a fragment")

    def handle_pi(self, data: str) -> None:
        self._fail("a processing instruction is not allowed in a fragment")

    def unknown_decl(self, data: str) -> None:
        self._fail("a declaration is not allowed in a fragment")


def fragment_problem(html: str) -> str | None:
    """Why `html` is not a strictly nested fragment, or None.

    Every start tag must be closed by its own end tag in order (void elements
    excepted), and a document wrapper, comment or declaration is refused. A
    fragment like that cannot close an element its container opened nor leave
    one open into the container.
    """
    parser = _Balance()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # a parser failure is a refusal, never a pass
        return "the html could not be parsed as a fragment"
    if parser.problem:
        return parser.problem
    if parser.stack:
        return f"<{parser.stack[-1]}> is never closed"
    return None


# ── Assembly (render time) ──────────────────────────────────────────────────


def wrap_part(
    layout_source: str,
    part: str,
    content: str,
    brand: Mapping[str, str],
    context: RenderContext | None = None,
) -> str:
    """Insert already-rendered `content` into one layout part.

    `part` is `text` or `html`. `content` must be the finished output of
    `render_part` for the same part (already escaped); it is spliced in verbatim
    and never re-parsed or re-escaped.
    """
    if part not in ("text", "html"):
        raise ValueError(f"a layout wraps the text or html part, not {part!r}")
    if part == "html":
        problem = fragment_problem(content)
        if problem:
            raise LayoutRefusedError(
                f"the content cannot be placed in a layout: {problem}"
            )
    ctx = context or layout_context()
    token = "TSSLOT" + secrets.token_hex(16)
    values = {**brand, SLOT: token}
    rendered = render_part(layout_source, values, part, context=ctx, strict=True)  # type: ignore[arg-type]
    if rendered.count(token) != 1:
        raise LayoutRefusedError(
            "the layout did not place the content slot exactly once"
        )
    before, _, after = rendered.partition(token)
    if len(before) + len(content) + len(after) > MAX_RENDERED_CHARS:
        raise LayoutRefusedError(
            f"rendered output exceeds {MAX_RENDERED_CHARS} characters"
        )
    return before + content + after


__all__ = [
    "LAYOUT_CHANNEL",
    "LAYOUT_CONTEXT_NAME",
    "LAYOUT_TARGET_CHANNEL",
    "SLOT",
    "BrandSpec",
    "LayoutRefusedError",
    "configure_brand",
    "current_brand",
    "fragment_problem",
    "layout_context",
    "resolve_brand",
    "slot_problems",
    "wrap_part",
]
