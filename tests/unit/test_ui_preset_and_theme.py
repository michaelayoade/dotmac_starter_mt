"""The two things a consumer needs before it can adopt the token layer.

`dotmac-ui` shipped 190 tokens and a dark theme, and no assembly used it. The
measured reason (`docs/inventories/ui-surface-inventory.md`) is that adopting it
still meant hand-writing two things per consumer: a Tailwind config mapping every
utility to a `--dmui-*` variable, and a pre-paint script to choose the theme.

`dotmac_academy_app` wrote both by hand, the script four times over, under an
attribute name that did not match this package's. Two copies of
`base/_tokens.css` in `dotmac_erp` and `dotmac_sub` have since drifted 48%.
Anything a consumer must copy will drift, so both are generated and shipped.
"""

from __future__ import annotations

import json

import pytest
from dotmac_ui import assets, build, contract, theme, tokens


def _preset() -> dict:
    """Parse the generated preset's exported object.

    `rindex`, not `index`: the usage comment at the top of the file also
    contains `module.exports`, and matching that one parses the comment.
    """
    source = build.render_tailwind_preset("0.0.0test")
    marker = "module.exports = "
    body = source[source.rindex(marker) + len(marker) :].rstrip().rstrip(";")
    return json.loads(body)


def test_breakpoints_are_literal_values_not_variables() -> None:
    """The bug this test exists for: `var()` does not work in a media query.

    `@media (min-width: var(--x))` is not valid CSS — custom properties are not
    substituted in a media condition. A preset emitting `var()` for `screens`
    compiles without complaint and silently breaks every responsive utility,
    which is the worst shape a bug can take. Caught before release; pinned so it
    cannot come back.
    """
    screens = _preset()["theme"]["extend"]["screens"]
    assert screens, "a preset with no breakpoints would pass vacuously"
    for name, value in screens.items():
        assert "var(" not in value, f"screens.{name} must be a literal, got {value!r}"


@pytest.mark.parametrize("key", ["spacing", "borderRadius", "boxShadow"])
def test_other_scales_do_use_variables(key: str) -> None:
    """Everything a media query does not touch must stay themeable.

    Baking these would make a brand override impossible, which is the opposite
    failure to the one above — hence both directions are pinned.
    """
    scale = _preset()["theme"]["extend"][key]
    assert scale
    assert all(v.startswith("var(--dmui-") for v in scale.values())


def test_every_colour_resolves_to_a_published_token() -> None:
    """A literal colour in the preset is a value a theme cannot move.

    Colours go through the channel form (`--dmui-x-rgb`) so alpha modifiers
    work; this checks the colour it derives from (`--dmui-x`) is a real
    published token, not an invented name that resolves to nothing.
    """
    declared = {t.variable for t in tokens.TOKENS}
    stack = [_preset()["theme"]["extend"]["colors"]]
    seen = 0
    while stack:
        node = stack.pop()
        for value in node.values():
            if isinstance(value, dict):
                stack.append(value)
                continue
            seen += 1
            inner = value[len("rgb(var(") : value.index(") / <alpha-value>)")]
            assert inner.endswith("-rgb"), f"{value} is not in channel form"
            assert inner[: -len("-rgb")] in declared, f"{inner} has no published token"
    assert seen > 100, f"expected the full palette, mapped only {seen}"


def test_colour_groups_do_not_stutter() -> None:
    """`text-*` and `border-*` are renamed for a reason.

    Tailwind derives utility names from the colour group, so a group called
    `text` yields `text-text-primary` and `border` yields `border-border-subtle`.
    """
    colors = _preset()["theme"]["extend"]["colors"]
    assert "content" in colors and "stroke" in colors
    assert "text" not in colors and "border" not in colors


def test_dark_mode_selector_matches_the_stylesheet() -> None:
    """The preset and the CSS must agree on what "dark" means."""
    assert _preset()["darkMode"][-1] == contract.DARK_THEME_SELECTORS[-1]


def test_preset_is_a_published_asset() -> None:
    assert assets.TAILWIND_PRESET_RELPATH in build.rendered_assets("0.0.0test")
    assert assets.tailwind_preset_path().is_file()


def test_bootstrap_sets_the_contract_attribute() -> None:
    script = theme.bootstrap_script()
    assert contract.THEME_ATTRIBUTE in script
    assert theme.THEME_STORAGE_KEY in script


def test_bootstrap_returns_source_not_a_tag() -> None:
    """The host owns the CSP nonce, so the host builds the tag."""
    script = theme.bootstrap_script()
    assert "<script" not in script and "</script>" not in script


def test_bootstrap_ignores_an_unrecognised_stored_value() -> None:
    """A stale or hand-edited preference must not be trusted.

    The script tests membership rather than truthiness, so `"DARK"` or a value
    from an older scheme falls through to the OS preference.
    """
    script = theme.bootstrap_script()
    assert "indexOf" in script
    assert '["light", "dark"]' in script.replace("'", '"')


def test_bootstrap_survives_storage_being_unavailable() -> None:
    """localStorage throws in Safari private mode and when embedded with
    third-party storage blocked. A theme script must never break a page."""
    script = theme.bootstrap_script()
    assert "try" in script and "catch" in script
    assert (
        script.count(contract.THEME_ATTRIBUTE) >= 2
    ), "the catch must still set a theme"


def test_set_theme_script_rejects_an_unknown_theme() -> None:
    with pytest.raises(ValueError):
        theme.set_theme_script("solarized")


@pytest.mark.parametrize("value", theme.THEME_VALUES)
def test_set_theme_script_writes_both_halves(value: str) -> None:
    """Storing without applying, or applying without storing, both look like a
    bug that only appears on reload."""
    script = theme.set_theme_script(value)
    assert theme.THEME_STORAGE_KEY in script
    assert contract.THEME_ATTRIBUTE in script
    assert script.count(f"'{value}'") >= 2


# --- alpha modifiers -------------------------------------------------------
#
# `bg-brand-500/50` is not a nicety: erp uses 4,372 opacity modifiers, sub
# 5,872, academy 48. A token layer that cannot express alpha cannot be adopted
# by any of them, and the failure is silent — the utility renders opaque.


def test_every_preset_colour_supports_an_alpha_modifier() -> None:
    """Tailwind can only synthesise alpha from separate channels.

    A variable holding a complete colour renders opaque with no warning, so
    every colour must go through the channel form.
    """
    stack = [_preset()["theme"]["extend"]["colors"]]
    checked = 0
    while stack:
        for value in stack.pop().values():
            if isinstance(value, dict):
                stack.append(value)
                continue
            checked += 1
            assert "<alpha-value>" in value, f"{value} cannot take an opacity modifier"
            assert value.startswith("rgb(var(--dmui-")
            assert value.endswith("-rgb) / <alpha-value>)")
    assert checked > 100, f"expected the full palette, checked {checked}"


def test_channel_tokens_exist_for_every_colour() -> None:
    css = build.render_stylesheet("0.0.0test")
    published = set(tokens.variable_names())
    for design_token in tokens.colour_tokens():
        variable = tokens.channel_variable(design_token.name)
        assert (
            f"{variable}:" in css
        ), f"{variable} is referenced by the preset but never declared"
        assert variable in published, (
            f"{variable} is emitted but missing from variable_names() — that "
            "would make it an undocumented public name"
        )


def test_dark_mode_restates_the_channel_forms() -> None:
    """The subtle half: an alpha utility must darken with its colour.

    If only the whole-colour variables are restated for dark, `bg-surface-primary/50`
    keeps rendering the LIGHT surface at 50% — a bug that survives a screenshot
    review because most of the page looks right.
    """
    css = build.render_stylesheet("0.0.0test")
    dark = css[css.index(contract.DARK_THEME_SELECTORS[-1]) :]
    changing = [
        t
        for t in tokens.tokens_in("surface")
        if tokens.resolve_color(t.name, "light") != tokens.resolve_color(t.name, "dark")
    ]
    assert changing, "no surface token differs between modes — fixture is wrong"
    for design_token in changing:
        assert f"{tokens.channel_variable(design_token.name)}:" in dark


@pytest.mark.parametrize(
    "hex_colour,expected",
    [("#3b82f6", "59 130 246"), ("#fff", "255 255 255"), ("#000000", "0 0 0")],
)
def test_channels_conversion(hex_colour: str, expected: str) -> None:
    assert build._channels(hex_colour) == expected


def test_channels_rejects_a_malformed_colour() -> None:
    """Silently emitting a broken channel string would produce invalid CSS that
    the browser drops, leaving the element transparent."""
    with pytest.raises(ValueError):
        build._channels("#12345")
