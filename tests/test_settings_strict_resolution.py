"""Hosted PostgreSQL proof: scalar freshness within READ COMMITTED transactions.

This is transaction visibility and ORM-state evidence, not a linearizability
claim. The root admin_engine fixture skips when hosted PostgreSQL is absent.
"""

from uuid import uuid4

import pytest
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel.setting_scopes import SettingScope
from dotmac_kernel.setting_value_types import SettingValueType
from dotmac_kernel.settings_models import DomainSetting, SettingDomain
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified


@pytest.mark.parametrize("at_tenant", [False, True])
def test_committed_update_is_fresh_despite_preloaded_pending_orm_state(
    admin_engine, tenant_a, at_tenant
):
    spec = sr.SettingSpec(
        domain=SettingDomain.auth,
        key=f"strict_pg_{uuid4().hex}",
        value_type=SettingValueType.string,
        default="closed",
        allowed={"open", "closed"},
    )
    sr.register_specs([spec])
    scope = SettingScope.tenant(tenant_a.id) if at_tenant else SettingScope.platform()
    engine = admin_engine.execution_options(isolation_level="READ COMMITTED")
    try:
        with Session(engine) as writer:
            writer.add(
                DomainSetting(
                    domain=spec.domain,
                    key=spec.key,
                    value_type=spec.value_type,
                    value_text="open",
                    scope_kind=scope.kind,
                    tenant_id=scope.tenant_id,
                )
            )
            writer.commit()

        with Session(engine, autoflush=True) as reader:
            loaded = reader.scalars(
                select(DomainSetting).where(DomainSetting.key == spec.key)
            ).one()
            assert loaded.value_text == "open"
            assert sr.resolve_strict(reader, spec, scope=scope) == "open"

            with Session(engine) as writer:
                writer.execute(
                    update(DomainSetting)
                    .where(DomainSetting.key == spec.key)
                    .values(value_text="closed")
                )
                writer.commit()

            # No reader commit/rollback: the next statement's READ COMMITTED
            # snapshot sees the change; the loaded identity still does not.
            assert loaded.value_text == "open"
            loaded.value_text = "pending-secret-edit"
            assert sr.resolve_strict(reader, spec, scope=scope) == "closed"
            assert loaded in reader.dirty
            assert loaded.value_text == "pending-secret-edit"
            loaded.value_text = "open"
            flag_modified(loaded, "value_text")
            reader.flush()
            assert sr.resolve_strict(reader, spec, scope=scope) == "open"
            # Closing rolls back this deliberately uncommitted caller write.

        with Session(engine) as observer:
            assert sr.resolve_strict(observer, spec, scope=scope) == "closed"
    finally:
        with Session(engine) as cleanup:
            cleanup.execute(delete(DomainSetting).where(DomainSetting.key == spec.key))
            cleanup.commit()
        sr._REGISTRY.pop((spec.domain, spec.key), None)
