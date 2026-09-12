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
import sys
import time
from types import SimpleNamespace

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


@pytest.mark.parametrize(
    ("pep621", "poetry"),
    [
        (">=3.12,>=3.12", ">=3.12"),
        (">=3.12,<3.13", ">=3.12,<3.13,!=3.11"),
        (">3.12,<3.13", ">=3.12.1,<3.13"),
    ],
)
def test_both_surfaces_accept_semantically_equivalent_spellings(
    pep621: str, poetry: str
) -> None:
    pyproject = {
        "project": {"requires-python": pep621},
        "tool": {"poetry": {"dependencies": {"python": poetry}}},
    }
    assert gate._declared_python_requirement(pyproject, product="academy") == _clauses(
        pep621
    )


# --- Plant 3: a missing requirement refuses --------------------------------


def test_missing_requirement_refuses() -> None:
    with pytest.raises(gate.GateDerivationError, match="no python requirement"):
        gate._declared_python_requirement({}, product="academy")


def test_unsatisfiable_requirement_refuses_even_when_both_surfaces_agree() -> None:
    pyproject = {
        "project": {"requires-python": ">=3.12,<3.12"},
        "tool": {"poetry": {"dependencies": {"python": ">=3.12,<3.12"}}},
    }
    with pytest.raises(gate.GateDerivationError, match="no satisfying version"):
        gate._declared_python_requirement(pyproject, product="academy")


def test_exclusions_covering_a_finite_range_make_it_unsatisfiable() -> None:
    pyproject = {"project": {"requires-python": ">=3.12,<=3.12.1,!=3.12,!=3.12.1"}}
    with pytest.raises(gate.GateDerivationError, match="no satisfying version"):
        gate._declared_python_requirement(pyproject, product="academy")


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


def _fake_interpreter_versions(
    monkeypatch: pytest.MonkeyPatch,
    versions: dict[str, tuple[int, int, int]] | None = None,
) -> None:
    actual = versions or {
        "python3.11": (3, 11, 9),
        "python3.12": (3, 12, 11),
        "python3.13": (3, 13, 7),
    }
    monkeypatch.setattr(
        gate, "_require_interpreter_available", lambda name: f"/trusted/{name}"
    )
    monkeypatch.setattr(
        gate,
        "_probe_interpreter_version",
        lambda _executable, *, name: actual[name],
    )


def test_selection_picks_the_lowest_satisfying_floor_for_the_three_real_products(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_interpreter_versions(monkeypatch)
    erp = gate._select_interpreter(_clauses(">=3.11,<3.13"), product="erp")
    sub = gate._select_interpreter(_clauses(">=3.11,<3.13"), product="sub")
    academy = gate._select_interpreter(_clauses(">=3.12,<3.14"), product="academy")

    assert (erp.name, erp.version) == ("python3.11", (3, 11, 9))
    assert (sub.name, sub.version) == ("python3.11", (3, 11, 9))
    assert (academy.name, academy.version) == ("python3.12", (3, 12, 11))


def test_selection_uses_the_executable_patch_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_interpreter_versions(monkeypatch)
    selected = gate._select_interpreter(_clauses(">=3.12.1,<3.13"), product="academy")
    assert selected.version == (3, 12, 11)


def test_selection_refuses_when_the_floor_executable_patch_is_too_old(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_interpreter_versions(
        monkeypatch,
        versions={
            "python3.11": (3, 11, 9),
            "python3.12": (3, 12, 11),
            "python3.13": (3, 13, 7),
        },
    )
    with pytest.raises(gate.GateAcquisitionError, match="does not satisfy"):
        gate._select_interpreter(_clauses(">=3.12.12,<3.13"), product="academy")


def test_selection_refuses_a_path_shadowing_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_interpreter_versions(
        monkeypatch,
        versions={
            "python3.11": (3, 11, 9),
            "python3.12": (3, 13, 7),
            "python3.13": (3, 13, 7),
        },
    )
    with pytest.raises(gate.GateAcquisitionError, match="expected 3.12.x"):
        gate._select_interpreter(_clauses(">=3.12,<3.14"), product="academy")


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


def test_real_interpreter_version_probe_reads_the_executable_not_its_name() -> None:
    executable = shutil.which("python3.12")
    if executable is None:
        pytest.skip("python3.12 is not available")
    result = gate._probe_interpreter_version(executable, name="python3.12")
    probe = subprocess.run(  # noqa: S603
        [executable, "-I", "-c", "import sys; print(*sys.version_info[:3])"],
        capture_output=True,
        check=True,
        text=True,
    )
    assert result == tuple(int(part) for part in probe.stdout.split())


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


# --- Persistent-child protocol failure and lifecycle controls -------------


def _successful_protocol_response(request: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": request["schema_version"],
        "status": "ok",
        "product": request["product"],
        "revision": request["revision"],
        "interpreter": request["interpreter"],
        "module": request["module"],
        "path": request["path"],
        "imports": ["dotmac_files"],
        "error": None,
    }


def _one_shot_imports(monkeypatch: pytest.MonkeyPatch, response_factory) -> None:
    def fake_run(_args, *, input, **_kwargs):
        request = json.loads(input)
        return SimpleNamespace(
            stdout=response_factory(request), returncode=0, stderr=b""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    gate._imports_under_interpreter(
        b"import dotmac_files\n",
        executable="python3.12",
        product="academy",
        revision="a" * 40,
        interpreter="python3.12",
        module="app.main",
        path="app/main.py",
        is_package=False,
        available_modules=frozenset({"app.main"}),
    )


def test_protocol_refuses_duplicate_response_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def response(request):
        valid = json.dumps(_successful_protocol_response(request)).encode()
        return valid.replace(
            b'{"schema_version":',
            b'{"schema_version":"duplicate","schema_version":',
            1,
        )

    with pytest.raises(gate.GateAcquisitionError, match="duplicate JSON key"):
        _one_shot_imports(monkeypatch, response)


def test_protocol_refuses_a_changed_identity_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def response(request):
        payload = _successful_protocol_response(request)
        payload["revision"] = "b" * 40
        return json.dumps(payload).encode()

    with pytest.raises(gate.GateAcquisitionError, match="changed identity"):
        _one_shot_imports(monkeypatch, response)


@pytest.mark.parametrize(
    "imports",
    [
        ["dotmac_tax", "dotmac_files"],
        ["dotmac_files", "dotmac_files"],
        [""],
    ],
)
def test_protocol_refuses_unsorted_duplicate_or_empty_import_facts(
    monkeypatch: pytest.MonkeyPatch, imports: list[str]
) -> None:
    def response(request):
        payload = _successful_protocol_response(request)
        payload["imports"] = imports
        return json.dumps(payload).encode()

    with pytest.raises(gate.GateAcquisitionError, match="invalid import facts"):
        _one_shot_imports(monkeypatch, response)


def test_protocol_refuses_an_overlimit_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(gate.GateAcquisitionError, match="response exceeds"):
        _one_shot_imports(
            monkeypatch,
            lambda _request: b"x" * (gate._FOREIGN_RESPONSE_BYTE_LIMIT + 1),
        )


def test_protocol_refuses_an_overlimit_import_fact_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def response(request):
        payload = _successful_protocol_response(request)
        payload["imports"] = [
            f"module_{index:05d}" for index in range(gate._FOREIGN_IMPORT_LIMIT + 1)
        ]
        return json.dumps(payload).encode()

    with pytest.raises(gate.GateAcquisitionError, match="invalid import facts"):
        _one_shot_imports(monkeypatch, response)


def test_protocol_reports_a_child_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=b"", returncode=4, stderr=b"child refused"
        ),
    )
    with pytest.raises(gate.GateAcquisitionError, match="child refused"):
        gate._imports_under_interpreter(
            b"pass\n",
            executable="python3.12",
            product="academy",
            revision="a" * 40,
            interpreter="python3.12",
            module="app.main",
            path="app/main.py",
            is_package=False,
            available_modules=frozenset({"app.main"}),
        )


def test_one_shot_protocol_has_a_hard_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("python3.12", 10)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(gate.GateAcquisitionError, match="analysis timed out"):
        gate._imports_under_interpreter(
            b"pass\n",
            executable="python3.12",
            product="academy",
            revision="a" * 40,
            interpreter="python3.12",
            module="app.main",
            path="app/main.py",
            is_package=False,
            available_modules=frozenset({"app.main"}),
        )


def test_persistent_reader_drains_stderr_while_waiting_for_stdout() -> None:
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-I",
            "-c",
            "import sys,time; "
            "sys.stderr.buffer.write(b'x' * 50000); sys.stderr.flush(); "
            "sys.stdout.buffer.write(b'{\\\"ok\\\":true}\\n'); sys.stdout.flush(); "
            "time.sleep(60)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd="/",
    )
    try:
        response, _returncode, stderr = gate._read_foreign_response(
            process, executable=sys.executable, timeout=2
        )
        assert json.loads(response) == {"ok": True}
        assert stderr == b"x" * 50000
    finally:
        gate._stop_foreign_process(process)
    assert process.poll() is not None


def test_persistent_reader_drains_and_bounds_pipe_filling_stderr() -> None:
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-I",
            "-c",
            "import sys,time; "
            "sys.stderr.buffer.write(b'x' * 100000); sys.stderr.flush(); "
            "sys.stdout.buffer.write(b'{\\\"ok\\\":true}\\n'); sys.stdout.flush(); "
            "time.sleep(60)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd="/",
    )
    try:
        with pytest.raises(gate.GateAcquisitionError, match="stderr exceeds"):
            gate._read_foreign_response(process, executable=sys.executable, timeout=2)
    finally:
        gate._stop_foreign_process(process)
    assert process.poll() is not None


def test_blocked_child_times_out_and_cleanup_escalates_to_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(__import__("signal"), "SIGTERM"):
        pytest.skip("requires POSIX process signals")
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-I",
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(60)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd="/",
    )
    time.sleep(0.1)
    with pytest.raises(gate.GateAcquisitionError, match="timed out"):
        gate._read_foreign_response(process, executable=sys.executable, timeout=0.05)
    monkeypatch.setattr(gate, "_FOREIGN_CLEANUP_TIMEOUT_SECONDS", 0.05)
    started = time.monotonic()
    gate._stop_foreign_process(process)
    assert process.poll() is not None
    assert time.monotonic() - started < 1


def test_child_blocked_before_read_cannot_deadlock_a_large_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd="/",
    )
    try:
        with pytest.raises(gate.GateAcquisitionError, match="write timed out"):
            gate._write_foreign_request(
                process,
                b"x" * 1_000_000,
                executable=sys.executable,
                timeout=0.05,
            )
    finally:
        monkeypatch.setattr(gate, "_FOREIGN_CLEANUP_TIMEOUT_SECONDS", 0.05)
        gate._stop_foreign_process(process)
    assert process.poll() is not None
