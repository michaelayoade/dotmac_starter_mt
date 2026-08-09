"""`inherits` — whether a less-specific scope may answer for a setting.

A fallback is the claim that a less-specific value is a valid answer to the
question. For a timezone, a format, a threshold, a toggle, it is: a value set
for the deployment is a real answer for a tenant that has not overridden it.

For a value that IDENTIFIES something belonging to one scope it is not. There is
no "default GL account", and inheriting one means posting to another tenant's
books. The resolver cannot tell the two apart; only the declaration can, which
is why this is a spec field rather than a resolver rule.

Found in `dotmac_erp`: `fx_revaluation` hand-writes an organisation-only query
to avoid exactly this, while `payment_service` reads a structurally identical
GL account id through the resolver and inherits the fallback. Same class of
data, opposite safety, decided by which author thought of it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel.setting_scopes import SettingScope
from dotmac_kernel.setting_value_types import SettingValueType
from dotmac_kernel.settings_models import SettingDomain

TENANT = uuid4()


def _spec(key: str, *, inherits: bool) -> sr.SettingSpec[str]:
    declared: sr.SettingSpec[str] = sr.SettingSpec(
        domain=SettingDomain.auth,
        key=key,
        value_type=SettingValueType.string,
        default="from-default",
        inherits=inherits,
    )
    sr.register_specs([declared])
    return declared


@pytest.fixture
def inheriting():
    spec = _spec(f"inh_{uuid4().hex[:8]}", inherits=True)
    yield spec
    sr._REGISTRY.pop((SettingDomain.auth, spec.key), None)


@pytest.fixture
def isolated():
    spec = _spec(f"iso_{uuid4().hex[:8]}", inherits=False)
    yield spec
    sr._REGISTRY.pop((SettingDomain.auth, spec.key), None)


def _platform_row(db, spec, value="from-platform"):
    sr.upsert_by_key(db, spec.domain, spec.key, value, scope=SettingScope.platform())
    db.flush()


def test_inheriting_is_the_default() -> None:
    """Nearly every setting wants a fallback, so the safe-by-omission choice is
    the common one — a spec that says nothing keeps today's behaviour."""
    spec: sr.SettingSpec[str] = sr.SettingSpec(
        domain=SettingDomain.auth,
        key="probe",
        value_type=SettingValueType.string,
        default="x",
    )
    assert spec.inherits is True


def test_an_inheriting_setting_falls_back_to_platform(db, inheriting):
    _platform_row(db, inheriting)
    value, source = sr.resolve_with_source(
        db, inheriting.domain, inheriting.key, tenant_id=TENANT
    )
    assert (value, source) == ("from-platform", "platform")


def test_a_non_inheriting_setting_does_NOT_see_the_platform_row(db, isolated):
    """The load-bearing test. A platform row exists and must not answer for a
    tenant — this is the GL-account case."""
    _platform_row(db, isolated)
    value, source = sr.resolve_with_source(
        db, isolated.domain, isolated.key, tenant_id=TENANT
    )
    assert (value, source) == ("from-default", "default")


def test_a_non_inheriting_setting_still_reads_its_own_scope(db, isolated):
    """Not inheriting must not mean not resolving."""
    sr.upsert_by_key(
        db, isolated.domain, isolated.key, "mine", scope=SettingScope.tenant(TENANT)
    )
    db.flush()
    value, source = sr.resolve_with_source(
        db, isolated.domain, isolated.key, tenant_id=TENANT
    )
    assert (value, source) == ("mine", "tenant")


def test_a_non_inheriting_setting_read_AT_platform_reads_the_platform_row(db, isolated):
    """Truncating the chain must not break the platform-level read itself —
    the row is at the scope being asked about."""
    _platform_row(db, isolated)
    value, source = sr.resolve_with_source(
        db, isolated.domain, isolated.key, tenant_id=None
    )
    assert (value, source) == ("from-platform", "platform")


def test_bulk_and_single_key_agree_for_a_non_inheriting_setting(db, isolated):
    """The drift `_finish` is shared to prevent, one layer up. A settings screen
    showing an inherited GL account that no individual read would return is
    worse than either behaviour on its own."""
    _platform_row(db, isolated)
    single, _ = sr.resolve_with_source(
        db, isolated.domain, isolated.key, tenant_id=TENANT
    )
    bulk = sr.resolve_many(db, isolated.domain, (isolated.key,), tenant_id=TENANT)
    assert bulk[isolated.key] == single == "from-default"


def test_bulk_and_single_key_agree_for_an_inheriting_setting(db, inheriting):
    """The other half — truncating the chain must not have broken inheritance
    for everyone else."""
    _platform_row(db, inheriting)
    single, _ = sr.resolve_with_source(
        db, inheriting.domain, inheriting.key, tenant_id=TENANT
    )
    bulk = sr.resolve_many(db, inheriting.domain, (inheriting.key,), tenant_id=TENANT)
    assert bulk[inheriting.key] == single == "from-platform"


def test_bulk_resolves_a_mixed_set_correctly(db, inheriting, isolated):
    """The realistic case: one screen, both kinds, one query pass."""
    _platform_row(db, inheriting, "inherited")
    _platform_row(db, isolated, "not-inherited")
    resolved = sr.resolve_many(
        db, SettingDomain.auth, (inheriting.key, isolated.key), tenant_id=TENANT
    )
    assert resolved[inheriting.key] == "inherited"
    assert resolved[isolated.key] == "from-default"


def test_inherits_is_part_of_a_spec_identity(db, isolated):
    """Two declarations of one key differing only in `inherits` are a genuine
    conflict, not a harmless re-import — they resolve differently."""
    conflicting: sr.SettingSpec[str] = sr.SettingSpec(
        domain=isolated.domain,
        key=isolated.key,
        value_type=SettingValueType.string,
        default="from-default",
        inherits=True,
    )
    with pytest.raises(sr.DuplicateSettingSpecError):
        sr.register_specs([conflicting])
