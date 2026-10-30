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

DASHBOARD_GRID: Final[ComponentContract] = ComponentContract(
    template="dotmac_ui/components/dashboard_grid.html",
    macro="dashboard_grid",
    parameters=("label",),
    classes=frozenset({"dmui-dashboard-grid"}),
)

DASHBOARD_PANEL: Final[ComponentContract] = ComponentContract(
    template="dotmac_ui/components/dashboard_panel.html",
    macro="dashboard_panel",
    parameters=("title", "description", "action_label", "action_url", "span"),
    classes=frozenset(
        {
            "dmui-dashboard-panel",
            "dmui-dashboard-panel--half",
            "dmui-dashboard-panel--full",
            "dmui-dashboard-panel__header",
            "dmui-dashboard-panel__heading",
            "dmui-dashboard-panel__title",
            "dmui-dashboard-panel__description",
            "dmui-dashboard-panel__action",
            "dmui-dashboard-panel__body",
        }
    ),
)

METRIC_CARD: Final[ComponentContract] = ComponentContract(
    template="dotmac_ui/components/metric_card.html",
    macro="metric_card",
    parameters=(
        "label",
        "value",
        "primary_detail",
        "secondary_detail",
        "href",
        "tone",
    ),
    classes=frozenset(
        {
            "dmui-metric-card",
            "dmui-metric-card--link",
            "dmui-metric-card--static",
            "dmui-metric-card--neutral",
            "dmui-metric-card--positive",
            "dmui-metric-card--info",
            "dmui-metric-card--warning",
            "dmui-metric-card--negative",
            "dmui-metric-card__heading",
            "dmui-metric-card__label",
            "dmui-metric-card__value",
            "dmui-metric-card__details",
            "dmui-metric-card__primary-detail",
            "dmui-metric-card__secondary-detail",
        }
    ),
)

NOTICE_BANNER: Final[ComponentContract] = ComponentContract(
    template="dotmac_ui/components/notice_banner.html",
    macro="notice_banner",
    parameters=("title", "message", "dismiss_label"),
    classes=frozenset(
        {
            "dmui-notice-banner",
            "dmui-notice-banner__content",
            "dmui-notice-banner__indicator",
            "dmui-notice-banner__title",
            "dmui-notice-banner__message",
            "dmui-notice-banner__dismiss",
        }
    ),
)

DASHBOARD_LINE_CHART: Final[ComponentContract] = ComponentContract(
    template="dotmac_ui/components/dashboard_line_chart.html",
    macro="dashboard_line_chart",
    parameters=(
        "title",
        "description",
        "series",
        "range_label",
        "filter_label",
        "filter_name",
        "filter_options",
    ),
    classes=frozenset(
        {
            "dmui-line-chart",
            "dmui-line-chart__header",
            "dmui-line-chart__heading",
            "dmui-line-chart__title",
            "dmui-line-chart__description",
            "dmui-line-chart__toolbar",
            "dmui-line-chart__filter",
            "dmui-line-chart__filter-label",
            "dmui-line-chart__select",
            "dmui-line-chart__range",
            "dmui-line-chart__legend",
            "dmui-line-chart__legend-item",
            "dmui-line-chart__swatch",
            "dmui-line-chart__swatch--neutral",
            "dmui-line-chart__swatch--positive",
            "dmui-line-chart__swatch--info",
            "dmui-line-chart__swatch--warning",
            "dmui-line-chart__swatch--negative",
            "dmui-line-chart__figure",
            "dmui-line-chart__svg",
            "dmui-line-chart__grid",
            "dmui-line-chart__series",
            "dmui-line-chart__series--neutral",
            "dmui-line-chart__series--positive",
            "dmui-line-chart__series--info",
            "dmui-line-chart__series--warning",
            "dmui-line-chart__series--negative",
        }
    ),
)

NOTIFICATION_TOAST: Final[ComponentContract] = ComponentContract(
    template="dotmac_ui/components/notification_toast.html",
    macro="notification_toast",
    parameters=(
        "title",
        "message",
        "action_label",
        "action_url",
        "dismiss_label",
        "auto_dismiss_ms",
    ),
    classes=frozenset(
        {
            "dmui-notification-toast",
            "dmui-notification-toast__content",
            "dmui-notification-toast__title",
            "dmui-notification-toast__message",
            "dmui-notification-toast__actions",
            "dmui-notification-toast__action",
            "dmui-notification-toast__dismiss",
        }
    ),
)

#: Every component this contract version publishes.
COMPONENTS: Final[tuple[ComponentContract, ...]] = (
    EMPTY_STATE,
    MAP_FRAME,
    DASHBOARD_GRID,
    DASHBOARD_PANEL,
    METRIC_CARD,
    NOTICE_BANNER,
    DASHBOARD_LINE_CHART,
    NOTIFICATION_TOAST,
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
    "COMPONENTS",
    "DASHBOARD_GRID",
    "DASHBOARD_LINE_CHART",
    "DASHBOARD_PANEL",
    "EMPTY_STATE",
    "MAP_FRAME",
    "METRIC_CARD",
    "NOTICE_BANNER",
    "NOTIFICATION_TOAST",
    "TEMPLATE_NAMESPACE",
    "ComponentContract",
    "component_classes",
    "template_dir",
]
