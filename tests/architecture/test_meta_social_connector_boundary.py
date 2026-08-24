"""Meta Social is a stateless provider edge, never a product runtime."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import dotmac_connector_meta_social
from dotmac_connector_meta_social import MANIFEST

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = PROJECT_ROOT / "packages" / "dotmac-connector-meta-social"
SOURCE = PACKAGE / "src" / "dotmac_connector_meta_social"


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
    assert version == dotmac_connector_meta_social.__version__ == MANIFEST.version


def test_connector_imports_only_the_spi_among_dotmac_packages() -> None:
    internal = {name for name in _imports() if name.startswith("dotmac_")}
    assert "dotmac_connector_meta_social" in internal
    assert internal - {"dotmac_connector_meta_social"} == {"dotmac_integration"}


def test_connector_has_no_persistence_or_execution_dependency() -> None:
    """`httpx` left this set when the DELIVERY slice landed, and only `httpx`.

    The delivery half exists to make one provider call, so a network client is
    the point rather than a violation. What must stay absent is anything that
    would let this package keep state or schedule itself — the engine owns the
    outbox, the retry loop and every row.
    """
    forbidden = {
        "alembic",
        "asyncpg",
        "celery",
        "psycopg",
        "requests",
        "sqlalchemy",
        "tenacity",
        "urllib3",
    }
    assert not (_imports() & forbidden)


def test_ingress_handler_performs_no_network_io() -> None:
    """The two halves share a file; they must not share a network posture.

    Verification and normalization run on the provider's callback thread with
    the request open. A Graph call from inside either one would put provider
    latency between a signed request and its acknowledgement, which is how a
    verified batch turns into a provider redelivery storm.
    """
    tree = ast.parse((SOURCE / "plugin.py").read_text(encoding="utf-8"))
    ingress = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MetaSocialIngressHandler"
    )
    rendered = ast.unparse(ingress)
    assert "httpx" not in rendered
    assert ".post(" not in rendered


def test_the_delivery_half_never_decides_whether_a_reply_is_allowed() -> None:
    """The boundary this slice is most likely to be walked across.

    Sub owns the messaging-window rule and the permission to respond. If that
    arithmetic were ever copied in here, the connector would start refusing
    sends the product had already authorized — two authorities, one of them
    stale. The absence of any clock arithmetic and of a window vocabulary is
    what makes "the connector performs the wire operation only" checkable.
    """
    source = (SOURCE / "plugin.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    delivery = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MetaSocialDeliveryHandler"
    )
    rendered = ast.unparse(delivery)
    for forbidden in ("timedelta", "now(", "utcnow", "hours", "window"):
        assert forbidden not in rendered, forbidden
    # `timedelta` must not reach the module at all: a window rule needs a
    # duration, and there is no other reason for this package to hold one.
    assert "timedelta" not in source


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
    assert "dotmac_connector_meta_social" not in assembly
    assert "dotmac-connector-meta-social" not in runtime_dependencies
    assert "dotmac-connector-meta-social" in dev_dependencies
