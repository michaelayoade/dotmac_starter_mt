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
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

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
