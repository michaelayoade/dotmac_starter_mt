"""At-rest encryption of secret settings (`dotmac_kernel.settings_crypto`).

The property under test is not "Fernet works" — it is that a secret NEVER
reaches the column in plaintext, and that a deployment which cannot encrypt is
told so at the write rather than discovering it in a database dump.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from dotmac_kernel import settings_crypto as sc
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel.settings_models import DomainSetting, SettingDomain, SettingValueType
from sqlalchemy import select

KEY = Fernet.generate_key().decode()
OLD_KEY = Fernet.generate_key().decode()


def _keyring(*entries: dict) -> str:
    import json

    return json.dumps(list(entries))


@pytest.fixture(autouse=True)
def _isolated_keyring(monkeypatch):
    """Start every test from "no key configured".

    The Fernet memo is process-global — a leaked entry would let one test's key
    decrypt another's ciphertext — and an ambient key variable on the machine
    running the suite would otherwise decide what these tests observe.
    """
    for variable in (sc.KEYRING_ENV_VAR, sc.KEY_ENV_VAR, sc.KEY_FILE_ENV_VAR):
        monkeypatch.delenv(variable, raising=False)
    sc._FERNET_CACHE.clear()
    yield
    sc._FERNET_CACHE.clear()


@pytest.fixture
def _secret_spec():
    before = set(sr._REGISTRY.keys())
    sr.register_specs(
        [
            sr.SettingSpec(
                domain=SettingDomain.auth,
                key="test_secret",
                value_type=SettingValueType.string,
                default=None,
                is_secret=True,
            )
        ]
    )
    yield
    for key in set(sr._REGISTRY.keys()) - before:
        del sr._REGISTRY[key]


def _stored_text(db, key: str) -> str | None:
    return db.scalar(select(DomainSetting.value_text).where(DomainSetting.key == key))


# ---------------------------------------------------------------------------
# The module in isolation
# ---------------------------------------------------------------------------


def test_round_trip(monkeypatch):
    monkeypatch.setenv(sc.KEY_ENV_VAR, KEY)
    token = sc.encrypt_value("hunter2")
    assert token.startswith(sc.ENCRYPTED_PREFIX)
    assert "hunter2" not in token
    assert sc.decrypt_value(token) == "hunter2"


def test_encryption_is_idempotent(monkeypatch):
    """A re-flush of an already-encrypted row must not double-encrypt it."""
    monkeypatch.setenv(sc.KEY_ENV_VAR, KEY)
    once = sc.encrypt_value("hunter2")
    assert sc.encrypt_value(once) == once


def test_encrypting_without_a_key_raises(monkeypatch):
    monkeypatch.delenv(sc.KEY_ENV_VAR, raising=False)
    monkeypatch.delenv(sc.KEY_FILE_ENV_VAR, raising=False)
    with pytest.raises(sc.SettingsEncryptionError) as exc:
        sc.encrypt_value("hunter2")
    assert sc.KEY_ENV_VAR in str(exc.value)


def test_plaintext_reads_through(monkeypatch):
    """Rows written before a key existed must keep resolving."""
    monkeypatch.setenv(sc.KEY_ENV_VAR, KEY)
    assert sc.decrypt_value("legacy-plaintext") == "legacy-plaintext"


def test_a_rotated_out_key_yields_none_rather_than_raising(monkeypatch):
    """The read path degrades to the spec default; it must not 500 every
    request that touches settings."""
    monkeypatch.setenv(sc.KEY_ENV_VAR, KEY)
    token = sc.encrypt_value("hunter2")
    monkeypatch.setenv(sc.KEY_ENV_VAR, Fernet.generate_key().decode())
    assert sc.decrypt_value(token) is None


# ---------------------------------------------------------------------------
# Keyring and rotation — the reason the key id is in the ciphertext
# ---------------------------------------------------------------------------


def test_the_ciphertext_names_its_key(monkeypatch):
    """A Fernet token carries no key id, so without this rotation cannot know
    which key a value is under."""
    monkeypatch.setenv(
        sc.KEYRING_ENV_VAR,
        _keyring({"key_id": "2026-08", "key": KEY, "status": "active"}),
    )
    stored = sc.encrypt_value("hunter2")
    assert stored.startswith(f"{sc.ENCRYPTED_PREFIX}2026-08:")
    assert sc.encrypted_key_id(stored) == "2026-08"


def test_a_retired_key_still_decrypts_during_the_overlap(monkeypatch):
    monkeypatch.setenv(
        sc.KEYRING_ENV_VAR,
        _keyring({"key_id": "old", "key": OLD_KEY, "status": "active"}),
    )
    stored = sc.encrypt_value("hunter2")

    monkeypatch.setenv(
        sc.KEYRING_ENV_VAR,
        _keyring(
            {"key_id": "new", "key": KEY, "status": "active"},
            {"key_id": "old", "key": OLD_KEY, "status": "retired"},
        ),
    )
    assert sc.decrypt_value(stored) == "hunter2"


def test_a_revoked_key_decrypts_nothing(monkeypatch):
    """Revocation is for material believed compromised: values under it become
    unreadable ON PURPOSE."""
    monkeypatch.setenv(
        sc.KEYRING_ENV_VAR,
        _keyring({"key_id": "old", "key": OLD_KEY, "status": "active"}),
    )
    stored = sc.encrypt_value("hunter2")

    monkeypatch.setenv(
        sc.KEYRING_ENV_VAR,
        _keyring(
            {"key_id": "new", "key": KEY, "status": "active"},
            {"key_id": "old", "key": OLD_KEY, "status": "revoked"},
        ),
    )
    assert sc.decrypt_value(stored) is None


def test_encrypting_a_retired_value_rewrites_it_onto_the_active_key(monkeypatch):
    monkeypatch.setenv(
        sc.KEYRING_ENV_VAR,
        _keyring({"key_id": "old", "key": OLD_KEY, "status": "active"}),
    )
    stored = sc.encrypt_value("hunter2")

    monkeypatch.setenv(
        sc.KEYRING_ENV_VAR,
        _keyring(
            {"key_id": "new", "key": KEY, "status": "active"},
            {"key_id": "old", "key": OLD_KEY, "status": "retired"},
        ),
    )
    rewritten = sc.encrypt_value(stored)
    assert sc.encrypted_key_id(rewritten) == "new"
    # A rewrite, not a nested encryption nobody can unwrap.
    assert sc.decrypt_value(rewritten) == "hunter2"


def test_a_value_already_on_the_active_key_is_untouched(monkeypatch):
    monkeypatch.setenv(
        sc.KEYRING_ENV_VAR,
        _keyring({"key_id": "new", "key": KEY, "status": "active"}),
    )
    stored = sc.encrypt_value("hunter2")
    assert sc.encrypt_value(stored) == stored


def test_two_active_keys_is_a_configuration_error(monkeypatch):
    """Writing would have to guess which key to encrypt with."""
    monkeypatch.setenv(
        sc.KEYRING_ENV_VAR,
        _keyring(
            {"key_id": "a", "key": KEY, "status": "active"},
            {"key_id": "b", "key": OLD_KEY, "status": "active"},
        ),
    )
    with pytest.raises(sc.KeyringError):
        sc.keyring()
    with pytest.raises(sc.SettingsEncryptionError):
        sc.encrypt_value("hunter2")


def test_a_duplicate_key_id_is_a_configuration_error(monkeypatch):
    monkeypatch.setenv(
        sc.KEYRING_ENV_VAR,
        _keyring(
            {"key_id": "a", "key": KEY, "status": "active"},
            {"key_id": "a", "key": OLD_KEY, "status": "retired"},
        ),
    )
    with pytest.raises(sc.KeyringError):
        sc.keyring()


def test_a_key_id_containing_the_delimiter_is_rejected(monkeypatch):
    """`key_id` is a ciphertext field delimited by ':'."""
    monkeypatch.setenv(
        sc.KEYRING_ENV_VAR,
        _keyring({"key_id": "a:b", "key": KEY, "status": "active"}),
    )
    with pytest.raises(sc.KeyringError):
        sc.keyring()


def test_a_malformed_keyring_never_reads_as_encryption_off(monkeypatch):
    """A typo must fail, not silently disable encryption."""
    monkeypatch.setenv(sc.KEYRING_ENV_VAR, "{not json")
    assert sc.encryption_configured() is False
    with pytest.raises(sc.SettingsEncryptionError):
        sc.encrypt_value("hunter2")


def test_the_single_key_variable_is_a_one_entry_keyring(monkeypatch):
    monkeypatch.setenv(sc.KEY_ENV_VAR, KEY)
    stored = sc.encrypt_value("hunter2")
    assert sc.encrypted_key_id(stored) == sc.DEFAULT_KEY_ID


def test_an_id_less_value_from_erp_still_decrypts(monkeypatch):
    """`dotmac_erp` writes `enc:<token>` with no key id; an adopting deployment
    must be able to read the rows its own code wrote."""
    from cryptography.fernet import Fernet as _F

    legacy = f"{sc.ENCRYPTED_PREFIX}{_F(OLD_KEY.encode()).encrypt(b'hunter2').decode()}"
    monkeypatch.setenv(
        sc.KEYRING_ENV_VAR,
        _keyring(
            {"key_id": "new", "key": KEY, "status": "active"},
            {"key_id": "imported", "key": OLD_KEY, "status": "retired"},
        ),
    )
    assert sc.encrypted_key_id(legacy) is None
    assert sc.decrypt_value(legacy) == "hunter2"


def test_the_key_can_come_from_a_file(monkeypatch, tmp_path):
    key_file = tmp_path / "settings.key"
    key_file.write_text(KEY + "\n", encoding="utf-8")
    monkeypatch.delenv(sc.KEY_ENV_VAR, raising=False)
    monkeypatch.setenv(sc.KEY_FILE_ENV_VAR, str(key_file))
    assert sc.decrypt_value(sc.encrypt_value("hunter2")) == "hunter2"


def test_an_invalid_key_is_reported_not_used(monkeypatch):
    monkeypatch.setenv(sc.KEY_ENV_VAR, "not-a-fernet-key")
    assert sc.encryption_configured() is False
    with pytest.raises(sc.SettingsEncryptionError):
        sc.encrypt_value("hunter2")


# ---------------------------------------------------------------------------
# Through the resolver — the property that actually matters
# ---------------------------------------------------------------------------


def test_a_secret_is_ciphertext_in_the_column(db, monkeypatch, _secret_spec):
    monkeypatch.setenv(sc.KEY_ENV_VAR, KEY)
    sr.upsert_by_key(db, SettingDomain.auth, "test_secret", "hunter2", tenant_id=None)

    stored = _stored_text(db, "test_secret")
    assert stored is not None
    assert stored.startswith(sc.ENCRYPTED_PREFIX)
    assert "hunter2" not in stored

    assert (
        sr.resolve_value(db, SettingDomain.auth, "test_secret", tenant_id=None)
        == "hunter2"
    )


def test_a_non_secret_is_not_encrypted(db, monkeypatch):
    """Encryption follows the SPEC, not the key's presence — an ordinary
    setting stays readable in the column."""
    monkeypatch.setenv(sc.KEY_ENV_VAR, KEY)
    sr.upsert_by_key(
        db, SettingDomain.auth, "registration_policy", "open", tenant_id=None
    )
    assert _stored_text(db, "registration_policy") == "open"


def test_writing_a_secret_without_a_key_fails_the_write(db, monkeypatch, _secret_spec):
    """Fail closed: no row is better than a plaintext credential."""
    monkeypatch.delenv(sc.KEY_ENV_VAR, raising=False)
    monkeypatch.delenv(sc.KEY_FILE_ENV_VAR, raising=False)
    with pytest.raises(sc.SettingsEncryptionError):
        sr.upsert_by_key(
            db, SettingDomain.auth, "test_secret", "hunter2", tenant_id=None
        )
    assert _stored_text(db, "test_secret") is None


def test_a_secret_stored_as_legacy_plaintext_still_resolves(
    db, monkeypatch, _secret_spec
):
    """A row from before encryption existed — the read path tolerates it."""
    monkeypatch.setenv(sc.KEY_ENV_VAR, KEY)
    db.add(
        DomainSetting(
            tenant_id=None,
            domain=SettingDomain.auth,
            key="test_secret",
            value_type=SettingValueType.string,
            value_text="legacy-plaintext",
            is_secret=True,
        )
    )
    db.flush()
    assert (
        sr.resolve_value(db, SettingDomain.auth, "test_secret", tenant_id=None)
        == "legacy-plaintext"
    )


def test_an_undecryptable_secret_degrades_to_the_default(db, monkeypatch, _secret_spec):
    monkeypatch.setenv(sc.KEY_ENV_VAR, KEY)
    sr.upsert_by_key(db, SettingDomain.auth, "test_secret", "hunter2", tenant_id=None)
    monkeypatch.setenv(sc.KEY_ENV_VAR, Fernet.generate_key().decode())

    value, source = sr.resolve_with_source(
        db, SettingDomain.auth, "test_secret", tenant_id=None
    )
    assert (value, source) == (None, "default")


# ---------------------------------------------------------------------------
# reencrypt_secrets — the second half of a rotation
# ---------------------------------------------------------------------------


def test_reencrypt_moves_rows_onto_the_active_key(db, monkeypatch, _secret_spec):
    monkeypatch.setenv(
        sc.KEYRING_ENV_VAR,
        _keyring({"key_id": "old", "key": OLD_KEY, "status": "active"}),
    )
    sr.upsert_by_key(db, SettingDomain.auth, "test_secret", "hunter2", tenant_id=None)
    assert sc.encrypted_key_id(_stored_text(db, "test_secret")) == "old"

    monkeypatch.setenv(
        sc.KEYRING_ENV_VAR,
        _keyring(
            {"key_id": "new", "key": KEY, "status": "active"},
            {"key_id": "old", "key": OLD_KEY, "status": "retired"},
        ),
    )
    assert sc.reencrypt_secrets(db) == (1, 0)
    assert sc.encrypted_key_id(_stored_text(db, "test_secret")) == "new"
    assert (
        sr.resolve_value(db, SettingDomain.auth, "test_secret", tenant_id=None)
        == "hunter2"
    )


def test_reencrypt_is_idempotent(db, monkeypatch, _secret_spec):
    """An interrupted rotation is continued by running it again, so a second
    run must rewrite nothing."""
    monkeypatch.setenv(
        sc.KEYRING_ENV_VAR,
        _keyring({"key_id": "new", "key": KEY, "status": "active"}),
    )
    sr.upsert_by_key(db, SettingDomain.auth, "test_secret", "hunter2", tenant_id=None)
    assert sc.reencrypt_secrets(db) == (0, 0)


def test_reencrypt_counts_a_row_it_cannot_read_and_keeps_going(
    db, monkeypatch, _secret_spec
):
    """One row under a key nobody has must not stop the rest of the rotation —
    and a non-zero failure count means the rotation is INCOMPLETE."""
    monkeypatch.setenv(
        sc.KEYRING_ENV_VAR,
        _keyring({"key_id": "lost", "key": OLD_KEY, "status": "active"}),
    )
    sr.upsert_by_key(db, SettingDomain.auth, "test_secret", "hunter2", tenant_id=None)

    monkeypatch.setenv(
        sc.KEYRING_ENV_VAR,
        _keyring({"key_id": "new", "key": KEY, "status": "active"}),
    )
    rewritten, failed = sc.reencrypt_secrets(db)
    assert (rewritten, failed) == (0, 1)


def test_reencrypt_without_an_active_key_raises(db, monkeypatch, _secret_spec):
    monkeypatch.delenv(sc.KEYRING_ENV_VAR, raising=False)
    monkeypatch.delenv(sc.KEY_ENV_VAR, raising=False)
    monkeypatch.delenv(sc.KEY_FILE_ENV_VAR, raising=False)
    with pytest.raises(sc.SettingsEncryptionError):
        sc.reencrypt_secrets(db)


# ---------------------------------------------------------------------------
# History (DomainSettingHistory) — the value transition, never the secret
# ---------------------------------------------------------------------------


def _history(db, key: str):
    from dotmac_kernel.settings_models import DomainSettingHistory

    return (
        db.scalars(
            select(DomainSettingHistory)
            .where(DomainSettingHistory.key == key)
            .order_by(DomainSettingHistory.changed_at)
        )
        .unique()
        .all()
    )


def test_a_secret_change_is_recorded_without_the_secret(db, monkeypatch, _secret_spec):
    """The whole point: rotating a compromised credential must not leave it
    readable in the table that explains the rotation."""
    monkeypatch.setenv(sc.KEY_ENV_VAR, KEY)
    sr.upsert_by_key(db, SettingDomain.auth, "test_secret", "first", tenant_id=None)
    sr.upsert_by_key(db, SettingDomain.auth, "test_secret", "second", tenant_id=None)

    entries = _history(db, "test_secret")
    assert [e.action.value for e in entries] == ["create", "update"]
    for entry in entries:
        assert entry.secret_changed is True
        assert entry.value_before is None
        assert entry.value_after is None
    # Nothing anywhere in the history row carries either value.
    blob = " ".join(str(v) for e in entries for v in vars(e).values())
    assert "first" not in blob
    assert "second" not in blob
