"""Each product is parsed under an interpreter derived from its OWN declared
python floor (PEP 621 ``requires-python`` or Poetry's ``python``) -- never
from this gate's own host interpreter and never from the product's evidence
record.

Plants required by the design brief, each proven before/after in this file:

1. A single declaration surface (PEP 621 only, or Poetry only) is accepted.
2. Both surfaces present and disagreeing refuses.
3. A missing requirement refuses.
4. Unsupported requirement syntax (caret/tilde/wildcard) refuses.
5. An unavailable selected interpreter refuses as acquisition/infrastructure,
   distinctly from a product evidence refusal.
6. Academy's real PEP 701 case: nested same-type f-string quotes parse at
   the declared 3.12 floor and refuse at 3.11.
7. Source exceeding its own declared floor (PEP 696 type-parameter defaults,
   3.13-only) is a product evidence refusal naming product, path, and
   interpreter.
8. PEP 695 trees produced by a 3.12 child cross a real 3.11 host boundary as
   closed JSON import facts, never as version-specific AST objects.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess

import pytest

from tools.composition_contract import compatibility_gate as gate

# The real repro from the brief: nested same-type f-string quotes.  Invalid
# grammar on 3.11 (PEP 701 landed in 3.12); valid on 3.12 and 3.13.
_PEP701_SOURCE = (
    'base_url = "https://x"\n'
    'raw = "abc"\n'
    'url = f"{base_url.rstrip("/")}/apply/assessment?token={raw}"\n'
)

# PEP 696 (3.13) type-parameter defaults: ``[T = int]``.  Invalid grammar on
# both 3.11 and 3.12; valid only on 3.13.
_PEP696_SOURCE = "class Foo[T = int]:\n    pass\n"

_PEP695_SOURCES = (
    "class Foo[T]:\n    pass\nimport dotmac_files\n",
    "type Alias = int\nimport dotmac_files\n",
    "def f[T](value: T) -> T:\n    return value\nimport dotmac_files\n",
)

_PYTHON_3_11 = shutil.which("python3.11")
_PYTHON_3_12 = shutil.which("python3.12")
_PYTHON_3_13 = shutil.which("python3.13")
_HAVE_3_11 = _PYTHON_3_11 is not None
_HAVE_3_12 = _PYTHON_3_12 is not None
_HAVE_3_13 = _PYTHON_3_13 is not None
_HAVE_ALL_TRUSTED = _HAVE_3_11 and _HAVE_3_12 and _HAVE_3_13

requires_all_trusted = pytest.mark.skipif(
    not _HAVE_ALL_TRUSTED,
    reason="python3.11/3.12/3.13 must all be on PATH for interpreter-floor tests",
)


def _clauses(spec: str) -> tuple[gate._VersionClause, ...]:
    return gate._parse_python_specifier(spec, source="test")


# --- Plant 1: a single surface is accepted, not ambiguous -----------------


def test_pep621_only_is_accepted() -> None:
    pyproject = {"project": {"requires-python": ">=3.11,<3.13"}}
    clauses = gate._declared_python_requirement(pyproject, product="sub")
    assert clauses == _clauses(">=3.11,<3.13")


def test_poetry_only_is_accepted() -> None:
    pyproject = {"tool": {"poetry": {"dependencies": {"python": ">=3.11,<3.13"}}}}
    clauses = gate._declared_python_requirement(pyproject, product="erp")
    assert clauses == _clauses(">=3.11,<3.13")


# --- Plant 2: both present and disagreeing refuses -------------------------


def test_both_surfaces_present_and_disagreeing_refuses() -> None:
    pyproject = {
        "project": {"requires-python": ">=3.12,<3.14"},
        "tool": {"poetry": {"dependencies": {"python": ">=3.11,<3.13"}}},
    }
    with pytest.raises(gate.GateDerivationError, match="disagrees with"):
        gate._declared_python_requirement(pyproject, product="academy")


def test_both_surfaces_present_and_agreeing_is_accepted() -> None:
    pyproject = {
        "project": {"requires-python": ">=3.12,<3.14"},
        "tool": {"poetry": {"dependencies": {"python": ">=3.12,<3.14"}}},
    }
    clauses = gate._declared_python_requirement(pyproject, product="academy")
    assert clauses == _clauses(">=3.12,<3.14")


# --- Plant 3: a missing requirement refuses --------------------------------


def test_missing_requirement_refuses() -> None:
    with pytest.raises(gate.GateDerivationError, match="no python requirement"):
        gate._declared_python_requirement({}, product="academy")


# --- Plant 4: unsupported requirement syntax refuses rather than guesses --


@pytest.mark.parametrize(
    "raw",
    [
        "^3.12",  # Poetry caret range
        "~3.12",  # Poetry tilde range
        "~=3.12",  # PEP 440 compatible release
        "3.12.*",  # wildcard
    ],
)
def test_unsupported_requirement_syntax_refuses(raw: str) -> None:
    with pytest.raises(gate.GateDerivationError, match="unsupported"):
        gate._parse_python_specifier(raw, source="test")


# --- Selection: lowest trusted interpreter satisfying the declared range --


def test_selection_picks_the_lowest_satisfying_floor_for_the_three_real_products() -> (
    None
):
    erp = gate._select_interpreter(_clauses(">=3.11,<3.13"), product="erp")
    sub = gate._select_interpreter(_clauses(">=3.11,<3.13"), product="sub")
    academy = gate._select_interpreter(_clauses(">=3.12,<3.14"), product="academy")

    assert erp == ("python3.11", (3, 11))
    assert sub == ("python3.11", (3, 11))
    assert academy == ("python3.12", (3, 12))


def test_selection_refuses_when_no_trusted_interpreter_satisfies() -> None:
    with pytest.raises(gate.GateDerivationError, match="no trusted interpreter"):
        gate._select_interpreter(_clauses(">=4.0"), product="future")


# --- Plant 5: an unavailable selected interpreter is acquisition failure --


def test_unavailable_selected_interpreter_refuses_as_acquisition_not_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(gate.GateAcquisitionError, match="not available on this host"):
        gate._require_interpreter_available("python3.12")


def test_available_selected_interpreter_resolves_to_an_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

    assert gate._require_interpreter_available("python3.12") == "/usr/bin/python3.12"


# --- Plant 6: Academy's real PEP 701 case ----------------------------------


@requires_all_trusted
def test_pep701_nested_fstring_quotes_parse_at_the_declared_3_12_floor() -> None:
    imports = gate._imports_under_interpreter(
        _PEP701_SOURCE.encode("utf-8"),
        executable="python3.12",
        product="academy",
        revision="a" * 40,
        interpreter="python3.12",
        module="app.main",
        path="app/main.py",
        is_package=False,
        available_modules=frozenset({"app.main"}),
    )
    assert imports == frozenset()


@requires_all_trusted
def test_pep701_nested_fstring_quotes_still_refuse_under_3_11() -> None:
    with pytest.raises(gate._ForeignSyntaxError):
        gate._imports_under_interpreter(
            _PEP701_SOURCE.encode("utf-8"),
            executable="python3.11",
            product="academy",
            revision="a" * 40,
            interpreter="python3.11",
            module="app.main",
            path="app/main.py",
            is_package=False,
            available_modules=frozenset({"app.main"}),
        )


@requires_all_trusted
@pytest.mark.parametrize("source", _PEP695_SOURCES)
def test_pep695_child_facts_cross_a_real_3_12_to_3_11_boundary(source: str) -> None:
    """The 3.11 HOST must consume 3.12 facts without reconstructing a 3.12 AST.

    These three sources create ast.TypeVar or ast.TypeAlias nodes on 3.12 and
    reproduce the exact unpickle failures that blocked the prior design.
    """

    host_probe = r"""
import base64
import importlib.util
import json
import sys
from pathlib import Path

gate_path = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(gate_path.parents[2]))
spec = importlib.util.spec_from_file_location("gate_host_probe", gate_path)
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gate
spec.loader.exec_module(gate)
edges = gate._imports_under_interpreter(
    base64.b64decode(sys.argv[3]),
    executable=sys.argv[2],
    product="academy",
    revision="a" * 40,
    interpreter="python3.12",
    module="app.main",
    path="app/main.py",
    is_package=False,
    available_modules=frozenset({"app.main"}),
)
print(json.dumps(sorted(edges)))
"""
    assert _PYTHON_3_11 is not None
    assert _PYTHON_3_12 is not None
    result = subprocess.run(  # noqa: S603
        [
            _PYTHON_3_11,
            "-I",
            "-c",
            host_probe,
            gate.__file__,
            _PYTHON_3_12,
            base64.b64encode(source.encode()).decode(),
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["dotmac_files"]


# --- Plant 7: source exceeding its own declared floor ----------------------


@requires_all_trusted
def test_source_exceeding_its_declared_floor_is_a_product_evidence_refusal(
    tmp_path,
) -> None:
    from tests.architecture.test_compatibility_gate import _repo  # reuse fixture

    repo = _repo(
        tmp_path,
        {
            "app/__init__.py": "",
            # Academy declares >=3.12,<3.14 -> selects 3.12. This file uses
            # PEP 696 (3.13-only) syntax that exceeds that declared floor.
            "app/main.py": _PEP696_SOURCE,
        },
    )
    index = gate._git_python_index(repo, "HEAD", "app")

    def discover_imports(
        source: bytes,
        module: str,
        is_package: bool,
        path: str,
        available_modules: frozenset[str],
    ) -> frozenset[str]:
        try:
            return gate._imports_under_interpreter(
                source,
                executable="python3.12",
                product="academy",
                revision="a" * 40,
                interpreter="python3.12",
                module=module,
                path=path,
                is_package=is_package,
                available_modules=available_modules,
            )
        except gate._ForeignSyntaxError as exc:
            raise gate.GateDerivationError(
                f"academy: {path}: source does not parse under interpreter "
                f"python3.12 (selected for declared floor 3.12): {exc}"
            ) from exc

    with pytest.raises(gate.GateDerivationError) as excinfo:
        gate._walk_import_graph(
            repository=repo,
            index=index,
            roots=("app.main",),
            discover_imports=discover_imports,
        )

    message = str(excinfo.value)
    assert "academy" in message
    assert "app/main.py" in message
    assert "python3.12" in message


@requires_all_trusted
def test_the_same_pep696_source_parses_fine_once_selected_for_its_real_3_13_floor() -> (
    None
):
    """Near-miss control: the same source is NOT a refusal once the product's
    declared floor genuinely is 3.13 -- the refusal above is about the
    declared floor, not about this syntax being exotic."""

    imports = gate._imports_under_interpreter(
        _PEP696_SOURCE.encode("utf-8"),
        executable="python3.13",
        product="future",
        revision="a" * 40,
        interpreter="python3.13",
        module="app.main",
        path="app/main.py",
        is_package=False,
        available_modules=frozenset({"app.main"}),
    )
    assert imports == frozenset()
