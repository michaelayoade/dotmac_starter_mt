"""The allowlisted brand values, and what happens to everything else.

Michael's 2026-08-19 ruling fixed the three-way boundary this file implements:

- **`dotmac-ui`** owns the token vocabulary, the projection logic and contrast
  validation.
- **`dotmac-brand-profiles`** owns the scoped values, their provenance, the
  precedence between them and the locks over them.
- **The assembly** maps profile values into `dotmac_ui.BrandOverride`.

So this module holds CONSTRAINED RUNTIME brand/accent values — that is permitted
and intended — and it does not construct the `BrandOverride`, does not project
to CSS, and does not own a colour parser.

## Why the allowlist is a map with a checked invariant, not a literal

`BRAND_OVERRIDE_INPUTS` names our column for each `BrandOverride` field.
`test_the_allowlist_matches_dotmac_uis_own_fields` asserts its values are
EXACTLY `BrandOverride`'s fields, so if `dotmac-ui` ever publishes a third
accepted input the build fails here rather than this module silently
under-carrying it. A hand-written list with no such check answers "what did
somebody think of?"; the check answers "what does the vocabulary actually
accept?", and only the second one fails when `dotmac-ui` grows.

## Unsupported input is REPORTED, never dropped

`dotmac_ui.BrandWarning`'s own docstring states the rule this file obeys:
*"A warning, never a silent adjustment: D8 requires that unsupported or altered
branding input be reported to the caller, not quietly dropped."*

`translate_legacy_brand_values` is the migration seam for Sub's cutover, and it
returns both halves. A translation that returned only what it accepted would let
a cutover migrate five semantic tones into nothing and report success.

## Sub's `semantic_colors`, dispositioned

Sub stores an allowlisted five-tone quintet in `metadata_["semantic_colors"]`,
validated for known tone names, 6-digit hex, and WCAG AA in both themes. It is
not an open token map, and the audit that called it one was wrong about its
shape.

It is still **unsupported here**, and the reason is ownership rather than
safety: `dotmac_ui.SEMANTIC_INTENTS` publishes exactly those five names as
TOKENS with built-in ramps, and `render_brand_css` seeds only the brand and
accent ramps. Carrying a per-profile override for them would put a second
authority beside a published token — which is the thing ADR-0006 D8 and U1 exist
to prevent, and is a different objection from "raw CSS is dangerous".

The disposition is therefore `OWNED_BY_PUBLISHED_TOKEN`, it is returned to the
caller for every affected value, and the cutover has to look at it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Final

from dotmac_ui import SEMANTIC_INTENTS, BrandOverride

#: Our column name for each `BrandOverride` field. The invariant that its values
#: equal `BrandOverride`'s fields is asserted in the architecture tests — see the
#: module docstring for why that check, rather than the literal, is the part that
#: matters.
BRAND_OVERRIDE_INPUTS: Final[Mapping[str, str]] = {
    "primary_hex": "primary",
    "accent_hex": "accent",
}


def brand_override_fields() -> frozenset[str]:
    """`BrandOverride`'s accepted field names, read from the dataclass itself."""
    return frozenset(field.name for field in fields(BrandOverride))


class Disposition(StrEnum):
    """Why a legacy value did not become an allowlisted input.

    Two reasons, and they are genuinely different decisions:

    `OWNED_BY_PUBLISHED_TOKEN` — the concept exists in `dotmac-ui`, as a
    published token with its own ramp. The value is not carried because carrying
    it would create a second authority over something already published. A
    product that needs it changes the published token, not this module.

    `NOT_AN_ALLOWLISTED_INPUT` — the concept does not exist in the vocabulary at
    all. There is nothing to change; the value has no home.

    Collapsing them would tell an operator "unsupported" for both and leave them
    unable to tell which one has a path forward.
    """

    # `noqa: S105` — bandit's hardcoded-password heuristic fires on the word
    # TOKEN. This is a DESIGN token (`dotmac_ui.SEMANTIC_INTENTS`), not a
    # credential, and renaming it to dodge the heuristic would cost the one
    # word that makes the disposition self-explanatory.
    OWNED_BY_PUBLISHED_TOKEN = "owned_by_published_token"  # noqa: S105
    NOT_AN_ALLOWLISTED_INPUT = "not_an_allowlisted_input"


@dataclass(frozen=True, slots=True)
class UnsupportedBrandValue:
    """One legacy value that did not survive translation, and why.

    Carries the value itself, not just the key. A cutover reviewer deciding
    whether a tone mattered needs to see what it was — and a report that named
    only the keys would make "was anyone actually using this?" a second query
    against a database that is about to be migrated.
    """

    source_key: str
    value: str
    disposition: Disposition
    detail: str


@dataclass(frozen=True, slots=True)
class BrandValueTranslation:
    """Both halves of a translation. Neither is optional.

    A translation that returned only `accepted` would let a cutover migrate five
    semantic tones into nothing and report success — which is exactly the silent
    drop D8 forbids.
    """

    accepted: Mapping[str, str]
    unsupported: tuple[UnsupportedBrandValue, ...]

    @property
    def is_lossless(self) -> bool:
        return not self.unsupported


#: Sub's column spellings for the two values that ARE allowlisted inputs.
#: `dotmac_sub/app/models/branding.py::BrandProfile`.
_LEGACY_ALIASES: Final[Mapping[str, str]] = {
    "primary_color": "primary_hex",
    "secondary_color": "accent_hex",
    # Already-correct spellings pass through, so a caller can hand this function
    # a partially-migrated record without special-casing.
    "primary_hex": "primary_hex",
    "accent_hex": "accent_hex",
}

#: Sub's semantic tone keys, in both the flat and the nested spellings its
#: service uses (`semantic_positive_color` in the static map,
#: `metadata_["semantic_colors"]["positive"]` per profile).
_SEMANTIC_KEYS: Final[frozenset[str]] = frozenset(
    set(SEMANTIC_INTENTS)
    | {f"semantic_{intent}_color" for intent in SEMANTIC_INTENTS}
    | {f"brand_semantic_{intent}_color" for intent in SEMANTIC_INTENTS}
)


def translate_legacy_brand_values(
    values: Mapping[str, str | None],
) -> BrandValueTranslation:
    """Map a legacy brand record onto the allowlisted inputs, reporting the rest.

    The migration seam for Sub's cutover, and deliberately a pure function: it
    touches no database, so a cutover can run it over every row and review the
    aggregate BEFORE writing anything.

    `None` values are skipped rather than reported — an unset column is not an
    unsupported value, and reporting it would bury the five that matter under
    every optional field a profile left blank.
    """
    accepted: dict[str, str] = {}
    unsupported: list[UnsupportedBrandValue] = []

    for key, raw in values.items():
        if raw is None:
            continue
        value = str(raw)
        target = _LEGACY_ALIASES.get(key)
        if target is not None:
            accepted[target] = value
            continue
        if key in _SEMANTIC_KEYS:
            unsupported.append(
                UnsupportedBrandValue(
                    source_key=key,
                    value=value,
                    disposition=Disposition.OWNED_BY_PUBLISHED_TOKEN,
                    detail=(
                        "dotmac-ui publishes the semantic intents "
                        f"{', '.join(SEMANTIC_INTENTS)} as tokens with built-in "
                        "ramps, and render_brand_css seeds only the brand and "
                        "accent ramps. A per-profile override would be a second "
                        "authority over a published token (ADR-0006 D8/U1). "
                        "Changing the published token is the path forward."
                    ),
                )
            )
            continue
        unsupported.append(
            UnsupportedBrandValue(
                source_key=key,
                value=value,
                disposition=Disposition.NOT_AN_ALLOWLISTED_INPUT,
                detail=(
                    "not an input dotmac_ui.BrandOverride accepts; the "
                    f"allowlist is {', '.join(sorted(BRAND_OVERRIDE_INPUTS))}"
                ),
            )
        )
    return BrandValueTranslation(accepted=accepted, unsupported=tuple(unsupported))


def validate_brand_values(values: Mapping[str, str | None]) -> None:
    """Raise if any allowlisted value is not one `dotmac-ui` would accept.

    Validation is `dotmac-ui`'s, not this module's: constructing a throwaway
    `BrandOverride` runs `parse_hex` through the published surface, so there is
    no second parser here to drift from the first. A malformed colour fails where
    it was entered rather than when a page renders.

    Deliberately does NOT return the `BrandOverride`. Mapping profile values into
    one is the assembly's job under the 2026-08-19 boundary, and a validator that
    handed back a ready-made override would quietly take that job back.
    """
    primary = values.get("primary_hex")
    accent = values.get("accent_hex")
    if primary is None:
        # An uncoloured profile is legitimate — the deployment falls back to
        # dotmac-ui's own tokens. There is nothing to validate.
        if accent is None:
            return
        # An accent with no primary cannot be rendered: `render_brand_css`
        # generates the accent ramp only alongside a brand ramp, so this would
        # be a value that silently never reaches a page.
        raise ValueError(
            "accent_hex is set without primary_hex; dotmac-ui renders an accent "
            "ramp only alongside a brand ramp, so this accent would never reach "
            "a page"
        )
    BrandOverride(primary=str(primary), accent=str(accent) if accent else None)


__all__ = [
    "BRAND_OVERRIDE_INPUTS",
    "BrandValueTranslation",
    "Disposition",
    "UnsupportedBrandValue",
    "brand_override_fields",
    "translate_legacy_brand_values",
    "validate_brand_values",
]
