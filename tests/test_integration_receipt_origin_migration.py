"""Hosted proofs against the real ig_0015 predecessor and guarded downgrade."""

from __future__ import annotations

import os

import pytest
from dotmac_integration import InboxReceipt
from dotmac_integration.execution import receipt_verification_origin
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.test_integration_isolation import (  # noqa: F401
    INTEGRATION_VERSIONS,
    KERNEL_VERSIONS,
    REPO_ROOT,
    _installed_capability_registry,
    migrated_scratch,
)
from tests.test_integration_receipt_delivery_isolation import _receipt


def _config(admin_url: str):
    from alembic.config import Config

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    config.set_main_option(
        "version_locations", f"{KERNEL_VERSIONS} {INTEGRATION_VERSIONS}"
    )
    os.environ["MIGRATION_DATABASE_URL"] = admin_url
    return config


def test_real_predecessor_legacy_row_stays_unknown_and_can_roundtrip(
    migrated_scratch: tuple[str, str],  # noqa: F811
    request: pytest.FixtureRequest,
) -> None:
    from alembic import command

    admin_url, _ = migrated_scratch
    config = _config(admin_url)
    # This module gets its own scratch fixture. No original-context receipts
    # exist, so the real downgrade reaches the actual published predecessor.
    command.downgrade(config, "ig_0015_descriptor_contract")
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        assert (
            conn.scalar(
                text("""
            SELECT count(*) FROM information_schema.columns
            WHERE table_schema='mod_intg' AND table_name='inbox_receipts'
              AND column_name LIKE 'verification_%'
        """)
            )
            == 0
        )
        # Raw historical fixture deliberately uses ONLY the predecessor schema.
        legacy_id = _receipt(conn, request)
    command.upgrade(config, "heads")
    with Session(engine) as db:
        assert receipt_verification_origin(db.get(InboxReceipt, legacy_id)) is None
    # Unknown historical rows do not prevent a lossless downgrade/upgrade.
    command.downgrade(config, "ig_0015_descriptor_contract")
    command.upgrade(config, "heads")
    with Session(engine) as db:
        assert receipt_verification_origin(db.get(InboxReceipt, legacy_id)) is None
    engine.dispose()
    _assert_downgrade_protects_origin(admin_url)


def _assert_downgrade_protects_origin(admin_url: str) -> None:
    import uuid

    # Build uses the real lifecycle audit declaration just as production does.
    from dotmac_integration import InboundEvent, module
    from dotmac_integration.ingress import prepare_ingress, record_batch
    from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions

    from alembic import command
    from tests.unit.test_integration_ingress import (
        IngressFake,
        address,
        build,
        registry_for,
    )

    registry = registry_for(IngressFake())
    install_audit_actions(AuditActionRegistry.from_manifests([module]))
    # Explicit capability registry fixture is requested below by autouse import;
    # keep setup against the actual owners, not a copied receipt INSERT.
    engine = create_engine(admin_url)
    with Session(engine) as db, db.begin():
        _, _, key = build(db, registry)
        prepared = prepare_ingress(db, endpoint=address(key), registry=registry)
        ((receipt_id, _),) = record_batch(
            db,
            prepared,
            (
                InboundEvent(
                    provider_event_id=uuid.uuid4().hex,
                    event_type="received",
                    payload={},
                ),
            ),
        )
    with pytest.raises(
        DBAPIError, match="cannot erase recorded receipt verification origin"
    ):
        command.downgrade(_config(admin_url), "ig_0015_descriptor_contract")
    with Session(engine) as db:
        assert (
            receipt_verification_origin(db.get(InboxReceipt, receipt_id))
            == prepared.verification_origin
        )
    engine.dispose()
