"""The reference assembly imports only the kernel's documented public surface
(kernel-boundary Task 2).

`dotmac_kernel.__init__` is the single canonical manifest: `SUPPORTED_MODULES`
(which submodules are public), `INTERNAL_MODULES` (explicitly private), each
supported module's own `__all__`, and the curated top-level `__all__`. This
test AST-scans every `app/` module's imports of `dotmac_kernel` and fails on
any name or module that the manifest does not declare public — so the assembly
can never quietly reach into a kernel internal, and COMPATIBILITY.md stays
honest.

AST-based, not string matching: a `dotmac_kernel` mention in a comment or a
string never trips it; a real `from dotmac_kernel.db import SessionLocal` does.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import dotmac_kernel

APP_ROOT = Path(__file__).resolve().parent.parent.parent / "app"


def _public_names(module: str) -> set[str]:
    """The declared public names of a supported kernel module (its __all__)."""
    mod = importlib.import_module(module)
    return set(getattr(mod, "__all__", ()))


def _iter_app_files():
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def find_surface_violations(rel_path: str, source: str) -> list[str]:
    """Return 'rel:line reason' for every assembly import of an undocumented
    kernel name or an unsupported/internal kernel module."""
    violations: list[str] = []
    top_level = set(dotmac_kernel.__all__)
    supported = dotmac_kernel.SUPPORTED_MODULES
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "dotmac_kernel":
                for alias in node.names:
                    if alias.name not in top_level:
                        violations.append(
                            f"{rel_path}:{node.lineno} `from dotmac_kernel import "
                            f"{alias.name}` — not in the curated top-level __all__"
                        )
            elif mod.startswith("dotmac_kernel."):
                if mod not in supported:
                    violations.append(
                        f"{rel_path}:{node.lineno} imports from `{mod}` — not a "
                        "SUPPORTED_MODULE (internal/undocumented kernel module)"
                    )
                    continue
                allowed = _public_names(mod)
                for alias in node.names:
                    if alias.name not in allowed:
                        violations.append(
                            f"{rel_path}:{node.lineno} `from {mod} import "
                            f"{alias.name}` — not in {mod}.__all__"
                        )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name == "dotmac_kernel":
                    continue
                if name.startswith("dotmac_kernel.") and name not in supported:
                    violations.append(
                        f"{rel_path}:{node.lineno} `import {name}` — not a "
                        "SUPPORTED_MODULE"
                    )
    return violations


def test_assembly_imports_only_public_kernel_surface() -> None:
    violations: list[str] = []
    for path in _iter_app_files():
        rel = "app/" + str(path.relative_to(APP_ROOT)).replace("\\", "/")
        violations.extend(find_surface_violations(rel, path.read_text()))
    assert not violations, (
        "Assembly imports of undocumented kernel names/modules "
        "(add to the module's __all__ + COMPATIBILITY.md if intentionally "
        "public, or stop importing a kernel internal):\n" + "\n".join(violations)
    )


def test_checker_flags_private_and_internal_imports() -> None:
    """Sensitivity self-test — the checker must bite on:
    (a) a name absent from a supported module's __all__,
    (b) an import from an INTERNAL (unsupported) module,
    (c) a top-level name absent from the curated __all__;
    and must NOT flag a legitimate public import."""
    bad_private = "from dotmac_kernel.db import SessionLocal\n"
    assert find_surface_violations(
        "app/_probe_.py", bad_private
    ), "checker missed a private submodule name"

    bad_internal = "from dotmac_kernel.display import DisplaySettings\n"
    assert find_surface_violations(
        "app/_probe_.py", bad_internal
    ), "checker missed an import from an internal module"

    bad_toplevel = "from dotmac_kernel import _some_private\n"
    assert find_surface_violations(
        "app/_probe_.py", bad_toplevel
    ), "checker missed a private top-level name"

    good = (
        "from dotmac_kernel.db import get_db\n"
        "from dotmac_kernel import Party, resolve_value\n"
        "from dotmac_kernel.settings_admin import upsert_by_key\n"
        "# dotmac_kernel.db.SessionLocal in a comment must not trip the checker\n"
    )
    assert find_surface_violations("app/_probe_.py", good) == []


def test_manifest_modules_are_importable_and_declare_all() -> None:
    """Every SUPPORTED_MODULE imports and declares an __all__; INTERNAL and
    SUPPORTED sets are disjoint."""
    assert not (dotmac_kernel.SUPPORTED_MODULES & dotmac_kernel.INTERNAL_MODULES)
    for module in sorted(dotmac_kernel.SUPPORTED_MODULES):
        mod = importlib.import_module(module)
        assert hasattr(mod, "__all__"), f"{module} declares no __all__"
        assert mod.__all__, f"{module}.__all__ is empty"
