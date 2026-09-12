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

Each product's own source is parsed under an interpreter selected from that
product's own bound revision's checked-in Python requirement (PEP 621
``project.requires-python`` or Poetry's ``tool.poetry.dependencies.python``)
-- never from this gate's own host interpreter and never from the product's
evidence record.  Parsing and every AST-dependent import derivation happen in
one short-lived subprocess of the selected trusted interpreter.  Only closed
JSON reachability facts cross back to the host: this module PARSES and WALKS
foreign product source, but it never IMPORTS or EXECUTES it.
"""

from __future__ import annotations

import ast
import base64
import configparser
import importlib.util
import json
import os
import re
import selectors
import shutil
import subprocess
import tempfile
import time
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

BINDINGS_SCHEMA: Final = "kernel-composition-compatibility-bindings.v3"
TRUSTED_CONTRACT_REVISION: Final = "8b4b6d4b42e650c47fe4c04a679a5ccb51c4b2cd"
TRUSTED_SEMANTIC_PATHS: Final = (
    "tools/composition_contract/composition_schema.py",
    "tools/composition_contract/observations.py",
    "tools/composition_contract/specs.py",
)
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


class GateConfigurationError(ValueError):
    """The fixed gate configuration is malformed or internally inconsistent."""


class GateAcquisitionError(RuntimeError):
    """A required Git object or checkout could not be read."""


class GateDerivationError(ValueError):
    """Verified source bytes cannot support one unambiguous derivation."""


class _ForeignSyntaxError(Exception):
    """The selected trusted interpreter could not parse the given source."""


# The trusted interpreter floors this gate may select, ascending.  A product
# declares a range against these; the gate never parses a product under any
# other interpreter, and never under its own (potentially newer) host
# interpreter merely because that happens to be what is running the gate.
TRUSTED_INTERPRETERS: Final = (
    ("python3.11", (3, 11)),
    ("python3.12", (3, 12)),
    ("python3.13", (3, 13)),
)

_SUPPORTED_VERSION_OPERATORS: Final = frozenset({">=", "<=", "==", "!=", ">", "<"})
_VERSION_CLAUSE: Final = re.compile(r"^(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+){0,2})$")


@dataclass(frozen=True)
class _VersionClause:
    operator: str
    version: tuple[int, int, int]


@dataclass(frozen=True)
class _VersionDomain:
    """Canonical meaning of the supported comparison-only specifier subset."""

    lower: tuple[int, int, int] | None
    lower_inclusive: bool
    upper: tuple[int, int, int] | None
    upper_inclusive: bool
    excluded: frozenset[tuple[int, int, int]]
    exact: tuple[int, int, int] | None = None
    empty: bool = False


@dataclass(frozen=True)
class _SelectedInterpreter:
    name: str
    executable: str
    version: tuple[int, int, int]


def _normalize_version(parts: tuple[int, ...]) -> tuple[int, int, int]:
    padded = (*parts, 0, 0)
    return (padded[0], padded[1], padded[2])


def _parse_python_specifier(raw: str, *, source: str) -> tuple[_VersionClause, ...]:
    """Parse a comma-separated PEP 440 comparison specifier.

    Only the plain comparison operators are supported.  Caret/tilde ranges,
    compatible-release (``~=``), wildcards, and any other requirement syntax
    are refused rather than guessed at.
    """

    clauses: list[_VersionClause] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            raise GateDerivationError(f"{source}: empty version clause in {raw!r}")
        match = _VERSION_CLAUSE.fullmatch(piece)
        if match is None or match.group(1) not in _SUPPORTED_VERSION_OPERATORS:
            raise GateDerivationError(
                f"{source}: unsupported python requirement syntax {piece!r}"
            )
        operator, version_text = match.groups()
        version = _normalize_version(
            tuple(int(part) for part in version_text.split("."))
        )
        clauses.append(_VersionClause(operator, version))
    if not clauses:
        raise GateDerivationError(f"{source}: python requirement is empty")
    return tuple(sorted(clauses, key=lambda clause: (clause.operator, clause.version)))


def _canonical_version_domain(
    clauses: tuple[_VersionClause, ...],
) -> _VersionDomain:
    """Reduce comparison clauses to an interval, exclusions, or one point.

    Agreement is semantic, not textual: duplicate and redundant clauses do
    not make two checked-in requirement surfaces disagree.
    """

    lower: tuple[int, int, int] | None = None
    lower_inclusive = True
    upper: tuple[int, int, int] | None = None
    upper_inclusive = True
    exact_values: set[tuple[int, int, int]] = set()
    excluded: set[tuple[int, int, int]] = set()
    for clause in clauses:
        version = clause.version
        if clause.operator == "==":
            exact_values.add(version)
        elif clause.operator == "!=":
            excluded.add(version)
        elif clause.operator in {">", ">="}:
            inclusive = clause.operator == ">="
            if lower is None or version > lower:
                lower, lower_inclusive = version, inclusive
            elif version == lower:
                lower_inclusive = lower_inclusive and inclusive
        else:
            inclusive = clause.operator == "<="
            if upper is None or version < upper:
                upper, upper_inclusive = version, inclusive
            elif version == upper:
                upper_inclusive = upper_inclusive and inclusive

    if len(exact_values) > 1:
        return _VersionDomain(None, True, None, True, frozenset(), empty=True)
    if exact_values:
        exact = next(iter(exact_values))
        other_clauses = tuple(clause for clause in clauses if clause.operator != "==")
        if exact in excluded or not all(
            _clause_satisfied(clause, exact) for clause in other_clauses
        ):
            return _VersionDomain(None, True, None, True, frozenset(), empty=True)
        return _VersionDomain(exact, True, exact, True, frozenset(), exact=exact)

    # Interpreter versions are concrete ``sys.version_info[:3]`` triples.
    # Normalize exclusive bounds when the adjacent triple is representable so
    # spellings such as ``>3.12`` and ``>=3.12.1`` have one meaning here.
    if lower is not None and not lower_inclusive:
        lower = (lower[0], lower[1], lower[2] + 1)
        lower_inclusive = True
    if upper is not None and not upper_inclusive and upper[2] > 0:
        upper = (upper[0], upper[1], upper[2] - 1)
        upper_inclusive = True

    if lower is not None and upper is not None:
        if lower > upper or (
            lower == upper and not (lower_inclusive and upper_inclusive)
        ):
            return _VersionDomain(None, True, None, True, frozenset(), empty=True)
        if lower == upper and lower in excluded:
            return _VersionDomain(None, True, None, True, frozenset(), empty=True)
        if lower[:2] == upper[:2] and lower_inclusive and upper_inclusive:
            candidates = upper[2] - lower[2] + 1
            exclusions = sum(lower <= version <= upper for version in excluded)
            if exclusions == candidates:
                return _VersionDomain(None, True, None, True, frozenset(), empty=True)

    def inside(version: tuple[int, int, int]) -> bool:
        if lower is not None and (
            version < lower or (version == lower and not lower_inclusive)
        ):
            return False
        if upper is not None and (
            version > upper or (version == upper and not upper_inclusive)
        ):
            return False
        return True

    return _VersionDomain(
        lower,
        lower_inclusive,
        upper,
        upper_inclusive,
        frozenset(version for version in excluded if inside(version)),
    )


def _pep621_requires_python(pyproject: Mapping[str, object]) -> str | None:
    project = pyproject.get("project")
    if not isinstance(project, Mapping):
        return None
    value = project.get("requires-python")
    if value is None:
        return None
    if not isinstance(value, str):
        raise GateDerivationError("project.requires-python must be a string")
    return value


def _poetry_python_constraint(pyproject: Mapping[str, object]) -> str | None:
    tool = pyproject.get("tool")
    if not isinstance(tool, Mapping):
        return None
    poetry = tool.get("poetry")
    if not isinstance(poetry, Mapping):
        return None
    dependencies = poetry.get("dependencies")
    if not isinstance(dependencies, Mapping):
        return None
    value = dependencies.get("python")
    if value is None:
        return None
    if not isinstance(value, str):
        raise GateDerivationError("tool.poetry.dependencies.python must be a string")
    return value


def _declared_python_requirement(
    pyproject: Mapping[str, object], *, product: str
) -> tuple[_VersionClause, ...]:
    """Accept exactly one declaration surface, or two that agree.

    A single surface (PEP 621 alone, or Poetry alone) is accepted outright.
    Both present must describe the same canonical version domain; disagreement
    refuses.  Neither present refuses -- a missing requirement is not a
    default.
    """

    pep621 = _pep621_requires_python(pyproject)
    poetry = _poetry_python_constraint(pyproject)
    if pep621 is None and poetry is None:
        raise GateDerivationError(
            f"{product}: no python requirement is declared (neither "
            "project.requires-python nor tool.poetry.dependencies.python)"
        )
    pep621_clauses = (
        _parse_python_specifier(pep621, source=f"{product}: project.requires-python")
        if pep621 is not None
        else None
    )
    poetry_clauses = (
        _parse_python_specifier(
            poetry, source=f"{product}: tool.poetry.dependencies.python"
        )
        if poetry is not None
        else None
    )
    if (
        pep621_clauses is not None
        and poetry_clauses is not None
        and _canonical_version_domain(pep621_clauses)
        != _canonical_version_domain(poetry_clauses)
    ):
        raise GateDerivationError(
            f"{product}: project.requires-python ({pep621!r}) disagrees with "
            f"tool.poetry.dependencies.python ({poetry!r})"
        )
    selected = pep621_clauses if pep621_clauses is not None else poetry_clauses
    assert selected is not None
    if _canonical_version_domain(selected).empty:
        raise GateDerivationError(
            f"{product}: declared python requirement has no satisfying version"
        )
    return selected


def _clause_satisfied(clause: _VersionClause, candidate: tuple[int, int, int]) -> bool:
    if clause.operator == ">=":
        return candidate >= clause.version
    if clause.operator == "<=":
        return candidate <= clause.version
    if clause.operator == "==":
        return candidate == clause.version
    if clause.operator == "!=":
        return candidate != clause.version
    if clause.operator == ">":
        return candidate > clause.version
    return candidate < clause.version  # "<"


def _minor_can_satisfy(
    clauses: tuple[_VersionClause, ...], minor: tuple[int, int]
) -> bool:
    """Whether some patch release in ``minor`` can satisfy the requirement."""

    domain = _canonical_version_domain(clauses)
    if domain.empty:
        return False
    start = (minor[0], minor[1], 0)
    next_minor = (minor[0], minor[1] + 1, 0)
    if domain.exact is not None:
        return start <= domain.exact < next_minor
    lower = domain.lower
    upper = domain.upper
    if upper is not None and (
        upper < start or (upper == start and not domain.upper_inclusive)
    ):
        return False
    if lower is not None and lower >= next_minor:
        return False
    first_patch = 0
    if lower is not None and lower[:2] == minor:
        first_patch = lower[2] + (not domain.lower_inclusive)
    last_patch: int | None = None
    if upper is not None and upper[:2] == minor:
        last_patch = upper[2] - (not domain.upper_inclusive)
        if last_patch < first_patch:
            return False
    if last_patch is not None:
        available = last_patch - first_patch + 1
        excluded = sum(
            version[:2] == minor and first_patch <= version[2] <= last_patch
            for version in domain.excluded
        )
        if excluded == available:
            return False
    # A finite exclusion set cannot consume an unbounded patch line.
    return True


def _probe_interpreter_version(executable: str, *, name: str) -> tuple[int, int, int]:
    """Read the executable's real version; its filename is not evidence."""

    try:
        result = subprocess.run(  # noqa: S603
            [
                executable,
                "-I",
                "-c",
                "import json,sys; print(json.dumps(list(sys.version_info[:3])))",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
            cwd=tempfile.gettempdir(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GateAcquisitionError(
            f"could not interrogate selected interpreter {name!r}: {exc}"
        ) from exc
    if result.returncode != 0:
        raise GateAcquisitionError(
            f"selected interpreter {name!r} could not report its version: "
            f"{result.stderr.strip()}"
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GateAcquisitionError(
            f"selected interpreter {name!r} reported a non-JSON version"
        ) from exc
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(not isinstance(part, int) or isinstance(part, bool) for part in value)
    ):
        raise GateAcquisitionError(
            f"selected interpreter {name!r} reported an invalid version"
        )
    return (value[0], value[1], value[2])


def _select_interpreter(
    clauses: tuple[_VersionClause, ...], *, product: str
) -> _SelectedInterpreter:
    """Select and verify the LOWEST trusted interpreter floor.

    Lowest, not merely compatible, is load-bearing: it is what detects a
    product using syntax newer than the floor it declares.
    """

    selected = next(
        (
            (name, minor)
            for name, minor in TRUSTED_INTERPRETERS
            if _minor_can_satisfy(clauses, minor)
        ),
        None,
    )
    if selected is None:
        raise GateDerivationError(
            f"{product}: no trusted interpreter "
            f"({', '.join(name for name, _ in TRUSTED_INTERPRETERS)}) satisfies "
            "the declared python requirement"
        )
    name, minor = selected
    executable = _require_interpreter_available(name)
    actual = _probe_interpreter_version(executable, name=name)
    if actual[:2] != minor:
        raise GateAcquisitionError(
            f"selected interpreter {name!r} resolved to Python "
            f"{actual[0]}.{actual[1]}.{actual[2]}, expected {minor[0]}.{minor[1]}.x"
        )
    if not all(_clause_satisfied(clause, actual) for clause in clauses):
        raise GateAcquisitionError(
            f"selected interpreter {name!r} is Python "
            f"{actual[0]}.{actual[1]}.{actual[2]}, which does not satisfy "
            f"{product}'s declared python requirement"
        )
    return _SelectedInterpreter(name, executable, actual)


def _require_interpreter_available(name: str) -> str:
    """Resolve a selected interpreter's executable, or refuse acquisition.

    An unavailable selected interpreter is an acquisition/infrastructure
    failure, distinct from a product evidence refusal: it says nothing about
    whether the product's source is valid, only that this host cannot check.
    """

    executable = shutil.which(name)
    if executable is None:
        raise GateAcquisitionError(
            f"selected interpreter {name!r} is not available on this host"
        )
    return executable


_FOREIGN_IMPORT_PROTOCOL: Final = "foreign-import-analysis.v1"
_FOREIGN_IMPORT_LIMIT: Final = 10_000
_FOREIGN_RESPONSE_BYTE_LIMIT: Final = 1_000_000
_FOREIGN_STDERR_BYTE_LIMIT: Final = 65_536
_FOREIGN_REQUEST_TIMEOUT_SECONDS: Final = 10.0
_FOREIGN_CLEANUP_TIMEOUT_SECONDS: Final = 1.0
_FOREIGN_REQUEST_KEYS: Final = frozenset(
    {
        "schema_version",
        "product",
        "revision",
        "interpreter",
        "module",
        "path",
        "is_package",
        "available_modules",
        "source_base64",
    }
)
_FOREIGN_RESPONSE_KEYS: Final = frozenset(
    {
        "schema_version",
        "status",
        "product",
        "revision",
        "interpreter",
        "module",
        "path",
        "imports",
        "error",
    }
)

# The selected interpreter loads these already hash-verified gate bytes, parses
# the foreign source with its own grammar, performs every AST-dependent import
# derivation there, and returns JSON facts.  It never returns an AST: Python's
# AST classes are version-specific (3.12 adds TypeAlias/TypeVar), so bridging a
# tree back into a 3.11 host would impose the host's AST vocabulary as an
# unauthorized ceiling on a product that correctly declares a 3.12 floor.
_CHILD_ANALYZE_SOURCE: Final = r"""
import base64
import importlib.util
import json
import sys
from pathlib import Path

def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result

try:
    gate_path = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(gate_path.parents[2]))
    spec = importlib.util.spec_from_file_location("_trusted_gate_child", gate_path)
    if spec is None or spec.loader is None:
        raise ValueError("trusted gate module could not be loaded")
    gate = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = gate
    spec.loader.exec_module(gate)
    for raw_request in sys.stdin.buffer:
        request = json.loads(
            raw_request.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
        expected = {
            "schema_version", "product", "revision", "interpreter", "module",
            "path", "is_package", "available_modules", "source_base64",
        }
        if not isinstance(request, dict) or set(request) != expected:
            raise ValueError("request does not have the closed protocol shape")
        source = base64.b64decode(request["source_base64"], validate=True)
        available = request["available_modules"]
        if (
            request["schema_version"] != "foreign-import-analysis.v1"
            or not isinstance(request["is_package"], bool)
            or not isinstance(available, list)
            or available != sorted(set(available))
            or any(not isinstance(item, str) for item in available)
        ):
            raise ValueError("request values do not satisfy the closed protocol")
        try:
            imports = gate._import_edges_from_source(
                source,
                current_module=request["module"],
                is_package=request["is_package"],
                available_modules=frozenset(available),
            )
            status = "ok"
            error = None
        except (UnicodeDecodeError, SyntaxError) as exc:
            imports = frozenset()
            status = "syntax_error"
            error = str(exc)
        except gate.GateDerivationError as exc:
            imports = frozenset()
            status = "derivation_error"
            error = str(exc)
        if len(imports) > 10000:
            raise ValueError("derived import set exceeds the protocol limit")
        response = {
            "schema_version": request["schema_version"],
            "status": status,
            "product": request["product"],
            "revision": request["revision"],
            "interpreter": request["interpreter"],
            "module": request["module"],
            "path": request["path"],
            "imports": sorted(imports),
            "error": error,
        }
        encoded = json.dumps(response, sort_keys=True, separators=(",", ":")).encode()
        if len(encoded) > 1000000:
            raise ValueError("response exceeds the protocol byte limit")
        sys.stdout.buffer.write(encoded + b"\n")
        sys.stdout.buffer.flush()
except Exception as exc:
    sys.stderr.write(f"foreign import protocol refused: {exc}")
    sys.exit(4)
"""


def _json_object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> object:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _stop_foreign_process(process: subprocess.Popen[bytes]) -> None:
    """Close protocol streams and escalate wait -> terminate -> kill."""

    if process.stdin is not None and not process.stdin.closed:
        try:
            process.stdin.close()
        except OSError:
            pass
    if process.poll() is None:
        try:
            process.wait(timeout=_FOREIGN_CLEANUP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=_FOREIGN_CLEANUP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    process.wait(timeout=_FOREIGN_CLEANUP_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    # A platform process API that cannot reap after SIGKILL is
                    # already outside what this gate can repair.  Preserve the
                    # original acquisition/derivation error, if any.
                    pass
    for stream in (process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except OSError:
                pass


def _read_foreign_response(
    process: subprocess.Popen[bytes],
    *,
    executable: str,
    timeout: float = _FOREIGN_REQUEST_TIMEOUT_SECONDS,
) -> tuple[bytes, int | None, bytes]:
    """Read one bounded line while concurrently draining child stderr."""

    if process.stdout is None or process.stderr is None:
        raise GateAcquisitionError(
            f"{executable}: foreign-source process has no response streams"
        )
    stdout = bytearray()
    stderr = bytearray()
    deadline = time.monotonic() + timeout
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                diagnostic = bytes(stderr).decode("utf-8", errors="replace").strip()
                suffix = f": {diagnostic}" if diagnostic else ""
                raise GateAcquisitionError(
                    f"{executable}: foreign-source analysis timed out after "
                    f"{timeout:g} seconds{suffix}"
                )
            events = selector.select(remaining)
            if not events:
                continue
            for key, _events in events:
                try:
                    chunk = os.read(key.fileobj.fileno(), 65_536)
                except OSError as exc:
                    raise GateAcquisitionError(
                        f"{executable}: foreign-source response stream failed: {exc}"
                    ) from exc
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stderr":
                    stderr.extend(chunk)
                    if len(stderr) > _FOREIGN_STDERR_BYTE_LIMIT:
                        raise GateAcquisitionError(
                            f"{executable}: foreign-source stderr exceeds "
                            f"{_FOREIGN_STDERR_BYTE_LIMIT} bytes"
                        )
                    continue
                stdout.extend(chunk)
                if len(stdout) > _FOREIGN_RESPONSE_BYTE_LIMIT + 1:
                    raise GateAcquisitionError(
                        f"{executable}: foreign-source response exceeds "
                        f"{_FOREIGN_RESPONSE_BYTE_LIMIT} bytes"
                    )
                if b"\n" in stdout:
                    response, remainder = bytes(stdout).split(b"\n", 1)
                    if remainder:
                        raise GateAcquisitionError(
                            f"{executable}: foreign-source process emitted "
                            "more than one response"
                        )
                    return response, process.poll(), bytes(stderr)
            if not selector.get_map():
                return bytes(stdout), process.poll(), bytes(stderr)
    finally:
        selector.close()


@contextmanager
def _foreign_import_process(
    executable: str,
) -> Iterator[subprocess.Popen[bytes]]:
    """Keep one selected interpreter alive for one product import graph."""

    try:
        process = subprocess.Popen(  # noqa: S603
            [
                executable,
                "-I",
                "-c",
                _CHILD_ANALYZE_SOURCE,
                str(Path(__file__).resolve()),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=tempfile.gettempdir(),
        )
    except OSError as exc:
        raise GateAcquisitionError(
            f"could not launch {executable!r} to analyze foreign source: {exc}"
        ) from exc
    try:
        yield process
    finally:
        _stop_foreign_process(process)


def _imports_under_interpreter(
    source: bytes,
    *,
    executable: str,
    product: str,
    revision: str,
    interpreter: str,
    module: str,
    path: str,
    is_package: bool,
    available_modules: frozenset[str],
    process: subprocess.Popen[bytes] | None = None,
) -> frozenset[str]:
    """Return import edges derived wholly under the selected interpreter.

    The transport is a closed JSON fact record, never a pickled AST.  The
    parent validates identity echoes, exact keys, ordering, uniqueness and
    bounds before accepting an edge.  Foreign source is parsed and walked but
    never imported or executed.
    """

    request = {
        "schema_version": _FOREIGN_IMPORT_PROTOCOL,
        "product": product,
        "revision": revision,
        "interpreter": interpreter,
        "module": module,
        "path": path,
        "is_package": is_package,
        "available_modules": sorted(available_modules),
        "source_base64": base64.b64encode(source).decode("ascii"),
    }
    encoded_request = json.dumps(
        request, sort_keys=True, separators=(",", ":")
    ).encode()
    if process is None:
        try:
            result = subprocess.run(  # noqa: S603
                [
                    executable,
                    "-I",
                    "-c",
                    _CHILD_ANALYZE_SOURCE,
                    str(Path(__file__).resolve()),
                ],
                input=encoded_request,
                capture_output=True,
                check=False,
                cwd=tempfile.gettempdir(),
                timeout=_FOREIGN_REQUEST_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise GateAcquisitionError(
                f"{executable}: foreign-source analysis timed out after "
                f"{_FOREIGN_REQUEST_TIMEOUT_SECONDS:g} seconds"
            ) from exc
        except OSError as exc:
            raise GateAcquisitionError(
                f"could not launch {executable!r} to analyze foreign source: {exc}"
            ) from exc
        response_bytes = result.stdout
        returncode = result.returncode
        error_bytes = result.stderr
    else:
        if process.stdin is None or process.stdout is None:
            raise GateAcquisitionError(
                f"{executable}: foreign-source process has no protocol streams"
            )
        try:
            process.stdin.write(encoded_request + b"\n")
            process.stdin.flush()
            response_bytes, returncode, error_bytes = _read_foreign_response(
                process, executable=executable
            )
        except (BrokenPipeError, OSError) as exc:
            raise GateAcquisitionError(
                f"{executable}: foreign-source protocol stream failed: {exc}"
            ) from exc
    if returncode not in {None, 0} or not response_bytes:
        raise GateAcquisitionError(
            f"{executable}: foreign-source analysis subprocess exited "
            f"{returncode}: {error_bytes.decode('utf-8', errors='replace').strip()}"
        )
    if len(response_bytes) > _FOREIGN_RESPONSE_BYTE_LIMIT:
        raise GateAcquisitionError(
            f"{executable}: foreign-source response exceeds "
            f"{_FOREIGN_RESPONSE_BYTE_LIMIT} bytes"
        )
    try:
        response = json.loads(
            response_bytes.decode("utf-8"),
            object_pairs_hook=_json_object_without_duplicate_keys,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise GateAcquisitionError(
            f"{executable}: foreign-source response is not strict JSON: {exc}"
        ) from exc
    if not isinstance(response, dict) or set(response) != _FOREIGN_RESPONSE_KEYS:
        raise GateAcquisitionError(
            f"{executable}: foreign-source response does not have the closed shape"
        )
    for key in (
        "schema_version",
        "product",
        "revision",
        "interpreter",
        "module",
        "path",
    ):
        if response[key] != request[key]:
            raise GateAcquisitionError(
                f"{executable}: foreign-source response changed identity field {key}"
            )
    status = response["status"]
    error = response["error"]
    imports = response["imports"]
    if (
        not isinstance(imports, list)
        or imports != sorted(set(imports))
        or len(imports) > _FOREIGN_IMPORT_LIMIT
        or any(not isinstance(item, str) or not item for item in imports)
    ):
        raise GateAcquisitionError(
            f"{executable}: foreign-source response carries invalid import facts"
        )
    if status == "ok":
        if error is not None:
            raise GateAcquisitionError(
                f"{executable}: successful foreign-source response carries an error"
            )
        return frozenset(imports)
    if not isinstance(error, str) or not error:
        raise GateAcquisitionError(
            f"{executable}: refused foreign-source response has no diagnostic"
        )
    if status == "syntax_error":
        raise _ForeignSyntaxError(error)
    if status == "derivation_error":
        raise GateDerivationError(error)
    raise GateAcquisitionError(
        f"{executable}: foreign-source response has unknown status {status!r}"
    )


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
    """Refuse working-tree helper bytes that differ from the bound contract.

    The pinned compatibility runner verifies these files before importing this
    module.  This second check catches drift between that verification and the
    evaluation; it is deliberately not described as the pre-import authority.
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
                f"{relative}: current bytes differ from trusted contract "
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


def _mapping_lookup_key_strings(
    tree: ast.AST, lookup_name: str
) -> frozenset[str] | None:
    """Resolve the literal keys of a mapping consumed through ``get(name)``."""

    mappings: dict[str, ast.Dict] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Dict)
        ):
            mappings[node.targets[0].id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Dict)
        ):
            mappings[node.target.id] = node.value
    values: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == lookup_name
        ):
            continue
        mapping = mappings.get(node.func.value.id)
        if mapping is None:
            continue
        if not all(
            isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value
            for key in mapping.keys
        ):
            return None
        values.update(
            key.value
            for key in mapping.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        )
    return frozenset(values) if values else None


def _name_is_unshadowed(tree: ast.AST, name: str) -> bool:
    """Prove a builtin name has no binding anywhere in the measured module."""

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id == name
            and isinstance(node.ctx, ast.Store | ast.Del)
        ):
            return False
        if isinstance(node, ast.arg) and node.arg == name:
            return False
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if node.name == name:
                return False
        if isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if bound == name:
                    return False
    return True


def _dynamic_import_aliases(tree: ast.AST) -> Mapping[str, str]:
    """Map proven aliases of the three supported import APIs to their owner."""

    owners = {
        "builtins": frozenset({"__import__"}),
        "importlib": frozenset({"import_module"}),
        "importlib.util": frozenset({"find_spec"}),
    }
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module not in owners:
            continue
        for alias in node.names:
            if alias.name in owners[node.module]:
                aliases[alias.asname or alias.name] = alias.name
    return MappingProxyType(aliases)


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
    importer_aliases = _dynamic_import_aliases(tree)

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
                name = importer_aliases.get(node.func.id, node.func.id)
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
                fromlist_values: set[str] = set()
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
                    fromlist_expression = _call_argument(
                        node,
                        position=3,
                        keyword="fromlist",
                        current_module=current_module,
                    )
                    if fromlist_expression is not None:
                        if not isinstance(fromlist_expression, ast.Tuple | ast.List):
                            raise GateDerivationError(
                                f"{current_module}: __import__ fromlist is not a "
                                "bounded literal name sequence"
                            )
                        for item in fromlist_expression.elts:
                            item_values = _possible_import_strings(
                                item,
                                current_module=current_module,
                                assignments=assignments,
                                dictionary_values=dictionary_values,
                            )
                            if item_values is None and isinstance(item, ast.Name):
                                item_values = _mapping_lookup_key_strings(tree, item.id)
                            if not item_values or any(
                                not value or value == "*" for value in item_values
                            ):
                                raise GateDerivationError(
                                    f"{current_module}: __import__ fromlist is not "
                                    "a bounded literal name sequence"
                                )
                            fromlist_values.update(item_values)
                    if level:
                        globals_expression = _call_argument(
                            node,
                            position=1,
                            keyword="globals",
                            current_module=current_module,
                        )
                        if not (
                            isinstance(globals_expression, ast.Call)
                            and isinstance(globals_expression.func, ast.Name)
                            and globals_expression.func.id == "globals"
                            and not globals_expression.args
                            and not globals_expression.keywords
                            and _name_is_unshadowed(tree, "globals")
                        ):
                            raise GateDerivationError(
                                f"{current_module}: relative __import__ globals do "
                                "not prove the current module package"
                            )
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
                    for imported_name in fromlist_values:
                        candidate = f"{target}.{imported_name}"
                        if (
                            target not in index
                            and target not in in_memory_sources
                            or candidate in index
                            or candidate in in_memory_sources
                        ):
                            enqueue(candidate)
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


def _import_edges_from_source(
    source_bytes: bytes,
    *,
    current_module: str,
    is_package: bool,
    available_modules: frozenset[str],
) -> frozenset[str]:
    """Parse one module and derive its import edges without executing it.

    This is the complete AST-dependent unit run by the selected product
    interpreter.  Its output is plain module-name facts, so no version-specific
    AST object crosses the subprocess boundary.
    """

    source = source_bytes.decode("utf-8")
    tree = ast.parse(source)
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
                    and parameters[0].arg in {"self", "cls"}
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
    frozen_assignments = {name: tuple(values) for name, values in assignments.items()}
    edges: set[str] = set()
    available_index = {name: None for name in available_modules}
    _visit_imports(
        tree=tree,
        current_module=current_module,
        is_package=is_package,
        assignments=frozen_assignments,
        dictionary_values=dictionary_values,
        index=available_index,  # type: ignore[arg-type]
        in_memory_sources=MappingProxyType({}),
        enqueue=edges.add,
    )
    return frozenset(edges)


def _walk_import_graph(
    *,
    repository: Path,
    index: Mapping[str, _GitPythonFile],
    roots: Sequence[str],
    in_memory_sources: Mapping[str, bytes] = MappingProxyType({}),
    discover_imports: (
        Callable[[bytes, str, bool, str, frozenset[str]], frozenset[str]] | None
    ) = None,
) -> _ImportReachability:
    """Walk the import graph, parsing each reachable module exactly once.

    ``discover_imports`` is an injection point for interpreter-floor-correct
    parsing and AST walking (see ``_product_runtime_reachability``). Its
    default preserves this function's host interpreter for trusted,
    Starter-owned sources such as the deferred-import-debt catalogue.
    """

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
        is_package = source_item is not None and source_item.path.endswith(
            "/__init__.py"
        )
        path_label = source_item.path if source_item is not None else module
        available_modules = frozenset((*index, *in_memory_sources))
        try:
            imports = (
                discover_imports(
                    source_bytes,
                    module,
                    is_package,
                    path_label,
                    available_modules,
                )
                if discover_imports is not None
                else _import_edges_from_source(
                    source_bytes,
                    current_module=module,
                    is_package=is_package,
                    available_modules=available_modules,
                )
            )
        except (UnicodeDecodeError, SyntaxError) as exc:
            raise GateDerivationError(
                f"{module}: reachable source does not parse"
            ) from exc
        visited.add(module)
        for imported_module in imports:
            enqueue(imported_module)

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
    pyproject: Mapping[str, object],
) -> _ImportReachability:
    spec = _DERIVATION_SPECS[product]
    clauses = _declared_python_requirement(pyproject, product=product)
    selected = _select_interpreter(clauses, product=product)
    interpreter_name = selected.name
    executable = selected.executable
    floor = ".".join(str(part) for part in selected.version)
    index = dict(_git_python_index(clone, revision, "app"))
    extra_sources = {
        module: observations[observation_id]
        for module, observation_id in spec.extra_source_modules.items()
    }

    def discover_imports(
        source: bytes,
        module: str,
        is_package: bool,
        path: str,
        available_modules: frozenset[str],
    ) -> frozenset[str]:
        try:
            return _imports_under_interpreter(
                source,
                executable=executable,
                product=product,
                revision=revision,
                interpreter=interpreter_name,
                module=module,
                path=path,
                is_package=is_package,
                available_modules=available_modules,
                process=process,
            )
        except _ForeignSyntaxError as exc:
            raise GateDerivationError(
                f"{product}: {path}: source does not parse under interpreter "
                f"{interpreter_name} (selected for declared floor {floor}): {exc}"
            ) from exc

    with _foreign_import_process(executable) as process:
        return _walk_import_graph(
            repository=clone,
            index=MappingProxyType(index),
            roots=spec.runtime_roots,
            in_memory_sources=MappingProxyType(extra_sources),
            discover_imports=discover_imports,
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
        pyproject=pyproject,
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
