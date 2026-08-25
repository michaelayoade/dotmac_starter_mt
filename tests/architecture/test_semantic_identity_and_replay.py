"""Guards for ADR-0064 and ADR-0014's 2026-08-25 replay amendment.

The semantic-identity detector deliberately covers DIRECT dataflow from a
generic ``version``/``updated_at`` attribute into a semantic identity sink. It
does not claim whole-program taint analysis. The one apparent hit is a typed
``SourceIdentity`` that carries an independent fingerprint; its premise is
checked below rather than hidden in an unexplained path allowlist.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import textwrap
from dataclasses import dataclass
from pathlib import Path

import dotmac_subscriptions.treatments as treatments
from dotmac_accounting import service as accounting_service
from dotmac_accounting.contracts import SourceIdentity
from dotmac_kernel import idempotency

REPO_ROOT = Path(__file__).resolve().parents[2]

_COUNTER_ATTRIBUTES = {"updated_at", "version"}
_SEMANTIC_SINKS = {
    "content_version",
    "dedup_key",
    "etag",
    "event_version",
    "fingerprint",
    "idempotency_key",
    "request_fingerprint",
    "source_fingerprint",
    "source_version",
}


@dataclass(frozen=True, slots=True)
class _CounterSink:
    path: str
    sink: str
    counter: str


def _counter_attributes(node: ast.AST) -> tuple[ast.Attribute, ...]:
    return tuple(
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Attribute) and child.attr in _COUNTER_ATTRIBUTES
    )


def _target_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _semantic_counter_sinks(source: str, *, path: str) -> set[_CounterSink]:
    """Return direct counter-to-semantic-identity flows in one Python file."""
    violations: set[_CounterSink] = set()
    for node in ast.walk(ast.parse(source)):
        values: list[tuple[str, ast.AST]] = []
        if isinstance(node, ast.keyword) and node.arg in _SEMANTIC_SINKS:
            values.append((node.arg, node.value))
        elif isinstance(node, ast.Assign):
            values.extend(
                (name, node.value)
                for target in node.targets
                if (name := _target_name(target)) in _SEMANTIC_SINKS
            )
        elif isinstance(node, ast.AnnAssign):
            name = _target_name(node.target)
            if name in _SEMANTIC_SINKS and node.value is not None:
                values.append((name, node.value))
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and key.value in _SEMANTIC_SINKS
                ):
                    values.append((key.value, value))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "fingerprint_of"
        ):
            values.extend(("fingerprint_of", value) for value in node.args)

        for sink, candidate in values:
            for counter in _counter_attributes(candidate):
                violations.add(
                    _CounterSink(
                        path=path,
                        sink=sink,
                        counter=ast.unparse(counter),
                    )
                )
    return violations


def _production_python_files() -> tuple[Path, ...]:
    files: list[Path] = []
    for root in (REPO_ROOT / "app", REPO_ROOT / "packages"):
        files.extend(
            path
            for path in root.rglob("*.py")
            if "migrations" not in path.parts and "tests" not in path.parts
        )
    return tuple(sorted(files))


def _call_line(function: ast.FunctionDef, name: str) -> int:
    lines = [
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
    ]
    assert lines, f"{function.name} does not call {name}"
    return min(lines)


def _function(source: str, name: str) -> ast.FunctionDef:
    tree = ast.parse(textwrap.dedent(source))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _kernel_replay_order_violations(
    source: str, *, function_name: str, lookup_name: str
) -> set[str]:
    function = _function(source, function_name)
    validate = _call_line(function, "_validate")
    lookup = _call_line(function, lookup_name)
    replay = _call_line(function, "_replay_or_conflict")
    operation = _call_line(function, "operation")
    violations: set[str] = set()
    if validate >= lookup:
        violations.add("trust_validation_after_lookup")
    if lookup >= replay:
        violations.add("replay_not_resolved_from_lookup")
    if replay >= operation:
        violations.add("mutable_operation_before_replay")
    return violations


def test_row_mutation_counters_do_not_flow_into_semantic_identity() -> None:
    violations: set[_CounterSink] = set()
    for path in _production_python_files():
        violations.update(
            _semantic_counter_sinks(
                path.read_text(encoding="utf-8"),
                path=str(path.relative_to(REPO_ROOT)),
            )
        )

    # This is a typed source-supplied revision, not an ORM mutation counter.
    # The premise is proved by the next test and remains visible here so a new
    # direct use cannot hide behind a directory-wide exemption.
    assert violations == {
        _CounterSink(
            path="packages/dotmac-accounting/src/dotmac_accounting/service.py",
            sink="source_version",
            counter="source.version",
        )
    }


def test_accounting_source_identity_keeps_version_and_fingerprint_separate() -> None:
    parameters = inspect.signature(SourceIdentity).parameters
    dataclass_parameters = SourceIdentity.__dataclass_params__  # type: ignore[attr-defined]
    source = inspect.getsource(SourceIdentity)
    create_journal = inspect.getsource(accounting_service.create_journal)

    assert dataclasses.is_dataclass(SourceIdentity)
    assert dataclass_parameters.frozen is True
    assert {"version", "fingerprint"} <= set(parameters)
    assert "class SourceIdentity:" in source
    assert "source_version=source.version" in create_journal
    assert "source_fingerprint=source.fingerprint" in create_journal


def test_semantic_identity_guard_is_sensitive_to_direct_counter_flows() -> None:
    planted = """
source_version = invoice.version
fingerprint = fingerprint_of({"changed": invoice.updated_at})
send(etag=row.updated_at)
"""

    assert _semantic_counter_sinks(planted, path="planted.py") == {
        _CounterSink("planted.py", "source_version", "invoice.version"),
        _CounterSink("planted.py", "fingerprint", "invoice.updated_at"),
        _CounterSink("planted.py", "fingerprint_of", "invoice.updated_at"),
        _CounterSink("planted.py", "etag", "row.updated_at"),
    }
    assert not _semantic_counter_sinks(
        "source_version = submission.content_version\n",
        path="typed.py",
    )


def test_kernel_resolves_exact_replay_before_running_the_operation() -> None:
    source = inspect.getsource(idempotency)
    assert not _kernel_replay_order_violations(
        source,
        function_name="execute_once",
        lookup_name="_lookup",
    )
    assert not _kernel_replay_order_violations(
        source,
        function_name="execute_once_platform",
        lookup_name="_lookup_platform",
    )


def test_subscription_approval_resolves_replay_before_mutable_preview() -> None:
    function = _function(
        inspect.getsource(treatments.approve_billing_arrangement),
        "approve_billing_arrangement",
    )
    preview_line = _call_line(function, "preview_billing_arrangement")
    replay_branch = next(
        node
        for node in function.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "replay"
    )

    assert replay_branch.end_lineno is not None
    assert replay_branch.end_lineno < preview_line
    assert any(
        isinstance(node, ast.Attribute) and node.attr == "command_fingerprint"
        for node in ast.walk(replay_branch)
    )
    assert any(
        isinstance(node, ast.Return)
        and any(
            keyword.arg == "replayed"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            for keyword in call.keywords
        )
        for node in ast.walk(replay_branch)
    )


def test_replay_order_guard_is_sensitive_to_operation_before_lookup() -> None:
    planted = """
def execute_once(db, scope, key, operation):
    _validate(scope, key)
    result = operation(db)
    existing = _lookup(db)
    if existing:
        return _replay_or_conflict(existing)
    return result
"""

    assert _kernel_replay_order_violations(
        planted,
        function_name="execute_once",
        lookup_name="_lookup",
    ) == {"mutable_operation_before_replay"}
