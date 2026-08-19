"""Caller-session kernel services must not construct a second database runtime.

Applications are independent assemblies (ADR-0024): each owns its engine,
sessions and transaction boundaries.  Consent, delivery, idempotency and
external-identity services all receive a ``Session`` from that assembly.  If
one of those services imports ``dotmac_kernel.db`` merely to open a SAVEPOINT,
the import constructs the kernel engines anyway and an adopter such as Sub ends
up with two runtime authorities pointed at the same database.

The subprocess proof uses a deliberately unparsable kernel ``DATABASE_URL``
and a perfectly usable caller-owned SQLite session.  The operations must work
without importing ``dotmac_kernel.db`` at all.  The AST guard covers deferred
function-local imports too, and its sabotage check keeps the detector live.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

KERNEL_ROOT = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "dotmac-kernel"
    / "src"
    / "dotmac_kernel"
)

CALLER_SESSION_SERVICES = (
    "consent.py",
    "delivery.py",
    "external_identity.py",
    "idempotency.py",
)


def _db_import_lines(source: str) -> tuple[int, ...]:
    tree = ast.parse(source)
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "dotmac_kernel.db":
            lines.append(node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "dotmac_kernel.db":
                    lines.append(node.lineno)
    return tuple(sorted(lines))


def test_caller_session_services_never_enter_the_kernel_engine_owner() -> None:
    violations: list[str] = []
    for relative in CALLER_SESSION_SERVICES:
        path = KERNEL_ROOT / relative
        for line in _db_import_lines(path.read_text()):
            violations.append(f"{relative}:{line}")

    assert not violations, (
        "services that receive an assembly-owned Session imported "
        "dotmac_kernel.db and therefore constructed a second engine/session "
        "authority: "
        + ", ".join(violations)
    )


def test_the_import_guard_detects_a_deferred_db_import() -> None:
    probe = "def write(db):\n    from dotmac_kernel.db import conflict_savepoint\n"
    assert _db_import_lines(probe) == (2,)


_CALLER_SESSION_PROBE = r"""
import sys
from uuid import uuid4

from sqlalchemy.orm import Session

from dotmac_kernel import consent
from dotmac_kernel.consent_models import CommunicationSuppression
from dotmac_kernel.delivery import record_receipt
from dotmac_kernel.delivery_models import CommunicationDelivery, DELIVERY_ACCEPTED
from dotmac_kernel.idempotency import execute_once
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.models import Tenant
from dotmac_kernel.testing import create_test_engine

engine = create_test_engine(
    tables=(
        Tenant.__table__,
        CommunicationSuppression.__table__,
        CommunicationDelivery.__table__,
        IdempotencyRecord.__table__,
    )
)
try:
    with Session(engine, autoflush=False) as db:
        tenant = Tenant(id=uuid4(), slug="caller", name="Caller")
        db.add(tenant)
        db.flush()

        consent.register_marketing_categories("campaign")
        consent.suppress(
            db,
            tenant.id,
            channel="email",
            address="blocked@example.com",
        )
        assert not consent.may_send(
            db,
            tenant.id,
            channel="email",
            address="blocked@example.com",
            category="campaign",
        )

        outcome = execute_once(
            db,
            tenant_id=tenant.id,
            scope="campaign.delivery",
            key="recipient-step-1",
            operation=lambda _db: {"intent_id": "intent-1"},
            fingerprint="fingerprint-1",
        )
        assert outcome.replayed is False

        receipt = record_receipt(
            db,
            tenant.id,
            channel="email",
            address="sent@example.com",
            provider="fake",
            status=DELIVERY_ACCEPTED,
            provider_message_id="message-1",
        )
        assert receipt.provider_message_id == "message-1"
        assert "dotmac_kernel.db" not in sys.modules
finally:
    engine.dispose()
"""


def test_services_use_only_the_callers_session_and_database_runtime() -> None:
    env = dict(os.environ)
    env["DATABASE_URL"] = "not-a-parseable-database-url"
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _CALLER_SESSION_PROBE],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, (
        "caller-session services entered the kernel database runtime instead "
        "of using only their supplied Session:\n"
        f"{result.stderr}"
    )
