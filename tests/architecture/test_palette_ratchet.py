"""Starter-owned templates must migrate off the hardcoded palette, never onto it.

`dotmac-ui` publishes 190 role-named tokens, and `app/assembly.py` links the
compiled stylesheet on every page — but the admin portal's own templates are
still authored against literal Tailwind palette utilities (`bg-slate-700`,
`text-primary-600`) resolved from the copied green ramp in
`static/css/src/main.css`.  The platform templates are the counter-example: they
already author against `var(--dmui-*)` and carry zero palette debt.

This gate does NOT migrate anything.  It freezes the existing debt exactly, so
that the portal can be moved onto tokens one component slice at a time while it
stays impossible to add a new hardcoded colour on the way.

## Why the baseline is per-file and per-token, not a single number

A single total lets a deletion pay for an addition: delete a `<div>` carrying
four palette classes, add one new `bg-red-600`, and a count-only ratchet sees
-3 and reports an improvement.  The baseline therefore records an exact
`{file: {token: count}}` inventory, and the gate compares the whole structure.
The stored `total` is kept alongside it and cross-checked, so a hand-edited
baseline that lowers the headline without lowering any entry fails too.

## Two-directional (ADR-0018)

The gate fails when debt RISES **and** when it FALLS.  A slice that genuinely
retires palette usage must lower the baseline in the same change — which is what
makes the reduction reviewable as a diff rather than silently absorbed.
Regenerate with::

    make palette-baseline

## Scope

Every starter-owned template root is discovered, not one convenient directory:
`packages/*/src/*/templates`.  That is the kernel's tree (admin, auth,
components — including the shared `form_macros.html` and `table_macros.html` —
layouts, errors, platform) and every in-repo module's tree.  Discovery is
asserted non-empty and the known roots are pinned, so a new package's templates
cannot join the repo unmonitored.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Final

import pytest

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
BASELINE_PATH: Final[Path] = Path(__file__).parent / "palette_debt_baseline.json"

#: Where starter-owned Jinja templates live.  Glob, not a hardcoded list, so a
#: new in-repo package cannot ship templates outside the gate's view.
TEMPLATE_ROOT_GLOB: Final[str] = "packages/*/src/*/templates"

#: Roots that are already token-native and must never acquire palette debt.
#: Anchored on real evidence: both regions measure zero today.
TOKEN_NATIVE_PREFIXES: Final[tuple[str, ...]] = (
    "packages/dotmac-kernel/src/dotmac_kernel/templates/platform/",
    "packages/dotmac-kernel/src/dotmac_kernel/templates/layouts/platform.html",
    "packages/dotmac-template-studio/src/dotmac_template_studio/templates/",
    # `dotmac-ui`'s published components. Zero is not merely their current
    # state, it is their CONTRACT: a consumer does not compile this package's
    # templates, so a utility class would render unstyled from site-packages.
    # `test_component_markup_uses_only_published_classes` says the same thing
    # from the component's side; this says it from the palette's, so neither
    # rule can be satisfied by weakening the other.
    "packages/dotmac-ui/src/dotmac_ui/templates/",
)

#: Tailwind's default palette names plus this repo's two theme ramps.  A
#: utility only counts as colour-bearing when its value resolves to one of
#: these, which is what keeps `text-sm`, `border-b` and `shadow-lg` out.
PALETTE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "slate",
        "gray",
        "zinc",
        "neutral",
        "stone",
        "red",
        "orange",
        "amber",
        "yellow",
        "lime",
        "green",
        "emerald",
        "teal",
        "cyan",
        "sky",
        "blue",
        "indigo",
        "violet",
        "purple",
        "fuchsia",
        "pink",
        "rose",
        # This repo's own ramps, declared in static/css/src/main.css.
        "primary",
        "accent",
        # Bare literals: not token-backed either.
        "white",
        "black",
    }
)

#: Utility prefixes whose value position carries a colour.  `shadow` is here for
#: `shadow-primary-500/25`; `shadow-lg` is excluded by the palette check above.
COLOUR_PREFIXES: Final[tuple[str, ...]] = (
    "ring-offset",
    "bg",
    "text",
    "border",
    "ring",
    "divide",
    "from",
    "via",
    "to",
    "outline",
    "decoration",
    "accent",
    "caret",
    "fill",
    "stroke",
    "placeholder",
    "shadow",
)

#: Colour keywords that are NOT palette values and stay permitted.
NEUTRAL_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"current", "transparent", "inherit", "auto", "none"}
)

#: A class-like token, including variant chain (`dark:hover:`), an optional
#: arbitrary-value bracket, and an optional `/50` opacity suffix.
_TOKEN = re.compile(
    r"[A-Za-z][A-Za-z0-9_\-]*(?::[A-Za-z0-9_\-]+)*" r"(?:\[[^\]\s\"']*\])?(?:/\d+)?"
)

#: Raw colour literals anywhere in a template (inline `style=`, a `<style>`
#: block, a Jinja default).  There are zero today; this keeps it that way.
_HEX = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")
_FUNCTIONAL = re.compile(r"\b(?:rgba?|hsla?)\s*\(")


def classify_token(raw: str) -> str | None:
    """Return the normalised debt key for one token, or None if it is clean.

    Permitted, and therefore returning None: any `var(--dmui-*)` reference,
    non-colour utilities (`text-sm`), and the neutral keywords.
    """
    base = raw.split(":")[-1].split("/")[0].lstrip("!")
    for prefix in sorted(COLOUR_PREFIXES, key=len, reverse=True):
        if not base.startswith(prefix + "-"):
            continue
        value = base[len(prefix) + 1 :]
        if value.startswith("["):
            # Arbitrary value.  A dmui custom property is the sanctioned escape
            # hatch; a raw literal is not.
            if "--dmui-" in value:
                return None
            inner = value[1:]
            if inner.startswith("#") or inner[:3] in {"rgb", "hsl"}:
                return f"{prefix}-[literal]"
            return None
        head = value.split("-")[0]
        if head in NEUTRAL_KEYWORDS:
            return None
        if head in PALETTE_NAMES:
            return base
        return None
    return None


def scan_text(text: str) -> Counter[str]:
    """Count hardcoded-palette debt in one template's full source.

    Scans the WHOLE document, not just `class="..."` attributes: this repo holds
    palette strings in Jinja `{% set %}` maps too (`table_macros.html`'s
    `status_badge` colour map is 36 occurrences on its own), and an
    attribute-only detector would let a migration hide colours by moving them
    into a variable.
    """
    found: Counter[str] = Counter()
    for raw in _TOKEN.findall(text):
        key = classify_token(raw)
        if key is not None:
            found[key] += 1
    # `var(--dmui-...)` never contains a hex literal, so these are unguarded
    # raw colours wherever they appear.
    for _ in _HEX.finditer(text):
        found["literal:hex"] += 1
    for _ in _FUNCTIONAL.finditer(text):
        found["literal:functional"] += 1
    return found


def template_roots() -> list[Path]:
    return sorted(PROJECT_ROOT.glob(TEMPLATE_ROOT_GLOB))


def scan_repository() -> dict[str, dict[str, int]]:
    """The live per-file, per-token debt inventory, keyed by repo-relative path."""
    inventory: dict[str, dict[str, int]] = defaultdict(dict)
    for root in template_roots():
        for template in sorted(root.rglob("*.html")):
            found = scan_text(template.read_text(encoding="utf-8"))
            if found:
                relative = template.relative_to(PROJECT_ROOT).as_posix()
                inventory[relative] = dict(sorted(found.items()))
    return dict(sorted(inventory.items()))


def total_of(inventory: dict[str, dict[str, int]]) -> int:
    return sum(count for tokens in inventory.values() for count in tokens.values())


def load_baseline() -> dict[str, object]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _describe_drift(
    live: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]]
) -> list[str]:
    problems: list[str] = []
    for path in sorted(set(live) | set(baseline)):
        live_tokens = live.get(path, {})
        base_tokens = baseline.get(path, {})
        for token in sorted(set(live_tokens) | set(base_tokens)):
            now = live_tokens.get(token, 0)
            was = base_tokens.get(token, 0)
            if now != was:
                direction = "ADDED" if now > was else "REMOVED"
                problems.append(f"{direction} {path}: {token} {was} -> {now}")
    return problems


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_template_roots_are_discovered_and_pinned() -> None:
    """A vacuous scan is indistinguishable from a clean one — prove neither."""
    roots = template_roots()
    assert roots, f"no template roots matched {TEMPLATE_ROOT_GLOB}"
    discovered = {root.relative_to(PROJECT_ROOT).as_posix() for root in roots}
    assert discovered == {
        "packages/dotmac-kernel/src/dotmac_kernel/templates",
        "packages/dotmac-template-studio/src/dotmac_template_studio/templates",
        # The design system's own component templates. Added deliberately when
        # `empty_state` shipped -- this assertion firing on that change is the
        # gate working, not an obstacle to route around.
        "packages/dotmac-ui/src/dotmac_ui/templates",
    }, (
        "template roots changed; a new package's templates must be brought under "
        f"the palette ratchet deliberately, not silently: {sorted(discovered)}"
    )
    scanned = sum(len(list(root.rglob("*.html"))) for root in roots)
    assert scanned >= 40, f"only {scanned} templates scanned; discovery looks broken"


def test_palette_debt_matches_the_baseline_exactly() -> None:
    """Two-directional: fails when debt rises AND when it falls."""
    baseline = load_baseline()
    recorded = baseline["files"]
    assert isinstance(recorded, dict)
    live = scan_repository()

    problems = _describe_drift(live, recorded)  # type: ignore[arg-type]
    assert not problems, (
        "hardcoded-palette debt drifted from the baseline.\n"
        + "\n".join(f"  {line}" for line in problems)
        + "\n\nAdding palette utilities is not allowed. If you legitimately "
        "RETIRED some, lower the baseline in this same change:\n"
        "  make palette-baseline"
    )


def test_the_baseline_total_agrees_with_its_own_entries() -> None:
    """A headline number that is not derived from the entries can be edited alone."""
    baseline = load_baseline()
    recorded: dict[str, dict[str, int]] = baseline["files"]  # type: ignore[assignment]
    assert baseline["total"] == total_of(recorded), (
        "baseline 'total' does not equal the sum of its per-file entries; "
        "regenerate it with `make palette-baseline` rather than editing by hand"
    )


def test_token_native_regions_carry_no_palette_debt() -> None:
    """The platform portal and Template Studio are already on `var(--dmui-*)`.

    Checked against the LIVE scan, not the baseline, so a regenerated baseline
    cannot legalise a regression here.
    """
    live = scan_repository()
    regressions = {
        path: tokens
        for path, tokens in live.items()
        if path.startswith(TOKEN_NATIVE_PREFIXES)
    }
    assert not regressions, (
        "these templates are token-native and must stay that way — author "
        f"against var(--dmui-*): {json.dumps(regressions, indent=2)}"
    )


# ---------------------------------------------------------------------------
# Sensitivity proofs (ADR-0018: a detector with no proof it fires is blind)
# ---------------------------------------------------------------------------


def test_sensitivity_a_new_palette_utility_is_detected() -> None:
    found = scan_text('<button class="rounded-lg bg-primary-600 px-4">Save</button>')
    assert found["bg-primary-600"] == 1


def test_sensitivity_palette_hidden_in_a_jinja_set_is_detected() -> None:
    """The attribute-only detector this replaced missed 36 real occurrences."""
    found = scan_text("{% set m = {'active': 'bg-green-100 dark:text-green-400'} %}")
    assert found["bg-green-100"] == 1
    assert found["text-green-400"] == 1


def test_sensitivity_removing_an_occurrence_requires_lowering_the_baseline() -> None:
    """A genuine reduction must be spent as a baseline diff, not pocketed."""
    baseline: dict[str, dict[str, int]] = load_baseline()["files"]  # type: ignore[assignment]
    path, tokens = next(iter(baseline.items()))
    token = next(iter(tokens))

    reduced = {p: dict(t) for p, t in baseline.items()}
    reduced[path][token] -= 1

    problems = _describe_drift(reduced, baseline)
    assert problems, "a removed occurrence must be visible to the ratchet"
    assert problems[0].startswith("REMOVED"), problems


def test_sensitivity_a_dmui_replacement_passes() -> None:
    clean = (
        '<div class="rounded-lg border p-4 text-sm shadow-lg"'
        '     style="color: var(--dmui-text-primary);'
        '            background: var(--dmui-surface-elevated);">'
        '  <span class="bg-[var(--dmui-status-warning-background)] text-current">'
        "ok</span>"
        "</div>"
    )
    assert scan_text(clean) == Counter()


def test_sensitivity_a_token_native_template_would_be_caught_if_it_regressed() -> None:
    """Proves the zero-region assertion above is load-bearing, not vacuous."""
    template = (
        PROJECT_ROOT
        / "packages/dotmac-kernel/src/dotmac_kernel/templates/platform/flags.html"
    )
    original = template.read_text(encoding="utf-8")
    assert scan_text(original) == Counter(), "fixture drifted: it must start clean"
    assert (
        scan_text(original + '<p class="text-slate-500">x</p>')["text-slate-500"] == 1
    )


def test_sensitivity_raw_colour_literals_are_detected() -> None:
    assert scan_text('<i style="color:#ff0000">')["literal:hex"] == 1
    assert scan_text('<i style="color:rgb(1,2,3)">')["literal:functional"] == 1


@pytest.mark.parametrize(
    "clean_token",
    [
        "text-sm",
        "border-b",
        "shadow-lg",
        "text-current",
        "border-transparent",
        "from-2xl",
        "to-do",
        "text-left",
        "ring-2",
    ],
)
def test_sensitivity_non_colour_utilities_are_not_flagged(clean_token: str) -> None:
    """False positives here would make the baseline noise and the gate ignored."""
    assert classify_token(clean_token) is None
