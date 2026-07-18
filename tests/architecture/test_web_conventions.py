"""Web-portal conventions (Task 8): templates + web.py import hygiene.

Three independent static checks, all belt-and-suspenders alongside runtime
behavior (nothing here talks to the DB or the app):

1. Every `templates/admin/**/*.html` + `templates/auth/*.html` file either
   `{% extends %}`s a layout (the full-page templates) or is an
   `_`-prefixed fragment (an htmx partial, never rendered standalone — see
   e.g. `app.features.parties.web.index`'s HX-Request branch). A template
   that is neither is an orphaned convention violation: either it forgot to
   extend a layout, or it's a fragment that should be renamed with the `_`
   prefix so the convention is visible from the filename alone.
2. No template contains a live `<form ...method="post"...>` tag — every
   mutation in this app's portal goes through htmx (`hx-post`/`hx-put`/
   `hx-delete`), because a plain `method="post"` form has no hook point for
   the CSRF header bridge (`static/js/csrf.js`, see `templates/base.html`'s
   comment). Jinja/HTML *comments* are allowed to mention the string
   `method="post"` in prose (several already do, describing the anti-
   pattern they moved away from) — this check strips comments before
   scanning so those don't false-positive.
3. `| safe` may appear ONLY when a comment on the same line or one of the
   preceding lines mentions "sanitiz" (sanitize/sanitizer/sanitized) —
   `templates/admin/settings/branding.html`'s `custom_css` preview is the
   one branding usage that passes (its value is already run through
   `app.core.branding.sanitize_branding_css` before the template ever sees
   it); anything else must fail until it's threaded through a real
   sanitizer the same way.
4. Every `app/features/<name>/web.py` imports only `app.core.*` and its OWN
   feature's `app.features.<name>.*` — belt-and-suspenders alongside the
   import-linter "Features are independent of each other" contract
   (`pyproject.toml`), which covers `router.py` too but is a config file, not
   a test that runs in the fast unit/architecture suite by itself; this
   gives immediate, in-repo feedback without invoking `make lint-imports`.

SCOPE LIMITATION: The template checks cover `templates/admin/**` and
`templates/auth/*` only (errors/layouts/components unscanned; currently
verified clean by the T8 review). The import scan skips relative imports
(import-linter is the authoritative parallel contract). A future non-admin
portal surface must extend `_admin_and_auth_templates()` and the sweep
prefix accordingly.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from jinja2 import nodes

from app.core.templating import templates

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_ROOT = PROJECT_ROOT / "templates"

# ---------------------------------------------------------------------------
# 1. Every admin/auth template extends a layout or is an `_`-prefixed
#    fragment.
# ---------------------------------------------------------------------------


def _admin_and_auth_templates() -> list[Path]:
    return sorted(TEMPLATES_ROOT.glob("admin/**/*.html")) + sorted(
        TEMPLATES_ROOT.glob("auth/*.html")
    )


def test_every_admin_or_auth_template_extends_a_layout_or_is_a_fragment() -> None:
    violations: list[str] = []
    for path in _admin_and_auth_templates():
        rel = str(path.relative_to(PROJECT_ROOT))
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        if "{% extends" not in text:
            violations.append(rel)
    assert not violations, (
        "Template(s) neither extend a layout nor use the `_`-prefixed "
        "fragment naming convention:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# 2. No live `method="post"` form (all mutations are hx-*).
# ---------------------------------------------------------------------------

_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_FORM_TAG = re.compile(r"<form\b[^>]*>", re.IGNORECASE | re.DOTALL)
_METHOD_POST = re.compile(r"""method\s*=\s*["']post["']""", re.IGNORECASE)


def _blank_out(match: re.Match[str]) -> str:
    """Replace a matched comment with newlines only, preserving line numbers
    so callers that report a 1-based `lineno` against the ORIGINAL text stay
    accurate even after stripping.
    """
    return "\n" * match.group(0).count("\n")


def _strip_comments(text: str) -> str:
    """Drop Jinja `{# #}` and HTML `<!-- -->` comments before scanning —
    several templates deliberately document the `method="post"`/`| safe`
    anti-patterns in prose (see module docstring) and must NOT trip these
    checks. Line numbers are preserved (comments are blanked, not removed).
    """
    return _HTML_COMMENT.sub(_blank_out, _JINJA_COMMENT.sub(_blank_out, text))


def test_no_template_uses_a_plain_method_post_form() -> None:
    violations: list[str] = []
    for path in _admin_and_auth_templates():
        text = _strip_comments(path.read_text(encoding="utf-8"))
        for tag in _FORM_TAG.findall(text):
            if _METHOD_POST.search(tag):
                violations.append(f"{path.relative_to(PROJECT_ROOT)}: {tag.strip()}")
    assert not violations, (
        'Template(s) use a plain method="post" form — every mutation must '
        "be hx-post/hx-put/hx-delete instead (see module docstring):\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# 3. `| safe` only alongside a "sanitiz*"-mentioning comment nearby.
# ---------------------------------------------------------------------------

_SAFE_FILTER = re.compile(r"\|\s*safe\b")
_SANITIZE_MENTION = re.compile(r"sanitiz", re.IGNORECASE)
# How many preceding lines (inclusive of the `| safe` line itself) count as
# "nearby" — generous enough to cover a multi-line `{# ... #}` comment block
# like branding.html's (8 lines above the `| safe` usage), not so generous
# that an unrelated sanitize-mention elsewhere in the file would satisfy it.
_NEARBY_LINES = 12


def test_safe_filter_only_used_with_a_sanitize_comment_nearby() -> None:
    violations: list[str] = []
    for path in _admin_and_auth_templates():
        original_text = path.read_text(encoding="utf-8")
        original_lines = original_text.splitlines()
        # Detect REAL `| safe` usages against the comment-stripped text (a
        # comment merely mentioning the string, e.g. rbac/audit.html's "no
        # `| safe` is used" prose, must not count as a usage) — but search
        # for the justifying "sanitiz*" mention against the ORIGINAL text,
        # since that mention lives inside the very comment that would
        # otherwise get stripped.
        stripped_lines = _strip_comments(original_text).splitlines()
        for lineno, stripped_line in enumerate(stripped_lines, start=1):
            if not _SAFE_FILTER.search(stripped_line):
                continue
            window_start = max(0, lineno - _NEARBY_LINES)
            window = "\n".join(original_lines[window_start:lineno])
            if not _SANITIZE_MENTION.search(window):
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{lineno}: "
                    f"{original_lines[lineno - 1].strip()}"
                )
    assert not violations, (
        "`| safe` used without a nearby comment mentioning `sanitiz*` — "
        "either thread the value through a real sanitizer and document it, "
        "or drop `| safe`:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# 4. Every app/features/<name>/web.py imports only app.core + its own
#    feature.
# ---------------------------------------------------------------------------


def _web_py_files() -> list[Path]:
    return sorted((PROJECT_ROOT / "app" / "features").glob("*/web.py"))


def _own_feature_name(path: Path) -> str:
    # app/features/<name>/web.py -> <name>
    return path.parent.name


def _imported_module_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
    return modules


def _is_allowed_app_import(module: str, own_feature: str) -> bool:
    if module == "app.core" or module.startswith("app.core."):
        return True
    own_prefix = f"app.features.{own_feature}"
    return module == own_prefix or module.startswith(f"{own_prefix}.")


def test_web_py_imports_only_its_own_feature_and_core() -> None:
    violations: list[str] = []
    for path in _web_py_files():
        own_feature = _own_feature_name(path)
        for module in _imported_module_names(path):
            if not module.startswith("app."):
                continue
            if not _is_allowed_app_import(module, own_feature):
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)} imports {module!r} "
                    f"(only app.core.* and app.features.{own_feature}.* allowed)"
                )
    assert not violations, (
        "web.py module(s) importing outside their own feature + app.core "
        "(features must stay independent of each other):\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# 5. Every `*_at` timestamp render goes through local_datetime/local_date
#    (Task 2: tenant display settings).
#
# AST-based, not a substring match: a prior version of this check scanned
# each `{{ ... }}` expression's raw text for `\w+_at\b` and then just tested
# whether the literal substring "local_date" appeared ANYWHERE in that same
# expression. That's bypassable — `{{ x.created_at | default("local_datetime") }}`
# and `{{ x.created_at if local_date_format else "" }}` both contain the
# substring "local_date" without the `created_at` attribute ever actually
# being piped through the real filter, so the old check silently accepted
# them (see `test_timestamp_filter_check_catches_bypass_attempts` below,
# which pins this against the app's REAL Jinja environment).
#
# This walks the parsed AST (`templates.env.parse`, the same environment
# every template actually renders through — so filter/extension behavior
# matches production) instead: only `Output` nodes (the `{{ ... }}` render
# sites) are inspected — a `{% if x.created_at %}` test expression lives on
# the `If` node directly, never inside an `Output`, so it's correctly out of
# scope (that's a truthiness check, not a render). Within each Output child
# expression, every `Getattr`/`Getitem`/`Name` node whose name matches
# `\w+_at$` must have a `Filter` node named `local_date`/`local_datetime`
# somewhere between it and the root of that expression tree.
# ---------------------------------------------------------------------------

_TIMESTAMP_NAME_RE = re.compile(r"\w+_at$")
_LOCAL_FILTER_NAMES = frozenset({"local_date", "local_datetime"})


def _template_files() -> list[Path]:
    """Every template under `templates/**` — wider than
    `_admin_and_auth_templates()` above (which is scoped to admin/auth full
    pages) because fragments (`_*.html`, e.g. `_audit_table.html`) render
    `*_at` timestamps too and must be covered here.
    """
    return sorted(TEMPLATES_ROOT.glob("**/*.html"))


def _timestamp_attr_name(expr: nodes.Node) -> str | None:
    """If `expr` itself renders a `*_at`-named attribute/variable, return
    its name; otherwise None. Checks `Getattr` (`x.created_at`), `Getitem`
    with a literal string key (`x["created_at"]`), and bare `Name`
    (`created_at` as a top-level template variable).
    """
    if isinstance(expr, nodes.Getattr) and _TIMESTAMP_NAME_RE.fullmatch(expr.attr):
        return expr.attr
    if isinstance(expr, nodes.Getitem):
        arg = expr.arg
        if (
            isinstance(arg, nodes.Const)
            and isinstance(arg.value, str)
            and _TIMESTAMP_NAME_RE.fullmatch(arg.value)
        ):
            return arg.value
    if isinstance(expr, nodes.Name) and _TIMESTAMP_NAME_RE.fullmatch(expr.name):
        return expr.name
    return None


def _unwrapped_timestamp_attrs(expr: nodes.Node, wrapped: bool) -> list[str]:
    """Recursively collect `*_at` attribute/name renders in `expr` that are
    NOT wrapped by a `local_date`/`local_datetime` filter at any ancestor
    depth within the expression tree.

    `wrapped` is threaded down through the recursion: it becomes True only
    while descending into the piped VALUE of a `Filter` node whose name is
    `local_date`/`local_datetime` (a filter's OTHER args — e.g.
    `default(x.created_at, "fallback")`'s second argument — are siblings,
    not the piped value, and must NOT inherit that wrapped state from a
    different filter).
    """
    offenders: list[str] = []
    if isinstance(expr, nodes.Filter):
        node_wrapped = wrapped or expr.name in _LOCAL_FILTER_NAMES
        offenders.extend(_unwrapped_timestamp_attrs(expr.node, node_wrapped))
        for arg in expr.args:
            offenders.extend(_unwrapped_timestamp_attrs(arg, wrapped))
        for kwarg in expr.kwargs:
            offenders.extend(_unwrapped_timestamp_attrs(kwarg.value, wrapped))
        if expr.dyn_args is not None:
            offenders.extend(_unwrapped_timestamp_attrs(expr.dyn_args, wrapped))
        if expr.dyn_kwargs is not None:
            offenders.extend(_unwrapped_timestamp_attrs(expr.dyn_kwargs, wrapped))
        return offenders

    attr_name = _timestamp_attr_name(expr)
    if attr_name is not None and not wrapped:
        offenders.append(attr_name)

    for child in expr.iter_child_nodes():
        offenders.extend(_unwrapped_timestamp_attrs(child, wrapped))
    return offenders


def _template_offenders(source: str) -> list[str]:
    """Parse `source` with the app's real Jinja environment and return one
    `*_at` attribute name per unwrapped timestamp render found in any
    `{{ ... }}` output expression.

    Shared by the production check (`test_timestamp_renders_go_through_local_filters`)
    and the sensitivity/bypass check
    (`test_timestamp_filter_check_catches_bypass_attempts`) so both exercise
    the exact same logic — a fix to one can't silently diverge from the
    other (same structure as `_manifest_nav_coherence_failures` in
    `tests/architecture/test_feature_manifests.py`).
    """
    template_ast = templates.env.parse(source)
    offenders: list[str] = []
    for output in template_ast.find_all(nodes.Output):
        for child in output.nodes:
            offenders.extend(_unwrapped_timestamp_attrs(child, wrapped=False))
    return offenders


def test_timestamp_renders_go_through_local_filters() -> None:
    """A raw `{{ x.created_at }}` bypasses tenant display settings.

    Any `{{ ... }}` render of a `*_at` attribute/variable must be wrapped in
    `| local_datetime` or `| local_date`, checked via the real Jinja AST
    (see module docstring above section 5) rather than a substring match.
    """
    offenders: list[str] = []
    for path in _template_files():
        source = path.read_text(encoding="utf-8")
        for attr in _template_offenders(source):
            offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {attr}")
    assert not offenders, (
        "Raw timestamp renders (add `| local_datetime` or `| local_date`): "
        + "; ".join(offenders)
    )


def test_timestamp_filter_check_catches_bypass_attempts() -> None:
    """Sensitivity test: prove `_template_offenders` (the SAME helper the
    production check above calls — no re-implementation to drift from it)
    catches non-literal bypasses of the local filter requirement, and does
    not false-positive on the two legit forms.

    These bypasses previously slipped past the old substring check (`"local_date"
    not in expr`) because the literal substring "local_date" appears
    SOMEWHERE in the expression even though `created_at` itself is never
    piped through the real filter:

    - `{{ x.created_at | default("local_datetime") }}` — "local_datetime"
      only appears as a fallback STRING ARGUMENT to an unrelated filter
      (`default`), not as the filter wrapping `created_at`.
    - `{{ x.created_at if local_date_format else "" }}` — "local_date" only
      appears as a substring of an unrelated variable name
      (`local_date_format`) used as a conditional test, not a filter at all.
    """
    bypasses = [
        "{{ x.created_at }}",
        '{{ x.created_at | default("local_datetime") }}',
        '{{ x.created_at if local_date_format else "" }}',
        # Multi-line variant of the conditional bypass above — proves the
        # AST-based check isn't sensitive to formatting/line breaks the way
        # a regex-over-raw-text check could be.
        '{{\n    x.created_at\n    if local_date_format\n    else ""\n}}',
    ]
    for source in bypasses:
        offenders = _template_offenders(source)
        assert offenders, f"expected this bypass attempt to be caught: {source!r}"

    legit = [
        "{{ x.created_at | local_datetime }}",
        "{{ x.created_at | local_date }}",
    ]
    for source in legit:
        offenders = _template_offenders(source)
        assert (
            not offenders
        ), f"expected legit filtered render to pass: {source!r} -> {offenders}"
