"""Single-brace template substitution, independent of Template Studio wiring.

Importing this module does not assemble the module's routes, web surface,
database models, or feature flags. It is the renderer shared by the service
and callers that only need to substitute already-approved template text.
"""

from __future__ import annotations

import html as _html
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

from dotmac_template_studio.sections import (
    Section,
    TemplateSyntaxError,
    Text,
    Var,
    parse,
)

if TYPE_CHECKING:  # typing only: this module must not import the kernel
    from dotmac_template_studio.contexts import RenderContext

# Negative lookarounds leave {{double}} braces untouched. Save-time validation
# in the service rejects that syntax before a template can be published.
_PLACEHOLDER = re.compile(r"(?<!\{)\{\s*([a-z][a-z0-9_]*)\s*\}(?!\})")


class MissingTemplateValueError(ValueError):
    """Strict rendering found a placeholder with no supplied value."""


def render(body: str, values: dict[str, str], *, strict: bool = True) -> str:
    """Substitute `{name}` placeholders with `values` without evaluating text.

    Strict mode refuses a half-substituted result. Preview callers may pass
    ``strict=False`` to leave absent placeholders visible.
    """
    missing: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in values:
            return values[name]
        missing.append(name)
        return match.group(0)

    rendered = _PLACEHOLDER.sub(_sub, body)
    if strict and missing:
        names = ", ".join(sorted(set(missing)))
        raise MissingTemplateValueError(
            f"missing value(s) for template variable(s): {names}"
        )
    return rendered


# ── Part-aware rendering (ADR-0006 § 5h, § 5i) ──────────────────────────────

Part = Literal["subject", "text", "html"]

# Hard bounds. A context may lower the list bound, never raise it past the cap.
MAX_LIST_ITEMS = 100
HARD_MAX_LIST_ITEMS = 500
# Two nested lists of 100 items multiply; bound what a render may produce.
MAX_RENDERED_CHARS = 1_000_000
# The only URL schemes a `url` value may carry in the html part.
ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto"})


class RenderRefusedError(ValueError):
    """A value or a size bound made the render unsafe, so nothing is returned."""


def check_url(value: str) -> str:
    """Return `value` unchanged if it is an allowed absolute URL, else raise.

    Fail-closed: relative URLs, scheme-less values, `javascript:`/`data:` and
    anything with control characters or whitespace are refused. `http(s)` needs
    a host; `mailto` needs an address.
    """
    if not isinstance(value, str) or not value:
        raise RenderRefusedError("a url value must be a non-empty string")
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise RenderRefusedError(
            "a url value may not contain whitespace or control characters"
        )
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise RenderRefusedError("a url value is not a valid URL") from exc
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        raise RenderRefusedError(
            f"url scheme {parts.scheme!r} is not allowed — use one of "
            f"{', '.join(sorted(ALLOWED_URL_SCHEMES))}"
        )
    if scheme == "mailto":
        if not parts.path:
            raise RenderRefusedError("a mailto url needs an address")
    elif not parts.netloc or not parts.hostname:
        raise RenderRefusedError(f"a {scheme} url needs a host")
    return value


def _present(value: Any) -> bool:
    return isinstance(value, str) and value != ""


class _Renderer:
    def __init__(
        self,
        part: Part,
        values: Mapping[str, Any],
        context: Any,
        strict: bool,
    ) -> None:
        self.part = part
        self.values = values
        self.context = context
        self.strict = strict
        self.missing: list[str] = []
        self.out: list[str] = []
        self.size = 0
        self.url_names: frozenset[str] = (
            frozenset(getattr(context, "url", ()))
            if context is not None
            else frozenset()
        )
        self.lists: Mapping[str, Any] = (
            getattr(context, "lists", {}) if context is not None else {}
        )
        self.optional: frozenset[str] = (
            frozenset(getattr(context, "optional", ()))
            if context is not None
            else frozenset()
        )
        cap = getattr(context, "max_list_items", None) if context is not None else None
        self.max_items = min(cap or MAX_LIST_ITEMS, HARD_MAX_LIST_ITEMS)

    def emit(self, text: str) -> None:
        self.size += len(text)
        if self.size > MAX_RENDERED_CHARS:
            raise RenderRefusedError(
                f"rendered output exceeds {MAX_RENDERED_CHARS} characters"
            )
        self.out.append(text)

    def encode(self, name: str, value: str) -> str:
        if not isinstance(value, str):
            raise RenderRefusedError(f"value for {name!r} must be a string")
        if self.part == "text":
            return value
        if self.part == "subject":
            if "\r" in value or "\n" in value:
                raise RenderRefusedError(
                    f"value for {name!r} contains a line break, which a subject "
                    "may not carry"
                )
            return value
        if name in self.url_names:
            try:
                return _html.escape(check_url(value), quote=True)
            except RenderRefusedError as exc:
                raise RenderRefusedError(f"value for {name!r}: {exc}") from exc
        return _html.escape(value, quote=True)

    def lookup(self, name: str, scopes: list[Mapping[str, Any]]) -> Any:
        for scope in reversed(scopes):
            if name in scope:
                return scope[name]
        return self.values.get(name)

    def run(self, nodes: list[Any], scopes: list[Mapping[str, Any]]) -> None:
        for node in nodes:
            if isinstance(node, Text):
                self.emit(node.value)
            elif isinstance(node, Var):
                value = self.lookup(node.name, scopes)
                if value is None or node.name in self.lists:
                    self.missing.append(node.name)
                    self.emit(node.raw)
                    continue
                self.emit(self.encode(node.name, value))
            else:
                self.section(node, scopes)

    def section(self, node: Section, scopes: list[Mapping[str, Any]]) -> None:
        name = node.name
        if name in self.lists:
            raw = self.values.get(name)
            if raw is None:
                items: Sequence[Mapping[str, Any]] = ()
            elif isinstance(raw, str | bytes) or not isinstance(raw, Sequence):
                raise RenderRefusedError(f"value for list {name!r} must be a list")
            else:
                items = raw
            if len(items) > self.max_items:
                raise RenderRefusedError(
                    f"list {name!r} has {len(items)} items; at most "
                    f"{self.max_items} may be rendered"
                )
            if node.inverted:
                if not items:
                    self.run(node.children, scopes)
                return
            allowed = frozenset(self.lists[name])
            for item in items:
                if not isinstance(item, Mapping):
                    raise RenderRefusedError(
                        f"every item of list {name!r} must be a mapping"
                    )
                # Only the declared item vocabulary is visible in an item scope.
                scope = {k: v for k, v in item.items() if k in allowed}
                self.run(node.children, [*scopes, scope])
            return
        if name in self.optional:
            present = _present(self.values.get(name))
            if present != node.inverted:
                self.run(node.children, scopes)
            return
        raise TemplateSyntaxError(
            f"section {name!r} is not a declared list or optional variable"
        )


def render_part(
    source: str,
    values: Mapping[str, Any],
    part: Part,
    *,
    context: RenderContext | None = None,
    strict: bool = True,
) -> str:
    """Render one part of a template (`subject`, `text` or `html`).

    - `text`: values are inserted raw, exactly as `render()` does.
    - `html`: every value is HTML-escaped; a variable the context declares in
      `url` must pass `check_url` and is attribute-escaped. Refused otherwise.
    - `subject`: values are inserted raw but a result containing CR or LF is
      refused (header injection). Sections are not allowed in a subject.

    Sections need `context` (it declares which names are lists/optionals and
    each list's item vocabulary); without one, any section is a syntax error.
    Substitution is single pass: inserted values are never re-scanned, so a
    value containing `{#x}` is data, not template.

    Raises `MissingTemplateValueError` (strict only), `TemplateSyntaxError`, or
    `RenderRefusedError`.
    """
    if part not in ("subject", "text", "html"):
        raise ValueError(f"unknown template part {part!r}")
    nodes = parse(source)
    if part == "subject" and any(isinstance(n, Section) for n in nodes):
        raise TemplateSyntaxError("a subject may not contain sections")
    renderer = _Renderer(part, values, context, strict)
    renderer.run(nodes, [])
    if part == "subject":
        result = "".join(renderer.out)
        if "\r" in result or "\n" in result:
            raise RenderRefusedError("the rendered subject contains a line break")
    if strict and renderer.missing:
        names = ", ".join(sorted(set(renderer.missing)))
        raise MissingTemplateValueError(
            f"missing value(s) for template variable(s): {names}"
        )
    return "".join(renderer.out)


__all__ = [
    "ALLOWED_URL_SCHEMES",
    "HARD_MAX_LIST_ITEMS",
    "MAX_LIST_ITEMS",
    "MAX_RENDERED_CHARS",
    "MissingTemplateValueError",
    "RenderRefusedError",
    "check_url",
    "render",
    "render_part",
]
