"""ADR-0011 (runtime half): resolution reads rows and defaults. Nothing else.

The environment is made to raise, then real settings — including one whose spec
declares an `env_var`, with that variable set to a different value — are
resolved. Any read of the environment on the resolution path becomes a failure
here rather than a stored row being silently outranked in production.

This is the `os.environ` analogue of `test_secrets_no_network.py`'s socket
patch, and it exists for the same reason: the behaviour has been correct since
`0.1.0a19`, but nothing stopped it being undone. A property that is merely true
erodes; one that is tested holds.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel.setting_scopes import SettingScope
from dotmac_kernel.setting_value_types import SettingValueType
from dotmac_kernel.settings_models import SettingDomain

ENV_VAR = "DOTMAC_ADR0011_PROBE"


class _EnvironmentRead(AssertionError):
    """Resolution touched the environment."""


@pytest.fixture
def no_env(monkeypatch):
    """Make any environment read fail loudly for the duration of a test.

    Request this AFTER any fixture that sets an environment variable —
    `monkeypatch.setenv` reads `os.environ.get` to record the prior value, so a
    fixture ordered before this one would trip the patch during setup.
    """

    def _forbidden(*args, **kwargs):
        raise _EnvironmentRead(
            "settings resolution read the environment — a stored row is the "
            "authority and env is a bootstrap input only (ADR-0011)"
        )

    monkeypatch.setattr(os.environ, "get", _forbidden)
    monkeypatch.setattr(os, "getenv", _forbidden)


@pytest.fixture
def spec(monkeypatch):
    """A spec that DECLARES an env_var, with the variable set to a value that
    differs from both the row and the default — so any env read is visible in
    the result, not just in the patch."""
    monkeypatch.setenv(ENV_VAR, "from-environment")
    key = f"adr0011_{uuid4().hex[:8]}"
    declared: sr.SettingSpec[str] = sr.SettingSpec(
        domain=SettingDomain.auth,
        key=key,
        value_type=SettingValueType.string,
        default="from-default",
        env_var=ENV_VAR,
    )
    sr.register_specs([declared])
    yield declared
    sr._REGISTRY.pop((SettingDomain.auth, key), None)


def test_the_no_env_fixture_actually_fires(no_env):
    """Sensitivity proof: without this, every test below could pass because the
    patch does nothing rather than because the rule holds."""
    with pytest.raises(_EnvironmentRead):
        os.environ.get("ANYTHING")
    with pytest.raises(_EnvironmentRead):
        os.getenv("ANYTHING")


def test_resolution_does_not_read_the_environment(db, spec, no_env):
    """The load-bearing test: a spec with a declared env_var, resolved with the
    environment unreadable."""
    value, source = sr.resolve_with_source(db, spec.domain, spec.key, tenant_id=None)
    assert (value, source) == ("from-default", "default")


def test_bulk_resolution_does_not_read_the_environment(db, spec, no_env):
    """`resolve_many` is a separate path; a reintroduced env read could hide in
    the one the single-key test does not exercise."""
    resolved = sr.resolve_many(db, spec.domain, (spec.key,), tenant_id=None)
    assert resolved == {spec.key: "from-default"}


def test_a_stored_row_is_not_outranked_by_the_environment(db, spec):
    """Stated without the patch, because this is the BEHAVIOUR the rule exists
    for: the variable is set and the row still wins. Under the pre-a19 kernel
    this returned "from-environment"."""
    sr.upsert_by_key(
        db, spec.domain, spec.key, "from-row", scope=SettingScope.platform()
    )
    db.flush()
    value, source = sr.resolve_with_source(db, spec.domain, spec.key, tenant_id=None)
    assert (value, source) == ("from-row", "platform")


def test_the_spec_default_wins_over_the_environment(db, spec):
    """The subtler half. A row is an operator decision, so it beating env is
    unsurprising; a DEFAULT beating env is the part people expect to go the
    other way, and it is what makes env a loader rather than an override."""
    value, source = sr.resolve_with_source(db, spec.domain, spec.key, tenant_id=None)
    assert (value, source) == ("from-default", "default")


def test_env_reaches_the_value_only_by_seeding_a_row(db, spec):
    """`env_var` is not inert — it is consumed at a different time, by a
    different function, producing a visible row rather than an invisible
    override."""
    assert sr.seed_settings_from_env(db) >= 1
    db.flush()
    value, source = sr.resolve_with_source(db, spec.domain, spec.key, tenant_id=None)
    assert (value, source) == ("from-environment", "platform")
