"""sRGB ↔ OKLCH, and the gamut clamp a generated ramp depends on.

Brand ramps are generated perceptually rather than in sRGB because the two
things a ramp must do — hold a hue steady while lightness marches, and stay
legible at every step — are properties of a perceptual space and accidents of
sRGB. Interpolating `#1d4ed8` toward white in sRGB darkens the hue and loses
chroma unevenly; doing it in OKLCH does not.

## Why the clamp is not optional

OKLCH can name colours sRGB cannot show. Converting one without a clamp
produces channel values outside 0…255, which round-trip to a **different
colour** rather than an error.

Measured on the real input that prompted this module — `dotmac_academy_app`'s
21 hand-authored oklch colours — **2 fall outside sRGB** (`brand-600` and
`brand-800`), and both by a trivial amount: 0.0025 and 0.0005 of chroma. Its
primary, `brand-700`, converts exactly. An earlier pass reported three
including the primary; that came from a conversion that ran negative linear
values through `abs(u) ** (1/2.4)`, which fabricates in-range results from
out-of-range input and reports gamut errors that are not there. The check that
matters tests the LINEAR values before encoding, which is what `_in_gamut`
does.

So `oklch_to_hex` reduces chroma until the colour fits, holding lightness and
hue — the two things a reader notices — and reports that it did. A caller that
wants to know (the brand pipeline does) can see the reduction rather than
discover it by eye.

Conversion constants are Björn Ottosson's published OKLab matrices.
"""

from __future__ import annotations

import math
from typing import Final, NamedTuple

__all__ = ["OKLCH", "ClampedColor", "hex_to_oklch", "oklch_to_hex", "parse_hex"]


class OKLCH(NamedTuple):
    """Lightness 0…1, chroma 0…~0.4, hue in degrees."""

    lightness: float
    chroma: float
    hue: float


class ClampedColor(NamedTuple):
    """A converted colour, and how much chroma sRGB refused to show."""

    hex_value: str
    #: `0.0` when the requested colour fitted. Positive when chroma was reduced
    #: to bring it into gamut — the brand pipeline surfaces this rather than
    #: silently shifting an operator's colour.
    chroma_lost: float

    @property
    def was_clamped(self) -> bool:
        return self.chroma_lost > 1e-6


_LINEAR_TO_LMS: Final = (
    (0.4122214708, 0.5363325363, 0.0514459929),
    (0.2119034982, 0.6806995451, 0.1073969566),
    (0.0883024619, 0.2817188376, 0.6299787005),
)
_LMS_TO_OKLAB: Final = (
    (0.2104542553, 0.7936177850, -0.0040720468),
    (1.9779984951, -2.4285922050, 0.4505937099),
    (0.0259040371, 0.7827717662, -0.8086757660),
)
_OKLAB_TO_LMS: Final = (
    (1.0, 0.3963377774, 0.2158037573),
    (1.0, -0.1055613458, -0.0638541728),
    (1.0, -0.0894841775, -1.2914855480),
)
_LMS_TO_LINEAR: Final = (
    (4.0767416621, -3.3077115913, 0.2309699292),
    (-1.2684380046, 2.6097574011, -0.3413193965),
    (-0.0041960863, -0.7034186147, 1.7076147010),
)

#: Rounding to 8-bit costs about this much per channel, so a colour is "in
#: gamut" if it misses by less than half a step. Without the tolerance, a
#: colour landing exactly on 0.0 or 1.0 clamps for no visible reason.
_GAMUT_EPSILON: Final[float] = 0.5 / 255.0


def parse_hex(value: str) -> tuple[float, float, float]:
    """`#rgb` or `#rrggbb` -> three 0…1 channels. Raises on anything else.

    Deliberately strict: a malformed brand colour must fail where it is
    entered, not resolve to black three layers down.
    """
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError(f"expected #rgb or #rrggbb, got {value!r}")
    try:
        return tuple(int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError as exc:
        raise ValueError(f"{value!r} is not a hex colour") from exc


def _to_linear(channel: float) -> float:
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _from_linear(channel: float) -> float:
    if channel <= 0.0031308:
        return 12.92 * channel
    return 1.055 * (abs(channel) ** (1 / 2.4)) - 0.055


def _apply(
    matrix: tuple[tuple[float, float, float], ...], vec: tuple[float, float, float]
) -> tuple[float, float, float]:
    return tuple(sum(row[i] * vec[i] for i in range(3)) for row in matrix)  # type: ignore[return-value]


def hex_to_oklch(value: str) -> OKLCH:
    """`#3b82f6` -> `OKLCH(0.623, 0.188, 259.8)`."""
    linear = tuple(_to_linear(c) for c in parse_hex(value))
    lms = _apply(_LINEAR_TO_LMS, linear)  # type: ignore[arg-type]
    lab = _apply(_LMS_TO_OKLAB, tuple(math.copysign(abs(c) ** (1 / 3), c) for c in lms))  # type: ignore[arg-type]
    lightness, a, b = lab
    return OKLCH(lightness, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360.0)


def _oklch_to_linear(colour: OKLCH) -> tuple[float, float, float]:
    radians = math.radians(colour.hue)
    lab = (
        colour.lightness,
        colour.chroma * math.cos(radians),
        colour.chroma * math.sin(radians),
    )
    lms = _apply(_OKLAB_TO_LMS, lab)
    return _apply(_LMS_TO_LINEAR, tuple(c**3 for c in lms))  # type: ignore[arg-type]


def _in_gamut(linear: tuple[float, float, float]) -> bool:
    return all(-_GAMUT_EPSILON <= c <= 1.0 + _GAMUT_EPSILON for c in linear)


def oklch_to_hex(colour: OKLCH) -> ClampedColor:
    """Convert to sRGB, reducing chroma until it fits, and report the loss.

    Lightness and hue are held: a step that shifts hue to stay in gamut reads
    as a different colour, while one that loses a little chroma reads as the
    same colour slightly muted. Binary search rather than a linear walk so the
    result does not depend on a step size.
    """
    if colour.chroma <= 0.0:
        return ClampedColor(_pack(_oklch_to_linear(colour)), 0.0)

    if _in_gamut(_oklch_to_linear(colour)):
        return ClampedColor(_pack(_oklch_to_linear(colour)), 0.0)

    low, high = 0.0, colour.chroma
    for _ in range(24):  # ~1e-7 of chroma; far below a visible difference
        mid = (low + high) / 2
        if _in_gamut(_oklch_to_linear(colour._replace(chroma=mid))):
            low = mid
        else:
            high = mid
    return ClampedColor(
        _pack(_oklch_to_linear(colour._replace(chroma=low))), colour.chroma - low
    )


def _pack(linear: tuple[float, float, float]) -> str:
    channels = (min(255, max(0, round(_from_linear(c) * 255))) for c in linear)
    return "#" + "".join(f"{c:02x}" for c in channels)
