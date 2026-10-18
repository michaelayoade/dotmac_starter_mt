"""Verification-only successors preserve the Kernel's tenant audit estate."""

from importlib import import_module

import pytest
from dotmac_kernel.prerequisites import (
    TENANT_AUDIT_LOG_V1,
    install_prerequisite_bindings,
    installed_bindings,
)


@pytest.fixture(autouse=True)
def _assembly_bindings():
    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    previous = tuple(installed_bindings())
    install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
    try:
        yield
    finally:
        install_prerequisite_bindings(previous)


@pytest.mark.parametrize(
    ("package", "revision", "parent"),
    (
        ("dotmac_learning", "lr_0005_tenant_audit", "lr_0004_assessment"),
        ("dotmac_fulfillment", "fu_0002_tenant_audit", "fu_0001_fulfillment"),
        (
            "dotmac_template_studio",
            "ts_0003_tenant_audit",
            "ts_0002_notify_identity",
        ),
    ),
)
def test_tenant_audit_successor_refuses_before_ddl_and_never_mutates_provider(
    monkeypatch, package, revision, parent
):
    migration = import_module(f"{package}.migrations.versions.{revision}")
    assert migration.revision == revision
    assert migration.down_revision == parent
    assert migration.branch_labels is None
    assert migration.REQUIRES == (TENANT_AUDIT_LOG_V1.name,)
    connection = object()
    observed = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

    def forbidden(*args, **kwargs):
        pytest.fail("tenant audit verifier must not execute DDL")

    for operation in (
        "create_table",
        "drop_table",
        "create_index",
        "drop_index",
        "execute",
        "add_column",
        "drop_column",
        "alter_column",
        "create_foreign_key",
    ):
        monkeypatch.setattr(migration.op, operation, forbidden)

    def refuse(bind, requires):
        assert bind is connection
        assert requires == migration.REQUIRES
        raise RuntimeError("provider catalog refused")

    monkeypatch.setattr(migration, "require_prerequisites", refuse)
    with pytest.raises(RuntimeError, match="provider catalog refused"):
        migration.upgrade()

    monkeypatch.setattr(
        migration, "require_prerequisites", lambda *args: observed.append(args)
    )
    migration.upgrade()
    migration.downgrade()
    assert observed == [(connection, migration.REQUIRES)]
