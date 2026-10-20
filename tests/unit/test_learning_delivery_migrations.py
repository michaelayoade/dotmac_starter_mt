"""Migration guards; requires the published tenant-audit Kernel contract."""

from importlib import import_module

import pytest
from dotmac_kernel.prerequisites import (
    install_prerequisite_bindings,
    installed_bindings,
)

PREFIX = "dotmac_learning.migrations.versions."


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
    ("name", "parent"),
    (
        ("lr_0005_tenant_audit", "lr_0004_assessment"),
        ("lr_0006_delivery_support", "lr_0005_tenant_audit"),
        ("lr_0007_enrollment_state_policy", "lr_0006_delivery_support"),
    ),
)
def test_lineage_and_pre_ddl_prerequisite_refusal(monkeypatch, name, parent):
    migration = import_module(PREFIX + name)
    assert migration.revision == name
    assert migration.down_revision == parent
    assert migration.branch_labels is None
    observed = []
    bind = object()
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)

    def refuse(connection, names):
        assert connection is bind
        observed.append(names)
        raise RuntimeError("provider catalog refused")

    def forbidden(*args, **kwargs):
        pytest.fail("DDL ran before prerequisite verification")

    monkeypatch.setattr(migration, "require_prerequisites", refuse)
    for operation in (
        "create_table",
        "create_index",
        "execute",
        "add_column",
        "alter_column",
    ):
        monkeypatch.setattr(migration.op, operation, forbidden)
    with pytest.raises(RuntimeError, match="provider catalog refused"):
        migration.upgrade()
    assert observed == [migration.REQUIRES]


def test_audit_revision_only_verifies_and_downgrade_leaves_provider(monkeypatch):
    migration = import_module(PREFIX + "lr_0005_tenant_audit")
    from dotmac_kernel.prerequisites import TENANT_AUDIT_LOG_V1

    assert migration.REQUIRES == (TENANT_AUDIT_LOG_V1.name,)
    observed = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: "connection")
    monkeypatch.setattr(
        migration, "require_prerequisites", lambda *args: observed.append(args)
    )

    def forbidden(*args, **kwargs):
        pytest.fail("verification revision must not mutate provider storage")

    for operation in ("create_table", "drop_table", "create_index", "execute"):
        monkeypatch.setattr(migration.op, operation, forbidden)
    migration.upgrade()
    migration.downgrade()
    assert observed == [("connection", migration.REQUIRES)]
