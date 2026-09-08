"""Read the kernel's shipped public-export catalogue.

The catalogue is package data so an installed wheel can be inspected without
importing every kernel module (some modules construct application resources at
import time).  It is derived from the same ``SUPPORTED_MODULES`` and
``INTERNAL_MODULES`` declarations that define the source surface, but the
catalogue itself is the immutable, release-verifiable representation.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType

CATALOGUE_SCHEMA = "dotmac.kernel-public-exports.v1"
CATALOGUE_RESOURCE = "public_exports.json"
_ROOT_KEYS = frozenset(
    {"schema", "supported_modules", "internal_modules", "root_exports", "modules"}
)
_ENTRY_KEYS = frozenset({"classification", "status", "exports"})


class PublicExportsFormatError(ValueError):
    """The installed public-export catalogue is unreadable or inconsistent."""


@dataclass(frozen=True)
class PublicExportCatalogue:
    """Validated module export declarations and the exact source bytes."""

    supported_modules: frozenset[str]
    internal_modules: frozenset[str]
    root_exports: tuple[str, ...]
    modules: Mapping[str, tuple[str, ...] | None]
    canonical_bytes: bytes

    @property
    def digest(self) -> str:
        """Digest the exact package-data bytes for a future release record."""
        return "sha256:" + hashlib.sha256(self.canonical_bytes).hexdigest()

    def exports_for(self, module: str) -> tuple[str, ...]:
        """Return exports, refusing a module with no explicit ``__all__``."""
        try:
            exports = self.modules[module]
        except KeyError as exc:
            raise PublicExportsFormatError(
                f"module {module!r} is absent from the public-export catalogue"
            ) from exc
        if exports is None:
            raise PublicExportsFormatError(
                f"module {module!r} has no explicit __all__ export declaration"
            )
        return exports


def _validate(
    document: object,
) -> tuple[
    frozenset[str],
    frozenset[str],
    tuple[str, ...],
    Mapping[str, tuple[str, ...] | None],
]:
    if not isinstance(document, dict):
        raise PublicExportsFormatError("catalogue must be a JSON object")
    if set(document) != _ROOT_KEYS:
        raise PublicExportsFormatError("catalogue has unknown or missing root fields")
    if document.get("schema") != CATALOGUE_SCHEMA:
        raise PublicExportsFormatError(f"catalogue schema must be {CATALOGUE_SCHEMA!r}")
    supported = document.get("supported_modules")
    internal = document.get("internal_modules")
    root_exports = document.get("root_exports")
    modules = document.get("modules")
    if (
        not isinstance(supported, list)
        or not isinstance(internal, list)
        or not isinstance(root_exports, list)
        or not isinstance(modules, dict)
        or not modules
        or set(supported) & set(internal)
        or supported != sorted(set(supported))
        or internal != sorted(set(internal))
        or root_exports != sorted(set(root_exports))
        or any(
            not isinstance(value, str)
            for value in (*supported, *internal, *root_exports)
        )
    ):
        raise PublicExportsFormatError("catalogue modules must be a JSON object")
    classified = set(supported) | set(internal)
    if set(modules) != classified:
        raise PublicExportsFormatError("catalogue classifications and modules differ")
    result: dict[str, tuple[str, ...] | None] = {}
    for module, entry in modules.items():
        if not isinstance(module, str) or not (
            module == "dotmac_kernel" or module.startswith("dotmac_kernel.")
        ):
            raise PublicExportsFormatError(
                "catalogue module names must be kernel names"
            )
        if not isinstance(entry, dict):
            raise PublicExportsFormatError(
                f"catalogue entry for {module!r} is not an object"
            )
        if set(entry) != _ENTRY_KEYS:
            raise PublicExportsFormatError(
                f"catalogue entry for {module!r} has unknown or missing fields"
            )
        classification = entry.get("classification")
        if classification not in {"supported", "internal"}:
            raise PublicExportsFormatError(f"module {module!r} classification differs")
        if (classification == "supported") != (module in supported):
            raise PublicExportsFormatError(
                f"module {module!r} classification is misplaced"
            )
        status = entry.get("status")
        exports = entry.get("exports")
        if status == "unavailable":
            if exports is not None:
                raise PublicExportsFormatError(
                    f"unavailable module {module!r} must have null exports"
                )
            result[module] = None
            continue
        if status != "declared" or not isinstance(exports, list):
            raise PublicExportsFormatError(
                f"module {module!r} must carry a declared export list or an "
                "unavailable state"
            )
        if any(not isinstance(name, str) or not name for name in exports):
            raise PublicExportsFormatError(
                f"module {module!r} has invalid export names"
            )
        if exports != sorted(set(exports)):
            raise PublicExportsFormatError(
                f"module {module!r} exports must be unique and sorted"
            )
        result[module] = tuple(exports)
    return (
        frozenset(supported),
        frozenset(internal),
        tuple(root_exports),
        MappingProxyType(result),
    )


def _module_path(source_root: Path, module: str) -> Path:
    relative = Path(*module.split("."))
    source = source_root.parent / relative.with_suffix(".py")
    if source.is_file():
        return source
    package = source_root.parent / relative / "__init__.py"
    if package.is_file():
        return package
    raise PublicExportsFormatError(f"catalogue module {module!r} has no source file")


def _source_exports(source: Path) -> tuple[str, ...] | None:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    bindings: set[str] = set()

    def assigned_names(statement: ast.stmt) -> set[str]:
        if isinstance(statement, ast.Import | ast.ImportFrom):
            return {
                alias.asname or alias.name.split(".")[0] for alias in statement.names
            }
        if isinstance(statement, ast.Assign):
            return {
                target.id
                for target in statement.targets
                if isinstance(target, ast.Name)
            }
        if (
            isinstance(statement, ast.AnnAssign)
            and statement.value is not None
            and isinstance(statement.target, ast.Name)
        ):
            return {statement.target.id}
        return set()

    def lazy_exports(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
        """Recognise only a branch that returns a name bound in that branch."""
        if not node.args.args:
            return set()
        argument = node.args.args[0].arg
        resolved: set[str] = set()
        for branch in node.body:
            if not isinstance(branch, ast.If):
                continue
            comparison = branch.test
            if not (
                isinstance(comparison, ast.Compare)
                and isinstance(comparison.left, ast.Name)
                and comparison.left.id == argument
                and len(comparison.ops) == 1
                and isinstance(comparison.ops[0], ast.Eq)
                and len(comparison.comparators) == 1
                and isinstance(comparison.comparators[0], ast.Constant)
                and isinstance(comparison.comparators[0].value, str)
            ):
                continue
            if not (
                len(branch.body) == 2
                and isinstance(branch.body[0], ast.Import | ast.ImportFrom)
                and isinstance(branch.body[1], ast.Return)
                and isinstance(branch.body[1].value, ast.Name)
            ):
                continue
            branch_bindings = assigned_names(branch.body[0])
            if branch.body[1].value.id in branch_bindings:
                resolved.add(comparison.comparators[0].value)
        return resolved

    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bindings.add(node.name)
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name == "__getattr__"
            ):
                bindings.update(lazy_exports(node))
        else:
            bindings.update(assigned_names(node))
    declaration: ast.Assign | ast.AnnAssign | None = None
    declared_exports: tuple[str, ...] | None = None
    for node in tree.body:
        candidate: ast.Assign | ast.AnnAssign | None = None
        if isinstance(node, ast.Assign):
            candidate = node
            targets = candidate.targets
        elif isinstance(node, ast.AnnAssign):
            candidate = node
            targets = [candidate.target]
        else:
            targets = []
        if not any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in targets
        ):
            continue
        if candidate is None:
            raise PublicExportsFormatError(
                f"module {source.stem!r} has an unreadable __all__ declaration"
            )
        if declaration is not None:
            raise PublicExportsFormatError(
                f"module {source.stem!r} declares __all__ more than once"
            )
        declaration = candidate
        if not isinstance(candidate.value, ast.List | ast.Tuple):
            raise PublicExportsFormatError(
                f"module {source.stem!r} has a non-literal __all__"
            )
        names = [
            item.value
            for item in candidate.value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        ]
        if len(names) != len(candidate.value.elts):
            raise PublicExportsFormatError(
                f"module {source.stem!r} has a non-string __all__ entry"
            )
        if len(names) != len(set(names)):
            raise PublicExportsFormatError(
                f"module {source.stem!r} has duplicate __all__ entries"
            )
        declared_exports = tuple(sorted(names))
        missing = set(names) - bindings
        if missing:
            raise PublicExportsFormatError(
                f"module {source.stem!r} exports names not defined: {sorted(missing)}"
            )
    return declared_exports


def derive_public_exports(source_root: Path) -> bytes:
    """Derive canonical manifest bytes from a source ``dotmac_kernel`` tree.

    This deliberately parses source instead of importing modules.  Importing
    the kernel would make the release identity depend on optional application
    dependencies and import-time side effects.
    """
    init = source_root / "__init__.py"
    try:
        tree = ast.parse(init.read_text(encoding="utf-8"), filename=str(init))
    except (OSError, SyntaxError) as exc:
        raise PublicExportsFormatError("kernel source manifest is unreadable") from exc
    classifications: dict[str, set[str]] = {
        "supported": set(),
        "internal": set(),
    }
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            targets = []
        if not any(
            isinstance(target, ast.Name)
            and target.id in {"SUPPORTED_MODULES", "INTERNAL_MODULES"}
            for target in targets
        ):
            continue
        classification = (
            "supported"
            if any(
                isinstance(target, ast.Name) and target.id == "SUPPORTED_MODULES"
                for target in targets
            )
            else "internal"
        )
        classifications[classification].update(
            value.value
            for value in ast.walk(node)
            if isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and (
                value.value == "dotmac_kernel"
                or value.value.startswith("dotmac_kernel.")
            )
        )
    classified = classifications["supported"] | classifications["internal"]
    discovered: set[str] = set()

    def importable(path: Path) -> bool:
        """Return whether ``path`` belongs to a regular package tree."""
        directory = path.parent
        while True:
            if not (directory / "__init__.py").is_file():
                return False
            if directory == source_root:
                return True
            directory = directory.parent

    for path in source_root.rglob("*.py"):
        if not importable(path):
            continue
        relative = path.relative_to(source_root)
        if relative.name == "__init__.py":
            if relative.parent != Path("."):
                discovered.add("dotmac_kernel." + ".".join(relative.parent.parts))
        else:
            discovered.add("dotmac_kernel." + ".".join(relative.with_suffix("").parts))
    missing = discovered - classified
    stale = classified - discovered
    if missing:
        raise PublicExportsFormatError(
            "catalogue classifications omit importable modules: " f"{sorted(missing)}"
        )
    if stale:
        raise PublicExportsFormatError(
            "catalogue classifications name missing source modules: " f"{sorted(stale)}"
        )
    root_exports = _source_exports(init)
    if root_exports is None:
        raise PublicExportsFormatError("kernel root must declare __all__")
    modules: dict[str, dict[str, object]] = {}
    for module in sorted(classified):
        exports = _source_exports(_module_path(source_root, module))
        modules[module] = {
            "classification": (
                "supported" if module in classifications["supported"] else "internal"
            ),
            "exports": list(exports) if exports is not None else None,
            "status": "declared" if exports is not None else "unavailable",
        }
    return (
        json.dumps(
            {
                "internal_modules": sorted(classifications["internal"]),
                "modules": modules,
                "root_exports": list(root_exports),
                "schema": CATALOGUE_SCHEMA,
                "supported_modules": sorted(classifications["supported"]),
            },
            sort_keys=True,
            indent=2,
        ).encode("utf-8")
        + b"\n"
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PublicExportsFormatError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def parse_public_exports(raw: bytes) -> PublicExportCatalogue:
    """Parse strict catalogue bytes without importing kernel modules."""
    try:
        document = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except PublicExportsFormatError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicExportsFormatError(
            "public-export catalogue is not valid UTF-8 JSON"
        ) from exc
    supported, internal, root_exports, modules = _validate(document)
    return PublicExportCatalogue(supported, internal, root_exports, modules, raw)


def load_public_exports() -> PublicExportCatalogue:
    """Load and validate the catalogue from this installed distribution."""
    resource = files("dotmac_kernel").joinpath(CATALOGUE_RESOURCE)
    raw = resource.read_bytes()
    return parse_public_exports(raw)


__all__ = [
    "CATALOGUE_RESOURCE",
    "CATALOGUE_SCHEMA",
    "PublicExportCatalogue",
    "PublicExportsFormatError",
    "derive_public_exports",
    "load_public_exports",
    "parse_public_exports",
]
