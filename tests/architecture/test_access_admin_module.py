"""`dotmac-access-admin` structural and pure-logic guarantees (module contract 2).

No database: these run on the unit/architecture job. The hosted PostgreSQL
acceptance tests live in `tests/test_access_admin_pg.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from dotmac_access_admin import manifest as manifest_module
from dotmac_access_admin import roles as roles_module
from dotmac_access_admin.schemas import (
    CompositionRequest,
    RoleCreate,
    UserCreate,
    UserCreated,
)
from dotmac_kernel.exceptions import BadRequestError
from dotmac_kernel.permissions import PermissionCatalogue, PermissionSpec

PACKAGE = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "dotmac-access-admin"
    / "src"
    / "dotmac_access_admin"
)
module = manifest_module.module


def _catalogue(*specs: PermissionSpec) -> PermissionCatalogue:
    return PermissionCatalogue(("owner", spec) for spec in specs)


def test_the_manifest_is_a_stateless_contract_2_module() -> None:
    assert module.contract_version == 2
    assert module.web_surfaces and not module.web_routers and not module.nav
    assert module.short_code is None and module.migration_prefix is None
    assert not module.tables and not module.platform_tables
    surface = module.web_surfaces[0]
    assert surface.facet == "staff_admin"
    # No htmx requirement: the surface works with native forms, so an assembly
    # without an htmx provision (ERP v2) can still select it.
    assert not surface.browser_capabilities


def test_every_route_declares_a_permission_this_module_owns() -> None:
    declared = {spec.code for spec in module.permissions}
    source = "".join((PACKAGE / name).read_text() for name in ("router.py", "web.py"))
    tree = ast.parse(source)
    guarded = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr in {"get", "post", "patch", "delete"}
            for d in node.decorator_list
        ):
            text = ast.unparse(node)
            assert (
                "require_permission(" in text or "require_web_permission(" in text
            ), f"route {node.name} has no declared-permission guard"
            guarded += 1
    assert guarded >= 10
    referenced = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and n.value.startswith("access_admin.")
    }
    assert referenced <= declared
    # every declared code is used by at least one route guard
    assert declared <= referenced


def test_every_declared_audit_action_has_a_writer() -> None:
    text = "".join(
        p.read_text() for p in PACKAGE.glob("*.py") if p.name != "manifest.py"
    )
    for action in module.audit_actions:
        if action == "access_admin.credential.provisioned":
            assert "PROVISIONED_AUDIT_ACTION" in text
        else:
            assert f'"{action}"' in text, f"{action} is declared but never written"


def test_the_outbox_event_type_has_a_producer() -> None:
    assert list(module.outbox_event_types) == ["access_admin.recovery_requested.v1"]
    assert "enqueue_event" in (PACKAGE / "provisioning.py").read_text()


def test_no_request_or_response_shape_can_carry_password_material() -> None:
    forbidden = ("password", "secret", "token", "hash", "credential")
    for model in (UserCreate, UserCreated, RoleCreate, CompositionRequest):
        for name in model.model_fields:
            assert not any(word in name for word in forbidden), (model, name)
    # extra fields are refused, so a caller cannot smuggle `password` in.
    with pytest.raises(ValueError):
        UserCreate(
            email="a@example.test",
            first_name="A",
            last_name="B",
            password="x",  # type: ignore[call-arg]
        )


def test_credentials_are_created_only_through_the_lifecycle_provision() -> None:
    for path in PACKAGE.glob("*.py"):
        text = path.read_text()
        assert "hash_password" not in text, path.name
        assert "verify_password" not in text, path.name
    # The only place that constructs a credential row is the provision store.
    constructors = [
        p.name for p in PACKAGE.glob("*.py") if "UserCredential(" in p.read_text()
    ]
    assert constructors == ["provisioning.py"]
    assert "lifecycle.provision(" in (PACKAGE / "service.py").read_text()


def test_the_package_never_logs_or_prints() -> None:
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "print", path.name
            if isinstance(node, ast.Import):
                assert all(a.name != "logging" for a in node.names), path.name


def test_a_composition_may_only_reference_declared_permissions() -> None:
    catalogue = _catalogue(
        PermissionSpec(code="a.read", default_roles=("viewer",)),
        PermissionSpec(code="a.write", default_roles=("editor",)),
    )
    assert roles_module.validate_composition(
        ["a.write", "a.read", "a.read"], catalogue
    ) == ("a.read", "a.write")
    with pytest.raises(BadRequestError) as caught:
        roles_module.validate_composition(["a.read", "z.nope", "y.nope"], catalogue)
    assert "y.nope" in str(caught.value) and "z.nope" in str(caught.value)


def test_an_empty_catalogue_declares_nothing() -> None:
    with pytest.raises(BadRequestError):
        roles_module.validate_composition(["anything"], PermissionCatalogue(()))


ERP_ROLES = (
    "finance_preparer",
    "finance_approver",
    "treasury",
    "payroll_approver",
    "payroll_finalizer",
    "bank_reconciler",
)


def test_admin_staff_and_the_six_erp_roles_resolve_as_read_only_code_roles() -> None:
    specs = [PermissionSpec(code="erp.web.access", default_roles=("admin", "staff"))]
    specs += [
        PermissionSpec(code=f"erp.{slug}.act", default_roles=(slug,))
        for slug in ERP_ROLES
    ]
    catalogue = _catalogue(*specs)
    resolved = {role.slug: role for role in roles_module.code_roles(catalogue)}
    assert set(resolved) == {"admin", "staff", *ERP_ROLES}
    assert all(role.read_only for role in resolved.values())
    assert resolved["treasury"].permission_codes == ("erp.treasury.act",)
    assert resolved["admin"].permission_codes == ("erp.web.access",)
    assert roles_module.code_role_slugs(catalogue) == {"admin", "staff", *ERP_ROLES}


def test_sod_pairs_warn_and_never_raise() -> None:
    before = roles_module.registered_sod_pairs()
    try:
        roles_module.register_sod_pairs([("zz_preparer", "zz_approver")])
        warnings = roles_module.sod_warnings(["zz_preparer", "zz_approver", "other"])
        assert [w.roles for w in warnings] == [("zz_approver", "zz_preparer")]
        assert roles_module.sod_warnings(["zz_preparer"]) == ()
        with pytest.raises(ValueError):
            roles_module.register_sod_pairs([("same", "same")])
    finally:
        roles_module._SOD_PAIRS.clear()
        roles_module._SOD_PAIRS.update(before)


def test_the_stateless_package_owns_no_lineage_or_model() -> None:
    assert not (PACKAGE / "migrations").exists()
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = {ast.unparse(b) for b in node.bases}
                assert "Base" not in bases, (path.name, node.name)
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("alembic"), path.name
