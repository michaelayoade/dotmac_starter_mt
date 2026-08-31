"""The facet-principal projection refuses absence, the wrong plane, and credentials.

Three properties, each with a sensitivity proof, because a guard that cannot
fail is indistinguishable from no guard:

1. **Absent refuses.** A route composed outside an authenticating facet gets an
   error, never `None` silently attributed to nobody.
2. **The wrong plane refuses.** A tenant-plane principal reaching a caller that
   declares the platform plane fails loudly. The positive control -- the right
   plane succeeding -- is asserted beside it, so the refusal cannot be passing
   because everything fails.
3. **It cannot authenticate.** Asserted structurally, by reading the module's
   own source: it must contain no cookie/header/token/session/database read. A
   behavioural test cannot prove the ABSENCE of a fallback path; a source-level
   check can, and it is the property that keeps this a projection rather than a
   second authentication owner.
"""

from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_kernel import facet_principal as fp_module
from dotmac_kernel.facet_principal import (
    FacetPrincipal,
    FacetPrincipalError,
    FacetPrincipalPlaneMismatchError,
    FacetPrincipalUnavailableError,
    facet_principal,
    record_facet_principal,
    require_facet_principal,
)
from dotmac_kernel.web_surfaces import BrowserSecurityPlane

TENANT = BrowserSecurityPlane.TENANT
PLATFORM = BrowserSecurityPlane.PLATFORM


def _request() -> SimpleNamespace:
    """A request stand-in carrying only `state` -- deliberately no cookies,
    headers or session, so any attempt to authenticate here would fail loudly."""

    return SimpleNamespace(state=SimpleNamespace())


def _tenant_principal(**over):
    kwargs = {
        "facet": "admin",
        "security_plane": TENANT,
        "subject_id": uuid4(),
        "subject": object(),
        "tenant_id": uuid4(),
    }
    kwargs.update(over)
    return FacetPrincipal(**kwargs)


def _platform_principal(**over):
    kwargs = {
        "facet": "platform",
        "security_plane": PLATFORM,
        "subject_id": uuid4(),
        "subject": object(),
    }
    kwargs.update(over)
    return FacetPrincipal(**kwargs)


# 1 -- absence


def test_absent_principal_refuses_rather_than_returning_none():
    with pytest.raises(FacetPrincipalUnavailableError):
        require_facet_principal(_request(), plane=TENANT)


def test_the_optional_accessor_reports_absence_without_raising():
    assert facet_principal(_request()) is None


def test_absence_guard_is_sensitive_a_recorded_principal_is_returned():
    """Sensitivity: the refusal above must not be passing for everything."""

    request = _request()
    recorded = _tenant_principal()
    record_facet_principal(request, recorded)
    assert require_facet_principal(request, plane=TENANT) is recorded
    assert facet_principal(request) is recorded


def test_a_foreign_object_on_the_state_slot_is_not_treated_as_a_principal():
    request = _request()
    request.state.facet_principal = {"subject_id": "spoofed"}
    assert facet_principal(request) is None
    with pytest.raises(FacetPrincipalUnavailableError):
        require_facet_principal(request, plane=TENANT)


# 2 -- plane


def test_a_tenant_principal_is_refused_where_the_platform_plane_is_declared():
    request = _request()
    record_facet_principal(request, _tenant_principal())
    with pytest.raises(FacetPrincipalPlaneMismatchError):
        require_facet_principal(request, plane=PLATFORM)


def test_a_platform_principal_is_refused_where_the_tenant_plane_is_declared():
    """Both directions: the guard is not merely 'platform is stricter'."""

    request = _request()
    record_facet_principal(request, _platform_principal())
    with pytest.raises(FacetPrincipalPlaneMismatchError):
        require_facet_principal(request, plane=TENANT)


@pytest.mark.parametrize(
    ("principal_factory", "plane"),
    [(_tenant_principal, TENANT), (_platform_principal, PLATFORM)],
)
def test_plane_guard_is_sensitive_the_matching_plane_is_accepted(
    principal_factory, plane
):
    """Sensitivity: each plane must actually succeed on its own surface."""

    request = _request()
    recorded = principal_factory()
    record_facet_principal(request, recorded)
    assert require_facet_principal(request, plane=plane) is recorded


def test_the_public_plane_can_neither_be_recorded_nor_required():
    with pytest.raises(FacetPrincipalError):
        FacetPrincipal(
            facet="public",
            security_plane=BrowserSecurityPlane.NONE,
            subject_id=uuid4(),
            subject=object(),
        )
    request = _request()
    record_facet_principal(request, _tenant_principal())
    with pytest.raises(FacetPrincipalError):
        require_facet_principal(request, plane=BrowserSecurityPlane.NONE)


def test_a_tenant_plane_principal_without_a_tenant_is_refused_at_construction():
    with pytest.raises(FacetPrincipalError):
        FacetPrincipal(
            facet="admin",
            security_plane=TENANT,
            subject_id=uuid4(),
            subject=object(),
            tenant_id=None,
        )


# 3 -- it is a projection, not an authenticator


_CREDENTIAL_READS = (
    "cookies",
    "headers",
    "authorization",
    "access_token",
    "session",
    "query_params",
    "verify_password",
    "authenticate_request",
    "decode",
)


def test_the_projection_contains_no_credential_read_path():
    """Structural: there is no fallback authenticator to reach for.

    Read from the module source rather than asserted behaviourally, because no
    behavioural test can demonstrate the absence of a path it did not happen to
    trigger.
    """

    source = inspect.getsource(fp_module)
    body = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    tree = ast.parse(source)
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    for token in _CREDENTIAL_READS:
        assert token not in attributes, (
            f"{token!r} is read as an attribute in facet_principal.py; this "
            "module must project already-authenticated state, never obtain it"
        )
        assert token not in called, f"{token!r} is called in facet_principal.py"
    assert "request.cookies" not in body
    assert "request.headers" not in body


def test_the_projection_imports_no_authentication_or_database_module():
    tree = ast.parse(inspect.getsource(fp_module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    forbidden = {
        "dotmac_kernel.deps",
        "dotmac_kernel.security",
        "dotmac_kernel.web_deps",
        "dotmac_kernel.platform_auth",
        "dotmac_kernel.db",
        "dotmac_kernel.session_runtime",
        "sqlalchemy",
        "sqlalchemy.orm",
    }
    assert not (imported & forbidden), (
        f"facet_principal.py imports {sorted(imported & forbidden)}; a "
        "projection of authenticated state needs neither an authenticator nor "
        "a database session"
    )


def test_the_credential_detector_would_actually_fire():
    """Sensitivity for the structural guard itself.

    A source scan over a module that happens to be clean passes for the wrong
    reason unless the detector is shown to bite on a positive sample.
    """

    sample = "def f(request):\n    return request.cookies.get('access_token')\n"
    tree = ast.parse(sample)
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "cookies" in attributes
