"""The conformance kit a product runs in its OWN CI.

A standard enforced only in the repository that defines it is a standard the
consumers never feel. These functions are the enforcement surface a product
imports, so that "does ERP conform?" is answered by ERP's CI on ERP's tree
rather than by a reviewer's memory.

Deliberately framework-free: plain functions returning a list of problems, not
pytest fixtures and not assertions. A product wraps them in whatever its own
suite uses — one line each — and a CI job that is not pytest at all can call
them too. Returning problems rather than raising also lets a product report all
of them at once, which is the difference between one review cycle and five.

Every function here is also the thing the facility's own sensitivity tests
drive with planted violations, so each is demonstrated to fail rather than
assumed to.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from .errors import DeploymentFoundationError
from .spec import ProductDeploymentSpec


def check_descriptor(path: str | Path) -> list[str]:
    """The descriptor parses and every cross-field rule holds."""
    try:
        ProductDeploymentSpec.load(path)
    except DeploymentFoundationError as exc:
        return [str(exc)]
    except OSError as exc:
        return [f"{path}: {exc}"]
    return []


def check_rendered_assets_match(
    spec: ProductDeploymentSpec,
    rendered: Mapping[str, str],
    directory: str | Path,
) -> list[str]:
    """Committed assets equal what the descriptor renders.

    This is `render --check` as a library call, so a product's CI fails on a
    hand-edited Compose file at review time rather than on a host at 3am. The
    stray-file half matters as much as the difference half: an asset present
    but unrendered is an untracked override, which is the shape recorded twice
    against a live staging host.
    """
    root = Path(directory)
    problems: list[str] = []
    for name, expected in sorted(rendered.items()):
        target = root / name
        if not target.exists():
            problems.append(f"{target}: missing; run `dotmac-deploy render`")
            continue
        if target.read_text(encoding="utf-8") != expected:
            problems.append(
                f"{target}: differs from what the descriptor renders. Edit "
                "deploy/product.toml and re-render; never edit the result"
            )
    if root.exists():
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = str(path.relative_to(root))
            if relative not in rendered:
                problems.append(
                    f"{path}: present but not rendered by {spec.product}'s descriptor"
                )
    return problems


def check_no_product_branch(module_paths: Sequence[str | Path]) -> list[str]:
    """No shared execution path branches on product or provider identity.

    ADR-0024 § 4 applied to infrastructure, and ADR-0070 § 3 restates it: every
    difference between ERP, Sub, Integrator and Starter is a value in the
    descriptor, never an `if` in shared code.

    An AST walk rather than a substring scan, for the reason
    `executable-invariants-use-ast-not-text` records: the Workspace `.dmui-*`
    guard once failed CI on a class name that appeared only inside a comment
    explaining why inventing it was wrong. A guard that reads its own
    documentation is a guard nobody trusts, and an untrusted guard gets
    disabled.
    """
    import ast

    known = {"erp", "sub", "integrator", "starter", "dotmac_erp", "dotmac_sub"}
    problems: list[str] = []
    for raw in module_paths:
        path = Path(raw)
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            problems.append(f"{path}: could not be parsed ({exc})")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and comparator.value in known:
                    problems.append(
                        f"{path}:{node.lineno}: compares against product identity "
                        f"{comparator.value!r}. Variation enters through the typed "
                        "descriptor or a declared extension point, never a branch"
                    )
    return problems


def check_liveness_is_dependency_free(spec: ProductDeploymentSpec) -> list[str]:
    """Liveness and readiness are different endpoints, and readiness can fail.

    The defect this catches is live in the fleet: `dotmac_erp`'s `/health`
    returns a hardcoded `{"status":"ok"}` and is used as BOTH the container
    healthcheck and the deploy gate, while the real `readiness_probe()` — which
    does check the database and Redis — is called by neither. A gate that
    cannot fail is not a gate.
    """
    problems: list[str] = []
    for role in spec.roles:
        if role.replicas == 0:
            continue
        if (
            role.live is not None
            and role.ready is not None
            and role.live.path == role.ready.path
        ):
            problems.append(
                f"role {role.code!r}: liveness and readiness are both "
                f"{role.live.path!r}. One of them is wrong — liveness must not "
                "touch a dependency, readiness must fail when one is down"
            )
    if spec.ingress is not None:
        for route in spec.ingress.routes:
            if spec.role(route.role).ready is None:
                problems.append(
                    f"role {route.role!r} serves {route.path!r} with no readiness "
                    "probe, so a candidate can only be gated on a timer"
                )
    return problems


def check_credential_separation(spec: ProductDeploymentSpec) -> list[str]:
    """No runtime role holds the migration owner material.

    Statically checkable because both are declared by NAME. The defect it
    catches — `dotmac_erp:.env.example` defaulting the runtime DSN to the
    Postgres superuser — silently collapses a role split the deploy script
    otherwise enforces, so the enforcement passes while the property is gone.
    """
    owner = spec.migration.owner_material
    return [
        f"role {role.code!r} holds the migration owner material {owner!r}; it "
        "could create, alter and drop any table for the life of the deployment"
        for role in spec.roles
        if owner in role.materials
    ]


def check_image_is_pinned_by_digest(spec: ProductDeploymentSpec) -> list[str]:
    """The image is a digest, never a tag.

    `ProductDeploymentSpec` already refuses a tag at parse time, so this is a
    second line of defence and exists for one concrete reason: a product may
    build its own tooling around the descriptor, and the rule needs to be
    callable independently of the loader that enforces it.
    """
    if "@sha256:" not in spec.image:
        return [
            f"{spec.image} is not pinned by digest. Build once and promote the "
            "exact digest; a mutable tag is how a bare `docker compose up -d` "
            "downgraded a production deployment by five weeks"
        ]
    return []


_PLACEHOLDER_DIGEST = "sha256:" + "0" * 64


def check_no_placeholder_digests(spec: ProductDeploymentSpec) -> list[str]:
    """An all-zero digest is a placeholder, and a placeholder is not a pin.

    Every adapter written before a product had published an image carried
    `sha256:000…0` in both `image.reference` and `assembly.manifest_digest`. The
    descriptor PARSED — the value is a syntactically perfect digest — so a gate
    that checked parsing reported green on a deployment pinned to an image that
    can never exist.

    This is deliberately a CONFORMANCE check and not a parse error. During the
    work that produces the real digest, an author needs `render` and `plan` to
    run against an incomplete descriptor; what must not happen is that state
    reaching a merge. Parsing is for the author, conformance is for the gate.
    """
    problems: list[str] = []
    if spec.image_digest == _PLACEHOLDER_DIGEST:
        problems.append(
            f"image.reference is pinned to the placeholder {_PLACEHOLDER_DIGEST}. "
            "Replace it with the digest the release actually published — a "
            "syntactically valid digest that names nothing is not a pin"
        )
    if spec.manifest_digest == _PLACEHOLDER_DIGEST:
        problems.append(
            "assembly.manifest_digest is the placeholder. Generate it from the "
            "real ProductAssemblySpec; until then `dotmac-deploy drift` cannot "
            "tell an approved module set from any other"
        )
    return problems


def check_managed_dependencies_declare_a_probe(
    spec: ProductDeploymentSpec,
) -> list[str]:
    """A dependency this deployment starts must say when it is ready.

    Without a probe, a role waiting on it waits on `service_started`, which is
    satisfied the instant the container exists — before Postgres has finished
    recovery or Redis has loaded its dump. The role then starts, fails to
    connect, and restarts until it happens to win the race.

    Deliberately NOT a check that the ROLES' probes are runnable. The renderer's
    default probes with Python's standard library, and whether a given image
    has Python is not a fact this package can establish from a descriptor —
    claiming to check it would be an unenforceable premise (ADR-0018). A role
    on a non-Python image declares its own `probe`, and that is review
    discipline, stated rather than implied.
    """
    return [
        f"managed dependency {dependency.code!r} declares no health probe"
        for dependency in spec.managed_dependencies
        if not dependency.health_probe
    ]


def check_managed_dependencies_are_reachable(
    spec: ProductDeploymentSpec,
) -> list[str]:
    """A role configured to reach a dependency nothing starts.

    `dotmac_erp`'s rendered Compose file declared Celery broker configuration and
    no Redis service: internally consistent, and it would not have worked. A
    dependency this deployment does not run is legitimate — somebody else
    operates it — but then the role must not `depends_on` it, and the readiness
    probe is what covers it instead.
    """
    problems: list[str] = []
    unmanaged = {
        item.code: item for item in spec.external_dependencies if not item.managed
    }
    for role in spec.roles:
        for name in role.depends_on:
            if name in unmanaged:
                problems.append(
                    f"role {role.code!r} depends on {name!r}, which this "
                    "deployment does not run"
                )
    return problems


def check_no_duplicate_log_shipping(spec: ProductDeploymentSpec) -> list[str]:
    """Exactly one path ships each signal.

    `dotmac_sub` pushes application logs to Loki directly AND runs a promtail
    job that tails the same container, to two different default hosts. Every
    line is stored twice and every rate threshold is silently halved in
    meaning, and neither copy looks wrong in a dashboard.
    """
    if spec.telemetry.app_direct_shipping and spec.telemetry.logs:
        return [
            "the descriptor enables both application-direct log shipping and "
            "collector log collection; every line would be stored twice"
        ]
    return []


def check_security_exceptions_are_justified(spec: ProductDeploymentSpec) -> list[str]:
    """Every relaxation is declared, justified and attributed.

    Sub's WireGuard roles genuinely need `NET_ADMIN`. What they do not need is
    `privileged: true` with no recorded reason, which is what the source Compose
    file has. The grant is allowed to stay; the silence is not.
    """
    problems: list[str] = []
    for role in spec.roles:
        for exception in role.security.exceptions:
            if exception.kind == "privileged":
                problems.append(
                    f"role {role.code!r} declares a full 'privileged' exception "
                    f"({exception.justification!r}, approved by "
                    f"{exception.approved_by}). Full privilege grants everything "
                    "for one narrow need — prefer a specific capability, and if "
                    "it is genuinely required this line is the review record"
                )
    return problems


ALL_CHECKS = (
    check_no_placeholder_digests,
    check_managed_dependencies_declare_a_probe,
    check_managed_dependencies_are_reachable,
    check_liveness_is_dependency_free,
    check_credential_separation,
    check_image_is_pinned_by_digest,
    check_no_duplicate_log_shipping,
    check_security_exceptions_are_justified,
)


def check_all(spec: ProductDeploymentSpec) -> list[str]:
    """Every descriptor-level conformance check, reported together."""
    problems: list[str] = []
    for check in ALL_CHECKS:
        problems.extend(check(spec))
    return problems
