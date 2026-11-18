"""The `dotmac-ui` accessibility contract, machine-checked.

The design system targets **WCAG 2.2 Level AA** for critical journeys
(`dotmac_ui.a11y`'s module docstring states the journeys and the scope). Colour
contrast is the part of AA that is decidable from the token layer alone, so it
is the part enforced here — every pair the vocabulary claims will be used
together, in both colour modes, against the threshold that actually applies to
it (4.5:1 for text, 3:1 for non-text UI and the focus indicator).

Two guards, not one. The first walks the requirement list. The second checks the
requirement LIST itself, because a contrast suite has an obvious failure mode:
when a pair fails, deleting the requirement is easier than fixing the colour,
and a shrinking list still passes.
"""

from __future__ import annotations

import dotmac_ui
import pytest
from dotmac_ui import a11y


def test_every_contrast_requirement_is_met() -> None:
    failures = dotmac_ui.check_contrast()
    assert not failures, "WCAG 2.2 AA contrast failures:\n" + "\n".join(
        failure.describe() for failure in failures
    )


def test_contrast_arithmetic_matches_the_wcag_reference_values() -> None:
    """The checker is only worth having if the maths is right."""
    assert a11y.contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0, abs=0.01)
    assert a11y.contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0, abs=1e-9)
    # A known AA boundary pair: #767676 on white is the canonical 4.5:1 grey.
    assert a11y.contrast_ratio("#767676", "#ffffff") == pytest.approx(4.54, abs=0.02)
    assert a11y.contrast_ratio("#fff", "#000") == pytest.approx(21.0, abs=0.01)


def test_the_requirement_set_covers_both_modes_for_every_pair() -> None:
    """A pair checked in light mode only is a pair that breaks in dark mode."""
    by_pair: dict[tuple[str, str], set[str]] = {}
    for requirement in dotmac_ui.CONTRAST_REQUIREMENTS:
        key = (requirement.foreground, requirement.background)
        by_pair.setdefault(key, set()).add(requirement.mode)
    incomplete = {
        pair: sorted(modes)
        for pair, modes in by_pair.items()
        if set(modes) != set(dotmac_ui.MODES)
    }
    assert not incomplete, f"pairs not checked in every mode: {incomplete}"


def test_every_text_role_is_held_to_the_text_threshold() -> None:
    """`text-muted` being visually quiet is not an accessibility exemption —
    it is still text, and 4.5:1 is the floor, not a suggestion."""
    checked = {
        requirement.foreground
        for requirement in dotmac_ui.CONTRAST_REQUIREMENTS
        if requirement.minimum >= dotmac_ui.TEXT_CONTRAST_MINIMUM
    }
    for design_token in dotmac_ui.tokens_in("text"):
        assert (
            design_token.name in checked
        ), f"{design_token.name} is a text role with no contrast requirement"


def test_every_action_intent_is_checked_in_all_three_fill_states() -> None:
    """Resting, hover, and pressed. The hover pair is the one that silently
    breaks: it is only visible under a pointer, so no screenshot review catches
    it."""
    pairs = {
        (requirement.foreground, requirement.background)
        for requirement in dotmac_ui.CONTRAST_REQUIREMENTS
    }
    for intent in dotmac_ui.ACTION_INTENTS:
        for state in ("default", "hover", "pressed"):
            assert (f"action-{intent}-on", f"action-{intent}-{state}") in pairs


def test_every_semantic_intent_has_text_and_indicator_requirements() -> None:
    pairs = {
        (requirement.foreground, requirement.background, requirement.minimum)
        for requirement in dotmac_ui.CONTRAST_REQUIREMENTS
    }
    for intent in dotmac_ui.SEMANTIC_INTENTS:
        assert (
            f"status-{intent}-foreground",
            f"status-{intent}-surface",
            dotmac_ui.TEXT_CONTRAST_MINIMUM,
        ) in pairs
        assert (
            f"status-{intent}-indicator",
            f"status-{intent}-surface",
            dotmac_ui.NON_TEXT_CONTRAST_MINIMUM,
        ) in pairs


def test_the_focus_indicator_is_checked_against_every_surface_it_lands_on() -> None:
    """WCAG 2.2 SC 2.4.11 is new in 2.2 and is the one this package can most
    easily promise and most easily lose: the focus ring is a single token, so a
    brand override that dims it breaks keyboard navigation everywhere at once."""
    backgrounds = {
        requirement.background
        for requirement in dotmac_ui.CONTRAST_REQUIREMENTS
        if requirement.foreground == "focus-ring-color"
    }
    assert {"surface-primary", "surface-background"} <= backgrounds


def test_the_checker_actually_fails_when_a_pair_is_illegible() -> None:
    """Sensitivity self-test. A contrast suite that cannot fail is decoration."""
    impossible = a11y.ContrastRequirement(
        foreground="text-muted",
        background="surface-tertiary",
        mode="light",
        minimum=21.0,
        rationale="probe — nothing can reach 21:1 except pure black on white",
    )
    failures = dotmac_ui.check_contrast((impossible,))
    assert len(failures) == 1
    assert "21.0" in failures[0].describe()


def test_the_declared_thresholds_are_the_wcag_ones() -> None:
    assert dotmac_ui.TEXT_CONTRAST_MINIMUM == 4.5
    assert dotmac_ui.NON_TEXT_CONTRAST_MINIMUM == 3.0
    assert dotmac_ui.ACCESSIBILITY_TARGET == "WCAG 2.2 Level AA"


def test_reported_ratios_have_headroom_worth_reporting() -> None:
    """Not a pass/fail on the tokens — a visible record of how much margin each
    pair actually has, so the next value change is made with the tightest pairs
    in view rather than discovered by a failing build."""
    margins = sorted(
        (
            dotmac_ui.token_contrast(r.foreground, r.background, r.mode) - r.minimum,
            r.describe(),
        )
        for r in dotmac_ui.CONTRAST_REQUIREMENTS
    )
    tightest = margins[0]
    assert tightest[0] >= 0, f"no headroom on {tightest[1]}"
