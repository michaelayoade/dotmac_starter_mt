"""Authored hosted-only genuine whole-graph replay; UNEXECUTED locally.

CI provisions a separate EMPTY PostgreSQL database and the canonical cluster
roles. Actual installed Kernel and Learning migration bytes execute unchanged.
The synthetic host lineage has a real unapplied successor, not a stamped head.
No historical credential/session population is read or emitted in this test.
"""

import hashlib
import os
from pathlib import Path

import sqlalchemy as sa
from dotmac_kernel.migrations import identity_history as history
from dotmac_kernel.migrations import versions_dir
from dotmac_kernel.modules import ModuleRegistry
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    PARTY_PERSON_CATALOG_V1,
    TENANT_AUDIT_LOG_V1,
    TENANT_SCOPE_CATALOG_V1,
    PrerequisiteBinding,
    install_prerequisite_bindings,
    installed_bindings,
)
from sqlalchemy.engine import make_url

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_real_installed_kernel_learning_and_partial_assembly_replay(
    tmp_path,
    record_property,
):
    from dotmac_learning.manifest import module
    from dotmac_learning.migrations import versions_dir as learning_versions

    assert os.environ.get("CI") == "true", "whole-graph replay is hosted CI only"
    url = os.environ.get("IDENTITY_GRAPH_DATABASE_URL")
    assert url, "CI must provision a separate empty IDENTITY_GRAPH_DATABASE_URL"
    target = make_url(url)
    assert target.drivername == "postgresql+psycopg"
    for variable in (
        "TEST_DATABASE_URL",
        "TEST_MIGRATION_DATABASE_URL",
        "MIGRATION_DATABASE_URL",
    ):
        other = os.environ.get(variable)
        if other:
            assert target.database != make_url(other).database
    assert set(module.requires) == {
        TENANT_SCOPE_CATALOG_V1.name,
        MODULE_DATABASE_ROLES_V1.name,
        PARTY_PERSON_CATALOG_V1.name,
        TENANT_AUDIT_LOG_V1.name,
    }
    # This containing Kernel head supplies all four effects through genuine
    # ancestry, including 106's audit effect and the new credential tenant key.
    head = "0030_credential_tenant_key"
    bindings = tuple(
        PrerequisiteBinding(name, head, "kernel") for name in module.requires
    )
    registry = ModuleRegistry((module,))
    locations = {"learning": learning_versions()}
    assembly = tmp_path / "whole_assembly"
    assembly.mkdir()
    for revision, down, branch, dependency in (
        ("a001_identity_extensions", None, ("assembly",), head),
        ("a002_later", "a001_identity_extensions", None, None),
    ):
        (assembly / (revision + ".py")).write_text(
            f"revision={revision!r}\ndown_revision={down!r}\n"
            f"branch_labels={branch!r}\ndepends_on={dependency!r}\n"
            "def upgrade():\n    pass\ndef downgrade():\n    pass\n"
        )
    env = tmp_path / "environment"
    env.mkdir()
    (env / "env.py").write_text(
        "from alembic import context\n"
        "config=context.config\n"
        "context.configure(connection=config.attributes['connection'],"
        "target_metadata=None,version_table_schema='public',transactional_ddl=True,"
        "on_version_apply=config.attributes['on_version_apply'])\n"
        "with context.begin_transaction():\n    context.run_migrations()\n"
    )
    previous = installed_bindings()
    engine = sa.create_engine(url, hide_parameters=True, echo=False)
    try:
        # The trusted test environment installs once BEFORE source graph loads;
        # the importer only verifies these same explicit bindings.
        install_prerequisite_bindings(bindings)
        with engine.connect() as connection:
            with connection.begin():
                assert connection.scalar(sa.text("SELECT current_user")) == "app_admin"
                assert (
                    connection.scalar(
                        sa.text(
                            "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                            "ON n.oid=c.relnamespace WHERE n.nspname='public' "
                            "AND c.relkind IN ('r','p','v','m','S','f')"
                        )
                    )
                    == 0
                )
                cfg = Config()
                cfg.set_main_option("script_location", str(env))
                cfg.set_main_option(
                    "version_locations",
                    " ".join(
                        map(str, (versions_dir(), assembly, locations["learning"]))
                    ),
                )
                cfg.attributes["connection"] = connection
                applied = []
                scripts = ScriptDirectory.from_config(cfg)
                assert scripts.get_revision(head).revision == head
                assert (
                    scripts.get_revision("lr_0006_delivery_support").revision
                    == "lr_0006_delivery_support"
                )

                def observe(*, ctx, step, heads, run_args):
                    for revision in step.up_revision_ids:
                        path = Path(scripts.get_revision(revision).path)
                        applied.append(
                            (revision, hashlib.sha256(path.read_bytes()).hexdigest())
                        )

                cfg.attributes["on_version_apply"] = observe
                command.upgrade(cfg, "a001_identity_extensions")
                command.upgrade(cfg, "lr_0006_delivery_support")
                expected_applied = {
                    script.revision for script in scripts.walk_revisions()
                } - {"a002_later"}
                assert {revision for revision, _ in applied} == expected_applied
                assert len(applied) == len(expected_applied)
                assert (
                    connection.scalar(
                        sa.text(
                            "SELECT to_regclass('mod_learning.class_sessions') "
                            "IS NOT NULL"
                        )
                    )
                    is True
                )
                history._installed_kernel_coverage(
                    connection, head, assembly, registry, locations, bindings
                )
                command.upgrade(cfg, "a002_later")
                history._installed_kernel_coverage(
                    connection, head, assembly, registry, locations, bindings
                )
                assert applied[-1][0] == "a002_later"
                assert installed_bindings() == tuple(
                    sorted(bindings, key=lambda b: b.prerequisite)
                )
                record_property(
                    "genuine_executed_revisions",
                    ";".join(revision + ":" + digest for revision, digest in applied),
                )
                # Entire disposable replay rolls back; no stamp or version-row
                # edits, no source substitution and no separate commit authority.
                connection.get_transaction().rollback()
    finally:
        install_prerequisite_bindings(previous)
        engine.dispose()
