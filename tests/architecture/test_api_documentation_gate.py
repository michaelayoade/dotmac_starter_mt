"""The documentation gate bites in BOTH directions (ADR-0016 § 6, item 5).

`audit_api_documentation` is the half that reads what the application actually
serves, so it is the half that must be shown to fail. A gate exercised only
against an application that already satisfies it passes for the wrong reason:
it cannot distinguish "the policy is enforced" from "the audit found nothing
because it looks in the wrong place".

Three cases, and the third is why the other two are trustworthy:

1. **Planted default, bare app.** FastAPI's own default configuration must FAIL
   the production gate. This is the exact inherited state the kernel shipped
   before `api_documentation` existed.
2. **Planted default, the REFERENCE ASSEMBLY.** The same defaults on
   `create_app(assembly)` — the real composed application, every router mounted —
   must FAIL the production gate. A gate that catches a toy app and misses the
   product is the failure mode this repository has paid for elsewhere.
3. **The mirror.** An application serving NO documentation must FAIL the
   DEVELOPMENT gate. Without it the gate is one-sided: a broken audit that
   returned "no violations" unconditionally would pass cases 1 and 2 by
   accident, and only a required-but-absent surface exposes that.
"""

from __future__ import annotations

from dotmac_kernel.api_documentation import (
    PRODUCTION,
    api_documentation_policy,
    audit_api_documentation,
    documentation_arguments,
)
from dotmac_kernel.app_factory import create_app
from fastapi import FastAPI

from app.assembly import assembly

_PRODUCTION = api_documentation_policy(PRODUCTION)
_DEVELOPMENT = api_documentation_policy("development")


def test_fastapi_defaults_on_a_bare_app_fail_the_production_gate():
    """The inherited state: /docs, /redoc and /openapi.json, unauthenticated."""

    violations = audit_api_documentation(FastAPI(), _PRODUCTION)
    assert violations, (
        "FastAPI's default documentation configuration must fail the production "
        "gate; an audit that passes it is not reading the route inventory"
    )
    rendered = " ".join(violations)
    assert "/docs" in rendered or "interactive" in rendered


def test_fastapi_defaults_on_the_reference_assembly_fail_the_production_gate():
    """The same planted default, on the REAL composed application.

    Built with the development policy so the app carries FastAPI's default
    documentation coordinates, then audited against production — which is
    exactly the pre-fix kernel's inherited inventory, on the assembly this
    repository actually ships.
    """

    app = create_app(
        _spec_with(api_documentation=_DEVELOPMENT),
    )
    violations = audit_api_documentation(app, _PRODUCTION)
    assert violations, (
        "the reference assembly serving FastAPI's default documentation must "
        "fail the production gate"
    )


def test_an_app_serving_no_documentation_fails_the_development_gate():
    """The mirror, and the reason the two refusals above mean anything.

    An audit that returned no violations unconditionally would pass both
    planted-default cases. Only a policy that REQUIRES a surface, checked
    against an application that serves none, can catch that.
    """

    app = FastAPI(**documentation_arguments(_PRODUCTION))
    violations = audit_api_documentation(app, _DEVELOPMENT)
    assert violations, (
        "an application serving no documentation must fail the development "
        "gate, which declares both planes PUBLIC"
    )
    assert any("no route is mounted" in v for v in violations)


def test_the_gate_accepts_an_application_that_matches_its_policy():
    """Sensitivity for the three refusals: the gate is not refusing everything."""

    app = FastAPI(**documentation_arguments(_DEVELOPMENT))
    assert audit_api_documentation(app, _DEVELOPMENT) == ()


def test_clearing_the_attribute_cannot_hide_a_mounted_route():
    """Routes are located by PATH, never by reading `app.docs_url`."""

    app = FastAPI(**documentation_arguments(_DEVELOPMENT))
    app.docs_url = None  # the attribute lies; the route is still mounted
    violations = audit_api_documentation(app, _PRODUCTION)
    assert any("/docs" in v for v in violations)


def _spec_with(**overrides):
    """The reference assembly spec with fields replaced."""

    import dataclasses

    return dataclasses.replace(assembly, **overrides)
