"""The UI contract: version axis, namespaces, and theme selectors.

**Two version axes (ADR-0006 § 1).** A module declares the *module contract*
version it was built against (kernel-owned,
`dotmac_kernel.modules.KERNEL_MODULE_CONTRACT_VERSION`) **and**, separately, the
*UI contract* version it renders against (`UI_CONTRACT_VERSION`, below). They
are independent integers owned by two different packages: a UI revision must not
force every module to re-declare its capability contract, and a kernel manifest
revision must not invalidate a module's rendering assumptions. Nothing in this
package imports the kernel, so the two cannot accidentally be coupled — the
architecture guard proves it.

`UI_CONTRACT_VERSION` is bumped ONLY for a change a consumer can observe in the
*stable* surface described by `COMPATIBILITY.md`: removing or renaming a
published token, changing what a published token *role* means, or removing a
published component class / data attribute. Adding a token, changing a token's
*value*, or shipping a new component class is additive and does not bump it.

The contract version is also the artifact's identity: it appears in the compiled
stylesheet's filename (`dotmac-ui-<UI_CONTRACT_VERSION>.css`), so a consumer
that has pinned contract 1 keeps resolving contract 1's asset even after
contract 2 ships alongside it.
"""

from __future__ import annotations

from typing import Final

from dotmac_ui.components import component_classes

#: The UI contract generation this package publishes. INDEPENDENT of the
#: kernel's module contract version — see this module's docstring.
UI_CONTRACT_VERSION: Final[int] = 1

#: Contract generations this release can serve assets for. A rollout that must
#: support two generations at once ships both artifacts and widens this set;
#: it is a frozenset rather than a single int so that is a release, not a flag
#: day.
SUPPORTED_UI_CONTRACT_VERSIONS: Final[frozenset[int]] = frozenset({1})

#: Every CSS custom property this package publishes starts with this prefix.
#:
#: The prefix is not decoration. Tailwind v4's `@theme` block emits `--color-*`,
#: `--font-*`, `--spacing-*` and friends into `:root` of the CONSUMER's compiled
#: stylesheet, and every product in the fleet already defines unprefixed tokens
#: of its own (`--surface-primary` in `dotmac_sub`, `--teal` in `dotmac_erp`).
#: An unprefixed `--surface-primary` shipped by this package would collide with
#: Sub's, and whichever stylesheet happened to load last would win. Namespacing
#: makes the published surface collision-free and greppable; the ROLE names
#: inside it are Sub's vocabulary (see COMPATIBILITY.md § "Where the vocabulary
#: came from").
TOKEN_PREFIX: Final[str] = "--dmui-"

#: Namespace for published component classes. ADR-0006 § 5 still forbids
#: harvesting markup that merely looks similar: a class only becomes public by
#: being added to `PUBLISHED_COMPONENT_CLASSES`, backed by a
#: `dotmac_ui.components.ComponentContract`, and documented in
#: COMPATIBILITY.md.
CLASS_PREFIX: Final[str] = "dmui-"

#: Reserved namespace for published data attributes (state hooks that survive
#: a class-name refactor, e.g. `data-dmui-state="loading"`).
DATA_ATTRIBUTE_PREFIX: Final[str] = "data-dmui-"

#: The published component classes. DERIVED from the component contracts rather
#: than listed again here: a hand-maintained second list is a place for the
#: registry and the markup to disagree, and the stylesheet guard
#: (`test_no_component_class_is_published_without_its_contract`) would then be
#: checking the copy instead of the contract.
PUBLISHED_COMPONENT_CLASSES: Final[frozenset[str]] = component_classes()

#: The attribute a host document sets to force a colour mode.
THEME_ATTRIBUTE: Final[str] = "data-dmui-theme"

#: The selectors under which the dark-mode token values are emitted, in order.
#:
#: `.dark` is included for fleet compatibility, not as a preference: both
#: `dotmac_starter_mt` and `dotmac_sub` already toggle a `dark` class on
#: `<html>` (Tailwind's class strategy), so a consumer that adopts this
#: stylesheet gets correct dark tokens with no template change at all.
#: `[data-dmui-theme="dark"]` is the package-owned hook for a host that does
#: not use Tailwind's class strategy — ERP, for one.
DARK_THEME_SELECTORS: Final[tuple[str, ...]] = (
    ".dark",
    f'[{THEME_ATTRIBUTE}="dark"]',
)

#: The WCAG version and conformance level the design system targets for
#: critical journeys. See `dotmac_ui.a11y` for what is machine-checked today
#: and COMPATIBILITY.md § "Accessibility contract" for what is not.
ACCESSIBILITY_TARGET: Final[str] = "WCAG 2.2 Level AA"

__all__ = [
    "ACCESSIBILITY_TARGET",
    "CLASS_PREFIX",
    "DARK_THEME_SELECTORS",
    "DATA_ATTRIBUTE_PREFIX",
    "PUBLISHED_COMPONENT_CLASSES",
    "SUPPORTED_UI_CONTRACT_VERSIONS",
    "THEME_ATTRIBUTE",
    "TOKEN_PREFIX",
    "UI_CONTRACT_VERSION",
]
