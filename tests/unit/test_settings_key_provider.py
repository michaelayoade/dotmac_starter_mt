"""The `KeyProvider` seam: where encryption keys come from, and when.

The behaviours that matter are about TIMING and BLAST RADIUS, not about
encryption itself (`test_settings_crypto.py` covers that): a provider is read
once so a later outage cannot reach the request path, rotation is explicit, a
failure is loud, and key material never reaches a log or a traceback.
"""

from __future__ import annotations

import logging

import pytest
from cryptography.fernet import Fernet
from dotmac_kernel import settings_crypto as sc


def _key(key_id: str = "main", status: sc.KeyStatus = sc.KeyStatus.ACTIVE):
    return sc.EncryptionKey(
        key_id=key_id, material=Fernet.generate_key().decode(), status=status
    )


class _Provider:
    """A product-supplied provider. Records its calls so the tests can prove
    how many times the kernel read it."""

    def __init__(self, *keys):
        self.keys = list(keys)
        self.calls = 0

    def load_keys(self):
        self.calls += 1
        return tuple(self.keys)


class _BrokenProvider:
    def __init__(self, exc):
        self.exc = exc

    def load_keys(self):
        raise self.exc


@pytest.fixture(autouse=True)
def _no_provider_or_env(monkeypatch):
    """Every test starts with neither a provider nor environment keys."""
    for var in (sc.KEYRING_ENV_VAR, sc.KEY_ENV_VAR, sc.KEY_FILE_ENV_VAR):
        monkeypatch.delenv(var, raising=False)
    sc.clear_key_provider()
    yield
    sc.clear_key_provider()


def test_an_installed_provider_supplies_the_keyring():
    key = _key()
    sc.install_key_provider(_Provider(key))
    assert sc.keyring().active() == key
    assert sc.encryption_configured() is True


def test_the_provider_is_read_once_not_per_call():
    """The whole point of the seam. If `keyring()` re-read the provider, a
    secret store's outage would reach the per-request read path — which is
    exactly what reading VALUES over the network does and why we don't."""
    provider = _Provider(_key())
    sc.install_key_provider(provider)
    for _ in range(50):
        sc.keyring()
        sc.encryption_configured()
    assert provider.calls == 1


def test_a_provider_that_starts_failing_does_not_affect_reads():
    """The outage-after-boot case, stated directly."""
    key = _key()
    provider = _Provider(key)
    sc.install_key_provider(provider)

    def _now_broken():
        raise ConnectionError("secret store unreachable")

    provider.load_keys = _now_broken
    # Still resolves, still encrypts: the keys are already in the process.
    assert sc.keyring().active() == key
    assert sc.decrypt_value(sc.encrypt_value("hunter2")) == "hunter2"


def test_rotation_requires_an_explicit_refresh():
    """A rotation takes effect when an operator says so, not when a timer
    fires."""
    first, second = _key("first"), _key("second")
    provider = _Provider(first)
    sc.install_key_provider(provider)

    provider.keys = [second]
    assert sc.keyring().active() == first  # unchanged until asked

    assert sc.refresh_keys().active() == second
    assert sc.keyring().active() == second
    assert provider.calls == 2


def test_a_failed_refresh_keeps_the_working_keyring():
    """A store briefly unreachable during a rotation attempt must leave a
    working process working."""
    key = _key()
    provider = _Provider(key)
    sc.install_key_provider(provider)
    provider.load_keys = lambda: (_ for _ in ()).throw(ConnectionError("down"))

    with pytest.raises(sc.KeyProviderError):
        sc.refresh_keys()
    assert sc.keyring().active() == key


def test_refresh_without_a_provider_is_an_error_not_a_no_op():
    with pytest.raises(sc.KeyProviderError, match="no key provider is installed"):
        sc.refresh_keys()


def test_a_failing_provider_raises_at_install_not_at_the_first_write():
    with pytest.raises(sc.KeyProviderError, match="could not load encryption keys"):
        sc.install_key_provider(_BrokenProvider(ConnectionError("store down")))


def test_an_invalid_keyring_is_rejected_at_install():
    """Two active keys leaves no rule for which one encrypts. Caught here, not
    at the write that trips over it."""
    with pytest.raises(sc.KeyringError, match="more than one active key"):
        sc.install_key_provider(_Provider(_key("a"), _key("b")))


def test_provider_failure_never_quotes_the_underlying_error():
    """A store client's exception can quote the payload it choked on, and that
    payload is key material. Only the exception TYPE is reported."""
    secret = "AAAA-this-is-key-material-AAAA"
    with pytest.raises(sc.KeyProviderError) as caught:
        sc.install_key_provider(_BrokenProvider(ValueError(f"bad key: {secret}")))
    assert secret not in str(caught.value)
    assert "ValueError" in str(caught.value)


def test_key_material_is_absent_from_reprs():
    """A dataclass repr is reached from places nobody audits — a traceback, a
    debug log, a failed-assertion diff."""
    key = sc.EncryptionKey(key_id="main", material="SUPER-SECRET-MATERIAL")
    ring = sc.Keyring((key,))
    assert "SUPER-SECRET-MATERIAL" not in repr(key)
    assert "SUPER-SECRET-MATERIAL" not in repr(ring)
    # Still identifiable — redaction must not make a keyring undebuggable.
    assert "main" in repr(key)
    assert "main" in repr(ring)


def test_install_logs_ids_and_statuses_but_no_material(caplog):
    key = _key("main")
    with caplog.at_level(logging.INFO, logger=sc.__name__):
        sc.install_key_provider(_Provider(key))
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "main" in logged
    assert key.material not in logged


def test_a_provider_beats_the_environment(monkeypatch):
    """Installing one is an explicit statement about where keys come from; a
    variable left over in a unit file must not quietly win."""
    monkeypatch.setenv(sc.KEY_ENV_VAR, Fernet.generate_key().decode())
    provided = _key("from-provider")
    sc.install_key_provider(_Provider(provided))
    assert sc.keyring().active() == provided


def test_clearing_the_provider_falls_back_to_the_environment(monkeypatch):
    sc.install_key_provider(_Provider(_key("from-provider")))
    monkeypatch.setenv(sc.KEY_ENV_VAR, Fernet.generate_key().decode())
    sc.clear_key_provider()
    active = sc.keyring().active()
    assert active is not None and active.key_id == sc.DEFAULT_KEY_ID


def test_without_a_provider_the_environment_is_still_read_fresh(monkeypatch):
    """The env path is unchanged by this seam: no snapshot, so a rotated
    variable takes effect without a refresh call."""
    monkeypatch.setenv(sc.KEY_ENV_VAR, Fernet.generate_key().decode())
    first = sc.keyring().active()
    monkeypatch.setenv(sc.KEY_ENV_VAR, Fernet.generate_key().decode())
    second = sc.keyring().active()
    assert first is not None and second is not None
    assert first.material != second.material


def test_values_round_trip_through_provider_supplied_keys():
    sc.install_key_provider(_Provider(_key()))
    stored = sc.encrypt_value("s3cret")
    assert stored.startswith(sc.ENCRYPTED_PREFIX)
    assert "s3cret" not in stored
    assert sc.decrypt_value(stored) == "s3cret"


def test_a_retired_provider_key_still_decrypts_after_rotation():
    """Rotation overlap works the same through a provider as through env: the
    old key keeps reading rows not yet rewritten."""
    old = _key("old")
    provider = _Provider(old)
    sc.install_key_provider(provider)
    stored = sc.encrypt_value("legacy")

    new = _key("new")
    provider.keys = [
        sc.EncryptionKey(**{**old.__dict__, "status": sc.KeyStatus.RETIRED}),
        new,
    ]
    sc.refresh_keys()

    assert sc.decrypt_value(stored) == "legacy"  # read by the retired key
    assert sc.encrypted_key_id(sc.encrypt_value("fresh")) == "new"


def test_a_provider_returning_nothing_is_not_a_usable_keyring():
    """An empty return is how "no keys configured" looks — which is why a
    provider must RAISE on an unreachable store rather than return empty."""
    sc.install_key_provider(_Provider())
    assert sc.encryption_configured() is False
    with pytest.raises(sc.SettingsEncryptionError):
        sc.encrypt_value("nope")
