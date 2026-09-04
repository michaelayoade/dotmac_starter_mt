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
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to a checker
    from .authorization import ExecutionGrant
    from .engine.run import DeploymentOutcome, Effects
    from .execution_bindings import ExecutionBindings
    from .execution_plan import HostPrestateV1
    from .exposure import VerificationReport

from .authorization import OPERATIONS
from .errors import (
    DeploymentFoundationError,
    PreconditionFailed,
    RenderDrift,
    SpecError,
)
from .spec import ProductDeploymentSpec

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2

DEFAULT_DESCRIPTOR = "deploy/product.toml"
DEFAULT_OUTPUT_DIR = "deploy/rendered"
DEFAULT_DEPLOY_DIR = "."
# The only provider this package ships (`providers/compose_host.py`) — the
# dedicated-VM Docker Compose profile. `--provider` is still a real flag,
# not a decoration: `_provider_name` refuses an unknown value as a usage error
# naming the entry-point discovery seam rather than silently falling back to
# this one, so a typo fails loudly instead of deploying through the wrong
# provider.
PROVIDER_COMPOSE_HOST = "compose-host"


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


def _declared_provider_names() -> tuple[str, ...]:
    from .execution_bindings import declared_provider_names

    return declared_provider_names()


def _provider_name(value: str) -> str:
    """Parse a provider name without importing the provider implementation.

    ``argparse.choices`` says only "invalid choice" after an installed
    bindings distribution disappears.  That hides the repair: restore the
    distribution declaring the execution-bindings entry point.  Keep the
    metadata-only enumeration, but make the refusal name the discovery seam.
    """
    from .execution_bindings import ENTRY_POINT_GROUP

    allowed = (PROVIDER_COMPOSE_HOST, *_declared_provider_names())
    if value not in allowed:
        available = ", ".join(repr(name) for name in allowed)
        raise argparse.ArgumentTypeError(
            f"{value!r} is not declared by the {ENTRY_POINT_GROUP!r} entry-point "
            f"group; available providers: {available}"
        )
    return value


def _application_profile_digest() -> str:
    """The candidate's profile digest, or `""` when it declares none.

    REPORT-ONLY, and the distinction matters. This does not check whether the
    profile is complete, whether its bindings resolve, or whether its concerns
    are satisfied — ADR 0039 § 4's refusal is not composed into any deployment
    path, because that record stages the refusal per concern rather than
    switching it on for all thirteen at once.

    This paragraph used to give a count of how many concerns the fleet could
    bind. It was withdrawn on 2026-09-04: a maturity determination about the
    estate is not a fact this facility owns, can re-derive, or would learn of
    when it changed. `application_profile.py`'s own docstring carries the
    withdrawal and the reasoning.

    What it DOES do is carry the digest into the plan so it travels with the
    release and can be read back and compared (§ 8). An assembly declaring no
    profile yields `""`, which is a stated value, not a failure.

    A discovery REFUSAL still refuses, and that is not the completeness gate in
    disguise: two distributions declaring the profile group, or one that fails
    to import, is a broken deployment environment. Refusing there is the same
    judgement `discover_bindings` already makes, about the same kind of fact.
    """
    from .application_profile import discover_profile, profile_digest

    found = discover_profile()
    if found is None:
        return ""
    return profile_digest(found.as_document())


def _load_bindings(args: argparse.Namespace) -> ExecutionBindings | None:
    """Discover the assembly's bindings, once, on the execute path only.

    An embedder that already set `args.execution_bindings` wins — it IS the
    assembly, and discovering around it would let an installed distribution
    shadow the very thing that is embedding us.
    """
    supplied = getattr(args, "execution_bindings", None)
    if supplied is not None:
        return supplied
    from .execution_bindings import discover_bindings

    return discover_bindings()


def _build_effects(
    spec: ProductDeploymentSpec, args: argparse.Namespace, *, bindings=None
) -> Effects:
    """The `Effects` implementation `--execute` runs the plan against.

    `args.provider` is constrained by `_provider_name` (`build_parser`), so an
    unrecognised value never reaches here — it fails as a usage error before
    the descriptor is even loaded, naming the metadata group that must declare
    it. This function exists only to keep the one `if` legible as the facility
    grows a second provider, and to keep the import lazy like every other
    `cmd_*` handler in this module.
    """
    if args.provider != PROVIDER_COMPOSE_HOST:
        # A DISCOVERED provider. The menu offered this name from metadata; the
        # load already happened in `_load_bindings`, so a name with no bindings
        # behind it here means the environment changed between parse and use.
        if bindings is None or str(bindings.provider) != str(args.provider):
            raise PreconditionFailed(
                f"--provider {args.provider!r} was offered from declared entry "
                "points, but no loaded execution bindings answer to it. The "
                "environment changed between parsing and use; re-run, and if "
                "this persists the bindings distribution is broken"
            )
        if bindings.build_effects is None:
            raise PreconditionFailed(
                f"the execution bindings for {args.provider!r} declare no "
                "effects factory. They inject verifiers only, so select the "
                f"in-package provider ({PROVIDER_COMPOSE_HOST!r}) instead"
            )
        return bindings.build_effects(spec, Path(args.deploy_dir))
    if args.provider == PROVIDER_COMPOSE_HOST:
        from .providers.compose_host import ComposeHostEffects
        from .toolchain import DEFAULT_TOOLS, resolve_tool

        # The production constructor, and therefore where the filesystem half
        # of the tool pinning belongs. `ComposeHostEffects` already refuses a
        # non-absolute path on its own; this additionally proves the binary at
        # that path exists, is executable, and cannot be replaced by anyone but
        # its owner — which needs a real filesystem and so cannot live in a
        # constructor that unit tests drive with a scripted fake runner.
        tools = {
            name: resolve_tool(
                getattr(args, f"{name}_bin", "") or default, what=f"{name}_bin"
            )
            for name, default in DEFAULT_TOOLS.items()
        }
        return ComposeHostEffects(
            spec,
            Path(args.deploy_dir),
            docker_bin=tools["docker"],
            git_bin=tools["git"],
        )
    raise SpecError(
        f"unknown provider {args.provider!r}"
    )  # pragma: no cover - unreachable


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
    print(f"{args.descriptor}: valid {spec.descriptor_schema}")
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


def cmd_plan(args: argparse.Namespace) -> int:
    from .engine.plan import build_plan, format_plan

    spec = _load(args.descriptor)
    plan = build_plan(spec, previous_image=args.previous_image or "")
    sys.stdout.write(format_plan(plan))
    return EXIT_OK


def _require_grant(
    args: argparse.Namespace,
    spec: ProductDeploymentSpec,
    operation: str,
    *,
    bindings=None,
) -> ExecutionGrant:
    """Turn `--authorization` into an :class:`ExecutionGrant`, or refuse.

    `--execute` on its own reaches here and leaves through the first branch.
    That is the point of the whole change: the flag says what the operator
    INTENDS, and intent has never been authorization. What makes this a control
    rather than a nag is that there is no way past it — `Executor` cannot be
    constructed without the grant this returns.

    Raises `PreconditionFailed` rather than returning a sentinel so a caller
    cannot accidentally treat "refused" as "granted" by forgetting to check.
    """
    from .authorization import authorize
    from .provenance import verify_authorization

    target = getattr(args, "target", "") or ""
    if not target:
        raise PreconditionFailed(
            "--execute requires --target naming the host being deployed to. "
            "It is stated separately from the receipt on purpose: if this "
            "came from `receipt.target_ref`, the check below would compare "
            "the receipt with itself and approve every host"
        )
    path = getattr(args, "authorization", "") or ""
    if not path:
        raise PreconditionFailed(
            "--execute requires --authorization: a Platform CP authorization "
            "receipt naming this descriptor, this target and the "
            f"{operation!r} operation. --execute alone is the operator saying "
            "they meant it, which is not the same as being permitted, and this "
            "facility never authorizes its own deployments"
        )
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise PreconditionFailed(
            f"cannot read the authorization receipt {path}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise PreconditionFailed(
            f"the authorization receipt {path} is not valid JSON: {exc}"
        ) from exc
    # RAW MATERIAL ONLY THROUGH A VERIFIER, and this facility ships none.
    #
    # The same line `recovery_receipts` already draws, one layer up and for the
    # same reason: a zero-dependency build runner (ADR-0009/ADR-0070) must not
    # ship a weak stdlib signature substitute, because a weak verifier reads as
    # coverage. What stood here was
    # `AuthorizationReceipt.from_document(document)` — a public classmethod
    # over a JSON file, which proves the document has the right KEYS and says
    # nothing about whether Control signed it. Parsing is not attestation.
    #
    # So the CLI refuses rather than self-attesting, and an assembly embedding
    # `Executor` supplies the verifier its trust roots live in. A `--execute`
    # that quietly accepted an unsigned receipt would be the bypass this whole
    # contract exists to close.
    verifier = getattr(args, "authorization_verifier", None)
    if verifier is None and bindings is not None:
        verifier = bindings.authorization_verifier
    if verifier is None:
        from .execution_bindings import ENTRY_POINT_GROUP

        raise PreconditionFailed(
            f"the authorization receipt {path} was read but nothing can attest "
            "it: this facility declares zero runtime dependencies and ships no "
            "signature verifier, and parsing a JSON document is not "
            "verification. Install the assembly's bindings distribution (one "
            f"{ENTRY_POINT_GROUP!r} entry point) or embed Executor from an "
            "assembly that supplies an AuthorizationVerifier directly. "
            "Refusing to execute on material this process cannot authenticate"
        )
    verified = verify_authorization(document, verifier=verifier)
    return authorize(
        verified=verified,
        operation=operation,
        descriptor_digest=spec.to_canonical_document().sha256_digest(),
        target=target,
        # The clock is read HERE, at the adapter, and nowhere below it. An
        # expiry check whose `now` came from inside the library could not be
        # moved by a test, and an expiry nobody can test is a field.
        now=datetime.now(UTC),
    )


def _recovery_receipts(pairs: list[str]) -> dict[str, object]:
    """Parse `DATASET=PATH` pairs into envelopes, refusing anything ambiguous.

    A malformed pair is a REFUSAL rather than a skipped entry: an operator who
    typed the flag intends a receipt to be supplied, and silently dropping it
    would produce "no recovery receipt was supplied" for a receipt that is
    sitting right there on the command line.
    """
    receipts: dict[str, object] = {}
    for pair in pairs:
        dataset, separator, path = pair.partition("=")
        if not separator or not dataset.strip() or not path.strip():
            raise SpecError(
                f"--recovery-receipt {pair!r} is not DATASET=PATH. A receipt "
                "names the dataset it proves; one that did not could satisfy a "
                "gate for data it says nothing about"
            )
        if dataset in receipts:
            raise SpecError(
                f"--recovery-receipt names {dataset!r} twice. Two proofs for one "
                "dataset is a choice, and this facility does not make it"
            )
        receipts[dataset] = json.loads(Path(path).read_text(encoding="utf-8"))
    return receipts


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
        print("\nDRY RUN. Nothing was executed. Re-run with --execute to deploy.")
        return EXIT_OK

    from .engine.lock import deployment_lock
    from .engine.run import Executor
    from .execution_plan import HostPrestateV1, render_execution_plan

    bindings = _load_bindings(args)
    grant = _require_grant(args, spec, "deploy", bindings=bindings)
    effects = _build_effects(spec, args, bindings=bindings)
    # THE MIDDLE TERM, RENDERED AND HANDED OVER. Nothing here chooses the
    # authorized digest -- that rides on the grant, which took it from the
    # attested receipt. This renders the plan the digest is recomputed FROM, so
    # `_require_execution_plan` compares what will run against what Control
    # froze. Before this, `cmd_deploy` passed neither and every deployment ran
    # unbound with an empty digest in its evidence.
    execution_plan = render_execution_plan(
        spec,
        plan,
        target=args.target,
        operation="deploy",
        descriptor_digest=str(spec.to_canonical_document().sha256_digest()),
        # Observed through the SAME effects the run will mutate through, at
        # render time; the executor re-observes at the last moment before
        # mutation and refuses a host that moved in between.
        prestate=HostPrestateV1.from_observations(effects.observe_roles()),
        application_profile_digest=_application_profile_digest(),
    )
    executor = Executor(
        spec,
        effects,
        grant,
        execution_plan=execution_plan,
        # Receipts are HANDED OVER (one `--recovery-receipt CODE=PATH` per
        # externally executed dataset); the VERIFIERS and the trust policy come
        # from the assembly's discovered bindings, because this facility ships
        # no signature code of its own (ADR-0009/ADR-0070) and a weak stdlib
        # substitute would read as coverage. With no bindings installed every
        # verifying step still REFUSES, exactly as before — discovery makes the
        # admit representable, never the refusal weaker.
        recovery_receipts=_recovery_receipts(args.recovery_receipt),
        evidence_policy=bindings.evidence_policy if bindings else None,
        evidence_verifier=bindings.evidence_verifier if bindings else None,
        recovery_verifier=bindings.recovery_verifier if bindings else None,
    )
    # The lock wraps the WHOLE run, not a piece of it: `_do_acquire_lock` and
    # `_do_release_lock` are no-op steps that say so in their own detail text
    # (`engine/run.py`) — a lock released when the first step returns is not
    # a lock, it is a lock-shaped gap between the check and the mutation the
    # 2026-07-12 incident (`engine/lock.py`) actually needed closed.
    with deployment_lock(
        spec.product,
        label=f"dotmac-deploy deploy {plan.image_digest}",
        directory=args.lock_dir,
    ):
        outcome = executor.run(plan)
    print()
    _print_outcome(outcome)
    if outcome.succeeded:
        print(f"\nDEPLOYED {plan.image_digest}")
        return EXIT_OK
    failed = outcome.failed_step.value if outcome.failed_step else "(unknown step)"
    print(
        f"\nDEPLOY REFUSED at {failed}: {outcome.failure}\n"
        f"world mutated: {outcome.mutated}",
        file=sys.stderr,
    )
    return EXIT_REFUSED


def _load_prestate(path: str) -> HostPrestateV1:
    """Read a HostPrestateV1 document from a file, refusing loudly.

    The file form exists because the digest is produced OFF-HOST: Platform CP
    renders the plan it will submit, and the host's state has to travel to it
    as a document. `observe-prestate` (below) is what produces one on the
    host, so the two ends of that trip share one shape and one parser.
    """
    from .execution_plan import HostPrestateV1

    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise PreconditionFailed(
            f"cannot read the prestate file {path}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise PreconditionFailed(
            f"the prestate file {path} is not valid JSON: {exc}"
        ) from exc
    return HostPrestateV1.from_document(document)


def cmd_observe_prestate(args: argparse.Namespace) -> int:
    """Print the host's CURRENT prestate document. Read-only, by construction.

    Run on the target host; feed the output to `execution-plan --prestate` so
    the plan Platform CP submits is bound to the state this host actually had.
    Only `observe_roles` is touched — nothing here can mutate.
    """
    spec = _load(args.descriptor)
    bindings = _load_bindings(args)
    effects = _build_effects(spec, args, bindings=bindings)
    from .execution_plan import HostPrestateV1

    prestate = HostPrestateV1.from_observations(effects.observe_roles())
    sys.stdout.write(
        json.dumps(prestate.as_document(), indent=2, sort_keys=True) + "\n"
    )
    return EXIT_OK


def cmd_execution_plan(args: argparse.Namespace) -> int:
    """Render `FoundationExecutionPlanV1` and print its `ExecutionPlanDigestV1`.

    This is step 1 of the controlled-deployment flow and the only place the
    digest is produced. Platform CP submits what `--format digest` prints,
    verbatim, together with the same `--operation`; Control freezes and signs
    it and never reconstructs the document. `--format json` is the document
    itself, for a reviewer -- Control may store it, and must not re-derive the
    digest from it, because a second canonicalizer is a second answer and two
    answers is exactly the state this contract replaces.
    """
    from .engine.plan import build_plan
    from .execution_plan import render_execution_plan

    spec = _load(args.descriptor)
    plan = build_plan(
        spec,
        previous_image=args.previous_image or "",
        skip_backup=args.skip_backup,
        skip_backup_reason=args.skip_backup_reason or "",
    )
    rendered = render_execution_plan(
        spec,
        plan,
        target=args.target,
        operation=args.operation,
        # Stated by this caller rather than derived inside the renderer, so the
        # canonicalization that produced it is visible at the seam where two
        # canonicalizations diverging is the failure being repaired.
        descriptor_digest=str(spec.to_canonical_document().sha256_digest()),
        # Required, and a FILE rather than an implicit observation: this
        # subcommand runs off-host (Platform CP renders the digest it will
        # submit), so the host's state travels to it as a document produced by
        # `observe-prestate` on the target. An empty {"roles": []} document is
        # the explicit first-deploy claim, not a default.
        prestate=_load_prestate(args.prestate),
        application_profile_digest=_application_profile_digest(),
    )
    if args.format == "digest":
        print(rendered.digest())
        return EXIT_OK
    if args.format == "json":
        sys.stdout.write(rendered.canonical_bytes().decode("ascii") + "\n")
        return EXIT_OK
    print(f"execution plan for {rendered.product} -> {rendered.target}")
    print(f"  operation        {rendered.operation}")
    print(f"  foundation       {rendered.foundation_version}")
    print(f"  image            {rendered.image_reference}")
    print(f"  image digest     {rendered.image_digest}")
    print(f"  source revision  {rendered.source_revision}")
    print(f"  manifest digest  {rendered.manifest_digest}")
    print(f"  descriptor       {rendered.descriptor_digest}")
    print(f"  strategy         {rendered.strategy}")
    print(f"  prestate         {rendered.host_prestate.as_document()['roles']}")
    print(f"  materials        {list(rendered.environment_inventory)} (NAMES only)")
    print(f"  steps            {len(rendered.steps)}")
    print(f"  ExecutionPlanDigestV1  {rendered.digest()}")
    return EXIT_OK


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
        # Printed beside the window rather than merged into it. Cadence decides
        # STALENESS; the window above decides whether recovery has ever been
        # demonstrated. A product taking hourly backups nobody has restored
        # passes the first and fails the second.
        print(f"  backup cadence   every {dataset.expected_backup_interval_seconds}s")
        if dataset.external_executor is not None:
            executor = dataset.external_executor
            print(
                f"  executed by      {executor.kind}:{executor.identifier}"
                f"@{executor.version} (signing key {executor.key_id})"
            )
            print(f"  dataset lineage  {dataset.lineage}")
            print(
                "  NO backup step runs for this dataset. Another party executes "
                "recovery, so the plan carries verify_external_recovery_receipt "
                "instead -- a backup step here would attribute to this product "
                "an act it does not perform"
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
    if not args.execute:
        print(
            "\nCONTRACT ONLY. Nothing was executed. Re-run with --execute "
            "--manifest PATH to drive the restore procedure against the "
            "assembly's recovery session."
        )
        return EXIT_OK
    return _execute_restore_rehearsal(args, spec)


def _execute_restore_rehearsal(
    args: argparse.Namespace, spec: ProductDeploymentSpec
) -> int:
    """Drive `RESTORE_PROCEDURE` against the assembly's recovery session.

    NO `ExecutionGrant`, and the premise is enforceable rather than asserted:
    this executor acts only on a target it created itself through
    `create_fresh_target`, and `RecoveryEffects` has no method that names,
    reaches or mutates a running deployment. `deploy` and `rollback` need a
    grant because `Executor` mutates the product host; this cannot.

    That premise stops exactly where the act does. Recovering a FAILED
    PRODUCTION SYSTEM mutates something that already exists and needs the whole
    chain — grant, replay coordinate, Control settlement, signed result. It is
    a7's, it is not this, and `recover` is deliberately absent from
    `authorization.OPERATIONS` until it exists.
    """
    from .execution_bindings import ENTRY_POINT_GROUP
    from .recovery import load_manifest
    from .recovery_execution import (
        SESSION_ABSENT,
        RecoveryExecutor,
        RecoverySession,
    )

    if not args.manifest:
        raise PreconditionFailed(
            "--execute requires --manifest naming the recovery bundle manifest "
            "to rehearse. The procedure is derived from the bundle, so a "
            "rehearsal with no manifest has nothing to drive"
        )
    manifest = load_manifest(Path(args.manifest).read_text(encoding="utf-8"))

    bindings = _load_bindings(args)
    factory = getattr(bindings, "build_recovery_session", None) if bindings else None
    if factory is None:
        raise PreconditionFailed(
            "a restore rehearsal needs a RecoverySession and this facility "
            "cannot build one: it ships no database driver, opens no socket, "
            "and deliberately parses no bundle bytes, so the SOURCE "
            "CatalogEvidence a restore is compared against can only come from "
            "the assembly that captured it. Install the assembly's bindings "
            f"distribution (one {ENTRY_POINT_GROUP!r} entry point) declaring "
            "`build_recovery_session`. Refusing rather than rehearsing against "
            "an empty source catalogue, which compares clean against an empty "
            "restored one and would report a created database as a proved "
            "recovery",
            code=SESSION_ABSENT,
        )

    session = factory(spec, manifest)
    if not isinstance(session, RecoverySession):
        raise PreconditionFailed(
            f"build_recovery_session returned {type(session).__name__}, not a "
            "RecoverySession. The typed object is the contract; a look-alike "
            "is exactly what the type exists to refuse"
        )

    outcome = RecoveryExecutor(
        spec,
        manifest,
        session.effects,
        source_evidence=session.source_evidence,
        product_image=session.product_image,
    ).run(session.bundle)

    print()
    for step in outcome.steps_completed:
        print(f"  ok    {step}")
    if outcome.destroyed:
        print("  target DESTROYED by the adjudicator's verdict")
    for finding in outcome.findings:
        print(f"  finding: {finding}", file=sys.stderr)
    if outcome.proved:
        print("\nRESTORE REHEARSAL PROVED")
        return EXIT_OK
    print(f"\nRESTORE REHEARSAL REFUSED: {outcome.failure}", file=sys.stderr)
    return EXIT_REFUSED


def cmd_recovery_bundle(args: argparse.Namespace) -> int:
    """Describe the bundle a proved recovery needs, or adjudicate one in hand.

    With no `--manifest` this PRINTS the contract: every component, what its
    digest covers, and what its absence means. With one, it reads the manifest
    and either produces the ordered restore procedure or refuses — and the
    refusal an operator will actually meet is "this is a database-only dump".
    """
    from .recovery import COMPONENTS, REQUIRED_COMPONENTS, load_manifest, restore_plan

    spec = _load(args.descriptor)
    if spec.database is None:
        print(
            "the descriptor declares no [database] contract, so there is nothing "
            "to check a recovery against. A product cannot prove a restore "
            "without first saying which roles, schemas and isolation invariants "
            "its database has",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    if not args.manifest:
        print(f"recovery bundle contract for {spec.product}")
        print(f"  postgres major   {spec.database.postgres_major}")
        print(f"  declared roles   {[r.name for r in spec.database.roles]}")
        print(f"  invariants       {[i.code for i in spec.database.isolation]}")
        print(f"  tablespaces      {spec.database.tablespaces}")
        print("  components:")
        for component in REQUIRED_COMPONENTS:
            detail = COMPONENTS[component]
            print(f"    {component.value}")
            print(f"      digest covers  {detail.covers}")
            print(f"      absent means   {detail.absent_means}")
        return EXIT_OK

    manifest = load_manifest(Path(args.manifest).read_bytes())
    steps = restore_plan(spec, manifest)
    print(f"bundle {manifest.sha256_digest()}")
    print(f"  product          {manifest.product}")
    print(f"  postgres major   {manifest.postgres_major}")
    print(f"  role closure     {sorted(manifest.role_closure)}")
    print(f"  migration heads  {list(manifest.migration_heads)}")
    print("  restore procedure:")
    for step in steps:
        print(f"    {step.order:>2}. {step.what}")
        print(f"        refuses: {step.refuses}")
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


def cmd_exposure_apply(args: argparse.Namespace) -> int:
    """Apply the exposure plan through `ExposureTransaction` — DRY RUN by default.

    Routed through the transaction rather than a script on purpose: apply,
    snapshot, re-observation and rollback then live in one tested code path
    instead of in an operator's habit. A dry run still snapshots and still
    verifies, so it answers "would this host pass?" without touching it.
    """
    from .exposure import APPLY_COMMAND, ExposureTransaction, verify_exposure
    from .providers.compose_host import _default_runner
    from .providers.exposure_host import ComposeHostExposureEffects

    spec = _load(args.descriptor)
    effects = ComposeHostExposureEffects(
        spec, deploy_dir=args.deploy_dir, runner=_default_runner
    )
    if not args.execute:
        report = verify_exposure(spec, effects.observe())
        print(f"DRY RUN — nothing applied. descriptor {report.descriptor_digest}")
        _print_exposure_report(report)
        return EXIT_OK if report.ok else EXIT_REFUSED

    transaction = ExposureTransaction(
        spec=spec, effects=effects, lock_directory=args.lock_dir
    )
    try:
        report = transaction.run(command=APPLY_COMMAND)
    except PreconditionFailed as exc:
        state = "rolled back" if transaction.rolled_back else "NOT rolled back"
        print(f"error: {exc}", file=sys.stderr)
        print(f"({state})", file=sys.stderr)
        return EXIT_REFUSED
    print(f"applied and verified. descriptor {report.descriptor_digest}")
    _print_exposure_report(report)
    return EXIT_OK


def _print_exposure_report(report: VerificationReport) -> None:
    from .exposure import Severity

    for token in report.verified:
        print(f"  ok       {token}")
    for finding in report.findings:
        marker = "REFUSED" if finding.severity is Severity.REFUSE else "note   "
        print(f"  {marker}  [{finding.code}] {finding.detail}")


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
        print("\nDRY RUN. Nothing was executed. Re-run with --execute to roll back.")
        return EXIT_OK

    from .engine.lock import deployment_lock
    from .engine.run import Executor
    from .execution_plan import HostPrestateV1, render_execution_plan

    # A SEPARATE grant from the deploy's. One approval that covered both would
    # let a single decision make a change and then erase it.
    bindings = _load_bindings(args)
    grant = _require_grant(args, spec, "rollback", bindings=bindings)
    effects = _build_effects(spec, args, bindings=bindings)
    # A SEPARATE plan too, and for the same reason: one descriptor yields a
    # different plan per operation, so a deploy's frozen digest must not
    # recompute equal to a rollback's.
    execution_plan = render_execution_plan(
        spec,
        plan,
        target=args.target,
        operation="rollback",
        descriptor_digest=str(spec.to_canonical_document().sha256_digest()),
        prestate=HostPrestateV1.from_observations(effects.observe_roles()),
        application_profile_digest=_application_profile_digest(),
    )
    executor = Executor(
        spec,
        effects,
        grant,
        execution_plan=execution_plan,
        evidence_policy=bindings.evidence_policy if bindings else None,
        evidence_verifier=bindings.evidence_verifier if bindings else None,
        recovery_verifier=bindings.recovery_verifier if bindings else None,
    )
    # Same rule as `cmd_deploy`: the lock wraps the whole run.
    with deployment_lock(
        spec.product,
        label=f"dotmac-deploy rollback {plan.previous_image}",
        directory=args.lock_dir,
    ):
        outcome = executor.rollback(plan)
    print()
    _print_outcome(outcome)
    if outcome.succeeded:
        print(f"\nROLLED BACK to {plan.previous_image}")
        return EXIT_OK
    failed = outcome.failed_step.value if outcome.failed_step else "(unknown step)"
    print(f"\nROLLBACK REFUSED at {failed}: {outcome.failure}", file=sys.stderr)
    return EXIT_REFUSED


def _print_outcome(outcome: DeploymentOutcome) -> None:
    """The step-by-step record every executed run prints, deploy or rollback."""
    for record in outcome.records:
        status = "ok" if record.ok else "FAILED"
        print(f"{record.kind.value:<24} {status:<6} {record.detail}")
    for note in outcome.notes:
        print(f"NOTE  {note}")
    print(f"evidence: {outcome.evidence_path or '(not written; see notes)'}")


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
            "Reads deploy/product.toml (ProductDeploymentSpec.v1 or .v2)."
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

    add("preflight", cmd_preflight, "show the gates that run before any mutation")
    add("migrate", cmd_migrate, "show the migration contract")
    add("backup", cmd_backup, "show the backup policy and what verification means")

    rehearse = add(
        "restore-rehearsal",
        cmd_restore_rehearsal,
        "show the restore proof required, or DRIVE it with --execute",
    )
    rehearse.add_argument("--dataset")
    rehearse.add_argument(
        "--manifest", help="recovery bundle manifest to rehearse (with --execute)"
    )
    rehearse.add_argument(
        "--execute",
        action="store_true",
        help=(
            "drive RESTORE_PROCEDURE against the assembly's recovery session. "
            "Creates and destroys its OWN isolated target; never touches the "
            "product host, which is why it needs no authorization receipt"
        ),
    )

    bundle = add(
        "recovery-bundle",
        cmd_recovery_bundle,
        "show the recovery-bundle contract, or adjudicate a manifest",
    )
    bundle.add_argument(
        "--manifest",
        help=(
            "a PostgresRecoveryBundle.v1 manifest to check. A custom-format dump "
            "offered here is refused: it is a data export, not a bundle"
        ),
    )

    plan = add("plan", cmd_plan, "build and print the ordered deployment plan")
    plan.add_argument("--previous-image")

    execution = add(
        "execution-plan",
        cmd_execution_plan,
        "render FoundationExecutionPlanV1 and print its ExecutionPlanDigestV1",
    )
    execution.add_argument(
        "--target",
        required=True,
        help=(
            "the host this plan is bound to. Required and never inferred: a "
            "plan with no target is one that authorizes every host"
        ),
    )
    execution.add_argument(
        "--operation",
        required=True,
        choices=list(OPERATIONS),
        help=(
            "deploy or rollback, frozen separately -- one decision must not "
            "both make a change and erase it"
        ),
    )
    execution.add_argument(
        "--prestate",
        required=True,
        help=(
            "path to the HostPrestateV1 document `observe-prestate` printed on "
            "the target. Required: a plan that does not state its starting "
            'point binds to every starting point. {"roles": []} is the '
            "explicit first-deploy claim"
        ),
    )
    execution.add_argument(
        "--format",
        default="text",
        choices=["text", "json", "digest"],
        help=(
            "`digest` is the ExecutionPlanDigestV1 to submit to Control, "
            "verbatim; `json` is the canonical document itself"
        ),
    )
    execution.add_argument("--previous-image")
    execution.add_argument("--skip-backup", action="store_true")
    execution.add_argument("--skip-backup-reason", default="")

    deploy = add("deploy", cmd_deploy, "deploy (DRY RUN unless --execute)")
    deploy.add_argument("--previous-image")
    deploy.add_argument("--execute", action="store_true")
    deploy.add_argument(
        "--target",
        default="",
        help=(
            "the host this deployment targets, matched against the "
            "authorization receipt's target_ref. REQUIRED with --execute"
        ),
    )
    deploy.add_argument(
        "--authorization",
        default="",
        help=(
            "path to the Platform CP authorization receipt permitting this "
            "operation on this descriptor and target. REQUIRED with "
            "--execute; the flag alone is intent, not permission"
        ),
    )
    deploy.add_argument(
        "--recovery-receipt",
        action="append",
        default=[],
        metavar="DATASET=PATH",
        help=(
            "a signed RecoveryReceipt.v1 for one externally executed dataset. "
            "Repeatable. Receipts are passed in, never discovered: a search "
            "cannot tell 'no proof exists' from 'no proof was offered', and "
            "will happily find last quarter's"
        ),
    )
    deploy.add_argument("--skip-backup", action="store_true")
    deploy.add_argument("--skip-backup-reason", default="")
    deploy.add_argument(
        "--deploy-dir",
        default=DEFAULT_DEPLOY_DIR,
        help=(
            "the host directory `--execute` runs against, "
            f"default {DEFAULT_DEPLOY_DIR!r}"
        ),
    )
    deploy.add_argument(
        "--provider",
        default=PROVIDER_COMPOSE_HOST,
        # The in-package provider plus every DECLARED binding name. Names come
        # from package metadata only — parsing this value imports no assembly
        # code, so `validate` and a dry run stay side-effect free. Loading
        # happens once, on the execute path (`_load_bindings`).
        type=_provider_name,
        metavar="PROVIDER",
        help="the Effects implementation `--execute` runs the plan against",
    )

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

    apply_exposure_cmd = add(
        "exposure-apply",
        cmd_exposure_apply,
        "apply the exposure plan under the lock (DRY RUN unless --execute)",
    )
    apply_exposure_cmd.add_argument("--execute", action="store_true")
    apply_exposure_cmd.add_argument("--deploy-dir", default=DEFAULT_DEPLOY_DIR)
    apply_exposure_cmd.add_argument(
        "--lock-dir",
        default="/var/lock",
        help="where the product deployment lock lives",
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
    rollback.add_argument("--execute", action="store_true")
    rollback.add_argument(
        "--target",
        default="",
        help=(
            "the host this deployment targets, matched against the "
            "authorization receipt's target_ref. REQUIRED with --execute"
        ),
    )
    rollback.add_argument(
        "--authorization",
        default="",
        help=(
            "path to the Platform CP authorization receipt permitting this "
            "operation on this descriptor and target. REQUIRED with "
            "--execute; the flag alone is intent, not permission"
        ),
    )
    rollback.add_argument(
        "--deploy-dir",
        default=DEFAULT_DEPLOY_DIR,
        help=(
            "the host directory `--execute` runs against, "
            f"default {DEFAULT_DEPLOY_DIR!r}"
        ),
    )
    rollback.add_argument(
        "--provider",
        default=PROVIDER_COMPOSE_HOST,
        # The in-package provider plus every DECLARED binding name. Names come
        # from package metadata only — parsing this value imports no assembly
        # code, so `validate` and a dry run stay side-effect free. Loading
        # happens once, on the execute path (`_load_bindings`).
        type=_provider_name,
        metavar="PROVIDER",
        help="the Effects implementation `--execute` runs the plan against",
    )

    # Derived from the host-tool registry rather than listed again, so a tool
    # entering or leaving that registry cannot leave a flag behind that
    # configures nothing.
    observe = add(
        "observe-prestate",
        cmd_observe_prestate,
        "print the host's current HostPrestateV1 document (read-only)",
    )
    observe.add_argument(
        "--deploy-dir",
        default=DEFAULT_DEPLOY_DIR,
        help=f"the host directory to observe, default {DEFAULT_DEPLOY_DIR!r}",
    )
    observe.add_argument(
        "--provider",
        default=PROVIDER_COMPOSE_HOST,
        type=_provider_name,
        metavar="PROVIDER",
        help="the Effects implementation whose observation this is",
    )

    from .engine.lock import DEFAULT_LOCK_DIR as _LOCK_DIR

    for _sub in (deploy, rollback):
        _sub.add_argument(
            "--lock-dir",
            default=_LOCK_DIR,
            help=(
                f"directory holding the product deployment lock, default "
                f"{_LOCK_DIR!r}. Overridable per deployment (never hardcoded), "
                "and what lets a release smoke take ITS lock in a private "
                "directory instead of contending for the host-global one"
            ),
        )

    from .toolchain import DEFAULT_TOOLS as _HOST_TOOLS

    for _sub in (deploy, rollback, observe):
        for _tool in _HOST_TOOLS:
            _sub.add_argument(
                f"--{_tool.replace('_', '-')}-bin",
                dest=f"{_tool}_bin",
                default="",
                help=(
                    f"absolute path to the {_tool} binary. Overridable per "
                    "deployment; the default is absolute on purpose, because a "
                    "bare name would let PATH choose which binary supplies this "
                    "deployment's evidence"
                ),
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
