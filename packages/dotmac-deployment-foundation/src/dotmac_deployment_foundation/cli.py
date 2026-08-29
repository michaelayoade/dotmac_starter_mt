"""``dotmac-deploy`` — the one command every product's CI and host runs.

Three design choices are load-bearing:

**Ordinary commands do not mutate a host.** ``deploy`` and ``rollback`` print
their plans, and their former ``--execute`` spelling now refuses. The sole
mutation path is ``execute-authorized``, invoked by the independently verified
launcher with an exact ``DeploymentExecutionEnvelope.v1`` and sealed launch
context.

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
    spec: ProductDeploymentSpec,
    thresholds: dict[str, str],
    *,
    configuration_digest: str,
) -> dict[str, str]:
    """Every asset this descriptor produces, by relative path.

    Built here rather than in each renderer so that `render`, `render --check`
    and `drift` all enumerate the SAME set. A renderer the check does not know
    about is a renderer whose output can be hand-edited undetected.
    """
    from .alerts import render_alert_rules
    from .execution import ApplicationReleaseIdentityV1
    from .render.compose import render_compose
    from .render.nginx import render_nginx
    from .telemetry import render_collector_config

    identity = ApplicationReleaseIdentityV1(
        image_digest=spec.image_digest,
        source_revision=spec.source_revision,
        configuration_digest=configuration_digest,
        manifest_digest=spec.manifest_digest,
    )
    assets: dict[str, str] = {
        "docker-compose.yml": render_compose(spec, release_identity=identity),
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
            "  NOTE: no backup dataset is declared, so `dotmac-deploy deploy` will "
            "run no backup. If this product has durable state, that is a defect in "
            "the descriptor rather than a property of the deployment."
        )
    return EXIT_OK


def cmd_render(args: argparse.Namespace) -> int:
    spec = _load(args.descriptor)
    from .controller import digest_file

    assets = _rendered_assets(
        spec,
        _thresholds(args.thresholds),
        configuration_digest=digest_file(Path(args.descriptor)),
    )
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


def cmd_plan(args: argparse.Namespace) -> int:
    from .controller import (
        deployment_plan_digest,
        deployment_plan_document,
        digest_file,
    )
    from .engine.plan import build_plan, format_plan

    spec = _load(args.descriptor)
    plan = build_plan(spec, previous_image=args.previous_image or "")
    if args.json:
        document = deployment_plan_document(plan)
        document["plan_digest"] = deployment_plan_digest(plan)
        document["configuration_digest"] = digest_file(Path(args.descriptor))
        print(json.dumps(document, sort_keys=True, separators=(",", ":")))
    else:
        sys.stdout.write(format_plan(plan))
    return EXIT_OK


def cmd_deploy(args: argparse.Namespace) -> int:
    from .engine.plan import build_plan, format_plan

    spec = _load(args.descriptor)
    plan = build_plan(
        spec,
        previous_image=args.previous_image or "",
        skip_backup=args.skip_backup,
        skip_backup_reason=args.skip_backup_reason or "",
    )
    sys.stdout.write(format_plan(plan))
    if not args.execute:
        print(
            "\nDRY RUN. Nothing was executed. Deployment requires an authorized "
            "DeploymentExecutionEnvelope.v1 through the independently verified "
            "controller launcher."
        )
        return EXIT_OK

    raise SpecError(
        "direct deploy --execute is disabled; use the independently verified "
        "controller launcher and DeploymentExecutionEnvelope.v1"
    )


def cmd_execute_authorized(args: argparse.Namespace) -> int:
    """Run one Foundation-owned plan through the independent controller."""

    from .controller import (
        CONTROLLER_LOCK_ROOT,
        CONTROLLER_STATE_ROOT,
        ControllerStateStore,
        DockerCurrentReleaseObserver,
        execute_authorized,
    )
    from .execution import (
        DeploymentExecutionEnvelopeV1,
        GitRevisionOracle,
        provenance_from_launch_context,
        scrub_controller_provenance_environment,
    )

    if (
        not args.execution_envelope
        or not args.staged_application_root
        or args.launch_context_fd is None
    ):
        raise SpecError(
            "execute-authorized requires --execution-envelope and "
            "--staged-application-root plus an inherited launch context from "
            "the independent launcher"
        )
    staged_root = Path(args.staged_application_root).resolve()
    descriptor = Path(args.descriptor)
    if not descriptor.is_absolute():
        descriptor = staged_root / descriptor
    descriptor = descriptor.resolve()
    if not descriptor.is_relative_to(staged_root):
        raise SpecError("the product descriptor must be inside the staged application")
    envelope = DeploymentExecutionEnvelopeV1.load(args.execution_envelope)

    authorizer_repo = Path(args.authorizer_repo).resolve()
    application_history_repo = Path(args.application_history_repo).resolve()
    if authorizer_repo == staged_root or authorizer_repo.is_relative_to(staged_root):
        raise SpecError("authorizer checkout must be outside the staged application")
    if (
        application_history_repo == staged_root
        or application_history_repo.is_relative_to(staged_root)
    ):
        raise SpecError(
            "application-history checkout must be outside the staged application"
        )
    if application_history_repo == authorizer_repo:
        raise SpecError("authorizer and application-history checkouts must be distinct")

    actual_controller, actual_authorizer, authorization_evidence = (
        provenance_from_launch_context(
            args.launch_context_fd,
            staged_application_root=staged_root,
        )
    )
    if authorization_evidence.execution_envelope_digest != envelope.envelope_digest:
        raise SpecError(
            "signed authorization evidence does not bind this execution envelope"
        )
    scrub_controller_provenance_environment()
    state_store = ControllerStateStore(
        CONTROLLER_STATE_ROOT,
        product=envelope.product,
        target_ref=envelope.target_ref,
    )
    observer = DockerCurrentReleaseObserver(
        docker_binary=Path(args.docker_bin),
        product=envelope.product,
        state_store=state_store,
    )
    result = execute_authorized(
        envelope=envelope,
        descriptor_path=descriptor,
        actual_controller=actual_controller,
        actual_authorizer=actual_authorizer,
        authorization_evidence=authorization_evidence,
        revision_oracle=GitRevisionOracle(
            repository=application_history_repo,
            git_binary=Path(args.git_bin),
            snapshot=authorization_evidence.application_history,
        ),
        observer=observer,
        state_store=state_store,
        staged_application_root=staged_root,
        lock_directory=CONTROLLER_LOCK_ROOT,
    )
    output = {
        "allowed": result.decision.allowed,
        "relation": result.decision.relation.value,
        "reason_code": result.decision.reason_code,
        "overridden": result.decision.overridden,
        "blockers": list(result.decision.blockers),
        "deployment_succeeded": (
            None if result.outcome is None else result.outcome.succeeded
        ),
        "state_path": None if result.state_path is None else str(result.state_path),
    }
    print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    return EXIT_OK if result.decision.allowed else EXIT_REFUSED


def cmd_backup(args: argparse.Namespace) -> int:
    from .backup import verification_plan

    spec = _load(args.descriptor)
    if not spec.backup_datasets:
        print(
            "no backup dataset is declared in the descriptor. That is a claim "
            "that nothing needs backing up — if the product has durable state, "
            "fix the descriptor rather than this command",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    for dataset in spec.backup_datasets:
        checks = verification_plan(dataset)
        print(f"{dataset.code} ({dataset.kind}, material {dataset.material})")
        print(f"  retention        {dataset.retention_days} days")
        print(f"  encryption       {dataset.encryption}")
        print(f"  offsite          {dataset.offsite or '(none declared)'}")
        print(f"  verified when    {checks.describe()}")
        print(
            f"  restore proof    at most {dataset.restore_proof_max_age_days} days old"
        )
    return EXIT_OK


def cmd_restore_rehearsal(args: argparse.Namespace) -> int:
    from .backup import restore_rehearsal

    spec = _load(args.descriptor)
    codes = [args.dataset] if args.dataset else [d.code for d in spec.backup_datasets]
    if not codes:
        print("no backup dataset is declared", file=sys.stderr)
        return EXIT_REFUSED
    for code in codes:
        rehearsal = restore_rehearsal(spec, code)
        print(rehearsal.describe())
        print(
            "  the target MUST be disposable and destroyed afterwards. A "
            "rehearsal that restores anywhere the product can reach is not a "
            "rehearsal, it is a restore"
        )
    return EXIT_OK


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


def cmd_ingress_policy(args: argparse.Namespace) -> int:
    """The NON-MUTATING projection: what this descriptor exposes, and its digest.

    Nothing here touches a host, a socket or a firewall. It is the value a
    product's CI prints for review and the value `dotmac-deployment-control`
    places inside `desired_spec` so that ingress enters `plan_digest`.
    """
    from .ingress import PROVIDERS
    from .policy import (
        build_edge_plan,
        build_firewall_plan,
        ingress_policy_document,
        public_endpoint_tokens,
    )

    spec = _load(args.descriptor)
    canonical = spec.to_canonical_document()
    document = ingress_policy_document(spec)
    digest = canonical.sha256_digest()
    if args.format == "digest":
        print(digest)
        return EXIT_OK
    if args.format == "json":
        print(json.dumps({"digest": digest, "document": document}, indent=2))
        return EXIT_OK

    print(f"{document['schema']} (facility {canonical.foundation_version})")
    print(f"{canonical.schema} digest: {digest}")
    print("\npublications:")
    if not document["publications"]:
        print("  (none declared)")
    for publication in document["publications"]:
        # The MATERIAL name, because the document holds no resolved address.
        # A loopback publication needs none — its literal is derived from
        # exposure plus family — so it prints as `derived`.
        binds = (
            ", ".join(
                f"{entry['family']}={entry['material'] or 'derived'}"
                for entry in publication["binds"]
            )
            or "no socket"
        )
        print(
            f"  {publication['role']} {publication['host_port']}"
            f"/{publication['protocol']} exposure={publication['exposure']} "
            f"family={publication['address_family']} -> {binds}"
        )
    print("\nedge:")
    edge = document["edge"]
    if not edge["declared"]:
        print("  (none declared)")
    else:
        print(f"  host={edge['host']} exposure={edge['exposure']}")
        for endpoint in build_edge_plan(spec):
            print(f"    {endpoint.path} -> {endpoint.role}:{endpoint.upstream_port}")
    print("\nderived firewall plan (defense in depth, never the primary control):")
    rules = build_firewall_plan(spec)
    if not rules:
        print("  (nothing routable to filter)")
    for rule in rules:
        print(f"  {rule.family} {rule.chain}: {rule.render()}")
    print("\npublic endpoints:")
    for token in public_endpoint_tokens(spec):
        print(f"  {token}")
    if not public_endpoint_tokens(spec):
        print("  (none)")
    if args.providers:
        print("\nprovider capability matrix:")
        for code in sorted(PROVIDERS):
            capability = PROVIDERS[code]
            print(
                f"  {code}: families={sorted(capability.families)} "
                f"protocols={sorted(capability.protocols)} "
                f"authentication={sorted(capability.authentications)} "
                f"source_policy={capability.enforces_source_policy} "
                f"tls={capability.enforces_tls}"
            )
            print(f"      {capability.note}")
    return EXIT_OK


def cmd_exposure_verify(args: argparse.Namespace) -> int:
    """Verify RECORDED host output against the declared exposure.

    Off-host on purpose. The inputs are the text an operator already has —
    `ss -tlnp`, a process listing, `iptables-save` and `ip6tables-save` — so
    the same verifier that runs during an apply can be replayed against an
    incident's pasted output months later, with no host present and nothing
    mutated.
    """
    from .exposure import Severity, observation_from_text, verify_exposure

    spec = _load(args.descriptor)
    saves: dict[str, str] = {}
    if args.iptables_v4:
        saves["ipv4"] = Path(args.iptables_v4).read_text(encoding="utf-8")
    if args.iptables_v6:
        saves["ipv6"] = Path(args.iptables_v6).read_text(encoding="utf-8")
    observation = observation_from_text(
        socket_listing=(
            Path(args.sockets).read_text(encoding="utf-8") if args.sockets else ""
        ),
        process_listing=(
            Path(args.processes).read_text(encoding="utf-8") if args.processes else ""
        ),
        iptables_save=saves,
        closed_port_behaviour=args.closed_port_behaviour,
    )
    report = verify_exposure(spec, observation)
    print(f"descriptor digest: {report.descriptor_digest}")
    for token in report.verified:
        print(f"  ok       {token}")
    for finding in report.findings:
        marker = "REFUSED" if finding.severity is Severity.REFUSE else "note   "
        print(f"  {marker}  [{finding.code}] {finding.detail}")
    if not report.ok:
        return EXIT_REFUSED
    if not report.verified:
        # Not a pass. A verifier with nothing to verify reports green for the
        # wrong reason, and "no findings" over an empty binding set is exactly
        # the shape of a check that has silently stopped looking.
        print(
            "error: no declared publication was verified — the descriptor "
            "declares none, or the observation was empty. Green over an empty "
            "set is not a proof",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    return EXIT_OK


def cmd_drift(args: argparse.Namespace) -> int:
    from .drift import Observation, compare

    spec = _load(args.descriptor)
    observed = json.loads(Path(args.observed).read_text(encoding="utf-8"))
    from .controller import digest_file

    assets = _rendered_assets(
        spec,
        _thresholds(args.thresholds),
        configuration_digest=digest_file(Path(args.descriptor)),
    )
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


def cmd_rollback(args: argparse.Namespace) -> int:
    from .engine.plan import build_plan, steps_for_rollback

    spec = _load(args.descriptor)
    plan = build_plan(spec, previous_image=args.previous_image or "")
    steps = steps_for_rollback(plan)
    if not steps:
        print(f"rollback REFUSED: {plan.rollback_reason}", file=sys.stderr)
        if not spec.migration.is_online:
            print(
                "Recovery for a maintenance_required release is a restore from "
                "the pre-migration backup. A migration is never automatically "
                "downgraded: `downgrade()` correctness is an assumption no "
                "deployment tool may make on a production database.",
                file=sys.stderr,
            )
        return EXIT_REFUSED
    print(f"rollback permitted: {plan.rollback_reason}\n")
    for index, step in enumerate(steps, start=1):
        print(f"{index}. {step.kind.value} — {step.description}")
    if not args.execute:
        print(
            "\nDRY RUN. Nothing was executed. Rollback requires an exact typed "
            "override in DeploymentExecutionEnvelope.v1 through the independently "
            "verified controller launcher."
        )
        return EXIT_OK

    raise SpecError(
        "direct rollback --execute is disabled; a source rollback requires an "
        "exact DeploymentExecutionEnvelope.v1 override through the independent "
        "controller, and migration downgrade remains forbidden"
    )


def cmd_preflight(args: argparse.Namespace) -> int:
    from .engine.plan import build_plan

    spec = _load(args.descriptor)
    plan = build_plan(spec)
    print(f"{len(plan.gate_steps)} gate(s) run before anything is mutated:\n")
    for index, step in enumerate(plan.gate_steps, start=1):
        print(f"{index}. {step.kind.value}: {step.description}")
        if step.command:
            print(f"   $ {' '.join(step.command)}")
    return EXIT_OK


def cmd_migrate(args: argparse.Namespace) -> int:
    spec = _load(args.descriptor)
    migration = spec.migration
    print(f"command          {' '.join(migration.command)}")
    print(f"owner material   {migration.owner_material}")
    print(f"expected heads   {list(migration.expected_heads)}")
    print(f"compatibility    {migration.compatibility}")
    print(
        f"lock             {migration.lock_timeout_seconds}s, "
        f"{migration.lock_retries} attempt(s), retried ONLY on lock contention"
    )
    if not migration.is_online:
        print(
            "\nThis release is maintenance_required. Ingress, the application, "
            "every worker and the scheduler stop BEFORE DDL, and the previous "
            "image is NOT a valid rollback target afterwards."
        )
    return EXIT_OK


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
    parser.add_argument(
        "--execution-envelope",
        help="DeploymentExecutionEnvelope.v1 supplied by the independent launcher",
    )
    parser.add_argument(
        "--staged-application-root",
        help="application checkout being judged; never the controller import root",
    )
    parser.add_argument("--launch-context-fd", type=int, help=argparse.SUPPRESS)
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

    add("preflight", cmd_preflight, "show the gates that run before any mutation")
    add("migrate", cmd_migrate, "show the migration contract")
    add("backup", cmd_backup, "show the backup policy and what verification means")

    rehearse = add(
        "restore-rehearsal", cmd_restore_rehearsal, "show the restore proof required"
    )
    rehearse.add_argument("--dataset")

    plan = add("plan", cmd_plan, "build and print the ordered deployment plan")
    plan.add_argument("--previous-image")
    plan.add_argument("--json", action="store_true")

    deploy = add(
        "deploy",
        cmd_deploy,
        "show the deployment plan; direct execution is refused",
    )
    deploy.add_argument("--previous-image")
    deploy.add_argument(
        "--execute",
        action="store_true",
        help="compatibility flag that always refuses; use the verified launcher",
    )
    deploy.add_argument("--skip-backup", action="store_true")
    deploy.add_argument("--skip-backup-reason", default="")

    authorized = add(
        "execute-authorized",
        cmd_execute_authorized,
        "internal authenticated-launcher entrypoint; direct invocation is refused",
    )
    authorized.add_argument("--authorizer-repo", required=True)
    authorized.add_argument("--application-history-repo", required=True)
    authorized.add_argument("--git-bin", required=True)
    authorized.add_argument("--docker-bin", required=True)

    policy = add(
        "ingress-policy",
        cmd_ingress_policy,
        "show the declared exposure contract, its plans and its digest",
    )
    policy.add_argument(
        "--format",
        default="text",
        choices=["text", "json", "digest"],
        help="`json` is the canonical document; `digest` is what a plan carries",
    )
    policy.add_argument(
        "--providers",
        action="store_true",
        help="also print the provider capability matrix",
    )

    verify = add(
        "exposure-verify",
        cmd_exposure_verify,
        "check RECORDED host output against the declared exposure",
    )
    verify.add_argument("--sockets", help="`ss -tlnp` output")
    verify.add_argument("--processes", help="a process listing containing docker-proxy")
    verify.add_argument("--iptables-v4", help="`iptables-save` output")
    verify.add_argument("--iptables-v6", help="`ip6tables-save` output")
    verify.add_argument(
        "--closed-port-behaviour",
        default="unknown",
        choices=["unknown", "drop", "reset"],
        help=(
            "how this host answers a closed port. On a DROPPING host an "
            "external probe cannot tell loopback-bound from "
            "wildcard-bound-and-dropped, so the conclusion stays inconclusive "
            "without on-host socket evidence"
        ),
    )

    observe = add(
        "observe", cmd_observe, "show the resource attributes each role stamps"
    )
    observe.add_argument("--deployment-id")
    observe.add_argument("--host")

    drift_cmd = add("drift", cmd_drift, "compare running state with the approved plan")
    drift_cmd.add_argument("--observed", required=True, help="JSON of observed digests")
    drift_cmd.add_argument("--thresholds")

    rollback = add(
        "rollback", cmd_rollback, "show the rollback plan, or why it is refused"
    )
    rollback.add_argument("--previous-image")
    rollback.add_argument(
        "--execute",
        action="store_true",
        help="compatibility flag that always refuses; use the verified launcher",
    )
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
