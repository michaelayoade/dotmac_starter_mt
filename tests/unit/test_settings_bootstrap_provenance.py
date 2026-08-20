"""A bootstrap-created row says where it came from, and never what it was set to.

ADR-0011's amendment (2026-08-20, §4) closes the last gap in the bootstrap
story. `seed_settings_from_env` created platform rows with no `changed_by` at
all, so `change_reason` was NULL and a row that appeared on a boot was
indistinguishable from one an operator typed. That is exactly the question the
history table exists to answer, and for a setting nobody remembers configuring
it is the only way to find out that a variable in a unit file — which will not
update the row again, ever — is why the value is what it is.

The other half is what must NOT be recorded. `changed_by_party_id` stays NULL,
because no person did this and inventing an actor is worse than recording none.
And the variable's VALUE never appears: `DomainSettingHistory` already writes
NULL value columns for a secret spec so a rotated credential does not outlive
its rotation, and a provenance string carrying the value would walk straight
around that. ADR-0009 applies to the audit trail too.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from dotmac_kernel import settings_crypto as sc
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel.setting_scopes import SettingScope
from dotmac_kernel.setting_value_types import SettingValueType
from dotmac_kernel.settings_models import DomainSettingHistory, SettingDomain

ENV_VAR = "DOTMAC_BOOTSTRAP_PROVENANCE_PROBE"
SEEDED_VALUE = "value-from-the-unit-file"


def _history(db, key: str) -> list[DomainSettingHistory]:
    return (
        db.query(DomainSettingHistory)
        .filter(DomainSettingHistory.key == key)
        .order_by(DomainSettingHistory.changed_at)
        .all()
    )


@pytest.fixture
def env_spec(monkeypatch):
    """A registered spec declaring `env_var`, with the variable set."""
    monkeypatch.setenv(ENV_VAR, SEEDED_VALUE)
    key = f"bootstrap_prov_{uuid4().hex[:8]}"
    declared: sr.SettingSpec[str] = sr.SettingSpec(
        domain=SettingDomain.auth,
        key=key,
        value_type=SettingValueType("string"),
        default="from-default",
        env_var=ENV_VAR,
    )
    sr.register_specs([declared])
    yield declared
    sr._REGISTRY.pop((SettingDomain.auth, key), None)


@pytest.fixture
def secret_env_spec(monkeypatch):
    """The same, for a SECRET spec — where the value must never be written.

    A secret is a string spec with `is_secret=True`; there is no separate value
    type. The encryption key comes from the environment the way a deployment
    without an installed `KeyProvider` supplies it.
    """
    from cryptography.fernet import Fernet

    monkeypatch.setenv(sc.KEY_ENV_VAR, Fernet.generate_key().decode())
    monkeypatch.setenv(ENV_VAR, SEEDED_VALUE)
    key = f"bootstrap_secret_{uuid4().hex[:8]}"
    declared: sr.SettingSpec[str | None] = sr.SettingSpec(
        domain=SettingDomain.auth,
        key=key,
        value_type=SettingValueType("string"),
        default=None,
        env_var=ENV_VAR,
        is_secret=True,
    )
    sr.register_specs([declared])
    yield declared
    sr._REGISTRY.pop((SettingDomain.auth, key), None)


def test_the_context_names_the_system_and_the_variable_but_not_the_value() -> None:
    """The unit of the contract, before any database is involved."""
    context = sr.bootstrap_change_context(ENV_VAR)

    assert context.actor_party_id is None, (
        "a bootstrap has no person behind it; inventing one is worse than "
        "recording none"
    )
    assert sr.BOOTSTRAP_PROVENANCE in context.reason
    assert ENV_VAR in context.reason
    assert SEEDED_VALUE not in (context.reason or ""), (
        "the provenance string carried the variable's value — that is the leak "
        "the secret-NULL columns exist to prevent, reintroduced in a text field"
    )


def test_a_seeded_row_records_where_it_came_from(db, env_spec) -> None:
    assert sr.seed_settings_from_env(db) >= 1
    db.flush()

    entries = _history(db, env_spec.key)
    assert entries, "the bootstrap wrote no history row at all"
    entry = entries[-1]

    assert entry.change_reason is not None, (
        "a row created by the bootstrap is indistinguishable from one an "
        "operator typed — which is the question the history table exists to "
        "answer"
    )
    assert sr.BOOTSTRAP_PROVENANCE in entry.change_reason
    assert ENV_VAR in entry.change_reason
    assert entry.changed_by_party_id is None


def test_the_seeded_history_row_carries_no_actor_or_request_context(
    db, env_spec
) -> None:
    """A bootstrap has no request. Inventing one would make the row lie."""
    sr.seed_settings_from_env(db)
    db.flush()

    entry = _history(db, env_spec.key)[-1]
    assert entry.ip_address is None
    assert entry.user_agent is None
    assert entry.request_id is None


def test_a_secret_seeded_from_the_environment_never_writes_its_value(
    db, secret_env_spec
) -> None:
    """The load-bearing one: provenance must not become a side door for a value.

    The value columns are already NULL for a secret. This proves the reason
    string did not quietly become the place the credential lives instead.
    """
    sr.seed_settings_from_env(db)
    db.flush()

    entry = _history(db, secret_env_spec.key)[-1]
    assert entry.value_after is None
    assert entry.value_before is None
    assert entry.secret_changed is True
    assert SEEDED_VALUE not in (
        entry.change_reason or ""
    ), "the secret's value reached the history table through change_reason"


def test_reseeding_does_not_append_a_second_provenance_row(db, env_spec) -> None:
    """The seed is idempotent, so its history must be too.

    A row per boot would bury a real operator change under restart noise, and
    would also imply the environment keeps updating the setting — which it does
    not, and which is the misunderstanding the amendment exists to correct.
    """
    sr.seed_settings_from_env(db)
    db.flush()
    first = len(_history(db, env_spec.key))

    assert sr.seed_settings_from_env(db) == 0
    db.flush()

    assert len(_history(db, env_spec.key)) == first


def test_an_operator_change_after_the_bootstrap_is_distinguishable(
    db, env_spec
) -> None:
    """The point of recording provenance: the two are told apart afterwards."""
    sr.seed_settings_from_env(db)
    db.flush()
    sr.upsert_by_key(
        db,
        env_spec.domain,
        env_spec.key,
        "operator-set",
        scope=SettingScope.platform(),
        changed_by=sr.SettingChangeContext(reason="raised after the incident"),
    )
    db.flush()

    reasons = [entry.change_reason or "" for entry in _history(db, env_spec.key)]
    bootstrapped = [r for r in reasons if sr.BOOTSTRAP_PROVENANCE in r]
    operator = [r for r in reasons if sr.BOOTSTRAP_PROVENANCE not in r]
    assert len(bootstrapped) == 1
    assert len(operator) == 1
    assert os.environ[ENV_VAR] == SEEDED_VALUE, (
        "the variable is still set and still says something else — and the "
        "operator's value is the one that stands, which is the one-way "
        "property the amendment writes down"
    )
