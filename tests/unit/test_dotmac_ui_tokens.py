"""The `dotmac-ui` token vocabulary and its compiled-asset boundary.

These are the tests that make ADR-0006 D3 a contract rather than a paragraph.
D3 says a consumer gets *compiled, versioned, self-hosted* assets and never runs
this package through its own toolchain — which is only true if the committed
artifact really is the token source's output, really is self-contained, and
really has a stable, versioned identity. Each of those is asserted here.
"""

from __future__ import annotations

import ast
import re
import tomllib
from hashlib import sha256
from pathlib import Path

import dotmac_ui
import pytest
from dotmac_ui import build, tokens

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
UI_PYPROJECT = REPO_ROOT / "packages/dotmac-ui/pyproject.toml"

STYLESHEET = dotmac_ui.stylesheet_path().read_text(encoding="utf-8")

#: The stylesheet with `/* … */` comments removed. The self-containment and
#: no-preprocessor checks below run against THIS, not the raw text: the
#: generated banner necessarily talks about `@import`, CDNs, and `@layer` in
#: order to explain that it uses none of them, and a checker that cannot tell a
#: rule from a sentence about a rule is a checker nobody will trust.
STYLESHEET_RULES = re.sub(r"/\*.*?\*/", "", STYLESHEET, flags=re.DOTALL)


# ── The vocabulary ──────────────────────────────────────────────────────────


def test_every_token_name_is_unique_and_categorised() -> None:
    names = dotmac_ui.token_names()
    assert len(names) == len(set(names)), "duplicate token name"
    for design_token in dotmac_ui.TOKENS:
        assert design_token.category in dotmac_ui.CATEGORIES
        assert design_token.description, f"{design_token.name} has no description"


def test_every_category_the_contract_promises_is_populated() -> None:
    """The categories COMPATIBILITY.md advertises must actually exist.

    Listed explicitly rather than derived from `CATEGORIES` so that deleting a
    whole category (the way a vocabulary actually erodes) fails here instead of
    silently shrinking the list it is checked against.
    """
    for category in (
        "color",
        "surface",
        "text",
        "border",
        "action",
        "status",
        "typography",
        "space",
        "radius",
        "shadow",
        "focus",
        "breakpoint",
        "motion",
    ):
        assert dotmac_ui.tokens_in(category), f"category {category} is empty"


def test_every_action_intent_declares_every_interaction_state() -> None:
    """The gap this vocabulary exists to close (`dotmac_sub` has no `-hover`,
    `-pressed`, `-disabled`, or `-on-*` token at all) must not reopen one
    intent at a time."""
    for intent in dotmac_ui.ACTION_INTENTS:
        for state in dotmac_ui.ACTION_STATES:
            dotmac_ui.token(f"action-{intent}-{state}")


def test_every_semantic_intent_declares_the_full_status_quartet() -> None:
    for intent in dotmac_ui.SEMANTIC_INTENTS:
        for role in ("surface", "border", "foreground", "indicator"):
            assert dotmac_ui.token(f"status-{intent}-{role}").is_mode_dependent


def test_every_colour_token_resolves_to_a_literal_in_both_modes() -> None:
    """A `var()` chain that dead-ends, cycles, or lands on a non-colour is a
    token that renders as nothing. `resolve_color` raises on all three."""
    colour_categories = {"color", "surface", "text", "action", "status"}
    for design_token in dotmac_ui.TOKENS:
        if design_token.category not in colour_categories:
            continue
        for mode in dotmac_ui.MODES:
            resolved = dotmac_ui.resolve_color(design_token.name, mode)
            assert re.fullmatch(
                r"#[0-9a-f]{6}", resolved
            ), f"{design_token.name} ({mode}) resolved to {resolved!r}"


def test_resolving_a_non_colour_token_fails_loudly() -> None:
    """`resolve_color` is used by the contrast checker; a silent fallback to
    black would turn a mistyped pair into a passing test."""
    with pytest.raises(ValueError, match="not a hex colour"):
        dotmac_ui.resolve_color("space-md")
    with pytest.raises(KeyError):
        dotmac_ui.resolve_color("no-such-token")


def test_ramps_are_mode_independent() -> None:
    """A ramp step is the same colour in both modes; what changes is which step
    a ROLE points at. Restating ramps in the dark block would force a runtime
    brand override to be written twice."""
    for design_token in dotmac_ui.tokens_in("color"):
        assert (
            not design_token.is_mode_dependent
        ), f"{design_token.name} declares a dark value; ramps must not"


# ── The compiled artifact (ADR-0006 D3) ─────────────────────────────────────


def test_committed_stylesheet_matches_a_fresh_build() -> None:
    """The committed artifact IS the token source's output — not a fork of it.

    Without this, editing the CSS by hand would work perfectly until the next
    `make ui-build` silently reverted it.
    """
    assert STYLESHEET == build.render_stylesheet(dotmac_ui.__version__), (
        "packages/dotmac-ui/src/dotmac_ui/static/… is stale or hand-edited; "
        "run `make ui-build` and commit the result"
    )


def test_committed_manifest_matches_a_fresh_build() -> None:
    manifest_text = dotmac_ui.assets.manifest_path().read_text(encoding="utf-8")
    assert manifest_text == build.render_manifest(dotmac_ui.__version__, STYLESHEET)


def test_every_declared_token_is_emitted_in_the_stylesheet() -> None:
    missing = [
        variable
        for variable in dotmac_ui.variable_names()
        if f"{variable}:" not in STYLESHEET
    ]
    assert not missing, f"declared but never emitted: {missing}"


def test_the_stylesheet_declares_no_undeclared_token() -> None:
    """The reverse direction. A `--dmui-*` property in the CSS that no
    `DesignToken` declares is an undocumented public name — exactly what
    COMPATIBILITY.md would then be lying about."""
    declared = set(dotmac_ui.variable_names())
    emitted = set(re.findall(r"(--dmui-[a-z0-9-]+)\s*:", STYLESHEET))
    assert emitted - declared == set(), f"undeclared properties: {emitted - declared}"


def test_every_var_reference_in_the_stylesheet_points_at_a_declared_token() -> None:
    declared = set(dotmac_ui.variable_names())
    referenced = set(re.findall(r"var\((--dmui-[a-z0-9-]+)\)", STYLESHEET))
    assert referenced <= declared, f"dangling references: {referenced - declared}"


def test_stylesheet_references_no_external_origin() -> None:
    """Self-hosted, CSP-clean, air-gap-safe.

    The fleet has a no-CDN standard and ADR-0006 D7 makes the kernel own a
    deny-by-default CSP; an `@import`, a remote font, or a `url(https://…)` in
    the shared design system would defeat both for every product at once, and
    would break an air-gapped deployment on first paint.
    """
    for pattern in (r"https?://", r"//cdn", r"@import", r"@font-face"):
        assert not re.search(pattern, STYLESHEET_RULES), (
            f"compiled stylesheet matches {pattern!r} — assets must be "
            "self-hosted and self-contained"
        )
    # url() is allowed only for a data: URI; nothing today uses one at all.
    for url in re.findall(r"url\(([^)]*)\)", STYLESHEET_RULES):
        assert url.strip().strip("\"'").startswith("data:"), f"remote url(): {url}"


def test_stylesheet_needs_no_preprocessor() -> None:
    """The D3 promise in its most literal form: a consumer on Tailwind v3 (ERP),
    v4 (Sub, this repo), or no Tailwind at all links this file as-is. Any
    at-rule that a compiler must expand would make that false."""
    for directive in ("@tailwind", "@apply", "@theme", "@source", "@config", "@layer"):
        assert directive not in STYLESHEET_RULES, (
            f"{directive} requires the consumer to run a compiler — see " "ADR-0006 D3"
        )


def test_reduced_motion_block_neutralises_every_duration() -> None:
    block = STYLESHEET.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    block = block.split("\n}\n", 1)[0]
    durations = [t for t in dotmac_ui.tokens_in("motion") if "duration" in t.name]
    assert durations
    for design_token in durations:
        assert f"{design_token.variable}: {tokens.REDUCED_MOTION_DURATION};" in block


def test_dark_values_are_emitted_under_both_supported_selectors() -> None:
    for selector in dotmac_ui.DARK_THEME_SELECTORS:
        assert selector in STYLESHEET
    dark_block = STYLESHEET.split(dotmac_ui.DARK_THEME_SELECTORS[-1] + " {", 1)[1]
    dark_block = dark_block.split("\n}\n", 1)[0]
    for design_token in dotmac_ui.TOKENS:
        if design_token.is_mode_dependent:
            assert f"{design_token.variable}: {design_token.dark};" in dark_block


# ── Versioned identity ──────────────────────────────────────────────────────


def test_the_ui_contract_version_is_in_the_asset_path() -> None:
    """Contract-versioned, not package-versioned: a value-only patch release
    must not move every consumer's `<link href>`, and a contract 2 must be able
    to ship beside contract 1."""
    assert (
        f"dotmac-ui-{dotmac_ui.UI_CONTRACT_VERSION}.css" in dotmac_ui.STYLESHEET_RELPATH
    )
    assert dotmac_ui.UI_CONTRACT_VERSION in dotmac_ui.SUPPORTED_UI_CONTRACT_VERSIONS


def test_the_ui_contract_version_is_owned_by_this_package_alone() -> None:
    """ADR-0006 § 1's "two version axes". The UI contract version must be a
    constant this package declares — never derived from, aliased to, or
    imported from the kernel's module contract version. The forbidden-import
    guard covers the mechanism; this covers the intent.
    """
    source = (REPO_ROOT / "packages/dotmac-ui/src/dotmac_ui/contract.py").read_text()
    code = ast.parse(source)
    assignments = {
        target.id: node.value
        for node in ast.walk(code)
        if isinstance(node, ast.AnnAssign | ast.Assign)
        for target in (
            [node.target] if isinstance(node, ast.AnnAssign) else node.targets
        )
        if isinstance(target, ast.Name)
    }
    version = assignments["UI_CONTRACT_VERSION"]
    assert isinstance(version, ast.Constant) and isinstance(version.value, int), (
        "UI_CONTRACT_VERSION must be a literal this package owns, not a value "
        "derived from another package's contract version"
    )
    assert version.value == dotmac_ui.UI_CONTRACT_VERSION


def test_declared_version_matches_pyproject() -> None:
    declared = tomllib.loads(UI_PYPROJECT.read_text())["tool"]["poetry"]["version"]
    assert dotmac_ui.__version__ == declared


def test_asset_digest_is_the_file_digest_and_appears_in_the_url() -> None:
    full = sha256(dotmac_ui.stylesheet_path().read_bytes()).hexdigest()
    assert dotmac_ui.asset_digest() == full[: dotmac_ui.assets.DIGEST_LENGTH]
    manifest_assets = dotmac_ui.asset_manifest()["assets"]
    assert isinstance(manifest_assets, list)
    assert manifest_assets[0]["sha256"] == full
    url = dotmac_ui.stylesheet_url()
    assert url == f"/static/{dotmac_ui.STYLESHEET_RELPATH}?v={dotmac_ui.asset_digest()}"
    assert not url.startswith("http"), "asset URLs are same-origin, never a CDN"


def test_stylesheet_url_honours_a_non_default_mount() -> None:
    assert dotmac_ui.stylesheet_url("/assets/").startswith(
        f"/assets/{dotmac_ui.STYLESHEET_RELPATH}?v="
    )
