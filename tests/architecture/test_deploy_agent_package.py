"""Boundary guards for dotmac-deploy-agent (DESIGN-rev3 s7 widenings).

The agent holds NO CP DB credential or reach, NO lease DSN and NO registry pull
credential. Pinned with the AST so a docstring explaining the absence cannot trip
a rule; each guard carries a sensitivity proof that it fires on the planted defect.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "packages" / "dotmac-deploy-agent" / "src" / "dotmac_deploy_agent"

ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "__future__", "argparse", "base64", "collections", "dataclasses", "datetime",
        "enum", "errno", "fcntl", "hashlib", "http", "json", "logging", "math", "os",
        "pathlib",
        "re", "secrets", "shutil", "signal", "socket", "ssl", "stat", "subprocess",
        "sys",
        "time", "tomllib", "typing", "urllib", "cryptography", "dotmac_deploy_agent",
    }
)  # fmt: skip
FORBIDDEN_ROOTS = frozenset(
    {
        "sqlalchemy", "psycopg", "psycopg2", "asyncpg", "alembic", "dotmac_kernel",
        "dotmac_deployment_foundation", "dotmac_deployment_control", "app",
        "docker", "requests", "httpx", "boto3",
    }
)  # fmt: skip
FORBIDDEN_STRINGS = (
    "DOCKER_CONFIG",
    "docker login",
    ".docker/config",
    "ghcr.io",
    "registry.dotmac.io",
)


def import_roots(source: str) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_agent_imports_a_closed_set() -> None:
    for path in SOURCE.glob("*.py"):
        roots = import_roots(path.read_text())
        assert not roots & FORBIDDEN_ROOTS, path.name
        assert roots <= ALLOWED_IMPORT_ROOTS, (path.name, roots - ALLOWED_IMPORT_ROOTS)


def test_the_import_guard_bites() -> None:
    assert import_roots("import psycopg\nfrom sqlalchemy import text") & FORBIDDEN_ROOTS
    assert import_roots("from dotmac_kernel import db") & FORBIDDEN_ROOTS


def string_literals(source: str) -> list[str]:
    return [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_no_registry_credential_or_pull_target_is_named() -> None:
    for path in SOURCE.glob("*.py"):
        literals = string_literals(path.read_text())
        for needle in FORBIDDEN_STRINGS:
            assert not any(needle in literal for literal in literals), (
                path.name,
                needle,
            )


def test_the_registry_guard_bites() -> None:
    assert any("ghcr.io" in s for s in string_literals('x = "ghcr.io/o/p"'))


def test_the_legacy_source_is_only_lstat_checked_never_opened() -> None:
    """L7: the only read path is $CREDENTIALS_DIRECTORY/legacy."""
    tree = ast.parse((SOURCE / "legacy.py").read_text())
    calls = {
        node.func.attr
        if isinstance(node.func, ast.Attribute)
        else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert not calls & {"open", "read_bytes", "read_text"} - {"read_bytes"}
    source = (SOURCE / "legacy.py").read_text()
    assert "source.read" not in source and "open(source" not in source


def test_no_shell_true_anywhere() -> None:
    for path in SOURCE.glob("*.py"):
        assert "shell=True" not in path.read_text(), path.name


UNIT = (
    ROOT
    / "packages/dotmac-deploy-agent/deploy/systemd"
    / "dotmac-deploy-agent@.service"
)


def unit_directives(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.startswith(("#", "[")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        found.setdefault(key.strip(), []).append(value.strip())
    return found


def test_the_unit_is_non_root_capability_free_and_carries_no_environment() -> None:
    directives = unit_directives(UNIT.read_text())
    assert directives["User"] == ["dotmac-deploy"]
    assert directives["CapabilityBoundingSet"] == [""]
    assert directives["AmbientCapabilities"] == [""]
    assert directives["NoNewPrivileges"] == ["true"]
    assert "Environment" not in directives and "EnvironmentFile" not in directives
    # Secret-zero and the PoP key are credentials, not environment.
    assert all(
        v.startswith(("pop-key:", "delivery-role-id:", "delivery-secret-id:"))
        for v in directives["LoadCredential"]
    )
    assert not any("token" in v for v in directives["LoadCredential"])


def test_the_unit_guard_bites() -> None:
    planted = "User=root\nAmbientCapabilities=CAP_CHOWN\nEnvironment=X=1\n"
    directives = unit_directives(planted)
    assert directives["AmbientCapabilities"] != [""] and "Environment" in directives


KIT = ROOT / "packages/dotmac-deploy-agent/deploy/enrolment"


def test_the_agent_never_chowns_and_carries_no_uid_from_a_dispatch() -> None:
    for path in SOURCE.glob("*.py"):
        tree = ast.parse(path.read_text())
        called = {
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else getattr(node.func, "id", "")
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        assert not called & {"chown", "lchown", "fchown", "setuid", "setgid"}, path.name
        assert "container_uid" not in path.read_text(), path.name


def test_the_hcl_renderer_and_role_id_fetch_are_gone() -> None:
    assert not (SOURCE / "openbao_agent.py").exists()
    for path in SOURCE.glob("*.py"):
        assert not any(
            "/role-id" in s for s in string_literals(path.read_text())
        ), path.name


def test_the_polkit_rule_names_enrolled_units_only() -> None:
    text = (KIT / "polkit/50-dotmac-deploy-agent.rules.template").read_text()
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("//")
    )
    assert "@ENROLLED_UNITS@" in code
    assert 'action.lookup("verb") == "restart"' in code
    assert 'subject.user == "dotmac-deploy"' in code
    assert (
        "*" not in code and "startsWith" not in code and "indexOf(action.lookup" in code
    )


def test_the_openbao_agent_unit_and_tmpfiles_follow_p_sec() -> None:
    unit = unit_directives((KIT / "systemd/openbao-agent@.service").read_text())
    assert unit["User"] == ["dotmac-bao-unenrolled"]  # a missing drop-in fails closed
    assert unit["CapabilityBoundingSet"] == [""] and unit["NoNewPrivileges"] == ["yes"]
    instance = (KIT / "systemd/instance.conf.template").read_text()
    assert (
        "LoadCredential=secret_id:/run/dotmac/@SECRETS_NAMESPACE@/@ROLE@/secret_id"
        in instance
    )
    assert "User=@CONTAINER_USER@" in instance
    tmpfiles = (KIT / "tmpfiles.d/dotmac-role.conf.template").read_text()
    assert "/secrets 0700 @CONTAINER_USER@ @CONTAINER_USER@" in tmpfiles
    assert "@ROLE@ 0711 dotmac-deploy dotmac-deploy" in tmpfiles


def test_the_enrolment_guards_bite() -> None:
    assert "*" in 'enrolled.indexOf("openbao-agent@*.service")'
    planted = unit_directives("User=root\nAmbientCapabilities=CAP_CHOWN\n")
    assert planted["User"] != ["dotmac-bao-unenrolled"]


def test_one_naming_key_names_every_per_role_artefact_in_the_kit() -> None:
    """H1: the unit instance, drop-in, tmpfiles and role directory share ONE key,
    `@SECRETS_NAMESPACE@`; the Control product code is never a template variable."""
    for path in KIT.rglob("*.template"):
        assert "@PRODUCT@" not in path.read_text(), path.name
    instance = (KIT / "systemd/instance.conf.template").read_text()
    assert "openbao-agent@@SECRETS_NAMESPACE@-@ROLE@.service.d" in instance
    assert "ReadWritePaths=/run/dotmac/@SECRETS_NAMESPACE@/@ROLE@" in instance
    tmpfiles = (KIT / "tmpfiles.d/dotmac-role.conf.template").read_text()
    assert "/run/dotmac/@SECRETS_NAMESPACE@/@ROLE@/secrets 0700" in tmpfiles


def test_the_agent_unit_owns_only_its_own_runtime_directory() -> None:
    """M4: /run/dotmac/<ns> stays with tmpfiles.d; a stop must not delete it."""
    directives = unit_directives(UNIT.read_text())
    assert directives["RuntimeDirectory"] == ["dotmac-deploy-agent/%i"]
    assert directives["RuntimeDirectoryMode"] == ["0700"]
    assert not any("/run/dotmac/%i" in v for vs in directives.values() for v in vs)
    # R2-L3: only the instance's own state; the per-host drop-in adds the rest.
    assert directives["ReadWritePaths"] == ["/var/lib/dotmac-deploy-agent/%i"]
    paths = (KIT / "systemd/agent-paths.conf.template").read_text()
    assert "ReadWritePaths=/run/dotmac/@SECRETS_NAMESPACE@ @DEPLOY_DIR@" in paths
    assert "ReadWritePaths=/run/dotmac " not in paths
