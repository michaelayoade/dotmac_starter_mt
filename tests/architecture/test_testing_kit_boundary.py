"""`dotmac_kernel.testing` may be imported by tests and the floor probe only.

The kit ships INSIDE the kernel wheel, so it is importable from anywhere a
deployment runs — there is no packaging boundary keeping it out of production
code (ADR-0006 amendment 2026-08-11 accepts that deliberately). What keeps it
out is this contract.

Why it matters concretely. The kit holds an RLS-free SQLite engine
(`create_test_engine`), a session factory outside the one transaction authority
(`isolated_session`), and an Ed25519 signing helper (`FakeLicenceSigner`). Any
of those reached from application, module, or ordinary kernel runtime code is a
production defect wearing a test-support name: a request served from a session
no tenancy policy governs, or a licence envelope signed by a key the deployment
generated for itself.

Two things this deliberately does NOT do:

- It does not check `scripts/kernel_floor_check.sh`, which names the kit in the
  products' import allowlists. That third exemption is fleet Governance work
  and lands after this starter canary proves the rule.
- It does not scan the kit's own tree. `testing/__init__.py` re-exporting from
  `testing.fakes` is the package assembling itself, not a boundary crossing.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_KIT = "dotmac_kernel.testing"
_KIT_TREE = REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/testing"

# Trees that must NEVER import the kit: the assembly, ordinary kernel runtime
# code, every installed module, and the UI package.
_FORBIDDEN_TREES = (
    REPO_ROOT / "app",
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel",
    REPO_ROOT / "packages/dotmac-template-studio/src",
    REPO_ROOT / "packages/dotmac-ui/src",
)

# The exact non-test importer. `scripts/` is operator tooling and is otherwise
# out of scope, but the floor probe is named EXPLICITLY rather than exempting
# the whole directory — naming the file is the difference between an exemption
# with an enforceable premise and a blind spot (ADR-0018).
_PERMITTED_NON_TEST_IMPORTERS = (REPO_ROOT / "scripts/floor/probe.py",)


def _imports_the_kit(source: str) -> list[int]:
    """Line numbers of every import that reaches `dotmac_kernel.testing`.

    Covers all three spellings: `import dotmac_kernel.testing[.x]`,
    `from dotmac_kernel.testing[.x] import y`, and `from dotmac_kernel import
    testing`.
    """
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _KIT or alias.name.startswith(f"{_KIT}."):
                    lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == _KIT or module.startswith(f"{_KIT}."):
                lines.append(node.lineno)
            elif module == "dotmac_kernel" and any(
                alias.name == "testing" for alias in node.names
            ):
                lines.append(node.lineno)
    return lines


def _python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if _KIT_TREE in path.parents or path == _KIT_TREE:
            continue  # the kit assembling itself is not a boundary crossing
        yield path


def test_no_runtime_code_imports_the_testing_kit() -> None:
    violations: list[str] = []
    scanned = 0
    for tree in _FORBIDDEN_TREES:
        assert tree.is_dir(), f"scan root missing: {tree}"
        for path in _python_files(tree):
            scanned += 1
            for lineno in _imports_the_kit(path.read_text(encoding="utf-8")):
                rel = path.relative_to(REPO_ROOT)
                violations.append(f"{rel}:{lineno}")
    assert not violations, (
        "`dotmac_kernel.testing` imported by non-test code:\n"
        + "\n".join(violations)
        + "\n\nThe kit ships in the runtime wheel but is not runtime code: it "
        "holds an RLS-free engine, a session factory outside the transaction "
        "authority, and a signing key generator."
    )
    # Non-vacuity: a glob that silently matched nothing would make the
    # assertion above pass while checking no files at all.
    assert scanned > 100, f"only {scanned} files scanned — the roots look wrong"


def test_the_detector_actually_finds_a_kit_import() -> None:
    """Sensitivity proof against a REAL file, not a synthetic string.

    `scripts/floor/probe.py` genuinely imports the kit. If the detector stops
    recognising it, the test above becomes a green no-op — the exact failure
    mode a path-shaped guard is prone to.
    """
    probe = _PERMITTED_NON_TEST_IMPORTERS[0]
    assert probe.is_file(), f"the permitted importer is gone: {probe}"
    assert _imports_the_kit(probe.read_text(encoding="utf-8")), (
        f"{probe.relative_to(REPO_ROOT)} no longer imports the kit — either "
        "the detector broke, or this entry is stale and should be removed"
    )


def test_the_detector_recognises_every_import_spelling() -> None:
    assert _imports_the_kit("import dotmac_kernel.testing") == [1]
    assert _imports_the_kit("import dotmac_kernel.testing.harness") == [1]
    assert _imports_the_kit("from dotmac_kernel.testing import FakeClock") == [1]
    assert _imports_the_kit("from dotmac_kernel.testing.fakes import FakeClock") == [1]
    assert _imports_the_kit("from dotmac_kernel import testing") == [1]
    # Near misses that must NOT trip it.
    assert _imports_the_kit("from dotmac_kernel import db") == []
    assert _imports_the_kit("import dotmac_kernel") == []
    assert _imports_the_kit("# from dotmac_kernel.testing import FakeClock") == []
    assert _imports_the_kit('X = "dotmac_kernel.testing"') == []
