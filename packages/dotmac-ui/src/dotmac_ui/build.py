"""Generate the published assets from the token vocabulary.

INTERNAL — not part of the consumer contract (`dotmac_ui.INTERNAL_MODULES`). A
consumer references the *output* (`dotmac_ui.assets`); only this repository runs
the generator, via `make ui-build` or `python -m dotmac_ui.build`.

**Why a pure-Python generator and not Tailwind.** ADR-0006 D3 says the package
MAY build its own assets with Tailwind v4; it does not say it must. What U1
publishes is a token layer plus one base rule — there is nothing to compile,
scan, or purge, so introducing npm here would buy a dependency and a
non-hermetic build step in exchange for nothing. The decision that matters to
consumers is the *boundary*, not the tool behind it: when a later slice adds the
component library and does want Tailwind v4 to build it, that changes this file
and changes nothing a consumer does, which is precisely the property D3 is
protecting.

**The output is committed.** The generated stylesheet and manifest are tracked
in git, not gitignored build artifacts, because they ARE the published contract
— a reviewer must see a token change as a diff in the CSS, and an air-gapped
consumer must get working assets from a checkout with no build step.
`test_committed_stylesheet_matches_a_fresh_build` fails if the two drift, so the
committed copy cannot quietly become a fork of its own source.

Generation is deterministic: same tokens in, byte-identical file out.
"""

from __future__ import annotations

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path

from dotmac_ui.assets import (
    MANIFEST_RELPATH,
    STYLESHEET_RELPATH,
    TAILWIND_PRESET_RELPATH,
    static_dir,
)
from dotmac_ui.contract import (
    ACCESSIBILITY_TARGET,
    DARK_THEME_SELECTORS,
    TOKEN_PREFIX,
    UI_CONTRACT_VERSION,
)
from dotmac_ui.tokens import (
    REDUCED_MOTION_DURATION,
    TOKENS,
    declarations,
    iter_categories,
    resolve_color,
    tokens_in,
)

#: The categories the preset maps as colours, and therefore the ones that need a
#: channel form. Every other category (space, radius, shadow…) is used whole.
_COLOUR_CATEGORIES: tuple[str, ...] = (
    "color",
    "surface",
    "text",
    "border",
    "action",
    "status",
)


def _channel_variable(name: str) -> str:
    return f"{TOKEN_PREFIX}{name}-rgb"


def _channels(hex_colour: str) -> str:
    """`#3b82f6` -> `59 130 246`, the space-separated form `rgb()` wants.

    Tailwind synthesises `bg-brand-500/50` as `rgb(<channels> / 0.5)`, so it
    needs the components separately. A variable holding a complete colour cannot
    take an alpha modifier at all — the utility silently renders opaque.
    """
    value = hex_colour.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        raise ValueError(f"expected a 6-digit hex colour, got {hex_colour!r}")
    return " ".join(str(int(value[i : i + 2], 16)) for i in (0, 2, 4))


def _channel_declarations(mode: str) -> list[tuple[str, str]]:
    """`(--dmui-…-rgb, "R G B")` for every colour token, resolved for `mode`."""
    out: list[tuple[str, str]] = []
    for category in _COLOUR_CATEGORIES:
        for design_token in tokens_in(category):
            out.append(
                (
                    _channel_variable(design_token.name),
                    _channels(resolve_color(design_token.name, mode)),
                )
            )
    return out

_INDENT = "  "


def _banner(package_version: str) -> str:
    return "\n".join(
        (
            "/*!",
            " * dotmac-ui — the DotMac shared UI design system.",
            f" * UI contract version: {UI_CONTRACT_VERSION}",
            f" * Package version:     {package_version}",
            f" * Accessibility:       {ACCESSIBILITY_TARGET}",
            " *",
            " * GENERATED FILE — do not edit by hand. The source of truth is",
            " * dotmac_ui/tokens.py; rebuild with `make ui-build`.",
            " *",
            " * This file is self-contained: no @import, no remote origin, no",
            " * web font, no CDN. It needs no preprocessor and no particular",
            " * Tailwind major — see dotmac_ui/assets.py.",
            " */",
        )
    )


def _root_block() -> str:
    lines = [":root {"]
    first = True
    for category, members in iter_categories():
        if not first:
            lines.append("")
        first = False
        lines.append(f"{_INDENT}/* {category} */")
        for design_token in members:
            lines.append(f"{_INDENT}{design_token.variable}: {design_token.value};")
    lines.append("")
    lines.append(f"{_INDENT}/* channel forms, for alpha modifiers — see _channels */")
    for variable, value in _channel_declarations("light"):
        lines.append(f"{_INDENT}{variable}: {value};")
    lines.append("}")
    return "\n".join(lines)


def _dark_block() -> str:
    selector = ",\n".join(DARK_THEME_SELECTORS)
    lines = [
        "/* Dark mode. Only the mode-DEPENDENT roles are restated: the ramps are",
        " * identical in both modes, so a runtime brand override re-declares them",
        " * once and every role that points at them follows. `.dark` is honoured",
        " * alongside the package's own attribute so a host already using",
        " * Tailwind's class strategy needs no template change. */",
        f"{selector} {{",
    ]
    for variable, value in declarations("dark"):
        lines.append(f"{_INDENT}{variable}: {value};")
    lines.append("")
    lines.append(f"{_INDENT}/* channel forms restated: an alpha modifier must")
    lines.append(f"{_INDENT} * darken with its colour, not stay on the light one. */")
    for variable, value in _channel_declarations("dark"):
        lines.append(f"{_INDENT}{variable}: {value};")
    lines.append("}")
    return "\n".join(lines)


def _reduced_motion_block() -> str:
    lines = [
        "/* WCAG 2.2 SC 2.3.3 — a user who asks for reduced motion gets it from",
        f" * the token layer, not from each component. {REDUCED_MOTION_DURATION}"
        " rather than 0s so",
        " * transitionend/animationend handlers still fire and no interaction",
        " * that waits on one can hang. */",
        "@media (prefers-reduced-motion: reduce) {",
        f"{_INDENT}:root {{",
    ]
    for design_token in tokens_in("motion"):
        if design_token.name.startswith("duration-"):
            lines.append(
                f"{_INDENT * 2}{design_token.variable}: {REDUCED_MOTION_DURATION};"
            )
    lines.append(f"{_INDENT}}}")
    lines.append("}")
    return "\n".join(lines)


def _base_block() -> str:
    return "\n".join(
        (
            "/* The one base rule this contract ships. WCAG 2.2 SC 2.4.11 (focus",
            " * appearance) is a token-layer promise, so the indicator itself is",
            " * defined here rather than left to each consumer to re-derive.",
            " * Deliberately unlayered and at (0,1,0) specificity: an @layer would",
            " * lose to every unlayered rule in the host stylesheet, which would",
            " * silently give the promise away. */",
            ":focus-visible {",
            f"{_INDENT}outline: var({TOKEN_PREFIX}focus-ring-width) solid "
            f"var({TOKEN_PREFIX}focus-ring-color);",
            f"{_INDENT}outline-offset: var({TOKEN_PREFIX}focus-ring-offset);",
            "}",
        )
    )


def render_stylesheet(package_version: str) -> str:
    """The complete compiled stylesheet. Deterministic."""
    sections = (
        _banner(package_version),
        _root_block(),
        _dark_block(),
        _reduced_motion_block(),
        _base_block(),
    )
    return "\n\n".join(sections) + "\n"


#: Token category -> the Tailwind theme key it feeds, and the prefix to strip
#: from each token name. Two categories are renamed on the way through:
#: `text-*` would produce `text-text-primary` and `border-*` would produce
#: `border-border-subtle`, because Tailwind derives utility names from the
#: colour group. `content` and `stroke` keep the generated utilities readable.
_COLOR_GROUPS: tuple[tuple[str, str], ...] = (
    ("color", "color-"),
    ("surface", "surface-"),
    ("text", "text-"),
    ("border", "border-"),
    ("action", "action-"),
    ("status", "status-"),
)
_COLOR_GROUP_NAMES = {"text": "content", "border": "stroke", "color": ""}


def _nest(flat: dict[str, str]) -> dict[str, object]:
    """`{"brand-500": v}` -> `{"brand": {"500": v}}`, one level deep.

    Tailwind reads nested groups as `bg-brand-500`; a flat key with a dash works
    too, but nesting is what makes a palette legible in the generated file.
    """
    out: dict[str, object] = {}
    for key, value in flat.items():
        head, _, tail = key.partition("-")
        if tail:
            bucket = out.setdefault(head, {})
            if isinstance(bucket, dict):
                bucket[tail] = value
            continue
        out[key] = value
    return out


def _colors() -> dict[str, object]:
    colors: dict[str, object] = {}
    for category, prefix in _COLOR_GROUPS:
        flat = {
            design_token.name[len(prefix) :]: (
                f"rgb(var({_channel_variable(design_token.name)}) / <alpha-value>)"
            )
            for design_token in tokens_in(category)
        }
        group = _COLOR_GROUP_NAMES.get(category, category)
        if group:
            colors[group] = _nest(flat)
        else:
            colors.update(_nest(flat))
    return colors


def _scale(category: str, prefix: str) -> dict[str, str]:
    return {
        design_token.name[len(prefix) :]: f"var({design_token.variable})"
        for design_token in tokens_in(category)
    }


def _literal_scale(category: str, prefix: str) -> dict[str, str]:
    """Resolved values, for places a CSS variable cannot be used.

    Media queries are the case that matters: `@media (min-width: var(--x))` is
    not valid CSS — custom properties are not substituted in a media condition.
    A preset that emitted `var()` for `screens` would compile without complaint
    and silently break every responsive utility, which is the worst shape a bug
    can take here.

    The cost is that breakpoints are baked at build time and a brand override
    cannot move them. That is the correct trade: breakpoints are a layout
    contract, not a brand decision.
    """
    return {
        design_token.name[len(prefix) :]: design_token.value
        for design_token in tokens_in(category)
    }


def render_tailwind_preset(package_version: str) -> str:
    """A Tailwind preset pointing every utility at a token variable.

    Generated rather than written, for the same reason the stylesheet is: a
    hand-maintained preset is a second copy of the token names, and a second
    copy drifts. `dotmac_erp` and `dotmac_sub` have two copies of
    `base/_tokens.css` that are now 48% divergent.

    Utilities resolve to `var(--dmui-*)`, so a consumer's compiled CSS contains
    variable references, not values — one stylesheet swap re-themes it, and dark
    mode needs no `dark:` variant on any element.

    KNOWN LIMITATION: opacity modifiers (`bg-brand-500/50`) do not work on these
    colours. Tailwind needs channel components to synthesise alpha, and the
    tokens hold complete colours. Fixing it means publishing channel-form tokens
    from the token layer, which is a token change and not a preset change.
    """
    preset = {
        "darkMode": ["selector", DARK_THEME_SELECTORS[-1]],
        "theme": {
            "extend": {
                "colors": _colors(),
                "spacing": _scale("space", "space-"),
                "borderRadius": _scale("radius", "radius-"),
                "boxShadow": _scale("shadow", "shadow-"),
                "screens": _literal_scale("breakpoint", "breakpoint-"),
            }
        },
    }
    body = json.dumps(preset, indent=2, sort_keys=False)
    return (
        f"// Generated by dotmac-ui {package_version}. Do not edit.\n"
        f"// UI contract version {UI_CONTRACT_VERSION}.\n"
        "//\n"
        "// Usage, in a consumer's tailwind.config.js:\n"
        "//   const preset = require('<dotmac_ui>/static/"
        f"{TAILWIND_PRESET_RELPATH}');\n"
        "//   module.exports = { presets: [preset], content: [...] };\n"
        "//\n"
        "// Resolve <dotmac_ui> from the installed package rather than copying\n"
        "// this file: `python -c \"import dotmac_ui.assets as a;"
        " print(a.tailwind_preset_path())\"`.\n"
        f"module.exports = {body};\n"
    )


def render_manifest(package_version: str, stylesheet: str) -> str:
    """The published asset manifest. Deterministic; ends with a newline."""
    payload = {
        "name": "dotmac-ui",
        "version": package_version,
        "ui_contract_version": UI_CONTRACT_VERSION,
        "token_prefix": TOKEN_PREFIX,
        "token_count": len(TOKENS),
        "assets": [
            {
                "path": STYLESHEET_RELPATH,
                "bytes": len(stylesheet.encode("utf-8")),
                "sha256": sha256(stylesheet.encode("utf-8")).hexdigest(),
            }
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def rendered_assets(package_version: str) -> dict[str, str]:
    """`{relative path: contents}` for every published asset."""
    stylesheet = render_stylesheet(package_version)
    return {
        STYLESHEET_RELPATH: stylesheet,
        TAILWIND_PRESET_RELPATH: render_tailwind_preset(package_version),
        MANIFEST_RELPATH: render_manifest(package_version, stylesheet),
    }


def write_assets(package_version: str) -> list[Path]:
    """Write every published asset into the package's static directory."""
    written: list[Path] = []
    for relpath, contents in rendered_assets(package_version).items():
        target = static_dir() / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    from dotmac_ui import __version__

    parser = argparse.ArgumentParser(description="Build the dotmac-ui assets.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail (exit 1) if the committed assets differ from a fresh build "
        "instead of rewriting them.",
    )
    args = parser.parse_args(argv)

    expected = rendered_assets(__version__)
    if args.check:
        stale = [
            relpath
            for relpath, contents in expected.items()
            if not (static_dir() / relpath).is_file()
            or (static_dir() / relpath).read_text(encoding="utf-8") != contents
        ]
        if stale:
            print(
                "dotmac-ui assets are stale; run `make ui-build`:\n  "
                + "\n  ".join(stale),
                file=sys.stderr,
            )
            return 1
        print(f"dotmac-ui assets up to date ({len(expected)} files).")
        return 0

    for path in write_assets(__version__):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
