"""The semantic design-token vocabulary — the source of truth for the published
stylesheet.

**Named by ROLE, never by value.** `--dmui-action-destructive-hover` says what a
colour is FOR; `--teal` and `--gold` (dotmac_erp's 77 tokens) say what it looks
like, which is how a design system stops being able to change. The rule is
machine-checked (`test_no_token_is_named_by_value`), because it is exactly the
rule that erodes quietly.

**Where the vocabulary came from.** `dotmac_sub`'s `static/css/design-system.css`
is the most advanced token layer in the fleet (90 role-named properties) and the
inventories' own recommendation is to start from it rather than invent a scheme
(`docs/inventories/README.md`, reading 1). So the role words here are Sub's:
`surface-{primary,secondary,tertiary}`, `text-{primary,secondary,tertiary}`,
`border-{default,subtle}`, `semantic-{positive,info,warning,negative,neutral}`
with 50-950 ramp steps, and the `status-{surface,border,foreground,indicator}`
quartet. Five things are ADDED, each because inventory or a real cutover named
it as a gap, not because it seemed nice:

1. **Interaction/intent tokens** (`action-<intent>-{default,hover,pressed,
   disabled,on}`). Sub has none — "There are **no** interaction/intent tokens
   (`-hover`, `-pressed`, `-disabled`, `-on-*`)" — so every hover state in the
   fleet is a hardcoded utility class.
2. **Non-colour scales** (typography, spacing, radius, shadow, focus ring,
   breakpoints, motion). Sub has these only in `src/css/base/_tokens.css`, which
   the inventory records as git-tracked and *entirely unreferenced* — a dead
   third vocabulary. They are re-declared here as live, role-named tokens.
3. **`surface-background` / `surface-elevated`** — Sub has no page-background or
   raised-surface role, which is why its templates reach for `bg-slate-50`
   directly.
4. **`status-<intent>-*` promoted from class scope to root scope.** Sub sets its
   status quartet inside `.status-tone-*` class bodies; a token layer that ships
   almost no component classes (see `contract.PUBLISHED_COMPONENT_CLASSES`) must
   expose them as tokens, so the component layer consumes them rather than
   reinventing them — which is exactly what `.dmui-empty-state` now does.
5. **`surface-overlay`.** The reference assembly's first shell cutover needed a
   mobile-navigation scrim. No existing surface, action or status role means
   "separate a temporary layer from the page", so keeping `bg-black/50` or
   borrowing another role would preserve the same drift under a new spelling.

**Values carry no product identity.** ADR-0006 § 3 requires the generic
kernel-default brand layer to be *generic*: Sub's built-in defaults are the real
DotMac production identity, and adopting them would make every unbranded fork
look (and, for the non-colour fields, behave) like DotMac. The brand and accent
ramps below are deliberately unremarkable blue/cyan placeholders. They exist so
the system renders before any brand is resolved; U2's `BrandProfile` overrides
them at runtime by re-declaring the same custom properties.

**Two kinds of value.** A token's value is either a literal CSS value or a
single `var(--dmui-…)` reference to another token. References are how the
intent layer stays derived: `action-primary-default` IS `color-brand-600`, so a
runtime brand override of the ramp moves every button with it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from dotmac_ui.contract import TOKEN_PREFIX

# ── Token model ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DesignToken:
    """One published design token.

    `name` is the role name WITHOUT the `--dmui-` prefix (`css_variable()`
    builds the full custom-property name). `value` is the light/base value;
    `dark` is the dark-mode override or `None` when the token is
    mode-independent (ramps, spacing, typography, motion — a ramp step is the
    same colour in both modes; what changes is which step a role points at).
    """

    name: str
    category: str
    value: str
    description: str
    dark: str | None = None

    @property
    def variable(self) -> str:
        """The full CSS custom-property name, e.g. `--dmui-surface-primary`."""
        return f"{TOKEN_PREFIX}{self.name}"

    @property
    def is_mode_dependent(self) -> bool:
        return self.dark is not None

    def value_for(self, mode: str) -> str:
        """The declared value in `mode` ("light" or "dark")."""
        if mode not in _MODES:
            raise ValueError(f"unknown mode {mode!r}; expected one of {_MODES}")
        if mode == "dark" and self.dark is not None:
            return self.dark
        return self.value


_MODES: Final[tuple[str, ...]] = ("light", "dark")

#: Every colour-mode this package emits values for.
MODES: Final[tuple[str, ...]] = _MODES

#: Ramp steps, in emitted order. Positional, not descriptive: step 600 is "the
#: sixth stop", it is not a claim about the colour.
RAMP_STEPS: Final[tuple[str, ...]] = (
    "50",
    "100",
    "200",
    "300",
    "400",
    "500",
    "600",
    "700",
    "800",
    "900",
    "950",
)

#: The semantic intents, in emitted order. Sub's names, kept verbatim:
#: `positive`/`negative` rather than `success`/`error` because a role name
#: should survive a product deciding that "error" is really "blocked".
SEMANTIC_INTENTS: Final[tuple[str, ...]] = (
    "positive",
    "info",
    "warning",
    "negative",
    "neutral",
)

#: The action intents, in emitted order.
ACTION_INTENTS: Final[tuple[str, ...]] = (
    "primary",
    "accent",
    "destructive",
    "neutral",
)

#: The interaction states every action intent declares.
ACTION_STATES: Final[tuple[str, ...]] = (
    "default",
    "hover",
    "pressed",
    "disabled",
    "on",
)

#: Category order — also the order sections appear in the built stylesheet.
CATEGORIES: Final[tuple[str, ...]] = (
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
    "component",
)


# ── Colour ramps (literal values; the only place a hex appears) ─────────────
#
# Generic placeholders, NOT a brand. See this module's docstring.

_BRAND_RAMP: Final[Mapping[str, str]] = {
    "50": "#eff6ff",
    "100": "#dbeafe",
    "200": "#bfdbfe",
    "300": "#93c5fd",
    "400": "#60a5fa",
    "500": "#3b82f6",
    "600": "#2563eb",
    "700": "#1d4ed8",
    "800": "#1e40af",
    "900": "#1e3a8a",
    "950": "#172554",
}

_ACCENT_RAMP: Final[Mapping[str, str]] = {
    "50": "#ecfeff",
    "100": "#cffafe",
    "200": "#a5f3fc",
    "300": "#67e8f9",
    "400": "#22d3ee",
    "500": "#06b6d4",
    "600": "#0891b2",
    "700": "#0e7490",
    "800": "#155e75",
    "900": "#164e63",
    "950": "#083344",
}

_SEMANTIC_RAMPS: Final[Mapping[str, Mapping[str, str]]] = {
    "positive": {
        "50": "#f0fdf4",
        "100": "#dcfce7",
        "200": "#bbf7d0",
        "300": "#86efac",
        "400": "#4ade80",
        "500": "#22c55e",
        "600": "#16a34a",
        "700": "#15803d",
        "800": "#166534",
        "900": "#14532d",
        "950": "#052e16",
    },
    "info": {
        "50": "#f0f9ff",
        "100": "#e0f2fe",
        "200": "#bae6fd",
        "300": "#7dd3fc",
        "400": "#38bdf8",
        "500": "#0ea5e9",
        "600": "#0284c7",
        "700": "#0369a1",
        "800": "#075985",
        "900": "#0c4a6e",
        "950": "#082f49",
    },
    "warning": {
        "50": "#fffbeb",
        "100": "#fef3c7",
        "200": "#fde68a",
        "300": "#fcd34d",
        "400": "#fbbf24",
        "500": "#f59e0b",
        "600": "#d97706",
        "700": "#b45309",
        "800": "#92400e",
        "900": "#78350f",
        "950": "#451a03",
    },
    "negative": {
        "50": "#fef2f2",
        "100": "#fee2e2",
        "200": "#fecaca",
        "300": "#fca5a5",
        "400": "#f87171",
        "500": "#ef4444",
        "600": "#dc2626",
        "700": "#b91c1c",
        "800": "#991b1b",
        "900": "#7f1d1d",
        "950": "#450a0a",
    },
    "neutral": {
        "50": "#f8fafc",
        "100": "#f1f5f9",
        "200": "#e2e8f0",
        "300": "#cbd5e1",
        "400": "#94a3b8",
        "500": "#64748b",
        "600": "#475569",
        "700": "#334155",
        "800": "#1e293b",
        "900": "#0f172a",
        "950": "#020617",
    },
}

#: Which ramp each action intent derives from. `destructive` is the ACTION word
#: for the `negative` SEMANTIC role — an action that destroys, versus a state
#: that is bad. Two roles, one ramp, and the reference keeps them in step.
_ACTION_RAMP: Final[Mapping[str, str]] = {
    "primary": "color-brand",
    "accent": "color-accent",
    "destructive": "color-semantic-negative",
    "neutral": "color-semantic-neutral",
}

#: Ramp steps each action state points at, per mode.
#:
#: The steps are NOT uniform across intents, and that is deliberate: they are
#: chosen so that `action-<intent>-on` clears 4.5:1 against both `-default` and
#: `-hover` (`dotmac_ui.a11y.CONTRAST_REQUIREMENTS` enforces it). The cyan
#: accent ramp is lighter than the blue brand ramp, so its light-mode fill has
#: to sit one step deeper to carry white text. Symmetry would have been prettier
#: and would have failed the contract.
_ACTION_STEPS: Final[Mapping[str, Mapping[str, Mapping[str, str]]]] = {
    "primary": {
        "light": {
            "default": "600",
            "hover": "700",
            "pressed": "800",
            "disabled": "200",
        },
        "dark": {
            "default": "400",
            "hover": "300",
            "pressed": "200",
            "disabled": "800",
        },
    },
    "accent": {
        "light": {
            "default": "700",
            "hover": "800",
            "pressed": "900",
            "disabled": "200",
        },
        "dark": {
            "default": "400",
            "hover": "300",
            "pressed": "200",
            "disabled": "800",
        },
    },
    "destructive": {
        "light": {
            "default": "700",
            "hover": "800",
            "pressed": "900",
            "disabled": "200",
        },
        "dark": {
            "default": "400",
            "hover": "300",
            "pressed": "200",
            "disabled": "800",
        },
    },
    "neutral": {
        "light": {
            "default": "700",
            "hover": "800",
            "pressed": "900",
            "disabled": "200",
        },
        "dark": {
            "default": "300",
            "hover": "200",
            "pressed": "100",
            "disabled": "800",
        },
    },
}

#: The foreground a filled action carries, per intent and mode.
_ACTION_ON: Final[Mapping[str, Mapping[str, str]]] = {
    "primary": {"light": "#ffffff", "dark": "var(--dmui-color-brand-950)"},
    "accent": {"light": "#ffffff", "dark": "var(--dmui-color-accent-950)"},
    "destructive": {
        "light": "#ffffff",
        "dark": "var(--dmui-color-semantic-negative-950)",
    },
    "neutral": {
        "light": "#ffffff",
        "dark": "var(--dmui-color-semantic-neutral-950)",
    },
}

#: Which ramp step each `status-<intent>-<role>` points at, per mode. Sub's
#: quartet, lifted from class scope to token scope.
_STATUS_STEPS: Final[Mapping[str, Mapping[str, str]]] = {
    "light": {
        "surface": "50",
        "border": "200",
        "foreground": "700",
        "indicator": "600",
    },
    "dark": {
        "surface": "900",
        "border": "800",
        "foreground": "200",
        "indicator": "400",
    },
}

_STATUS_ROLE_DESCRIPTIONS: Final[Mapping[str, str]] = {
    "surface": "tinted background of a status pill/panel",
    "border": "border of a status pill/panel",
    "foreground": "text on a status surface",
    "indicator": "the status dot/bar — non-text, 3:1 against its surface",
}


def _build_tokens() -> tuple[DesignToken, ...]:
    tokens: list[DesignToken] = []

    def add(
        name: str,
        category: str,
        value: str,
        description: str,
        dark: str | None = None,
    ) -> None:
        tokens.append(DesignToken(name, category, value, description, dark))

    # ── color: the raw ramps ────────────────────────────────────────────────
    for step, hex_value in _BRAND_RAMP.items():
        add(
            f"color-brand-{step}",
            "color",
            hex_value,
            f"Brand ramp step {step}. Placeholder identity — overridden at "
            "runtime by the resolved BrandProfile.",
        )
    for step, hex_value in _ACCENT_RAMP.items():
        add(
            f"color-accent-{step}",
            "color",
            hex_value,
            f"Accent ramp step {step}. Placeholder identity — overridden at "
            "runtime by the resolved BrandProfile.",
        )
    for intent in SEMANTIC_INTENTS:
        for step, hex_value in _SEMANTIC_RAMPS[intent].items():
            add(
                f"color-semantic-{intent}-{step}",
                "color",
                hex_value,
                f"Semantic {intent} ramp step {step}.",
            )

    # ── surface ─────────────────────────────────────────────────────────────
    add(
        "surface-background",
        "surface",
        "var(--dmui-color-semantic-neutral-50)",
        "The page canvas behind every surface.",
        dark="var(--dmui-color-semantic-neutral-950)",
    )
    add(
        "surface-primary",
        "surface",
        "#ffffff",
        "The default content surface (cards, panels, table bodies).",
        dark="var(--dmui-color-semantic-neutral-900)",
    )
    add(
        "surface-secondary",
        "surface",
        "var(--dmui-color-semantic-neutral-100)",
        "A recessed surface (table headers, sidebars, inset wells).",
        dark="var(--dmui-color-semantic-neutral-800)",
    )
    add(
        "surface-tertiary",
        "surface",
        "var(--dmui-color-semantic-neutral-200)",
        "A further-recessed surface (rails, dividerless groupings).",
        dark="var(--dmui-color-semantic-neutral-700)",
    )
    add(
        "surface-elevated",
        "surface",
        "#ffffff",
        "A surface raised above the page (menus, popovers, dialogs). Pairs "
        "with a shadow token; in dark mode it lightens rather than shadows, "
        "because a shadow is invisible on a dark canvas.",
        dark="var(--dmui-color-semantic-neutral-800)",
    )
    add(
        "surface-overlay",
        "surface",
        "var(--dmui-color-semantic-neutral-950)",
        "The opaque colour behind a translucent scrim that separates a "
        "modal or mobile navigation layer from page content. Apply opacity "
        "at the scrim element; it is not a content surface.",
    )

    # ── text ────────────────────────────────────────────────────────────────
    add(
        "text-primary",
        "text",
        "var(--dmui-color-semantic-neutral-900)",
        "Body and heading text. 4.5:1 on every surface token.",
        dark="var(--dmui-color-semantic-neutral-50)",
    )
    add(
        "text-secondary",
        "text",
        "var(--dmui-color-semantic-neutral-700)",
        "Supporting text (labels, descriptions). Still real text: 4.5:1.",
        dark="var(--dmui-color-semantic-neutral-200)",
    )
    add(
        "text-tertiary",
        "text",
        "var(--dmui-color-semantic-neutral-600)",
        "De-emphasised text (metadata, captions). Still 4.5:1.",
        dark="var(--dmui-color-semantic-neutral-300)",
    )
    add(
        "text-muted",
        "text",
        "var(--dmui-color-semantic-neutral-500)",
        "The faintest text role. It is text, so it is still held to 4.5:1 — "
        "this is the floor, not a licence to go lighter.",
        dark="var(--dmui-color-semantic-neutral-400)",
    )
    add(
        "text-inverted",
        "text",
        "#ffffff",
        "Text on an inverted/filled surface. Use `action-<intent>-on` for "
        "text on a filled action; this is for inverted panels.",
        dark="var(--dmui-color-semantic-neutral-900)",
    )

    # ── border ──────────────────────────────────────────────────────────────
    add(
        "border-subtle",
        "border",
        "var(--dmui-color-semantic-neutral-100)",
        "A hairline that separates without dividing. Decorative — not held to "
        "a contrast minimum.",
        dark="var(--dmui-color-semantic-neutral-800)",
    )
    add(
        "border-default",
        "border",
        "var(--dmui-color-semantic-neutral-200)",
        "The default border for cards, inputs, and tables.",
        dark="var(--dmui-color-semantic-neutral-700)",
    )
    add(
        "border-strong",
        "border",
        "var(--dmui-color-semantic-neutral-500)",
        "A border that carries meaning (a control's boundary). Non-text UI "
        "component: 3:1 against its surface.",
        dark="var(--dmui-color-semantic-neutral-500)",
    )
    add(
        "border-focus",
        "border",
        "var(--dmui-focus-ring-color)",
        "The border a control adopts while focused; tracks the focus ring.",
    )

    # -- action (intent x state) ─────────────────────────────────────────────
    for intent in ACTION_INTENTS:
        ramp = _ACTION_RAMP[intent]
        for state in ACTION_STATES:
            if state == "on":
                add(
                    f"action-{intent}-on",
                    "action",
                    _ACTION_ON[intent]["light"],
                    f"Foreground on a filled {intent} action. 4.5:1 against "
                    f"both `action-{intent}-default` and `-hover`.",
                    dark=_ACTION_ON[intent]["dark"],
                )
                continue
            light_step = _ACTION_STEPS[intent]["light"][state]
            dark_step = _ACTION_STEPS[intent]["dark"][state]
            add(
                f"action-{intent}-{state}",
                "action",
                f"var(--dmui-{ramp}-{light_step})",
                f"The {intent} action's {state} fill.",
                dark=f"var(--dmui-{ramp}-{dark_step})",
            )

    # -- status (intent x role) ──────────────────────────────────────────────
    for intent in SEMANTIC_INTENTS:
        for role, description in _STATUS_ROLE_DESCRIPTIONS.items():
            add(
                f"status-{intent}-{role}",
                "status",
                f"var(--dmui-color-semantic-{intent}-"
                f"{_STATUS_STEPS['light'][role]})",
                f"{intent.capitalize()} status: {description}.",
                dark=f"var(--dmui-color-semantic-{intent}-"
                f"{_STATUS_STEPS['dark'][role]})",
            )

    # ── typography ──────────────────────────────────────────────────────────
    #
    # Font FILES are deliberately not shipped. A consumer supplies its own
    # self-hosted @font-face and re-declares the family token; the default
    # stacks are system fonts so the package never requests a remote font
    # (no-CDN standard, and ADR-0006 D7's deny-by-default CSP).
    add(
        "font-display",
        "typography",
        "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif",
        "Headings and display text. Re-declare to install a brand face.",
    )
    add(
        "font-body",
        "typography",
        "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif",
        "Body copy and UI chrome.",
    )
    add(
        "font-mono",
        "typography",
        "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
        "Identifiers, code, and fixed-width data.",
    )
    for size_name, size_value in (
        ("xs", "0.75rem"),
        ("sm", "0.875rem"),
        ("base", "1rem"),
        ("lg", "1.125rem"),
        ("xl", "1.25rem"),
        ("2xl", "1.5rem"),
        ("3xl", "1.875rem"),
        ("4xl", "2.25rem"),
    ):
        add(
            f"font-size-{size_name}",
            "typography",
            size_value,
            f"Type scale step `{size_name}`. Relative (rem) so a user's "
            "browser text-size preference is honoured (WCAG 1.4.4).",
        )
    for weight_name, weight_value in (
        ("regular", "400"),
        ("medium", "500"),
        ("semibold", "600"),
        ("bold", "700"),
    ):
        add(
            f"font-weight-{weight_name}",
            "typography",
            weight_value,
            f"Weight `{weight_name}`.",
        )
    for lh_name, lh_value in (
        ("tight", "1.2"),
        ("snug", "1.35"),
        ("normal", "1.5"),
        ("relaxed", "1.65"),
    ):
        add(
            f"line-height-{lh_name}",
            "typography",
            lh_value,
            f"Line height `{lh_name}`.",
        )
    for ls_name, ls_value in (
        ("tight", "-0.02em"),
        ("normal", "0em"),
        ("wide", "0.02em"),
    ):
        add(
            f"letter-spacing-{ls_name}",
            "typography",
            ls_value,
            f"Letter spacing `{ls_name}`.",
        )

    # ── space ───────────────────────────────────────────────────────────────
    for space_name, space_value in (
        ("3xs", "0.125rem"),
        ("2xs", "0.25rem"),
        ("xs", "0.5rem"),
        ("sm", "0.75rem"),
        ("md", "1rem"),
        ("lg", "1.5rem"),
        ("xl", "2rem"),
        ("2xl", "3rem"),
        ("3xl", "4rem"),
    ):
        add(
            f"space-{space_name}",
            "space",
            space_value,
            f"Spacing step `{space_name}`.",
        )

    # ── radius ──────────────────────────────────────────────────────────────
    for radius_name, radius_value in (
        ("none", "0"),
        ("sm", "0.25rem"),
        ("md", "0.375rem"),
        ("lg", "0.5rem"),
        ("xl", "0.75rem"),
        ("2xl", "1rem"),
        ("full", "9999px"),
    ):
        add(
            f"radius-{radius_name}",
            "radius",
            radius_value,
            f"Corner radius `{radius_name}`.",
        )

    # ── shadow ──────────────────────────────────────────────────────────────
    for shadow_name, light_shadow, dark_shadow in (
        ("none", "none", None),
        (
            "sm",
            "0 1px 2px 0 rgb(15 23 42 / 0.06)",
            "0 1px 2px 0 rgb(0 0 0 / 0.40)",
        ),
        (
            "md",
            "0 2px 4px -1px rgb(15 23 42 / 0.08), 0 4px 8px -2px rgb(15 23 42 / 0.06)",
            "0 2px 4px -1px rgb(0 0 0 / 0.45), 0 4px 8px -2px rgb(0 0 0 / 0.35)",
        ),
        (
            "lg",
            "0 4px 8px -2px rgb(15 23 42 / 0.10), "
            "0 12px 24px -4px rgb(15 23 42 / 0.08)",
            "0 4px 8px -2px rgb(0 0 0 / 0.50), 0 12px 24px -4px rgb(0 0 0 / 0.40)",
        ),
        (
            "xl",
            "0 8px 16px -4px rgb(15 23 42 / 0.12), "
            "0 24px 48px -8px rgb(15 23 42 / 0.10)",
            "0 8px 16px -4px rgb(0 0 0 / 0.55), 0 24px 48px -8px rgb(0 0 0 / 0.45)",
        ),
    ):
        add(
            f"shadow-{shadow_name}",
            "shadow",
            light_shadow,
            f"Elevation `{shadow_name}`.",
            dark=dark_shadow,
        )

    # ── focus ───────────────────────────────────────────────────────────────
    add(
        "focus-ring-color",
        "focus",
        "var(--dmui-color-brand-600)",
        "The focus indicator's colour. 3:1 against every surface token "
        "(WCAG 2.2 SC 1.4.11 / 2.4.11).",
        dark="var(--dmui-color-brand-300)",
    )
    add(
        "focus-ring-width",
        "focus",
        "2px",
        "Focus indicator thickness. At least 2 CSS px so the indicator meets "
        "SC 2.4.11's minimum-area expectation on a 1px-bordered control.",
    )
    add(
        "focus-ring-offset",
        "focus",
        "2px",
        "Gap between the control and its focus ring.",
    )

    # ── breakpoint ──────────────────────────────────────────────────────────
    #
    # NOTE: a CSS custom property cannot be used inside a media query's
    # condition. These tokens are the DECLARED values a consumer mirrors in its
    # own media/container queries (and reads programmatically via
    # `dotmac_ui.tokens`), not something the browser resolves in `@media`.
    for bp_name, bp_value in (
        ("xs", "480px"),
        ("sm", "640px"),
        ("md", "768px"),
        ("lg", "1024px"),
        ("xl", "1280px"),
        ("2xl", "1536px"),
    ):
        add(
            f"breakpoint-{bp_name}",
            "breakpoint",
            bp_value,
            f"Layout breakpoint `{bp_name}` (declared value; media queries "
            "cannot read custom properties).",
        )

    # ── motion ──────────────────────────────────────────────────────────────
    #
    # Every duration collapses to 1ms under `prefers-reduced-motion: reduce`
    # (emitted by the build, verified by
    # `test_reduced_motion_block_neutralises_every_duration`). 1ms rather than
    # 0s so transitionend/animationend handlers still fire.
    for dur_name, dur_value in (
        ("instant", "0ms"),
        ("fast", "120ms"),
        ("normal", "200ms"),
        ("slow", "320ms"),
    ):
        add(
            f"duration-{dur_name}",
            "motion",
            dur_value,
            f"Motion duration `{dur_name}`; collapses to 1ms under "
            "prefers-reduced-motion.",
        )
    for ease_name, ease_value in (
        ("standard", "cubic-bezier(0.4, 0, 0.2, 1)"),
        ("entrance", "cubic-bezier(0, 0, 0.2, 1)"),
        ("exit", "cubic-bezier(0.4, 0, 1, 1)"),
    ):
        add(
            f"easing-{ease_name}",
            "motion",
            ease_value,
            f"Easing curve `{ease_name}`.",
        )

    # ── component roles ───────────────────────────────────────────────────
    # A component default becomes a token when consumers need a direct,
    # supported override seam. This is a portable fallback, not a product
    # viewport: a host can re-declare the variable in its own surface scope.
    add(
        "map-frame-min-block-size",
        "component",
        "24rem",
        "Minimum map-frame block size. Consumers may override it for the "
        "surface that composes the frame.",
    )
    add(
        "control-min-block-size",
        "component",
        "2.75rem",
        "Minimum interactive control block size (44 CSS px at the default "
        "root size), shared by packaged form and pagination controls.",
    )

    return tuple(tokens)


#: The published token vocabulary, in emitted order.
TOKENS: Final[tuple[DesignToken, ...]] = _build_tokens()

#: Name → token lookup.
TOKENS_BY_NAME: Final[Mapping[str, DesignToken]] = {t.name: t for t in TOKENS}

#: The durations that `prefers-reduced-motion: reduce` neutralises.
REDUCED_MOTION_DURATION: Final[str] = "1ms"


def token(name: str) -> DesignToken:
    """Look a token up by role name (no `--dmui-` prefix)."""
    try:
        return TOKENS_BY_NAME[name]
    except KeyError:
        raise KeyError(f"no published token named {name!r}") from None


def tokens_in(category: str) -> tuple[DesignToken, ...]:
    """Every token in `category`, in emitted order."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}; expected one of {CATEGORIES}")
    return tuple(t for t in TOKENS if t.category == category)


def css_variable(name: str) -> str:
    """The full custom-property name for a role name."""
    return token(name).variable


_VAR_REFERENCE = re.compile(r"^var\(\s*--dmui-([a-z0-9-]+)\s*\)$")
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def reference_target(value: str) -> str | None:
    """The role name a `var(--dmui-…)` value points at, or None if literal."""
    match = _VAR_REFERENCE.match(value.strip())
    return match.group(1) if match else None


def resolve_color(
    name: str,
    mode: str = "light",
    overrides: Mapping[str, str] | None = None,
) -> str:
    """Resolve a colour token to a literal `#rrggbb`, following `var()` chains.

    Resolution is mode-aware all the way down: resolving `action-primary-on` in
    dark mode follows its dark value into `color-brand-950`, and if that token
    itself had a dark override the override would win — exactly what the
    cascade does at runtime. A cycle or a non-colour terminal value raises,
    which is how a mistyped reference fails loudly instead of silently
    rendering black.

    `overrides` maps a token NAME to a literal colour and is consulted at every
    hop, which is what makes a brand override behave the way the cascade will:
    replacing `color-brand-600` moves `action-primary-default` with it, because
    the chain terminates on the override rather than the built-in value. Pass
    the palette a product actually re-declares at runtime and this reports what
    that product will really render.
    """
    seen: list[str] = []
    current = name
    while True:
        if current in seen:
            raise ValueError(f"token reference cycle: {' -> '.join([*seen, current])}")
        seen.append(current)
        if overrides and current in overrides:
            return overrides[current]
        value = token(current).value_for(mode)
        target = reference_target(value)
        if target is None:
            if not _HEX.match(value):
                raise ValueError(
                    f"token {name!r} resolves to {value!r} in {mode} mode, "
                    "which is not a hex colour"
                )
            return _expand_hex(value)
        current = target


def _expand_hex(value: str) -> str:
    digits = value[1:]
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return "#" + digits.lower()


def declarations(mode: str) -> tuple[tuple[str, str], ...]:
    """`(custom-property, value)` pairs to emit for `mode`.

    Light emits every token. Dark emits ONLY the mode-dependent ones — a dark
    block that re-stated the ramps would make a runtime brand override have to
    be written twice.
    """
    if mode == "light":
        return tuple((t.variable, t.value) for t in TOKENS)
    if mode != "dark":
        raise ValueError(f"unknown mode {mode!r}; expected one of {_MODES}")
    pairs: list[tuple[str, str]] = []
    for design_token in TOKENS:
        if design_token.dark is not None:
            pairs.append((design_token.variable, design_token.dark))
    return tuple(pairs)


def token_names() -> tuple[str, ...]:
    """Every published role name, in emitted order."""
    return tuple(t.name for t in TOKENS)


#: Categories whose tokens hold a colour, and therefore also publish a channel
#: form. Kept here rather than in `build` so the derived names are part of the
#: declared surface — see `channel_variable`.
COLOUR_CATEGORIES: Final[tuple[str, ...]] = (
    "color",
    "surface",
    "text",
    "border",
    "action",
    "status",
)

#: Appended to a colour token's name to form its channel variable.
CHANNEL_SUFFIX: Final[str] = "-rgb"


def colour_tokens() -> tuple[DesignToken, ...]:
    """Every token holding a colour, in emitted order."""
    return tuple(t for c in COLOUR_CATEGORIES for t in tokens_in(c))


def channel_variable(name: str) -> str:
    """The channel-form custom property for a colour token.

    `surface-primary` -> `--dmui-surface-primary-rgb`, holding `255 255 255`
    rather than `#ffffff`.

    This exists because Tailwind can only synthesise alpha from separate
    channels: `bg-surface-primary/50` compiles to `rgb(<channels> / 0.5)`, and a
    variable holding a complete colour renders opaque with no warning. It is a
    *published* name, not an implementation detail — a consumer writing plain
    CSS can and should use it for the same reason.
    """
    return f"{TOKEN_PREFIX}{name}{CHANNEL_SUFFIX}"


def variable_names() -> tuple[str, ...]:
    """Every published custom-property name, in emitted order.

    Includes the derived channel forms. They are emitted by the stylesheet and
    consumers may reference them, so omitting them here would make them
    undocumented public names — which is the one thing COMPATIBILITY.md must
    never be wrong about.
    """
    return tuple(t.variable for t in TOKENS) + tuple(
        channel_variable(t.name) for t in colour_tokens()
    )


def iter_categories() -> Iterable[tuple[str, Sequence[DesignToken]]]:
    """`(category, tokens)` in emitted order, skipping empty categories."""
    for category in CATEGORIES:
        members = tokens_in(category)
        if members:
            yield category, members


__all__ = [
    "ACTION_INTENTS",
    "ACTION_STATES",
    "CATEGORIES",
    "CHANNEL_SUFFIX",
    "COLOUR_CATEGORIES",
    "DesignToken",
    "MODES",
    "RAMP_STEPS",
    "REDUCED_MOTION_DURATION",
    "SEMANTIC_INTENTS",
    "TOKENS",
    "TOKENS_BY_NAME",
    "channel_variable",
    "colour_tokens",
    "css_variable",
    "declarations",
    "iter_categories",
    "reference_target",
    "resolve_color",
    "token",
    "token_names",
    "tokens_in",
    "variable_names",
]
