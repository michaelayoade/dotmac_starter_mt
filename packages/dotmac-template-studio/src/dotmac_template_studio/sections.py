"""Tokenizer and parser for the logic-less section grammar (ADR-0006 § 5i).

Pure standard library: importing it loads no kernel, web or persistence code, so
the renderer seam stays usable on its own.

## Grammar

    {name}            a placeholder (unchanged from § 5b)
    {#name}...{/name} a section: iterate a declared list, or render once for a
                      present optional scalar
    {^name}...{/name} an inverted section: render when the list is empty or the
                      optional scalar is absent

There are no expressions, filters, helpers, partials or computed values. The
three tokens share ONE pattern with the same `{{`-guards the placeholder always
had, so `{{#x}}` is literal text (and is refused at save time, like `{{x}}`).
The pattern has no nested quantifier, and the parser is a single left-to-right
pass over its matches with an explicit stack, so parse time is linear in the
input however it is shaped.

The flat-placeholder branch of the pattern matches exactly what the original
`rendering._PLACEHOLDER` matched; the section branch only adds tokens that
begin with `#`, `^` or `/`, which the original never matched. That is what keeps
every existing template rendering byte-identically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Nesting depth of sections. A section inside a section is depth 2; deeper is
# refused. Fixed by § 5i, not configurable.
MAX_SECTION_DEPTH = 2

_TOKEN = re.compile(r"(?<!\{)\{\s*(?:([#^/])\s*)?([a-z][a-z0-9_]*)\s*\}(?!\})")
# Double-brace tag forms (`{{#x}}`, `{{^x}}`, `{{/x}}`), for save-time refusal.
DOUBLE_BRACE_SECTION = re.compile(r"\{\{\s*[#^/]\s*[a-zA-Z0-9_]+\s*\}\}")


class TemplateSyntaxError(ValueError):
    """The template's section structure is malformed."""


@dataclass(frozen=True, slots=True)
class Text:
    value: str


@dataclass(frozen=True, slots=True)
class Var:
    name: str
    raw: str  # the exact source text, kept so non-strict preview can echo it


@dataclass(slots=True)
class Section:
    name: str
    inverted: bool
    children: list[Node] = field(default_factory=list)
    raw_open: str = ""


Node = Text | Var | Section


def parse(source: str) -> list[Node]:
    """Parse `source` into a node tree, or raise `TemplateSyntaxError`.

    Checks balance, mis-nesting and depth. It knows nothing about vocabulary;
    that is the validator's job.
    """
    root: list[Node] = []
    stack: list[Section] = []
    position = 0
    for match in _TOKEN.finditer(source):
        current = stack[-1].children if stack else root
        if match.start() > position:
            current.append(Text(source[position : match.start()]))
        position = match.end()
        sigil, name = match.group(1), match.group(2)
        if sigil is None:
            current.append(Var(name, match.group(0)))
        elif sigil == "/":
            if not stack:
                raise TemplateSyntaxError(
                    f"closing tag {{/{name}}} has no matching opening tag"
                )
            if stack[-1].name != name:
                raise TemplateSyntaxError(
                    f"mis-nested sections: {{/{name}}} closes while "
                    f"{{{'^' if stack[-1].inverted else '#'}{stack[-1].name}}} "
                    "is still open"
                )
            stack.pop()
        else:
            if len(stack) >= MAX_SECTION_DEPTH:
                raise TemplateSyntaxError(
                    f"sections nest deeper than {MAX_SECTION_DEPTH} levels at "
                    f"{{{sigil}{name}}}"
                )
            if any(open_.name == name for open_ in stack):
                raise TemplateSyntaxError(
                    f"section {{{sigil}{name}}} is nested inside itself"
                )
            section = Section(name, sigil == "^", raw_open=match.group(0))
            current.append(section)
            stack.append(section)
    if stack:
        opened = stack[-1]
        raise TemplateSyntaxError(
            f"section {{{'^' if opened.inverted else '#'}{opened.name}}} is never "
            f"closed — add {{/{opened.name}}}"
        )
    if position < len(source):
        root.append(Text(source[position:]))
    return root


def walk(nodes: list[Node], depth: int = 0):
    """Yield `(node, depth, enclosing_sections)` for every node, in order."""
    stack: list[Section] = []

    def _walk(items: list[Node], level: int):
        for node in items:
            yield node, level, tuple(stack)
            if isinstance(node, Section):
                stack.append(node)
                yield from _walk(node.children, level + 1)
                stack.pop()

    yield from _walk(nodes, depth)


__all__ = [
    "DOUBLE_BRACE_SECTION",
    "MAX_SECTION_DEPTH",
    "Node",
    "Section",
    "TemplateSyntaxError",
    "Text",
    "Var",
    "parse",
    "walk",
]
