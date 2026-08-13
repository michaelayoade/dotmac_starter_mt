"""The first admin shell slice consumes semantic presentation roles end to end.

The palette ratchet proves the five templates carry no literal Tailwind colour.
These tests bind the replacement to the other two halves of the contract: every
referenced variable is published by ``dotmac-ui``, colour mode comes from the
token layer rather than per-element ``dark:`` branches, and the visible primary
actions terminate in the runtime-generated brand ramp.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from dotmac_ui import tokens

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SHELL_TEMPLATES: Final[tuple[str, ...]] = (
    "packages/dotmac-kernel/src/dotmac_kernel/templates/base.html",
    "packages/dotmac-kernel/src/dotmac_kernel/templates/auth/login.html",
    "packages/dotmac-kernel/src/dotmac_kernel/templates/layouts/admin.html",
    "packages/dotmac-kernel/src/dotmac_kernel/templates/components/sidebar.html",
    "packages/dotmac-kernel/src/dotmac_kernel/templates/components/topbar.html",
)
_TOKEN_REFERENCE: Final[re.Pattern[str]] = re.compile(r"var\((--dmui-[a-z0-9-]+)\)")
_DARK_COLOUR_BRANCH: Final[re.Pattern[str]] = re.compile(
    r"\bdark:(?:bg|text|border|ring|divide|from|via|to|shadow)-"
)


def _sources() -> dict[str, str]:
    return {
        relative: (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        for relative in SHELL_TEMPLATES
    }


def test_every_shell_token_reference_is_published() -> None:
    published = set(tokens.variable_names())
    references = {
        reference
        for source in _sources().values()
        for reference in _TOKEN_REFERENCE.findall(source)
    }

    assert references, "the shell must consume the token contract, not be colourless"
    assert references <= published, sorted(references - published)


def test_shell_leaves_light_and_dark_values_to_the_token_layer() -> None:
    offenders = {
        relative: sorted(set(_DARK_COLOUR_BRANCH.findall(source)))
        for relative, source in _sources().items()
        if _DARK_COLOUR_BRANCH.search(source)
    }

    assert not offenders, (
        "semantic roles already change under the package's dark selector; "
        f"per-element colour branches create a second theme path: {offenders}"
    )
    for role in (
        "surface-background",
        "surface-primary",
        "surface-elevated",
        "text-primary",
        "border-default",
    ):
        assert tokens.resolve_color(role, "light") != tokens.resolve_color(role, "dark")


def test_visible_primary_shell_actions_terminate_in_the_brand_ramp() -> None:
    sources = "\n".join(_sources().values())
    for role in (
        "action-primary-default",
        "action-primary-hover",
        "action-primary-on",
    ):
        assert f"var(--dmui-{role})" in sources

    for mode in tokens.MODES:
        terminal = tokens.reference_target(
            tokens.token("action-primary-default").value_for(mode)
        )
        assert terminal is not None
        assert terminal.startswith("color-brand-")


def test_overlay_uses_the_dedicated_surface_role() -> None:
    layout = _sources()[
        "packages/dotmac-kernel/src/dotmac_kernel/templates/layouts/admin.html"
    ]

    assert "var(--dmui-surface-overlay)" in layout
    assert tokens.token("surface-overlay").category == "surface"
