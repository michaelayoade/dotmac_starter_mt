"""Save-time validation of a revision against its render context (§ 5h, § 5i).

`validate_revision` is the one function that decides whether a revision's
subject, text part and optional html part fit a context. It returns every
problem it finds as a list of plain strings and never raises, so two callers can
share it:

- `service.create_version` / `update_version` refuse a revision with problems
  (`check_revision` raises `BadRequestError` with all of them);
- the deploy-time check (TS-6, not built here) can call it against every
  published revision to prove each one still fits the context a release
  registers, and report instead of failing on the first.

Rules, in addition to the flat-placeholder rules of § 5b:

1. Double-brace placeholders and double-brace section tags are refused.
2. Section structure must parse: balanced, correctly nested, depth <= 2, no
   section nested inside itself.
3. A section name must be a declared list or a declared optional scalar; an
   inverted section likewise. A subject takes no sections.
4. A flat placeholder must be a declared scalar, or an item variable of an
   enclosing list section. An item variable outside its list is refused, a list
   name used as a flat placeholder is refused, and an optional scalar may be used
   as a flat placeholder only inside its own section (it may be absent, so
   anywhere else it could render half-substituted).
5. In the html part a placeholder may appear only in element text, or as the
   whole value of a quoted `href`/`src` when declared `url` (`htmlctx`).
6. A subject may contain no line break or control character; every part is
   bounded by `MAX_TEMPLATE_CHARS`.
7. The html part must survive the nh3 allowlist sanitizer unchanged
   (`htmlsanitize`); a part that would be altered is refused, never stripped.
8. Every `required` variable must appear as a top-level placeholder in each body
   part present (text, and html when given). A subject is exempt. Occurrences
   inside sections do not count: a section may render zero times.
"""

from __future__ import annotations

import re
from typing import Any

from dotmac_kernel.exceptions import BadRequestError

from dotmac_template_studio.htmlctx import html_placeholder_problems
from dotmac_template_studio.htmlsanitize import html_sanitizer_problems
from dotmac_template_studio.rendering import MAX_TEMPLATE_CHARS, subject_problem
from dotmac_template_studio.sections import (
    DOUBLE_BRACE_SECTION,
    Section,
    TemplateSyntaxError,
    Var,
    parse,
    walk,
)

_DOUBLE_BRACE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def _validate_part(
    label: str, source: str, context: Any, *, is_subject: bool
) -> list[str]:
    problems: list[str] = []
    if len(source) > MAX_TEMPLATE_CHARS:
        return [f"{label}: longer than {MAX_TEMPLATE_CHARS} characters"]
    if is_subject:
        reason = subject_problem(source)
        if reason:
            problems.append(f"{label}: the subject {reason}")
    double = sorted(
        {"{{" + m + "}}" for m in _DOUBLE_BRACE.findall(source)}
        | {m.strip() for m in DOUBLE_BRACE_SECTION.findall(source)}
    )
    if double:
        problems.append(
            f"{label}: double braces are not a supported syntax — use single "
            f"braces: {', '.join(double)}"
        )
    try:
        nodes = parse(source)
    except TemplateSyntaxError as exc:
        problems.append(f"{label}: {exc}")
        return problems

    if label == "html":
        problems += [
            f"{label}: {p}" for p in html_placeholder_problems(nodes, context.url)
        ]
    scalars: frozenset[str] = frozenset(context.variables)
    optional: frozenset[str] = frozenset(context.optional)
    lists = context.lists
    all_items: frozenset[str] = (
        frozenset().union(*lists.values()) if lists else frozenset()
    )
    allowed = ", ".join("{" + n + "}" for n in sorted(scalars))
    seen_top_level: set[str] = set()

    for node, _depth, enclosing in walk(nodes):
        if isinstance(node, Section):
            tag = ("^" if node.inverted else "#") + node.name
            if is_subject:
                problems.append(
                    f"{label}: a subject may not contain sections ({{{tag}}})"
                )
            elif node.name not in lists and node.name not in optional:
                problems.append(
                    f"{label}: section {{{tag}}} names something that is not a "
                    f"declared list or optional variable of the "
                    f"{context.name!r} context"
                )
            continue
        if not isinstance(node, Var):
            continue
        name = node.name
        in_lists = [s.name for s in enclosing if s.name in lists and not s.inverted]
        in_item_scope = any(name in lists[ln] for ln in in_lists)
        in_own_section = any(
            s.name == name and not s.inverted and name in optional for s in enclosing
        )
        if in_item_scope:
            continue
        if name in lists:
            problems.append(
                f"{label}: {{{name}}} is a list — iterate it with "
                f"{{#{name}}}…{{/{name}}}"
            )
        elif name in optional:
            if not in_own_section:
                problems.append(
                    f"{label}: {{{name}}} is optional and may be absent — use it "
                    f"only inside {{#{name}}}…{{/{name}}}"
                )
        elif name in scalars:
            if not enclosing:
                seen_top_level.add(name)
        elif name in all_items:
            problems.append(
                f"{label}: {{{name}}} is a list item variable and can only be used "
                "inside its own list section"
            )
        else:
            problems.append(
                f"{label}: variable {{{name}}} is one the {context.name!r} context "
                f"cannot supply, which would be sent literally. Allowed: {allowed}"
            )
    if not is_subject:
        missing = sorted(set(context.required) - seen_top_level)
        if missing:
            problems.append(
                f"{label}: required variable(s) never used: "
                + ", ".join("{" + n + "}" for n in missing)
            )
    return problems


def validate_revision(
    context: Any,
    *,
    subject: str | None = None,
    text: str | None = None,
    html: str | None = None,
) -> list[str]:
    """Every problem with this revision against `context`; empty means valid."""
    problems: list[str] = []
    if subject:
        problems += _validate_part("subject", subject, context, is_subject=True)
    # The text part is the body today, so it is checked even when empty: a
    # required variable cannot be satisfied by an absent body.
    problems += _validate_part("text", text or "", context, is_subject=False)
    if html is not None:
        problems += _validate_part("html", html, context, is_subject=False)
        # The allowlist gate (§ 5h): the html must survive the sanitizer
        # UNCHANGED. Runs on the same source, so every problem is reported at once.
        problems += [f"html: {p}" for p in html_sanitizer_problems(html)]
    return problems


def check_revision(
    context: Any,
    *,
    subject: str | None = None,
    text: str | None = None,
    html: str | None = None,
    kind_hint: str = "version",
) -> None:
    """Raise `BadRequestError` naming every problem, or return if the revision fits."""
    problems = validate_revision(context, subject=subject, text=text, html=html)
    if problems:
        raise BadRequestError(
            f"{kind_hint} placeholder problem(s): " + "; ".join(problems)
        )


__all__ = ["check_revision", "validate_revision"]
