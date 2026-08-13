"""A brand override, generated from seed colours rather than hand-authored.

## What this replaces

`dotmac_kernel.branding` formerly accepted a `custom_css` field, sanitized it
with a regex, and rendered it `| safe` into a `<style>` block. ADR-0006 **D8
retired that capability**, and kernel 0.1.0a47 removed it: raw CSS can hide or
rewrite legal text, overlay same-origin controls, create lockouts, and
exfiltrate through attribute selectors, and a keyword sanitizer cannot make it
safe. This module is the replacement D8 names
— *"an allowlisted, validated token/asset/theme surface expanded into generated
CSS by the owning branding service"*.

## Why generated, and not a `:root` block each product writes

Two reasons, and the second is newer and sharper.

1. Anything a consumer must copy will drift. `dotmac_erp` and `dotmac_sub`
   already prove it: one CSS system, forked once, now 24% apart.
2. **Since 0.1.0a3 every colour has two published variables** — the whole
   colour and its `-rgb` channel form. A hand-written override that sets
   `--dmui-color-brand-500` but not `--dmui-color-brand-500-rgb` produces the
   brand on solid fills and the *placeholder blue* on every translucent one,
   silently. The fleet uses roughly 10,300 opacity modifiers. Only something
   that knows both forms exist can emit them consistently, so nothing else
   should be writing them.

## Why a seed, and not eleven hand-picked steps

Brand data in the wild is one or two colours — `dotmac_kernel.branding` stores
exactly `primary_color` and `accent_color`. The token layer's roles derive from
eleven-step ramps, so something has to bridge the two. Asking an operator for
eleven steps re-creates the drift problem inside the brand record; deriving ten
of them from one puts the derivation in one place.

The curve below is measured from the ramps this package already ships, so a
generated ramp has the same *shape* as the built-in one — a brand override
changes the hue, not the feel of the system.

Ramps are generated in OKLCH because the two things a ramp must do — hold a hue
while lightness marches, and stay legible at every step — are properties of a
perceptual space and accidents of sRGB. The output is ordinary sRGB hex; see
`dotmac_ui.color` for the gamut clamp that guarantees it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from dotmac_ui.a11y import check_contrast
from dotmac_ui.color import OKLCH, hex_to_oklch, oklch_to_hex, parse_hex
from dotmac_ui.contract import TOKEN_PREFIX
from dotmac_ui.tokens import RAMP_STEPS, channel_variable

__all__ = [
    "BrandOverride",
    "BrandWarning",
    "GeneratedBrand",
    "generate_ramp",
    "render_brand_css",
]

#: Lightness per step, measured in OKLCH from the built-in BRAND ramp.
#:
#: An earlier version averaged the brand and accent ramps. That was wrong: the
#: two differ by up to 0.09 at the same step (accent-500 sits at 0.715, brand-500
#: at 0.623), so the mean matched neither, and seeding with the package's own
#: `#3b82f6` anchored one step off — it landed on 600. A curve taken from a
#: single real ramp regenerates that ramp exactly, which is the property
#: `test_regenerating_the_builtin_seed_reproduces_the_builtin_ramp` pins.
_LIGHTNESS: Final[tuple[float, ...]] = (
    0.970,
    0.932,
    0.882,
    0.809,
    0.714,
    0.623,
    0.546,
    0.488,
    0.424,
    0.379,
    0.282,
)

#: Chroma per step as a fraction of the ramp's peak, measured from the built-in
#: brand ramp. Chroma is NOT flat across a ramp: the pale and dark ends carry
#: much less, and a ramp generated with constant chroma looks like a stack of
#: unrelated colours rather than one colour at eleven lightnesses.
_CHROMA_SHAPE: Final[tuple[float, ...]] = (
    0.0654,
    0.1455,
    0.2629,
    0.4402,
    0.6602,
    0.8656,
    0.9908,
    1.0,
    0.8329,
    0.6344,
    0.4024,
)

#: Hue offset per step, in degrees, relative to the anchor's own hue — also
#: measured from the built-in brand ramp.
#:
#: A ramp is not one hue at eleven lightnesses: the pale end drifts warm and the
#: dark end cool, by up to 8°. Holding hue constant produced a ramp that was
#: arithmetically defensible and looked wrong — its 600 and 700, the steps
#: primary buttons use, came out visibly more saturated than the built-in ones.
#: Modelling the drift is also what makes regeneration from a built-in seed
#: reproduce the whole built-in ramp rather than only its anchor step.
_HUE_DRIFT: Final[tuple[float, ...]] = (
    -5.2,
    -4.2,
    -5.7,
    -8.0,
    -5.2,
    0.0,
    3.1,
    4.6,
    5.8,
    5.7,
    8.1,
)


@dataclass(frozen=True, slots=True)
class BrandWarning:
    """Something a generated brand does that an operator should be told about.

    A warning, never a silent adjustment: D8 requires that unsupported or
    altered branding input be *reported to the caller*, not quietly dropped.
    """

    token: str
    message: str


@dataclass(frozen=True, slots=True)
class BrandOverride:
    """The brand data this package accepts. Colours only — no CSS, ever.

    `primary` and `accent` are the two fields `dotmac_kernel.branding` already
    stores, so an existing brand record maps onto this without a migration.
    """

    primary: str
    accent: str | None = None

    def __post_init__(self) -> None:
        # Validate at construction: a malformed brand colour must fail where it
        # was entered, not when a page renders.
        parse_hex(self.primary)
        if self.accent is not None:
            parse_hex(self.accent)


@dataclass(frozen=True, slots=True)
class GeneratedBrand:
    """The CSS to serve, and everything the operator should know about it."""

    css: str
    warnings: tuple[BrandWarning, ...]

    @property
    def is_clean(self) -> bool:
        return not self.warnings


def generate_ramp(seed: str) -> dict[str, str]:
    """Eleven steps from one colour, with the seed itself appearing exactly.

    The seed is placed at the step whose target lightness is closest to its
    own, and that step is pinned to the seed verbatim — an operator who enters
    their brand colour must be able to find that exact colour in the result,
    not a near miss produced by a round trip.
    """
    base = hex_to_oklch(seed)
    anchor = _anchor_index(base.lightness)

    # Scale the shape so the curve passes through the seed's own chroma at the
    # anchor. Without this, a muted brand would be regenerated as a vivid one.
    shape_at_anchor = _CHROMA_SHAPE[anchor] or 1.0
    scale = base.chroma / shape_at_anchor

    ramp: dict[str, str] = {}
    for index, step in enumerate(RAMP_STEPS):
        if index == anchor:
            ramp[step] = _normalise(seed)
            continue
        colour = OKLCH(
            _LIGHTNESS[index],
            _CHROMA_SHAPE[index] * scale,
            (base.hue + _HUE_DRIFT[index] - _HUE_DRIFT[anchor]) % 360.0,
        )
        ramp[step] = oklch_to_hex(colour).hex_value
    return ramp


def _anchor_index(lightness: float) -> int:
    """The step whose target lightness is nearest the seed's."""
    return min(range(len(RAMP_STEPS)), key=lambda i: abs(_LIGHTNESS[i] - lightness))


def _normalise(value: str) -> str:
    """`#ABC` and `#AABBCC` are the same colour; emit one spelling."""
    return "#" + "".join(f"{round(c * 255):02x}" for c in parse_hex(value))


# There is deliberately NO warning for a step whose chroma sRGB had to reduce.
#
# The clamp holds lightness and hue and lands on the most saturated renderable
# colour at that step, so the result is the best available answer, not a
# degraded one. Measured: seeding with this package's OWN accent (`#06b6d4`)
# reports six clamped steps while producing a ramp within 0.012 chroma of the
# built-in one at every step. Those six warnings describe an internal over-ask
# by the shared chroma curve — something the operator did not choose, cannot
# act on, and would learn to scroll past, taking the contrast warnings with it.
#
# The one colour the operator actually supplied is pinned verbatim at its anchor
# and never clamped, so nothing they chose is ever silently altered. D8's
# reporting duty is about dropped INPUT, and no input is dropped here.


def _contrast_warnings(palette: dict[str, str]) -> list[BrandWarning]:
    """Run the package's OWN contrast contract against the generated palette.

    Not a second gate written for brands: `CONTRAST_REQUIREMENTS` is every pair
    the vocabulary claims will be used together, in both modes, and the built-in
    ramps are held to it by the test suite. Re-deriving a simpler check here
    would hold overrides to a *weaker* standard than the defaults — and it did:
    the first version compared `color-<name>-600` to the page background, which
    fails the package's own accent ramp (3.68:1) because a filled action's
    contrast is white-on-fill and accent deliberately sits one step deeper.
    """
    return [
        BrandWarning(failure.requirement.foreground, failure.describe())
        for failure in check_contrast(overrides=palette)
    ]


def render_brand_css(
    override: BrandOverride, *, selector: str = ":root"
) -> GeneratedBrand:
    """The `<style>`-able CSS for a brand, plus what the operator should know.

    Emits **both** published forms for every generated colour — the whole
    colour and its `-rgb` channel form. Emitting only the first is the silent
    failure this module exists to make impossible.

    The result contains only custom-property declarations inside one selector:
    there is no place for a URL, an `@import`, a selector of the caller's
    choosing, or anything else that made `custom_css` unsafe.
    """
    warnings: list[BrandWarning] = []
    palette: dict[str, str] = {}

    for name, seed in (("brand", override.primary), ("accent", override.accent)):
        if seed is None:
            continue
        for step, value in generate_ramp(seed).items():
            palette[f"color-{name}-{step}"] = value

    # Contrast is checked on the WHOLE palette at once, after both ramps exist:
    # several requirements pair a brand role with an accent one, and checking
    # each ramp as it is generated would measure it against built-in values the
    # product is about to replace.
    warnings.extend(_contrast_warnings(palette))

    lines = [
        line
        for token_name, value in palette.items()
        for line in (
            f"  {TOKEN_PREFIX}{token_name}: {value};",
            f"  {channel_variable(token_name)}: {_channels(value)};",
        )
    ]
    body = "\n".join(lines)
    return GeneratedBrand(f"{selector} {{\n{body}\n}}\n", tuple(warnings))


def _channels(hex_colour: str) -> str:
    return " ".join(str(round(c * 255)) for c in parse_hex(hex_colour))
