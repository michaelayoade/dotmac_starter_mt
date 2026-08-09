"""Profile defaults — the deployment's answer of last resort.

A module declares the QUESTION: that a setting exists, its type, its
constraints, whether it inherits. A deployment declares the ANSWER when nothing
else supplies one, because that is what genuinely varies by region, regime and
topology — and it is otherwise hardcoded in module code where a deployment
cannot reach it.

The direction is the whole point:

    scope chain  ->  profile default  ->  spec fallback

A profile value LOSES to every stored row. The inverse is the defect ADR-0011
removed from `env_var`: a deployment-level value beating an operator's stored
row makes the settings screen lie about what is in effect.

`dotmac_erp` is the worked example. `refresh_cookie_samesite` has a spec default
of "lax" and a caller fallback of "strict", and neither is a deployment decision
anyone made — two answers, and which one runs depends on the code path.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel.setting_scopes import SettingScope
from dotmac_kernel.setting_value_types import SettingValueType
from dotmac_kernel.settings_models import SettingDomain

TENANT = uuid4()


@pytest.fixture
def spec():
    declared: sr.SettingSpec[str] = sr.SettingSpec(
        domain=SettingDomain.auth,
        key=f"profile_{uuid4().hex[:8]}",
        value_type=SettingValueType.string,
        default="module-fallback",
        allowed={"module-fallback", "deployment-intent", "operator-choice"},
    )
    sr.register_specs([declared])
    yield declared
    sr._REGISTRY.pop((SettingDomain.auth, declared.key), None)
    sr.install_setting_defaults({})


def _declare(spec, value):
    sr.install_setting_defaults({f"{spec.domain}/{spec.key}": value})


def test_with_no_profile_default_the_spec_fallback_answers(db, spec):
    value, source = sr.resolve_with_source(db, spec.domain, spec.key, tenant_id=None)
    assert (value, source) == ("module-fallback", "default")


def test_a_profile_default_beats_the_spec_fallback(db, spec):
    """What a deployment is for: stating intent the module cannot know."""
    _declare(spec, "deployment-intent")
    value, source = sr.resolve_with_source(db, spec.domain, spec.key, tenant_id=None)
    assert (value, source) == ("deployment-intent", "profile")


def test_a_stored_row_beats_the_profile_default(db, spec):
    """The load-bearing direction. An operator's stored value must win, or the
    settings screen lies about what is in effect — the ADR-0011 defect."""
    _declare(spec, "deployment-intent")
    sr.upsert_by_key(
        db, spec.domain, spec.key, "operator-choice", scope=SettingScope.platform()
    )
    db.flush()
    value, source = sr.resolve_with_source(db, spec.domain, spec.key, tenant_id=None)
    assert (value, source) == ("operator-choice", "platform")


def test_provenance_names_the_profile(db, spec):
    """A settings screen must be able to say WHERE a value came from. "the
    deployment declared this" and "the module fell back" are different answers
    for an operator deciding whether to override."""
    _declare(spec, "deployment-intent")
    _, source = sr.resolve_with_source(db, spec.domain, spec.key, tenant_id=None)
    assert source == "profile"


def test_bulk_and_single_key_agree(db, spec):
    """Both paths reach the shared `_finish`, which is why they cannot drift."""
    _declare(spec, "deployment-intent")
    single, _ = sr.resolve_with_source(db, spec.domain, spec.key, tenant_id=None)
    bulk = sr.resolve_many(db, spec.domain, (spec.key,), tenant_id=None)
    assert bulk[spec.key] == single == "deployment-intent"


def test_a_falsy_profile_default_is_still_a_declaration(db):
    """`0`, `False` and `""` are real answers. Distinguishing "declared as
    falsy" from "not declared" is why the lookup reports a flag rather than
    testing the value against None."""
    declared: sr.SettingSpec[int] = sr.SettingSpec(
        domain=SettingDomain.auth,
        key=f"zero_{uuid4().hex[:8]}",
        value_type=SettingValueType.integer,
        default=99,
    )
    sr.register_specs([declared])
    try:
        _declare(declared, 0)
        value, source = sr.resolve_with_source(
            db, declared.domain, declared.key, tenant_id=None
        )
        assert (value, source) == (0, "profile")
    finally:
        sr._REGISTRY.pop((SettingDomain.auth, declared.key), None)
        sr.install_setting_defaults({})


def test_installed_defaults_are_readable_but_not_mutable(spec):
    _declare(spec, "deployment-intent")
    view = sr.active_setting_defaults()
    assert view[f"{spec.domain}/{spec.key}"] == "deployment-intent"
    with pytest.raises(TypeError):
        view["auth/anything"] = "no"  # type: ignore[index]


def test_a_default_for_an_undeclared_key_fails_the_boot() -> None:
    """A profile supplies ANSWERS; it cannot introduce a question. Otherwise a
    deployment grows settings no module reads — the orphan the starter's
    no-orphan-settings rule exists to prevent."""
    from dotmac_kernel.app_factory import _install_profile_defaults

    with pytest.raises(ValueError, match="no installed module declares"):
        _install_profile_defaults({"auth/not_a_real_setting": "x"})


def test_a_default_its_own_spec_rejects_fails_the_boot(spec) -> None:
    """Silently ignoring it would leave the settings screen showing a value
    nothing resolves to."""
    from dotmac_kernel.app_factory import _install_profile_defaults

    with pytest.raises(ValueError, match="rejected by"):
        _install_profile_defaults({f"{spec.domain}/{spec.key}": "not-in-allowed"})


def test_a_malformed_key_fails_the_boot() -> None:
    from dotmac_kernel.app_factory import _install_profile_defaults

    with pytest.raises(ValueError, match="must be keyed"):
        _install_profile_defaults({"missing_the_separator": "x"})
