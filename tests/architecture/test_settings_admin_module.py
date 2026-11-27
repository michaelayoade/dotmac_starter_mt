"""`dotmac-settings-admin` shape: stateless, contract 2, old feature gone.

TODO(lane A): when `ProductAssemblySpec.surface_selection` and
`dotmac_kernel.testing.surface_conformance` land (programme section 1.2, lane A),
(1) add this module's `staff` surface to the reference assembly's explicit selection and
(2) run the conformance kit over it (two-prefix boot, guard on every route, CSRF on
mutations, named-route nav, namespace isolation). Until then the admin route sweep
(`tests/unit/test_admin_route_sweep.py`) is the guard/CSRF proof for these routes.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import dotmac_settings_admin
from dotmac_kernel.modules import KERNEL_MODULE_CONTRACT_VERSION

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "packages" / "dotmac-settings-admin"
SRC = PKG / "src" / "dotmac_settings_admin"


def test_the_manifest_is_contract_two_stateless_and_declares_one_staff_surface() -> (
    None
):
    module = dotmac_settings_admin.module
    assert module.contract_version == KERNEL_MODULE_CONTRACT_VERSION == 2
    assert [(s.code, s.facet) for s in module.web_surfaces] == [
        ("staff", "staff_admin")
    ]
    assert not module.web_routers and not module.nav
    assert (module.short_code, module.migration_prefix, module.tables) == (
        None,
        None,
        (),
    )
    assert module.platform_tables == ()


def test_no_migrations_models_or_table_definitions_ship_in_the_package() -> None:
    assert not (SRC / "migrations").exists()
    assert not (SRC / "models.py").exists()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = {getattr(b, "id", getattr(b, "attr", "")) for b in node.bases}
                assert "Base" not in bases, f"{path.name} declares an ORM table"


def test_the_package_depends_on_the_kernel_only() -> None:
    forbidden = ("app", "dotmac_ui", "dotmac_template_studio", "dotmac_ticketing")
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert (
                    name.split(".")[0] not in forbidden
                ), f"{path.name} imports {name}"
    deps = tomllib.loads((PKG / "pyproject.toml").read_text())["tool"]["poetry"][
        "dependencies"
    ]
    assert "dotmac-kernel" in deps and not any(
        d.startswith("dotmac-") and d != "dotmac-kernel" for d in deps
    )


def test_the_reference_app_uses_the_package_and_no_copy_of_the_feature_remains() -> (
    None
):
    from app.assembly import assembly
    from app.features import FEATURE_MODULES

    assert not (ROOT / "app" / "features" / "settings").exists()
    assert "app.features.settings" not in FEATURE_MODULES
    codes = [
        getattr(m, "code", None) or getattr(m, "name", None) for m in assembly.modules
    ]
    assert "settings_admin" in codes and "settings" not in codes
    kernel_templates = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/templates/admin"
    assert not (kernel_templates / "settings").exists()
