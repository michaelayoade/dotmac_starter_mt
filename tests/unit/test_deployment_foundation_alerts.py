"""Tests for `dotmac_deployment_foundation.alerts` — the common infrastructure
alert catalogue and its renderer.

The invariant this file protects is the one stated in the module's own
docstring: a fleet-wide alert is defined exactly ONCE (`COMMON_ALERTS`), a
product's own alerts never leak into that catalogue's group and vice versa,
and a `product-threshold` placeholder with no supplied value is a REFUSAL,
never a silently-guessed number (`dotmac_integrator` rule 19, restated for a
catalogue rather than a single retention knob).

Five kinds of coverage, in order:

1. **Catalogue well-formedness** — every one of the 64 transcribed rows is
   internally consistent (parametrised, so a single malformed row fails with
   its own id rather than one opaque assertion for the whole tuple), codes
   are unique, and the count is pinned to an explicit number so a row
   silently dropped during a future edit fails loudly here.
2. **Placeholder bookkeeping** — `PLACEHOLDER_SOURCES` and the placeholders
   actually used across the catalogue agree in BOTH directions (a stale
   entry left behind after an expression edit is exactly as wrong as a
   missing one), and `FOUNDATION_DEFAULTS` covers precisely the
   `foundation-default` names and no `product-threshold` ones.
3. **Rendering** — deterministic against a real `ProductDeploymentSpec`
   (built through `ProductDeploymentSpec.loads`, not hand-constructed, for
   the same reason `test_deployment_foundation_compose.py` does this: a
   mistake in this module's understanding of the loader's defaults should
   fail here, not three tests downstream), the two groups never cross-leak,
   and a missing `product-threshold` value names EVERY missing placeholder
   in one refusal.
4. **Producer classification** — every alert names a producer from the
   closed vocabulary; `metric_names_in` (the PromQL metric-name tokenizer)
   is checked against hand-picked expressions with an EXACT-set assertion;
   `UNBACKED_ALERTS` is a two-directional ratchet against the catalogue's
   actual `producer="unbacked"` rows; a rendered unbacked rule carries the
   `dotmac_unbacked` label and a comment, a backed one carries neither; and
   `include_unbacked=False` omits the unbacked rows and says so.
5. **Sensitivity proofs (ADR-0018)** for `Alert.__post_init__` and for
   `producer_consistency_errors` — a guard that only ever refuses is
   unfalsifiable, so a NEGATIVE CONTROL proves a well-formed `Alert`
   constructs cleanly (and a correctly-classified alert passes the
   consistency check) before the refusal/mismatch tests are trusted to mean
   anything.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from collections.abc import Mapping

import pytest
import yaml
from dotmac_deployment_foundation.alerts import (
    COMMON_ALERTS,
    FOUNDATION_DEFAULTS,
    PLACEHOLDER_SOURCES,
    PRODUCERS,
    UNBACKED,
    UNBACKED_ALERTS,
    Alert,
    metric_names_in,
    producer_consistency_errors,
    render_alert_rules,
    render_alert_rules_digest,
    unresolved_placeholders,
)
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

_EXPECTED_ALERT_COUNT = 64
_SEVERITIES = ("page", "ticket", "info")

# A SEPARATE derivation from the module's own `_placeholders_in`, so the
# well-formedness check below is not circular — it would prove nothing to
# re-run the exact function `Alert.__post_init__` already trusts.
_PLACEHOLDER_TOKEN = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")

_MANIFEST_DIGEST = "sha256:" + "a" * 64
_IMAGE = f"registry.example.com/acme/app@sha256:{'b' * 64}"
_SOURCE_REVISION = "c" * 40

_TOML = f"""
schema = "ProductDeploymentSpec.v1"
product = "acme"
environment = "prod"

[assembly]
manifest_path = "deploy/product.toml"
manifest_digest = "{_MANIFEST_DIGEST}"

[image]
reference = "{_IMAGE}"
source_revision = "{_SOURCE_REVISION}"

[[roles]]
code = "app"
command = ["python", "-m", "app"]

[roles.resources]
cpus = "1.0"
memory = "512m"

# A running role must declare SOME liveness signal — an HTTP probe, a worker
# ping or a scheduler tick budget. A role with none of the three is refused,
# because that role is unmonitored rather than exempt.
[roles.health.live]
path = "/health/live"
port = 8000

[migration]
command = ["alembic", "upgrade", "heads"]
heads_command = ["alembic", "current"]
owner_material = "MIGRATION_DATABASE_URL"
expected_heads = ["abc123"]
compatibility = "online"

[[alerts]]
code = "CHECKOUT_CONV_DROP"
severity = "page"
expression = "checkout_conversion_ratio < 0.5"
owner = "checkout-team"
for_seconds = 300
summary = "Checkout conversion dropped"
runbook = "#checkout-conv-drop"
recovery = "ratio back above 0.5"
protects = "checkout revenue"
"""


@pytest.fixture(scope="module")
def spec() -> ProductDeploymentSpec:
    return ProductDeploymentSpec.loads(_TOML, source="<test-fixture>")


def _all_product_thresholds() -> dict[str, str]:
    """A value for every `product-threshold` placeholder in the catalogue.

    The literal value ("1") is arbitrary — these tests never assert on the
    substituted NUMBER, only on structure and on which placeholders were
    resolvable — so one uniform stand-in keeps the fixture short.
    """
    return {
        name: "1"
        for name, kind in PLACEHOLDER_SOURCES.items()
        if kind == "product-threshold"
    }


def _valid_alert_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "code": "FDN_TEST_ALERT",
        "severity": "page",
        "expression": "up == 0",
        "for_seconds": 60,
        "owner": "foundation",
        "protects": "something worth protecting",
        "runbook": "#test-alert",
        "dedup_by": ("instance",),
        "recovery": "up == 1",
        "placeholders": (),
        "producer": "application",
    }
    base.update(overrides)
    return base


# ── 1. catalogue well-formedness ────────────────────────────────────────────


def test_the_catalogue_has_exactly_the_expected_number_of_alerts() -> None:
    """Pinned to an explicit number so a row silently dropped in a future
    edit fails here rather than passing with fewer, quieter tests."""
    assert len(COMMON_ALERTS) == _EXPECTED_ALERT_COUNT


def test_every_alert_code_is_unique() -> None:
    codes = [alert.code for alert in COMMON_ALERTS]
    assert len(set(codes)) == len(codes)


@pytest.mark.parametrize("alert", COMMON_ALERTS, ids=lambda alert: alert.code)
def test_every_foundation_alert_is_well_formed(alert: Alert) -> None:
    assert alert.code.startswith("FDN_")
    assert alert.severity in _SEVERITIES
    assert alert.owner == "foundation"
    assert alert.expression.strip()
    assert alert.for_seconds >= 0
    assert alert.protects.strip()
    assert alert.runbook.startswith("#")
    assert alert.recovery.strip()
    assert alert.dedup_by
    assert all(label.strip() for label in alert.dedup_by)
    # Re-derived independently of the constructor's own check: the
    # constructor already refuses a mismatch, so this restates the contract
    # as a property of the published catalogue rather than only trusting
    # that construction succeeded.
    assert set(alert.placeholders) == set(_PLACEHOLDER_TOKEN.findall(alert.expression))
    assert alert.producer in PRODUCERS


# ── 2. placeholder bookkeeping ──────────────────────────────────────────────


def test_every_used_placeholder_has_a_source_entry_and_every_entry_is_used() -> None:
    used: set[str] = set()
    for alert in COMMON_ALERTS:
        used.update(alert.placeholders)
    declared = set(PLACEHOLDER_SOURCES)
    assert used == declared, (
        f"used but undeclared: {sorted(used - declared)}; "
        f"declared but unused (stale): {sorted(declared - used)}"
    )


def test_placeholder_sources_only_names_the_two_known_kinds() -> None:
    assert set(PLACEHOLDER_SOURCES.values()) <= {
        "foundation-default",
        "product-threshold",
    }


def test_only_foundation_default_placeholders_carry_a_default() -> None:
    for name, kind in PLACEHOLDER_SOURCES.items():
        if kind == "foundation-default":
            assert name in FOUNDATION_DEFAULTS, f"{name} declares no default"
        else:
            assert name not in FOUNDATION_DEFAULTS, (
                f"{name} is product-threshold but has a default; a guessed "
                "number for a product-owned threshold defeats the refusal "
                "this catalogue exists to make"
            )


def test_foundation_defaults_has_no_entry_outside_the_declared_set() -> None:
    declared_foundation_default = {
        name
        for name, kind in PLACEHOLDER_SOURCES.items()
        if kind == "foundation-default"
    }
    assert set(FOUNDATION_DEFAULTS) == declared_foundation_default


# ── 3. rendering ─────────────────────────────────────────────────────────────


def test_rendering_with_complete_thresholds_succeeds_and_is_deterministic(
    spec: ProductDeploymentSpec,
) -> None:
    thresholds = _all_product_thresholds()
    first = render_alert_rules(spec, thresholds=thresholds)
    second = render_alert_rules(spec, thresholds=thresholds)
    assert first == second
    parsed = yaml.safe_load(first)
    assert isinstance(parsed, dict)
    assert "groups" in parsed


def test_render_alert_rules_digest_is_the_sha256_of_the_rendered_bytes(
    spec: ProductDeploymentSpec,
) -> None:
    thresholds = _all_product_thresholds()
    rendered = render_alert_rules(spec, thresholds=thresholds)
    digest = render_alert_rules_digest(spec, thresholds)
    assert digest == f"sha256:{hashlib.sha256(rendered.encode('utf-8')).hexdigest()}"


def test_unresolved_placeholders_with_no_thresholds_names_every_product_threshold(
    spec: ProductDeploymentSpec,
) -> None:
    expected = tuple(
        sorted(
            name
            for name, kind in PLACEHOLDER_SOURCES.items()
            if kind == "product-threshold"
        )
    )
    assert unresolved_placeholders(spec, {}) == expected


def test_unresolved_placeholders_shrinks_as_thresholds_are_supplied(
    spec: ProductDeploymentSpec,
) -> None:
    thresholds = _all_product_thresholds()
    del thresholds["pool_saturation_pct"]
    assert unresolved_placeholders(spec, thresholds) == ("pool_saturation_pct",)


def test_a_foundation_default_resolves_with_no_thresholds_supplied(
    spec: ProductDeploymentSpec,
) -> None:
    assert "cpu_saturation_pct" not in unresolved_placeholders(spec, {})


def test_rendering_with_a_missing_product_threshold_raises_naming_every_missing_one(
    spec: ProductDeploymentSpec,
) -> None:
    with pytest.raises(SpecError) as exc_info:
        render_alert_rules(spec, thresholds={})
    message = str(exc_info.value)
    for name in unresolved_placeholders(spec, {}):
        assert name in message, f"{name} missing from the refusal message"


def test_rendering_with_exactly_one_missing_threshold_raises_naming_only_that_one(
    spec: ProductDeploymentSpec,
) -> None:
    thresholds = _all_product_thresholds()
    del thresholds["pool_saturation_pct"]
    with pytest.raises(SpecError) as exc_info:
        render_alert_rules(spec, thresholds=thresholds)
    assert "pool_saturation_pct" in str(exc_info.value)


def test_product_alerts_render_in_their_own_group_and_never_leak(
    spec: ProductDeploymentSpec,
) -> None:
    thresholds = _all_product_thresholds()
    doc = yaml.safe_load(
        # include_unbacked=True: this is about group SEPARATION, so render the
        # whole catalogue — it gives leakage the largest possible surface.
        render_alert_rules(spec, thresholds=thresholds, include_unbacked=True)
    )
    groups: Mapping[str, Mapping[str, object]] = {
        group["name"]: group for group in doc["groups"]
    }
    assert set(groups) == {"dotmac_foundation", f"dotmac_product_{spec.product}"}

    foundation_names = {rule["alert"] for rule in groups["dotmac_foundation"]["rules"]}
    product_names = {
        rule["alert"] for rule in groups[f"dotmac_product_{spec.product}"]["rules"]
    }

    assert foundation_names == {alert.code for alert in COMMON_ALERTS}
    assert product_names == {alert.code for alert in spec.product_alerts}
    assert foundation_names.isdisjoint(product_names)


def test_a_product_with_no_declared_alerts_renders_an_empty_product_group(
    spec: ProductDeploymentSpec,
) -> None:
    empty = dataclasses.replace(spec, product_alerts=())
    thresholds = _all_product_thresholds()
    doc = yaml.safe_load(
        # include_unbacked=True: the assertion below counts the FULL catalogue.
        render_alert_rules(empty, thresholds=thresholds, include_unbacked=True)
    )
    groups = {group["name"]: group for group in doc["groups"]}
    assert groups[f"dotmac_product_{empty.product}"]["rules"] == []
    # The foundation group is unaffected by a product declaring no alerts.
    assert len(groups["dotmac_foundation"]["rules"]) == _EXPECTED_ALERT_COUNT


def test_every_rendered_rule_carries_the_required_labels_and_annotations(
    spec: ProductDeploymentSpec,
) -> None:
    """Every rule carries the three base labels; ONLY an unbacked foundation
    rule additionally carries `dotmac_unbacked` — see the producer-
    classification tests below for that label's own coverage."""
    thresholds = _all_product_thresholds()
    doc = yaml.safe_load(render_alert_rules(spec, thresholds=thresholds))
    unbacked_codes = {
        alert.code for alert in COMMON_ALERTS if alert.producer == UNBACKED
    }
    base_labels = {"severity", "owner", "product"}
    for group in doc["groups"]:
        for rule in group["rules"]:
            if rule["alert"] in unbacked_codes:
                assert set(rule["labels"]) == base_labels | {"dotmac_unbacked"}
                assert rule["labels"]["dotmac_unbacked"] == "true"
            else:
                assert set(rule["labels"]) == base_labels
            assert rule["labels"]["severity"] in _SEVERITIES
            assert rule["labels"]["product"] == spec.product
            assert set(rule["annotations"]) == {
                "summary",
                "runbook",
                "recovery",
                "protects",
                "dedup_by",
            }


def test_a_rendered_rules_for_duration_matches_the_alerts_for_seconds(
    spec: ProductDeploymentSpec,
) -> None:
    thresholds = _all_product_thresholds()
    doc = yaml.safe_load(render_alert_rules(spec, thresholds=thresholds))
    foundation_rules = {
        rule["alert"]: rule
        for group in doc["groups"]
        if group["name"] == "dotmac_foundation"
        for rule in group["rules"]
    }
    by_code = {alert.code: alert for alert in COMMON_ALERTS}
    for code, rule in foundation_rules.items():
        assert rule["for"] == f"{by_code[code].for_seconds}s"


# ── 4. producer classification ──────────────────────────────────────────────


def test_every_alert_has_a_producer_and_the_vocabulary_is_closed() -> None:
    assert UNBACKED in PRODUCERS
    for alert in COMMON_ALERTS:
        assert alert.producer in PRODUCERS


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        # A simple ratio with a status-code label filter: the label filter's
        # contents (status, the regex value) must not leak into the result,
        # and the same metric appearing twice must not duplicate.
        (
            'sum(rate(http_requests_total{status=~"5.."}[5m])) by (service,instance) '
            "/ sum(rate(http_requests_total[5m])) by (service,instance) "
            "> {{error_rate_warning_pct}}",
            frozenset({"http_requests_total"}),
        ),
        # `by (le,service,instance)` is a grouping clause, not a metric.
        (
            "histogram_quantile(0.99, "
            "sum(rate(http_request_duration_seconds_bucket[5m])) "
            "by (le,service,instance)) > {{latency_p99_warning_seconds}}",
            frozenset({"http_request_duration_seconds_bucket"}),
        ),
        # `offset 1d` is a bare keyword with no trailing `(`.
        (
            "sum(rate(http_requests_total[5m])) by (service) "
            "< sum(rate(http_requests_total[5m] offset 1d)) by (service) "
            "* {{traffic_drop_ratio}}",
            frozenset({"http_requests_total"}),
        ),
        # Two metrics joined by `and` — a bare PromQL keyword, not a metric.
        (
            "container_last_seen < time() - 60 and container_exit_code != 0",
            frozenset({"container_last_seen", "container_exit_code"}),
        ),
        # `on()` (empty) is still a grouping/vector-matching clause to strip.
        (
            "pg_alembic_version_info != on() product_expected_migration_head",
            frozenset({"pg_alembic_version_info", "product_expected_migration_head"}),
        ),
        # A `{{placeholder}}` nested INSIDE a label matcher's quoted value
        # must not survive as part of a label, and must not block stripping
        # the surrounding `{...}` block.
        (
            "absent_over_time(log_lines_received_total"
            '{instance=~"{{known_live_instances}}"}[15m])',
            frozenset({"log_lines_received_total"}),
        ),
        # A duration literal's trailing letter (`5m`, `1d`) must never
        # survive as a one-character "identifier" once its leading digit is
        # skipped.
        (
            "rate(container_cpu_usage_seconds_total[5m]) / container_spec_cpu_quota "
            "> {{cpu_saturation_pct}}",
            frozenset(
                {"container_cpu_usage_seconds_total", "container_spec_cpu_quota"}
            ),
        ),
        # A single bare metric with no function, label or grouping clause.
        ("pg_up == 0", frozenset({"pg_up"})),
    ],
    ids=[
        "ratio-with-label-filter",
        "histogram-quantile-with-by-clause",
        "offset-keyword",
        "and-keyword-two-metrics",
        "empty-on-clause",
        "placeholder-nested-in-label-value",
        "duration-literal-trailing-letter",
        "bare-metric-no-wrapper",
    ],
)
def test_metric_names_in_extracts_the_expected_exact_set(
    expression: str, expected: frozenset[str]
) -> None:
    assert metric_names_in(expression) == expected


def test_unbacked_alerts_matches_the_catalogue_exactly() -> None:
    """A two-directional ratchet (see the module docstring on
    `UNBACKED_ALERTS`): this fails if an alert becomes unbacked without the
    declared set being updated, AND if the declared set names a code that is
    no longer actually unbacked in the catalogue."""
    actual = frozenset(
        alert.code for alert in COMMON_ALERTS if alert.producer == UNBACKED
    )
    assert actual == UNBACKED_ALERTS


def test_unbacked_alerts_is_a_proper_subset_of_all_codes() -> None:
    all_codes = {alert.code for alert in COMMON_ALERTS}
    assert UNBACKED_ALERTS <= all_codes
    assert UNBACKED_ALERTS != all_codes, "expected at least one backed alert"


def test_the_real_catalogue_has_zero_producer_consistency_errors() -> None:
    """The independent ground-truth cross-check
    (`producer_consistency_errors`) agrees with every hand-typed `producer=`
    field actually in `COMMON_ALERTS` today."""
    assert producer_consistency_errors(COMMON_ALERTS) == ()


def test_include_unbacked_false_omits_unbacked_rules_and_says_so(
    spec: ProductDeploymentSpec,
) -> None:
    thresholds = _all_product_thresholds()
    rendered = render_alert_rules(spec, thresholds=thresholds, include_unbacked=False)
    doc = yaml.safe_load(rendered)
    foundation = next(
        group for group in doc["groups"] if group["name"] == "dotmac_foundation"
    )
    rendered_codes = {rule["alert"] for rule in foundation["rules"]}
    backed_codes = {alert.code for alert in COMMON_ALERTS if alert.producer != UNBACKED}
    assert rendered_codes == backed_codes
    assert rendered_codes.isdisjoint(UNBACKED_ALERTS)
    assert str(len(UNBACKED_ALERTS)) in rendered
    assert "omitted" in rendered


def test_the_default_render_emits_no_definition_that_has_no_producer(
    spec: ProductDeploymentSpec,
) -> None:
    """A RENDERED definition must have a producer.

    A rule evaluated against a metric nothing emits never fires, and a rule
    that never fires reads exactly like a system that is never unhealthy. The
    `dotmac_unbacked` label documents the gap but does not stop an evaluator
    loading the rule, so the default has to OMIT them, not decorate them.

    Renderable is NOT enabled: none of these is connected to an evaluator or a
    routing path, and none is fire/recovery-proven. This test guards condition
    1 of four — see the alert-producers inventory.
    """
    thresholds = _all_product_thresholds()
    default_render = render_alert_rules(spec, thresholds=thresholds)
    explicit_off = render_alert_rules(
        spec, thresholds=thresholds, include_unbacked=False
    )
    assert default_render == explicit_off

    doc = yaml.safe_load(default_render)
    foundation = next(
        group for group in doc["groups"] if group["name"] == "dotmac_foundation"
    )
    rendered = {rule["alert"] for rule in foundation["rules"]}
    assert rendered == {alert.code for alert in COMMON_ALERTS} - set(UNBACKED_ALERTS)
    assert rendered, "the default render must still emit the BACKED definitions"
    assert not rendered & set(UNBACKED_ALERTS)

    # Opting in is still possible, and is the only way to get all of them.
    everything = yaml.safe_load(
        render_alert_rules(spec, thresholds=thresholds, include_unbacked=True)
    )
    opted_in = next(
        group for group in everything["groups"] if group["name"] == "dotmac_foundation"
    )
    assert {rule["alert"] for rule in opted_in["rules"]} == {
        alert.code for alert in COMMON_ALERTS
    }


def test_include_unbacked_false_rendering_is_deterministic(
    spec: ProductDeploymentSpec,
) -> None:
    thresholds = _all_product_thresholds()
    first = render_alert_rules(spec, thresholds=thresholds, include_unbacked=False)
    second = render_alert_rules(spec, thresholds=thresholds, include_unbacked=False)
    assert first == second
    digest = render_alert_rules_digest(spec, thresholds, include_unbacked=False)
    assert digest == f"sha256:{hashlib.sha256(first.encode('utf-8')).hexdigest()}"


def test_an_unbacked_rule_carries_a_comment_directly_above_it(
    spec: ProductDeploymentSpec,
) -> None:
    """`yaml.safe_load` strips comments, so the comment line itself has to be
    checked against the raw text, immediately preceding the rule's own
    `- alert:` line."""
    thresholds = _all_product_thresholds()
    rendered = render_alert_rules(
        # This test is ABOUT the unbacked rows, which the default now omits.
        spec,
        thresholds=thresholds,
        include_unbacked=True,
    )
    lines = rendered.splitlines()
    an_unbacked_code = next(iter(UNBACKED_ALERTS))
    alert_line = next(
        i for i, line in enumerate(lines) if f'"{an_unbacked_code}"' in line
    )
    comment_line = lines[alert_line - 1]
    assert comment_line.strip().startswith("#")
    assert "UNBACKED" in comment_line
    assert "no producer" in comment_line.lower()


def test_a_backed_rule_carries_no_such_comment(spec: ProductDeploymentSpec) -> None:
    thresholds = _all_product_thresholds()
    rendered = render_alert_rules(spec, thresholds=thresholds)
    lines = rendered.splitlines()
    a_backed_code = next(
        alert.code for alert in COMMON_ALERTS if alert.producer != UNBACKED
    )
    alert_line = next(i for i, line in enumerate(lines) if f'"{a_backed_code}"' in line)
    comment_line = lines[alert_line - 1]
    assert not comment_line.strip().startswith("#")


# ── 5. sensitivity proofs (ADR-0018) ────────────────────────────────────────


def test_negative_control_a_well_formed_alert_constructs_cleanly() -> None:
    """Without this, every "is refused" assertion below could pass because
    the constructor rejects everything, well-formed input included."""
    alert = Alert(**_valid_alert_kwargs())
    assert alert.code == "FDN_TEST_ALERT"
    assert alert.placeholders == ()


def test_an_alert_with_an_undeclared_placeholder_in_its_expression_is_refused() -> None:
    with pytest.raises(SpecError):
        Alert(**_valid_alert_kwargs(expression='up{env=~"{{deploy_env}}"} == 0'))


def test_an_alert_with_a_declared_but_absent_placeholder_is_refused() -> None:
    with pytest.raises(SpecError):
        Alert(**_valid_alert_kwargs(placeholders=("deploy_env",)))


@pytest.mark.parametrize(
    "field", ["expression", "owner", "protects", "runbook", "recovery"]
)
def test_an_alert_with_an_empty_mandatory_text_field_is_refused(field: str) -> None:
    with pytest.raises(SpecError):
        Alert(**_valid_alert_kwargs(**{field: ""}))


def test_an_alert_with_empty_dedup_by_is_refused() -> None:
    with pytest.raises(SpecError):
        Alert(**_valid_alert_kwargs(dedup_by=()))


def test_an_alert_with_a_blank_dedup_by_label_is_refused() -> None:
    with pytest.raises(SpecError):
        Alert(**_valid_alert_kwargs(dedup_by=("instance", "  ")))


def test_a_catalogue_word_instead_of_the_routing_vocabulary_is_refused() -> None:
    """`severity` speaks `page | ticket | info`; the catalogue's own
    `critical`/`warning` words must be mapped before construction, not
    accepted as-is (see the module docstring)."""
    with pytest.raises(SpecError):
        Alert(**_valid_alert_kwargs(severity="warning"))


def test_an_alert_with_a_code_missing_the_fdn_prefix_is_refused() -> None:
    with pytest.raises(SpecError):
        Alert(**_valid_alert_kwargs(code="NOT_PREFIXED"))


def test_an_alert_with_negative_for_seconds_is_refused() -> None:
    with pytest.raises(SpecError):
        Alert(**_valid_alert_kwargs(for_seconds=-1))


def test_an_alert_with_a_producer_outside_the_closed_vocabulary_is_refused() -> None:
    with pytest.raises(SpecError):
        Alert(**_valid_alert_kwargs(producer="a_made_up_exporter"))


def test_negative_control_a_correct_producer_passes_the_consistency_check() -> None:
    """Without this, `producer_consistency_errors` finding a mismatch below
    could pass because the check flags everything, correct declarations
    included."""
    correct = Alert(
        **_valid_alert_kwargs(
            expression="node_cpu_seconds_total == 0",
            producer="node_exporter",
        )
    )
    assert producer_consistency_errors([correct]) == ()


def test_a_real_producer_declared_for_a_metric_nothing_emits_is_caught() -> None:
    """The sensitivity proof this module exists to make possible: a
    hand-typed `producer=` field can simply be wrong, and this is the
    independent check that catches it — the metric name here is not one any
    known exporter, the application, or the deployment engine emits, so
    declaring a REAL producer for it must be flagged."""
    mismatched = Alert(
        **_valid_alert_kwargs(
            expression="totally_invented_metric_no_one_emits == 0",
            producer="node_exporter",
        )
    )
    errors = producer_consistency_errors([mismatched])
    assert errors == (mismatched.code,)


def test_declaring_unbacked_for_a_known_backed_metric_is_also_caught() -> None:
    """The mismatch check works in both directions: UNDER-claiming a
    producer is exactly as wrong as over-claiming one, since it means a real
    alert would sit needlessly in `UNBACKED_ALERTS`, omitted whenever a
    deployment renders with `include_unbacked=False`."""
    under_claimed = Alert(
        **_valid_alert_kwargs(expression="pg_up == 0", producer=UNBACKED)
    )
    errors = producer_consistency_errors([under_claimed])
    assert errors == (under_claimed.code,)
