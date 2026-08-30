"""Template conventions across EVERY declared HTML facet, not one directory.

`tests/architecture/test_web_conventions.py` already holds the composed
template set to the timestamp-filter, `| safe`, inline-script and native-POST
CSRF rules, and it derives its roots from the assembly rather than from a
hand-maintained list.  Three of its checks, however, still choose their subject
by DIRECTORY NAME — `admin/**`, `auth/*`, `platform/**` — which is the exact
shape ADR-0018 warns about: the rule is right, the coverage silently narrows.
A product that declares a `customer_portal` facet whose templates live under
`portal/` escapes every one of them, and nothing fails.

This file re-derives the subject from the DECLARATIONS instead:

* `assembly.web_facets` supplies the facet codes, URL prefixes and shells.
* Every composed template root (kernel, packaged, and each v2 surface's own
  `TemplatePackage`) supplies the files.

and then adds the four conventions that had no gate at all:

1. **Shape, generalised.** Every composed template is a page (`{% extends %}`),
   a fragment (`_`-prefixed), or a DECLARED non-page.  The non-page exemption
   is not "it lives under `components/`" — that is a directory premise, which
   ADR-0018 § 2 forbids on its own.  It is "it is never returned as an HTTP
   response", and
   `test_non_page_templates_are_never_rendered_as_a_response` is what makes
   that premise machine-checkable.
2. **CSRF bridge reachability.** Every declared facet's shell must reach
   `static/js/csrf.js` through its `{% extends %}` chain.  Without it every
   htmx control in that facet loses the header transport and the facet's
   mutations fail closed at best — and a NEW facet that forgets to extend the
   shared document would ship a portal with no bridge at all.
3. **Form method validity.** HTML supports `get` and `post` on `<form>` (plus
   `dialog` inside `<dialog>`).  `method="put"` silently degrades to a GET,
   which turns a mutation into a CSRF-exempt safe method — the same class of
   defect as F7's `GET /admin/logout`.
4. **No hardcoded facet navigation.** A URL-bearing attribute must not author a
   declared facet's own prefix.  `surface.landing_path`, `surface.logout_path`
   and `surface_url()` exist precisely so a template does not decide which
   facet it is mounted under; a literal `/admin` inside a SHARED shell renders
   a cross-facet link the moment a second facet composes it.  There is real
   debt here, so this is a two-directional ratchet (ADR-0018 § 3) rather than a
   clean assertion — see `facet_navigation_debt_baseline.json`.

Every detector below carries a sensitivity proof.  A check whose real match set
is empty (form method, v2 prefix authorship) is indistinguishable from a blind
one until something proves it would still fire.
"""

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Final

from app.assembly import assembly

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
BASELINE_PATH: Final[Path] = (
    Path(__file__).parent / "facet_navigation_debt_baseline.json"
)

#: The one document that ships the CSRF header bridge.  Named once here so the
#: reachability walk below and the assertion read the same string.
CSRF_BRIDGE_ASSET: Final[str] = "/static/js/csrf.js"

_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_EXTENDS = re.compile(r"""\{%-?\s*extends\s+(?P<expr>.+?)\s*-?%\}""", re.DOTALL)
_QUOTED = re.compile(r"""(?P<q>["'])(?P<v>[^"']*)(?P=q)""")
_FORM_OPEN = re.compile(r"<form\b(?P<open>[^>]*)>", re.IGNORECASE | re.DOTALL)
_METHOD_ATTR = re.compile(r"""method\s*=\s*["'](?P<v>[^"']*)["']""", re.IGNORECASE)
_URL_ATTR = re.compile(
    r"""\b(?:href|action|src|hx-get|hx-post|hx-put|hx-patch|hx-delete)"""
    r"""\s*=\s*(?P<q>["'])(?P<v>.*?)(?P=q)""",
    re.IGNORECASE | re.DOTALL,
)

#: `<form method="...">` values a browser actually honours.  `dialog` is valid
#: HTML but only inside `<dialog>`; it submits nothing, so it cannot smuggle a
#: mutation past CSRF and is accepted here.
_VALID_FORM_METHODS: Final[frozenset[str]] = frozenset({"get", "post", "dialog"})

#: The rendering seam.  `dotmac_kernel.templating.render` is the only function
#: that turns a template name into an HTTP response, so a call to it (or to
#: Starlette's `TemplateResponse` underneath) is what "rendered as a response"
#: means.  Naming the callables rather than scanning every string literal is
#: what keeps `ComponentContract(template=...)` — a DECLARATION, not a render —
#: correctly out of scope.
_RENDER_CALLABLES: Final[frozenset[str]] = frozenset({"render", "TemplateResponse"})


def _strip_comments(text: str) -> str:
    def _blank(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    return _HTML_COMMENT.sub(_blank, _JINJA_COMMENT.sub(_blank, text))


# ---------------------------------------------------------------------------
# Subject derivation: facets and templates come from declarations.
# ---------------------------------------------------------------------------


def declared_facets() -> tuple[object, ...]:
    return tuple(assembly.web_facets)


def facet_prefixes() -> tuple[str, ...]:
    """Every declared facet's URL prefix, longest first.

    Longest-first matters for the navigation scan: a product may legitimately
    mount `/admin` and `/admin-tools`, and a shorter prefix must not swallow
    the longer one's occurrences.
    """
    prefixes = {facet.url_prefix for facet in declared_facets()}  # type: ignore[attr-defined]
    return tuple(sorted((p for p in prefixes if p != "/"), key=len, reverse=True))


def _surface_template_roots() -> tuple[Path, ...]:
    return tuple(
        surface.templates.root
        for manifest in assembly.modules
        for surface in getattr(manifest, "web_surfaces", ())
        if surface.templates is not None
    )


def composed_template_roots() -> tuple[Path, ...]:
    values = [
        PROJECT_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/templates",
        *assembly.packaged_template_dirs,
        *_surface_template_roots(),
    ]
    if assembly.assembly_template_dir is not None:
        values.append(assembly.assembly_template_dir)
    return tuple(dict.fromkeys(path.resolve() for path in values))


def composed_templates() -> list[Path]:
    return sorted(
        path for root in composed_template_roots() for path in root.rglob("*.html")
    )


def _relative(path: Path) -> str:
    """A stable, checkout-relative key for a composed template.

    Every composed root is expected to be inside this checkout — the packages
    are path dependencies installed editable, which is what makes a baseline
    keyed on repository paths meaningful.  A non-editable install would move a
    root into ``site-packages``; rather than raising deep inside a detector,
    fall back to a root-relative key and let
    ``test_composed_template_roots_live_in_this_checkout`` state that premise
    once, loudly, where it can be read.
    """
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        for root in composed_template_roots():
            try:
                return f"{root.name}/{path.relative_to(root).as_posix()}"
            except ValueError:
                continue
        return path.as_posix()


def test_composed_template_roots_live_in_this_checkout() -> None:
    """The premise every path-keyed assertion below depends on.

    The baseline, the pinned non-page set and the surface-package pin are all
    keyed on repository-relative paths.  That is only stable while the composed
    packages resolve to this checkout's own ``packages/`` tree.  If a root ever
    resolves into ``site-packages``, those keys change meaning silently — so
    say it here instead of discovering it as a confusing key mismatch.
    """
    roots = composed_template_roots()
    assert roots, "no composed template roots; every check in this file is vacuous"
    outside = [str(root) for root in roots if not root.is_relative_to(PROJECT_ROOT)]
    assert not outside, (
        "composed template root(s) resolve outside this checkout — the "
        "in-repo packages must be installed editable for the path-keyed "
        f"gates in this file to mean anything: {outside}"
    )
    missing = [str(root) for root in roots if not root.is_dir()]
    assert not missing, f"composed template root does not exist: {missing}"


# ---------------------------------------------------------------------------
# 1. Shape, derived from declarations rather than from three directory globs.
# ---------------------------------------------------------------------------


def _is_page(text: str) -> bool:
    return "{% extends" in text or "{%- extends" in text


def _is_fragment(path: Path) -> bool:
    return path.name.startswith("_")


def non_page_templates() -> list[Path]:
    """Composed templates that are neither a page nor a named fragment.

    These are the shared document root and the include/macro libraries.  The
    NEXT test is what turns that description into an enforceable premise.
    """
    return [
        path
        for path in composed_templates()
        if not _is_fragment(path) and not _is_page(path.read_text(encoding="utf-8"))
    ]


def test_the_facet_template_scope_is_derived_not_directory_named() -> None:
    """The generalisation must be a WIDENING, never a quiet re-scoping.

    `test_web_conventions.py` scans `admin/**`, `auth/*` and `platform/**`.
    Everything it sees must still be seen here, plus more — otherwise this
    file's arrival would have narrowed coverage while appearing to broaden it.
    """
    facets = declared_facets()
    assert len(facets) >= 2, (
        "the reference assembly must declare at least two facets for any "
        "cross-facet claim in this file to mean anything"
    )
    assert facet_prefixes(), "no declared facet contributes a URL prefix"

    scanned = {_relative(path) for path in composed_templates()}
    assert scanned, "the composed template scan is vacuous"

    legacy_globs = ("admin/**/*.html", "auth/*.html", "platform/**/*.html")
    legacy = {
        _relative(path)
        for root in composed_template_roots()
        for pattern in legacy_globs
        for path in root.glob(pattern)
    }
    assert legacy, "the legacy directory globs matched nothing; comparison is vacuous"
    assert legacy <= scanned, (
        "the declaration-derived scan lost templates the directory globs "
        f"covered: {sorted(legacy - scanned)}"
    )
    assert scanned - legacy, (
        "the declaration-derived scan sees nothing the directory globs did "
        "not; it is not actually a generalisation"
    )


def test_every_composed_template_is_a_page_a_fragment_or_a_declared_non_page() -> None:
    """The shape rule from `test_web_conventions.py`, applied facet-wide.

    A template that is none of the three is an orphan: it forgot to extend a
    shell, or it is an include that should carry the `_` prefix so its role is
    visible from the filename alone.
    """
    known_non_pages = {_relative(path) for path in non_page_templates()}
    # Pinned so a NEW non-page joins deliberately and is reviewed against the
    # never-rendered premise below, instead of appearing silently.
    assert known_non_pages == {
        "packages/dotmac-kernel/src/dotmac_kernel/templates/base.html",
        "packages/dotmac-kernel/src/dotmac_kernel/templates/components/form_macros.html",
        "packages/dotmac-kernel/src/dotmac_kernel/templates/components/sidebar.html",
        "packages/dotmac-kernel/src/dotmac_kernel/templates/components/table_macros.html",
        "packages/dotmac-kernel/src/dotmac_kernel/templates/components/topbar.html",
        "packages/dotmac-ui/src/dotmac_ui/templates/dotmac_ui/components/empty_state.html",
        "packages/dotmac-ui/src/dotmac_ui/templates/dotmac_ui/components/map_frame.html",
    }, (
        "the set of templates that are neither a page nor a `_` fragment "
        "changed. A new entry must be an include/macro library that is never "
        "rendered as a response — add it here in the same change that proves "
        f"that, or give it a shell/`_` prefix: {sorted(known_non_pages)}"
    )


def _rendered_template_names() -> set[str]:
    """Every string literal passed to a render call anywhere in the product."""

    names: set[str] = set()
    sources = [
        *sorted((PROJECT_ROOT / "app").rglob("*.py")),
        *sorted(PROJECT_ROOT.glob("packages/*/src/*/**/*.py")),
    ]
    for source in sources:
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id
                if isinstance(func, ast.Name)
                else ""
            )
            if name not in _RENDER_CALLABLES:
                continue
            for argument in (*node.args, *(kw.value for kw in node.keywords)):
                if isinstance(argument, ast.Constant) and isinstance(
                    argument.value, str
                ):
                    names.add(argument.value)
    return names


def _addressable_names(path: Path) -> set[str]:
    """Every name a template could be addressed by from a render call."""

    candidates: set[str] = set()
    for root in composed_template_roots():
        try:
            candidates.add(path.relative_to(root).as_posix())
        except ValueError:
            continue
    candidates.add(path.name)
    return candidates


def test_non_page_templates_are_never_rendered_as_a_response() -> None:
    """ADR-0018 § 2: the non-page exemption states an ENFORCEABLE premise.

    "It lives under `components/`" is a directory premise and would rot the
    first time someone renders a macro library directly. The premise that
    actually justifies skipping the shell requirement is "nothing returns this
    as a response", and this is the check that holds it true.
    """
    rendered = _rendered_template_names()
    assert rendered, "no render call sites found; this premise check is vacuous"

    offenders: list[str] = []
    for path in non_page_templates():
        overlap = _addressable_names(path) & rendered
        if overlap:
            offenders.append(f"{_relative(path)} rendered as {sorted(overlap)}")
    assert not offenders, (
        "template(s) exempt from the shell requirement on the grounds that "
        "they are include/macro libraries are in fact rendered as responses:\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# 2. Every declared facet's shell reaches the CSRF header bridge.
# ---------------------------------------------------------------------------


def _template_by_name(name: str) -> Path | None:
    for root in composed_template_roots():
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _extends_targets(text: str) -> list[str]:
    """Static parent names in a template's `{% extends %}` statements.

    `{% extends surface.shell or "layouts/admin.html" %}` names a fallback
    literal as well as a dynamic expression; both quoted strings are returned
    so the walk can follow whichever a deployment resolves to.
    """
    targets: list[str] = []
    for match in _EXTENDS.finditer(text):
        targets.extend(
            quoted.group("v") for quoted in _QUOTED.finditer(match.group("expr"))
        )
    return targets


def reaches_csrf_bridge(name: str, *, seen: frozenset[str] = frozenset()) -> bool:
    """Does `name`'s `{% extends %}` chain reach the CSRF bridge document?"""

    if name in seen:
        return False
    path = _template_by_name(name)
    if path is None:
        return False
    text = path.read_text(encoding="utf-8")
    if CSRF_BRIDGE_ASSET in _strip_comments(text):
        return True
    return any(
        reaches_csrf_bridge(parent, seen=seen | {name})
        for parent in _extends_targets(text)
    )


def test_every_declared_facet_shell_reaches_the_csrf_bridge() -> None:
    """A facet whose shell never loads `csrf.js` has no header transport.

    Native `<form method="post">` carries the hidden field and is checked in
    `test_web_conventions.py`. Every htmx-driven control instead relies on the
    bridge copying the cookie onto `X-CSRF-Token`, so a shell that omits it
    ships a facet whose mutations cannot succeed — and whose author is then
    tempted to relax the CSRF contract rather than fix the shell.
    """
    facets = declared_facets()
    assert facets, "no declared facets; this check is vacuous"
    missing = [
        f"{facet.code} (shell {facet.shell.qualified_name})"  # type: ignore[attr-defined]
        for facet in facets
        if not reaches_csrf_bridge(facet.shell.qualified_name)  # type: ignore[attr-defined]
    ]
    assert not missing, (
        "declared facet shell(s) never reach "
        f"{CSRF_BRIDGE_ASSET} through their extends chain: " + ", ".join(missing)
    )


def test_the_csrf_bridge_walk_has_a_sensitivity_proof() -> None:
    """Both shells reach the bridge today, so prove the walk would still fire."""
    assert reaches_csrf_bridge("layouts/admin.html")
    assert not reaches_csrf_bridge("this-template-does-not-exist.html")
    # A cycle must terminate as "not reached" rather than recursing forever.
    assert not reaches_csrf_bridge("base.html", seen=frozenset({"base.html"}))


# ---------------------------------------------------------------------------
# 3. `<form method="...">` declares something a browser honours.
# ---------------------------------------------------------------------------


def invalid_form_methods(text: str) -> list[str]:
    """Form methods a browser will not perform as written.

    An omitted `method` is a GET by specification and is left to the native
    POST/CSRF check in `test_web_conventions.py`; what this catches is the
    ACTIVE mistake — `method="put"`/`"delete"`, which HTML does not support.
    The browser silently performs a GET instead, converting a mutation into a
    CSRF-exempt safe method: F7's defect, reintroduced through markup.
    """
    found: list[str] = []
    for form in _FORM_OPEN.finditer(_strip_comments(text)):
        attr = _METHOD_ATTR.search(form.group("open"))
        if attr is None:
            continue
        value = attr.group("v").strip().lower()
        if value not in _VALID_FORM_METHODS:
            found.append(value)
    return found


def test_composed_forms_declare_a_method_html_actually_supports() -> None:
    offenders: list[str] = []
    scanned = 0
    for path in composed_templates():
        text = path.read_text(encoding="utf-8")
        scanned += len(_FORM_OPEN.findall(_strip_comments(text)))
        for value in invalid_form_methods(text):
            offenders.append(f"{_relative(path)}: method={value!r}")
    assert scanned, "no <form> elements scanned; the form-method check is vacuous"
    assert not offenders, (
        "form(s) declare a method HTML does not support — the browser "
        "degrades these to GET, turning a mutation into a CSRF-exempt safe "
        'method. Use method="post", or drive it with hx-put/hx-delete:\n'
        + "\n".join(offenders)
    )


def test_the_form_method_check_has_a_sensitivity_proof() -> None:
    """No template violates this today; prove the detector would still fire."""
    assert invalid_form_methods('<form method="put" action="/x">') == ["put"]
    assert invalid_form_methods('<form METHOD="DELETE">') == ["delete"]
    assert invalid_form_methods('<form method="post">') == []
    assert invalid_form_methods("<form>") == []
    assert invalid_form_methods('{# <form method="put"> #}') == []


# ---------------------------------------------------------------------------
# 4. No hardcoded facet navigation (two-directional ratchet, ADR-0018 § 3).
# ---------------------------------------------------------------------------


def _prefix_pattern(prefix: str) -> re.Pattern[str]:
    """Match `prefix` only at a path boundary.

    `/admin` must match `/admin`, `/admin/parties` and `/admin?x=1`, but never
    `/administration` — a different facet whose name merely starts the same way.
    """
    return re.compile(re.escape(prefix) + r"""(?=[/"'\s?#]|$)""")


def authored_facet_prefixes(text: str) -> dict[str, int]:
    """Count declared facet prefixes authored inside URL-bearing attributes.

    Restricted to `href`/`action`/`src`/`hx-*` values on purpose: a template
    NAME like `template_studio/admin/template_studio/_table.html` contains the
    same characters and is not navigation. Scanning raw text instead of
    attribute values reported 63 hits where 45 are real.
    """
    stripped = _strip_comments(text)
    counts: dict[str, int] = defaultdict(int)
    patterns = [(prefix, _prefix_pattern(prefix)) for prefix in facet_prefixes()]
    for attribute in _URL_ATTR.finditer(stripped):
        value = attribute.group("v")
        consumed: list[tuple[int, int]] = []
        for prefix, pattern in patterns:
            for hit in pattern.finditer(value):
                span = hit.span()
                if any(start <= span[0] < end for start, end in consumed):
                    continue
                consumed.append(span)
                counts[prefix] += 1
    return dict(counts)


def scan_repository() -> dict[str, dict[str, int]]:
    inventory: dict[str, dict[str, int]] = {}
    for path in composed_templates():
        counts = authored_facet_prefixes(path.read_text(encoding="utf-8"))
        if counts:
            inventory[_relative(path)] = dict(sorted(counts.items()))
    return inventory


def total_of(inventory: dict[str, dict[str, int]]) -> int:
    return sum(sum(counts.values()) for counts in inventory.values())


def load_baseline() -> dict[str, object]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _describe_drift(
    live: dict[str, dict[str, int]], recorded: dict[str, dict[str, int]]
) -> list[str]:
    problems: list[str] = []
    for name in sorted(set(live) | set(recorded)):
        current = live.get(name, {})
        frozen = recorded.get(name, {})
        for prefix in sorted(set(current) | set(frozen)):
            here, there = current.get(prefix, 0), frozen.get(prefix, 0)
            if here > there:
                problems.append(f"{name}: {prefix} rose {there} -> {here}")
            elif here < there:
                problems.append(
                    f"{name}: {prefix} fell {there} -> {here} (lower the baseline)"
                )
    return problems


def test_hardcoded_facet_navigation_matches_the_frozen_ratchet() -> None:
    """A URL attribute must not decide which facet the template is mounted in.

    `surface.landing_path`, `surface.logout_path` and `surface_url()` carry the
    answer from the assembly's declaration. A literal `/admin` in a template
    that a SECOND facet also composes — every `components/*` and `errors/*`
    file is exactly that — renders a link out of the facet the visitor is in.

    Two-directional: this fails when the count rises AND when it falls, so a
    slice that genuinely retires an authored prefix lowers the baseline in the
    same change and the reduction is reviewable as a diff.
    """
    recorded = load_baseline()["files"]
    assert isinstance(recorded, dict)
    problems = _describe_drift(scan_repository(), recorded)
    assert not problems, (
        "authored facet-prefix debt drifted from the baseline.\n"
        + "\n".join(f"  {line}" for line in problems)
        + "\n\nAuthoring a facet prefix in a template is not allowed. Use "
        "`surface.landing_path`/`surface.logout_path`/`surface_url()`. If you "
        "legitimately RETIRED one, lower the baseline in this same change:\n"
        "  make facet-nav-baseline"
    )


def test_the_navigation_baseline_total_agrees_with_its_entries() -> None:
    baseline = load_baseline()
    recorded: dict[str, dict[str, int]] = baseline["files"]  # type: ignore[assignment]
    assert baseline["total"] == total_of(recorded), (
        "baseline 'total' does not equal the sum of its entries; regenerate "
        "with `make facet-nav-baseline` rather than editing it by hand"
    )


def test_v2_surface_templates_author_no_facet_prefix() -> None:
    """The contract-v2 region is zero-pinned, not merely clean today.

    A v2 surface is mounted UNDER whichever prefix the assembly binds, so it
    cannot know its own path. `test_web_facet_contract.py` says this for
    Template Studio's one literal (`/admin/templates`); this says it for every
    declared surface package and every declared facet prefix, so the next v2
    surface inherits the rule without a second bespoke test.
    """
    roots = _surface_template_roots()
    assert roots, "no v2 surface template packages declared; this pin is vacuous"
    offenders: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.html")):
            counts = authored_facet_prefixes(path.read_text(encoding="utf-8"))
            if counts:
                offenders.append(f"{_relative(path)}: {counts}")
    assert not offenders, (
        "contract-v2 surface template(s) author an assembly facet prefix:\n"
        + "\n".join(offenders)
    )


def test_the_navigation_scanner_has_a_sensitivity_proof() -> None:
    """Pin the detector's real edges: it must fire, and must not over-fire."""
    prefix = facet_prefixes()[-1]  # the shortest declared prefix, e.g. "/admin"

    assert authored_facet_prefixes(f'<a href="{prefix}/parties">x</a>') == {prefix: 1}
    assert authored_facet_prefixes(
        f"<button hx-post=\"{{{{ surface.logout_path or '{prefix}/logout' }}}}\">"
    ) == {prefix: 1}
    # A template NAME is not navigation, even though it contains the same text.
    assert (
        authored_facet_prefixes(f'{{% include "studio{prefix}/_table.html" %}}') == {}
    )
    # A longer facet-like path must not be attributed to the shorter prefix.
    assert authored_facet_prefixes(f'<a href="{prefix}istration/x">y</a>') == {}
    # Commented-out prose documenting the anti-pattern must not count as debt.
    assert authored_facet_prefixes(f'{{# <a href="{prefix}/x"> #}}') == {}
    # The declaration-driven forms are the fix, and must stay clean.
    assert (
        authored_facet_prefixes('<a href="{{ surface.landing_path }}">home</a>') == {}
    )
