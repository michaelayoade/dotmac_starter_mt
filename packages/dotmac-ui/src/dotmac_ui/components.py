"""The Jinja/HTMX component library — package data, not a runtime dependency.

ADR-0006 § 2 assigns the component library to this package. Shipping Jinja
templates from a **dependency-free** package works because the templates are
INERT DATA: this module resolves a directory, and the HOST supplies Jinja. There
is no `import jinja2` here, in this package, or in its dependency set — the
consumer already has an environment, and hands it one more search path.

A consumer wires it exactly like the stylesheet, through an anonymous slot::

    spec = ProductAssemblySpec(
        packaged_template_dirs=(dotmac_ui.template_dir(),),
        packaged_static_dirs=(dotmac_ui.static_dir(),),
        stylesheets=(dotmac_ui.stylesheet_url(),),
    )

and then, in any template::

    {% from "dotmac_ui/components/empty_state.html" import empty_state %}
    {{ empty_state(title="No parties found") }}

## Why the namespaced path

`template_dir()` points at a directory whose ONLY child is `dotmac_ui/`, so
every template this package publishes is addressed as
`dotmac_ui/components/<name>.html`. A flat `components/empty_state.html` would
collide with the kernel's own `components/` tree the moment both are in one
`ChoiceLoader`, and the winner would depend on layer order — a silent,
order-dependent override of one package's markup by another's. The namespace
makes that impossible rather than unlikely.

## What a component may assume

Nothing beyond stock Jinja. A published template uses no custom filter, no
global, no context processor, and no `url_for` — every value it renders arrives
as a macro argument. That is what `test_components_render_on_a_clean_host`
proves, with a bare `Environment` that has none of the kernel's globals
installed. HTMX and Alpine are likewise not assumed: the markup is static, and a
consumer adds `hx-*` attributes from the outside if it wants them.

Styling is `.dmui-*` classes defined in the compiled stylesheet, never utility
classes. A consumer does not compile this package's templates, so a template
carrying `bg-slate-700` would render unstyled anywhere the consumer's Tailwind
content globs do not reach into site-packages — which is every correctly
configured consumer.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

_PKG_DIR: Final[Path] = Path(__file__).resolve().parent
_TEMPLATE_DIR: Final[Path] = _PKG_DIR / "templates"

#: The single top-level directory inside `template_dir()`. Every published
#: template lives under it, so a host's loader cannot confuse a dotmac-ui
#: template with one of its own.
TEMPLATE_NAMESPACE: Final[str] = "dotmac_ui"


@dataclass(frozen=True, slots=True)
class ComponentContract:
    """One published component: where it lives, how it is called, what it styles.

    The parameter tuple is the SIGNATURE, and it is part of the published
    contract: removing a parameter or changing what one means is a breaking
    change to the UI contract version, exactly like removing a token.
    """

    #: Loader-relative template path, always under `TEMPLATE_NAMESPACE`.
    template: str
    #: The macro to import from that template.
    macro: str
    #: Accepted macro parameters, in positional order.
    parameters: tuple[str, ...]
    #: Every `.dmui-*` class the macro's markup emits.
    classes: frozenset[str]


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """Display-only data for one catalog card.

    Products remain responsible for membership, authorization, availability,
    price formatting, state labels, and action targets. Keeping those values as
    optional display strings prevents the presentation package from becoming a
    second commercial or operational decision owner.
    """

    title: str
    meta: str | None = None
    description: str | None = None
    media_url: str | None = None
    media_alt: str = ""
    notice: str | None = None
    action_label: str | None = None
    action_url: str | None = None


LIST_CELL_TONES: Final[frozenset[str]] = frozenset(
    {"positive", "info", "warning", "negative", "neutral"}
)


@dataclass(frozen=True, slots=True)
class ListColumn:
    """One display column; query capability remains with the backend owner."""

    key: str
    label: str
    sortable: bool = False
    mobile_visible: bool = True

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.label.strip():
            raise ValueError("List column key and label are required")


@dataclass(frozen=True, slots=True)
class ListCell:
    """Already-formatted cell content, optionally carrying a semantic tone."""

    text: str
    secondary: str | None = None
    href: str | None = None
    tone: str | None = None

    def __post_init__(self) -> None:
        normalized = str(self.tone) if self.tone is not None else None
        if normalized is not None and normalized not in LIST_CELL_TONES:
            raise ValueError(f"Unsupported list-cell tone: {normalized}")
        object.__setattr__(self, "tone", normalized)


@dataclass(frozen=True, slots=True)
class ListRow:
    """One projected row with at most one already-eligible visible action."""

    cells: tuple[ListCell, ...]
    action_label: str | None = None
    action_url: str | None = None

    def __post_init__(self) -> None:
        if bool(self.action_label) != bool(self.action_url):
            raise ValueError("List row action label and URL must be declared together")


@dataclass(frozen=True, slots=True)
class ListFilterOption:
    """One product-supplied filter value and human label."""

    value: str
    label: str

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("List filter option label is required")


@dataclass(frozen=True, slots=True)
class ListFilter:
    """One visible filter whose key was declared by the list owner."""

    key: str
    label: str
    options: tuple[ListFilterOption, ...]

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.label.strip():
            raise ValueError("List filter key and label are required")


@dataclass(frozen=True, slots=True)
class ActivityItem:
    """Display-only official-timeline projection supplied by its product owner."""

    message: str
    detail: str | None = None
    time_label: str | None = None
    time_iso: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("Activity message is required")
        if self.time_iso and not self.time_label:
            raise ValueError("Activity time ISO value requires a display label")


EMPTY_STATE: Final[ComponentContract] = ComponentContract(
    template="dotmac_ui/components/empty_state.html",
    macro="empty_state",
    parameters=("title", "message", "action_label", "action_url"),
    classes=frozenset(
        {
            "dmui-empty-state",
            "dmui-empty-state__visual",
            "dmui-empty-state__icon",
            "dmui-empty-state__title",
            "dmui-empty-state__message",
            "dmui-empty-state__action",
            "dmui-empty-state__action-icon",
        }
    ),
)

MAP_FRAME: Final[ComponentContract] = ComponentContract(
    template="dotmac_ui/components/map_frame.html",
    macro="map_frame",
    parameters=(
        "canvas_id",
        "label",
        "state",
        "status_title",
        "status_message",
    ),
    classes=frozenset(
        {
            "dmui-map-frame",
            "dmui-map-frame--ready",
            "dmui-map-frame--loading",
            "dmui-map-frame--empty",
            "dmui-map-frame--error",
            "dmui-map-frame__canvas",
            "dmui-map-frame__state",
            "dmui-map-frame__state-panel",
            "dmui-map-frame__state-indicator",
            "dmui-map-frame__state-title",
            "dmui-map-frame__state-message",
            "dmui-map-frame__live",
        }
    ),
)

CATALOG_GRID: Final[ComponentContract] = ComponentContract(
    template="dotmac_ui/components/catalog_grid.html",
    macro="catalog_grid",
    parameters=(
        "items",
        "empty_title",
        "empty_message",
        "empty_action_label",
        "empty_action_url",
    ),
    classes=frozenset(
        {
            "dmui-catalog-grid",
            "dmui-catalog-grid__item",
            "dmui-catalog-grid__media",
            "dmui-catalog-grid__body",
            "dmui-catalog-grid__title",
            "dmui-catalog-grid__meta",
            "dmui-catalog-grid__description",
            "dmui-catalog-grid__notice",
            "dmui-catalog-grid__action",
            "dmui-catalog-grid__action-icon",
        }
    ),
)

LIST_SURFACE: Final[ComponentContract] = ComponentContract(
    template="dotmac_ui/components/list_surface.html",
    macro="list_surface",
    parameters=(
        "title",
        "columns",
        "rows",
        "query",
        "page_meta",
        "base_url",
        "filters",
        "search_label",
        "search_placeholder",
        "empty_title",
        "empty_message",
        "empty_action_label",
        "empty_action_url",
    ),
    classes=frozenset(
        {
            "dmui-list-surface",
            "dmui-list-surface__title",
            "dmui-list-surface__controls",
            "dmui-list-surface__search",
            "dmui-list-surface__search-label",
            "dmui-list-surface__search-input",
            "dmui-list-surface__filters",
            "dmui-list-surface__filter",
            "dmui-list-surface__filter-label",
            "dmui-list-surface__select",
            "dmui-list-surface__apply",
            "dmui-list-surface__table-wrap",
            "dmui-list-surface__table",
            "dmui-list-surface__head",
            "dmui-list-surface__heading",
            "dmui-list-surface__heading--desktop",
            "dmui-list-surface__sort",
            "dmui-list-surface__sort-indicator",
            "dmui-list-surface__row",
            "dmui-list-surface__cell",
            "dmui-list-surface__cell--desktop",
            "dmui-list-surface__cell-link",
            "dmui-list-surface__cell-text",
            "dmui-list-surface__cell-secondary",
            "dmui-list-surface__state",
            "dmui-list-surface__actions",
            "dmui-list-surface__row-action",
            "dmui-list-surface__footer",
            "dmui-list-surface__summary",
            "dmui-list-surface__pagination",
            "dmui-list-surface__page",
            "dmui-list-surface__page--current",
            "dmui-list-surface__page--disabled",
            "dmui-list-surface__ellipsis",
        }
    ),
)

RECENT_ACTIVITY: Final[ComponentContract] = ComponentContract(
    template="dotmac_ui/components/recent_activity.html",
    macro="recent_activity",
    parameters=(
        "activities",
        "title",
        "link_label",
        "link_url",
        "empty_title",
        "empty_message",
    ),
    classes=frozenset(
        {
            "dmui-recent-activity",
            "dmui-recent-activity__header",
            "dmui-recent-activity__heading",
            "dmui-recent-activity__heading-icon",
            "dmui-recent-activity__title",
            "dmui-recent-activity__all-link",
            "dmui-recent-activity__list",
            "dmui-recent-activity__item",
            "dmui-recent-activity__item-link",
            "dmui-recent-activity__marker",
            "dmui-recent-activity__body",
            "dmui-recent-activity__message",
            "dmui-recent-activity__detail",
            "dmui-recent-activity__time",
            "dmui-recent-activity__empty",
            "dmui-recent-activity__empty-icon",
            "dmui-recent-activity__empty-title",
            "dmui-recent-activity__empty-message",
        }
    ),
)

#: Every component this contract version publishes.
COMPONENTS: Final[tuple[ComponentContract, ...]] = (
    EMPTY_STATE,
    MAP_FRAME,
    CATALOG_GRID,
    LIST_SURFACE,
    RECENT_ACTIVITY,
)


@lru_cache(maxsize=1)
def template_dir() -> Path:
    """The directory to add to the host's Jinja search path.

    Resolved by PACKAGE PATH, never by CWD — an installed wheel lives outside
    every assembly's working directory, the same reason `static_dir()` works
    the way it does.
    """
    return _TEMPLATE_DIR


def component_classes() -> frozenset[str]:
    """Every `.dmui-*` class published by any component, as one set."""
    return frozenset().union(*(component.classes for component in COMPONENTS))


__all__ = [
    "ActivityItem",
    "CATALOG_GRID",
    "COMPONENTS",
    "EMPTY_STATE",
    "LIST_CELL_TONES",
    "LIST_SURFACE",
    "MAP_FRAME",
    "RECENT_ACTIVITY",
    "TEMPLATE_NAMESPACE",
    "CatalogItem",
    "ComponentContract",
    "ListCell",
    "ListColumn",
    "ListFilter",
    "ListFilterOption",
    "ListRow",
    "component_classes",
    "template_dir",
]
