"""ADR-0009 (static half): no settings module can reach a network.

The kernel's position on secret stores is meant to be a property it has rather
than a policy it argues for, so it is tested. This half catches a dereference on
a path no test happens to drive; the runtime half —
`tests/unit/test_secrets_no_network.py` — proves resolution completes with
sockets unavailable.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from dotmac_kernel import settings_resolver as sr

KERNEL = Path(sr.__file__).parent

# Every module that participates in resolving a setting. If resolution grows a
# new module, add it here — the point is that the WHOLE path is clean, not that
# one file is.
RESOLUTION_MODULES = (
    "settings_resolver.py",
    "settings_models.py",
    "settings_cache.py",
    "settings_crypto.py",
    "setting_scopes.py",
    "setting_value_types.py",
    "setting_domains.py",
    "secrets.py",
)

# Not an exhaustive list of the internet — an exhaustive list of the ways a
# dereference would plausibly be written.
NETWORK_MODULES = {
    "aiohttp",
    "asyncio",
    "azure",
    "boto3",
    "botocore",
    "ftplib",
    "google",
    "http",
    "httpx",
    "hvac",
    "requests",
    "smtplib",
    "socket",
    "subprocess",
    "telnetlib",
    "urllib",
    "urllib3",
}


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("module", RESOLUTION_MODULES)
def test_no_resolution_module_imports_a_network_client(module: str) -> None:
    """Note `secrets.py` is in scope: the module that HOLDS secrets must not
    also be able to fetch them, or the distinction it exists to draw
    disappears."""
    path = KERNEL / module
    assert path.exists(), f"{module} moved — update RESOLUTION_MODULES"
    offending = _imported_roots(path) & NETWORK_MODULES
    assert not offending, (
        f"{module} imports {sorted(offending)}. Settings resolution must reach "
        "no network: a secret is held, not fetched (ADR-0009). A product that "
        "needs material from a store installs a `SecretSource` or a "
        "`KeyProvider` at startup instead."
    )


def test_the_check_would_notice_a_network_import(tmp_path: Path) -> None:
    """Sensitivity proof: a passing suite must mean the rule holds, not that
    the detector is broken."""
    planted = tmp_path / "planted.py"
    planted.write_text("import httpx\nfrom urllib import parse\n", encoding="utf-8")
    assert _imported_roots(planted) & NETWORK_MODULES == {"httpx", "urllib"}


def test_secrets_module_has_no_accessor_that_dumps_every_value() -> None:
    """`secret_names()` exists and returns names. A mapping accessor must not:
    that is how held secrets end up rendered on a debug page."""
    from dotmac_kernel import secrets

    assert all(isinstance(name, str) for name in secrets.secret_names())
    for forbidden in ("secret_values", "all_secrets", "held_secrets", "as_dict"):
        assert not hasattr(secrets, forbidden), (
            f"`secrets.{forbidden}` would expose every held value at once — "
            "callers ask for one secret by name"
        )
