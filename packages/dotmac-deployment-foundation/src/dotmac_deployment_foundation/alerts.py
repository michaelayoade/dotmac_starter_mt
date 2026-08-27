"""The common infrastructure alert catalogue, and its renderer.

ADR-0070 draws the line this module enforces: a PRODUCT declares its own
domain alerts (`ProductDeploymentSpec.product_alerts`, typed as
`ProductAlert` in `spec.py`) — things like "checkout conversion dropped" that
only the product can define — while every INFRASTRUCTURE alert that is true
of any deployment on the fleet (HTTP error rate, container OOM, Postgres
replication lag, a stale backup, a silent telemetry pipeline) is defined
exactly ONCE here and inherited by every product built from this facility.
Before this module existed, `dotmac_sub`, `dotmac_erp` and `dotmac_integrator`
each hand-wrote their own partial, drifting version of the same sixty-odd
rules — `dotmac_integrator`'s `deploy/alerts/ingress.rules.yml` is one
surviving fragment of that pattern (rule 19: a threshold lives in
`deploy/alerts/`, never hand-typed into a process, and a retention threshold
that has no agreed value stays deliberately UNSET rather than guessed). This
catalogue is that discipline generalised: every number a product must supply
is a named placeholder, and a placeholder with no supplied value fails
`render_alert_rules` rather than silently rendering with an invented number.

## Why every `Alert` field is mandatory

`__post_init__` refuses an `Alert` missing any of the following, because each
missing field wakes somebody who cannot act:

- **no `owner`** — the alert is nobody's, so nobody is paged with standing to
  fix it.
- **no `runbook`** — the responder who gets paged at 03:00 starts from zero
  instead of from a known procedure.
- **no `recovery`** — the alert has no stated condition under which it
  resolves, so it never resolves. An alert that never resolves is eventually
  muted by whoever is tired of it, which is deleting the alert while still
  believing it protects something.
- **no `protects`** — nothing records WHY the alert exists, so a future
  cleanup cannot tell a load-bearing alert from a stale one.
- **empty `dedup_by`** — with no dedup identity, the same underlying fault
  pages once per label combination instead of once, and the second page
  during an active incident reads as a second incident.

## Severity is `page | ticket | info`, never `critical | warning`

The catalogue below is transcribed from a review written in the fleet's
familiar `critical`/`warning` vocabulary, but `Alert.severity` (like
`ProductAlert.severity` in `spec.py`) speaks the ROUTING vocabulary instead:
`page` wakes somebody now, `ticket` is worked in business hours, `info` pages
nobody. `critical` maps to `page` and `warning` maps to `ticket` at
transcription time (`_PAGE`/`_TICKET` below) precisely so that the rendered
rule file, the alert router and `ProductAlert` all agree on one vocabulary —
a renderer that emitted `critical`/`warning` labels would need a second,
driftable translation table downstream, in the router, forever.

## Why a foundation expression is never rewritten with a product selector

`render_alert_rules` does NOT inject a `dotmac_product="<product>"` matcher
into each foundation PromQL expression to scope it to one product's series.
Rewriting an arbitrary hand-authored expression per product is exactly the
kind of stringly-typed surgery this whole facility exists to avoid: an
expression with an aggregation (`sum(...) by (...)`), a `{{placeholder}}`
inside a label matcher, or an `on()` vector match is not safe to blindly
splice a selector into, and a renderer that gets it wrong produces a rule
that LOOKS scoped and silently is not. The real scoping already happens one
layer down, in the collector: every signal is stamped with the resource
attributes derived in `telemetry.py` (`RESOURCE_ATTRIBUTES`, notably
`dotmac.product`) before it ever reaches the time-series store, so a query
against `http_requests_total` on a shared Prometheus is already scoped by
whatever the query groups or filters on. What THIS module adds instead is a
`product` LABEL on every rendered rule (foundation and product alike), which
is what a dashboard or an alert router groups and routes on — the same fact,
expressed once, at the layer that actually owns it.

## Placeholders: a `product-threshold` with no value is a refusal, not a guess

Every `{{name}}` token in a foundation expression is one of two kinds
(`PLACEHOLDER_SOURCES`):

- **`foundation-default`** — a value the facility itself is willing to
  stand behind (`FOUNDATION_DEFAULTS`), because it is a reasonable fleet-wide
  starting point (an 85% CPU-saturation warning, a 21-day TLS-expiry
  warning) that a product may still override through `thresholds`.
- **`product-threshold`** — a number only the product can supply (how many
  worker-queue items constitute a backlog, what its own acceptable Postgres
  replication lag is). There is deliberately NO default here, for the same
  reason `dotmac_integrator`'s retention threshold ships unset rather than
  guessed: a monitoring rule silently rendered against an invented number
  reports coverage it does not have, and that is worse than the rule being
  visibly absent, because an absent rule is at least discoverable as a gap.

`render_alert_rules` therefore raises `SpecError` naming EVERY unresolved
`product-threshold` placeholder at once (never just the first), so a product
fixes its `thresholds` mapping in one pass instead of playing whack-a-mole
with one `SpecError` per missing name. `unresolved_placeholders` exposes the
same check as a query, for the CLI and the conformance kit to call before
attempting a render.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import ClassVar, Final

from .errors import SpecError
from .spec import ProductAlert, ProductDeploymentSpec
from .telemetry import RESOURCE_ATTRIBUTES

__all__ = [
    "Alert",
    "COMMON_ALERTS",
    "PLACEHOLDER_SOURCES",
    "FOUNDATION_DEFAULTS",
    "PRODUCERS",
    "UNBACKED",
    "UNBACKED_ALERTS",
    "metric_names_in",
    "producer_consistency_errors",
    "render_alert_rules",
    "render_alert_rules_digest",
    "unresolved_placeholders",
]

# Foundation alert codes carry the `FDN_` prefix so that a rendered rule file,
# a paging policy, or a grep can tell a fleet-wide alert apart from a
# product's own `ProductAlert.code` (validated only against the looser
# `_ALERT_CODE` in `spec.py`) on sight, with no lookup required.
_CODE: Final = re.compile(r"^FDN_[A-Z0-9]+(_[A-Z0-9]+)*$")

_PLACEHOLDER: Final = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")

# The severity-vocabulary map this module transcribes the catalogue through.
# See the module docstring, "Severity is `page | ticket | info`".
_PAGE: Final = "page"
_TICKET: Final = "ticket"

# ── producer vocabulary ─────────────────────────────────────────────────────
#
# "Who emits the metric(s) this expression reads" has a small, closed set of
# honest answers for a fleet built the way this one is (Compose hosts, no
# Kubernetes, an OTel-collector-based telemetry pipeline — see
# `telemetry.py`). See `docs/inventories/deployment-foundation-alert-producers.md`
# for the full alert-by-alert accounting this vocabulary supports.
UNBACKED: Final = "unbacked"

PRODUCERS: Final[tuple[str, ...]] = (
    # Standard, off-the-shelf infrastructure exporters: real software with a
    # well-known, documented metric surface, independent of whether THIS
    # package's Python code renders the sidecar that runs it (that is a
    # provider/compose concern, tracked separately from metric-name honesty).
    "node_exporter",
    "cadvisor",
    "postgres_exporter",
    "redis_exporter",
    "blackbox_exporter",
    # The ingress provider's own access/error telemetry (e.g. an nginx
    # module or its own exporter) — real once configured, but this facility
    # does not configure one today (see the inventory doc).
    "ingress",
    # The OTel collector's OWN self-telemetry, genuinely rendered by
    # `telemetry.render_collector_config` (`service.telemetry.metrics` on
    # 127.0.0.1:8888) — but only for the collector's OWN internal metric
    # names, never a stand-in for a Prometheus server's `up` or
    # `prometheus_remote_storage_*` metrics, which this OTLP-only pipeline
    # does not produce.
    "collector",
    # The product's own `/metrics` endpoint — real only once a product
    # actually instruments it; `dotmac_kernel` does not today.
    "application",
    # This facility itself — `backup.py`/`drift.py` compute policy and
    # comparisons in-process today but write no metric; a metric in this
    # category needs a gauge added to a `write_evidence`/drift/backup run.
    "deployment_engine",
    # Nothing emits it today. See the module docstring's "Why every `Alert`
    # field is mandatory" for why this is a refusal-by-declaration rather
    # than a silently absent field.
    UNBACKED,
)


def _placeholders_in(expression: str) -> tuple[str, ...]:
    """Every distinct `{{name}}` token in `expression`, in first-seen order."""
    seen: list[str] = []
    for name in _PLACEHOLDER.findall(expression):
        if name not in seen:
            seen.append(name)
    return tuple(seen)


# ── metric-name extraction ──────────────────────────────────────────────────
#
# `_LABEL_BLOCK` strips a PromQL label-matcher block (`{status=~"5.."}`) —
# its contents are label NAMES and quoted VALUES, never a metric name, and
# stripping it also removes any `{{placeholder}}` that was nested INSIDE a
# label value (e.g. `instance=~"{{known_live_instances}}"`) once the
# placeholder itself has already been stripped by `_PLACEHOLDER_BLOCK` below
# — placeholder-stripping must run first, or the placeholder's own braces
# would confuse the label-block matcher.
_PLACEHOLDER_BLOCK: Final = re.compile(r"\{\{[a-zA-Z_][a-zA-Z0-9_]*\}\}")
_LABEL_BLOCK: Final = re.compile(r"\{[^{}]*\}")

# `by (...)`, `on (...)`, `without (...)`, `group_left(...)`, `group_right(...)`
# — PromQL's grouping and vector-matching clauses. Their argument lists are
# label names (e.g. `by (le,service,instance)`), never metric names, and
# unlike a label-matcher block they sit OUTSIDE `{...}`, so they need their
# own strip.
_GROUPING_BLOCK: Final = re.compile(
    r"\b(?:by|on|without|group_left|group_right)\s*\([^()]*\)"
)

# The `(?<!\d)` lookbehind matters: a PromQL range-vector duration inside
# `[...]` (`5m`, `15m`, `1d`) is digit-then-letter with no separator, and
# without it the trailing letter (`m`, `d`) would match this pattern as its
# own one-character "identifier".
_METRIC_IDENTIFIER: Final = re.compile(r"(?<!\d)[a-zA-Z_][a-zA-Z0-9_]*")

# Bare PromQL keywords that appear with no trailing `(` — everything else
# (`by`, `on`, `without`, `group_left`, `group_right`) is removed whole by
# `_GROUPING_BLOCK` above, and every PromQL FUNCTION name (`rate`, `sum`,
# `histogram_quantile`, `increase`, `abs`, `absent`, `absent_over_time`,
# `max`, `avg`, `time`, ...) is excluded generically because it is always
# immediately followed by `(`.
_PROMQL_KEYWORDS: Final = frozenset(
    {"and", "or", "unless", "offset", "bool", "ignoring"}
)


def metric_names_in(expression: str) -> frozenset[str]:
    """Every bare metric identifier `expression` reads, best-effort.

    This is a regex tokenizer, not a PromQL parser. It excludes, in order:
    `{{placeholder}}` tokens, label-matcher blocks (`{...}`) and everything
    inside them (label names, quoted label values), grouping/vector-matching
    argument lists (`by (...)`, `on (...)`, `without (...)`, `group_left(...)`,
    `group_right(...)`), any identifier immediately followed by `(` (a
    PromQL function call), and the small set of bare keywords that carry no
    trailing `(` (`and`, `or`, `unless`, `offset`, `bool`, `ignoring`). A
    numeric literal (`0.99`, `86400`) is never matched in the first place,
    because the identifier pattern requires a letter or underscore as the
    first character; a duration literal (`5m`, `1d`) additionally requires a
    negative digit-lookbehind, because its trailing letter(s) alone (`m`,
    `d`) would otherwise match as a one-character "identifier" once the
    leading digit is skipped.

    A hand-rolled PromQL AST would get every corner of the grammar right; a
    regex over expressions this small and this hand-authored (64 rows, no
    subqueries, no `@` modifiers, no nested function calls inside a grouping
    clause) gets every expression actually in this catalogue right and is
    far cheaper for a reviewer to audit than a parser would be — which
    matters here because this function is exactly what a reviewer has to
    trust when `producer_consistency_errors` says "no mismatch".
    """
    text = _PLACEHOLDER_BLOCK.sub("", expression)
    text = _LABEL_BLOCK.sub("", text)
    text = _GROUPING_BLOCK.sub("", text)
    names: set[str] = set()
    for match in _METRIC_IDENTIFIER.finditer(text):
        name = match.group(0)
        if name in _PROMQL_KEYWORDS:
            continue
        if text[match.end() :].lstrip().startswith("("):
            continue
        names.add(name)
    return frozenset(names)


@dataclass(frozen=True, slots=True)
class Alert:
    """One foundation (fleet-wide, infrastructure) alert rule.

    See the module docstring for why every field below is mandatory and why
    `severity` speaks `page | ticket | info` rather than the catalogue's
    original `critical | warning`. `placeholders` is DERIVED-and-declared: it
    must equal, exactly, the `{{name}}` tokens actually present in
    `expression` — a placeholder declared but not used, or used but not
    declared, is refused at construction rather than discovered at render
    time, when a product's `thresholds` mapping is checked against it.

    `producer` names WHAT must emit the metric(s) `expression` reads — one
    of the closed `PRODUCERS` vocabulary, `UNBACKED` when nothing does. See
    the module docstring and
    `docs/inventories/deployment-foundation-alert-producers.md`.
    """

    code: str
    severity: str
    expression: str
    for_seconds: int
    owner: str
    protects: str
    runbook: str
    dedup_by: tuple[str, ...]
    recovery: str
    placeholders: tuple[str, ...]
    producer: str

    SEVERITIES: ClassVar[tuple[str, ...]] = ("page", "ticket", "info")

    def __post_init__(self) -> None:
        if not _CODE.match(self.code):
            raise SpecError(
                f"alert code {self.code!r} does not match {_CODE.pattern!r}; "
                "the foundation catalogue's codes all carry the FDN_ prefix "
                "so they are never mistaken for a product's own alert code",
                where=self.code,
            )
        if self.severity not in self.SEVERITIES:
            raise SpecError(
                f"severity must be one of {self.SEVERITIES}, not {self.severity!r}",
                where=self.code,
            )
        if self.producer not in PRODUCERS:
            raise SpecError(
                f"producer must be one of {PRODUCERS}, not {self.producer!r}; "
                "the vocabulary of 'who emits this metric' is closed — see "
                "the module docstring",
                where=self.code,
            )
        if not self.expression.strip():
            raise SpecError("expression may not be empty", where=self.code)
        if self.for_seconds < 0:
            raise SpecError("for_seconds may not be negative", where=self.code)
        if not self.owner.strip():
            raise SpecError(
                "owner may not be empty; an alert with no owner is nobody's",
                where=self.code,
            )
        if not self.protects.strip():
            raise SpecError(
                "protects may not be empty; nothing would record WHY the "
                "alert exists, and a future cleanup could not tell a "
                "load-bearing alert from a stale one",
                where=self.code,
            )
        if not self.runbook.strip():
            raise SpecError(
                "runbook may not be empty; a responder paged with no runbook "
                "starts from zero instead of from a known procedure",
                where=self.code,
            )
        if not self.recovery.strip():
            raise SpecError(
                "recovery may not be empty; an alert with no stated recovery "
                "condition never resolves, and is eventually muted by "
                "whoever is tired of it — which is deleting the alert while "
                "still believing it protects something",
                where=self.code,
            )
        if not self.dedup_by:
            raise SpecError(
                "dedup_by may not be empty; with no dedup identity the same "
                "underlying fault pages once per label combination, and the "
                "second page during an active incident reads as a second "
                "incident",
                where=self.code,
            )
        for label in self.dedup_by:
            if not label.strip():
                raise SpecError(
                    "dedup_by may not contain an empty label", where=self.code
                )
        actual = set(_placeholders_in(self.expression))
        declared = set(self.placeholders)
        if declared != actual:
            undeclared = sorted(actual - declared)
            unused = sorted(declared - actual)
            problems: list[str] = []
            if undeclared:
                problems.append(
                    f"expression uses {undeclared} which placeholders does not declare"
                )
            if unused:
                problems.append(
                    f"placeholders declares {unused} which the expression never uses"
                )
            raise SpecError(
                "placeholders must equal exactly the {{name}} tokens present "
                "in expression: " + "; ".join(problems),
                where=self.code,
            )


# ── the foundation catalogue ────────────────────────────────────────────────
#
# Transcribed row-for-row from the fleet infrastructure-alert review. Codes
# and PromQL expressions are copied verbatim; `for_window` is converted to
# seconds (the comment after each `for_seconds=` preserves the original
# window so a diff against the review stays readable); `owner` is "Foundation"
# in every row, mapped to the literal string "foundation"; the "dedup
# identity" column is split into a tuple of label names on `+`.

COMMON_ALERTS: Final[tuple[Alert, ...]] = (
    Alert(
        code="FDN_HTTP_5XX_RATE_HIGH",
        severity=_TICKET,
        expression=(
            'sum(rate(http_requests_total{status=~"5.."}[5m])) by (service,instance) '
            "/ sum(rate(http_requests_total[5m])) by (service,instance) "
            "> {{error_rate_warning_pct}}"
        ),
        for_seconds=600,  # 10m
        owner="foundation",
        protects="HTTP availability SLO",
        runbook="#http-5xx-rate",
        dedup_by=("service", "instance"),
        recovery="ratio below threshold for 2 consecutive windows",
        placeholders=("error_rate_warning_pct",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_HTTP_5XX_RATE_CRITICAL",
        severity=_PAGE,
        expression=(
            'sum(rate(http_requests_total{status=~"5.."}[5m])) by (service,instance) '
            "/ sum(rate(http_requests_total[5m])) by (service,instance) "
            "> {{error_rate_critical_pct}}"
        ),
        for_seconds=300,  # 5m
        owner="foundation",
        protects="HTTP availability SLO",
        runbook="#http-5xx-rate",
        dedup_by=("service", "instance"),
        recovery="ratio below threshold for 2 consecutive windows",
        placeholders=("error_rate_critical_pct",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_HTTP_LATENCY_P99_HIGH",
        severity=_TICKET,
        expression=(
            "histogram_quantile(0.99, "
            "sum(rate(http_request_duration_seconds_bucket[5m])) "
            "by (le,service,instance)) > {{latency_p99_warning_seconds}}"
        ),
        for_seconds=600,  # 10m
        owner="foundation",
        protects="request-latency SLO",
        runbook="#http-latency-p99",
        dedup_by=("service", "instance"),
        recovery="p99 under threshold for 10m",
        placeholders=("latency_p99_warning_seconds",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_HTTP_TRAFFIC_DROP",
        severity=_TICKET,
        expression=(
            "sum(rate(http_requests_total[5m])) by (service) "
            "< sum(rate(http_requests_total[5m] offset 1d)) by (service) "
            "* {{traffic_drop_ratio}}"
        ),
        for_seconds=600,  # 10m
        owner="foundation",
        protects="silent routing/ingress failure detection",
        runbook="#http-traffic-drop",
        dedup_by=("service",),
        recovery="traffic back to baseline ratio",
        placeholders=("traffic_drop_ratio",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_READINESS_FAILING",
        severity=_PAGE,
        expression='probe_success{probe="readiness"} == 0',
        for_seconds=120,  # 2m
        owner="foundation",
        protects="traffic-cutover safety invariant",
        runbook="#readiness-failing",
        dedup_by=("service", "instance"),
        recovery="3 consecutive successful probes",
        placeholders=(),
        producer="blackbox_exporter",
    ),
    Alert(
        code="FDN_LIVENESS_FAILING",
        severity=_PAGE,
        expression='probe_success{probe="liveness"} == 0',
        for_seconds=120,  # 2m
        owner="foundation",
        protects="crash-loop prevention",
        runbook="#liveness-failing",
        dedup_by=("service", "instance"),
        recovery="3 consecutive successful probes",
        placeholders=(),
        producer="blackbox_exporter",
    ),
    Alert(
        code="FDN_CONTAINER_RESTART_LOOP",
        severity=_PAGE,
        expression="increase(container_restarts_total[10m]) > {{restart_loop_count}}",
        for_seconds=0,  # 0m
        owner="foundation",
        protects="crash-loop detection",
        runbook="#restart-loop",
        dedup_by=("container",),
        recovery="0 restarts for 10m",
        placeholders=("restart_loop_count",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_CONTAINER_OOM_KILLED",
        severity=_PAGE,
        expression="increase(container_oom_events_total[5m]) > 0",
        for_seconds=0,  # 0m
        owner="foundation",
        protects="memory-limit correctness",
        runbook="#oom-kill",
        dedup_by=("container",),
        recovery="manual ack (limit is a config decision)",
        placeholders=(),
        producer="cadvisor",
    ),
    Alert(
        code="FDN_CONTAINER_EXITED_UNEXPECTEDLY",
        severity=_TICKET,
        expression="container_last_seen < time() - 60 and container_exit_code != 0",
        for_seconds=60,  # 1m
        owner="foundation",
        protects="unplanned-exit detection",
        runbook="#container-exited",
        dedup_by=("container",),
        recovery="container reports running with exit_code absent",
        placeholders=(),
        producer="unbacked",
    ),
    Alert(
        code="FDN_CONTAINER_CPU_SATURATION",
        severity=_TICKET,
        expression=(
            "rate(container_cpu_usage_seconds_total[5m]) / container_spec_cpu_quota "
            "> {{cpu_saturation_pct}}"
        ),
        for_seconds=600,  # 10m
        owner="foundation",
        protects="capacity headroom",
        runbook="#container-cpu-saturation",
        dedup_by=("container",),
        recovery="ratio below threshold for 10m",
        placeholders=("cpu_saturation_pct",),
        producer="cadvisor",
    ),
    Alert(
        code="FDN_CONTAINER_MEMORY_SATURATION",
        severity=_TICKET,
        expression=(
            "container_memory_working_set_bytes / container_spec_memory_limit_bytes "
            "> {{memory_saturation_pct}}"
        ),
        for_seconds=600,  # 10m
        owner="foundation",
        protects="OOM-kill prevention",
        runbook="#container-memory-saturation",
        dedup_by=("container",),
        recovery="ratio below threshold for 10m",
        placeholders=("memory_saturation_pct",),
        producer="cadvisor",
    ),
    Alert(
        code="FDN_HOST_CPU_SATURATION",
        severity=_TICKET,
        expression=(
            '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) by (host) '
            "> {{host_cpu_saturation_pct}}"
        ),
        for_seconds=900,  # 15m
        owner="foundation",
        protects="host capacity headroom",
        runbook="#host-cpu-saturation",
        dedup_by=("host",),
        recovery="ratio below threshold for 15m",
        placeholders=("host_cpu_saturation_pct",),
        producer="node_exporter",
    ),
    Alert(
        code="FDN_HOST_MEMORY_SATURATION",
        severity=_TICKET,
        expression=(
            "1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) "
            "> {{host_mem_saturation_pct}}"
        ),
        for_seconds=600,  # 10m
        owner="foundation",
        protects="host OOM prevention",
        runbook="#host-memory-saturation",
        dedup_by=("host",),
        recovery="ratio below threshold for 10m",
        placeholders=("host_mem_saturation_pct",),
        producer="node_exporter",
    ),
    Alert(
        code="FDN_HOST_DISK_LOW",
        severity=_TICKET,
        expression=(
            "node_filesystem_avail_bytes / node_filesystem_size_bytes < "
            "{{disk_warning_pct}}"
        ),
        for_seconds=900,  # 15m
        owner="foundation",
        protects="disk exhaustion",
        runbook="#disk-low",
        dedup_by=("host", "mount"),
        recovery="free > threshold + hysteresis",
        placeholders=("disk_warning_pct",),
        producer="node_exporter",
    ),
    Alert(
        code="FDN_HOST_DISK_CRITICAL",
        severity=_PAGE,
        expression=(
            "node_filesystem_avail_bytes / node_filesystem_size_bytes < "
            "{{disk_critical_pct}}"
        ),
        for_seconds=300,  # 5m
        owner="foundation",
        protects="disk exhaustion",
        runbook="#disk-low",
        dedup_by=("host", "mount"),
        recovery="free > threshold + hysteresis",
        placeholders=("disk_critical_pct",),
        producer="node_exporter",
    ),
    Alert(
        code="FDN_HOST_INODES_LOW",
        severity=_TICKET,
        expression=(
            "node_filesystem_files_free / node_filesystem_files < {{inode_warning_pct}}"
        ),
        for_seconds=900,  # 15m
        owner="foundation",
        protects="inode exhaustion",
        runbook="#inode-low",
        dedup_by=("host", "mount"),
        recovery="free > threshold",
        placeholders=("inode_warning_pct",),
        producer="node_exporter",
    ),
    Alert(
        code="FDN_HOST_CLOCK_SKEW",
        severity=_TICKET,
        expression="abs(node_timex_offset_seconds) > {{clock_skew_seconds}}",
        for_seconds=600,  # 10m
        owner="foundation",
        protects="audit-timestamp/TLS validation correctness",
        runbook="#clock-drift",
        dedup_by=("host",),
        recovery="offset under threshold",
        placeholders=("clock_skew_seconds",),
        producer="node_exporter",
    ),
    Alert(
        code="FDN_PG_DOWN",
        severity=_PAGE,
        expression="pg_up == 0",
        for_seconds=60,  # 1m
        owner="foundation",
        protects="data-plane availability",
        runbook="#postgres-down",
        dedup_by=("instance",),
        recovery="pg_up == 1 for 2m",
        placeholders=(),
        producer="postgres_exporter",
    ),
    Alert(
        code="FDN_PG_POOL_SATURATION",
        severity=_TICKET,
        expression=(
            "pg_stat_activity_count / pg_settings_max_connections > "
            "{{pool_saturation_pct}}"
        ),
        for_seconds=300,  # 5m
        owner="foundation",
        protects="request-serving capacity",
        runbook="#pg-pool-exhausted",
        dedup_by=("instance",),
        recovery="ratio below 80%",
        placeholders=("pool_saturation_pct",),
        producer="postgres_exporter",
    ),
    Alert(
        code="FDN_PG_LOCK_WAIT_HIGH",
        severity=_TICKET,
        expression="max(pg_locks_wait_seconds) by (instance) > {{lock_wait_seconds}}",
        for_seconds=300,  # 5m
        owner="foundation",
        protects="latency-cascade prevention",
        runbook="#pg-long-lock",
        dedup_by=("instance", "relation"),
        recovery="resolved lock, no wait above threshold",
        placeholders=("lock_wait_seconds",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_PG_LONG_TRANSACTION",
        severity=_TICKET,
        expression=(
            "max(pg_stat_activity_max_tx_duration_seconds) by (instance) "
            "> {{long_transaction_seconds}}"
        ),
        for_seconds=300,  # 5m
        owner="foundation",
        protects="bloat/lock-contention prevention",
        runbook="#pg-long-transaction",
        dedup_by=("instance",),
        recovery="duration under threshold",
        placeholders=("long_transaction_seconds",),
        producer="postgres_exporter",
    ),
    Alert(
        code="FDN_PG_REPLICATION_LAG_HIGH",
        severity=_PAGE,
        expression="pg_replication_lag_seconds > {{replication_lag_seconds}}",
        for_seconds=300,  # 5m
        owner="foundation",
        protects="DR/read-replica correctness",
        runbook="#pg-replication-lag",
        dedup_by=("instance",),
        recovery="lag under threshold",
        placeholders=("replication_lag_seconds",),
        producer="postgres_exporter",
    ),
    Alert(
        code="FDN_PG_DISK_LOW",
        severity=_PAGE,
        expression=(
            'node_filesystem_avail_bytes{mountpoint="{{pg_data_mount}}"} '
            '/ node_filesystem_size_bytes{mountpoint="{{pg_data_mount}}"} '
            "< {{pg_disk_critical_pct}}"
        ),
        for_seconds=600,  # 10m
        owner="foundation",
        protects="data-plane disk exhaustion",
        runbook="#pg-disk-low",
        dedup_by=("instance",),
        recovery="free > threshold",
        placeholders=("pg_data_mount", "pg_disk_critical_pct"),
        producer="node_exporter",
    ),
    Alert(
        code="FDN_PG_MIGRATION_HEAD_DRIFT",
        severity=_PAGE,
        expression="pg_alembic_version_info != on() product_expected_migration_head",
        for_seconds=300,  # 5m
        owner="foundation",
        protects="deploy correctness",
        runbook="#migration-head-drift",
        dedup_by=("instance",),
        recovery="heads match",
        placeholders=(),
        producer="unbacked",
    ),
    Alert(
        code="FDN_REDIS_DOWN",
        severity=_PAGE,
        expression="redis_up == 0",
        for_seconds=60,  # 1m
        owner="foundation",
        protects="cache/queue availability",
        runbook="#redis-down",
        dedup_by=("instance",),
        recovery="redis_up == 1",
        placeholders=(),
        producer="redis_exporter",
    ),
    Alert(
        code="FDN_REDIS_MEMORY_HIGH",
        severity=_TICKET,
        expression=(
            "redis_memory_used_bytes / redis_memory_max_bytes" " > {{redis_memory_pct}}"
        ),
        for_seconds=600,  # 10m
        owner="foundation",
        protects="eviction risk",
        runbook="#redis-mem-pressure",
        dedup_by=("instance",),
        recovery="ratio below threshold",
        placeholders=("redis_memory_pct",),
        producer="redis_exporter",
    ),
    Alert(
        code="FDN_REDIS_PERSISTENCE_STALE",
        severity=_TICKET,
        expression=(
            "time() - redis_rdb_last_save_timestamp_seconds "
            "> {{redis_persistence_stale_seconds}}"
        ),
        for_seconds=600,  # 10m
        owner="foundation",
        protects="data-loss-on-restart exposure",
        runbook="#redis-persistence-stale",
        dedup_by=("instance",),
        recovery="fresh save observed",
        placeholders=("redis_persistence_stale_seconds",),
        producer="redis_exporter",
    ),
    Alert(
        code="FDN_REDIS_QUEUE_DEPTH_HIGH",
        severity=_TICKET,
        expression='redis_queue_depth{queue=~".*"} > {{queue_depth_threshold}}',
        for_seconds=900,  # 15m
        owner="foundation",
        protects="queue backlog visibility",
        runbook="#redis-queue-depth",
        dedup_by=("instance", "queue"),
        recovery="depth under threshold",
        placeholders=("queue_depth_threshold",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_WORKER_BACKLOG_HIGH",
        severity=_TICKET,
        expression='worker_queue_backlog{queue=~".*"} > {{backlog_threshold}}',
        for_seconds=900,  # 15m
        owner="foundation",
        protects="worker throughput SLO",
        runbook="#worker-backlog",
        dedup_by=("service", "queue"),
        recovery="depth under threshold",
        placeholders=("backlog_threshold",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_WORKER_FAILURE_RATE_HIGH",
        severity=_TICKET,
        expression=(
            "sum(rate(worker_task_failures_total[15m])) by (service,task) "
            "/ sum(rate(worker_task_runs_total[15m])) by (service,task) "
            "> {{failure_rate_pct}}"
        ),
        for_seconds=900,  # 15m
        owner="foundation",
        protects="job-success SLO",
        runbook="#worker-failure-rate",
        dedup_by=("service", "task"),
        recovery="ratio under threshold",
        placeholders=("failure_rate_pct",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_WORKER_HEARTBEAT_STALE",
        severity=_PAGE,
        expression=(
            'time() - worker_last_success_timestamp_seconds{task=~".*"} '
            "> {{heartbeat_stale_seconds}}"
        ),
        for_seconds=300,  # 5m
        owner="foundation",
        protects="dead-man for scheduled work",
        runbook="#worker-heartbeat-stale",
        dedup_by=("service", "task"),
        recovery="fresh success observed",
        placeholders=("heartbeat_stale_seconds",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_WORKER_POISON_JOBS_DETECTED",
        severity=_TICKET,
        expression="increase(worker_task_poison_total[30m]) > {{poison_job_count}}",
        for_seconds=600,  # 10m
        owner="foundation",
        protects="queue-blocking prevention",
        runbook="#worker-poison-job",
        dedup_by=("service", "task"),
        recovery="0 increase for 30m",
        placeholders=("poison_job_count",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_SCHEDULER_TICK_STALE",
        severity=_PAGE,
        expression=(
            'time() - scheduler_last_tick_timestamp_seconds{job=~".*"} '
            "> {{scheduler_tick_stale_seconds}}"
        ),
        for_seconds=600,  # 10m
        owner="foundation",
        protects="scheduled-job liveness",
        runbook="#scheduler-tick-stale",
        dedup_by=("service", "job"),
        recovery="fresh tick observed",
        placeholders=("scheduler_tick_stale_seconds",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_OUTBOX_BACKLOG_HIGH",
        severity=_TICKET,
        expression='outbox_queue_depth{state="pending"} > {{outbox_backlog_threshold}}',
        for_seconds=900,  # 15m
        owner="foundation",
        protects="eventual-delivery SLO",
        runbook="#outbox-backlog",
        dedup_by=("service", "outbox"),
        recovery="depth under threshold",
        placeholders=("outbox_backlog_threshold",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_OUTBOX_RETRY_AGE_HIGH",
        severity=_TICKET,
        expression="outbox_oldest_pending_age_seconds > {{outbox_retry_age_seconds}}",
        for_seconds=900,  # 15m
        owner="foundation",
        protects="delivery-latency SLO (age hides behind depth)",
        runbook="#outbox-retry-age",
        dedup_by=("service", "outbox"),
        recovery="age under threshold",
        placeholders=("outbox_retry_age_seconds",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_OUTBOX_DEAD_LETTERS_PRESENT",
        severity=_TICKET,
        expression="outbox_dead_letter_count > 0",
        for_seconds=1800,  # 30m
        owner="foundation",
        protects="no-silent-data-loss invariant",
        runbook="#outbox-dead-letters",
        dedup_by=("service", "outbox"),
        recovery="count back to 0 or acked",
        placeholders=(),
        producer="unbacked",
    ),
    Alert(
        code="FDN_INBOX_BACKLOG_HIGH",
        severity=_TICKET,
        expression=(
            'inbox_queue_depth{state="unprocessed"}' " > {{inbox_backlog_threshold}}"
        ),
        for_seconds=900,  # 15m
        owner="foundation",
        protects="inbound-processing SLO",
        runbook="#inbox-backlog",
        dedup_by=("service", "inbox"),
        recovery="depth under threshold",
        placeholders=("inbox_backlog_threshold",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_INBOX_RETRY_AGE_HIGH",
        severity=_TICKET,
        expression="inbox_oldest_unprocessed_age_seconds > {{inbox_retry_age_seconds}}",
        for_seconds=900,  # 15m
        owner="foundation",
        protects="inbound-processing latency",
        runbook="#inbox-retry-age",
        dedup_by=("service", "inbox"),
        recovery="age under threshold",
        placeholders=("inbox_retry_age_seconds",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_INBOX_DEAD_LETTERS_PRESENT",
        severity=_TICKET,
        expression="inbox_dead_letter_count > 0",
        for_seconds=1800,  # 30m
        owner="foundation",
        protects="no-silent-data-loss invariant",
        runbook="#inbox-dead-letters",
        dedup_by=("service", "inbox"),
        recovery="count back to 0 or acked",
        placeholders=(),
        producer="unbacked",
    ),
    Alert(
        code="FDN_INGRESS_4XX_RATE_HIGH",
        severity=_TICKET,
        expression=(
            'sum(rate(ingress_requests_total{status=~"4.."}[10m])) by (vhost) '
            "/ sum(rate(ingress_requests_total[10m])) by (vhost) "
            "> {{ingress_4xx_rate_pct}}"
        ),
        for_seconds=900,  # 15m
        owner="foundation",
        protects="client-error surge / probing detection",
        runbook="#ingress-4xx",
        dedup_by=("vhost",),
        recovery="ratio under threshold",
        placeholders=("ingress_4xx_rate_pct",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_INGRESS_5XX_RATE_HIGH",
        severity=_PAGE,
        expression=(
            'sum(rate(ingress_requests_total{status=~"5.."}[5m])) by (vhost) '
            "/ sum(rate(ingress_requests_total[5m])) by (vhost) "
            "> {{ingress_5xx_rate_pct}}"
        ),
        for_seconds=300,  # 5m
        owner="foundation",
        protects="edge availability SLO",
        runbook="#ingress-5xx",
        dedup_by=("vhost",),
        recovery="ratio under threshold",
        placeholders=("ingress_5xx_rate_pct",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_INGRESS_UPSTREAM_LATENCY_HIGH",
        severity=_TICKET,
        expression=(
            "histogram_quantile(0.99, "
            "sum(rate(ingress_upstream_response_seconds_bucket[5m])) "
            "by (le,vhost)) > {{upstream_latency_seconds}}"
        ),
        for_seconds=600,  # 10m
        owner="foundation",
        protects="upstream-latency SLO",
        runbook="#ingress-upstream-latency",
        dedup_by=("vhost",),
        recovery="p99 under threshold",
        placeholders=("upstream_latency_seconds",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_TLS_CERT_EXPIRY_WARNING",
        severity=_TICKET,
        expression=(
            "(probe_ssl_earliest_cert_expiry - time()) / 86400"
            " < {{tls_expiry_warning_days}}"
        ),
        for_seconds=3600,  # 1h
        owner="foundation",
        protects="outage-by-expired-cert prevention",
        runbook="#tls-expiry",
        dedup_by=("hostname",),
        recovery="renewed cert observed",
        placeholders=("tls_expiry_warning_days",),
        producer="blackbox_exporter",
    ),
    Alert(
        code="FDN_TLS_CERT_EXPIRY_CRITICAL",
        severity=_PAGE,
        expression=(
            "(probe_ssl_earliest_cert_expiry - time()) / 86400"
            " < {{tls_expiry_critical_days}}"
        ),
        for_seconds=3600,  # 1h
        owner="foundation",
        protects="outage-by-expired-cert prevention",
        runbook="#tls-expiry",
        dedup_by=("hostname",),
        recovery="renewed cert observed",
        placeholders=("tls_expiry_critical_days",),
        producer="blackbox_exporter",
    ),
    Alert(
        code="FDN_EXTERNAL_DEP_LATENCY_HIGH",
        severity=_TICKET,
        expression=(
            "histogram_quantile(0.95, "
            "sum(rate(external_dependency_request_duration_seconds_bucket[10m])) "
            "by (le,service,dependency)) > {{external_dep_latency_seconds}}"
        ),
        for_seconds=900,  # 15m
        owner="foundation",
        protects="external-call latency SLO",
        runbook="#external-dep-latency",
        dedup_by=("service", "dependency"),
        recovery="p95 under threshold",
        placeholders=("external_dep_latency_seconds",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_EXTERNAL_DEP_ERROR_RATE_HIGH",
        severity=_TICKET,
        expression=(
            'sum(rate(external_dependency_requests_total{outcome="error"}[10m])) '
            "by (service,dependency) "
            "/ sum(rate(external_dependency_requests_total[10m])) by "
            "(service,dependency) "
            "> {{external_dep_error_rate_pct}}"
        ),
        for_seconds=900,  # 15m
        owner="foundation",
        protects="external-call error SLO",
        runbook="#external-dep-errors",
        dedup_by=("service", "dependency"),
        recovery="ratio under threshold",
        placeholders=("external_dep_error_rate_pct",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_EXTERNAL_DEP_BREAKER_OPEN",
        severity=_PAGE,
        expression='external_dependency_circuit_open{dependency=~".*"} == 1',
        for_seconds=300,  # 5m
        owner="foundation",
        protects="cascading-failure prevention",
        runbook="#external-dep-breaker-open",
        dedup_by=("service", "dependency"),
        recovery="circuit closed for 5m",
        placeholders=(),
        producer="unbacked",
    ),
    Alert(
        code="FDN_SECRET_REFRESH_FAILED",
        severity=_PAGE,
        expression=(
            'time() - secret_refresh_last_success_timestamp_seconds{secret=~".*"} '
            "> 2 * {{secret_refresh_interval_seconds}}"
        ),
        for_seconds=300,  # 5m
        owner="foundation",
        protects="rotation correctness (ADR-0009 boundary)",
        runbook="#secret-refresh-failed",
        # (name only, never value) — the label carries the material's NAME,
        # exactly as ADR-0009 requires; the alert must never carry the value.
        dedup_by=("service", "secret_name"),
        recovery="successful refresh observed",
        placeholders=("secret_refresh_interval_seconds",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_SECRET_MATERIAL_EXPIRING",
        severity=_TICKET,
        expression=(
            '(secret_material_expiry_timestamp_seconds{secret=~".*"} - time()) / 86400 '
            "< {{secret_expiry_warning_days}}"
        ),
        for_seconds=3600,  # 1h
        owner="foundation",
        protects="rotation lead time",
        runbook="#secret-expiring",
        # (name only, never value) — see FDN_SECRET_REFRESH_FAILED above.
        dedup_by=("service", "secret_name"),
        recovery="new material installed",
        placeholders=("secret_expiry_warning_days",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_OBJSTORE_FAILURE_RATE_HIGH",
        severity=_TICKET,
        expression=(
            'sum(rate(objstore_requests_total{outcome="error"}[10m])) by (bucket) '
            "/ sum(rate(objstore_requests_total[10m])) by (bucket) "
            "> {{objstore_error_rate_pct}}"
        ),
        for_seconds=900,  # 15m
        owner="foundation",
        protects="storage-dependent-feature SLO",
        runbook="#objstore-failures",
        dedup_by=("bucket",),
        recovery="ratio under threshold",
        placeholders=("objstore_error_rate_pct",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_OBJSTORE_CAPACITY_HIGH",
        severity=_TICKET,
        expression=(
            'objstore_used_bytes{bucket=~".*"} / objstore_quota_bytes{bucket=~".*"} '
            "> {{objstore_capacity_pct}}"
        ),
        for_seconds=3600,  # 1h
        owner="foundation",
        protects="capacity exhaustion",
        runbook="#objstore-capacity",
        dedup_by=("bucket",),
        recovery="ratio under threshold",
        placeholders=("objstore_capacity_pct",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_BACKUP_AGE_STALE",
        severity=_PAGE,
        expression=(
            'time() - backup_last_success_timestamp_seconds{database=~".*"} '
            "> {{backup_interval_seconds}} * {{backup_age_multiplier}}"
        ),
        for_seconds=900,  # 15m
        owner="foundation",
        protects="RPO",
        runbook="#backup-stale",
        dedup_by=("database",),
        recovery="fresh success observed",
        placeholders=("backup_interval_seconds", "backup_age_multiplier"),
        producer="unbacked",
    ),
    Alert(
        code="FDN_BACKUP_JOB_FAILED",
        severity=_PAGE,
        expression='increase(backup_job_failures_total{database=~".*"}[1d]) > 0',
        for_seconds=0,  # 0m
        owner="foundation",
        protects="RPO",
        runbook="#backup-failed",
        dedup_by=("database", "run_id"),
        recovery="next run succeeds",
        placeholders=(),
        producer="unbacked",
    ),
    Alert(
        code="FDN_RESTORE_NOT_VERIFIED",
        severity=_TICKET,
        expression=(
            'time() - backup_last_restore_verified_timestamp_seconds{database=~".*"} '
            "> {{restore_verify_max_age_days}} * 86400"
        ),
        for_seconds=86400,  # 1d
        owner="foundation",
        protects="RTO confidence",
        runbook="#restore-not-verified",
        dedup_by=("database",),
        recovery="drill recorded",
        placeholders=("restore_verify_max_age_days",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_IMAGE_DRIFT_DETECTED",
        severity=_TICKET,
        expression=(
            'deployed_image_digest{service=~".*"}'
            " != on(service) desired_image_digest"
        ),
        for_seconds=300,  # 5m
        owner="foundation",
        protects="supply-chain/deploy correctness",
        runbook="#image-drift",
        dedup_by=("service", "instance"),
        recovery="digests match",
        placeholders=(),
        producer="unbacked",
    ),
    Alert(
        code="FDN_CONFIG_DRIFT_DETECTED",
        severity=_TICKET,
        expression=(
            'deployed_config_digest{service=~".*"}'
            " != on(service) desired_config_digest"
        ),
        for_seconds=300,  # 5m
        owner="foundation",
        protects="config correctness",
        runbook="#config-drift",
        dedup_by=("service", "instance"),
        recovery="digests match",
        placeholders=(),
        producer="unbacked",
    ),
    Alert(
        code="FDN_MANIFEST_DRIFT_DETECTED",
        severity=_TICKET,
        expression=(
            'deployed_manifest_digest{product=~".*"}'
            " != on(product) approved_manifest_digest"
        ),
        for_seconds=300,  # 5m
        owner="foundation",
        protects="approved-plan conformance",
        runbook="#manifest-drift",
        dedup_by=("product",),
        recovery="digests match",
        placeholders=(),
        producer="unbacked",
    ),
    Alert(
        code="FDN_SYNTHETIC_HEALTH_FAILING",
        severity=_PAGE,
        expression='probe_success{probe="synthetic_health",product=~".*"} == 0',
        for_seconds=300,  # 5m
        owner="foundation",
        protects="end-to-end reachability",
        runbook="#synthetic-health-fail",
        dedup_by=("product",),
        recovery="3 consecutive successes",
        placeholders=(),
        producer="blackbox_exporter",
    ),
    Alert(
        code="FDN_SYNTHETIC_JOURNEY_FAILING",
        severity=_PAGE,
        expression=(
            'increase(synthetic_journey_failures_total{journey=~".*"}[15m]) '
            ">= {{synthetic_journey_failures}}"
        ),
        for_seconds=0,  # 0m
        owner="foundation",
        protects="end-to-end customer-facing correctness",
        runbook="#synthetic-journey-fail",
        dedup_by=("product", "journey"),
        recovery="1 success observed",
        placeholders=("synthetic_journey_failures",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_SCRAPE_TARGET_DOWN",
        severity=_PAGE,
        expression='up{job=~".*"} == 0',
        for_seconds=300,  # 5m
        owner="foundation",
        protects="telemetry pipeline (scrape) health",
        runbook="#scrape-target-down",
        dedup_by=("instance",),
        recovery="up == 1",
        placeholders=(),
        producer="unbacked",
    ),
    Alert(
        code="FDN_REMOTE_WRITE_FAILING",
        severity=_PAGE,
        expression="rate(prometheus_remote_storage_failed_samples_total[10m]) > 0",
        for_seconds=600,  # 10m
        owner="foundation",
        protects="metrics durability",
        runbook="#remote-write-failing",
        dedup_by=("instance",),
        recovery="0 failures for 10m",
        placeholders=(),
        producer="unbacked",
    ),
    Alert(
        code="FDN_LOG_INGESTION_GAP",
        severity=_PAGE,
        expression=(
            'absent_over_time(log_lines_received_total{instance=~"{{known_live_instances}}"}[15m])'
        ),
        for_seconds=900,  # 15m
        owner="foundation",
        protects="log-pipeline dead-man",
        runbook="#log-ingest-gap",
        dedup_by=("instance",),
        recovery="logs resume",
        placeholders=("known_live_instances",),
        producer="unbacked",
    ),
    Alert(
        code="FDN_TRACE_EXPORT_FAILING",
        severity=_TICKET,
        expression="rate(otel_exporter_send_failed_spans_total[15m]) > 0",
        for_seconds=900,  # 15m
        owner="foundation",
        protects="trace completeness",
        runbook="#trace-export-failing",
        dedup_by=("instance",),
        recovery="0 failures for 15m",
        placeholders=(),
        producer="unbacked",
    ),
    Alert(
        code="FDN_TELEMETRY_DEADMAN",
        severity=_PAGE,
        expression="absent(deadman_heartbeat_total)",
        for_seconds=300,  # 5m
        owner="foundation",
        protects='pipeline-is-alive invariant, distinct from "everything is quiet"',
        runbook="#deadman-silent",
        dedup_by=("fleet",),
        recovery="heartbeat resumes",
        placeholders=(),
        producer="unbacked",
    ),
)


# ── producer classification ─────────────────────────────────────────────────
#
# `_METRIC_PRODUCERS` is an INDEPENDENTLY researched ground truth — "what
# software, if any, really emits a metric with exactly this name" — kept
# deliberately separate from the `producer=` field hand-typed onto each
# `Alert` above. The two are supposed to agree; `producer_consistency_errors`
# is the check that they do, which is the whole point (ADR-0018): a
# hand-typed field that is only ever compared to itself proves nothing.
#
# Sources for the "real" side of this table (2026-08-26 research, recorded so
# the reasoning is not lost): node_exporter's `node_cpu_seconds_total` /
# `node_memory_Mem{Available,Total}_bytes` / `node_filesystem_*` /
# `node_timex_offset_seconds` are its stock metrics. cAdvisor's
# `container_cpu_usage_seconds_total`, `container_memory_working_set_bytes`,
# `container_spec_{cpu_quota,memory_limit_bytes}`, `container_last_seen` and
# `container_oom_events_total` (v0.40+) are stock; `container_restarts_total`
# and `container_exit_code` are NOT — cAdvisor tracks neither a per-container
# restart counter nor an exit code (the closest real thing,
# `kube_pod_container_status_restarts_total`, is Kubernetes-only and this
# fleet runs Compose hosts). postgres_exporter's `pg_up`,
# `pg_stat_activity_count`, `pg_settings_max_connections`,
# `pg_stat_activity_max_tx_duration_seconds` and `pg_replication_lag_seconds`
# are real (stock or its documented custom-query mechanism);
# `pg_locks_wait_seconds` is not (`pg_locks_count` — a count by lock MODE —
# is the real metric; nothing exposes a wait-DURATION gauge). redis_exporter's
# `redis_up`, `redis_memory_{used,max}_bytes` and
# `redis_rdb_last_save_timestamp_seconds` are stock; `redis_queue_depth` is
# not (redis_exporter has no concept of an application "queue" — depth would
# need either app-level instrumentation or `--check-keys` against real key
# names, producing `redis_key_size`, not this name). blackbox_exporter's
# `probe_success` and `probe_ssl_earliest_cert_expiry` are stock.
#
# Every other metric name in the catalogue needs a DOTMAC process that does
# not exist today: no HTTP-metrics middleware is wired into `dotmac_kernel`
# (no `prometheus_client`/OTel metrics import anywhere in it), no worker /
# outbox / inbox / scheduler / secret-refresh / object-store / external-
# dependency / synthetic-journey instrumentation exists anywhere in the
# fleet, `backup.py` and `drift.py` in THIS package compute their answers
# in-process and write no metric, and this deployment's own rendered
# collector config (`telemetry.render_collector_config`) has only an `otlp`
# receiver — no `prometheus` receiver and no remote_write exporter — so
# `up{job=~".*"}` and `prometheus_remote_storage_failed_samples_total`
# describe a pull-based Prometheus topology this facility does not build,
# and `otel_exporter_send_failed_spans_total` does not even match the
# collector's real self-telemetry name (`otelcol_exporter_send_failed_spans`
# — no `_total` suffix, and missing the `col`). See the inventory doc for the
# full alert-by-alert accounting.
_METRIC_PRODUCERS: Final[Mapping[str, str]] = {
    # node_exporter — stock metrics.
    "node_cpu_seconds_total": "node_exporter",
    "node_memory_MemAvailable_bytes": "node_exporter",
    "node_memory_MemTotal_bytes": "node_exporter",
    "node_filesystem_avail_bytes": "node_exporter",
    "node_filesystem_size_bytes": "node_exporter",
    "node_filesystem_files_free": "node_exporter",
    "node_filesystem_files": "node_exporter",
    "node_timex_offset_seconds": "node_exporter",
    # cadvisor — stock metrics.
    "container_cpu_usage_seconds_total": "cadvisor",
    "container_spec_cpu_quota": "cadvisor",
    "container_memory_working_set_bytes": "cadvisor",
    "container_spec_memory_limit_bytes": "cadvisor",
    "container_oom_events_total": "cadvisor",
    "container_last_seen": "cadvisor",
    # cadvisor-shaped names that are NOT real cAdvisor metrics.
    "container_restarts_total": UNBACKED,
    "container_exit_code": UNBACKED,
    # postgres_exporter — stock or its documented custom-query mechanism.
    "pg_up": "postgres_exporter",
    "pg_stat_activity_count": "postgres_exporter",
    "pg_settings_max_connections": "postgres_exporter",
    "pg_stat_activity_max_tx_duration_seconds": "postgres_exporter",
    "pg_replication_lag_seconds": "postgres_exporter",
    # pg_*-shaped names that are NOT real postgres_exporter metrics, or that
    # name a fact no exporter can observe (the deployment's APPROVED state).
    "pg_locks_wait_seconds": UNBACKED,
    "pg_alembic_version_info": UNBACKED,
    "product_expected_migration_head": UNBACKED,
    # redis_exporter — stock metrics.
    "redis_up": "redis_exporter",
    "redis_memory_used_bytes": "redis_exporter",
    "redis_memory_max_bytes": "redis_exporter",
    "redis_rdb_last_save_timestamp_seconds": "redis_exporter",
    "redis_queue_depth": UNBACKED,
    # blackbox_exporter — stock metrics.
    "probe_success": "blackbox_exporter",
    "probe_ssl_earliest_cert_expiry": "blackbox_exporter",
    # Needs the product's own /metrics endpoint; none exists today.
    "http_requests_total": UNBACKED,
    "http_request_duration_seconds_bucket": UNBACKED,
    "worker_queue_backlog": UNBACKED,
    "worker_task_failures_total": UNBACKED,
    "worker_task_runs_total": UNBACKED,
    "worker_last_success_timestamp_seconds": UNBACKED,
    "worker_task_poison_total": UNBACKED,
    "outbox_queue_depth": UNBACKED,
    "outbox_oldest_pending_age_seconds": UNBACKED,
    "outbox_dead_letter_count": UNBACKED,
    "inbox_queue_depth": UNBACKED,
    "inbox_oldest_unprocessed_age_seconds": UNBACKED,
    "inbox_dead_letter_count": UNBACKED,
    "external_dependency_request_duration_seconds_bucket": UNBACKED,
    "external_dependency_requests_total": UNBACKED,
    "external_dependency_circuit_open": UNBACKED,
    "secret_refresh_last_success_timestamp_seconds": UNBACKED,
    "secret_material_expiry_timestamp_seconds": UNBACKED,
    "objstore_requests_total": UNBACKED,
    "objstore_used_bytes": UNBACKED,
    "objstore_quota_bytes": UNBACKED,
    "synthetic_journey_failures_total": UNBACKED,
    # Needs the ingress provider's own telemetry; none is configured today.
    "ingress_requests_total": UNBACKED,
    "ingress_upstream_response_seconds_bucket": UNBACKED,
    # Needs this facility (backup.py / drift.py / the migration verifier) to
    # write a metric where today it only computes an in-process answer.
    "backup_last_success_timestamp_seconds": UNBACKED,
    "backup_job_failures_total": UNBACKED,
    "backup_last_restore_verified_timestamp_seconds": UNBACKED,
    "deployed_image_digest": UNBACKED,
    "desired_image_digest": UNBACKED,
    "deployed_config_digest": UNBACKED,
    "desired_config_digest": UNBACKED,
    "deployed_manifest_digest": UNBACKED,
    "approved_manifest_digest": UNBACKED,
    "deadman_heartbeat_total": UNBACKED,
    # Assume a pull-based Prometheus or a mismatched collector metric name;
    # this pipeline is OTLP-only end to end. See the block comment above.
    "up": UNBACKED,
    "prometheus_remote_storage_failed_samples_total": UNBACKED,
    "log_lines_received_total": UNBACKED,
    "otel_exporter_send_failed_spans_total": UNBACKED,
}


def _expected_producer(names: frozenset[str]) -> str:
    """What `_METRIC_PRODUCERS` implies the producer should be.

    `UNBACKED` when `names` is empty, when any name is unknown to
    `_METRIC_PRODUCERS`, or when the known names disagree with each other —
    an alert is only as reliable as the least-backed metric it reads, so a
    two-metric expression naming one `node_exporter` metric and one metric
    nothing emits is honestly `UNBACKED` as a whole, not `node_exporter`.
    """
    if not names:
        return UNBACKED
    implied = {_METRIC_PRODUCERS.get(name, UNBACKED) for name in names}
    if len(implied) == 1:
        return next(iter(implied))
    return UNBACKED


def producer_consistency_errors(
    alerts: Iterable[Alert] = COMMON_ALERTS,
) -> tuple[str, ...]:
    """Alert codes whose declared `producer` disagrees with what
    `_METRIC_PRODUCERS` implies from the metric names in `expression`.

    This is the ADR-0018 sensitivity proof made callable: `Alert.producer` is
    a hand-typed field like `owner` or `runbook`, and a hand-typed field can
    be wrong in either direction — a real producer declared for a metric
    nothing emits, or `UNBACKED` declared for a metric this module's own
    research says IS backed. Both are reported; sorted for a stable diff.
    """
    mismatches = [
        alert.code
        for alert in alerts
        if _expected_producer(metric_names_in(alert.expression)) != alert.producer
    ]
    return tuple(sorted(mismatches))


# The exact set of alert codes currently `producer="unbacked"`. This may only
# SHRINK — an alert leaving it means a real producer was built and wired for
# it, which is a diff a reviewer reads (see the module docstring's framing:
# an alert on a metric with no producer is a decoration reporting coverage it
# does not have). `test_unbacked_alerts_matches_the_catalogue_exactly` fails
# in EITHER direction: a code silently becoming unbacked with no update here,
# or a code fixed here without the underlying alert actually gaining a
# producer.
UNBACKED_ALERTS: Final[frozenset[str]] = frozenset(
    {
        "FDN_HTTP_5XX_RATE_HIGH",
        "FDN_HTTP_5XX_RATE_CRITICAL",
        "FDN_HTTP_LATENCY_P99_HIGH",
        "FDN_HTTP_TRAFFIC_DROP",
        "FDN_CONTAINER_RESTART_LOOP",
        "FDN_CONTAINER_EXITED_UNEXPECTEDLY",
        "FDN_PG_LOCK_WAIT_HIGH",
        "FDN_PG_MIGRATION_HEAD_DRIFT",
        "FDN_REDIS_QUEUE_DEPTH_HIGH",
        "FDN_WORKER_BACKLOG_HIGH",
        "FDN_WORKER_FAILURE_RATE_HIGH",
        "FDN_WORKER_HEARTBEAT_STALE",
        "FDN_WORKER_POISON_JOBS_DETECTED",
        "FDN_SCHEDULER_TICK_STALE",
        "FDN_OUTBOX_BACKLOG_HIGH",
        "FDN_OUTBOX_RETRY_AGE_HIGH",
        "FDN_OUTBOX_DEAD_LETTERS_PRESENT",
        "FDN_INBOX_BACKLOG_HIGH",
        "FDN_INBOX_RETRY_AGE_HIGH",
        "FDN_INBOX_DEAD_LETTERS_PRESENT",
        "FDN_INGRESS_4XX_RATE_HIGH",
        "FDN_INGRESS_5XX_RATE_HIGH",
        "FDN_INGRESS_UPSTREAM_LATENCY_HIGH",
        "FDN_EXTERNAL_DEP_LATENCY_HIGH",
        "FDN_EXTERNAL_DEP_ERROR_RATE_HIGH",
        "FDN_EXTERNAL_DEP_BREAKER_OPEN",
        "FDN_SECRET_REFRESH_FAILED",
        "FDN_SECRET_MATERIAL_EXPIRING",
        "FDN_OBJSTORE_FAILURE_RATE_HIGH",
        "FDN_OBJSTORE_CAPACITY_HIGH",
        "FDN_BACKUP_AGE_STALE",
        "FDN_BACKUP_JOB_FAILED",
        "FDN_RESTORE_NOT_VERIFIED",
        "FDN_IMAGE_DRIFT_DETECTED",
        "FDN_CONFIG_DRIFT_DETECTED",
        "FDN_MANIFEST_DRIFT_DETECTED",
        "FDN_SYNTHETIC_JOURNEY_FAILING",
        "FDN_SCRAPE_TARGET_DOWN",
        "FDN_REMOTE_WRITE_FAILING",
        "FDN_LOG_INGESTION_GAP",
        "FDN_TRACE_EXPORT_FAILING",
        "FDN_TELEMETRY_DEADMAN",
    }
)


# ── placeholder classification ──────────────────────────────────────────────

# Every distinct placeholder name used anywhere in `COMMON_ALERTS`, mapped to
# whether the facility supplies a default or the product must. See the module
# docstring, "Placeholders: a `product-threshold` with no value is a refusal".
#: The two words `PLACEHOLDER_SOURCES` maps to. Named constants rather than
#: forty repeated literals, so bandit's password heuristic has ONE line to be
#: told about instead of forty — and so a typo in one entry is a NameError
#: rather than a silently unrecognised source.
_FOUNDATION_DEFAULT: Final = "foundation-default"
_PRODUCT_THRESHOLD: Final = "product-threshold"  # nosec B105

PLACEHOLDER_SOURCES: Final[Mapping[str, str]] = {
    "traffic_drop_ratio": _FOUNDATION_DEFAULT,
    "restart_loop_count": _FOUNDATION_DEFAULT,
    "cpu_saturation_pct": _FOUNDATION_DEFAULT,
    "memory_saturation_pct": _FOUNDATION_DEFAULT,
    "host_cpu_saturation_pct": _FOUNDATION_DEFAULT,
    "host_mem_saturation_pct": _FOUNDATION_DEFAULT,
    "disk_warning_pct": _FOUNDATION_DEFAULT,
    "disk_critical_pct": _FOUNDATION_DEFAULT,
    "inode_warning_pct": _FOUNDATION_DEFAULT,
    "clock_skew_seconds": _FOUNDATION_DEFAULT,
    "tls_expiry_warning_days": _FOUNDATION_DEFAULT,
    "tls_expiry_critical_days": _FOUNDATION_DEFAULT,
    "known_live_instances": _FOUNDATION_DEFAULT,
    "error_rate_warning_pct": _PRODUCT_THRESHOLD,
    "error_rate_critical_pct": _PRODUCT_THRESHOLD,
    "latency_p99_warning_seconds": _PRODUCT_THRESHOLD,
    "pool_saturation_pct": _PRODUCT_THRESHOLD,
    "lock_wait_seconds": _PRODUCT_THRESHOLD,
    "long_transaction_seconds": _PRODUCT_THRESHOLD,
    "replication_lag_seconds": _PRODUCT_THRESHOLD,
    "pg_data_mount": _PRODUCT_THRESHOLD,
    "pg_disk_critical_pct": _PRODUCT_THRESHOLD,
    "redis_memory_pct": _PRODUCT_THRESHOLD,
    "redis_persistence_stale_seconds": _PRODUCT_THRESHOLD,
    "queue_depth_threshold": _PRODUCT_THRESHOLD,
    "backlog_threshold": _PRODUCT_THRESHOLD,
    "failure_rate_pct": _PRODUCT_THRESHOLD,
    "heartbeat_stale_seconds": _PRODUCT_THRESHOLD,
    "poison_job_count": _PRODUCT_THRESHOLD,
    "scheduler_tick_stale_seconds": _PRODUCT_THRESHOLD,
    "outbox_backlog_threshold": _PRODUCT_THRESHOLD,
    "outbox_retry_age_seconds": _PRODUCT_THRESHOLD,
    "inbox_backlog_threshold": _PRODUCT_THRESHOLD,
    "inbox_retry_age_seconds": _PRODUCT_THRESHOLD,
    "ingress_4xx_rate_pct": _PRODUCT_THRESHOLD,
    "ingress_5xx_rate_pct": _PRODUCT_THRESHOLD,
    "upstream_latency_seconds": _PRODUCT_THRESHOLD,
    "external_dep_latency_seconds": _PRODUCT_THRESHOLD,
    "external_dep_error_rate_pct": _PRODUCT_THRESHOLD,
    "secret_refresh_interval_seconds": _PRODUCT_THRESHOLD,
    "secret_expiry_warning_days": _PRODUCT_THRESHOLD,
    "objstore_error_rate_pct": _PRODUCT_THRESHOLD,
    "objstore_capacity_pct": _PRODUCT_THRESHOLD,
    "backup_interval_seconds": _PRODUCT_THRESHOLD,
    "backup_age_multiplier": _PRODUCT_THRESHOLD,
    "restore_verify_max_age_days": _PRODUCT_THRESHOLD,
    "synthetic_journey_failures": _PRODUCT_THRESHOLD,
}

# Default VALUES for the `foundation-default` placeholders only. A
# `product-threshold` name intentionally has no entry here — see the module
# docstring for why a missing one is a refusal rather than a guess.
FOUNDATION_DEFAULTS: Final[Mapping[str, str]] = {
    "traffic_drop_ratio": "0.5",
    "restart_loop_count": "3",
    "cpu_saturation_pct": "0.85",
    "memory_saturation_pct": "0.85",
    "host_cpu_saturation_pct": "0.90",
    "host_mem_saturation_pct": "0.90",
    "disk_warning_pct": "0.15",
    "disk_critical_pct": "0.05",
    "inode_warning_pct": "0.15",
    "clock_skew_seconds": "0.5",
    "tls_expiry_warning_days": "21",
    "tls_expiry_critical_days": "7",
    "known_live_instances": ".+",
}


def _resolve_value(name: str, thresholds: Mapping[str, str]) -> str | None:
    """The value a placeholder resolves to, or `None` if unresolved.

    `thresholds` always wins when the product supplies a value — including
    for a `foundation-default` name, which a product is free to override —
    and `FOUNDATION_DEFAULTS` is the fallback only for the names it declares.
    """
    if name in thresholds:
        return thresholds[name]
    return FOUNDATION_DEFAULTS.get(name)


def unresolved_placeholders(
    spec: ProductDeploymentSpec, thresholds: Mapping[str, str]
) -> tuple[str, ...]:
    """Every placeholder — foundation or product — with no resolvable value.

    Sorted and de-duplicated, so a caller (the `dotmac-deploy` CLI and the
    conformance kit both call this) can report the full gap in one message
    before ever attempting `render_alert_rules`, which raises on exactly this
    same set.
    """
    names: set[str] = set()
    for alert in COMMON_ALERTS:
        names.update(alert.placeholders)
    for product_alert in spec.product_alerts:
        names.update(_placeholders_in(product_alert.expression))
    return tuple(
        sorted(name for name in names if _resolve_value(name, thresholds) is None)
    )


def _render_expression(expression: str, thresholds: Mapping[str, str]) -> str:
    """Substitute every `{{name}}` token in `expression` with its resolved value.

    Callable only after `unresolved_placeholders` has been checked empty —
    `render_alert_rules` enforces that ordering, so a token reaching this
    function with no resolution would be a defect in that caller, not a
    legitimate runtime state.
    """

    def _substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        value = _resolve_value(name, thresholds)
        if value is None:
            raise SpecError(
                f"placeholder {name!r} has no resolved value; "
                "unresolved_placeholders should have been checked first"
            )
        return value

    return _PLACEHOLDER.sub(_substitute, expression)


# ── rendering ────────────────────────────────────────────────────────────────


def _quote(value: str) -> str:
    """A JSON string literal, which is always a valid YAML flow scalar.

    Reused instead of a hand-rolled quoting table (contrast `render/compose.py`)
    because every value rendered here — a PromQL expression, a runbook slug, a
    free-text recovery sentence — is far more likely to contain YAML-special
    characters (`{`, `}`, `"`, `:`) than a compose field is, and `json.dumps`
    already produces a byte-stable, correctly escaped result for any string.
    """
    return json.dumps(value)


def _emit_rule(
    lines: list[str],
    *,
    code: str,
    severity: str,
    expression: str,
    for_seconds: int,
    owner: str,
    product: str,
    summary: str,
    runbook: str,
    recovery: str,
    protects: str,
    dedup_by: tuple[str, ...],
    thresholds: Mapping[str, str],
    unbacked: bool = False,
) -> None:
    if unbacked:
        lines.append(
            "      # UNBACKED: no producer emits the metric(s) this "
            "expression reads today. See "
            "docs/inventories/deployment-foundation-alert-producers.md."
        )
    lines.append(f"      - alert: {_quote(code)}")
    lines.append(f"        expr: {_quote(_render_expression(expression, thresholds))}")
    lines.append(f"        for: {_quote(f'{for_seconds}s')}")
    lines.append("        labels:")
    lines.append(f"          severity: {_quote(severity)}")
    lines.append(f"          owner: {_quote(owner)}")
    lines.append(f"          product: {_quote(product)}")
    if unbacked:
        lines.append(f"          dotmac_unbacked: {_quote('true')}")
    lines.append("        annotations:")
    lines.append(f"          summary: {_quote(summary)}")
    lines.append(f"          runbook: {_quote(runbook)}")
    lines.append(f"          recovery: {_quote(recovery)}")
    lines.append(f"          protects: {_quote(protects)}")
    lines.append(f"          dedup_by: {_quote(','.join(dedup_by))}")


def _emit_foundation_group(
    lines: list[str],
    *,
    product: str,
    thresholds: Mapping[str, str],
    include_unbacked: bool,
) -> None:
    lines.append(f"  - name: {_quote('dotmac_foundation')}")
    omitted = tuple(alert for alert in COMMON_ALERTS if alert.producer == UNBACKED)
    if not include_unbacked and omitted:
        lines.append(
            f"    # {len(omitted)} alert(s) omitted: producer=UNBACKED — no"
            " producer emits the metric(s) they read today. See"
            " docs/inventories/deployment-foundation-alert-producers.md."
            " Pass include_unbacked=True to render them anyway (each still"
            ' carries dotmac_unbacked="true").'
        )
    lines.append("    rules:")
    for alert in COMMON_ALERTS:
        unbacked = alert.producer == UNBACKED
        if unbacked and not include_unbacked:
            continue
        _emit_rule(
            lines,
            code=alert.code,
            severity=alert.severity,
            expression=alert.expression,
            for_seconds=alert.for_seconds,
            owner=alert.owner,
            product=product,
            # `Alert` (unlike `ProductAlert`) carries no separate summary
            # text — `protects` already states why the alert exists, which
            # is what a summary annotation would otherwise repeat.
            summary=alert.protects,
            runbook=alert.runbook,
            recovery=alert.recovery,
            protects=alert.protects,
            dedup_by=alert.dedup_by,
            thresholds=thresholds,
            unbacked=unbacked,
        )


def _emit_product_group(
    lines: list[str],
    *,
    product: str,
    product_alerts: tuple[ProductAlert, ...],
    thresholds: Mapping[str, str],
) -> None:
    lines.append(f"  - name: {_quote(f'dotmac_product_{product}')}")
    if not product_alerts:
        lines.append("    rules: []")
        return
    lines.append("    rules:")
    for alert in product_alerts:
        _emit_rule(
            lines,
            code=alert.code,
            severity=alert.severity,
            expression=alert.expression,
            for_seconds=alert.for_seconds,
            owner=alert.owner,
            product=product,
            summary=alert.summary,
            runbook=alert.runbook,
            recovery=alert.recovery,
            protects=alert.protects,
            dedup_by=alert.dedup_by,
            thresholds=thresholds,
        )


def render_alert_rules(
    spec: ProductDeploymentSpec,
    *,
    thresholds: Mapping[str, str],
    include_unbacked: bool = False,
) -> str:
    """A Prometheus-style alerting-rules YAML for one product's deployment.

    Two groups: `dotmac_foundation` (the `COMMON_ALERTS` catalogue, identical
    across every product) and `dotmac_product_<product>` (`spec.product_alerts`
    only — foundation alerts never leak into it, and a product's alerts never
    leak into the foundation group). Every rule carries `product` as a LABEL
    rather than a rewritten PromQL selector — see the module docstring,
    "Why a foundation expression is never rewritten with a product selector".

    `include_unbacked` (default `False`) controls the `producer="unbacked"`
    rows of `COMMON_ALERTS` (see `UNBACKED_ALERTS`). It defaults to OFF
    because **a RENDERED definition must have a producer**: a rule evaluated
    against a metric nothing emits never fires, and a rule that never fires
    is indistinguishable from a system that is never unhealthy. A label
    saying `dotmac_unbacked: "true"` documents the gap for whoever reads the
    file, but it does not stop an evaluator loading the rule, so labelling
    alone leaves 42 rules that would report coverage they do not have.
    Omitted, the rendered file states how many were dropped and why, right
    above the `rules:` list they would have appeared in.

    **What this function produces is RENDERABLE DEFINITIONS, not operational
    alerting.** Rendering is one of four conditions and the only one this
    package can establish. The current fleet truth is: 64 catalogued, 22
    producer-backed and therefore renderable, **zero connected to an
    evaluator or a routing path, and zero fire/recovery-proven.** Do not
    describe the 22 as "enabled", "active" or "live" — none of them is, and
    the vocabulary is what keeps the remaining three conditions visible.

    Passing `include_unbacked=True` renders the unbacked rows anyway, each
    with a `# UNBACKED: ...` comment and the label. That is for reviewing the
    catalogue, not for loading into an evaluator. A row becomes renderable by
    acquiring a real producer; see `producer_consistency_errors()` and
    `docs/inventories/deployment-foundation-alert-producers.md`.

    Raises `SpecError` naming EVERY unresolved `product-threshold` placeholder
    at once (`unresolved_placeholders`) rather than failing on the first: a
    product fixing its `thresholds` mapping should not have to re-run this
    once per missing name. Rendering is hand-rolled text, not a YAML library
    — this package ships zero runtime dependencies (ADR-0070 § 1) — and is
    therefore deterministic: an unchanged descriptor and threshold mapping
    render byte-identical output no matter how many times or when.
    """
    missing = unresolved_placeholders(spec, thresholds)
    if missing:
        raise SpecError(
            "missing threshold value(s) for placeholder(s) "
            + ", ".join(missing)
            + ". A monitoring rule silently rendered against a guessed "
            "number reports coverage it does not have, which is worse than "
            "the rule being visibly absent; supply every product-threshold "
            "placeholder in `thresholds` before rendering",
            where=spec.source,
        )

    lines: list[str] = [
        "# GENERATED by dotmac-deployment-foundation. Do not edit; edit",
        "# deploy/product.toml and re-run `dotmac-deploy render`.",
        f"# product {spec.product}",
        "#",
        "# Every signal this fires against is already scoped by the resource",
        "# attributes the platform stamps at collection time",
        "# (" + ", ".join(RESOURCE_ATTRIBUTES) + " — see",
        "# dotmac_deployment_foundation.telemetry.RESOURCE_ATTRIBUTES), so the",
        "# foundation expressions below are NOT rewritten with a per-product",
        "# PromQL label selector; splicing one into an arbitrary hand-authored",
        "# expression is error-prone and untestable. The `product` label on",
        "# every rule below is what a dashboard or alert router groups on",
        "# instead.",
        "",
        "groups:",
    ]
    _emit_foundation_group(
        lines,
        product=spec.product,
        thresholds=thresholds,
        include_unbacked=include_unbacked,
    )
    _emit_product_group(
        lines,
        product=spec.product,
        product_alerts=spec.product_alerts,
        thresholds=thresholds,
    )
    return "\n".join(lines) + "\n"


def render_alert_rules_digest(
    spec: ProductDeploymentSpec,
    thresholds: Mapping[str, str],
    *,
    include_unbacked: bool = False,
) -> str:
    """`sha256:<hex>` of the rendered document — what a drift checker compares
    against a committed alert-rules file without re-parsing either one."""
    rendered = render_alert_rules(
        spec, thresholds=thresholds, include_unbacked=include_unbacked
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(rendered).hexdigest()}"
