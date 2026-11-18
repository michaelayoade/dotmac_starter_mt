"""`stored_at` — what is persisted at one scope, which is not what resolves.

An editor and a reader ask different questions. Building a settings screen on
`resolve_value` produces two specific bugs, and both are tested here:

* an inherited value shown in an edit box becomes an accidental override on
  save, with nothing on screen to warn the operator;
* a stored value that fails its spec degrades to the default during resolution,
  so the screen shows something healthy while the bad row persists —
  unshowable and therefore unfixable.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel.setting_scopes import SettingScope
from dotmac_kernel.setting_value_types import SettingValueType
from dotmac_kernel.settings_models import DomainSetting, SettingDomain

TENANT = uuid4()


@pytest.fixture
def spec():
    declared: sr.SettingSpec[str] = sr.SettingSpec(
        domain=SettingDomain.auth,
        key=f"stored_{uuid4().hex[:8]}",
        value_type=SettingValueType.string,
        default="from-default",
        allowed={"from-default", "open", "closed"},
    )
    sr.register_specs([declared])
    yield declared
    sr._REGISTRY.pop((SettingDomain.auth, declared.key), None)


def test_no_row_at_the_scope_returns_none(db, spec):
    """`None` means "no override here" — the thing an editor must distinguish
    from "the inherited value happens to equal this"."""
    assert stored(db, spec, SettingScope.tenant(TENANT)) is None


def stored(db, spec, scope):
    return sr.stored_at(db, spec.domain, spec.key, scope=scope)


def test_it_never_walks_the_chain(db, spec):
    """The bug this prevents: a platform row must not make a tenant look
    overridden, or saving the form writes an override nobody asked for."""
    sr.upsert_by_key(db, spec.domain, spec.key, "open", scope=SettingScope.platform())
    db.flush()

    assert (
        stored(db, spec, SettingScope.tenant(TENANT)) is None
    ), "a platform row leaked into the tenant's stored view"
    at_platform = stored(db, spec, SettingScope.platform())
    assert at_platform is not None and at_platform.raw == "open"


def test_a_row_at_the_scope_is_returned_with_its_raw_value(db, spec):
    sr.upsert_by_key(
        db, spec.domain, spec.key, "closed", scope=SettingScope.tenant(TENANT)
    )
    db.flush()
    found = stored(db, spec, SettingScope.tenant(TENANT))
    assert found is not None
    assert (found.raw, found.valid, found.error) == ("closed", True, None)
    assert found.scope_kind == "tenant"


def test_an_invalid_stored_value_is_visible_and_explained(db, spec):
    """The load-bearing test. Resolution degrades this row to the default and
    reports source="default", so a screen built on resolution cannot show it.

    Planted directly because the WRITE path now refuses a value the spec
    rejects — which is exactly why such rows are legacy or hand-edited.
    """
    db.add(
        DomainSetting(
            tenant_id=TENANT,
            scope_kind="tenant",
            domain=spec.domain,
            key=spec.key,
            value_type=SettingValueType("string"),
            value_text="not-in-the-allowed-set",
        )
    )
    db.flush()

    # Resolution hides it.
    value, source = sr.resolve_with_source(db, spec.domain, spec.key, tenant_id=TENANT)
    assert (value, source) == ("from-default", "default")

    # `stored_at` shows it, and says why.
    found = stored(db, spec, SettingScope.tenant(TENANT))
    assert found is not None
    assert found.raw == "not-in-the-allowed-set"
    assert found.valid is False
    assert found.error and "not one of" in found.error


def test_validity_is_judged_by_the_same_rules_resolution_uses(db, spec):
    """One implementation. An admin screen and the resolver disagreeing about
    whether a value is usable would be worse than either answer alone."""
    good, error = sr._check_against_spec(spec, "open")
    assert (good, error) == ("open", None)
    bad, error = sr._check_against_spec(spec, "nonsense")
    assert bad is None and error is not None


def test_a_secret_never_returns_its_stored_value(db):
    """A settings screen must not echo a credential. Returning a
    `StoredSetting` at all already answers the form's only question — whether a
    value is set."""
    secret: sr.SettingSpec[str] = sr.SettingSpec(
        domain=SettingDomain.auth,
        key=f"secret_{uuid4().hex[:8]}",
        value_type=SettingValueType.string,
        default="",
        is_secret=True,
    )
    sr.register_specs([secret])
    try:
        db.add(
            DomainSetting(
                tenant_id=TENANT,
                scope_kind="tenant",
                domain=secret.domain,
                key=secret.key,
                value_type=SettingValueType("string"),
                value_text="hunter2-the-actual-credential",
                is_secret=True,
            )
        )
        db.flush()
        found = stored(db, secret, SettingScope.tenant(TENANT))
        assert found is not None, "the form still needs to know a value is set"
        assert found.raw is None
        assert found.redacted is True
        assert "hunter2" not in repr(found)
    finally:
        sr._REGISTRY.pop((SettingDomain.auth, secret.key), None)


def test_a_row_whose_spec_is_gone_is_still_visible(db):
    """A retired setting, or one belonging to a module this deployment no
    longer installs. It is precisely the row an operator may want to delete, so
    hiding it would strand it."""
    orphan_key = f"orphan_{uuid4().hex[:8]}"
    db.add(
        DomainSetting(
            tenant_id=TENANT,
            scope_kind="tenant",
            domain=SettingDomain.auth,
            key=orphan_key,
            value_type=SettingValueType("string"),
            value_text="left-behind",
        )
    )
    db.flush()

    found = sr.stored_at(
        db, SettingDomain.auth, orphan_key, scope=SettingScope.tenant(TENANT)
    )
    assert found is not None
    assert found.raw == "left-behind"
    assert found.valid is False
    assert found.error and "no registered spec" in found.error
