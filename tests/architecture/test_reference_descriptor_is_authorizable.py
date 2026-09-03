"""The committed descriptor must be one deployment control can authorize.

`dotmac-deploy deploy --execute` cannot run without a grant, a grant is bound
to a canonical document, and `build_canonical_document` refuses an address
literal anywhere in a descriptor. For as long as `deploy/product.toml`'s app
role read::

    command = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

`to_canonical_document()` raised on this repository's own reference descriptor,
so no deployment of the Starter could ever be authorized — and nothing failed,
because the two contracts that parse the real file both edited it first,
stripping `"--host", "0.0.0.0", ` out of the text before handing it to the
parser. Those workarounds are deleted; this module is what replaces them.

Every assertion below resolves to the REAL committed artifacts — the descriptor
file, the launcher module, the Dockerfile — never to a fixture shaped like
them. A fixture that happens to canonicalize proves nothing about the file that
ships, which is the precise way this survived.

The repair: `0.0.0.0` is not topology, it is how the process binds inside its
own container. It lives in `app/runtime.py` (product-owned, its implementation
fixed by the image digest the descriptor already pins), the descriptor owns the
launcher and the port, and no second `refuse_resolved_material=False` caller
was added — `test_canonical_document_boundary_flag.py` still pins that to the
renderer alone.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import shlex
from pathlib import Path
from typing import Any

import pytest
from dotmac_deployment_foundation.document import _walk_strings
from dotmac_deployment_foundation.engine.plan import build_plan
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.execution_plan import (
    FoundationExecutionPlanV1,
    HostPrestateV1,
    execution_plan_digest,
    render_execution_plan,
    require_execution_plan_digest,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

from app import runtime as launcher

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DESCRIPTOR = PROJECT_ROOT / "deploy" / "product.toml"
DOCKERFILE = PROJECT_ROOT / "Dockerfile"

#: A concrete host, stated by the caller. Never derived from the descriptor — a
#: derived target makes every comparison compare the descriptor with itself.
TARGET = "prod-lagos-01"


def _spec() -> ProductDeploymentSpec:
    """The committed bytes, parsed. No substitution, no fixture."""
    return ProductDeploymentSpec.loads(
        DESCRIPTOR.read_text(encoding="utf-8"), source=str(DESCRIPTOR)
    )


def _app_role() -> Any:
    return next(role for role in _spec().roles if role.code == "app")


def _looks_like_an_address(text: str) -> bool:
    """The same breadth `ingress.refuse_address_literal` uses.

    Re-derived here rather than imported so this module can report WHICH
    strings offend and how many, instead of only that the first one raised.
    One refusal masks the others: `build_canonical_document` stops at the first
    offender, so "it raised" never told anyone whether the descriptor held one
    address literal or four.
    """
    candidate = text.strip().strip("[]")
    if not candidate:
        return False
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        try:
            ipaddress.ip_network(candidate, strict=False)
        except ValueError:
            return False
    return True


def _offenders(content: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (path, text)
        for path, text in _walk_strings(content)
        # An image reference carries a `sha256:` digest and no address; the
        # refusal itself skips those explicitly, for the same reason.
        if not text.startswith("sha256:") and _looks_like_an_address(text)
    ]


# ── proof 1: the real descriptor canonicalizes, strictly ────────────────────


def test_the_committed_descriptor_canonicalizes_with_no_exemption() -> None:
    """`to_canonical_document()` — the STRICT path that everything sending a
    document to deployment control takes."""
    document = _spec().to_canonical_document()

    assert document.sha256_digest().startswith("sha256:")
    assert document.canonical_bytes()


def test_no_string_anywhere_in_the_descriptor_is_an_address_literal() -> None:
    """Enumerated, rather than inferred from "it did not raise".

    The strict build above stops at the first offender, so on its own it can
    only ever say that ONE refusal is gone. This walks every string in the
    finished document and requires the count to be zero, which is the question
    that matters before a production window rather than during one.
    """
    found = _offenders(_spec().to_canonical_document().content)

    assert not found, (
        f"the reference descriptor carries resolved topology: {found}. "
        "Deployment control binds this document's digest into an "
        "independently signed authorization and resolves addresses "
        "separately; an address that reaches this digest collapses the two "
        "owners into one"
    )


def test_the_check_still_bites_if_the_bind_comes_back() -> None:
    """Sensitivity. A check over a set that is now empty passes for the wrong
    reason, and this set is empty by design — so plant the exact regression and
    require both halves to react: the refusal, and the enumeration above."""
    planted = DESCRIPTOR.read_text(encoding="utf-8").replace(
        'command = ["python", "-m", "app.runtime", "--port", "8000"]',
        'command = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]',
        1,
    )
    assert "--host" in planted, "the plant did not apply; this test proves nothing"
    spec = ProductDeploymentSpec.loads(planted, source="<planted>")

    with pytest.raises(SpecError, match="address literal"):
        spec.to_canonical_document()


def test_the_enumeration_would_report_a_planted_address() -> None:
    """The other half of the sensitivity: `_offenders` must be able to find
    one. Driven over a synthetic document, because the real one must stay at
    zero and `_walk_strings` does not care where its content came from."""
    wildcard = "0.0.0.0"  # noqa: S104 - a planted value, not a bind
    assert _offenders({"roles": [{"command": ["--host", wildcard]}]}) == [
        ("document.roles[0].command[1]", wildcard)
    ]


# ── proof 2: an execution plan renders from it ──────────────────────────────


def _plan(**overrides: Any) -> FoundationExecutionPlanV1:
    spec = _spec()
    kwargs: dict[str, Any] = {
        "target": TARGET,
        "operation": "deploy",
        "descriptor_digest": spec.to_canonical_document().sha256_digest(),
        "prestate": HostPrestateV1.first_deploy(),
        "application_profile_digest": "",
    }
    kwargs.update(overrides)
    return render_execution_plan(spec, build_plan(spec), **kwargs)


def test_an_execution_plan_renders_from_the_committed_descriptor() -> None:
    plan = _plan()

    assert plan.product == "dotmac_starter_mt"
    assert plan.target == TARGET
    assert plan.operation == "deploy"
    assert plan.steps, "a plan with no steps makes 'deploy' a word, not a procedure"
    assert plan.digest().startswith("sha256:")
    # The middle term is its own value. Reaching for one of the three digests
    # it resembles is how Control's and the Foundation's came to be unequal.
    assert plan.digest() != plan.descriptor_digest


def test_the_launcher_reaches_the_plan_digest_through_the_descriptor() -> None:
    """The role command is not a plan STEP — the steps are the engine's, and
    the app role's command reaches execution through the rendered compose file.
    It is still inside what gets authorized, via `descriptor_digest`, and this
    proves that link rather than assuming it: change the launcher, and the plan
    Control freezes is a different plan.
    """
    spec = _spec()
    relaunched = dataclasses.replace(
        spec,
        roles=(
            dataclasses.replace(
                _app_role(),
                command=("python", "-m", "app.somewhere_else", "--port", "8000"),
            ),
            *spec.roles[1:],
        ),
    )

    moved = render_execution_plan(
        relaunched,
        build_plan(relaunched),
        target=TARGET,
        operation="deploy",
        descriptor_digest=relaunched.to_canonical_document().sha256_digest(),
        prestate=HostPrestateV1.first_deploy(),
        application_profile_digest="",
    )
    assert moved.digest() != _plan().digest()


# ── proof 3: the digest is reproduced BEFORE execution ──────────────────────


def test_the_plan_digest_is_reproduced_before_execution() -> None:
    """Step 4 of the flow: re-derive, do not trust the authorization.

    Rendered twice from the committed bytes, exactly as a long-running process
    would — once while Platform CP asks Control, once at the point of use.
    """
    authorized = _plan().digest()

    assert require_execution_plan_digest(_plan(), authorized=authorized) == authorized


def test_the_reproduced_digest_is_the_published_canonicalization() -> None:
    """Two other repositories bind to these bytes, so the value has to be
    reachable through the published function and not only through the method."""
    plan = _plan()

    assert execution_plan_digest(plan.as_document()) == plan.digest()


def test_a_plan_that_moved_is_refused_at_that_gate() -> None:
    """Sensitivity for the gate above: it must be able to fail. A recompute
    that accepted anything would be a comparison nobody could observe."""
    authorized = _plan().digest()

    with pytest.raises(PreconditionFailed, match="between authorization and execution"):
        require_execution_plan_digest(
            _plan(target="staging-abuja-01"), authorized=authorized
        )


# ── the launcher is real, and it is what the image runs ─────────────────────


def test_the_launcher_the_descriptor_names_exists_and_owns_the_bind() -> None:
    """Resolve to the module, not to its spelling in a string.

    The descriptor names `python -m app.runtime`. This IMPORTS that module and
    reads the constant, so a descriptor pointing at a module that does not
    exist — or a launcher that quietly stopped binding every interface inside
    its container — fails here rather than at a container that never becomes
    ready.
    """
    assert _app_role().command[:3] == ("python", "-m", launcher.__name__)
    assert launcher.BIND_ADDRESS == "0.0.0.0"  # noqa: S104 - see app/runtime.py
    assert callable(launcher.main)


def test_the_descriptor_and_the_launcher_agree_about_the_port() -> None:
    """The port is the descriptor's to declare, and the launcher must REQUIRE
    it — one that fell back to a default could listen somewhere the health
    probes are not looking while both files still read correctly."""
    role = _app_role()
    port = int(role.command[role.command.index("--port") + 1])

    assert port == role.live.port == role.ready.port
    with pytest.raises(SystemExit):
        launcher.build_parser().parse_args([])


def test_the_image_runs_the_same_launcher_the_descriptor_declares() -> None:
    """The binding the whole repair rests on: the descriptor names the
    launcher, and the image digest fixes its implementation. If the Dockerfile
    CMD ran something else, that digest would be binding the wrong bytes.

    The PORT is deliberately not compared: the image takes it from the
    `APP_PORT` build arg so a local run can move it, while the descriptor
    states the one a deployment uses.
    """
    lines = [
        line
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.startswith("CMD ")
    ]

    assert len(lines) == 1, f"expected exactly one CMD, found {lines}"
    assert shlex.split(lines[0][len("CMD ") :])[:3] == [
        "python",
        "-m",
        launcher.__name__,
    ]
