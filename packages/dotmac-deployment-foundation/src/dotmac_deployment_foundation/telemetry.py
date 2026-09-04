"""Resource attributes, deployment annotations, and the one-shipper rule.

The kernel owns the in-process seams: structured request context and
correlation ids, standard error attributes, the metric and tracing seams, a
DB-free `/health/live`, a dependency-aware `/health/ready` that returns non-200
when a dependency is unavailable, and an authenticated metrics contract. This
module owns the deploy-time half: what every signal is STAMPED with, where
signals are ROUTED, and what is emitted when a deployment starts, succeeds,
fails or rolls back.

## Why the attributes are derived rather than declared

Every one of them is already known at deploy time from the descriptor and the
run. Letting a product declare them would let two products disagree about what
`release` means, and a dashboard that has to normalise `release` across
products is a dashboard nobody maintains. So the descriptor declares WHETHER to
ship (`telemetry.logs/metrics/traces`) and this module decides WHAT the stamp
says.

The one attribute that cannot be derived is ``deployment_id``. It identifies a
RUN rather than a release: two deployments of the same digest to the same host
are two deployments, and an operator asking "did the second one fix it?" needs
them distinguishable. It is supplied by the caller.

## The duplicate-shipping rule

`dotmac_sub` ships its application logs to Loki directly from the process AND
runs a promtail job that tails the same container — to two different default
hosts. Every line is stored twice, every rate-based threshold is silently
doubled, and the duplication is invisible in a dashboard because both copies
look like ordinary lines. `dotmac_erp`'s promtail deliberately excludes the app,
which is the correct shape.

`spec.py` refuses the combination at parse time; this module refuses to build a
collector configuration for it, so neither a descriptor nor a rendered asset
can express the defect.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from .errors import SpecError
from .spec import ProductDeploymentSpec

# The closed set. A signal carrying an attribute outside it is refused rather
# than passed through: an open attribute set is how a subscriber identifier
# ends up as a metric label, and a high-cardinality label is not a mistake you
# can take back once it is in the time series database (ADR-0003,
# "Fleet observability").
RESOURCE_ATTRIBUTES: Final[tuple[str, ...]] = (
    "service.name",
    "deployment.environment",
    "dotmac.product",
    "dotmac.deployment_id",
    "dotmac.release",
    "dotmac.git_sha",
    "dotmac.image_digest",
    "dotmac.host",
    "dotmac.role",
)

ANNOTATION_EVENTS: Final[tuple[str, ...]] = (
    "deployment.start",
    "deployment.success",
    "deployment.failure",
    "deployment.rollback",
)


@dataclass(frozen=True, slots=True)
class ResourceAttributes:
    """The stamp every log line, metric point and span carries.

    Frozen and complete. There is no "extra attributes" escape: a product that
    needs another dimension adds it to its own metric, where cardinality is its
    own problem, rather than to the resource, where it multiplies every series
    the deployment emits.
    """

    service_name: str
    environment: str
    product: str
    deployment_id: str
    release: str
    git_sha: str
    image_digest: str
    host: str
    role: str

    def as_mapping(self) -> dict[str, str]:
        return {
            "service.name": self.service_name,
            "deployment.environment": self.environment,
            "dotmac.product": self.product,
            "dotmac.deployment_id": self.deployment_id,
            "dotmac.release": self.release,
            "dotmac.git_sha": self.git_sha,
            "dotmac.image_digest": self.image_digest,
            "dotmac.host": self.host,
            "dotmac.role": self.role,
        }

    def as_otel_env(self) -> str:
        """The `OTEL_RESOURCE_ATTRIBUTES` value for this role.

        Comma-separated `k=v`, which is the OTel convention — so a value
        containing a comma or an equals sign would silently split into two
        wrong attributes. Refused rather than escaped, because every legitimate
        value here is a product code, an environment name, a hex digest or a
        host name, and one that is not is a bug upstream worth surfacing.
        """
        parts: list[str] = []
        for key, value in self.as_mapping().items():
            if "," in value or "=" in value:
                raise SpecError(
                    f"resource attribute {key}={value!r} contains a comma or an "
                    "equals sign, which OTEL_RESOURCE_ATTRIBUTES cannot encode. "
                    "It would silently become two wrong attributes"
                )
            parts.append(f"{key}={value}")
        return ",".join(parts)


def resource_attributes(
    spec: ProductDeploymentSpec,
    *,
    role: str,
    deployment_id: str,
    host: str,
    environment: str = "",
) -> ResourceAttributes:
    """Derive the stamp for one role of one deployment.

    ``release`` is the image digest's short form rather than a version string,
    and that is deliberate. A version string is a claim a product makes about
    itself; a digest is the bytes that are running. When they disagree — and
    they do, because a version bump and a rebuild are separate events — the
    digest is the one that answers "what is actually serving this request".
    """
    if role not in spec.role_codes:
        raise SpecError(f"no role {role!r} in the descriptor", where=spec.source)
    if not deployment_id.strip():
        raise SpecError(
            "deployment_id is required. It identifies a RUN, not a release: two "
            "deployments of one digest to one host are two deployments, and an "
            "operator asking whether the second one fixed it needs them apart"
        )
    digest = spec.image_digest
    return ResourceAttributes(
        service_name=f"{spec.product}.{role}",
        environment=environment or spec.environment or "unknown",
        product=spec.product,
        deployment_id=deployment_id.strip(),
        release=digest.split(":", 1)[1][:12],
        git_sha=spec.source_revision,
        image_digest=digest,
        host=host,
        role=role,
    )


#: A stable identifier for the refusal above. Assert this; read the prose.
ANNOTATION_DETAIL_NOT_A_TOKEN: Final = "telemetry.annotation_detail_not_a_token"

#: Same shape rule `deployment_evidence` uses for a step kind.
ANNOTATION_DETAIL: Final = re.compile(r"^[a-z][a-z0-9_.]{2,63}$")


@dataclass(frozen=True, slots=True)
class Annotation:
    """One deployment event, as it reaches the observability platform.

    An annotation is the cheapest incident-response tool there is: the first
    question about any graph that turned bad at 14:32 is "what changed at
    14:32", and no product in the fleet emits one today.
    """

    event: str
    product: str
    environment: str
    deployment_id: str
    image_digest: str
    git_sha: str
    strategy: str
    #: A machine TOKEN or empty — never a sentence, and never an exception.
    #:
    #: This was free text, and `Executor` filled it on the failure path with
    #: `outcome.failure`, which is `str(exc)`. So raw exception text — a DSN in
    #: a connection error, a fragment of SQL, whatever a driver put in the
    #: message — was leaving the host for the observability platform, on the one
    #: path least exercised and most likely to contain it.
    #:
    #: Refused at construction rather than filtered before the send: an
    #: annotation is emitted from several places and a filter is only ever as
    #: good as the caller who remembered it. `ANNOTATION_DETAIL` is the same
    #: shape rule `deployment_evidence` applies to a step kind, for the same
    #: reason — prose does not match, and an exception message never matches.
    detail: str = ""

    def as_mapping(self) -> dict[str, str]:
        """The annotation as flat string pairs.

        Flat and stringly-typed on purpose: this crosses a provider seam and
        ends up as an HTTP form field, a label set or a log line depending on
        what the deployment ships to. A nested structure would have to be
        flattened by every implementation, and they would flatten it differently.
        """
        if self.event not in ANNOTATION_EVENTS:
            raise SpecError(f"unknown annotation event {self.event!r}")
        self._require_token_detail()
        return {
            "event": self.event,
            "product": self.product,
            "environment": self.environment,
            "deployment_id": self.deployment_id,
            "image_digest": self.image_digest,
            "git_sha": self.git_sha,
            "strategy": self.strategy,
            "detail": self.detail,
        }

    def _require_token_detail(self) -> None:
        """`detail` is a standing, not a story. See the field's own comment."""
        if self.detail and not ANNOTATION_DETAIL.match(str(self.detail)):
            raise SpecError(
                f"annotation detail {self.detail!r} is not a machine token. An "
                "annotation crosses a provider seam and lands in a log line, a "
                "label set or an HTTP field; free text here is how an exception "
                "message leaves the host. Send a standing",
                code=ANNOTATION_DETAIL_NOT_A_TOKEN,
            )

    def as_json(self) -> str:
        if self.event not in ANNOTATION_EVENTS:
            raise SpecError(f"unknown annotation event {self.event!r}")
        self._require_token_detail()
        return json.dumps(
            {
                "event": self.event,
                "product": self.product,
                "environment": self.environment,
                "deployment_id": self.deployment_id,
                "image_digest": self.image_digest,
                "git_sha": self.git_sha,
                "strategy": self.strategy,
                "detail": self.detail,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


def render_collector_config(
    spec: ProductDeploymentSpec,
    *,
    deployment_id: str,
    host: str,
    environment: str = "",
) -> str:
    """An OpenTelemetry collector configuration for this deployment.

    Vendor-neutral by construction: the collector receives OTLP and forwards
    OTLP, and the only vendor-specific fact in the whole file is an endpoint
    supplied as a material NAME. Swapping the Observability platform is then a
    material change rather than a code change, which is what "vendor-neutral"
    has to mean to be worth claiming.

    Emitted as text by hand for the same reason every other renderer is: the
    package carries no YAML dependency, and a byte-comparable output makes
    `render --check` a diff.
    """
    telemetry = spec.telemetry
    if telemetry.app_direct_shipping and telemetry.logs:
        # Unreachable through `ProductDeploymentSpec`, which refuses this at
        # parse time. Kept because this function is also callable with a spec
        # built by a future loader, and a second line of defence on a rule this
        # expensive to get wrong is cheap.
        raise SpecError(
            "the descriptor asks for both application-direct log shipping and "
            "collector log collection; every line would be stored twice and "
            "every rate threshold silently doubled"
        )

    base = resource_attributes(
        spec,
        role=spec.role_codes[0],
        deployment_id=deployment_id,
        host=host,
        environment=environment,
    )
    shared = {
        key: value for key, value in base.as_mapping().items() if key != "dotmac.role"
    }
    shared["service.name"] = spec.product

    lines: list[str] = [
        "# GENERATED by dotmac-deployment-foundation. Do not edit; edit",
        "# deploy/product.toml and re-run `dotmac-deploy render`.",
        f"# product {spec.product}   image {spec.image_digest}",
        "#",
        "# Vendor-neutral OTLP in, OTLP out. The only platform-specific value in",
        "# this file is an endpoint supplied as a material NAME, so changing the",
        "# Observability platform is a material change and not a code change.",
        "",
        "receivers:",
        "  otlp:",
        "    protocols:",
        "      grpc:",
        "        endpoint: 127.0.0.1:4317",
        "      http:",
        "        endpoint: 127.0.0.1:4318",
    ]
    if telemetry.logs:
        lines.extend(
            [
                "  filelog/containers:",
                "    include: [/var/lib/docker/containers/*/*-json.log]",
                "    include_file_path: true",
                "    operators:",
                "      - type: json_parser",
                "        parse_from: body",
            ]
        )

    lines.extend(
        [
            "",
            "processors:",
            "  # The resource stamp is applied HERE rather than in each process, so",
            "  # a role that forgets to set it cannot ship unattributed signal.",
            "  resource/dotmac:",
            "    attributes:",
        ]
    )
    for key, value in sorted(shared.items()):
        lines.append(f"      - key: {key}")
        lines.append(f"        value: {value}")
        lines.append("        action: upsert")
    lines.extend(
        [
            "  memory_limiter:",
            "    check_interval: 5s",
            "    limit_percentage: 70",
            "    spike_limit_percentage: 20",
            "  batch:",
            "    timeout: 5s",
            "",
            "exporters:",
            "  otlp/platform:",
            f"    endpoint: ${{{telemetry.endpoint_material}}}",
            "    tls:",
            # A deployment decision, not a constant. `false` is the right
            # default and the wrong absolute: a disposable rehearsal against a
            # plaintext local sink cannot use the rendered config at all if this
            # is hardcoded, which is how a facility ends up with a collector
            # configuration nobody can exercise.
            f"      insecure: {'true' if telemetry.collector_insecure else 'false'}",
            "",
            "extensions:",
            "  health_check:",
            "    endpoint: 127.0.0.1:13133",
            "",
            "service:",
            "  extensions: [health_check]",
            "  telemetry:",
            "    metrics:",
            "      # The collector's OWN metrics. A pipeline that stops shipping",
            "      # is otherwise indistinguishable from a quiet system, which is",
            "      # what the dead-man alert exists to tell apart.",
            "      address: 127.0.0.1:8888",
            "  pipelines:",
        ]
    )
    pipelines = [
        ("traces", telemetry.traces, "otlp"),
        ("metrics", telemetry.metrics, "otlp"),
        ("logs", telemetry.logs, "otlp, filelog/containers"),
    ]
    for name, enabled, receivers in pipelines:
        if not enabled:
            lines.append(f"    # {name}: disabled by the descriptor")
            continue
        lines.append(f"    {name}:")
        lines.append(f"      receivers: [{receivers}]")
        lines.append("      processors: [memory_limiter, resource/dotmac, batch]")
        lines.append("      exporters: [otlp/platform]")
    return "\n".join(lines) + "\n"


def required_attribute_names() -> tuple[str, ...]:
    """The conformance kit's expectation, exported so both sides read one list."""
    return RESOURCE_ATTRIBUTES


def missing_attributes(observed: Mapping[str, str]) -> tuple[str, ...]:
    """Which required attributes a signal is missing.

    Used by the conformance test that drives a real signal through a collector
    and asserts the stamp arrived. Asserting the CONFIG contains the attributes
    would prove only that the file was written.
    """
    return tuple(
        name for name in RESOURCE_ATTRIBUTES if not str(observed.get(name, "")).strip()
    )
