"""ADR-0009 (runtime half): resolution completes with the network unavailable.

Sockets are made to fail, then real settings — including an encrypted secret —
are resolved. Any attempt to dereference a value against a store turns into a
failure here rather than into an outage when that store is down.

Also covers `dotmac_kernel.secrets`, the place a product PUTS material it
resolved itself: loaded once, rotated explicitly, never logged.
"""

from __future__ import annotations

import logging
import socket
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from dotmac_kernel import secrets as ks
from dotmac_kernel import settings_crypto as sc
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel.setting_scopes import SettingScope
from dotmac_kernel.setting_value_types import SettingValueType
from dotmac_kernel.settings_models import SettingDomain


class _NetworkUsed(AssertionError):
    """A resolution path tried to open a socket."""


@pytest.fixture
def no_network(monkeypatch):
    def _forbidden(*args, **kwargs):
        raise _NetworkUsed(
            "settings resolution opened a socket — a secret is HELD, not "
            "fetched while handling a request (ADR-0009)"
        )

    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)


@pytest.fixture
def secret_spec(monkeypatch):
    """A registered secret setting plus a key to encrypt it with.

    Teardown removes exactly the key it added, so this spec does not leak into
    tests that inspect `all_specs()` — the pattern `test_settings_service.py`
    established.
    """
    monkeypatch.setenv(sc.KEY_ENV_VAR, Fernet.generate_key().decode())
    sc.clear_key_provider()
    key = f"held_secret_{uuid4().hex[:8]}"
    spec = sr.SettingSpec(
        domain=SettingDomain.auth,
        key=key,
        value_type=SettingValueType("string"),
        default="fallback",
        is_secret=True,
        description="Fixture for the ADR-0009 no-network proof.",
    )
    sr.register_specs([spec])
    yield spec
    sr._REGISTRY.pop((SettingDomain.auth, key), None)


def test_the_no_network_fixture_actually_fires(no_network):
    """Sensitivity proof. Without this, every test below could be passing
    because the patch does nothing rather than because the rule holds."""
    with pytest.raises(_NetworkUsed):
        socket.socket()
    with pytest.raises(_NetworkUsed):
        socket.create_connection(("example.invalid", 80))


def test_resolving_an_encrypted_secret_does_no_network_io(db, no_network, secret_spec):
    """The load-bearing test: a real secret, written and read, with no network."""
    sr.upsert_by_key(
        db,
        secret_spec.domain,
        secret_spec.key,
        "the-actual-secret",
        scope=SettingScope.platform(),
    )
    db.flush()
    value, source = sr.resolve_with_source(
        db, secret_spec.domain, secret_spec.key, tenant_id=None
    )
    assert (value, source) == ("the-actual-secret", "platform")


def test_bulk_resolution_does_no_network_io(db, no_network, secret_spec):
    """`resolve_many` is a separate path; a dereference could hide in the one
    the single-key test doesn't exercise."""
    resolved = sr.resolve_many(
        db, secret_spec.domain, (secret_spec.key,), tenant_id=None
    )
    assert resolved == {secret_spec.key: "fallback"}


def test_a_stored_uri_is_a_value_not_a_reference(db, no_network, secret_spec):
    """The behaviour that distinguishes ADR-0009 from the rejected alternative.

    A row holding what LOOKS like a store reference resolves to that string.
    The kernel does not recognise the scheme, does not fetch it, and does not
    fail — it is simply a value. A product wanting the pointed-at secret
    installs a `SecretSource`; it never asks the kernel to dereference.
    """
    pointer = "bao://secret/settings/auth#some_key"
    sr.upsert_by_key(
        db, secret_spec.domain, secret_spec.key, pointer, scope=SettingScope.platform()
    )
    db.flush()
    value, _ = sr.resolve_with_source(
        db, secret_spec.domain, secret_spec.key, tenant_id=None
    )
    assert value == pointer


# ── dotmac_kernel.secrets ────────────────────────────────────────────────────


class _Source:
    def __init__(self, **held):
        self.held = dict(held)
        self.calls = 0

    def load(self):
        self.calls += 1
        return dict(self.held)


@pytest.fixture(autouse=True)
def _clean_secret_source():
    ks.clear_secret_source()
    yield
    ks.clear_secret_source()


def test_secrets_are_loaded_once_not_per_lookup():
    """The whole point: a lookup is a dict read, so it cannot be affected by
    the store being unreachable later."""
    source = _Source(jwt="a-signing-secret")
    assert ks.install_secret_source(source) == ("jwt",)
    for _ in range(50):
        assert ks.get_secret("jwt") == "a-signing-secret"
    assert source.calls == 1


def test_a_source_that_starts_failing_does_not_affect_lookups():
    source = _Source(jwt="a-signing-secret")
    ks.install_secret_source(source)
    source.load = lambda: (_ for _ in ()).throw(ConnectionError("store down"))
    assert ks.get_secret("jwt") == "a-signing-secret"


def test_rotation_requires_an_explicit_refresh():
    source = _Source(jwt="old")
    ks.install_secret_source(source)
    source.held["jwt"] = "new"
    assert ks.get_secret("jwt") == "old"
    ks.refresh_secrets()
    assert ks.get_secret("jwt") == "new"


def test_a_failed_refresh_keeps_the_working_set():
    source = _Source(jwt="working")
    ks.install_secret_source(source)
    source.load = lambda: (_ for _ in ()).throw(ConnectionError("down"))
    with pytest.raises(ks.SecretSourceError):
        ks.refresh_secrets()
    assert ks.get_secret("jwt") == "working"


def test_a_failing_source_raises_at_install():
    class _Broken:
        def load(self):
            raise ConnectionError("store unreachable")

    with pytest.raises(ks.SecretSourceError, match="could not load secrets"):
        ks.install_secret_source(_Broken())


def test_source_failure_never_quotes_the_underlying_error():
    """A store client's message can quote the payload it choked on, and that
    payload is the secret. Only the exception TYPE is reported."""
    material = "AAAA-actual-secret-material-AAAA"

    class _Leaky:
        def load(self):
            raise ValueError(f"bad value: {material}")

    with pytest.raises(ks.SecretSourceError) as caught:
        ks.install_secret_source(_Leaky())
    assert material not in str(caught.value)
    assert "ValueError" in str(caught.value)


def test_a_missing_secret_names_what_is_held_not_what_it_holds():
    ks.install_secret_source(_Source(jwt="x", smtp="y"))
    with pytest.raises(ks.MissingSecretError) as caught:
        ks.require_secret("typo")
    message = str(caught.value)
    assert "typo" in message and "jwt" in message and "smtp" in message
    assert "x" not in message.replace("typo", "").replace("secret", "")


def test_install_logs_names_but_never_values(caplog):
    with caplog.at_level(logging.INFO, logger=ks.__name__):
        ks.install_secret_source(_Source(jwt="a-signing-secret"))
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "jwt" in logged
    assert "a-signing-secret" not in logged


def test_refresh_without_a_source_is_an_error_not_a_no_op():
    with pytest.raises(ks.SecretSourceError, match="no secret source is installed"):
        ks.refresh_secrets()


def test_a_non_string_value_is_rejected_by_name():
    """The name is safe to print; the value never is — so the error names the
    key and says nothing about what was there."""

    class _Wrong:
        def load(self):
            return {"jwt": 12345}

    with pytest.raises(ks.SecretSourceError, match="non-string value for 'jwt'"):
        ks.install_secret_source(_Wrong())


def test_an_empty_source_holds_nothing_and_says_so():
    """Empty is a legitimate answer for "nothing is configured" — which is why
    a source must RAISE for an unreachable store rather than return empty."""
    ks.install_secret_source(_Source())
    assert ks.secret_names() == ()
    assert ks.get_secret("jwt") is None
