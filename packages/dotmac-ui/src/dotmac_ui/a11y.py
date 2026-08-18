"""The accessibility contract, and the part of it a machine can check today.

**The target.** `dotmac-ui` targets **WCAG 2.2 Level AA** for the critical
journeys of any product assembled from it: sign-in, navigation, reading and
filtering a record list, creating/editing a record, confirming a destructive
action, and recovering from an error. "Target" is a commitment about the design
system's own output — a consumer can still build an inaccessible page out of
accessible tokens, and this package makes no claim about that.

**What is machine-checked here, and why it is the right first thing.** Colour
contrast is the one AA criterion that is fully decidable from the token layer
alone: given two tokens and a mode, the ratio is arithmetic. Other AA criteria
(focus order, name/role/value, error identification, target size, dragging
alternatives) depend on markup and composition. This release publishes the
`empty_state` macro; its narrow markup guarantees live with its component
contract and tests, not in this token checker. `CONTRAST_REQUIREMENTS` below is
therefore not a sample: it is every colour PAIR the token vocabulary claims
will be used together, in both modes, and
`tests/unit/test_dotmac_ui_a11y.py::test_every_contrast_requirement_is_met`
fails the build on any that drops below its minimum.

The thresholds are the WCAG 2.2 ones, applied per pair:

- **4.5:1** — normal-size text against its background (SC 1.4.3). Every `text-*`
  role and every `status-*-foreground` is held to this, including the faintest
  one: `text-muted` being "muted" is a visual intent, not an exemption.
- **3:1** — non-text user-interface components and graphical objects (SC 1.4.11)
  and the focus indicator (SC 2.4.11, new in 2.2). Applies to `border-strong`,
  `focus-ring-color`, and every `status-*-indicator`.

**What is NOT claimed.** Large-text's relaxed 3:1 allowance is deliberately not
used anywhere: the type scale is a token, so the package cannot know which size
a role will render at, and assuming "large" would be assuming away the failure.
Contrast of a consumer's own colours and of text over imagery is out of scope.
Tenant-supplied `custom_css` is outside the contract entirely; ADR-0006 D8 and
kernel 0.1.0a47 retired that input rather than pretending it could be measured
or sanitized safely.

**Reduced motion** is the second machine-checked commitment: every duration
token collapses to 1ms under `prefers-reduced-motion: reduce` (SC 2.3.3), which
`dotmac_ui.build` emits and the stylesheet test verifies.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from dotmac_ui.contract import ACCESSIBILITY_TARGET
from dotmac_ui.tokens import MODES, SEMANTIC_INTENTS, resolve_color

#: WCAG 2.2 SC 1.4.3 — minimum contrast for normal-size text.
TEXT_CONTRAST_MINIMUM: Final[float] = 4.5

#: WCAG 2.2 SC 1.4.11 / 2.4.11 — minimum contrast for non-text UI components,
#: graphical objects, and the focus indicator.
NON_TEXT_CONTRAST_MINIMUM: Final[float] = 3.0


@dataclass(frozen=True)
class ContrastRequirement:
    """A pair of tokens the vocabulary promises will be legible together.

    `foreground`/`background` are role names (no `--dmui-` prefix). `mode` is
    "light" or "dark". `minimum` is the WCAG threshold that applies, and
    `rationale` records WHY this pair is a real pair — a requirement nobody can
    justify is a requirement that will be deleted the first time it fails.
    """

    foreground: str
    background: str
    mode: str
    minimum: float
    rationale: str

    @property
    def is_text(self) -> bool:
        return self.minimum >= TEXT_CONTRAST_MINIMUM

    def describe(self) -> str:
        return f"{self.foreground} on {self.background} ({self.mode})"


def _srgb_channel(value: int) -> float:
    channel = value / 255.0
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of an `#rrggbb` colour."""
    digits = hex_color.lstrip("#")
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    if len(digits) != 6:
        raise ValueError(f"not a hex colour: {hex_color!r}")
    red, green, blue = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
    return (
        0.2126 * _srgb_channel(red)
        + 0.7152 * _srgb_channel(green)
        + 0.0722 * _srgb_channel(blue)
    )


def contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast ratio between two `#rrggbb` colours (1.0 … 21.0)."""
    lum_a = relative_luminance(first)
    lum_b = relative_luminance(second)
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def token_contrast(
    foreground: str,
    background: str,
    mode: str = "light",
    overrides: Mapping[str, str] | None = None,
) -> float:
    """Contrast ratio between two TOKENS in `mode`, resolving `var()` chains.

    `overrides` is passed through to `resolve_color`, so a re-declared brand
    ramp is measured as the product will render it.
    """
    return contrast_ratio(
        resolve_color(foreground, mode, overrides),
        resolve_color(background, mode, overrides),
    )


def _build_requirements() -> tuple[ContrastRequirement, ...]:
    requirements: list[ContrastRequirement] = []

    def require(
        foreground: str, background: str, minimum: float, rationale: str
    ) -> None:
        for mode in MODES:
            requirements.append(
                ContrastRequirement(foreground, background, mode, minimum, rationale)
            )

    # Text roles on the surface they are read against. `surface-primary` is the
    # default content surface, so every text role must clear it; the other
    # surfaces are checked for `text-primary`, which is the role that renders on
    # all of them.
    for text_role in (
        "text-primary",
        "text-secondary",
        "text-tertiary",
        "text-muted",
    ):
        require(
            text_role,
            "surface-primary",
            TEXT_CONTRAST_MINIMUM,
            "body text on the default content surface (SC 1.4.3)",
        )
    for surface_role in (
        "surface-background",
        "surface-secondary",
        "surface-tertiary",
        "surface-elevated",
    ):
        require(
            "text-primary",
            surface_role,
            TEXT_CONTRAST_MINIMUM,
            f"primary text renders on {surface_role} too (SC 1.4.3)",
        )
    require(
        "text-secondary",
        "surface-secondary",
        TEXT_CONTRAST_MINIMUM,
        "labels in table headers and sidebars sit on the recessed surface",
    )
    for text_role in ("text-secondary", "text-tertiary"):
        require(
            text_role,
            "surface-elevated",
            TEXT_CONTRAST_MINIMUM,
            "catalog metadata and descriptions sit on elevated cards (SC 1.4.3)",
        )
    require(
        "text-inverted",
        "action-neutral-default",
        TEXT_CONTRAST_MINIMUM,
        "inverted text on the neutral filled surface (toasts, tooltips)",
    )

    # Filled actions: the label must survive both resting and hover fills. The
    # hover pair is the one that silently breaks, because it is only visible
    # under the pointer.
    for intent in ("primary", "accent", "destructive", "neutral"):
        require(
            f"action-{intent}-on",
            f"action-{intent}-default",
            TEXT_CONTRAST_MINIMUM,
            f"label on the resting {intent} button (SC 1.4.3)",
        )
        require(
            f"action-{intent}-on",
            f"action-{intent}-hover",
            TEXT_CONTRAST_MINIMUM,
            f"label on the hovered {intent} button (SC 1.4.3)",
        )
        require(
            f"action-{intent}-on",
            f"action-{intent}-pressed",
            TEXT_CONTRAST_MINIMUM,
            f"label on the pressed {intent} button (SC 1.4.3)",
        )

    # Status pills/panels: text on the tinted surface, and the indicator dot as
    # a graphical object.
    for intent in SEMANTIC_INTENTS:
        require(
            f"status-{intent}-foreground",
            f"status-{intent}-surface",
            TEXT_CONTRAST_MINIMUM,
            f"{intent} status text on its own tinted surface (SC 1.4.3)",
        )
        require(
            f"status-{intent}-indicator",
            f"status-{intent}-surface",
            NON_TEXT_CONTRAST_MINIMUM,
            f"{intent} status dot is a graphical object (SC 1.4.11)",
        )

    # Non-text UI: the meaningful border and the focus indicator.
    require(
        "border-strong",
        "surface-primary",
        NON_TEXT_CONTRAST_MINIMUM,
        "a control's boundary is a UI component (SC 1.4.11)",
    )
    require(
        "focus-ring-color",
        "surface-primary",
        NON_TEXT_CONTRAST_MINIMUM,
        "focus indicator against the content surface (SC 2.4.11)",
    )
    require(
        "focus-ring-color",
        "surface-background",
        NON_TEXT_CONTRAST_MINIMUM,
        "focus indicator against the page canvas (SC 2.4.11)",
    )

    return tuple(requirements)


#: Every colour pair the vocabulary claims will be used together, in both
#: modes. The suite in `tests/unit/test_dotmac_ui_a11y.py` walks all of them.
CONTRAST_REQUIREMENTS: Final[tuple[ContrastRequirement, ...]] = _build_requirements()


@dataclass(frozen=True)
class ContrastFailure:
    """A requirement that does not hold, with the measured ratio."""

    requirement: ContrastRequirement
    ratio: float

    def describe(self) -> str:
        return (
            f"{self.requirement.describe()}: {self.ratio:.2f}:1 "
            f"< {self.requirement.minimum}:1 — {self.requirement.rationale}"
        )


def check_contrast(
    requirements: tuple[ContrastRequirement, ...] = CONTRAST_REQUIREMENTS,
    overrides: Mapping[str, str] | None = None,
) -> tuple[ContrastFailure, ...]:
    """Every requirement that is not met, in declaration order.

    Public API on purpose: a consuming product that re-declares brand ramps at
    runtime (U2) runs this against its own resolved palette rather than
    re-deriving the pair list, so "the brand is legible" is one call, not a
    review habit. `overrides` is how that palette gets in — without it the
    promise in the previous sentence was not reachable, because resolution
    always read the built-in values.
    """
    failures: list[ContrastFailure] = []
    for requirement in requirements:
        ratio = token_contrast(
            requirement.foreground, requirement.background, requirement.mode, overrides
        )
        if ratio + 1e-9 < requirement.minimum:
            failures.append(ContrastFailure(requirement, ratio))
    return tuple(failures)


__all__ = [
    "ACCESSIBILITY_TARGET",
    "CONTRAST_REQUIREMENTS",
    "ContrastFailure",
    "ContrastRequirement",
    "NON_TEXT_CONTRAST_MINIMUM",
    "TEXT_CONTRAST_MINIMUM",
    "check_contrast",
    "contrast_ratio",
    "relative_luminance",
    "token_contrast",
]
