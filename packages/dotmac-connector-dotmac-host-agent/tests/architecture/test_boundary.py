from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).parents[2]
SOURCE = PACKAGE / "src" / "dotmac_connector_dotmac_host_agent"


def _imports() -> set[str]:
    modules: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.split(".", 1)[0])
    return modules


def test_client_has_no_state_or_generic_execution_dependencies() -> None:
    forbidden = {
        "alembic",
        "asyncio.subprocess",
        "fabric",
        "paramiko",
        "requests",
        "sqlalchemy",
        "subprocess",
    }
    assert not (_imports() & forbidden)


def test_source_cannot_express_shell_ssh_argv_or_file_run() -> None:
    forbidden_names = {
        "argv",
        "command",
        "command_line",
        "executable",
        "run_file",
        "shell",
        "ssh",
        "startup_script",
    }
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        names = {
            node.id.casefold() for node in ast.walk(tree) if isinstance(node, ast.Name)
        }
        attributes = {
            node.attr.casefold()
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        assert not (forbidden_names & (names | attributes))


def test_real_transport_disables_redirects_and_environment_proxies() -> None:
    tree = ast.parse((SOURCE / "transport.py").read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and ast.unparse(node.func) == "httpx.Client"
    ]
    assert len(calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    assert isinstance(keywords["follow_redirects"], ast.Constant)
    assert keywords["follow_redirects"].value is False
    assert isinstance(keywords["trust_env"], ast.Constant)
    assert keywords["trust_env"].value is False


def test_routes_are_closed_capability_operation_families() -> None:
    transport_tree = ast.parse((SOURCE / "transport.py").read_text())
    patterns = [
        node.value
        for node in ast.walk(transport_tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("^/v1/")
    ]
    assert len(patterns) == 1
    pattern = patterns[0]
    assert "capabilities" in pattern
    assert "provision" in pattern
    assert "apply|cancel|observe|plan" in pattern
    assert not any(
        token in pattern for token in ("exec", "file", "run", "shell", "ssh")
    )


def test_public_evidence_is_validated_by_exact_owner_schema() -> None:
    tree = ast.parse((SOURCE / "plugin.py").read_text())
    validator_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and ast.unparse(node.func).endswith(".validate")
    ]
    assert len(validator_calls) == 1
    assert "CAPABILITY_SCHEMAS" in (SOURCE / "plugin.py").read_text()
