"""dotmac-ui — the DotMac shared UI design system's public API.

This module is the SINGLE CANONICAL public-surface manifest, deliberately shaped
like `dotmac_kernel.__init__` so the two packages are governed the same way:
`SUPPORTED_MODULES` (which submodules are public), `INTERNAL_MODULES`
(explicitly private), each supported module's own `__all__`, and the curated
top-level `__all__` re-exported below. Anything else is private and may change
without a deprecation cycle. The prose companion is `COMPATIBILITY.md`;
`tests/architecture/test_ui_public_surface.py` enforces that the reference
assembly imports only what is declared here.

**What this package is** (ADR-0006 § 2): semantic design tokens, packaged static
assets, accessibility contracts, and the Jinja/HTMX component library — shipped
as INERT package data, so the package still imports no templating engine. See
`dotmac_ui.components`. Layouts and navigation primitives are later slices.

**What it is not, and cannot become.** It has no business logic, reads no
database, mounts no route, and imports no module, no assembly, and (at 0.1.0a1)
not even the kernel. ADR-0006 § 2 permits `dotmac-ui → dotmac-kernel`; this
release takes no such dependency, and the architecture guard pins the stronger
fact rather than the permitted one — which is what lets `dotmac_erp`, a product
that has adopted no kernel at all, consume the design system today.

**Toolchain-agnostic to consumers (ADR-0006 D3).** The published contract is
compiled CSS plus these Python contracts. A consumer never runs this package
through Tailwind, PostCSS, or a bundler, and never has to match a Tailwind
major. See `dotmac_ui.assets`.
"""

from __future__ import annotations

from typing import Final

from dotmac_ui.a11y import (
    CONTRAST_REQUIREMENTS,
    NON_TEXT_CONTRAST_MINIMUM,
    TEXT_CONTRAST_MINIMUM,
    ContrastFailure,
    ContrastRequirement,
    check_contrast,
    contrast_ratio,
    token_contrast,
)
from dotmac_ui.assets import (
    MANIFEST_RELPATH,
    STYLESHEET_RELPATH,
    TAILWIND_PRESET_RELPATH,
    asset_digest,
    asset_manifest,
    static_dir,
    stylesheet_path,
    stylesheet_url,
    tailwind_preset_path,
)
from dotmac_ui.brand import (
    BrandOverride,
    BrandWarning,
    GeneratedBrand,
    generate_ramp,
    render_brand_css,
)
from dotmac_ui.color import OKLCH, ClampedColor, hex_to_oklch, oklch_to_hex
from dotmac_ui.components import (
    COMPONENTS,
    EMPTY_STATE,
    TEMPLATE_NAMESPACE,
    ComponentContract,
    component_classes,
    template_dir,
)
from dotmac_ui.contract import (
    ACCESSIBILITY_TARGET,
    CLASS_PREFIX,
    DARK_THEME_SELECTORS,
    DATA_ATTRIBUTE_PREFIX,
    PUBLISHED_COMPONENT_CLASSES,
    SUPPORTED_UI_CONTRACT_VERSIONS,
    THEME_ATTRIBUTE,
    TOKEN_PREFIX,
    UI_CONTRACT_VERSION,
)
from dotmac_ui.theme import (
    DEFAULT_THEME,
    THEME_STORAGE_KEY,
    THEME_VALUES,
    bootstrap_script,
    set_theme_script,
)
from dotmac_ui.tokens import (
    ACTION_INTENTS,
    ACTION_STATES,
    CATEGORIES,
    MODES,
    SEMANTIC_INTENTS,
    TOKENS,
    DesignToken,
    css_variable,
    resolve_color,
    token,
    token_names,
    tokens_in,
    variable_names,
)

#: The distribution version. Kept in sync with `pyproject.toml` by
#: `test_declared_version_matches_pyproject` — `importlib.metadata` is not used
#: because the package must be importable straight from a source checkout (an
#: air-gapped or vendored consumer) where no distribution is installed.
__version__: Final[str] = "0.1.0a6"

#: Public submodules. `from dotmac_ui.<module> import X` is supported for any
#: `X` in that module's `__all__`.
SUPPORTED_MODULES: Final[frozenset[str]] = frozenset(
    {
        "dotmac_ui.a11y",
        "dotmac_ui.assets",
        "dotmac_ui.components",
        "dotmac_ui.contract",
        "dotmac_ui.theme",
        "dotmac_ui.tokens",
    }
)

#: Explicitly private modules. `build` is the asset generator: this repository
#: runs it, consumers reference its output.
INTERNAL_MODULES: Final[frozenset[str]] = frozenset({"dotmac_ui.build"})

__all__ = [
    "render_brand_css",
    "oklch_to_hex",
    "hex_to_oklch",
    "generate_ramp",
    "OKLCH",
    "GeneratedBrand",
    "ClampedColor",
    "BrandWarning",
    "BrandOverride",
    "ACCESSIBILITY_TARGET",
    "ACTION_INTENTS",
    "ACTION_STATES",
    "CATEGORIES",
    "CLASS_PREFIX",
    "COMPONENTS",
    "CONTRAST_REQUIREMENTS",
    "DARK_THEME_SELECTORS",
    "DATA_ATTRIBUTE_PREFIX",
    "DEFAULT_THEME",
    "EMPTY_STATE",
    "INTERNAL_MODULES",
    "MANIFEST_RELPATH",
    "MODES",
    "NON_TEXT_CONTRAST_MINIMUM",
    "PUBLISHED_COMPONENT_CLASSES",
    "SEMANTIC_INTENTS",
    "STYLESHEET_RELPATH",
    "SUPPORTED_MODULES",
    "SUPPORTED_UI_CONTRACT_VERSIONS",
    "TAILWIND_PRESET_RELPATH",
    "TEMPLATE_NAMESPACE",
    "TEXT_CONTRAST_MINIMUM",
    "THEME_ATTRIBUTE",
    "THEME_STORAGE_KEY",
    "THEME_VALUES",
    "TOKENS",
    "TOKEN_PREFIX",
    "UI_CONTRACT_VERSION",
    "ComponentContract",
    "ContrastFailure",
    "ContrastRequirement",
    "DesignToken",
    "__version__",
    "asset_digest",
    "asset_manifest",
    "bootstrap_script",
    "check_contrast",
    "component_classes",
    "contrast_ratio",
    "css_variable",
    "resolve_color",
    "set_theme_script",
    "static_dir",
    "stylesheet_path",
    "stylesheet_url",
    "tailwind_preset_path",
    "template_dir",
    "token",
    "token_contrast",
    "token_names",
    "tokens_in",
    "variable_names",
]
