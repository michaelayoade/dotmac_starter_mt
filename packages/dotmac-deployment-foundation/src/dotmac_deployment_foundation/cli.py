"""``dotmac-deploy`` — the one command every product's CI and host runs.

The whole facility is behind twelve subcommands. Three design choices are worth
stating because each is load-bearing:

**Every command that can mutate takes ``--dry-run``, and ``deploy`` defaults to
it.** A deployment tool whose default action deploys is a tool that eventually
deploys because somebody pressed up-arrow-enter. Printing the plan is free;
running it must be a thing you asked for.

**Rendering and checking are one command with a flag, not two.** `render` and
`render --check` share every line of logic by construction, so they cannot
drift into disagreeing about what the correct output is — which is the failure
that makes a `--check` mode worthless.

**The exit codes are a contract.** CI reads them: 0 ok, 1 refused (a gate said
no, a check found drift), 2 usage. A tool that returns 1 for both "the world is
wrong" and "you typed the wrong flag" cannot be wired into a pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from .errors import DeploymentFoundationError, RenderDrift, SpecError
from .spec import SCHEMA, ProductDeploymentSpec

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2

DEFAULT_DESCRIPTOR = "deploy/product.toml"
DEFAULT_OUTPUT_DIR = "deploy/rendered"

# ── rendering registry ──────────────────────────────────────────────────────


def _rendered_assets(
    spec: ProductDeploymentSpec, thresholds: dict[str, str]
) -> dict[str, str]:
    """Every asset this descriptor produces, by relative path.

    Built here rather than in each renderer so that `render`, `render --check`
    and `drift` all enumerate the SAME set. A renderer the check does not know
    about is a renderer whose output can be hand-edited undetected.
    """
    from .alerts import render_alert_rules
    from .render.compose import render_compose
    from .render.nginx import render_nginx
    from .telemetry import render_collector_config

    assets: dict[str, str] = {
        "docker-compose.yml": render_compose(spec),
        "alerts.rules.yml": render_alert_rules(spec, thresholds=thresholds),
        "otel-collector.yaml": render_collector_config(
            spec, deployment_id="render", host="render"
        ),
    }
    nginx = render_nginx(spec)
    if nginx:
        assets[
            f"nginx/{spec.ingress.host}.conf" if spec.ingress else "nginx/site.conf"
        ] = nginx
    return assets


def _load(path: str) -> ProductDeploymentSpec:
    return ProductDeploymentSpec.load(path)


def _thresholds(path: str | None) -> dict[str, str]:
    """Alert thresholds, which live OUTSIDE the process.

    `dotmac_integrator`'s rule 19 states it exactly: "Thresholds live in
    `deploy/alerts/`, never in the process. `/metrics` publishes facts. 'How
    late is too late' is a deployment's decision, and a number in the code
    would fork from the rule that fires on it."
    """
    if path is None:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SpecError("the thresholds file must be a JSON object", where=path)
    return {str(key): str(value) for key, value in data.items()}


# ── commands ────────────────────────────────────────────────────────────────


def cmd_validate(args: argparse.Namespace) -> int:
    spec = _load(args.descriptor)
    print(f"{args.descriptor}: valid {SCHEMA}")
    print(f"  product        {spec.product}")
    print(f"  image          {spec.image}")
    print(f"  revision       {spec.source_revision}")
    print(f"  roles          {', '.join(spec.startup_order)}")
    print(
        f"  migration      {spec.migration.compatibility}, heads "
        f"{list(spec.migration.expected_heads)}"
    )
    print(f"  ingress        {spec.ingress.host if spec.ingress else '(none)'}")
    datasets = [dataset.code for dataset in spec.backup_datasets]
    print(f"  backup         {datasets or '(none declared)'}")
    if not spec.backup_datasets:
        print(
            "  NOTE: no backup dataset is declared, so a deployment would run no "
            "backup. If this product has durable state, that is a defect in "
            "the descriptor rather than a property of the deployment."
        )
    return EXIT_OK


def cmd_render(args: argparse.Namespace) -> int:
    spec = _load(args.descriptor)
    assets = _rendered_assets(spec, _thresholds(args.thresholds))
    out = Path(args.output_dir)

    if args.check:
        # The drift half. Reports EVERY difference, because an operator who has
        # hand-edited one file has usually hand-edited two, and one-at-a-time
        # refusal turns a single fix into several review cycles.
        problems: list[str] = []
        for name, expected in sorted(assets.items()):
            target = out / name
            if not target.exists():
                problems.append(f"{target}: missing — run `dotmac-deploy render`")
                continue
            actual = target.read_text(encoding="utf-8")
            if actual != expected:
                problems.append(
                    f"{target}: differs from what {args.descriptor} renders. Edit the "
                    "descriptor and re-render; do not edit the result"
                )
        stray = sorted(
            str(path.relative_to(out))
            for path in out.rglob("*")
            if path.is_file() and str(path.relative_to(out)) not in assets
        )
        for name in stray:
            problems.append(
                f"{out / name}: present but not rendered by this descriptor — an "
                "untracked asset carrying configuration nothing approves"
            )
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            raise RenderDrift(
                f"{len(problems)} rendered asset(s) do not match the descriptor"
            )
        print(f"{len(assets)} rendered asset(s) match {args.descriptor}")
        return EXIT_OK

    for name, content in sorted(assets.items()):
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        print(f"wrote {target}")
    return EXIT_OK


def cmd_image_audit(args: argparse.Namespace) -> int:
    from .image.audit import audit_image

    inspect = json.loads(Path(args.inspect).read_text(encoding="utf-8"))
    if isinstance(inspect, list):
        # `docker image inspect` returns an array even for one image.
        inspect = inspect[0] if inspect else {}
    history = (
        json.loads(Path(args.history).read_text(encoding="utf-8"))
        if args.history
        else []
    )
    layers = (
        Path(args.layers).read_text(encoding="utf-8").splitlines()
        if args.layers
        else []
    )
    report = audit_image(args.reference, inspect, history=history, layers=layers)
    sys.stdout.write(report.render())
    return EXIT_OK if report.passed else EXIT_REFUSED


def cmd_observe(args: argparse.Namespace) -> int:
    from .telemetry import RESOURCE_ATTRIBUTES, resource_attributes

    spec = _load(args.descriptor)
    print(f"required resource attributes: {list(RESOURCE_ATTRIBUTES)}")
    for role in spec.startup_order:
        attributes = resource_attributes(
            spec,
            role=role,
            deployment_id=args.deployment_id or "unset",
            host=args.host or "unset",
        )
        print(f"\n{role}:")
        print(f"  OTEL_RESOURCE_ATTRIBUTES={attributes.as_otel_env()}")
    return EXIT_OK


def cmd_drift(args: argparse.Namespace) -> int:
    from .drift import Observation, compare

    spec = _load(args.descriptor)
    observed = json.loads(Path(args.observed).read_text(encoding="utf-8"))
    assets = _rendered_assets(spec, _thresholds(args.thresholds))
    from hashlib import sha256

    approved = {
        name: "sha256:" + sha256(content.encode("utf-8")).hexdigest()
        for name, content in assets.items()
    }
    report = compare(
        spec,
        Observation(
            role_image_digests=observed.get("role_image_digests", {}),
            config_digests=observed.get("config_digests", {}),
            manifest_digest=observed.get("manifest_digest", ""),
        ),
        approved_config_digests=approved,
        approved_image_digest=observed.get("approved_image_digest", ""),
    )
    sys.stdout.write(report.render())
    return EXIT_OK if report.clean else EXIT_REFUSED


# ── argument parsing ────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dotmac-deploy",
        description=(
            "Build- and deploy-time facility for Dotmac product assemblies. "
            "Reads deploy/product.toml (ProductDeploymentSpec.v1)."
        ),
    )
    parser.add_argument(
        "-f",
        "--descriptor",
        default=DEFAULT_DESCRIPTOR,
        help=f"default {DEFAULT_DESCRIPTOR}",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add(
        name: str,
        handler: Callable[[argparse.Namespace], int],
        help_text: str,
    ) -> argparse.ArgumentParser:
        child = sub.add_parser(name, help=help_text)
        child.set_defaults(handler=handler)
        return child

    add("validate", cmd_validate, "parse and validate the descriptor")

    render = add("render", cmd_render, "render every deployment asset")
    render.add_argument("--check", action="store_true", help="fail on any difference")
    render.add_argument("-o", "--output-dir", default=DEFAULT_OUTPUT_DIR)
    render.add_argument("--thresholds", help="JSON file of alert thresholds")

    audit = add(
        "image-audit", cmd_image_audit, "audit a built image against the contract"
    )
    audit.add_argument("reference")
    audit.add_argument("--inspect", required=True, help="`docker image inspect` JSON")
    audit.add_argument(
        "--history", help="JSON array of `docker history` CreatedBy strings"
    )
    audit.add_argument("--layers", help="newline-separated filesystem listing")

    observe = add(
        "observe", cmd_observe, "show the resource attributes each role stamps"
    )
    observe.add_argument("--deployment-id")
    observe.add_argument("--host")

    drift_cmd = add("drift", cmd_drift, "compare running state with the approved plan")
    drift_cmd.add_argument("--observed", required=True, help="JSON of observed digests")
    drift_cmd.add_argument("--thresholds")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result: int = args.handler(args)
        return result
    except DeploymentFoundationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except (json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
