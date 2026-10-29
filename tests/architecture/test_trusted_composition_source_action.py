"""The pre-import composition-source check is an immutable, closed action."""

from __future__ import annotations

import hashlib
import importlib.util
import py_compile
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github/actions/verify-composition-contract-sources"
TRUSTED_REVISION = "8b4b6d4b42e650c47fe4c04a679a5ccb51c4b2cd"
TRUSTED_PATHS = (
    "tools/composition_contract/composition_schema.py",
    "tools/composition_contract/observations.py",
    "tools/composition_contract/specs.py",
)


def _module(name: str = "verify") -> ModuleType:
    if str(ACTION) not in sys.path:
        sys.path.insert(0, str(ACTION))
    spec = importlib.util.spec_from_file_location(
        f"trusted_source_action_{name}", ACTION / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repository), *arguments],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_action_owns_a_closed_nonempty_trust_anchor() -> None:
    module = _module()

    assert module.TRUSTED_CONTRACT_REVISION == TRUSTED_REVISION
    assert module.TRUSTED_SEMANTIC_PATHS == TRUSTED_PATHS
    with pytest.raises(ValueError, match="protected helper set is closed"):
        module.find_source_drift(ROOT, paths=())

    action = yaml.safe_load((ACTION / "action.yml").read_text())
    assert "inputs" not in action
    step = action["runs"]["steps"][0]
    command = step["run"]
    assert command == 'python -I "$GITHUB_ACTION_PATH/run_gate.py"'
    assert "${{" not in command

    runner = _module("run_gate")
    assert runner.EXPECTED_STATUS == {
        "academy": "satisfied",
        "erp": "satisfied",
        "sub": "satisfied",
    }
    assert runner.EXPECTED_REVISIONS == {
        "academy": "ca1f9058a6483fe52207556fa2d17c56b1e237c7",
        "erp": "134abce8a74a205bd9636cafcf232178acb70874",
        "sub": "272a897b778899b110c5790fcaf43bbb54efe27c",
    }


def test_byte_pins_refuse_a_changed_gate_or_binding(tmp_path: Path) -> None:
    runner = _module("run_gate")
    path = tmp_path / "candidate"
    path.write_bytes(b"trusted")
    expected = hashlib.sha256(b"trusted").hexdigest()

    trusted = path.read_bytes()
    runner._require_sha256(trusted, expected, label="candidate")
    path.write_bytes(b"changed")
    with pytest.raises(runner.TrustedRunnerError, match="not the pinned candidate"):
        runner._require_sha256(path.read_bytes(), expected, label="candidate")


def test_verified_incompatibility_is_a_failing_process_verdict() -> None:
    runner = _module("run_gate")

    assert (
        runner._compatibility_exit_code(SimpleNamespace(compatibility_satisfied=False))
        == 1
    )
    assert (
        runner._compatibility_exit_code(SimpleNamespace(compatibility_satisfied=True))
        == 0
    )


def test_evidence_refusal_preserves_the_product_diagnosis_before_raising(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _module("run_gate")
    diagnosis = (
        "COMPATIBILITY REFUSED: 1 of 3 product evaluation(s) refused\n"
        "COMPATIBILITY SATISFIED: academy @ academy-revision; no findings\n"
        "COMPATIBILITY REFUSED (evidence_refused): erp @ erp-revision; "
        "erp: observation 'install-recipe' extract digest differs\n"
        "COMPATIBILITY SATISFIED: sub @ sub-revision; no findings\n"
        "ADOPTION NOT EVALUATED"
    )
    result = SimpleNamespace(
        evaluations=(
            SimpleNamespace(product="academy"),
            SimpleNamespace(product="erp"),
            SimpleNamespace(product="sub"),
        ),
        evidence_verified=False,
        explain=lambda: diagnosis,
    )

    with pytest.raises(
        runner.TrustedRunnerError,
        match="gate did not verify all three product records",
    ):
        runner._report_and_require_expected_result(result)

    assert capsys.readouterr().out == f"{diagnosis}\n"


def test_expected_result_is_reported_exactly_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _module("run_gate")
    result = _satisfied_result()
    runner._report_and_require_expected_result(result)
    assert capsys.readouterr().out == "expected product detail\n"


def _satisfied_result() -> SimpleNamespace:
    return SimpleNamespace(
        evaluations=tuple(
            SimpleNamespace(
                product=product,
                compatibility_status="satisfied",
                deferred_debt=(),
                runtime_exposure=SimpleNamespace(unknown=0),
            )
            for product in ("academy", "erp", "sub")
        ),
        evidence_verified=True,
        compatibility_satisfied=True,
        adoption_status="not_evaluated",
        explain=lambda: "expected product detail",
    )


@pytest.mark.parametrize("product", ("academy", "erp", "sub"))
@pytest.mark.parametrize("violation", ("debt", "status", "unknown", "missing_exposure"))
def test_every_product_must_be_satisfied_without_debt_or_unknowns(
    product: str,
    violation: str,
) -> None:
    runner = _module("run_gate")
    result = _satisfied_result()
    evaluation = next(item for item in result.evaluations if item.product == product)
    if violation == "debt":
        evaluation.deferred_debt = (SimpleNamespace(source_path="reached.py", sites=1),)
    elif violation == "status":
        evaluation.compatibility_status = "deferred_runtime_debt"
    elif violation == "unknown":
        evaluation.runtime_exposure.unknown = 1
    else:
        evaluation.runtime_exposure = None
    with pytest.raises(runner.TrustedRunnerError):
        runner._require_expected_result(result)


@pytest.mark.parametrize("violation", ("duplicate", "incompatible", "adoption"))
def test_clean_result_cannot_mask_duplicate_products_incompatibility_or_adoption(
    violation: str,
) -> None:
    runner = _module("run_gate")
    result = _satisfied_result()
    if violation == "duplicate":
        result.evaluations += (result.evaluations[0],)
    elif violation == "incompatible":
        result.compatibility_satisfied = False
    else:
        result.adoption_status = "adopted"
    with pytest.raises(runner.TrustedRunnerError):
        runner._require_expected_result(result)


def _configuration(runner: ModuleType) -> SimpleNamespace:
    return SimpleNamespace(
        bindings={
            product: SimpleNamespace(revision=revision)
            for product, revision in runner.EXPECTED_REVISIONS.items()
        },
        catalogue_revision="8c12998b57a31b5cf4a3e296d092460f97e638ba",
        kernel_subject=SimpleNamespace(
            distribution="dotmac-kernel",
            version="0.1.0a107",
            tag="dotmac-kernel-v0.1.0a107",
            peeled_commit="8c12998b57a31b5cf4a3e296d092460f97e638ba",
        ),
        published_sources=(object(),),
    )


def test_runner_passes_captured_bytes_to_parser_and_retains_configuration() -> None:
    runner = _module("run_gate")
    configuration = _configuration(runner)
    captured = b"captured v4 configuration"
    calls = []
    gate = SimpleNamespace(
        parse_bindings=lambda content: calls.append(content) or configuration,
    )
    assert runner._require_exact_bindings(gate, captured) is configuration
    assert calls == [captured]


@pytest.mark.parametrize("violation", ("revision", "catalogue", "subject"))
def test_runner_refuses_changed_closed_v4_coordinates(violation: str) -> None:
    runner = _module("run_gate")
    configuration = _configuration(runner)
    if violation == "revision":
        configuration.bindings["erp"].revision = "a" * 40
    elif violation == "catalogue":
        configuration.catalogue_revision = "a" * 40
    else:
        configuration.kernel_subject.version = "0.1.0a106"
    gate = SimpleNamespace(parse_bindings=lambda content: configuration)
    with pytest.raises(runner.TrustedRunnerError):
        runner._require_exact_bindings(gate, b"captured")


def test_main_checks_both_byte_pins_before_loading_and_parsing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _module("run_gate")
    events = []
    configuration = _configuration(runner)
    result = _satisfied_result()

    def parse(content: bytes) -> SimpleNamespace:
        events.append(("parse", content))
        return configuration

    def evaluate(bindings: object, **kwargs: object) -> SimpleNamespace:
        assert bindings is configuration
        events.append(("evaluate", None))
        return result

    gate = SimpleNamespace(parse_bindings=parse, evaluate_gate=evaluate)
    monkeypatch.setattr(runner, "_workspace", lambda: tmp_path)
    monkeypatch.setattr(
        runner,
        "_load_verifier",
        lambda: SimpleNamespace(
            read_verified_sources=lambda workspace: {},
        ),
    )
    monkeypatch.setattr(
        runner, "_read_regular_file", lambda path, **kwargs: path.name.encode()
    )
    monkeypatch.setattr(
        runner,
        "_require_sha256",
        lambda content, expected, **kwargs: events.append(("pin", content)),
    )

    def load(*args: object, **kwargs: object) -> SimpleNamespace:
        events.append(("load", None))
        return gate

    monkeypatch.setattr(runner, "_load_verified_gate", load)
    assert runner.main() == 0
    assert events == [
        ("pin", b"compatibility_gate.py"),
        ("pin", b"compatibility_gate_bindings.json"),
        ("load", None),
        ("parse", b"compatibility_gate_bindings.json"),
        ("evaluate", None),
    ]


@pytest.mark.parametrize("failed_label", ("compatibility gate", "bindings"))
def test_main_refuses_bad_byte_pins_before_candidate_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failed_label: str,
) -> None:
    runner = _module("run_gate")
    monkeypatch.setattr(runner, "_workspace", lambda: tmp_path)
    monkeypatch.setattr(
        runner,
        "_load_verifier",
        lambda: SimpleNamespace(
            read_verified_sources=lambda workspace: {},
            TrustedSourceAcquisitionError=OSError,
        ),
    )
    monkeypatch.setattr(
        runner, "_read_regular_file", lambda path, **kwargs: b"captured"
    )
    loaded = []
    monkeypatch.setattr(
        runner, "_load_verified_gate", lambda *args, **kwargs: loaded.append(True)
    )

    def require(content: bytes, expected: str, *, label: str) -> None:
        if label == failed_label:
            raise runner.TrustedRunnerError("changed pinned bytes")

    monkeypatch.setattr(runner, "_require_sha256", require)
    assert runner.main() == 1
    assert loaded == []


def test_v4_parser_refusal_is_not_replaced_with_reconstructed_bindings() -> None:
    runner = _module("run_gate")

    def refuse(content: bytes) -> None:
        raise ValueError("closed v4 schema refused")

    with pytest.raises(ValueError, match="closed v4 schema refused"):
        runner._require_exact_bindings(
            SimpleNamespace(parse_bindings=refuse), b"captured"
        )


def test_explicit_loader_never_executes_candidate_package_initializers(
    tmp_path: Path,
) -> None:
    runner = _module("run_gate")
    contract = tmp_path / "tools" / "composition_contract"
    contract.mkdir(parents=True)
    marker = tmp_path / "initializer-ran"
    (tmp_path / "tools" / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('tools')\n"
    )
    (contract / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('contract')\n"
    )
    helper_sources = {
        f"tools/composition_contract/{filename}": b"VALUE = 1\n"
        for _, filename in runner._HELPER_MODULES
    }
    for _, filename in runner._HELPER_MODULES:
        (contract / filename).write_text("raise RuntimeError('reopened helper')\n")
    gate_path = contract / "compatibility_gate.py"
    gate_path.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('source')\n"
    )
    (contract / "__pycache__").mkdir()
    py_compile.compile(
        str(gate_path),
        cfile=str(contract / "__pycache__" / "compatibility_gate.pyc"),
        doraise=True,
    )

    loaded = runner._load_verified_gate(
        tmp_path,
        helper_sources=helper_sources,
        gate_source=b"VALUE = 2\n",
    )

    assert loaded.VALUE == 2
    assert not marker.exists()


def test_single_open_reader_refuses_symlinks_and_returns_captured_bytes(
    tmp_path: Path,
) -> None:
    runner = _module("run_gate")
    target = tmp_path / "target"
    target.write_bytes(b"first")
    captured = runner._read_regular_file(target, label="target")
    target.write_bytes(b"second")
    assert captured == b"first"

    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(runner.TrustedRunnerAcquisitionError, match="unreadable"):
        runner._read_regular_file(link, label="link")


def test_action_detects_current_drift_against_one_git_coordinate(
    tmp_path: Path,
) -> None:
    module = _module()
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Trust Test")
    _git(repository, "config", "user.email", "trust@example.invalid")
    _git(repository, "config", "commit.gpgsign", "false")
    for relative in TRUSTED_PATHS:
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "trusted fixture")
    revision = _git(repository, "rev-parse", "HEAD")

    assert module.find_source_drift(repository, revision=revision) == ()
    changed = repository / TRUSTED_PATHS[0]
    changed.write_text("# changed\n")

    assert module.find_source_drift(repository, revision=revision) == (
        TRUSTED_PATHS[0],
    )
