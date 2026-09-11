"""All-of Kernel-successor compatibility from verified product observations.

Products publish only v3 observation coordinates and digests.  This module
owns the decisions: it loads each record from an immutable protected-main
revision, re-derives every observation, derives the four dimensional facts,
and intersects runtime-reached package modules with the Kernel's frozen
``dotmac_kernel.db`` import debt.  Product-authored booleans are never an
input.

Compatibility and adoption are deliberately different outputs.  Incomplete
evidence, an incoherent dimensional record, or reached deferred-import debt
refuses compatibility.  Composition states are reported for inspection but
do not become an adoption verdict; adoption remains ``not_evaluated``.
"""

from __future__ import annotations

import ast
import configparser
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import tomllib
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final

from tools.composition_contract.composition_schema import (
    CURRENT_SCHEMA_VERSION,
    CompositionCoverageReport,
    CompositionRecord,
    CompositionState,
    DimensionalIncoherence,
    DimensionValue,
    IncompatibleSchemaVersion,
    InstallRecipeParseError,
    PackageClassification,
    RuntimeExposureReport,
    build_coverage_report,
    build_runtime_exposure_report,
    composition_record_from_payload,
    derive_composition_state,
    derive_distribution_universe,
    derive_group_optionality,
    derive_installation_dimension,
    derive_lock_group_membership,
    derive_migration_lineage_applicability_from_manifest,
    parse_install_command,
)
from tools.composition_contract.observations import (
    ObservationAcquisitionError,
    ObservationRefusal,
    VerifiedObservationEnvelope,
    read_checkout_json_document,
    verify_observation_envelope,
)
from tools.composition_contract.specs import PRODUCT_OBSERVATION_SPECS

TRUSTED_CONTRACT_REVISION: Final = "8b4b6d4b42e650c47fe4c04a679a5ccb51c4b2cd"
BINDINGS_SCHEMA: Final = "kernel-composition-compatibility-bindings.v3"
PRODUCTS: Final = tuple(PRODUCT_OBSERVATION_SPECS)
IMMUTABLE_COMMIT: Final = re.compile(r"^[0-9a-f]{40}$")
_SAFE_COMPONENT: Final = re.compile(r"^[A-Za-z0-9._-]+$")
_REGULAR_MODES: Final = frozenset({"100644", "100755"})
_HERE: Final = Path(__file__).resolve().parents[2]
DEFAULT_BINDINGS_PATH: Final = (
    _HERE / "tests" / "architecture" / "compatibility_gate_bindings.json"
)
DEFERRED_DEBT_RECORD_PATH: Final = (
    "tests/architecture/conflict_savepoint_package_backlog_baseline.json"
)
TRUSTED_SEMANTIC_PATHS: Final = (
    "tools/composition_contract/composition_schema.py",
    "tools/composition_contract/observations.py",
    "tools/composition_contract/specs.py",
)


class GateConfigurationError(ValueError):
    """The fixed gate configuration is malformed or internally inconsistent."""


class GateAcquisitionError(RuntimeError):
    """A required Git object or checkout could not be read."""


class GateDerivationError(ValueError):
    """Verified source bytes cannot support one unambiguous derivation."""


@dataclass(frozen=True)
class ProductBinding:
    product: str
    revision: str

    def __post_init__(self) -> None:
        if self.product not in PRODUCTS:
            raise GateConfigurationError(f"unknown product {self.product!r}")
        if not isinstance(self.revision, str) or not IMMUTABLE_COMMIT.fullmatch(
            self.revision
        ):
            raise GateConfigurationError(
                f"{self.product}: revision is not an immutable lowercase commit"
            )


@dataclass(frozen=True)
class DeferredDebtReach:
    distribution: str
    source_path: str
    sites: int


@dataclass(frozen=True)
class ProductEvaluation:
    product: str
    revision: str
    compatibility_status: str
    findings: tuple[str, ...]
    coverage: CompositionCoverageReport | None = None
    runtime_exposure: RuntimeExposureReport | None = None
    deferred_debt: tuple[DeferredDebtReach, ...] = ()

    @property
    def evidence_verified(self) -> bool:
        return self.coverage is not None and self.runtime_exposure is not None

    @property
    def compatibility_satisfied(self) -> bool:
        return self.compatibility_status == "satisfied"

    @property
    def adoption_status(self) -> str:
        return "not_evaluated"

    def explain(self) -> str:
        verdict = (
            "COMPATIBILITY SATISFIED"
            if self.compatibility_satisfied
            else f"COMPATIBILITY REFUSED ({self.compatibility_status})"
        )
        details = "; ".join(self.findings) if self.findings else "no findings"
        return (
            f"{verdict}: {self.product} @ {self.revision}; {details}\n"
            f"ADOPTION NOT EVALUATED ({self.product})"
        )


@dataclass(frozen=True)
class GateResult:
    evaluations: tuple[ProductEvaluation, ...]

    @property
    def evidence_verified(self) -> bool:
        return (
            len(self.evaluations) == len(PRODUCTS)
            and {item.product for item in self.evaluations} == set(PRODUCTS)
            and all(item.evidence_verified for item in self.evaluations)
        )

    @property
    def compatibility_satisfied(self) -> bool:
        return self.evidence_verified and all(
            item.compatibility_satisfied for item in self.evaluations
        )

    @property
    def adoption_status(self) -> str:
        return "not_evaluated"

    @property
    def refusing(self) -> tuple[ProductEvaluation, ...]:
        return tuple(
            item for item in self.evaluations if not item.compatibility_satisfied
        )

    def explain(self) -> str:
        if not self.evaluations:
            return (
                "COMPATIBILITY REFUSED: no product was evaluated\n"
                "ADOPTION NOT EVALUATED"
            )
        verdict = (
            "COMPATIBILITY SATISFIED"
            if self.compatibility_satisfied
            else "COMPATIBILITY REFUSED"
        )
        return (
            f"{verdict}: {len(self.refusing)} of {len(self.evaluations)} product "
            "evaluation(s) refused\n"
            + "\n".join(item.explain() for item in self.evaluations)
            + "\nADOPTION NOT EVALUATED"
        )


@dataclass(frozen=True)
class _ProductDerivationSpec:
    clone_env_var: str
    runtime_roots: tuple[str, ...]
    assembly_module: str
    extra_source_modules: Mapping[str, str]


_DERIVATION_SPECS: Final = MappingProxyType(
    {
        "academy": _ProductDerivationSpec(
            "COMPAT_GATE_CLONE_DOTMAC_ACADEMY_APP",
            ("app.main",),
            "app.assembly",
            MappingProxyType({}),
        ),
        "erp": _ProductDerivationSpec(
            "COMPAT_GATE_CLONE_DOTMAC_ERP",
            ("app.main", "app.celery_app"),
            "app.product_assembly",
            MappingProxyType({}),
        ),
        "sub": _ProductDerivationSpec(
            "COMPAT_GATE_CLONE_DOTMAC_SUB",
            ("app.main", "scripts.migration.collections_module_shadow_parity"),
            "app.composition",
            MappingProxyType(
                {
                    "scripts.migration.collections_module_shadow_parity": (
                        "collections-operator-entry-point"
                    )
                }
            ),
        ),
    }
)


def _strict_json(data: bytes, *, source: str) -> Mapping[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise GateConfigurationError(f"{source}: duplicate field {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(data, object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateConfigurationError(f"{source}: not strict UTF-8 JSON") from exc
    if not isinstance(value, Mapping):
        raise GateConfigurationError(f"{source}: root must be an object")
    return value


def load_bindings(path: Path = DEFAULT_BINDINGS_PATH) -> Mapping[str, ProductBinding]:
    document = _strict_json(path.read_bytes(), source=str(path))
    if set(document) != {"schema", "bindings"}:
        raise GateConfigurationError(
            "bindings must contain exactly schema and bindings"
        )
    if document["schema"] != BINDINGS_SCHEMA:
        raise GateConfigurationError(
            f"bindings schema must be {BINDINGS_SCHEMA!r}, got {document['schema']!r}"
        )
    rows = document["bindings"]
    if not isinstance(rows, Mapping) or set(rows) != set(PRODUCTS):
        raise GateConfigurationError(f"bindings must name exactly {list(PRODUCTS)!r}")
    result: dict[str, ProductBinding] = {}
    for product in PRODUCTS:
        row = rows[product]
        if not isinstance(row, Mapping) or set(row) != {"revision"}:
            raise GateConfigurationError(
                f"bindings[{product!r}] must contain exactly revision"
            )
        revision = row["revision"]
        if not isinstance(revision, str):
            raise GateConfigurationError(
                f"bindings[{product!r}].revision must be a string"
            )
        result[product] = ProductBinding(product, revision)
    return MappingProxyType(result)


def clone_paths_from_environment() -> Mapping[str, Path]:
    result: dict[str, Path] = {}
    for product, spec in _DERIVATION_SPECS.items():
        raw = os.environ.get(spec.clone_env_var)
        if not raw:
            raise GateAcquisitionError(f"{spec.clone_env_var} is unset")
        path = Path(raw)
        probe = subprocess.run(  # noqa: S603
            ["git", "-C", str(path), "rev-parse", "--git-dir"],  # noqa: S607
            check=False,
            capture_output=True,
        )
        if probe.returncode != 0:
            raise GateAcquisitionError(
                f"{spec.clone_env_var}={raw!r} is not a usable Git repository"
            )
        result[product] = path
    return MappingProxyType(result)


def _git_bytes(
    repository: Path,
    arguments: Sequence[str],
    *,
    accepted: frozenset[int] = frozenset({0}),
) -> bytes:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", str(repository), *arguments],  # noqa: S607
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise GateAcquisitionError(
            f"could not launch git {arguments[0]!r}: {exc}"
        ) from exc
    if result.returncode not in accepted:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        raise GateAcquisitionError(
            f"git {arguments[0]!r} failed with exit {result.returncode}: "
            f"{diagnostic or '<no diagnostic>'}"
        )
    return result.stdout


def _git_blob(repository: Path, object_id: str, *, source: str) -> bytes:
    size_bytes = _git_bytes(repository, ("cat-file", "-s", object_id)).strip()
    try:
        size = int(size_bytes)
    except ValueError as exc:
        raise GateAcquisitionError(
            f"{source}: Git returned a non-integer size"
        ) from exc
    if size > 1024 * 1024:
        raise GateDerivationError(f"{source}: Python source exceeds the 1 MiB bound")
    content = _git_bytes(repository, ("cat-file", "blob", object_id))
    if len(content) != size:
        raise GateAcquisitionError(
            f"{source}: Git reported {size} bytes but returned {len(content)}"
        )
    return content


def _git_show(repository: Path, revision: str, path: str) -> bytes:
    return _git_bytes(repository, ("show", f"{revision}:{path}"))


def _require_trusted_contract_sources(repository_root: Path) -> None:
    """Refuse when executing helpers differ from the pinned contract.

    Product observations name ``TRUSTED_CONTRACT_REVISION``. Loading current
    working-tree helper modules while deriving the catalogue from that revision
    would let one evaluation combine two contracts without saying so.
    """

    for relative in TRUSTED_SEMANTIC_PATHS:
        path = repository_root / relative
        try:
            current = path.read_bytes()
        except OSError as exc:
            raise GateAcquisitionError(
                f"trusted contract source {relative!r} is unreadable: {exc}"
            ) from exc
        trusted = _git_show(repository_root, TRUSTED_CONTRACT_REVISION, relative)
        if current != trusted:
            raise GateConfigurationError(
                f"{relative}: executing bytes differ from trusted contract "
                f"revision {TRUSTED_CONTRACT_REVISION}"
            )


@dataclass(frozen=True)
class _TrustedCatalogue:
    packages_root: Path
    import_roots: Mapping[str, str]


@contextmanager
def _trusted_catalogue(repository_root: Path) -> Iterator[_TrustedCatalogue]:
    """Materialize only dossiers/manifests from the pinned contract commit."""

    names = tuple(
        item.decode("utf-8")
        for item in _git_bytes(
            repository_root,
            (
                "ls-tree",
                "-d",
                "-z",
                "--name-only",
                f"{TRUSTED_CONTRACT_REVISION}:packages",
            ),
        ).split(b"\0")
        if item
    )
    if not names:
        raise GateConfigurationError("trusted contract contains no package catalogue")
    with tempfile.TemporaryDirectory(prefix="kernel-composition-catalogue-") as raw:
        packages_root = Path(raw) / "packages"
        packages_root.mkdir()
        roots: dict[str, str] = {}
        for distribution in names:
            if not _SAFE_COMPONENT.fullmatch(distribution):
                raise GateConfigurationError(
                    f"unsafe distribution path in trusted catalogue: {distribution!r}"
                )
            package_dir = packages_root / distribution
            package_dir.mkdir()
            dossier = _git_show(
                repository_root,
                TRUSTED_CONTRACT_REVISION,
                f"packages/{distribution}/EXTRACTION.toml",
            )
            (package_dir / "EXTRACTION.toml").write_bytes(dossier)
            source_roots = tuple(
                item.decode("utf-8")
                for item in _git_bytes(
                    repository_root,
                    (
                        "ls-tree",
                        "-d",
                        "-z",
                        "--name-only",
                        f"{TRUSTED_CONTRACT_REVISION}:packages/{distribution}/src",
                    ),
                ).split(b"\0")
                if item and not item.decode("utf-8").endswith(".egg-info")
            )
            if len(source_roots) != 1 or not source_roots[0].isidentifier():
                raise GateConfigurationError(
                    f"{distribution}: expected one import package, got {source_roots!r}"
                )
            import_root = source_roots[0]
            roots[distribution] = import_root
            import_dir = package_dir / "src" / import_root
            import_dir.mkdir(parents=True)
            parsed = tomllib.loads(dossier.decode("utf-8"))
            if (
                parsed.get("classification")
                == PackageClassification.OPTIONAL_MODULE.value
            ):
                manifest = _git_show(
                    repository_root,
                    TRUSTED_CONTRACT_REVISION,
                    f"packages/{distribution}/src/{import_root}/manifest.py",
                )
                (import_dir / "manifest.py").write_bytes(manifest)
        catalogue = _TrustedCatalogue(packages_root, MappingProxyType(roots))
        # This independently re-reads every materialized dossier and refuses
        # any missing/unknown classification before product facts are derived.
        derive_distribution_universe(packages_root)
        yield catalogue


@dataclass(frozen=True)
class _GitPythonFile:
    path: str
    object_id: str


def _module_name(path: str, prefix: str, module_prefix: str) -> str:
    relative = PurePosixPath(path).relative_to(PurePosixPath(prefix))
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join((module_prefix, *parts))


def _git_python_index(
    repository: Path,
    revision: str,
    prefix: str,
    *,
    module_prefix: str | None = None,
) -> Mapping[str, _GitPythonFile]:
    raw = _git_bytes(
        repository,
        ("ls-tree", "-r", "-z", "--full-tree", revision, "--", prefix),
    )
    index: dict[str, _GitPythonFile] = {}
    for entry in (item for item in raw.split(b"\0") if item):
        try:
            metadata, path_bytes = entry.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode("ascii").split(" ")
            path = path_bytes.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise GateAcquisitionError(
                "git ls-tree returned malformed metadata"
            ) from exc
        if not path.endswith(".py"):
            continue
        if mode not in _REGULAR_MODES or object_type != "blob":
            raise GateDerivationError(f"{path}: Python source is not a regular blob")
        module = _module_name(path, prefix, module_prefix or prefix)
        candidate = _GitPythonFile(path, object_id)
        existing = index.get(module)
        if existing is not None:
            existing_is_package = existing.path.endswith("/__init__.py")
            candidate_is_package = candidate.path.endswith("/__init__.py")
            if existing_is_package == candidate_is_package:
                raise GateDerivationError(f"duplicate Python module {module!r}")
            # Python's path finder resolves a package directory before a
            # same-named module file.  Reproduce that rule explicitly so a
            # source collision is neither refused nor made dependent on Git's
            # lexical tree order.
            if existing_is_package:
                continue
        index[module] = candidate
    return MappingProxyType(index)


def _resolve_relative(
    current: str, node: ast.ImportFrom, is_package: bool
) -> str | None:
    parts = current.split(".")
    base_length = len(parts) if is_package else len(parts) - 1
    keep = base_length - (node.level - 1)
    if keep < 0:
        return None
    anchor = parts[:keep]
    if node.module:
        anchor.extend(node.module.split("."))
    return ".".join(anchor) or None


def _is_type_checking_test(node: ast.expr) -> bool:
    return (isinstance(node, ast.Name) and node.id == "TYPE_CHECKING") or (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "typing"
        and node.attr == "TYPE_CHECKING"
    )


def _possible_import_strings(
    expression: ast.expr,
    *,
    current_module: str,
    assignments: Mapping[str, tuple[ast.expr, ...]],
    dictionary_values: Mapping[str, frozenset[str]],
    resolving: frozenset[str] = frozenset(),
) -> frozenset[str] | None:
    """Resolve bounded string data flow into a dynamic import target.

    This deliberately models values consumed by the import call, not every
    module-shaped string in the file.  The latter makes inventories and prose
    into dependency edges.  Multiple statically possible values are retained
    as a conservative union; an unmodelled value refuses the measurement.
    """

    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return frozenset({expression.value})
    if isinstance(expression, ast.Name):
        if expression.id == "__name__":
            return frozenset({current_module})
        if expression.id in resolving:
            return None
        candidates = assignments.get(expression.id)
        if not candidates:
            return None
        resolved: set[str] = set()
        for candidate in candidates:
            candidate_values = _possible_import_strings(
                candidate,
                current_module=current_module,
                assignments=assignments,
                dictionary_values=dictionary_values,
                resolving=resolving | {expression.id},
            )
            if candidate_values is None:
                return None
            resolved.update(candidate_values)
        return frozenset(resolved)
    if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add):
        left = _possible_import_strings(
            expression.left,
            current_module=current_module,
            assignments=assignments,
            dictionary_values=dictionary_values,
            resolving=resolving,
        )
        right = _possible_import_strings(
            expression.right,
            current_module=current_module,
            assignments=assignments,
            dictionary_values=dictionary_values,
            resolving=resolving,
        )
        if left is None or right is None:
            return None
        return frozenset(a + b for a in left for b in right)
    if isinstance(expression, ast.JoinedStr):
        joined_values: set[str] = {""}
        for item in expression.values:
            item_values: frozenset[str] | None
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                item_values = frozenset({item.value})
            elif isinstance(item, ast.FormattedValue):
                item_values = _possible_import_strings(
                    item.value,
                    current_module=current_module,
                    assignments=assignments,
                    dictionary_values=dictionary_values,
                    resolving=resolving,
                )
            else:
                return None
            if item_values is None:
                return None
            joined_values = {
                prefix + suffix for prefix in joined_values for suffix in item_values
            }
        return frozenset(joined_values)
    if (
        isinstance(expression, ast.Call)
        and isinstance(expression.func, ast.Attribute)
        and expression.func.attr == "get"
        and isinstance(expression.func.value, ast.Name)
    ):
        mapping = dictionary_values.get(expression.func.value.id)
        if mapping:
            return mapping
    return None


def _table_driven_import_strings(
    tree: ast.AST, imported_name: str
) -> frozenset[str] | None:
    """Trace a tuple field from a looped literal table into an importer.

    Products use this shape for lazy router loading: a module-level list of
    tuples is iterated, one tuple is unpacked, and its module field is passed
    through a small forwarding function.  The linkage is structural at every
    hop; unrelated module-shaped literals are never candidates.
    """

    tables: dict[str, ast.List | ast.Tuple | ast.Set] = {}
    row_sources: dict[str, str] = {}
    field_positions: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.List | ast.Tuple | ast.Set)
        ):
            tables[node.targets[0].id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.List | ast.Tuple | ast.Set)
        ):
            tables[node.target.id] = node.value
        elif (
            isinstance(node, ast.For | ast.AsyncFor)
            and isinstance(node.target, ast.Name)
            and isinstance(node.iter, ast.Name)
        ):
            row_sources[node.target.id] = node.iter.id
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Tuple | ast.List)
            and isinstance(node.value, ast.Name)
        ):
            for position, target in enumerate(node.targets[0].elts):
                if isinstance(target, ast.Name) and target.id == imported_name:
                    field_positions.add((node.value.id, position))
    values: set[str] = set()
    direct_table = tables.get(row_sources.get(imported_name, ""))
    if direct_table is not None:
        for item in direct_table.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return None
            values.add(item.value)
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == imported_name
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.In)
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Name)
        ):
            continue
        guarded_table = tables.get(node.comparators[0].id)
        if guarded_table is None:
            continue
        for item in guarded_table.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return None
            values.add(item.value)
    for row_name, position in field_positions:
        table = tables.get(row_sources.get(row_name, ""))
        if table is None or isinstance(table, ast.Set):
            continue
        for row in table.elts:
            if not isinstance(row, ast.Tuple | ast.List) or position >= len(row.elts):
                return None
            item = row.elts[position]
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return None
            values.add(item.value)
    return frozenset(values) if values else None


def _runtime_manifest_import_strings(tree: ast.AST) -> frozenset[str] | None:
    """Trace registry definitions' ``runtime.module`` into ``find_spec``.

    The result exists only when the file contains the complete structural
    chain: a ``_module_file_size(definition.runtime.module)`` consumer and
    literal ``RuntimeManifest(module=...)`` producers.  A coincidental
    ``module=`` keyword elsewhere is not enough.
    """

    has_consumer = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_module_file_size"
        and node.args
        and isinstance(node.args[0], ast.Attribute)
        and node.args[0].attr == "module"
        and isinstance(node.args[0].value, ast.Attribute)
        and node.args[0].value.attr == "runtime"
        for node in ast.walk(tree)
    )
    if not has_consumer:
        return None
    values: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "RuntimeManifest")
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "RuntimeManifest"
                )
            )
        ):
            continue
        keyword = next((item for item in node.keywords if item.arg == "module"), None)
        if keyword is None:
            continue
        if not isinstance(keyword.value, ast.Constant) or not isinstance(
            keyword.value.value, str
        ):
            return None
        values.add(keyword.value.value)
    return frozenset(values) if values else None


def _forwarded_literal_table_strings(
    tree: ast.AST, imported_name: str
) -> frozenset[str] | None:
    """Resolve a function parameter supplied from a literal module table."""

    tables: dict[str, tuple[str, ...]] = {}
    candidate_parameters = {imported_name}
    for node in ast.walk(tree):
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if not (
            isinstance(target, ast.Name)
            and isinstance(value, ast.List | ast.Tuple | ast.Set)
            and all(
                isinstance(item, ast.Constant) and isinstance(item.value, str)
                for item in value.elts
            )
        ):
            if (
                isinstance(node, ast.For | ast.AsyncFor)
                and isinstance(node.target, ast.Name)
                and node.target.id == imported_name
                and isinstance(node.iter, ast.Name)
            ):
                candidate_parameters.add(node.iter.id)
            continue
        tables[target.id] = tuple(
            item.value
            for item in value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        )

    values: set[str] = set()
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and any(argument.arg in candidate_parameters for argument in node.args.args)
    ]
    for function in functions:
        parameters = (*function.args.posonlyargs, *function.args.args)
        parameter_position = next(
            position
            for position, parameter in enumerate(parameters)
            if parameter.arg in candidate_parameters
        )
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            called_name = (
                call.func.id
                if isinstance(call.func, ast.Name)
                else call.func.attr
                if isinstance(call.func, ast.Attribute)
                else None
            )
            if called_name != function.name:
                continue
            call_position = parameter_position
            if (
                isinstance(call.func, ast.Attribute)
                and parameters
                and parameters[0].arg
                in {
                    "self",
                    "cls",
                }
            ):
                call_position -= 1
            if call_position < 0 or call_position >= len(call.args):
                continue
            argument = call.args[call_position]
            if isinstance(argument, ast.Name):
                values.update(tables.get(argument.id, ()))
    return frozenset(values) if values else None


def _mapping_tuple_import_strings(
    tree: ast.AST, imported_name: str
) -> frozenset[str] | None:
    """Trace one tuple field from a literal mapping into an importer."""

    mappings: dict[str, ast.Dict] = {}
    assignments: dict[str, ast.expr] = {}
    field_sources: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                assignments[target.id] = node.value
                if isinstance(node.value, ast.Dict):
                    mappings[target.id] = node.value
            elif isinstance(target, ast.Tuple | ast.List) and isinstance(
                node.value, ast.Name
            ):
                for position, item in enumerate(target.elts):
                    if isinstance(item, ast.Name) and item.id == imported_name:
                        field_sources.append((node.value.id, position))
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            assignments[node.target.id] = node.value
            if isinstance(node.value, ast.Dict):
                mappings[node.target.id] = node.value
    values: set[str] = set()
    for row_name, position in field_sources:
        row_expression = assignments.get(row_name)
        if not (
            isinstance(row_expression, ast.Call)
            and isinstance(row_expression.func, ast.Attribute)
            and row_expression.func.attr == "get"
            and isinstance(row_expression.func.value, ast.Name)
        ):
            continue
        mapping = mappings.get(row_expression.func.value.id)
        if mapping is None:
            continue
        for row in mapping.values:
            if not isinstance(row, ast.Tuple | ast.List) or position >= len(row.elts):
                return None
            item = row.elts[position]
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return None
            values.add(item.value)
    return frozenset(values) if values else None


def _split_forwarded_import_strings(
    tree: ast.AST, imported_name: str
) -> frozenset[str] | None:
    """Trace ``parameter.rsplit('.', 1)[0]`` from literal call arguments."""

    source_parameters: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Tuple | ast.List)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "rsplit"
            and isinstance(node.value.func.value, ast.Name)
            and len(node.value.args) == 2
            and isinstance(node.value.args[0], ast.Constant)
            and node.value.args[0].value == "."
            and isinstance(node.value.args[1], ast.Constant)
            and node.value.args[1].value == 1
        ):
            continue
        targets = node.targets[0].elts
        if (
            targets
            and isinstance(targets[0], ast.Name)
            and targets[0].id == imported_name
        ):
            source_parameters.add(node.value.func.value.id)
    values: set[str] = set()
    for function in (
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ):
        parameters = (*function.args.posonlyargs, *function.args.args)
        for position, parameter in enumerate(parameters):
            if parameter.arg not in source_parameters:
                continue
            for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                called_name = (
                    call.func.id
                    if isinstance(call.func, ast.Name)
                    else call.func.attr
                    if isinstance(call.func, ast.Attribute)
                    else None
                )
                if called_name != function.name or position >= len(call.args):
                    continue
                argument = call.args[position]
                if not isinstance(argument, ast.Constant) or not isinstance(
                    argument.value, str
                ):
                    return None
                module_path, separator, _ = argument.value.rpartition(".")
                if not separator or not module_path:
                    return None
                values.add(module_path)
    return frozenset(values) if values else None


def _guarded_namespace_import_strings(
    tree: ast.AST,
    imported_name: str,
    index: Mapping[str, _GitPythonFile],
) -> frozenset[str] | None:
    """Resolve a data-driven import bounded by a checked namespace prefix."""

    sources: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Tuple | ast.List)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr in {"split", "rsplit"}
            and isinstance(node.value.func.value, ast.Name)
        ):
            continue
        for target in node.targets[0].elts:
            if isinstance(target, ast.Name) and target.id == imported_name:
                sources.add(node.value.func.value.id)
    prefixes: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "startswith"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in sources
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            continue
        prefix = node.args[0].value
        if not prefix.startswith("app.") or not prefix.endswith("."):
            return None
        prefixes.add(prefix[:-1])
    if not prefixes:
        return None
    return frozenset(
        module
        for module in index
        if any(
            module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes
        )
    )


@dataclass(frozen=True)
class _ImportReachability:
    visited: frozenset[str]
    external_modules: frozenset[str]


def _call_argument(
    node: ast.Call,
    *,
    position: int,
    keyword: str,
    current_module: str,
) -> ast.expr | None:
    """Return one call argument without collapsing absent and ambiguous input."""

    keyword_values = [item.value for item in node.keywords if item.arg == keyword]
    if any(item.arg is None for item in node.keywords):
        raise GateDerivationError(
            f"{current_module}: dynamic import arguments expanded with ** are "
            "not structurally resolvable"
        )
    positional = node.args[position] if position < len(node.args) else None
    if len(keyword_values) > 1 or (positional is not None and keyword_values):
        raise GateDerivationError(
            f"{current_module}: dynamic import argument {keyword!r} is ambiguous"
        )
    if keyword_values:
        return keyword_values[0]
    return positional


def _resolve_relative_dynamic_names(
    *,
    names: frozenset[str],
    packages: frozenset[str] | None,
    importer: str,
    current_module: str,
) -> frozenset[str]:
    """Apply importlib's package semantics or refuse an unresolved relative name."""

    resolved: set[str] = set()
    for name in names:
        if not name:
            raise GateDerivationError(
                f"{current_module}: dynamic import target is empty"
            )
        if not name.startswith("."):
            resolved.add(name)
            continue
        if importer not in {"import_module", "find_spec", "__import__"} or not packages:
            raise GateDerivationError(
                f"{current_module}: relative {importer} target has no structurally "
                "resolved package"
            )
        for package in packages:
            if not package or package.startswith("."):
                raise GateDerivationError(
                    f"{current_module}: dynamic import package is not absolute"
                )
            try:
                resolved.add(importlib.util.resolve_name(name, package))
            except (ImportError, ValueError) as exc:
                raise GateDerivationError(
                    f"{current_module}: relative {importer} target escapes package"
                ) from exc
    return frozenset(resolved)


def _visit_imports(
    *,
    tree: ast.AST,
    current_module: str,
    is_package: bool,
    assignments: Mapping[str, tuple[ast.expr, ...]],
    dictionary_values: Mapping[str, frozenset[str]],
    index: Mapping[str, _GitPythonFile],
    in_memory_sources: Mapping[str, bytes],
    enqueue: Callable[[str], None],
) -> None:
    class Visitor(ast.NodeVisitor):
        def visit_If(self, node: ast.If) -> None:
            if _is_type_checking_test(node.test):
                for statement in node.orelse:
                    self.visit(statement)
                return
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                enqueue(alias.name)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            base = (
                _resolve_relative(current_module, node, is_package)
                if node.level
                else node.module
            )
            if not base:
                return
            enqueue(base)
            for alias in node.names:
                if alias.name != "*":
                    candidate = f"{base}.{alias.name}"
                    if candidate in index or candidate in in_memory_sources:
                        enqueue(candidate)

        def visit_Call(self, node: ast.Call) -> None:
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in {"import_module", "__import__", "find_spec"}:
                target_expression = _call_argument(
                    node,
                    position=0,
                    keyword="name",
                    current_module=current_module,
                )
                if target_expression is None:
                    raise GateDerivationError(
                        f"{current_module}: dynamic import target is absent"
                    )
                targets = _possible_import_strings(
                    target_expression,
                    current_module=current_module,
                    assignments=assignments,
                    dictionary_values=dictionary_values,
                )
                if targets is None and isinstance(target_expression, ast.Name):
                    targets = _table_driven_import_strings(tree, target_expression.id)
                if targets is None and isinstance(target_expression, ast.Name):
                    targets = _forwarded_literal_table_strings(
                        tree, target_expression.id
                    )
                if targets is None and isinstance(target_expression, ast.Name):
                    targets = _mapping_tuple_import_strings(tree, target_expression.id)
                if targets is None and isinstance(target_expression, ast.Name):
                    targets = _split_forwarded_import_strings(
                        tree, target_expression.id
                    )
                if targets is None and isinstance(target_expression, ast.Name):
                    targets = _guarded_namespace_import_strings(
                        tree, target_expression.id, index
                    )
                if targets is None and name == "find_spec":
                    targets = _runtime_manifest_import_strings(tree)
                if targets is None:
                    raise GateDerivationError(
                        f"{current_module}: dynamic import target is not "
                        "structurally resolvable"
                    )
                package_values: frozenset[str] | None = None
                if name == "__import__":
                    level_expression = _call_argument(
                        node,
                        position=4,
                        keyword="level",
                        current_module=current_module,
                    )
                    if level_expression is None:
                        level = 0
                    elif (
                        isinstance(level_expression, ast.Constant)
                        and isinstance(level_expression.value, int)
                        and not isinstance(level_expression.value, bool)
                        and level_expression.value >= 0
                    ):
                        level = level_expression.value
                    else:
                        raise GateDerivationError(
                            f"{current_module}: __import__ level is not a "
                            "structurally resolved non-negative integer"
                        )
                    if level:
                        package = (
                            current_module
                            if is_package
                            else current_module.rpartition(".")[0]
                        )
                        if not package or any(
                            target.startswith(".") for target in targets
                        ):
                            raise GateDerivationError(
                                f"{current_module}: relative __import__ target has "
                                "no unambiguous package"
                            )
                        targets = frozenset(
                            f"{'.' * level}{target}" for target in targets
                        )
                        package_values = frozenset({package})
                if any(target.startswith(".") for target in targets):
                    if name != "__import__":
                        package_expression = _call_argument(
                            node,
                            position=1,
                            keyword="package",
                            current_module=current_module,
                        )
                        if package_expression is not None:
                            package_values = _possible_import_strings(
                                package_expression,
                                current_module=current_module,
                                assignments=assignments,
                                dictionary_values=dictionary_values,
                            )
                            if package_values is None:
                                raise GateDerivationError(
                                    f"{current_module}: dynamic import package is not "
                                    "structurally resolvable"
                                )
                targets = _resolve_relative_dynamic_names(
                    names=targets,
                    packages=package_values,
                    importer=name,
                    current_module=current_module,
                )
                for target in targets:
                    enqueue(target)
            if name == "autodiscover_tasks":
                values = _call_argument(
                    node,
                    position=0,
                    keyword="packages",
                    current_module=current_module,
                )
                if values is None:
                    raise GateDerivationError(
                        f"{current_module}: autodiscover packages are absent"
                    )
                related_expression = _call_argument(
                    node,
                    position=1,
                    keyword="related_name",
                    current_module=current_module,
                )
                if related_expression is None:
                    related_name: str | None = "tasks"
                elif isinstance(related_expression, ast.Constant) and (
                    isinstance(related_expression.value, str)
                    or related_expression.value is None
                ):
                    related_name = related_expression.value
                else:
                    raise GateDerivationError(
                        f"{current_module}: autodiscover related_name is not "
                        "structurally resolvable"
                    )
                if isinstance(values, ast.List | ast.Tuple):
                    for item in values.elts:
                        if isinstance(item, ast.Constant) and isinstance(
                            item.value, str
                        ):
                            enqueue(item.value)
                            if related_name:
                                enqueue(f"{item.value}.{related_name}")
                        else:
                            raise GateDerivationError(
                                f"{current_module}: autodiscover target is not "
                                "structurally resolvable"
                            )
                else:
                    raise GateDerivationError(
                        f"{current_module}: autodiscover target is not "
                        "structurally resolvable"
                    )
            self.generic_visit(node)

    Visitor().visit(tree)


def _walk_import_graph(
    *,
    repository: Path,
    index: Mapping[str, _GitPythonFile],
    roots: Sequence[str],
    in_memory_sources: Mapping[str, bytes] = MappingProxyType({}),
) -> _ImportReachability:
    queue = deque(dict.fromkeys(roots))
    visited: set[str] = set()
    external: set[str] = set()

    def enqueue(module: str) -> None:
        if module in index or module in in_memory_sources:
            if module not in visited:
                queue.append(module)
        else:
            external.add(module)

    while queue:
        module = queue.popleft()
        if module in visited:
            continue
        source_item = index.get(module)
        source_bytes = in_memory_sources.get(module)
        if source_bytes is None:
            if source_item is None:
                raise GateDerivationError(f"entry module {module!r} is absent")
            source_bytes = _git_blob(
                repository, source_item.object_id, source=source_item.path
            )
        try:
            source = source_bytes.decode("utf-8")
            tree = ast.parse(source)
        except (UnicodeDecodeError, SyntaxError) as exc:
            raise GateDerivationError(
                f"{module}: reachable source does not parse"
            ) from exc
        visited.add(module)
        is_package = source_item is not None and source_item.path.endswith(
            "/__init__.py"
        )
        assignments: dict[str, list[ast.expr]] = {}
        dictionary_values: dict[str, frozenset[str]] = {}
        literal_tables: dict[str, tuple[str, ...]] = {}
        for candidate in ast.walk(tree):
            target: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(candidate, ast.Assign) and len(candidate.targets) == 1:
                target, value = candidate.targets[0], candidate.value
            elif isinstance(candidate, ast.AnnAssign):
                target, value = candidate.target, candidate.value
            if not isinstance(target, ast.Name) or value is None:
                continue
            assignments.setdefault(target.id, []).append(value)
            if isinstance(value, ast.List | ast.Tuple | ast.Set) and all(
                isinstance(item, ast.Constant) and isinstance(item.value, str)
                for item in value.elts
            ):
                literal_tables[target.id] = tuple(
                    item.value
                    for item in value.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                )
            if isinstance(value, ast.Dict):
                values = {
                    item.value
                    for item in value.values
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                }
                if (
                    all(
                        isinstance(item, ast.Constant) and isinstance(item.value, str)
                        for item in value.values
                    )
                    and values
                ):
                    dictionary_values[target.id] = frozenset(values)
        for candidate in ast.walk(tree):
            if (
                isinstance(candidate, ast.Compare)
                and isinstance(candidate.left, ast.Name)
                and len(candidate.ops) == 1
                and isinstance(candidate.ops[0], ast.In)
                and len(candidate.comparators) == 1
                and isinstance(candidate.comparators[0], ast.Name)
            ):
                for literal in literal_tables.get(candidate.comparators[0].id, ()):
                    assignments.setdefault(candidate.left.id, []).append(
                        ast.Constant(value=literal)
                    )
        functions = [
            item
            for item in ast.walk(tree)
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        calls = [item for item in ast.walk(tree) if isinstance(item, ast.Call)]
        for function in functions:
            parameters = (*function.args.posonlyargs, *function.args.args)
            for call in calls:
                called_name = (
                    call.func.id
                    if isinstance(call.func, ast.Name)
                    else call.func.attr
                    if isinstance(call.func, ast.Attribute)
                    else None
                )
                if called_name != function.name:
                    continue
                for position, parameter in enumerate(parameters):
                    call_position = position
                    if (
                        isinstance(call.func, ast.Attribute)
                        and parameters
                        and parameters[0].arg
                        in {
                            "self",
                            "cls",
                        }
                    ):
                        call_position -= 1
                    if call_position < 0 or call_position >= len(call.args):
                        continue
                    argument = call.args[call_position]
                    if isinstance(argument, ast.Constant) and isinstance(
                        argument.value, str
                    ):
                        assignments.setdefault(parameter.arg, []).append(argument)
                        continue
                    if not isinstance(argument, ast.Name):
                        continue
                    for literal in literal_tables.get(argument.id, ()):
                        assignments.setdefault(parameter.arg, []).append(
                            ast.Constant(value=literal)
                        )
        frozen_assignments = {
            name: tuple(values) for name, values in assignments.items()
        }

        _visit_imports(
            tree=tree,
            current_module=module,
            is_package=is_package,
            assignments=frozen_assignments,
            dictionary_values=dictionary_values,
            index=index,
            in_memory_sources=in_memory_sources,
            enqueue=enqueue,
        )

    return _ImportReachability(frozenset(visited), frozenset(external))


def _observation_bytes(envelope: VerifiedObservationEnvelope) -> Mapping[str, bytes]:
    return MappingProxyType(
        {item.claim.observation_id: item.extracted for item in envelope.observations}
    )


def _decode(observations: Mapping[str, bytes], name: str) -> str:
    try:
        return observations[name].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateDerivationError(f"observation {name!r} is not UTF-8") from exc


def _module_registrations(
    assembly_source: str,
    expression_source: str,
    import_roots: Mapping[str, str],
) -> frozenset[str]:
    try:
        tree = ast.parse(assembly_source)
        expression = ast.parse(expression_source, mode="eval").body
    except SyntaxError as exc:
        raise GateDerivationError("module-registration source does not parse") from exc
    imports: dict[str, str] = {}
    assignments: dict[str, ast.expr] = {}
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom) and statement.module:
            for alias in statement.names:
                imports[alias.asname or alias.name] = statement.module
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                imports[alias.asname or alias.name.split(".")[0]] = alias.name
        value = None
        target = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target, value = statement.targets[0], statement.value
        elif isinstance(statement, ast.AnnAssign):
            target, value = statement.target, statement.value
        if isinstance(target, ast.Name) and value is not None:
            assignments[target.id] = value
    root_to_distribution = {
        root: distribution for distribution, root in import_roots.items()
    }
    found: set[str] = set()
    resolving: set[str] = set()

    def walk(node: ast.expr) -> None:
        if isinstance(node, ast.Name):
            origin = imports.get(node.id)
            if origin:
                distribution = root_to_distribution.get(origin.split(".")[0])
                if distribution and ".manifest" in origin:
                    found.add(distribution)
                return
            if node.id in assignments:
                if node.id in resolving:
                    raise GateDerivationError(
                        f"cyclic module-registration assignment {node.id!r}"
                    )
                resolving.add(node.id)
                walk(assignments[node.id])
                resolving.remove(node.id)
                return
            raise GateDerivationError(
                f"unresolved module-registration name {node.id!r}"
            )
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            origin = imports.get(node.value.id, node.value.id)
            distribution = root_to_distribution.get(origin.split(".")[0])
            if distribution and node.attr == "module":
                found.add(distribution)
                return
            raise GateDerivationError("unmodelled module-registration attribute")
        if isinstance(node, ast.List | ast.Tuple | ast.Set):
            for element in node.elts:
                walk(element)
            return
        if isinstance(node, ast.Starred):
            walk(node.value)
            return
        if isinstance(node, ast.Call):
            # Product-owned FeatureManifest declarations are vocabulary, not
            # installable ModuleManifest registrations.
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name == "FeatureManifest":
                return
        raise GateDerivationError(
            f"unmodelled module-registration expression {type(node).__name__}"
        )

    walk(expression)
    return frozenset(found)


def _migration_lineages(source: str, import_roots: Mapping[str, str]) -> frozenset[str]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(source)
        locations = parser.get("alembic", "version_locations", fallback="")
    except configparser.Error as exc:
        raise GateDerivationError("migration config does not parse") from exc
    root_to_distribution = {
        root: distribution for distribution, root in import_roots.items()
    }
    lineages: set[str] = set()
    for token in locations.split():
        package, separator, resource = token.partition(":")
        if not separator or resource != "versions" or ".migrations" not in package:
            continue
        root = package.split(".")[0]
        distribution = root_to_distribution.get(root)
        if distribution:
            lineages.add(distribution)
    return frozenset(lineages)


def _product_runtime_reachability(
    *,
    product: str,
    clone: Path,
    revision: str,
    observations: Mapping[str, bytes],
) -> _ImportReachability:
    spec = _DERIVATION_SPECS[product]
    index = dict(_git_python_index(clone, revision, "app"))
    extra_sources = {
        module: observations[observation_id]
        for module, observation_id in spec.extra_source_modules.items()
    }
    return _walk_import_graph(
        repository=clone,
        index=MappingProxyType(index),
        roots=spec.runtime_roots,
        in_memory_sources=MappingProxyType(extra_sources),
    )


def _derive_records(
    *,
    product: str,
    envelope: VerifiedObservationEnvelope,
    clone: Path,
    catalogue: _TrustedCatalogue,
) -> tuple[tuple[CompositionRecord, ...], _ImportReachability]:
    observations = _observation_bytes(envelope)
    try:
        pyproject = tomllib.loads(_decode(observations, "dependency-manifest"))
        lock = tomllib.loads(_decode(observations, "dependency-lock"))
    except tomllib.TOMLDecodeError as exc:
        raise GateDerivationError("dependency manifest or lock is not TOML") from exc
    try:
        recipe = parse_install_command(
            _decode(observations, "production-install"),
            source="production-install",
        )
    except InstallRecipeParseError as exc:
        raise GateDerivationError(f"production install recipe refused: {exc}") from exc
    membership = derive_lock_group_membership(lock)
    optionality = derive_group_optionality(pyproject)
    reachability = _product_runtime_reachability(
        product=product,
        clone=clone,
        revision=envelope.product_revision,
        observations=observations,
    )
    derivation_spec = _DERIVATION_SPECS[product]
    assembly_reached = derivation_spec.assembly_module in reachability.visited
    registered = (
        _module_registrations(
            _decode(observations, "product-assembly-source"),
            _decode(observations, "module-registration"),
            catalogue.import_roots,
        )
        if assembly_reached
        else frozenset()
    )
    lineages = _migration_lineages(
        _decode(observations, "migration-config"), catalogue.import_roots
    )
    reached_roots = {name.split(".")[0] for name in reachability.external_modules}
    dossiers = derive_distribution_universe(catalogue.packages_root)
    rows: list[CompositionRecord] = []
    for dossier in dossiers:
        registration_applies = dossier.classification.module_registration_applies
        lineage_applies = False
        if dossier.classification is PackageClassification.OPTIONAL_MODULE:
            lineage_applies = derive_migration_lineage_applicability_from_manifest(
                catalogue.packages_root, dossier.distribution
            )
        payload = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "product": PRODUCT_OBSERVATION_SPECS[product].product_id,
            "distribution": dossier.distribution,
            "classification": dossier.classification.value,
            "installation": derive_installation_dimension(
                distribution=dossier.distribution,
                lock_membership=membership,
                recipes=(recipe,),
                group_optionality=optionality,
            ).value,
            "module_registration": (
                (
                    DimensionValue.TRUE
                    if dossier.distribution in registered
                    else DimensionValue.FALSE
                ).value
                if registration_applies
                else DimensionValue.NOT_APPLICABLE.value
            ),
            "migration_lineage": (
                (
                    DimensionValue.TRUE
                    if dossier.distribution in lineages
                    else DimensionValue.FALSE
                ).value
                if lineage_applies
                else DimensionValue.NOT_APPLICABLE.value
            ),
            "runtime_consumption": (
                DimensionValue.TRUE
                if catalogue.import_roots[dossier.distribution] in reached_roots
                else DimensionValue.FALSE
            ).value,
        }
        rows.append(composition_record_from_payload(payload, catalogue.packages_root))
    return tuple(rows), reachability


def _deferred_import_sites(source: bytes, *, path: str) -> int:
    try:
        tree = ast.parse(source.decode("utf-8"), filename=path)
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise GateConfigurationError(
            f"trusted deferred-import source does not parse: {path}"
        ) from exc
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "dotmac_kernel.db"
        and any(alias.name == "conflict_savepoint" for alias in node.names)
    )


def _derive_deferred_import_debt(repository_root: Path) -> Mapping[str, int]:
    raw = _git_bytes(
        repository_root,
        (
            "grep",
            "-l",
            "-z",
            "--fixed-strings",
            "dotmac_kernel.db",
            TRUSTED_CONTRACT_REVISION,
            "--",
            "packages",
        ),
        accepted=frozenset({0, 1}),
    )
    derived: dict[str, int] = {}
    prefix = f"{TRUSTED_CONTRACT_REVISION}:".encode()
    for entry in (item for item in raw.split(b"\0") if item):
        try:
            if not entry.startswith(prefix):
                raise ValueError
            path = entry.removeprefix(prefix).decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise GateAcquisitionError(
                "git grep returned malformed deferred-debt metadata"
            ) from exc
        if not path.endswith(".py"):
            continue
        count = _deferred_import_sites(
            _git_show(repository_root, TRUSTED_CONTRACT_REVISION, path), path=path
        )
        if count:
            derived[path] = count
    return MappingProxyType(derived)


def _load_debt(repository_root: Path) -> Mapping[str, int]:
    document = _strict_json(
        _git_show(
            repository_root,
            TRUSTED_CONTRACT_REVISION,
            DEFERRED_DEBT_RECORD_PATH,
        ),
        source=f"{TRUSTED_CONTRACT_REVISION}:{DEFERRED_DEBT_RECORD_PATH}",
    )
    files = document.get("files")
    total = document.get("total")
    if not isinstance(files, Mapping) or not files:
        raise GateConfigurationError("deferred-import debt files must be non-empty")
    parsed: dict[str, int] = {}
    for source_path, count in files.items():
        if not isinstance(source_path, str) or not isinstance(count, int) or count <= 0:
            raise GateConfigurationError("deferred-import debt has an invalid entry")
        parts = PurePosixPath(source_path).parts
        if len(parts) < 5 or parts[0] != "packages" or parts[2] != "src":
            raise GateConfigurationError(
                f"deferred-import debt path is outside package source: {source_path!r}"
            )
        parsed[source_path] = count
    if total != sum(parsed.values()):
        raise GateConfigurationError("deferred-import debt total disagrees with files")
    derived = _derive_deferred_import_debt(repository_root)
    if parsed != derived:
        differences = [
            f"{path}: recorded {parsed.get(path, 0)}, derived {derived.get(path, 0)}"
            for path in sorted(set(parsed) | set(derived))
            if parsed.get(path, 0) != derived.get(path, 0)
        ]
        raise GateConfigurationError(
            "deferred-import debt disagrees with trusted source blobs:\n"
            + "\n".join(differences)
        )
    return MappingProxyType(parsed)


def _reached_deferred_debt(
    *,
    repository_root: Path,
    records: Sequence[CompositionRecord],
    product_external_modules: frozenset[str],
    catalogue: _TrustedCatalogue,
    debt: Mapping[str, int],
) -> tuple[DeferredDebtReach, ...]:
    runtime_distributions = {
        record.distribution
        for record in records
        if record.runtime_consumption is DimensionValue.TRUE
    }
    result: list[DeferredDebtReach] = []
    for distribution in sorted(runtime_distributions):
        distribution_debt = {
            path: sites
            for path, sites in debt.items()
            if PurePosixPath(path).parts[1] == distribution
        }
        if not distribution_debt:
            continue
        import_root = catalogue.import_roots[distribution]
        prefix = f"packages/{distribution}/src/{import_root}"
        index = _git_python_index(
            repository_root,
            TRUSTED_CONTRACT_REVISION,
            prefix,
            module_prefix=import_root,
        )
        roots = {import_root}
        roots.update(
            module
            for module in product_external_modules
            if module == import_root or module.startswith(f"{import_root}.")
        )
        reachability = _walk_import_graph(
            repository=repository_root,
            index=index,
            roots=tuple(sorted(roots)),
        )
        for source_path, sites in sorted(distribution_debt.items()):
            module = _module_name(source_path, prefix, import_root)
            if module in reachability.visited:
                result.append(DeferredDebtReach(distribution, source_path, sites))
    return tuple(result)


def _evaluate_product(
    *,
    binding: ProductBinding,
    clone: Path,
    repository_root: Path,
    catalogue: _TrustedCatalogue,
    debt_inventory: Mapping[str, int],
) -> ProductEvaluation:
    spec = PRODUCT_OBSERVATION_SPECS[binding.product]
    try:
        document = read_checkout_json_document(clone, product_revision=binding.revision)
        envelope = verify_observation_envelope(
            document,
            spec=spec,
            product_clone=clone,
            product_revision=binding.revision,
            trusted_contract_revision=TRUSTED_CONTRACT_REVISION,
        )
        records, reachability = _derive_records(
            product=binding.product,
            envelope=envelope,
            clone=clone,
            catalogue=catalogue,
        )
        states = tuple((record, derive_composition_state(record)) for record in records)
        coverage = build_coverage_report(records)
        exposure = build_runtime_exposure_report(records)
        debt = _reached_deferred_debt(
            repository_root=repository_root,
            records=records,
            product_external_modules=reachability.external_modules,
            catalogue=catalogue,
            debt=debt_inventory,
        )
    except ObservationAcquisitionError as exc:
        raise GateAcquisitionError(f"{binding.product}: {exc}") from exc
    except (
        ObservationRefusal,
        GateDerivationError,
        DimensionalIncoherence,
        IncompatibleSchemaVersion,
    ) as exc:
        return ProductEvaluation(
            binding.product,
            binding.revision,
            "evidence_refused",
            (str(exc),),
        )

    incomplete = [
        record.distribution
        for record, state in states
        if state in {CompositionState.EVIDENCE_INCOMPLETE, CompositionState.INVALID}
    ]
    findings: list[str] = []
    if incomplete:
        findings.append(f"incomplete or invalid dimensional evidence: {incomplete!r}")
    if debt:
        findings.extend(
            f"reached deferred dotmac_kernel.db debt: {item.source_path} "
            f"({item.sites} site(s))"
            for item in debt
        )
    status = "satisfied"
    if incomplete:
        status = "evidence_refused"
    elif debt:
        status = "deferred_runtime_debt"
    return ProductEvaluation(
        binding.product,
        binding.revision,
        status,
        tuple(findings),
        coverage,
        exposure,
        debt,
    )


def evaluate_gate(
    bindings: Mapping[str, ProductBinding],
    *,
    clones: Mapping[str, Path],
    repository_root: Path = _HERE,
) -> GateResult:
    if set(bindings) != set(PRODUCTS):
        raise GateConfigurationError(f"gate requires exactly {list(PRODUCTS)!r}")
    if set(clones) != set(PRODUCTS):
        raise GateAcquisitionError(f"clone map requires exactly {list(PRODUCTS)!r}")
    _require_trusted_contract_sources(repository_root)
    debt = _load_debt(repository_root)
    with _trusted_catalogue(repository_root) as catalogue:  # type: _TrustedCatalogue
        evaluations = tuple(
            _evaluate_product(
                binding=bindings[product],
                clone=clones[product],
                repository_root=repository_root,
                catalogue=catalogue,
                debt_inventory=debt,
            )
            for product in PRODUCTS
        )
    return GateResult(evaluations)


__all__ = [
    "BINDINGS_SCHEMA",
    "DEFAULT_BINDINGS_PATH",
    "DeferredDebtReach",
    "GateAcquisitionError",
    "GateConfigurationError",
    "GateDerivationError",
    "GateResult",
    "ProductBinding",
    "ProductEvaluation",
    "TRUSTED_CONTRACT_REVISION",
    "clone_paths_from_environment",
    "evaluate_gate",
    "load_bindings",
]
