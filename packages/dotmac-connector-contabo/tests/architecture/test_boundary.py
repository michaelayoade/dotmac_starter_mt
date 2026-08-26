from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).parents[2]
SOURCE = PACKAGE / "src" / "dotmac_connector_contabo"


def _modules() -> set[str]:
    imported: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
    return imported


def test_connector_is_stateless_and_has_no_generic_execution_surface() -> None:
    forbidden_modules = {
        "alembic",
        "asyncio.subprocess",
        "paramiko",
        "requests",
        "sqlalchemy",
        "subprocess",
    }
    assert not (_modules() & forbidden_modules)

    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert not ({"argv", "shell", "ssh_command"} & (names | attributes))


def test_manifest_has_no_provider_branch_or_unsupported_claim() -> None:
    plugin_path = SOURCE / "plugin.py"
    tree = ast.parse(plugin_path.read_text())
    manifest_capabilities: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == "MANIFEST"
            for target in targets
        ):
            continue
        value = node.value
        assert isinstance(value, ast.Call)
        for keyword in value.keywords:
            if keyword.arg == "capabilities":
                assert isinstance(keyword.value, ast.Tuple)
                manifest_capabilities = {
                    ast.unparse(element) for element in keyword.value.elts
                }

    assert manifest_capabilities == {
        "_declaration(FIREWALL_LIFECYCLE, INFRASTRUCTURE_SCHEMAS)"
    }


def test_real_transport_disables_redirects_and_environment_proxies() -> None:
    tree = ast.parse((SOURCE / "transport.py").read_text())
    client_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and ast.unparse(node.func) == "httpx.Client"
    ]
    assert len(client_calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in client_calls[0].keywords}
    assert isinstance(keywords["follow_redirects"], ast.Constant)
    assert keywords["follow_redirects"].value is False
    assert isinstance(keywords["trust_env"], ast.Constant)
    assert keywords["trust_env"].value is False


def test_no_operation_payload_can_include_or_return_secret_shape() -> None:
    forbidden = {
        "api_secret_ref",
        "client_secret",
        "password",
        "private_key",
        "secret",
        "token",
    }
    tree = ast.parse((SOURCE / "plugin.py").read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {
            key.value
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        if "evidence" in keys or "changes" in keys or "resource_ref" in keys:
            assert not (keys & forbidden)
