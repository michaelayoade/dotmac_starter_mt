"""What a brand override must guarantee before it can replace `custom_css`.

`dotmac_kernel.branding` formerly accepted raw CSS, regex-sanitized and rendered
`| safe` into a `<style>` block — the capability ADR-0006 D8 retired. The
generated surface must be at least as expressive for legitimate colour use and
strictly narrower for everything else, which is what these pin.
"""

from __future__ import annotations

import re

import pytest
from dotmac_ui import a11y, brand, color, tokens

_HEX = re.compile(r"^#[0-9a-f]{6}$")


def test_regenerating_the_builtin_seed_reproduces_the_builtin_ramp() -> None:
    """The curve is measured from the built-in brand ramp, so it must return it.

    This is the calibration test. It caught two real errors: averaging the brand
    and accent lightness profiles (which anchored `#3b82f6` on step 600 instead
    of 500), and holding hue constant (which made 600 and 700 — the steps
    primary buttons use — visibly more saturated than the built-in ones).
    """
    generated = brand.generate_ramp("#3b82f6")
    for step in tokens.RAMP_STEPS:
        assert generated[step] == tokens.resolve_color(
            f"color-brand-{step}", "light"
        ), f"step {step} does not reproduce the ramp the curve was measured from"


def test_the_seed_appears_verbatim_in_its_ramp() -> None:
    """An operator must find the exact colour they entered, not a round trip."""
    for seed in ("#006340", "#ff6d00", "#123456"):
        assert seed in brand.generate_ramp(seed).values()


def test_a_three_digit_seed_is_accepted_and_normalised() -> None:
    assert "#aabbcc" in brand.generate_ramp("#abc").values()


def test_both_published_forms_are_emitted_for_every_colour() -> None:
    """The 0.1.0a3 trap, and the reason this module exists at all.

    A hand-written override that sets `--dmui-color-brand-500` but not
    `--dmui-color-brand-500-rgb` renders the brand on solid fills and the
    placeholder blue on every translucent one, silently.
    """
    css = brand.render_brand_css(brand.BrandOverride("#006340", "#c2703a")).css
    for name in ("brand", "accent"):
        for step in tokens.RAMP_STEPS:
            token_name = f"color-{name}-{step}"
            assert f"--dmui-{token_name}:" in css
            assert f"{tokens.channel_variable(token_name)}:" in css


def test_the_two_forms_always_agree() -> None:
    """A channel form that does not match its own colour is worse than neither."""
    css = brand.render_brand_css(brand.BrandOverride("#8e44ad")).css
    colours = dict(re.findall(r"--dmui-(\S+?): (#[0-9a-f]{6});", css))
    channels = dict(re.findall(r"--dmui-(\S+?)-rgb: (\d+ \d+ \d+);", css))
    assert colours and channels.keys() == colours.keys()
    for name, hex_value in colours.items():
        expected = " ".join(str(round(c * 255)) for c in color.parse_hex(hex_value))
        assert channels[name] == expected


def test_dependent_role_channels_follow_the_brand_in_both_modes() -> None:
    """The defect Train 3 found: aliases had compiled placeholder channels.

    Whole role variables already follow the ramp through `var()`. Their channel
    forms are literals in the compiled asset, though, and dark mode can point a
    role at a different step. The generated layer must therefore re-project
    both aliases after the package CSS.
    """
    css = brand.render_brand_css(brand.BrandOverride("#112233", "#445566")).css

    light, dark = css.split("\n.dark,\n", 1)
    assert (
        "--dmui-action-primary-default-rgb: " "var(--dmui-color-brand-600-rgb);"
    ) in light
    assert (
        "--dmui-action-accent-default-rgb: " "var(--dmui-color-accent-700-rgb);"
    ) in light
    assert (
        "--dmui-action-primary-default-rgb: " "var(--dmui-color-brand-400-rgb);"
    ) in dark
    assert (
        "--dmui-action-accent-default-rgb: " "var(--dmui-color-accent-400-rgb);"
    ) in dark


def test_brand_channel_projection_does_not_overwrite_unrelated_theme_roles() -> None:
    css = brand.render_brand_css(brand.BrandOverride("#112233")).css

    assert "--dmui-action-destructive-default-rgb:" not in css
    assert "--dmui-status-negative-foreground-rgb:" not in css


def test_every_generated_step_is_renderable_srgb() -> None:
    """OKLCH can name colours sRGB cannot show; the clamp must catch all of them."""
    for seed in ("#00ff00", "#ff00ff", "#0000ff", "#006340"):
        for value in brand.generate_ramp(seed).values():
            assert _HEX.match(value), f"{value} is not a plain sRGB hex colour"


@pytest.mark.parametrize("seed", ["", "#12345", "not-a-colour", "#gggggg", "#"])
def test_a_malformed_seed_is_rejected_at_construction(seed: str) -> None:
    """Fail where the colour was entered, not when a page renders."""
    with pytest.raises(ValueError):
        brand.BrandOverride(primary=seed)


def test_generated_css_carries_nothing_but_declarations() -> None:
    """The security property. `custom_css` could carry all of these; this cannot.

    Not a sanitizer — the output is assembled from validated colours, so there
    is no path by which any of this could appear. The test states the invariant
    a reviewer would otherwise have to re-derive from the implementation.
    """
    css = brand.render_brand_css(brand.BrandOverride("#006340", "#c2703a")).css
    for forbidden in (
        "@import",
        "url(",
        "expression(",
        "behavior:",
        "javascript:",
        "<",
    ):
        assert forbidden not in css
    structural_lines = {
        ":root {",
        ".dark,",
        '[data-dmui-theme="dark"] {',
        "}",
    }
    for line in filter(None, (line.strip() for line in css.splitlines())):
        if line in structural_lines:
            continue
        assert re.fullmatch(r"--dmui-[a-z0-9-]+: [^;{}]+;", line), line


def test_the_selector_is_the_only_thing_a_caller_chooses() -> None:
    css = brand.render_brand_css(
        brand.BrandOverride("#006340"), selector="[data-t]"
    ).css
    assert css.startswith("[data-t] {")


# --- the structural guarantee ---------------------------------------------
#
# Contrast is not checked per brand and hoped for: pinning lightness to the
# curve makes legibility a property of the generator. These prove it rather
# than assume it.


@pytest.mark.parametrize("hue", range(0, 360, 15))
def test_a_generated_brand_meets_the_contrast_contract_at_every_hue(hue: int) -> None:
    """Every hue, at the most saturated seed sRGB can express for it.

    Checked against `a11y.CONTRAST_REQUIREMENTS` — the package's OWN pair list,
    the same one the built-in ramps are held to. A weaker check written for
    brands would hold overrides to a lower standard than the defaults, which is
    what the first version of this did.
    """
    seed = color.oklch_to_hex(color.OKLCH(0.62, 0.30, hue)).hex_value
    result = brand.render_brand_css(brand.BrandOverride(primary=seed))
    assert result.is_clean, [warning.message for warning in result.warnings]


def test_the_contrast_check_can_actually_fail() -> None:
    """Guard the guard: a check that cannot fail proves nothing.

    The generator will not produce an illegible palette, so the failing case
    has to be constructed — an override that makes the primary action fill the
    same colour as the white text it carries.
    """
    failures = a11y.check_contrast(
        overrides={f"color-brand-{step}": "#ffffff" for step in tokens.RAMP_STEPS}
    )
    assert failures, "check_contrast reported nothing for a white-on-white palette"


def test_an_override_moves_the_roles_derived_from_it() -> None:
    """The whole point of overriding a ramp rather than every role.

    `action-primary-default` IS `color-brand-600`, so replacing the ramp must
    move the button with it. If resolution ignored overrides mid-chain, a brand
    would apply to raw colours and leave every action on the placeholder.
    """
    overrides = {"color-brand-600": "#006340"}
    assert (
        tokens.resolve_color("action-primary-default", "light", overrides) == "#006340"
    )
    assert tokens.resolve_color("action-primary-default", "light") != "#006340"


def test_accent_is_optional_and_absent_means_untouched() -> None:
    css = brand.render_brand_css(brand.BrandOverride("#006340")).css
    assert "--dmui-color-brand-500:" in css
    assert "color-accent" not in css


# --- colour space ----------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["#eff6ff", "#3b82f6", "#1d4ed8", "#172554", "#000000"]
)
def test_oklch_round_trips_exactly(value: str) -> None:
    assert color.oklch_to_hex(color.hex_to_oklch(value)).hex_value == value


def test_out_of_gamut_loses_chroma_and_keeps_lightness_and_hue() -> None:
    """Which two properties survive the clamp is the whole design decision.

    A step that shifts hue to stay in gamut reads as a different colour; one
    that loses chroma reads as the same colour slightly muted.
    """
    requested = color.OKLCH(0.62, 0.40, 150)  # far outside sRGB
    result = color.oklch_to_hex(requested)
    assert result.was_clamped and result.chroma_lost > 0
    landed = color.hex_to_oklch(result.hex_value)
    assert abs(landed.lightness - requested.lightness) < 0.01
    assert abs(landed.hue - requested.hue) < 1.0
    assert landed.chroma < requested.chroma
