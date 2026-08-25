"""The first connector stays a stateless wire adapter, not a product/runtime."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import dotmac_connector_whatsapp
from dotmac_connector_whatsapp import MANIFEST

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = PROJECT_ROOT / "packages" / "dotmac-connector-whatsapp"
SOURCE = PACKAGE / "src" / "dotmac_connector_whatsapp"


def _imports() -> set[str]:
    roots: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_connector_version_is_one_fact_on_every_public_surface() -> None:
    declared = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    version = declared["tool"]["poetry"]["version"]
    assert version == dotmac_connector_whatsapp.__version__ == MANIFEST.version


def test_connector_imports_only_the_spi_among_dotmac_packages() -> None:
    internal = {name for name in _imports() if name.startswith("dotmac_")}
    # The public package surface imports its own implementation; only imports
    # crossing out of this distribution are subject to the sibling boundary.
    assert "dotmac_connector_whatsapp" in internal
    assert internal - {"dotmac_connector_whatsapp"} == {"dotmac_integration"}


def test_connector_has_no_persistence_or_execution_dependency() -> None:
    forbidden = {
        "alembic",
        "asyncpg",
        "psycopg",
        "sqlalchemy",
        "celery",
        "tenacity",
    }
    assert not (_imports() & forbidden)


def test_ingress_handler_performs_no_network_io() -> None:
    source = (SOURCE / "plugin.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    ingress = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "WhatsAppIngressHandler"
    )
    rendered = ast.unparse(ingress)
    assert "httpx" not in rendered
    assert ".post(" not in rendered


def test_connector_has_no_product_decision_vocabulary() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SOURCE.rglob("*.py"))
    ).lower()
    for forbidden in (
        "subscriber_id",
        "conversation_id",
        "ticket_id",
        "assign_team",
        "entitlement",
        "permission",
    ):
        assert forbidden not in source


def test_connector_package_is_not_composed_into_the_starter_runtime() -> None:
    assembly = (PROJECT_ROOT / "app" / "assembly.py").read_text(encoding="utf-8")
    root = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime_dependencies = root["tool"]["poetry"]["dependencies"]
    dev_dependencies = root["tool"]["poetry"]["group"]["dev"]["dependencies"]
    assert "dotmac_connector_whatsapp" not in assembly
    assert "dotmac-connector-whatsapp" not in runtime_dependencies
    assert "dotmac-connector-whatsapp" in dev_dependencies


def _source(name: str) -> str:
    return (SOURCE / name).read_text(encoding="utf-8")


def test_the_catalogue_cache_never_outlives_the_process() -> None:
    """A memo of provider facts, not a second authority on what is approved.

    The moment the connector persists the catalogue it owns a projection: rows
    that can disagree with the provider, that need repair, and that no product
    reconciler is watching. It is allowed to remember; it is not allowed to
    store.
    """
    forbidden = {
        "sqlite3",
        "shelve",
        "pickle",
        "pathlib",
        "tempfile",
        "redis",
        "diskcache",
    }
    assert not (_imports() & forbidden)
    joined = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SOURCE.rglob("*.py"))
    )
    for token in ("open(", "os.environ", ".write_text(", ".read_text("):
        assert token not in joined


def _identifiers() -> set[str]:
    """Every name the connector's CODE uses.

    Prose is excluded on purpose. The package docstring says the connector owns
    no "checkpoint" — a text scan would read that disclaimer as the violation it
    exists to deny, and the usual repair is to delete the sentence, which makes
    the boundary less documented and no better enforced.
    """
    names: set[str] = set()
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.FunctionDef | ast.ClassDef):
                names.add(node.name)
            elif isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg)
    return {name.lower() for name in names}


def test_the_connector_owns_no_retry_checkpoint_or_ledger_vocabulary() -> None:
    """Those belong to `dotmac-integration`, which is why they are not here.

    A connector that grew its own attempt counter or cursor store would be a
    second execution engine wearing a plugin's name.
    """
    forbidden = {
        "attempt_count",
        "dead_letter",
        "outbox",
        "idempotency_ledger",
        "checkpoint",
        "advance_checkpoint",
    }
    assert not (_identifiers() & forbidden)


def test_the_ledger_vocabulary_guard_still_bites() -> None:
    """The sensitivity proof: the detector fires on a name that is really there.

    `cursor` IS a parameter of the POLL handler, so a detector that could not
    see it would be reporting nothing about anything.
    """
    assert "cursor" in _identifiers()


def test_provider_size_limits_are_declared_once_and_configurable() -> None:
    """`media.py` is the only place a byte limit is written down.

    A limit copied into the wire path is a limit that will be changed in one
    place and not the other, and the disagreement surfaces as a provider
    rejection nobody can trace back to a number.
    """
    limits_module = _source("media.py")
    assert "DEFAULT_MEDIA_BYTE_LIMITS" in limits_module
    assert "MEDIA_LIMITS_SCHEMA" in limits_module
    for name in ("plugin.py", "catalogue.py", "wire.py"):
        assert "1024 * 1024" not in _source(name)


def _catalogue_read_precedes_the_send(source: str) -> bool:
    """Sub's ordering guard, ported.

    `test_team_inbox_boundaries.py` asserts
    `reply_source.index("list_approved_templates") < reply_source.index("nowait=True")`
    for the same reason in the other direction: an ordering that is only true by
    accident is one refactor from being false, and the failure is invisible —
    the message simply goes out unchecked.
    """
    gate = source.find("require_sendable_template(")
    send = source.find("/messages")
    return gate != -1 and send != -1 and gate < send


def test_a_template_send_consults_the_catalogue_before_it_reaches_the_provider() -> (
    None
):
    assert _catalogue_read_precedes_the_send(_source("plugin.py"))


def test_the_ordering_guard_still_bites() -> None:
    """The sensitivity proof.

    A positional check passes trivially when one of the two markers is absent
    or when the file is rearranged, so the detector is run against a source that
    violates the rule and must report it.
    """
    violating = (
        "client.post(f'/{version}/{phone}/messages')\n"
        "require_sendable_template(params)\n"
    )
    assert not _catalogue_read_precedes_the_send(violating)
    assert not _catalogue_read_precedes_the_send("no markers at all")
